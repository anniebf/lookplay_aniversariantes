"""
Implementações falsas do Oracle e do Lookplay, usadas quando MODO=exemplo.

Permitem ver o dashboard funcionando sem acesso a nenhum sistema. As pessoas são
fictícias, e as datas são calculadas a partir de hoje para sempre haver dados na
semana atual. A escolha é fixa por filial, para a tela não mudar a cada consulta.
"""
import random
import time
from datetime import date, timedelta

from app.cliente_lookplay import ConteudoNaoEncontrado, ErroLookplay
from app.dominio import Aniversariante, Tipo

FILIAIS = [
    "Matriz", "Filial Alfa", "Filial Beta", "Filial Gama", "Filial Delta", "Filial Epsilon", "Filial Zeta",
    "Filial Eta", "Filial Teta", "Filial Iota", "Filial Kapa", "Filial Lambda", "Filial Mi", "Filial Ni",
    "Filial Omicron", "Filial Pi", "Filial Ro", "Filial Sigma", "Filial Tau", "Filial Upsilon",
]
# Para mostrar os outros estados na tela: uma filial sem conteúdo no Lookplay e uma com erro.
SEM_CONTEUDO = "Filial Kapa"
COM_ERRO = "Filial Lambda"
NOMES = ["ANA", "BRUNO", "CARLA", "DIEGO", "ELISA", "FABIO", "GISELE", "HENRIQUE", "IRIS", "JOAO", "LARA", "MARCOS"]
SOBRENOMES = ["ALVES", "BARROS", "CAMPOS", "DUARTE", "FARIAS", "GOMES", "LOPES", "MOURA", "NUNES", "PRADO"]
SECOES = ["Administrativo", "Logística", "Manutenção", "Operações Agrícolas", "Recursos Humanos", "Tecnologia"]


class RepositorioExemplo:
    def __init__(self, hoje: date | None = None):
        self._hoje = hoje or date.today()

    def filiais(self) -> list[str]:
        return list(FILIAIS)

    def aniversariantes(self, tipo: Tipo) -> dict[str, list[Aniversariante]]:
        segunda = self._hoje - timedelta(days=self._hoje.weekday())
        resultado = {}
        for filial in FILIAIS:
            sorteio = random.Random(f"{filial}-{tipo.value}")
            pessoas = [self._pessoa(filial, tipo, sorteio, segunda) for _ in range(sorteio.choice([0, 0, 1, 1, 2, 3, 5]))]
            if pessoas:
                resultado[filial] = sorted(pessoas, key=lambda p: p.nome)
        return resultado

    def _pessoa(self, filial: str, tipo: Tipo, sorteio: random.Random, segunda: date) -> Aniversariante:
        ano = self._hoje.year
        dia = segunda + timedelta(days=sorteio.randrange(7))
        if tipo is Tipo.VIDA:
            nascimento = date(ano - sorteio.randint(20, 58), dia.month, min(dia.day, 28))
            admissao = date(ano - sorteio.randint(1, 15), sorteio.randint(1, 12), sorteio.randint(1, 28))
        else:
            # Como na consulta real, "casa" traz o mês todo.
            dia_mes = dia if sorteio.random() < 0.5 else self._hoje.replace(day=sorteio.randint(1, 28))
            nascimento = date(ano - sorteio.randint(20, 58), sorteio.randint(1, 12), sorteio.randint(1, 28))
            admissao = date(ano - sorteio.randint(1, 15), dia_mes.month, min(dia_mes.day, 28))
        nome = f"{sorteio.choice(NOMES)} {sorteio.choice(SOBRENOMES)}"
        return Aniversariante(filial, nome, nascimento.strftime("%d/%m/%Y"), admissao.strftime("%d/%m/%Y"), sorteio.choice(SECOES))


class ClienteExemplo:
    """Simula o Lookplay com uma filial sem conteúdo e uma com erro, para mostrar esses estados."""

    def abrir_sessao(self) -> None:
        time.sleep(0.2)

    def conteudos(self, tipo: Tipo) -> set[str]:
        return set(FILIAIS) - {SEM_CONTEUDO}

    def enviar_csv(self, tipo: Tipo, filial: str, nome_arquivo: str, conteudo: bytes) -> None:
        time.sleep(0.05)
        if filial == SEM_CONTEUDO:
            raise ConteudoNaoEncontrado(f"Sem conteúdo '{filial}' neste filtro do Lookplay.")
        if filial == COM_ERRO:
            raise ErroLookplay("O Lookplay recusou o arquivo (erro simulado).")
