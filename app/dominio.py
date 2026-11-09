"""
Regras de negócio sem dependência de banco, rede ou framework.

Por ficarem isoladas, estas regras podem ser testadas sem Oracle nem Lookplay.

Atenção: a consulta de vida retorna a SEMANA atual, mas a de casa retorna o MÊS
atual (é o que o script antigo já enviava à TV). Por isso, o dashboard calcula
aqui quem de "casa" faz aniversário nesta semana.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


class Tipo(str, Enum):
    VIDA = "vida"
    CASA = "casa"


@dataclass(frozen=True)
class Aniversariante:
    """Uma linha retornada pela consulta. As datas ficam como dd/mm/aaaa."""

    filial: str
    nome: str
    nascimento: str
    admissao: str
    secao: str


def _para_data(texto: str) -> date:
    return datetime.strptime(texto, "%d/%m/%Y").date()


def data_de_referencia(pessoa: Aniversariante, tipo: Tipo) -> date:
    """A data que importa para o tipo: nascimento (vida) ou admissão (casa)."""
    return _para_data(pessoa.nascimento if tipo is Tipo.VIDA else pessoa.admissao)


def aniversario_no_ano(original: date, ano: int) -> date:
    """Traz a data para o ano informado. 29/02 vira 28/02, como no SQL atual."""
    if (original.month, original.day) == (2, 29):
        return date(ano, 2, 28)
    return date(ano, original.month, original.day)


def na_semana_atual(original: date, hoje: date) -> bool:
    """Compara a semana ISO, o mesmo critério do to_char(..., 'iw') do Oracle."""
    aniversario = aniversario_no_ano(original, hoje.year)
    return aniversario.isocalendar()[1] == hoje.isocalendar()[1]


def dias_da_semana(hoje: date) -> list[date]:
    """Lista de segunda a domingo da semana de 'hoje'."""
    segunda = hoje - timedelta(days=hoje.weekday())
    return [segunda + timedelta(days=i) for i in range(7)]


def gerar_csv(pessoas: list[Aniversariante]) -> bytes:
    """
    Gera o CSV em memória, no mesmo formato aceito hoje pelo Lookplay:
    separador ';', UTF-8 sem BOM e fim de linha CRLF. Sem arquivo em disco, os
    dados pessoais não ficam espalhados pela máquina.
    """
    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escritor.writerow(["nome", "data_de_nascimento", "data_de_admissão", "secao"])
    for pessoa in pessoas:
        escritor.writerow([pessoa.nome, pessoa.nascimento, pessoa.admissao, pessoa.secao])
    return buffer.getvalue().encode("utf-8")


def resumo_do_envio(pessoas: list[Aniversariante], tipo: Tipo) -> list[dict]:
    """
    O que fica guardado de cada envio para consulta no dashboard: nome, dia e mês
    da data comemorada (nascimento ou admissão) e seção. O ano não é guardado.
    """
    datadas = [(data_de_referencia(pessoa, tipo), pessoa) for pessoa in pessoas]
    datadas.sort(key=lambda par: (par[0].month, par[0].day, par[1].nome))
    return [
        {"nome": " ".join(pessoa.nome.split()).title(), "dia": data.strftime("%d/%m"), "secao": pessoa.secao}
        for data, pessoa in datadas
    ]


def nome_do_csv(filial: str) -> str:
    return f"aniversariantes_{filial}.csv"
