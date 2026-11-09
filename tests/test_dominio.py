from datetime import date

from app.dominio import Aniversariante, aniversario_no_ano, dias_da_semana, gerar_csv, na_semana_atual, nome_do_csv

HOJE = date(2026, 11, 5)


def test_csv_mantem_o_formato_atual():
    conteudo = gerar_csv([Aniversariante("X", "ANA ALVES", "03/11/1990", "10/01/2015", "Gestão")])
    assert conteudo.decode("utf-8") == (
        "nome;data_de_nascimento;data_de_admissão;secao\r\n"
        "ANA ALVES;03/11/1990;10/01/2015;Gestão\r\n"
    )
    assert not conteudo.startswith(b"\xef\xbb\xbf")  # sem BOM


def test_nome_do_csv():
    assert nome_do_csv("Agua Azul") == "aniversariantes_Agua Azul.csv"


def test_29_de_fevereiro_vira_28_em_ano_comum():
    assert aniversario_no_ano(date(2000, 2, 29), 2026) == date(2026, 2, 28)


def test_semana_atual_segue_a_semana_iso():
    assert na_semana_atual(date(1990, 11, 2), HOJE)  # segunda
    assert na_semana_atual(date(1990, 11, 8), HOJE)  # domingo
    assert not na_semana_atual(date(1990, 11, 1), HOJE)
    assert not na_semana_atual(date(1990, 11, 9), HOJE)


def test_dias_da_semana_vao_de_segunda_a_domingo():
    dias = dias_da_semana(HOJE)
    assert (dias[0], dias[-1]) == (date(2026, 11, 2), date(2026, 11, 8))
