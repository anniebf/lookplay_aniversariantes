"""Ponto de entrada do servidor: python -m uvicorn app.main:app --host 0.0.0.0 --port 8765"""
from app.api import criar_api
from app.config import carregar_config
from app.montagem import configurar_log, criar_agendador, montar

_config = carregar_config()
configurar_log(_config.log_em_arquivo)
_painel, _orquestrador, _historico = montar(_config)
_agendador = criar_agendador(_config, _orquestrador, _historico)
app = criar_api(_config, _painel, _orquestrador, _historico, _agendador)

# O servidor que fica no ar também dispara o envio semanal (padrão: segunda, 06:00).
if _agendador:
    _agendador.iniciar()

# No modo exemplo, a primeira abertura já mostra uma execução no histórico.
if not _config.producao and not _historico.listar(1):
    _orquestrador.iniciar_em_segundo_plano("ambos", None, "exemplo")
