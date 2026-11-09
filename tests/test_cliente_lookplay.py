"""Testa o cliente com respostas HTTP simuladas, no formato que o Selenium já usava."""
import pytest
import responses

from app.cliente_lookplay import ClienteLookplay, ConteudoNaoEncontrado, ErroLookplay, SessaoInvalida, campos_do_formulario
from app.config import Config
from app.dominio import Tipo

BASE = "https://exemplo.test/sistema"
LISTA = f"{BASE}/modules/conteudo/_list.php?filtro=ANIVERSARIANTES_3"
EDICAO = f"{BASE}/modules/conteudo/_edit.php"
LOGIN = f"{BASE}/index.php"

TELA_LOGIN = """<form method="post" action="index.php">
  <input type="hidden" name="token" value="t1"><input id="username" name="user_campo">
  <input id="password" type="password" name="senha_campo"><button name="login" value="1">Entrar</button></form>"""
LISTAGEM = """<table id="table-1"><thead><tr><th>#</th><th>Nome</th></tr></thead><tbody>
  <tr><td>1</td><td> Alfa </td><td><form action="_edit.php" method="post">
    <input type="hidden" name="id" value="77"><button>Editar</button></form></td></tr>
  <tr><td>2</td><td>Alfa Sul</td><td><form action="_edit.php" method="post">
    <input type="hidden" name="id" value="88"><button>Editar</button></form></td></tr></tbody></table>"""


def tela_edicao(arquivo="antigo.csv", tipo="ANIVERSARIANTES_3", campos_extras=""):
    """Reproduz a tela real: "arquivo" e "texto" só existem dentro do modelo JavaScript."""
    return f"""<form action="_edit.php" method="post" enctype="multipart/form-data">
  <input type="hidden" name="id" value="77">
  <select name="tipo"><option value="OUTRO">Outro</option><option value="{tipo}" selected>Aniversariantes</option></select>
  <input name="titulo" value="Alfa">
  <select name="regiao[]" multiple><option value="1">A</option><option value="3" selected>Cuiabá</option></select>
  <select name="dia_semana[]" multiple><option value="0">Domingo</option></select>
  <label><input type="radio" name="status" value="ATIVO"></label>
  <label><input type="radio" name="status" value="INATIVO" checked></label>
  <button name="_edit" type="submit">Atualizar</button></form>
  <script type="text/x-jquery-tmpl" id="OUTRO-template"><input name="outro"></script>
  <script type="text/x-jquery-tmpl" id="{tipo}-template">{{{{if (arquivo)}}}}
    <a href="http://x/storage/conteudo/{arquivo}">{arquivo}</a>{{{{/if}}}}
    <input type="file" name="arquivo" accept=".csv">{campos_extras}
    <select name="texto">{{{{each {{'SEMANA':'da Semana'}} }}}}
    {{{{if ["SEMANA"].includes($index)}}}}<option selected>x</option>{{{{/if}}}}{{{{/each}}}}</select></script>"""


def cliente(**extras):
    padrao = {"lookplay_url": BASE, "lookplay_usuario": "robo.tv", "lookplay_cookies": "PHPSESSID=abc"}
    return ClienteLookplay(Config(**{**padrao, **extras}))


def corpo_texto(chamada):
    corpo = chamada.request.body
    return corpo.decode("utf-8", "replace") if isinstance(corpo, bytes) else corpo


@responses.activate
def test_envio_usa_o_cookie_mantem_os_campos_e_anexa_o_csv():
    responses.get(LISTA, body=LISTAGEM)
    responses.post(EDICAO, body=tela_edicao("antigo.csv"))
    responses.post(EDICAO, body="<div class='alert-success'>ok</div>")
    responses.post(EDICAO, body=tela_edicao("novo.csv"))

    c = cliente()
    c.abrir_sessao()
    c.enviar_csv(Tipo.VIDA, "Alfa", "aniversariantes_Alfa.csv", b"nome;x\r\n")

    assert "PHPSESSID=abc" in responses.calls[0].request.headers["Cookie"]
    assert corpo_texto(responses.calls[2]) == "id=77"  # abriu a linha exata, não "Alfa Sul"
    multipart = corpo_texto(responses.calls[3])
    for trecho in ('name="titulo"', "Alfa", 'name="tipo"', 'name="regiao[]"', "\r\n\r\n3\r\n", 'name="status"',
                   "\r\n\r\nINATIVO\r\n", 'name="texto"', "\r\n\r\nSEMANA\r\n", 'name="_edit"',
                   'filename="aniversariantes_Alfa.csv"', "nome;x"):
        assert trecho in multipart
    assert "\r\n\r\nATIVO\r\n" not in multipart  # opção não marcada não é enviada
    assert 'name="outro"' not in multipart  # campo do modelo de outro tipo


@responses.activate
def test_filtro_pode_ser_trocado_para_testar_sem_afetar_a_tv():
    url = f"{BASE}/modules/conteudo/_list.php?filtro=ANIVERSARIANTES_2"
    responses.get(url, body=LISTAGEM)
    responses.post(EDICAO, body=tela_edicao("a.csv", "ANIVERSARIANTES_2"))
    responses.post(EDICAO, body="ok")
    responses.post(EDICAO, body=tela_edicao("b.csv", "ANIVERSARIANTES_2"))
    cliente(filtro_vida="ANIVERSARIANTES_2").enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")
    assert responses.calls[0].request.url == url


@responses.activate
def test_arquivo_que_nao_mudou_nao_conta_como_envio():
    responses.get(LISTA, body=LISTAGEM)
    responses.post(EDICAO, body=tela_edicao("igual.csv"))
    responses.post(EDICAO, body="ok")
    responses.post(EDICAO, body=tela_edicao("igual.csv"))
    with pytest.raises(ErroLookplay, match="não mudou"):
        cliente().enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")


@responses.activate
def test_campo_novo_no_modelo_cancela_o_envio_para_nao_apagar_dados():
    responses.get(LISTA, body=LISTAGEM)
    responses.post(EDICAO, body=tela_edicao(campos_extras='<input name="novo_campo">'))
    with pytest.raises(ErroLookplay, match="novo_campo"):
        cliente().enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")
    assert len(responses.calls) == 2  # não chegou a enviar


@responses.activate
def test_titulo_repetido_cancela_o_envio():
    linha = LISTAGEM.split("<tbody>")[1].split("</tr>")[0] + "</tr>"
    responses.get(LISTA, body=LISTAGEM.replace("</tbody>", linha + "</tbody>"))
    with pytest.raises(ErroLookplay, match="mais de um"):
        cliente().enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")


@responses.activate
def test_cookie_expirado_sem_senha_informa_o_que_fazer():
    responses.get(LISTA, body=TELA_LOGIN)
    with pytest.raises(SessaoInvalida, match="LOOKPLAY_COOKIES"):
        cliente().abrir_sessao()


@responses.activate
def test_cookie_expirado_com_senha_faz_login_uma_vez():
    responses.get(LISTA, body=TELA_LOGIN)
    responses.get(LOGIN, body=TELA_LOGIN)
    responses.post(LOGIN, body="<h1>Painel</h1>")

    cliente(lookplay_senha="segredo").abrir_sessao()

    corpo = corpo_texto(responses.calls[-1])
    assert "user_campo=robo.tv" in corpo and "senha_campo=segredo" in corpo and "token=t1" in corpo and "login=1" in corpo


@responses.activate
def test_login_recusado_nao_repete_a_tentativa():
    responses.get(LOGIN, body=TELA_LOGIN)
    responses.post(LOGIN, body=TELA_LOGIN)
    with pytest.raises(SessaoInvalida, match="recusou"):
        cliente(lookplay_cookies="", lookplay_senha="errada").abrir_sessao()
    assert len([c for c in responses.calls if c.request.method == "POST"]) == 1


@responses.activate
def test_filial_fora_da_listagem_e_sem_conteudo():
    responses.get(LISTA, body=LISTAGEM)
    c = cliente()
    with pytest.raises(ConteudoNaoEncontrado):
        c.enviar_csv(Tipo.VIDA, "Beta", "a.csv", b"x")


@responses.activate
def test_listagem_vazia_nao_vira_falso_sem_conteudo():
    responses.get(LISTA, body="<table id='table-1'><tbody></tbody></table>")
    with pytest.raises(ErroLookplay, match="HTML pode ter mudado"):
        cliente().enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")


@responses.activate
def test_resposta_200_com_tela_de_login_nao_conta_como_sucesso():
    responses.get(LISTA, body=LISTAGEM)
    responses.post(EDICAO, body=tela_edicao())
    responses.post(EDICAO, body=TELA_LOGIN)
    with pytest.raises(SessaoInvalida):
        cliente().enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")


@responses.activate
def test_alerta_de_erro_do_lookplay_vira_erro_da_filial():
    responses.get(LISTA, body=LISTAGEM)
    responses.post(EDICAO, body=tela_edicao())
    responses.post(EDICAO, body="<div class='alert alert-danger'>Arquivo inválido</div>")
    with pytest.raises(ErroLookplay, match="Arquivo inválido"):
        cliente().enviar_csv(Tipo.VIDA, "Alfa", "a.csv", b"x")


def test_campos_do_formulario_ignora_botao_e_o_modelo():
    from bs4 import BeautifulSoup

    form = BeautifulSoup(tela_edicao(), "html.parser").form
    assert campos_do_formulario(form) == [
        ("id", "77"), ("tipo", "ANIVERSARIANTES_3"), ("titulo", "Alfa"), ("regiao[]", "3"), ("status", "INATIVO"),
    ]
