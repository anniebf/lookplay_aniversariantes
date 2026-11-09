"""
Agendamento interno: o próprio sistema dispara o envio, sem Airflow nem Agendador do Windows.

Lógica: uma thread acorda a cada 30 segundos e verifica se está na janela do envio
(padrão: segunda-feira, das 06:00 até 2 horas depois). Se estiver e ainda não houve
envio automático nessa janela, roda todas as filiais, vida e empresa.

- A verificação "já rodou?" consulta o histórico no Oracle. Assim, se houver mais de um
  servidor no ar (ex.: o container e a máquina antiga), só um deles envia.
- Se o servidor estiver desligado às 06:00 e subir dentro da janela, o envio acontece
  ao subir. Fora da janela não: reiniciar o servidor numa segunda à tarde não envia nada.
- Se já houver uma execução em andamento (ex.: alguém clicou em "Rodar"), tenta de novo
  na próxima verificação, ainda dentro da janela.
"""
import logging
import threading
from datetime import datetime, time, timedelta

from app.historico import ExecucaoEmAndamento

logger = logging.getLogger(__name__)

ORIGEM = "agendamento"
DIAS = {"segunda": 0, "terca": 1, "terça": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "sábado": 5, "domingo": 6}


def ler_dia(texto: str) -> int:
    """Aceita o nome do dia em português ou o número (0 = segunda)."""
    texto = texto.strip().lower()
    if texto.isdigit() and 0 <= int(texto) <= 6:
        return int(texto)
    if texto not in DIAS:
        raise ValueError(f"AGENDA_DIA inválido: {texto!r}. Use segunda, terca, ..., domingo.")
    return DIAS[texto]


class Agendador:
    def __init__(self, orquestrador, historico, dia_semana: int = 0, hora: time = time(6, 0),
                 tolerancia: timedelta = timedelta(hours=2), relogio=datetime.now, intervalo_segundos: int = 30):
        self._orquestrador = orquestrador
        self._historico = historico
        self._dia = dia_semana
        self._hora = hora
        self._tolerancia = tolerancia
        self._relogio = relogio
        self._intervalo = intervalo_segundos
        self._parar = threading.Event()

    def _inicio_da_janela(self, agora: datetime) -> datetime | None:
        """Horário agendado de hoje, se hoje for o dia do envio."""
        if agora.weekday() != self._dia:
            return None
        return datetime.combine(agora.date(), self._hora)

    def proxima(self, agora: datetime | None = None) -> datetime:
        """Próximo envio automático, exibido no dashboard."""
        agora = agora or self._relogio()
        dias = (self._dia - agora.weekday()) % 7
        candidato = datetime.combine(agora.date() + timedelta(days=dias), self._hora)
        return candidato if candidato > agora else candidato + timedelta(days=7)

    def verificar(self, agora: datetime | None = None) -> int | None:
        """Roda o envio se estiver na janela e ainda não rodou. Devolve o id da execução, se rodou."""
        agora = agora or self._relogio()
        inicio = self._inicio_da_janela(agora)
        if inicio is None or not (inicio <= agora < inicio + self._tolerancia):
            return None
        if self._historico.houve_execucao(ORIGEM, desde=inicio):
            return None
        try:
            logger.info("Iniciando o envio automático agendado para %s.", inicio.strftime("%d/%m %H:%M"))
            return self._orquestrador.executar_agora("ambos", None, ORIGEM)
        except ExecucaoEmAndamento:
            logger.info("Envio automático adiado: já há uma execução em andamento.")
            return None

    def _laco(self) -> None:
        while not self._parar.wait(self._intervalo):
            try:
                self.verificar()
            except Exception:  # ex.: Oracle fora do ar; tenta de novo na próxima volta
                logger.exception("Falha ao verificar o agendamento")

    def iniciar(self) -> None:
        threading.Thread(target=self._laco, name="agendador", daemon=True).start()
        logger.info("Agendamento ativo. Próximo envio automático: %s.", self.proxima().strftime("%d/%m/%Y %H:%M"))

    def parar(self) -> None:
        self._parar.set()
