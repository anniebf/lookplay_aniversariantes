"""Agendamento: roda uma vez na janela de segunda, não roda fora dela e não duplica."""
from datetime import datetime, time, timedelta

from app.agendador import Agendador, ler_dia
from app.historico import ExecucaoEmAndamento

SEGUNDA_6H05 = datetime(2026, 11, 9, 6, 5)


class OrquestradorFalso:
    """Registra a execução no histórico real, como o orquestrador faria."""

    def __init__(self, historico, ocupado=False):
        self.historico = historico
        self.ocupado = ocupado
        self.chamadas = 0

    def executar_agora(self, escolha, filial, origem):
        if self.ocupado:
            raise ExecucaoEmAndamento("ocupado")
        self.chamadas += 1
        execucao = self.historico.criar_execucao(origem, escolha, filial)
        self.historico.finalizar(execucao, "concluida")
        return execucao


def agendador(historico, orquestrador, agora):
    return Agendador(orquestrador, historico, 0, time(6, 0), timedelta(hours=2), relogio=lambda: agora)


def test_roda_uma_vez_na_janela(historico):
    orquestrador = OrquestradorFalso(historico)
    a = agendador(historico, orquestrador, SEGUNDA_6H05)
    assert a.verificar() is not None
    assert a.verificar(SEGUNDA_6H05 + timedelta(minutes=1)) is None  # já rodou nesta janela
    assert orquestrador.chamadas == 1
    assert historico.listar(1)[0]["origem"] == "agendamento"


def test_nao_roda_antes_nem_depois_da_janela(historico):
    orquestrador = OrquestradorFalso(historico)
    a = agendador(historico, orquestrador, SEGUNDA_6H05)
    assert a.verificar(datetime(2026, 11, 9, 5, 59)) is None
    assert a.verificar(datetime(2026, 11, 9, 10, 49)) is None  # reiniciar numa segunda tarde não envia
    assert a.verificar(datetime(2026, 11, 10, 6, 5)) is None  # terça
    assert orquestrador.chamadas == 0


def test_envio_manual_nao_conta_como_automatico(historico):
    historico.finalizar(historico.criar_execucao("dashboard", "ambos", None), "concluida")
    orquestrador = OrquestradorFalso(historico)
    assert agendador(historico, orquestrador, SEGUNDA_6H05).verificar() is not None


def test_execucao_em_andamento_adia_para_a_proxima_verificacao(historico):
    orquestrador = OrquestradorFalso(historico, ocupado=True)
    a = agendador(historico, orquestrador, SEGUNDA_6H05)
    assert a.verificar() is None
    orquestrador.ocupado = False
    assert a.verificar(SEGUNDA_6H05 + timedelta(minutes=10)) is not None


def test_proximo_envio():
    a = Agendador(None, None, 0, time(6, 0))
    assert a.proxima(datetime(2026, 11, 9, 5, 0)) == datetime(2026, 11, 9, 6, 0)
    assert a.proxima(datetime(2026, 11, 9, 10, 49)) == datetime(2026, 11, 16, 6, 0)
    assert a.proxima(datetime(2026, 11, 12, 9, 0)) == datetime(2026, 11, 16, 6, 0)


def test_ler_dia():
    assert ler_dia("segunda") == 0 and ler_dia("Sábado") == 5 and ler_dia("6") == 6
