"""
Dublês compartilhados pelos testes. Por padrão, nenhum teste acessa Oracle ou Lookplay reais.

Com ORACLE_TESTE=1, os testes que usam o histórico rodam também no Oracle do .env, em
tabelas temporárias (LOOKPLAY_TESTE_<pid>) que são apagadas no fim. As de produção não são tocadas.
"""
import os
from datetime import date

import pytest

from app.dominio import Aniversariante, Tipo
from app.historico import Historico, SQLite

HOJE = date(2026, 11, 5)  # quinta-feira; a semana vai de 02/11 a 08/11


class RepositorioFalso:
    def __init__(self):
        self.dados = {
            Tipo.VIDA: {"Alfa": [Aniversariante("Alfa", "ANA ALVES", "03/11/1990", "10/01/2015", "RH")]},
            Tipo.CASA: {
                "Alfa": [
                    Aniversariante("Alfa", "BIA LOPES", "01/02/1985", "04/11/2016", "TI"),  # nesta semana
                    Aniversariante("Alfa", "CAIO REIS", "01/03/1985", "20/11/2020", "TI"),  # só no mês
                ]
            },
        }

    def filiais(self):
        return ["Alfa", "Beta"]

    def aniversariantes(self, tipo):
        return self.dados[tipo]


class ClienteFalso:
    def __init__(self, erro_por_filial=None, erro_sessao=None):
        self.envios = []
        self.erro_por_filial = erro_por_filial or {}
        self.erro_sessao = erro_sessao

    def abrir_sessao(self):
        if self.erro_sessao:
            raise self.erro_sessao

    def conteudos(self, tipo):
        return {"Beta"}

    def enviar_csv(self, tipo, filial, nome_arquivo, conteudo):
        if filial in self.erro_por_filial:
            raise self.erro_por_filial[filial]
        self.envios.append((tipo, filial, nome_arquivo, conteudo))


BANCOS = ["sqlite"] + (["oracle"] if os.getenv("ORACLE_TESTE") == "1" else [])


def alterar(historico, sql, **parametros):
    """Ajuste direto no banco, usado para simular dados antigos. {execucoes}, {itens}... viram os nomes reais."""
    with historico._sessao() as s:
        s.executar(historico._sql(sql), **parametros)


@pytest.fixture(scope="session")
def oracle_teste():
    from app.config import carregar_config
    from app.historico import Oracle

    config = carregar_config()
    dialeto = Oracle(config.oracle_usuario, config.oracle_senha, config.oracle_dsn, config.historico_schema)
    tabela = f"LOOKPLAY_TESTE_{os.getpid()}"
    Historico(dialeto, tabela)
    yield dialeto, tabela
    nomes = dialeto.nomes(tabela)
    with dialeto.sessao() as cursor:
        for chave in ("pessoas", "eventos", "itens", "execucoes"):  # filhas antes da principal
            cursor.execute(f"DROP TABLE {nomes[chave]} PURGE")


@pytest.fixture(params=BANCOS)
def historico(request, tmp_path):
    if request.param == "sqlite":
        return Historico(SQLite(tmp_path / "historico.db"))
    dialeto, tabela = request.getfixturevalue("oracle_teste")
    historico = Historico(dialeto, tabela)
    for chave in ("pessoas", "eventos", "itens", "execucoes"):  # cada teste começa vazio
        alterar(historico, f"DELETE FROM {{{chave}}}")
    return historico


@pytest.fixture
def repositorio():
    return RepositorioFalso()
