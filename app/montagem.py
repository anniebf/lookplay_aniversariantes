"""Liga as peças conforme o modo: exemplo (falso) ou producao (Oracle e Lookplay reais)."""
import logging
from logging.handlers import RotatingFileHandler

from datetime import timedelta

from app.agendador import Agendador
from app.cliente_lookplay import ClienteLookplay
from app.config import RAIZ, Config
from app.historico import Historico, Oracle, SQLite
from app.modo_exemplo import ClienteExemplo, RepositorioExemplo
from app.orquestrador import Orquestrador
from app.painel import Painel
from app.repositorio_oracle import RepositorioOracle


def configurar_log(em_arquivo: bool = True) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if em_arquivo:
        (RAIZ / "logs").mkdir(exist_ok=True)
        handlers.append(RotatingFileHandler(RAIZ / "logs" / "lookplay.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def criar_historico(config: Config) -> Historico:
    """Oracle em produção (schema e tabela configuráveis); SQLite no modo exemplo."""
    if config.historico == "oracle":
        dialeto = Oracle(config.oracle_usuario, config.oracle_senha, config.oracle_dsn, config.historico_schema)
        return Historico(dialeto, config.historico_tabela)
    return Historico(SQLite(config.sqlite_path), config.historico_tabela)


def criar_agendador(config: Config, orquestrador: Orquestrador, historico: Historico) -> Agendador | None:
    if not config.agenda_ativa:
        return None
    return Agendador(
        orquestrador, historico, config.agenda_dia, config.agenda_hora,
        timedelta(minutes=config.agenda_tolerancia_minutos),
    )


def montar(config: Config) -> tuple[Painel, Orquestrador, Historico]:
    historico = criar_historico(config)
    if config.producao:
        repositorio = RepositorioOracle(config)
        fabrica_cliente = lambda: ClienteLookplay(config)
    else:
        repositorio = RepositorioExemplo()
        fabrica_cliente = ClienteExemplo

    painel = Painel(repositorio, historico, config.cache_consulta_segundos, fabrica_cliente=fabrica_cliente)
    orquestrador = Orquestrador(
        repositorio,
        fabrica_cliente,
        historico,
        pausa=config.pausa_entre_envios if config.producao else 0,
        ao_terminar=painel.invalidar_cache,
    )
    return painel, orquestrador, historico
