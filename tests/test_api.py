import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api import criar_api
from app.cliente_lookplay import SessaoInvalida
from app.config import Config
from app.dominio import Tipo
from app.orquestrador import Orquestrador
from app.painel import Painel

from tests.conftest import HOJE, ClienteFalso, alterar


@pytest.fixture
def cliente_falso():
    return ClienteFalso()


def montar_cliente(repositorio, historico, cliente_falso, config=None):
    painel = Painel(repositorio, historico, relogio=lambda: HOJE)
    orquestrador = Orquestrador(repositorio, lambda: cliente_falso, historico, pausa=0, ao_terminar=painel.invalidar_cache)
    return TestClient(criar_api(config or Config(), painel, orquestrador, historico))


@pytest.fixture
def api(repositorio, historico, cliente_falso):
    return montar_cliente(repositorio, historico, cliente_falso)


def esperar_fim(api, execucao_id):
    for _ in range(100):
        execucao = api.get(f"/api/execucoes/{execucao_id}").json()
        if execucao["status"] != "em_andamento":
            return execucao
        time.sleep(0.02)
    raise AssertionError("A execução não terminou.")


def test_dashboard_e_entregue(api):
    resposta = api.get("/")
    assert resposta.status_code == 200 and "RPA Aniversariantes" in resposta.text


def test_visao_separa_casa_da_semana_e_do_mes(api):
    visao = api.get("/api/filiais").json()
    alfa = next(f for f in visao["filiais"] if f["filial"] == "Alfa")
    assert alfa["tipos"]["vida"]["semana"] == 1
    assert alfa["tipos"]["casa"]["semana"] == 1
    assert alfa["tipos"]["casa"]["total_enviado"] == 2
    assert alfa["dias"] == ["2026-11-03", "2026-11-04"]
    assert {d["data"]: d["vida"] + d["casa"] for d in visao["semana"]}["2026-11-03"] == 1


def test_api_nao_expoe_ano_de_nascimento(api):
    pessoas = api.get("/api/filiais/Alfa/aniversariantes").json()
    assert "1990" not in str(pessoas) and "1985" not in str(pessoas)
    casa = next(p for p in pessoas if p["tipo"] == "casa" and p["na_semana"])
    assert (casa["nome"], casa["dia"], casa["anos_de_casa"]) == ("Bia Lopes", "04/11", 10)


def test_filtro_por_tipo(api):
    pessoas = api.get("/api/filiais/Alfa/aniversariantes", params={"tipo": "vida"}).json()
    assert {p["tipo"] for p in pessoas} == {"vida"}


def test_filial_inexistente_responde_404(api):
    assert api.get("/api/filiais/Nada/aniversariantes").status_code == 404
    assert api.post("/api/execucoes", json={"filial": "Nada"}).status_code == 404


def test_rodar_de_novo_em_uma_filial(api, cliente_falso):
    resposta = api.post("/api/execucoes", json={"tipo": "casa", "filial": "Alfa"})
    assert resposta.status_code == 202
    execucao = esperar_fim(api, resposta.json()["id"])
    assert execucao["contagem"] == {"enviado": 1}
    assert execucao["inicio"] and execucao["fim"]

    alfa = next(f for f in api.get("/api/filiais").json()["filiais"] if f["filial"] == "Alfa")
    assert alfa["tipos"]["casa"]["ultimo"]["ultimo_envio"]
    assert alfa["tipos"]["vida"]["ultimo"] is None


def filial_da_visao(api, nome):
    return next(f for f in api.get("/api/filiais").json()["filiais"] if f["filial"] == nome)


def test_situacao_antes_de_rodar(api):
    assert filial_da_visao(api, "Alfa")["situacao"] == "nao_rodou"
    assert filial_da_visao(api, "Beta")["situacao"] == "sem_aniversariantes"
    assert api.get("/api/filiais").json()["resumo"]["nao_rodou"] == 1


def test_situacao_depois_de_enviar(api):
    esperar_fim(api, api.post("/api/execucoes", json={"filial": "Alfa"}).json()["id"])
    alfa = filial_da_visao(api, "Alfa")
    assert alfa["situacao"] == "enviado"
    assert alfa["tipos"]["vida"]["situacao"] == "enviado"


def test_filial_assume_a_pior_situacao_entre_vida_e_casa(api, cliente_falso):
    esperar_fim(api, api.post("/api/execucoes", json={"tipo": "vida", "filial": "Alfa"}).json()["id"])
    alfa = filial_da_visao(api, "Alfa")
    assert alfa["tipos"]["vida"]["situacao"] == "enviado"
    assert alfa["tipos"]["casa"]["situacao"] == "nao_rodou"
    assert alfa["situacao"] == "nao_rodou"


def test_envio_de_semana_anterior_nao_conta_como_rodou(api, historico):
    esperar_fim(api, api.post("/api/execucoes", json={"filial": "Alfa"}).json()["id"])
    alterar(historico, "UPDATE {itens} SET horario = :antigo", antigo=datetime(2026, 10, 1, 8))
    assert filial_da_visao(api, "Alfa")["situacao"] == "nao_rodou"


def api_com_lookplay(repositorio, historico, fabrica):
    painel = Painel(repositorio, historico, relogio=lambda: HOJE, fabrica_cliente=fabrica)
    orquestrador = Orquestrador(repositorio, fabrica, historico, pausa=0, ao_terminar=painel.invalidar_cache)
    return TestClient(criar_api(Config(), painel, orquestrador, historico))


def test_aniversariante_no_banco_sem_conteudo_aparece_sem_precisar_rodar(repositorio, historico):
    api = api_com_lookplay(repositorio, historico, ClienteFalso)  # o Lookplay só tem "Beta"
    visao = api.get("/api/filiais").json()
    alfa = next(f for f in visao["filiais"] if f["filial"] == "Alfa")
    assert visao["lookplay_consultado"] is True
    assert alfa["situacao"] == "sem_conteudo"
    assert alfa["tipos"]["vida"]["no_lookplay"] is False
    assert visao["resumo"]["sem_conteudo"] == 1


def test_filial_sem_aniversariantes_e_sem_conteudo_nao_e_problema(repositorio, historico):
    repositorio.dados = {Tipo.VIDA: {}, Tipo.CASA: {}}
    api = api_com_lookplay(repositorio, historico, ClienteFalso)
    assert api.get("/api/filiais").json()["resumo"]["sem_conteudo"] == 0


def test_lookplay_fora_do_ar_nao_derruba_o_dashboard(repositorio, historico):
    api = api_com_lookplay(repositorio, historico, lambda: ClienteFalso(erro_sessao=SessaoInvalida("expirou")))
    visao = api.get("/api/filiais").json()
    assert visao["lookplay_consultado"] is False
    assert next(f for f in visao["filiais"] if f["filial"] == "Alfa")["situacao"] == "nao_rodou"


def test_conteudo_criado_no_lookplay_resolve_a_pendencia(repositorio, historico):
    from app.painel import situacao_do_tipo

    ultimo = {"status": "sem_conteudo", "horario": f"{HOJE.isoformat()}T08:00:00-04:00"}
    assert situacao_do_tipo(ultimo, 2, HOJE, True) == "nao_rodou"
    assert situacao_do_tipo(ultimo, 2, HOJE, False) == "sem_conteudo"


def test_visao_informa_a_ultima_atualizacao(api):
    assert api.get("/api/filiais").json()["atualizado_em"].startswith("20")


def test_enviados_mostra_quem_foi_e_a_data_do_aniversario(api):
    assert api.get("/api/filiais/Alfa/enviados").json() == {"vida": None, "casa": None}

    execucao = api.post("/api/execucoes", json={"filial": "Alfa"}).json()
    esperar_fim(api, execucao["id"])

    enviados = api.get("/api/filiais/Alfa/enviados").json()
    assert enviados["vida"]["pessoas"] == [{"nome": "Ana Alves", "dia": "03/11", "secao": "RH"}]
    assert [(p["nome"], p["dia"]) for p in enviados["casa"]["pessoas"]] == [("Bia Lopes", "04/11"), ("Caio Reis", "20/11")]
    assert enviados["vida"]["horario"]
    assert "1990" not in str(enviados)  # o ano não é guardado


def test_enviados_nao_muda_quando_a_consulta_do_oracle_muda(api, repositorio):
    from app.dominio import Tipo

    esperar_fim(api, api.post("/api/execucoes", json={"tipo": "vida", "filial": "Alfa"}).json()["id"])
    repositorio.dados[Tipo.VIDA] = {}
    assert api.get("/api/filiais/Alfa/enviados").json()["vida"]["pessoas"][0]["nome"] == "Ana Alves"


def test_enviados_de_filial_inexistente_responde_404(api):
    assert api.get("/api/filiais/Nada/enviados").status_code == 404


def test_segunda_execucao_recebe_409(api, historico):
    historico.criar_execucao("agendador", "ambos", None)
    assert api.post("/api/execucoes", json={}).status_code == 409
    assert api.get("/api/status").json()["em_andamento"]["origem"] == "agendador"


def test_conflito_diz_qual_filial_esta_rodando(api, historico):
    historico.criar_execucao("dashboard", "vida", "Alfa")
    resposta = api.post("/api/execucoes", json={"filial": "Beta"})
    assert resposta.status_code == 409
    assert "Já está rodando: Alfa (vida)" in resposta.json()["detail"]


def test_conflito_na_rodada_geral_diz_onde_esta(api, historico):
    execucao = historico.criar_execucao("dashboard", "ambos", None)
    historico.registrar_evento(execucao, "Vida: enviando", "Beta")
    detalhe = api.post("/api/execucoes", json={"filial": "Alfa"}).json()["detail"]
    assert "todas as filiais" in detalhe and "agora em Beta" in detalhe


def test_log_da_filial(api):
    assert api.get("/api/filiais/Alfa/log").json() is None
    esperar_fim(api, api.post("/api/execucoes", json={"tipo": "vida", "filial": "Alfa"}).json()["id"])
    log = api.get("/api/filiais/Alfa/log").json()
    assert log["status"] == "concluida"
    assert log["eventos"][-1]["mensagem"] == "Execução concluída."
    assert api.get("/api/filiais/Nada/log").status_code == 404


def test_tipo_invalido_e_recusado(api):
    assert api.post("/api/execucoes", json={"tipo": "outro"}).status_code == 422


def test_banco_fora_do_ar_responde_503_sem_detalhes(repositorio, historico, cliente_falso):
    def quebrar():
        raise OSError("senha=secreta")

    repositorio.filiais = quebrar
    api = montar_cliente(repositorio, historico, cliente_falso)
    resposta = api.get("/api/filiais")
    assert resposta.status_code == 503 and "secreta" not in resposta.text


def test_senha_protege_todas_as_rotas(repositorio, historico, cliente_falso):
    config = Config(dashboard_usuario="gestor", dashboard_senha="s3nha")
    api = montar_cliente(repositorio, historico, cliente_falso, config)
    assert api.get("/").status_code == 401
    assert api.get("/api/filiais").status_code == 401
    assert api.post("/api/execucoes", json={}).status_code == 401
    assert api.get("/api/filiais", auth=("gestor", "errada")).status_code == 401
    assert api.get("/api/filiais", auth=("gestor", "s3nha")).status_code == 200


def test_producao_nao_sobe_sem_senha(repositorio, historico, cliente_falso):
    with pytest.raises(RuntimeError, match="DASHBOARD_SENHA"):
        montar_cliente(repositorio, historico, cliente_falso, Config(modo="producao"))
