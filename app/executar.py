"""
Execução pela linha de comando, para o Agendador de Tarefas (substitui o main.py antigo).

    python -m app.executar                       todas as filiais, vida e casa
    python -m app.executar --tipo vida
    python -m app.executar --filial "Matriz"

Retorna código 1 quando há erro, para o Agendador registrar a falha.
"""
import argparse
import sys

from app.config import carregar_config
from app.historico import ExecucaoEmAndamento
from app.montagem import configurar_log, montar


def principal() -> int:
    parser = argparse.ArgumentParser(description="Envia os CSVs de aniversariantes ao Lookplay.")
    parser.add_argument("--tipo", choices=["vida", "casa", "ambos"], default="ambos")
    parser.add_argument("--filial")
    argumentos = parser.parse_args()

    config = carregar_config()
    configurar_log(config.log_em_arquivo)
    _, orquestrador, historico = montar(config)
    try:
        execucao_id = orquestrador.executar_agora(argumentos.tipo, argumentos.filial)
    except ExecucaoEmAndamento as erro:
        print(erro)
        return 1
    except KeyboardInterrupt:
        print("Execução interrompida.")
        return 1

    execucao = historico.obter(execucao_id)
    print(f"Execução {execucao_id}: {execucao['status']} {execucao['contagem']}")
    return 0 if execucao["status"] == "concluida" else 1


if __name__ == "__main__":
    sys.exit(principal())
