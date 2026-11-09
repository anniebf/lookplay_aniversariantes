"""
Confere a conexão com o Lookplay real SEM enviar nenhum arquivo:

    python -m app.diagnostico

Valida a sessão, lê as listagens e abre uma tela de edição de cada tipo. Mostra só
os NOMES dos campos do formulário, nunca os valores. Rode antes do primeiro envio.
"""
import json

from app.cliente_lookplay import ClienteLookplay
from app.config import carregar_config
from app.dominio import Tipo


def principal() -> None:
    cliente = ClienteLookplay(carregar_config())
    cliente.abrir_sessao()
    print("Sessão válida.")
    for tipo in Tipo:
        print(json.dumps(cliente.inspecionar(tipo), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    principal()
