"""
Consultas de aniversariantes no Oracle.

Lógica:
1. Uma consulta por tipo traz todas as filiais de uma vez e o Python agrupa
   (2 idas ao banco em vez de uma por filial).
2. Nada específico da empresa fica no código: a view de funcionários, as empresas
   e seções excluídas e as seções renomeadas vêm do .env (ver app/config.py).
   O SQL só filtra o período; as regras da empresa são aplicadas no Python.
3. Cada consulta abre e fecha a sua conexão, o que é seguro para várias threads.

Colunas esperadas na view: nome, dtnascimento, dataadmissao, secao, unidade,
codsituacao ('D' = demitido) e emp_protheus (código da empresa).
"""
from collections import defaultdict

from app.config import Config
from app.dominio import Aniversariante, Tipo

SQL_FILIAIS = """
    SELECT DISTINCT unidade
    FROM {view}
    WHERE nome <> ' '
      AND codsituacao <> 'D'
      AND unidade IS NOT NULL
    ORDER BY unidade
"""

# Datas no formato do CSV. O nome reduzido fica em {nome} e o filtro de período em {periodo}.
SQL_ANIVERSARIANTES = """
    SELECT
        {nome} AS nome,
        TO_CHAR(dtnascimento, 'dd/mm/yyyy') AS nascimento,
        TO_CHAR(dataadmissao, 'dd/mm/yyyy') AS admissao,
        secao,
        unidade,
        TRIM(TO_CHAR(emp_protheus)) AS empresa
    FROM {view}
    WHERE nome <> ' '
      AND codsituacao <> 'D'
      AND unidade IS NOT NULL
      AND {periodo}
    ORDER BY 1
"""

# Data do aniversário trazida para o ano atual (29/02 vira 28/02 em ano comum).
def _no_ano_atual(coluna: str) -> str:
    return (
        f"TO_DATE(DECODE(TO_CHAR({coluna}, 'mmdd'), '0229', '0228' || TO_CHAR(SYSDATE, 'yyyy'), "
        f"TO_CHAR({coluna}, 'mmdd') || TO_CHAR(SYSDATE, 'yyyy')), 'mmddyyyy')"
    )


# Primeiro e último nome. Mantém exatamente o formato do projeto anterior: no tipo
# empresa o primeiro nome vem com o espaço final, o que gera dois espaços entre os nomes.
NOMES = {
    Tipo.VIDA: "SUBSTR(nome, 1, INSTR(nome, ' ') - 1) || ' ' || SUBSTR(nome, INSTR(nome, ' ', -1) + 1)",
    Tipo.CASA: "SUBSTR(nome, 1, INSTR(nome, ' ')) || ' ' || SUBSTR(nome, INSTR(nome, ' ', -1) + 1)",
}

PERIODOS = {
    # Vida: nascimento na semana ISO atual.
    Tipo.VIDA: f"TO_CHAR({_no_ano_atual('dtnascimento')}, 'iw') = TO_CHAR(SYSDATE, 'iw')",
    # Empresa: admissão no mês atual, com pelo menos 1 ano de casa.
    Tipo.CASA: (
        f"TO_CHAR({_no_ano_atual('dataadmissao')}, 'mm') = TO_CHAR(SYSDATE, 'mm') "
        "AND TO_CHAR(SYSDATE, 'yyyy') - TO_CHAR(dataadmissao, 'yyyy') > 0"
    ),
}


def aplicar_regras(linhas, config: Config) -> dict[str, list[Aniversariante]]:
    """Exclusões e renomeações de seção configuradas no .env. Separado para ser testado sem banco."""
    por_filial = defaultdict(list)
    for nome, nascimento, admissao, secao, filial, empresa in linhas:
        empresa = (empresa or "").strip()
        if empresa in config.empresas_excluidas or (empresa, secao) in config.secoes_excluidas:
            continue
        secao = config.secoes_renomeadas.get((empresa, secao), secao)
        por_filial[filial].append(Aniversariante(filial, nome, nascimento, admissao, secao))
    return dict(por_filial)


class RepositorioOracle:
    def __init__(self, config: Config):
        self._config = config

    def _conectar(self):
        # Importado aqui para o modo exemplo e os testes não dependerem do driver.
        import oracledb

        return oracledb.connect(
            user=self._config.oracle_usuario,
            password=self._config.oracle_senha,
            dsn=self._config.oracle_dsn,
        )

    def filiais(self) -> list[str]:
        with self._conectar() as conexao, conexao.cursor() as cursor:
            cursor.execute(SQL_FILIAIS.format(view=self._config.oracle_view))
            return [linha[0] for linha in cursor.fetchall()]

    def aniversariantes(self, tipo: Tipo) -> dict[str, list[Aniversariante]]:
        """Retorna {filial: [pessoas]}. Filial sem pessoas não aparece no dicionário."""
        sql = SQL_ANIVERSARIANTES.format(view=self._config.oracle_view, nome=NOMES[tipo], periodo=PERIODOS[tipo])
        with self._conectar() as conexao, conexao.cursor() as cursor:
            cursor.execute(sql)
            return aplicar_regras(cursor.fetchall(), self._config)
