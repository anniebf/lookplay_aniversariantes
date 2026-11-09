"""
API REST e página do dashboard (FastAPI).

Rotas:
    GET  /                                      dashboard
    GET  /api/status                            modo e execução em andamento
    GET  /api/filiais                           visão por filial e semana
    GET  /api/filiais/{filial}/aniversariantes  pessoas de uma filial
    GET  /api/filiais/{filial}/enviados         quem foi no último envio de cada tipo
    GET  /api/filiais/{filial}/log              passos da execução atual ou da última da filial
    GET  /api/execucoes                         histórico
    GET  /api/execucoes/{id}                    detalhe, filial por filial
    POST /api/execucoes                         roda de novo (todas ou uma filial)

Segurança: as rotas expõem dados pessoais e disparam envios, por isso exigem
usuário e senha (HTTP Basic). O modo producao não sobe sem senha configurada.
O POST só aceita JSON e não há CORS liberado: outro site não consegue dispará-lo.
"""
import logging
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from app.config import Config
from app.dominio import Tipo
from app.historico import ExecucaoEmAndamento, Historico
from app.orquestrador import Orquestrador
from app.painel import Painel

logger = logging.getLogger(__name__)
PAGINA = Path(__file__).parent / "static" / "index.html"


class PedidoExecucao(BaseModel):
    tipo: Literal["vida", "casa", "ambos"] = "ambos"
    filial: str | None = None


def _descrever_conflito(ativa: dict | None) -> str:
    """Mensagem de quem tentou rodar enquanto outra execução está em andamento: diz o que está rodando."""
    if ativa is None:  # terminou entre a tentativa e esta consulta
        return "Havia uma execução em andamento, que acabou de terminar. Tente de novo."
    tipo = {"ambos": "vida e empresa", "vida": "vida", "casa": "empresa"}[ativa["tipo"]]
    if ativa["filial"]:
        return f"Já está rodando: {ativa['filial']} ({tipo}). Aguarde terminar para rodar de novo."
    agora = f", agora em {ativa['filial_atual']}" if ativa.get("filial_atual") else ""
    return f"Já está rodando: todas as filiais ({tipo}{agora}). Aguarde terminar para rodar de novo."


def criar_api(config: Config, painel: Painel, orquestrador: Orquestrador, historico: Historico, agendador=None) -> FastAPI:
    if config.producao and not config.dashboard_senha:
        raise RuntimeError("Defina DASHBOARD_SENHA no .env para usar o modo producao.")

    basic = HTTPBasic(auto_error=False)

    def autenticar(credenciais: HTTPBasicCredentials | None = Depends(basic)) -> None:
        # Sem senha configurada (permitido só no modo exemplo), o acesso é livre.
        if not config.dashboard_senha:
            return
        # compare_digest evita descobrir a senha medindo o tempo de resposta.
        valido = credenciais is not None and all(
            [
                secrets.compare_digest(credenciais.username.encode(), config.dashboard_usuario.encode()),
                secrets.compare_digest(credenciais.password.encode(), config.dashboard_senha.encode()),
            ]
        )
        if not valido:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha incorretos.", {"WWW-Authenticate": "Basic"})

    api = FastAPI(title="Lookplay Aniversariantes", version="1.0.0", dependencies=[Depends(autenticar)])

    @api.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(PAGINA)

    @api.get("/api/status")
    def obter_status():
        proximo = agendador.proxima().astimezone().isoformat(timespec="seconds") if agendador else None
        return {
            "modo": config.modo,
            "usuario_lookplay": config.lookplay_usuario,
            "em_andamento": historico.em_andamento(),
            "proximo_envio_automatico": proximo,
            "filial_matriz": config.filial_matriz,
        }

    @api.get("/api/filiais")
    def listar_filiais(atualizar: bool = False):
        try:
            return painel.visao_geral(atualizar)
        except Exception as erro:
            # O detalhe técnico fica no log, sem expor dados de conexão na tela.
            logger.exception("Falha ao consultar os aniversariantes")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Não foi possível consultar o banco de dados.") from erro

    @api.get("/api/filiais/{filial}/aniversariantes")
    def listar_aniversariantes(filial: str, tipo: Tipo | None = None):
        if not painel.existe_filial(filial):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Filial não encontrada.")
        return painel.aniversariantes(filial, tipo)

    @api.get("/api/filiais/{filial}/enviados")
    def listar_enviados(filial: str):
        if not painel.existe_filial(filial):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Filial não encontrada.")
        return painel.enviados(filial)

    @api.get("/api/filiais/{filial}/log")
    def obter_log(filial: str):
        if not painel.existe_filial(filial):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Filial não encontrada.")
        return historico.log_da_filial(filial)

    @api.get("/api/execucoes")
    def listar_execucoes(limite: int = Query(20, ge=1, le=200)):
        return historico.listar(limite)

    @api.get("/api/execucoes/{execucao_id}")
    def obter_execucao(execucao_id: int):
        execucao = historico.obter(execucao_id)
        if execucao is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execução não encontrada.")
        return execucao

    @api.post("/api/execucoes", status_code=status.HTTP_202_ACCEPTED)
    def criar_execucao(pedido: PedidoExecucao):
        if pedido.filial and not painel.existe_filial(pedido.filial):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Filial não encontrada.")
        try:
            execucao_id = orquestrador.iniciar_em_segundo_plano(pedido.tipo, pedido.filial, "dashboard")
        except ExecucaoEmAndamento as erro:
            raise HTTPException(status.HTTP_409_CONFLICT, _descrever_conflito(historico.em_andamento())) from erro
        return {"id": execucao_id}

    return api
