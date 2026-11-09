"""Regras da empresa (vindas do .env) aplicadas às linhas do Oracle, sem precisar do banco."""
from app.config import Config, _pares, _renomeacoes
from app.repositorio_oracle import aplicar_regras

LINHAS = [
    ("ANA ALVES", "03/11/1990", "01/01/2015", "RH", "Alfa", "10"),
    ("BIA LOPES", "04/11/1991", "01/01/2016", "Conselho", "Alfa", "20"),
    ("CAIO REIS", "05/11/1992", "01/01/2017", "Diretoria Geral", "Beta", "20"),
    ("DANI MELO", "06/11/1993", "01/01/2018", "TI", "Beta", "99"),
]


def test_exclusoes_e_renomeacoes_do_env():
    config = Config(
        empresas_excluidas=frozenset({"99"}),
        secoes_excluidas=frozenset(_pares("20|Conselho")),
        secoes_renomeadas=_renomeacoes("20|Diretoria Geral=Diretoria"),
    )
    resultado = aplicar_regras(LINHAS, config)
    assert [p.nome for p in resultado["Alfa"]] == ["ANA ALVES"]  # Conselho excluído
    assert [(p.nome, p.secao) for p in resultado["Beta"]] == [("CAIO REIS", "Diretoria")]  # empresa 99 excluída


def test_sem_regras_nada_e_excluido():
    assert sum(len(v) for v in aplicar_regras(LINHAS, Config()).values()) == 4


def test_formato_das_regras():
    assert _pares("17|Seção A; 16|Seção B ;") == [("17", "Seção A"), ("16", "Seção B")]
    assert _renomeacoes("17|Seção - A=Nova") == {("17", "Seção - A"): "Nova"}
