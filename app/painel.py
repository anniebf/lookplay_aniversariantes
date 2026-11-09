"""
Monta os dados do dashboard: junta o Oracle (quem faz aniversário), o Lookplay (quais
conteúdos existem) e o histórico (o que já rodou).

Cache curto: abrir o dashboard várias vezes não gera consultas repetidas ao Oracle.

LGPD: a API entrega só dia e mês. O ano de nascimento não sai daqui, e os anos de
casa aparecem apenas para o tipo "casa", onde fazem parte da homenagem.
"""
import logging
import threading
import time
from datetime import date, datetime

from app.dominio import Aniversariante, Tipo, aniversario_no_ano, data_de_referencia, dias_da_semana, na_semana_atual
from app.historico import Historico

logger = logging.getLogger(__name__)

# Da situação mais urgente para a menos urgente. A filial assume a pior entre vida e casa.
ORDEM_SITUACOES = ("erro", "sem_conteudo", "nao_rodou", "enviado", "sem_aniversariantes")


def situacao_do_tipo(ultimo: dict | None, total: int, hoje: date, no_lookplay: bool | None = None) -> str:
    """
    Responde "como está esta filial nesta semana?" para um tipo.
    Resultados de semanas anteriores não contam: quem tem aniversariantes e não
    rodou desde segunda-feira aparece como "nao_rodou".
    """
    # Tem gente no banco e o conteúdo não existe: nem adianta rodar. Não depende de execução.
    if total and no_lookplay is False:
        return "sem_conteudo"
    segunda = dias_da_semana(hoje)[0]
    rodou_na_semana = ultimo is not None and datetime.fromisoformat(ultimo["horario"]).date() >= segunda
    # "sem_conteudo" antigo deixa de valer quando o conteúdo passa a existir no Lookplay.
    resolvido = ultimo is not None and ultimo["status"] == "sem_conteudo" and no_lookplay is True
    if rodou_na_semana and ultimo["status"] != "sem_aniversariantes" and not resolvido:
        return ultimo["status"]
    # Rodou sem ninguém e agora tem gente (ou não rodou): ainda falta enviar.
    return "nao_rodou" if total else "sem_aniversariantes"


class Painel:
    def __init__(self, repositorio, historico: Historico, cache_segundos: int = 300, relogio=date.today, fabrica_cliente=None):
        self._repositorio = repositorio
        self._historico = historico
        self._cache_segundos = cache_segundos
        self._relogio = relogio
        self._fabrica_cliente = fabrica_cliente
        self._trava = threading.Lock()
        self._cache = None
        self._cache_em = 0.0

    def invalidar_cache(self) -> None:
        with self._trava:
            self._cache = None

    def _consultar(self, atualizar: bool) -> dict:
        with self._trava:
            vencido = time.monotonic() - self._cache_em > self._cache_segundos
            if atualizar or self._cache is None or vencido:
                self._cache = {
                    "filiais": self._repositorio.filiais(),
                    Tipo.VIDA: self._repositorio.aniversariantes(Tipo.VIDA),
                    Tipo.CASA: self._repositorio.aniversariantes(Tipo.CASA),
                    "lookplay": self._consultar_lookplay(),
                    # Momento real da leitura no banco, exibido ao lado de "Atualizar dados".
                    "atualizado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                self._cache_em = time.monotonic()
            return self._cache

    def _consultar_lookplay(self) -> dict | None:
        """Lê os conteúdos cadastrados. Se o Lookplay falhar, o dashboard continua com os dados do banco."""
        if self._fabrica_cliente is None:
            return None
        try:
            cliente = self._fabrica_cliente()
            cliente.abrir_sessao()
            return {tipo: cliente.conteudos(tipo) for tipo in Tipo}
        except Exception:
            logger.exception("Não foi possível consultar os conteúdos do Lookplay")
            return None

    def existe_filial(self, filial: str) -> bool:
        return filial in self._consultar(False)["filiais"]

    def visao_geral(self, atualizar: bool = False) -> dict:
        hoje = self._relogio()
        dados = self._consultar(atualizar)
        ultimos = self._historico.ultimos_por_filial()

        contagem_dias = {dia: {"vida": 0, "casa": 0} for dia in dias_da_semana(hoje)}
        filiais = []
        resumo = {situacao: 0 for situacao in ORDEM_SITUACOES}
        for filial in dados["filiais"]:
            item = {"filial": filial, "dias": set(), "tipos": {}}
            for tipo in Tipo:
                pessoas = dados[tipo].get(filial, [])
                na_semana = 0
                for pessoa in pessoas:
                    original = data_de_referencia(pessoa, tipo)
                    if not na_semana_atual(original, hoje):
                        continue
                    na_semana += 1
                    dia = self._dia_na_semana(original, hoje)
                    contagem_dias[dia][tipo.value] += 1
                    item["dias"].add(dia.isoformat())
                ultimo = ultimos.get((filial, tipo.value))
                no_lookplay = filial in dados["lookplay"][tipo] if dados["lookplay"] else None
                item["tipos"][tipo.value] = {
                    "semana": na_semana,
                    "total_enviado": len(pessoas),
                    "ultimo": ultimo,
                    "no_lookplay": no_lookplay,
                    "situacao": situacao_do_tipo(ultimo, len(pessoas), hoje, no_lookplay),
                }
            item["dias"] = sorted(item["dias"])
            situacoes = [dados_tipo["situacao"] for dados_tipo in item["tipos"].values()]
            item["situacao"] = next(s for s in ORDEM_SITUACOES if s in situacoes)
            resumo[item["situacao"]] += 1
            filiais.append(item)

        return {
            "hoje": hoje.isoformat(),
            "atualizado_em": dados["atualizado_em"],
            "semana": [{"data": dia.isoformat(), **totais} for dia, totais in contagem_dias.items()],
            "resumo": resumo,
            "lookplay_consultado": dados["lookplay"] is not None,
            "filiais": filiais,
        }

    @staticmethod
    def _dia_na_semana(original: date, hoje: date) -> date:
        """Dia da semana atual em que o aniversário cai (mesmo dia e mês)."""
        for dia in dias_da_semana(hoje):
            if (dia.month, dia.day) == (aniversario_no_ano(original, dia.year).month, aniversario_no_ano(original, dia.year).day):
                return dia
        # Virada de ano: o Oracle compara só o número da semana, então pode não haver dia exato.
        aniversario = aniversario_no_ano(original, hoje.year)
        return dias_da_semana(hoje)[aniversario.weekday()]

    def enviados(self, filial: str) -> dict:
        """Quem foi no último envio bem-sucedido de cada tipo, vindo do histórico."""
        return {tipo.value: self._historico.ultimo_envio(filial, tipo.value) for tipo in Tipo}

    def aniversariantes(self, filial: str, tipo: Tipo | None = None) -> list[dict]:
        hoje = self._relogio()
        dados = self._consultar(False)
        resultado = []
        for tipo_atual in ([tipo] if tipo else list(Tipo)):
            for pessoa in dados[tipo_atual].get(filial, []):
                resultado.append(self._pessoa_para_tela(pessoa, tipo_atual, hoje))
        return sorted(resultado, key=lambda p: (p["tipo"], p["mes_dia"], p["nome"]))

    @staticmethod
    def _pessoa_para_tela(pessoa: Aniversariante, tipo: Tipo, hoje: date) -> dict:
        original = data_de_referencia(pessoa, tipo)
        tela = {
            "tipo": tipo.value,
            "nome": " ".join(pessoa.nome.split()).title(),
            "secao": pessoa.secao,
            "dia": original.strftime("%d/%m"),
            "mes_dia": original.strftime("%m%d"),
            "na_semana": na_semana_atual(original, hoje),
        }
        if tipo is Tipo.CASA:
            tela["anos_de_casa"] = hoje.year - original.year
        return tela
