"""
Configuração do projeto.

Toda opção vem de variáveis de ambiente ou do arquivo .env: nenhum segredo nem
dado específico da empresa (endereço do Lookplay, usuário, view do Oracle, regras
de exclusão) fica no código. Os nomes usernameBd, passwordBd e dsn são os mesmos do
projeto anterior, o que permite copiar os valores do .env antigo.

No Docker, as mesmas variáveis chegam pelo env_file do docker-compose.
"""
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

from app.agendador import ler_dia

RAIZ = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    modo: str = "exemplo"
    lookplay_url: str = ""
    lookplay_usuario: str = ""
    lookplay_cookies: str = ""
    lookplay_senha: str = ""
    # Filtro do Lookplay usado por tipo. O _3 é o que aparece na TV hoje.
    filtro_vida: str = "ANIVERSARIANTES_3"
    filtro_casa: str = "TEMPO_CASA_3"
    oracle_usuario: str = ""
    oracle_senha: str = ""
    oracle_dsn: str = ""
    # View de funcionários e regras da empresa (ver .env.example).
    oracle_view: str = ""
    empresas_excluidas: frozenset = frozenset()
    secoes_excluidas: frozenset = frozenset()  # {(empresa, secao)}
    secoes_renomeadas: dict = field(default_factory=dict)  # {(empresa, secao): novo_nome}
    # Filial exibida em destaque e no topo do dashboard.
    filial_matriz: str = ""
    dashboard_usuario: str = "admin"
    dashboard_senha: str = ""
    # Onde fica o histórico: "oracle" (produção) ou "sqlite" (exemplo e testes).
    historico: str = "sqlite"
    historico_schema: str = "RPA"
    historico_tabela: str = "LOOKPLAY_ANIVERSARIANTES"
    sqlite_path: Path = RAIZ / "dados" / "exemplo.db"
    # Envio automático: dia (0 = segunda), hora e quanto tempo depois ainda pode rodar.
    agenda_ativa: bool = True
    agenda_dia: int = 0
    agenda_hora: time = time(6, 0)
    agenda_tolerancia_minutos: int = 120
    # No Docker, o log vai só para a saída padrão (docker logs).
    log_em_arquivo: bool = True
    pausa_entre_envios: float = 0.5
    cache_consulta_segundos: int = 300

    @property
    def producao(self) -> bool:
        return self.modo == "producao"


# Nome da view: SCHEMA.VIEW ou VIEW, só letras, números e "_" (entra no SQL como texto).
VIEW_VALIDA = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*(\.[A-Za-z][A-Za-z0-9_$#]*)?$")
# Variáveis que precisam existir em produção, mesmo vazias: sem elas as regras mudariam sem aviso.
OBRIGATORIAS_PRODUCAO = (
    "LOOKPLAY_URL", "LOOKPLAY_USUARIO", "ORACLE_VIEW",
    "ORACLE_EMPRESAS_EXCLUIDAS", "ORACLE_SECOES_EXCLUIDAS", "ORACLE_SECOES_RENOMEADAS",
)


def _pares(texto: str) -> list[tuple[str, str]]:
    """'17|Seção A;16|Seção B' -> [('17', 'Seção A'), ('16', 'Seção B')]."""
    pares = []
    for item in filter(None, (parte.strip() for parte in texto.split(";"))):
        empresa, separador, secao = item.partition("|")
        if not separador:
            raise ValueError(f"Use EMPRESA|SEÇÃO, separado por ';'. Valor inválido: {item!r}.")
        pares.append((empresa.strip(), secao.strip()))
    return pares


def _renomeacoes(texto: str) -> dict:
    """'17|Seção A=Novo nome;...' -> {('17', 'Seção A'): 'Novo nome'}."""
    resultado = {}
    for item in filter(None, (parte.strip() for parte in texto.split(";"))):
        origem, separador, novo = item.rpartition("=")
        if not separador:
            raise ValueError(f"Use EMPRESA|SEÇÃO=NOVO NOME, separado por ';'. Valor inválido: {item!r}.")
        resultado[_pares(origem)[0]] = novo.strip()
    return resultado


def carregar_config() -> Config:
    """Lê o .env da raiz do projeto e monta a configuração."""
    load_dotenv(RAIZ / ".env")
    ler = lambda nome, padrao="": os.getenv(nome, padrao).strip()

    modo = ler("MODO", "exemplo").lower()
    config = Config(
        modo=modo,
        lookplay_url=ler("LOOKPLAY_URL").rstrip("/"),
        lookplay_usuario=ler("LOOKPLAY_USUARIO"),
        lookplay_cookies=ler("LOOKPLAY_COOKIES"),
        # LOOKPLAY_SENHA; o nome antigo senhawdg continua aceito.
        lookplay_senha=ler("LOOKPLAY_SENHA") or ler("senhawdg"),
        filtro_vida=ler("LOOKPLAY_FILTRO_VIDA", Config.filtro_vida),
        filtro_casa=ler("LOOKPLAY_FILTRO_CASA", Config.filtro_casa),
        oracle_usuario=ler("usernameBd"),
        oracle_senha=ler("passwordBd"),
        oracle_dsn=ler("dsn"),
        oracle_view=ler("ORACLE_VIEW"),
        empresas_excluidas=frozenset(filter(None, (e.strip() for e in ler("ORACLE_EMPRESAS_EXCLUIDAS").split(",")))),
        secoes_excluidas=frozenset(_pares(ler("ORACLE_SECOES_EXCLUIDAS"))),
        secoes_renomeadas=_renomeacoes(ler("ORACLE_SECOES_RENOMEADAS")),
        filial_matriz=ler("FILIAL_MATRIZ"),
        dashboard_usuario=ler("DASHBOARD_USUARIO", "admin"),
        dashboard_senha=ler("DASHBOARD_SENHA"),
        pausa_entre_envios=float(ler("PAUSA_ENTRE_ENVIOS", "0.5")),
        cache_consulta_segundos=int(ler("CACHE_CONSULTA_SEGUNDOS", "300")),
        historico=ler("HISTORICO", "oracle" if modo == "producao" else "sqlite").lower(),
        historico_schema=ler("HISTORICO_SCHEMA", Config.historico_schema),
        historico_tabela=ler("HISTORICO_TABELA", Config.historico_tabela),
        sqlite_path=Path(ler("SQLITE_PATH", str(Config.sqlite_path))),
        log_em_arquivo=ler("LOG_EM_ARQUIVO", "1") != "0",
        agenda_ativa=ler("AGENDA_ATIVA", "1") != "0",
        agenda_dia=ler_dia(ler("AGENDA_DIA", "segunda")),
        agenda_hora=time.fromisoformat(ler("AGENDA_HORA", "06:00")),
        agenda_tolerancia_minutos=int(ler("AGENDA_TOLERANCIA_MINUTOS", "120")),
    )
    if config.modo not in ("exemplo", "producao"):
        raise ValueError("MODO deve ser 'exemplo' ou 'producao'.")
    if config.producao:
        faltando = [nome for nome in OBRIGATORIAS_PRODUCAO if os.getenv(nome) is None]
        faltando += [n for n, v in (("LOOKPLAY_URL", config.lookplay_url), ("LOOKPLAY_USUARIO", config.lookplay_usuario),
                                    ("ORACLE_VIEW", config.oracle_view)) if not v and n not in faltando]
        if faltando:
            raise ValueError(f"Defina no .env para o modo producao: {', '.join(faltando)} (veja o .env.example).")
        if not VIEW_VALIDA.match(config.oracle_view):
            raise ValueError(f"ORACLE_VIEW inválida: {config.oracle_view!r}. Use SCHEMA.VIEW.")
    if config.historico not in ("oracle", "sqlite"):
        raise ValueError("HISTORICO deve ser 'oracle' ou 'sqlite'.")
    # O modo exemplo grava execuções fictícias: não podem ir para as tabelas de produção.
    if config.modo == "exemplo" and config.historico == "oracle":
        raise ValueError("No modo exemplo use HISTORICO=sqlite, para não gravar dados fictícios no Oracle.")
    return config
