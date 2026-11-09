from datetime import datetime

import pytest

from app.cliente_lookplay import ConteudoNaoEncontrado, ErroLookplay, SessaoInvalida
from app.dominio import Tipo
from app.historico import ExecucaoEmAndamento
from app.orquestrador import Orquestrador

from tests.conftest import ClienteFalso, alterar


def rodar(repositorio, historico, cliente, **argumentos):
    orquestrador = Orquestrador(repositorio, lambda: cliente, historico, pausa=0)
    return historico.obter(orquestrador.executar_agora(**argumentos))


def status_por_filial(execucao):
    return {(i["filial"], i["tipo"]): i["status"] for i in execucao["itens"]}


def test_so_envia_para_quem_tem_aniversariantes(repositorio, historico):
    cliente = ClienteFalso()
    execucao = rodar(repositorio, historico, cliente)

    assert execucao["status"] == "concluida"
    assert status_por_filial(execucao) == {
        ("Alfa", "casa"): "enviado", ("Alfa", "vida"): "enviado",
        ("Beta", "casa"): "sem_aniversariantes", ("Beta", "vida"): "sem_aniversariantes",
    }
    assert [(e[0], e[1]) for e in cliente.envios] == [(Tipo.CASA, "Alfa"), (Tipo.VIDA, "Alfa")]


def test_sem_aniversariantes_nao_abre_o_lookplay(repositorio, historico):
    repositorio.dados = {Tipo.VIDA: {}, Tipo.CASA: {}}
    execucao = rodar(repositorio, historico, ClienteFalso(erro_sessao=SessaoInvalida("não deveria abrir")))
    assert execucao["status"] == "concluida"


def test_rodar_uma_filial_e_um_tipo(repositorio, historico):
    cliente = ClienteFalso()
    execucao = rodar(repositorio, historico, cliente, escolha="vida", filial="Alfa")
    assert status_por_filial(execucao) == {("Alfa", "vida"): "enviado"}
    assert len(cliente.envios) == 1


def test_erro_em_uma_filial_nao_interrompe_as_outras(repositorio, historico):
    repositorio.dados[Tipo.VIDA]["Beta"] = repositorio.dados[Tipo.VIDA]["Alfa"]
    cliente = ClienteFalso(erro_por_filial={"Alfa": ErroLookplay("falhou")})
    execucao = rodar(repositorio, historico, cliente, escolha="vida")
    assert execucao["status"] == "concluida_com_erros"
    assert status_por_filial(execucao) == {("Alfa", "vida"): "erro", ("Beta", "vida"): "enviado"}


def test_filial_sem_conteudo_tem_situacao_propria(repositorio, historico):
    cliente = ClienteFalso(erro_por_filial={"Alfa": ConteudoNaoEncontrado("sem cadastro")})
    execucao = rodar(repositorio, historico, cliente, escolha="vida", filial="Alfa")
    assert status_por_filial(execucao) == {("Alfa", "vida"): "sem_conteudo"}


def test_sessao_invalida_para_de_tentar_e_explica(repositorio, historico):
    cliente = ClienteFalso(erro_sessao=SessaoInvalida("Atualize o cookie"))
    execucao = rodar(repositorio, historico, cliente)
    assert execucao["status"] == "concluida_com_erros"
    assert execucao["mensagem"] == "Atualize o cookie"
    assert cliente.envios == []


def test_oracle_fora_do_ar_marca_falha_sem_expor_detalhes(repositorio, historico):
    def quebrar(_tipo):
        raise OSError("senha=secreta dsn=interno")

    repositorio.aniversariantes = quebrar
    execucao = rodar(repositorio, historico, ClienteFalso())
    assert execucao["status"] == "falhou"
    assert "secreta" not in execucao["mensagem"]


def test_nao_permite_duas_execucoes_ao_mesmo_tempo(historico):
    historico.criar_execucao("dashboard", "ambos", None)
    with pytest.raises(ExecucaoEmAndamento):
        historico.criar_execucao("agendador", "ambos", None)


def test_log_mostra_os_passos_da_filial_em_ordem(repositorio, historico):
    rodar(repositorio, historico, ClienteFalso(), escolha="vida", filial="Alfa")
    mensagens = [e["mensagem"] for e in historico.log_da_filial("Alfa")["eventos"]]
    assert mensagens == [
        "Consultando os aniversariantes no banco de dados.",
        "Abrindo a sessão no Lookplay.",
        "Vida: enviando o CSV com 1 pessoa(s).",
        "Vida: arquivo enviado e confirmado no Lookplay.",
        "Execução concluída.",
    ]
    assert "ANA" not in str(mensagens)  # o log não leva nomes de pessoas


def test_log_de_uma_filial_nao_traz_passos_de_outra(repositorio, historico):
    rodar(repositorio, historico, ClienteFalso())
    mensagens = str(historico.log_da_filial("Beta")["eventos"])
    assert "sem aniversariantes" in mensagens and "enviando" not in mensagens


def test_log_registra_o_erro_da_filial(repositorio, historico):
    rodar(repositorio, historico, ClienteFalso(erro_por_filial={"Alfa": ErroLookplay("falhou")}), escolha="vida", filial="Alfa")
    assert "Vida: erro. falhou" in str(historico.log_da_filial("Alfa")["eventos"])


def test_status_informa_a_filial_atual_da_rodada_geral(historico):
    execucao = historico.criar_execucao("dashboard", "ambos", None)
    assert historico.em_andamento()["filial_atual"] is None
    historico.registrar_evento(execucao, "Vida: enviando", "Alfa")
    assert historico.em_andamento()["filial_atual"] == "Alfa"


def test_ctrl_c_encerra_a_execucao_e_libera_a_proxima(repositorio, historico):
    def interromper(_tipo):
        raise KeyboardInterrupt

    repositorio.aniversariantes = interromper
    orquestrador = Orquestrador(repositorio, ClienteFalso, historico, pausa=0)
    with pytest.raises(KeyboardInterrupt):
        orquestrador.executar_agora()
    assert historico.listar(1)[0]["status"] == "interrompida"
    assert historico.em_andamento() is None


def test_execucao_parada_some_do_status_sem_precisar_de_nova_execucao(historico):
    parada = historico.criar_execucao("dashboard", "ambos", None)
    alterar(historico, "UPDATE {execucoes} SET ultima_atividade = :antigo WHERE id = :id", antigo=datetime(2000, 1, 1), id=parada)
    assert historico.em_andamento() is None
    assert historico.obter(parada)["status"] == "interrompida"


def test_execucao_travada_nao_bloqueia_para_sempre(historico):
    primeira = historico.criar_execucao("dashboard", "ambos", None)
    alterar(historico, "UPDATE {execucoes} SET ultima_atividade = :antigo WHERE id = :id", antigo=datetime(2000, 1, 1), id=primeira)
    segunda = historico.criar_execucao("dashboard", "ambos", None)
    assert segunda != primeira
    assert historico.obter(primeira)["status"] == "interrompida"
