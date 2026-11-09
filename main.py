"""
Ponto de entrada do envio para o Airflow (ou qualquer agendador).

Lógica: consulta os aniversariantes de vida e de empresa no Oracle, gera o CSV de cada
filial e envia ao Lookplay por requisições HTTP (sem navegador). Cada execução fica
registrada nas tabelas RPA.LOOKPLAY_ANIVERSARIANTES*, e aparece no dashboard.

Uso:
    python main.py                               todas as filiais, vida e empresa
    python main.py --tipo vida                   só vida (casa = empresa)
    python main.py --filial "Matriz"             uma filial

Código de saída: 0 quando tudo foi enviado; 1 quando houve erro, filial fora do Lookplay
ou já existia outra execução em andamento. O Airflow marca a tarefa como falha no 1.
A configuração vem do .env ou das variáveis de ambiente (MODO=producao para enviar de verdade).
"""
import sys

from app.executar import principal

if __name__ == "__main__":
    sys.exit(principal())
