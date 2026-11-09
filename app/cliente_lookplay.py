"""
Cliente HTTP do Lookplay: substitui o Selenium por requisições diretas.

Passos, os mesmos que o navegador fazia:
1. Abrir a sessão, reutilizando o cookie do .env. Só faz login com senha se ela
   estiver configurada e o cookie não for válido.
2. Baixar a listagem do tipo (vida ou casa) e achar a linha de cada filial, na
   tabela #table-1, com o formulário que aponta para _edit.php.
3. Enviar esse formulário para abrir a tela de edição.
4. Reenviar o formulário de edição com TODOS os campos atuais e o novo CSV no
   campo "arquivo". Reenviar os campos evita apagar os demais dados do conteúdo.
5. Abrir a tela de novo e confirmar que o "Arquivo Atual" mudou. Só isso prova o envio.

Detalhe importante: "arquivo" e "texto" não existem no HTML do formulário. O JavaScript
do Lookplay os cria a partir de um modelo (<script id="TIPO-template">), que o navegador
executa e o requests não. Por isso, estes campos são lidos diretamente desse modelo.

O Lookplay não tem API documentada, então o HTML é a interface. Quando ele
mudar, este é o único arquivo a ajustar. Cookies e senha nunca vão para o log.
"""
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import Config
from app.dominio import Tipo

logger = logging.getLogger(__name__)

CAMPOS_ESPERADOS_NO_MODELO = {"arquivo", "texto"}
TIMEOUT = (10, 120)  # segundos para conectar e para receber a resposta


class ErroLookplay(Exception):
    """Falha ao enviar para uma filial. As demais podem continuar."""


class ConteudoNaoEncontrado(ErroLookplay):
    """A filial não tem conteúdo cadastrado neste filtro do Lookplay."""


class SessaoInvalida(Exception):
    """Sem sessão válida. Interrompe a execução: as próximas filiais falhariam igual."""


@dataclass
class _LinhaConteudo:
    url_edicao: str
    campos: list[tuple[str, str]]


@dataclass
class _TelaEdicao:
    url: str
    form: object
    campos_do_modelo: list[tuple[str, str]]
    arquivo_atual: str | None


def _ler_html(resposta) -> BeautifulSoup:
    """Lê os bytes para o BeautifulSoup respeitar a codificação informada pela página."""
    return BeautifulSoup(resposta.content, "html.parser")


def _eh_tela_de_login(soup: BeautifulSoup) -> bool:
    return soup.find("input", attrs={"type": "password"}) is not None


def campos_do_formulario(form) -> list[tuple[str, str]]:
    """Copia os campos preenchidos, como o navegador faria ao enviar o formulário."""
    campos: list[tuple[str, str]] = []
    for elemento in form.find_all(["input", "select", "textarea"]):
        nome = elemento.get("name")
        if not nome or elemento.has_attr("disabled"):
            continue

        if elemento.name == "select":
            opcoes = elemento.find_all("option")
            selecionadas = [o for o in opcoes if o.has_attr("selected")]
            if not selecionadas and opcoes and not elemento.has_attr("multiple"):
                selecionadas = opcoes[:1]  # sem seleção, o navegador usa a primeira
            campos += [(nome, o.get("value", o.get_text(strip=True))) for o in selecionadas]
        elif elemento.name == "textarea":
            campos.append((nome, elemento.get_text()))
        else:
            tipo = elemento.get("type", "text").lower()
            if tipo in ("file", "submit", "button", "image", "reset"):
                continue
            if tipo in ("checkbox", "radio") and not elemento.has_attr("checked"):
                continue
            campos.append((nome, elemento.get("value", "on" if tipo in ("checkbox", "radio") else "")))
    return campos


class ClienteLookplay:
    def __init__(self, config: Config):
        self._config = config
        self._http = requests.Session()
        # Repete só GET em falha temporária. POST não: poderia enviar em duplicidade.
        repeticao = Retry(total=2, backoff_factor=1, status_forcelist=(502, 503, 504), allowed_methods=("GET",))
        self._http.mount("https://", HTTPAdapter(max_retries=repeticao))
        self._indices: dict[Tipo, dict[str, _LinhaConteudo]] = {}
        self._filtros = {Tipo.VIDA: config.filtro_vida, Tipo.CASA: config.filtro_casa}
        self._repetidos: dict[Tipo, set[str]] = {}

    # ---------- Sessão ----------

    def abrir_sessao(self) -> None:
        """Garante uma sessão válida ou levanta SessaoInvalida."""
        self._indices.clear()
        self._aplicar_cookies()
        if self._config.lookplay_cookies and self._sessao_valida():
            logger.info("Sessão do Lookplay aberta com o cookie configurado.")
            return
        self._login()

    def _aplicar_cookies(self) -> None:
        """Converte 'a=1; b=2' em cookies da sessão, restritos ao domínio do Lookplay."""
        dominio = urlparse(self._config.lookplay_url).hostname
        for par in self._config.lookplay_cookies.split(";"):
            nome, separador, valor = par.strip().partition("=")
            if separador and nome:
                self._http.cookies.set(nome, valor, domain=dominio, path="/")

    def _sessao_valida(self) -> bool:
        resposta = self._http.get(self._url_listagem(Tipo.VIDA), timeout=TIMEOUT)
        return resposta.ok and not _eh_tela_de_login(_ler_html(resposta))

    def _login(self) -> None:
        if not self._config.lookplay_senha:
            raise SessaoInvalida(
                "O cookie do Lookplay está vazio ou expirou. Atualize LOOKPLAY_COOKIES no .env e rode de novo."
            )
        url = f"{self._config.lookplay_url}/index.php"
        pagina = self._http.get(url, timeout=TIMEOUT)
        soup = _ler_html(pagina)
        campo_senha = soup.find("input", attrs={"type": "password"})
        form = campo_senha.find_parent("form") if campo_senha else None
        if form is None:
            raise SessaoInvalida("Formulário de login do Lookplay não encontrado.")

        # Preenche pelos ids que o script antigo usava e mantém os campos ocultos.
        dados = dict(campos_do_formulario(form))
        dados[form.find(id="username")["name"]] = self._config.lookplay_usuario
        dados[campo_senha["name"]] = self._config.lookplay_senha
        botao = form.find(attrs={"name": "login"})
        if botao is not None:
            dados["login"] = botao.get("value", "")

        resposta = self._http.post(urljoin(pagina.url, form.get("action") or ""), data=dados, timeout=TIMEOUT)
        # Sem nova tentativa em caso de falha, para não bloquear o usuário.
        if not resposta.ok or _eh_tela_de_login(_ler_html(resposta)):
            raise SessaoInvalida("O Lookplay recusou o login. Confira o usuário e a senha.")
        logger.info("Sessão do Lookplay aberta com login.")

    # ---------- Listagem ----------

    def _url_listagem(self, tipo: Tipo) -> str:
        return f"{self._config.lookplay_url}/modules/conteudo/_list.php?filtro={self._filtros[tipo]}"

    def _indice(self, tipo: Tipo) -> dict[str, _LinhaConteudo]:
        """Lê a listagem uma vez por execução e indexa as linhas por filial."""
        if tipo in self._indices:
            return self._indices[tipo]

        resposta = self._http.get(self._url_listagem(tipo), timeout=TIMEOUT)
        resposta.raise_for_status()
        soup = _ler_html(resposta)
        if _eh_tela_de_login(soup):
            raise SessaoInvalida("A sessão do Lookplay expirou. Atualize LOOKPLAY_COOKIES no .env e rode de novo.")

        indice: dict[str, _LinhaConteudo] = {}
        repetidos: set[str] = set()
        for linha in soup.select("table#table-1 tr"):
            colunas = linha.find_all("td", recursive=False)
            form = linha.select_one('form[action$="_edit.php"]')
            if len(colunas) < 2 or form is None:
                continue
            # Como no script antigo, a filial fica na 2ª coluna. Espaços são normalizados.
            filial = " ".join(colunas[1].get_text().split())
            if filial in indice:
                repetidos.add(filial)
            indice[filial] = _LinhaConteudo(urljoin(resposta.url, form["action"]), campos_do_formulario(form))

        # Com título repetido não há como saber qual é o conteúdo certo: melhor não enviar.
        self._repetidos[tipo] = repetidos

        # Lista vazia indica mudança no HTML ou carga por Ajax. Sem este erro, todas
        # as filiais apareceriam como "sem conteúdo", escondendo o problema real.
        if not indice:
            raise ErroLookplay(f"Nenhum conteúdo lido na listagem {self._filtros[tipo]}. O HTML pode ter mudado.")
        self._indices[tipo] = indice
        return indice

    def conteudos(self, tipo: Tipo) -> set[str]:
        """Títulos cadastrados no filtro do tipo. Só leitura: usado para achar filiais fora do Lookplay."""
        return set(self._indice(tipo))

    def _abrir_edicao(self, linha: _LinhaConteudo) -> _TelaEdicao:
        """Abre a tela de edição (clique em "Editar") e lê o formulário e o modelo do tipo."""
        resposta = self._http.post(linha.url_edicao, data=linha.campos, timeout=TIMEOUT)
        resposta.raise_for_status()
        soup = _ler_html(resposta)
        if _eh_tela_de_login(soup):
            raise SessaoInvalida("A sessão do Lookplay expirou durante a execução.")
        botao = soup.find(attrs={"name": "_edit"})
        form = botao.find_parent("form") if botao else None
        if form is None:
            raise ErroLookplay("A tela de edição não tem o formulário esperado.")
        campos, arquivo_atual = self._ler_modelo(soup, form)
        return _TelaEdicao(resposta.url, form, campos, arquivo_atual)

    @staticmethod
    def _ler_modelo(soup: BeautifulSoup, form) -> tuple[list[tuple[str, str]], str | None]:
        """Extrai do modelo JavaScript do tipo os campos extras e o nome do arquivo atual."""
        seletor = form.find("select", attrs={"name": "tipo"})
        opcao = seletor.find("option", selected=True) if seletor else None
        modelo = soup.find("script", id=f"{opcao.get('value')}-template") if opcao else None
        if modelo is None or not modelo.string:
            raise ErroLookplay("A tela de edição não tem o modelo do tipo de conteúdo.")
        texto = modelo.string

        nomes = set(re.findall(r'\bname="([^"]+)"', texto))
        if "arquivo" not in nomes:
            raise ErroLookplay("O modelo do tipo de conteúdo não tem o campo 'arquivo'.")
        # Campo novo no Lookplay seria apagado sem aviso: é melhor parar e ajustar o código.
        desconhecidos = nomes - CAMPOS_ESPERADOS_NO_MODELO
        if desconhecidos:
            raise ErroLookplay(f"O modelo tem campos desconhecidos: {sorted(desconhecidos)}.")

        campos: list[tuple[str, str]] = []
        if "texto" in nomes:
            # O servidor escreve o valor atual assim: {{if ["SEMANA"].includes($index)}}
            atual = re.search(r"(\[[^\]]*\])\.includes\(\$index\)", texto)
            if atual is None:
                raise ErroLookplay("Não foi possível ler o valor atual do campo 'texto'.")
            campos = [("texto", valor) for valor in json.loads(atual.group(1))]

        arquivo = re.search(r'/storage/conteudo/([^"\'<>\s]+)', texto)
        return campos, arquivo.group(1) if arquivo else None

    # ---------- Envio ----------

    def enviar_csv(self, tipo: Tipo, filial: str, nome_arquivo: str, conteudo: bytes) -> None:
        """Envia o CSV. Sem exceção, o Lookplay aceitou o envio."""
        indice = self._indice(tipo)
        filtro = self._filtros[tipo]
        linha = indice.get(filial)
        if linha is None:
            raise ConteudoNaoEncontrado(f"Sem conteúdo '{filial}' no filtro {filtro} do Lookplay.")
        if filial in self._repetidos[tipo]:
            raise ErroLookplay(f"Há mais de um conteúdo '{filial}' no filtro {filtro}. Envio cancelado.")

        tela = self._abrir_edicao(linha)
        dados = campos_do_formulario(tela.form) + tela.campos_do_modelo + [("_edit", "")]

        resposta = self._http.post(
            urljoin(tela.url, tela.form.get("action") or ""),
            data=dados,
            files={"arquivo": (nome_arquivo, conteudo, "text/csv")},
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()

        # HTTP 200 não basta: sessão expirada também pode responder 200.
        soup = _ler_html(resposta)
        if _eh_tela_de_login(soup):
            raise SessaoInvalida("A sessão do Lookplay expirou durante o envio.")
        alerta = soup.select_one(".alert-danger")
        if alerta is not None:
            raise ErroLookplay(f"O Lookplay recusou o arquivo: {alerta.get_text(' ', strip=True)[:200]}")

        # Prova do envio: o Lookplay gera um nome novo para cada arquivo recebido.
        if self._abrir_edicao(linha).arquivo_atual == tela.arquivo_atual:
            raise ErroLookplay("O Lookplay respondeu, mas o arquivo atual não mudou.")

    # ---------- Diagnóstico (só leitura) ----------

    def inspecionar(self, tipo: Tipo) -> dict:
        """Mostra o que o cliente enxerga no Lookplay, sem enviar nada nem expor valores."""
        indice = self._indice(tipo)
        tela = self._abrir_edicao(next(iter(indice.values())))
        return {
            "filtro": self._filtros[tipo],
            "conteudos_na_listagem": len(indice),
            "exemplos_de_filial": list(indice)[:5],
            "campos_do_formulario": [nome for nome, _ in campos_do_formulario(tela.form)],
            "campos_do_modelo": [nome for nome, _ in tela.campos_do_modelo],
            "tem_arquivo_atual": tela.arquivo_atual is not None,
            "titulos_repetidos": sorted(self._repetidos[tipo]),
        }
