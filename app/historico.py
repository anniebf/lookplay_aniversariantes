"""
Histórico das execuções do RPA.

Em produção fica no Oracle, no schema configurado (padrão RPA), em quatro tabelas
com o mesmo prefixo (padrão LOOKPLAY_ANIVERSARIANTES):

    LOOKPLAY_ANIVERSARIANTES           uma linha por execução: início, fim, status
    LOOKPLAY_ANIVERSARIANTES_ITENS     resultado de cada filial e tipo dentro da execução
    LOOKPLAY_ANIVERSARIANTES_PESSOAS   quem foi enviado em cada item (nome, dia/mês, seção)
    LOOKPLAY_ANIVERSARIANTES_EVENTOS   linhas do log mostradas abaixo da filial no dashboard

No modo exemplo e nos testes, a mesma estrutura fica num arquivo SQLite.

O SQL é escrito uma vez só, com parâmetros nomeados (:nome), que os dois bancos aceitam.
Cada "dialeto" resolve apenas o que muda entre eles: conexão, id gerado, limite de linhas
e criação das tabelas. As tabelas são criadas automaticamente na primeira subida.

Trava: um índice único aceita só uma execução 'em_andamento' por vez. Como a trava fica
no banco, ela vale entre processos e servidores (API, agendador, outro container).

Datas: gravadas na hora local do servidor, sem fuso. Defina a variável TZ no servidor.
O ano de nascimento das pessoas não é guardado.
"""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

# Uma execução parada há mais que isso é considerada interrompida (queda do processo).
LIMITE_SEM_ATIVIDADE = timedelta(minutes=10)
COLUNAS_DE_DATA = {"inicio", "fim", "ultima_atividade", "horario"}
TABELA_PADRAO = "LOOKPLAY_ANIVERSARIANTES"
SUFIXOS = {"execucoes": "", "itens": "_ITENS", "pessoas": "_PESSOAS", "eventos": "_EVENTOS"}
# Nomes de schema e tabela entram no SQL como texto: só letras, números e "_", para impedir injeção.
IDENTIFICADOR = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


class ExecucaoEmAndamento(Exception):
    """Já existe uma execução rodando."""


def validar_identificador(nome: str) -> str:
    if not IDENTIFICADOR.match(nome):
        raise ValueError(f"Nome inválido para tabela ou schema: {nome!r}.")
    return nome


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def _cortar(texto: str | None, tamanho: int) -> str | None:
    """Respeita o tamanho das colunas VARCHAR2 do Oracle."""
    return texto[:tamanho] if texto else texto


def _para_api(nomes: list[str], valores) -> dict:
    """Linha do banco -> dicionário da API: datas em ISO com fuso e números inteiros."""
    linha = {}
    for nome, valor in zip(nomes, valores):
        if nome in COLUNAS_DE_DATA and valor is not None:
            if isinstance(valor, str):  # o SQLite devolve texto
                valor = datetime.fromisoformat(valor)
            valor = valor.astimezone().isoformat(timespec="seconds")  # sem fuso = hora local do servidor
        elif isinstance(valor, float) and valor.is_integer():
            valor = int(valor)
        linha[nome] = valor
    return linha


class _Sessao:
    """Envolve o cursor de qualquer banco com uma interface única e devolve dicionários."""

    def __init__(self, cursor, dialeto):
        self._cursor = cursor
        self._dialeto = dialeto

    def executar(self, sql: str, **parametros) -> "_Sessao":
        self._cursor.execute(sql, self._dialeto.parametros(parametros))
        return self

    def varios(self, sql: str, lista: list[dict]) -> None:
        if lista:
            self._cursor.executemany(sql, [self._dialeto.parametros(p) for p in lista])

    def inserir(self, sql: str, **parametros) -> int:
        """Executa um INSERT e devolve o id gerado."""
        return self._dialeto.inserir(self._cursor, sql, self._dialeto.parametros(parametros))

    def _nomes(self) -> list[str]:
        return [coluna[0].lower() for coluna in self._cursor.description]

    def linha(self) -> dict | None:
        valores = self._cursor.fetchone()
        return _para_api(self._nomes(), valores) if valores else None

    def linhas(self) -> list[dict]:
        nomes = self._nomes()
        return [_para_api(nomes, valores) for valores in self._cursor.fetchall()]


# ---------------------------------------------------------------- SQLite (exemplo e testes)

DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS {execucoes} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, origem TEXT NOT NULL, tipo TEXT NOT NULL, filial TEXT,
    status TEXT NOT NULL, mensagem TEXT, inicio TEXT NOT NULL, fim TEXT, ultima_atividade TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS {execucoes}_uq_andamento ON {execucoes}(status) WHERE status = 'em_andamento';
CREATE TABLE IF NOT EXISTS {itens} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, execucao_id INTEGER NOT NULL REFERENCES {execucoes}(id),
    filial TEXT NOT NULL, tipo TEXT NOT NULL, status TEXT NOT NULL, quantidade INTEGER NOT NULL,
    mensagem TEXT, horario TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS {itens}_filial ON {itens}(filial, tipo, id);
CREATE TABLE IF NOT EXISTS {pessoas} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL REFERENCES {itens}(id),
    nome TEXT NOT NULL, dia TEXT NOT NULL, secao TEXT
);
CREATE INDEX IF NOT EXISTS {pessoas}_item ON {pessoas}(item_id);
CREATE TABLE IF NOT EXISTS {eventos} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, execucao_id INTEGER NOT NULL REFERENCES {execucoes}(id),
    filial TEXT, mensagem TEXT NOT NULL, horario TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS {eventos}_execucao ON {eventos}(execucao_id, filial);
"""


class SQLite:
    ErroIntegridade = sqlite3.IntegrityError

    def __init__(self, caminho: Path):
        self._caminho = Path(caminho)
        self._caminho.parent.mkdir(parents=True, exist_ok=True)

    def nomes(self, tabela: str) -> dict[str, str]:
        return {chave: (tabela + sufixo).lower() for chave, sufixo in SUFIXOS.items()}

    @contextmanager
    def sessao(self):
        # Conexão curta por operação: simples e segura entre threads.
        conexao = sqlite3.connect(self._caminho, timeout=10)
        try:
            with conexao:  # commit no fim, rollback em erro
                yield conexao.cursor()
        finally:
            conexao.close()

    @staticmethod
    def parametros(parametros: dict) -> dict:
        # O SQLite não tem tipo data: grava texto ISO, que ordena e compara certo.
        return {k: v.isoformat(timespec="seconds") if isinstance(v, datetime) else v for k, v in parametros.items()}

    @staticmethod
    def primeiros(quantidade: int) -> str:
        return f"LIMIT {int(quantidade)}"

    @staticmethod
    def inserir(cursor, sql: str, parametros: dict) -> int:
        cursor.execute(sql, parametros)
        return cursor.lastrowid

    def criar_estrutura(self, tabela: str) -> None:
        with self.sessao() as cursor:
            cursor.connection.executescript(DDL_SQLITE.format(**self.nomes(tabela)))


# ---------------------------------------------------------------- Oracle (produção)

# Uma lista de comandos por tabela: só as tabelas que ainda não existem são criadas.
DDL_ORACLE = {
    "execucoes": [
        """CREATE TABLE {execucoes} (
            id NUMBER(12) GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            origem VARCHAR2(30 CHAR) NOT NULL,
            tipo VARCHAR2(10 CHAR) NOT NULL,
            filial VARCHAR2(200 CHAR),
            status VARCHAR2(30 CHAR) NOT NULL,
            mensagem VARCHAR2(1000 CHAR),
            inicio TIMESTAMP(0) NOT NULL,
            fim TIMESTAMP(0),
            ultima_atividade TIMESTAMP(0) NOT NULL)""",
        # Índice único só sobre as linhas 'em_andamento': as demais ficam NULL e não contam.
        "CREATE UNIQUE INDEX {tabela}_UQ_ANDAMENTO ON {execucoes} (CASE WHEN status = 'em_andamento' THEN 1 END)",
        "COMMENT ON TABLE {execucoes} IS 'RPA Lookplay Aniversariantes: uma linha por execução (dashboard ou agendador)'",
    ],
    "itens": [
        """CREATE TABLE {itens} (
            id NUMBER(12) GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            execucao_id NUMBER(12) NOT NULL REFERENCES {execucoes}(id),
            filial VARCHAR2(200 CHAR) NOT NULL,
            tipo VARCHAR2(10 CHAR) NOT NULL,
            status VARCHAR2(30 CHAR) NOT NULL,
            quantidade NUMBER(6) NOT NULL,
            mensagem VARCHAR2(1000 CHAR),
            horario TIMESTAMP(0) NOT NULL)""",
        "CREATE INDEX {tabela}_IT_FILIAL ON {itens} (filial, tipo, id)",
        "CREATE INDEX {tabela}_IT_EXECUCAO ON {itens} (execucao_id)",
        "COMMENT ON TABLE {itens} IS 'RPA Lookplay Aniversariantes: resultado de cada filial e tipo (vida/casa) na execução'",
    ],
    "pessoas": [
        """CREATE TABLE {pessoas} (
            id NUMBER(12) GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            item_id NUMBER(12) NOT NULL REFERENCES {itens}(id),
            nome VARCHAR2(200 CHAR) NOT NULL,
            dia VARCHAR2(5 CHAR) NOT NULL,
            secao VARCHAR2(200 CHAR))""",
        "CREATE INDEX {tabela}_PE_ITEM ON {pessoas} (item_id)",
        "COMMENT ON TABLE {pessoas} IS 'RPA Lookplay Aniversariantes: pessoas enviadas em cada item (sem ano de nascimento)'",
    ],
    "eventos": [
        """CREATE TABLE {eventos} (
            id NUMBER(12) GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            execucao_id NUMBER(12) NOT NULL REFERENCES {execucoes}(id),
            filial VARCHAR2(200 CHAR),
            mensagem VARCHAR2(1000 CHAR) NOT NULL,
            horario TIMESTAMP(0) NOT NULL)""",
        "CREATE INDEX {tabela}_EV_EXECUCAO ON {eventos} (execucao_id, filial)",
        "COMMENT ON TABLE {eventos} IS 'RPA Lookplay Aniversariantes: log da execução exibido no dashboard'",
    ],
}


class Oracle:
    def __init__(self, usuario: str, senha: str, dsn: str, schema: str = "RPA"):
        # Importado aqui para o modo exemplo e os testes não dependerem do driver.
        import oracledb

        self.ErroIntegridade = oracledb.IntegrityError
        self._schema = validar_identificador(schema).upper()
        # Pool: o dashboard consulta a cada poucos segundos; reaproveitar conexões evita
        # abrir uma nova a cada consulta. Modo thin: não precisa do Oracle Client instalado.
        self._pool = oracledb.create_pool(
            user=usuario, password=senha, dsn=dsn, min=1, max=5, increment=1,
            getmode=oracledb.POOL_GETMODE_TIMEDWAIT, wait_timeout=15000,
        )

    def nomes(self, tabela: str) -> dict[str, str]:
        return {chave: f"{self._schema}.{tabela.upper()}{sufixo}" for chave, sufixo in SUFIXOS.items()}

    @contextmanager
    def sessao(self):
        with self._pool.acquire() as conexao:
            cursor = conexao.cursor()
            try:
                yield cursor
                conexao.commit()
            except BaseException:
                conexao.rollback()
                raise

    @staticmethod
    def parametros(parametros: dict) -> dict:
        return parametros

    @staticmethod
    def primeiros(quantidade: int) -> str:
        return f"FETCH FIRST {int(quantidade)} ROWS ONLY"

    @staticmethod
    def inserir(cursor, sql: str, parametros: dict) -> int:
        novo_id = cursor.var(int)
        cursor.execute(f"{sql} RETURNING id INTO :novo_id", {**parametros, "novo_id": novo_id})
        return novo_id.getvalue()[0]

    def criar_estrutura(self, tabela: str) -> None:
        nomes = {**self.nomes(tabela), "tabela": tabela.upper()}
        with self.sessao() as cursor:
            # Filtra pelos 4 nomes: a lista completa de all_tables leva vários segundos.
            procuradas = {f"t{i}": nomes[chave].split(".")[1] for i, chave in enumerate(SUFIXOS)}
            filtro = ", ".join(f":{chave}" for chave in procuradas)
            cursor.execute(
                f"SELECT table_name FROM all_tables WHERE owner = :dono AND table_name IN ({filtro})",
                dono=self._schema, **procuradas,
            )
            existentes = {linha[0] for linha in cursor}
            for chave, comandos in DDL_ORACLE.items():  # ordem importa: as chaves estrangeiras
                if nomes[chave].split(".")[1] in existentes:
                    continue
                for comando in comandos:
                    cursor.execute(comando.format(**nomes))


# ---------------------------------------------------------------- Histórico


class Historico:
    def __init__(self, dialeto, tabela: str = TABELA_PADRAO):
        self._dialeto = dialeto
        self._tabelas = dialeto.nomes(validar_identificador(tabela))
        dialeto.criar_estrutura(tabela)

    @contextmanager
    def _sessao(self):
        with self._dialeto.sessao() as cursor:
            yield _Sessao(cursor, self._dialeto)

    def _sql(self, texto: str, limite: int | None = None) -> str:
        sql = texto.format(**self._tabelas)
        return f"{sql} {self._dialeto.primeiros(limite)}" if limite else sql

    def _encerrar_paradas(self, s: _Sessao) -> None:
        """Marca como interrompida a execução sem atividade há muito tempo (queda do processo)."""
        s.executar(
            self._sql(
                "UPDATE {execucoes} SET status = 'interrompida', fim = :agora, "
                "mensagem = 'A execução parou sem terminar.' "
                "WHERE status = 'em_andamento' AND ultima_atividade < :limite"
            ),
            agora=_agora(),
            limite=_agora() - LIMITE_SEM_ATIVIDADE,
        )

    def _tocar(self, s: _Sessao, execucao_id: int, agora: datetime) -> None:
        """Atualiza a última atividade: prova de que a execução segue viva."""
        s.executar(self._sql("UPDATE {execucoes} SET ultima_atividade = :agora WHERE id = :id"), agora=agora, id=execucao_id)

    def criar_execucao(self, origem: str, tipo: str, filial: str | None) -> int:
        agora = _agora()
        try:
            with self._sessao() as s:
                self._encerrar_paradas(s)
                return s.inserir(
                    self._sql(
                        "INSERT INTO {execucoes} (origem, tipo, filial, status, inicio, ultima_atividade) "
                        "VALUES (:origem, :tipo, :filial, 'em_andamento', :agora, :agora)"
                    ),
                    origem=origem, tipo=tipo, filial=filial, agora=agora,
                )
        except self._dialeto.ErroIntegridade as erro:
            raise ExecucaoEmAndamento("Já existe uma execução em andamento.") from erro

    def registrar_item(
        self,
        execucao_id: int,
        filial: str,
        tipo: str,
        status: str,
        quantidade: int,
        mensagem: str | None = None,
        pessoas: list[dict] | None = None,
    ) -> None:
        agora = _agora()
        with self._sessao() as s:
            item_id = s.inserir(
                self._sql(
                    "INSERT INTO {itens} (execucao_id, filial, tipo, status, quantidade, mensagem, horario) "
                    "VALUES (:execucao_id, :filial, :tipo, :status, :quantidade, :mensagem, :agora)"
                ),
                execucao_id=execucao_id, filial=filial, tipo=tipo, status=status,
                quantidade=quantidade, mensagem=_cortar(mensagem, 1000), agora=agora,
            )
            s.varios(
                self._sql("INSERT INTO {pessoas} (item_id, nome, dia, secao) VALUES (:item_id, :nome, :dia, :secao)"),
                [
                    {"item_id": item_id, "nome": _cortar(p["nome"], 200), "dia": p["dia"], "secao": _cortar(p["secao"], 200)}
                    for p in pessoas or []
                ],
            )
            self._tocar(s, execucao_id, agora)

    def registrar_evento(self, execucao_id: int, mensagem: str, filial: str | None = None) -> None:
        """Uma linha do log exibido no dashboard. filial=None vale para a execução inteira."""
        agora = _agora()
        with self._sessao() as s:
            s.executar(
                self._sql("INSERT INTO {eventos} (execucao_id, filial, mensagem, horario) VALUES (:execucao_id, :filial, :mensagem, :agora)"),
                execucao_id=execucao_id, filial=filial, mensagem=_cortar(mensagem, 1000), agora=agora,
            )
            self._tocar(s, execucao_id, agora)

    def log_da_filial(self, filial: str) -> dict | None:
        """Log da execução em andamento que inclui a filial ou, se não houver, da última que a envolveu."""
        with self._sessao() as s:
            self._encerrar_paradas(s)
            execucao = s.executar(
                self._sql("SELECT * FROM {execucoes} WHERE status = 'em_andamento' AND (filial IS NULL OR filial = :filial)"),
                filial=filial,
            ).linha() or s.executar(
                self._sql(
                    "SELECT e.* FROM {execucoes} e JOIN {eventos} v ON v.execucao_id = e.id "
                    "WHERE v.filial = :filial ORDER BY e.id DESC",
                    limite=1,
                ),
                filial=filial,
            ).linha()
            if execucao is None:
                return None
            eventos = s.executar(
                self._sql(
                    "SELECT horario, filial, mensagem FROM {eventos} "
                    "WHERE execucao_id = :id AND (filial = :filial OR filial IS NULL) ORDER BY id"
                ),
                id=execucao["id"], filial=filial,
            ).linhas()
        return {**execucao, "eventos": eventos}

    def houve_execucao(self, origem: str, desde: datetime) -> bool:
        """Já existe execução dessa origem iniciada a partir do horário? Usado pelo agendamento."""
        with self._sessao() as s:
            linha = s.executar(
                self._sql("SELECT COUNT(*) AS total FROM {execucoes} WHERE origem = :origem AND inicio >= :desde"),
                origem=origem, desde=desde,
            ).linha()
        return linha["total"] > 0

    def finalizar(self, execucao_id: int, status: str, mensagem: str | None = None) -> None:
        with self._sessao() as s:
            s.executar(
                self._sql("UPDATE {execucoes} SET status = :status, mensagem = :mensagem, fim = :agora WHERE id = :id"),
                status=status, mensagem=_cortar(mensagem, 1000), agora=_agora(), id=execucao_id,
            )

    def _resumo(self, s: _Sessao, execucao: dict) -> dict:
        contagem = s.executar(
            self._sql("SELECT status, COUNT(*) AS total FROM {itens} WHERE execucao_id = :id GROUP BY status"),
            id=execucao["id"],
        ).linhas()
        return {**execucao, "contagem": {linha["status"]: linha["total"] for linha in contagem}}

    def listar(self, limite: int = 20) -> list[dict]:
        with self._sessao() as s:
            linhas = s.executar(self._sql("SELECT * FROM {execucoes} ORDER BY id DESC", limite=limite)).linhas()
            return [self._resumo(s, linha) for linha in linhas]

    def obter(self, execucao_id: int) -> dict | None:
        with self._sessao() as s:
            linha = s.executar(self._sql("SELECT * FROM {execucoes} WHERE id = :id"), id=execucao_id).linha()
            if linha is None:
                return None
            itens = s.executar(self._sql("SELECT * FROM {itens} WHERE execucao_id = :id ORDER BY id"), id=execucao_id).linhas()
            return {**self._resumo(s, linha), "itens": itens}

    def em_andamento(self) -> dict | None:
        with self._sessao() as s:
            # Também aqui: o dashboard mostra o andamento enquanto houver execução viva.
            self._encerrar_paradas(s)
            linha = s.executar(self._sql("SELECT * FROM {execucoes} WHERE status = 'em_andamento'")).linha()
            if linha is None:
                return None
            # Em "todas as filiais", informa em qual filial o robô está agora.
            atual = s.executar(
                self._sql(
                    "SELECT filial FROM {eventos} WHERE execucao_id = :id AND filial IS NOT NULL ORDER BY id DESC",
                    limite=1,
                ),
                id=linha["id"],
            ).linha()
            return {**self._resumo(s, linha), "filial_atual": atual["filial"] if atual else None}

    def ultimo_envio(self, filial: str, tipo: str) -> dict | None:
        """Horário e pessoas do último envio bem-sucedido, ou None se nunca houve."""
        with self._sessao() as s:
            item = s.executar(
                self._sql(
                    "SELECT id, horario FROM {itens} WHERE filial = :filial AND tipo = :tipo AND status = 'enviado' "
                    "ORDER BY id DESC",
                    limite=1,
                ),
                filial=filial, tipo=tipo,
            ).linha()
            if item is None:
                return None
            # O id segue a ordem em que as pessoas foram gravadas (por data do aniversário).
            pessoas = s.executar(
                self._sql("SELECT nome, dia, secao FROM {pessoas} WHERE item_id = :id ORDER BY id"), id=item["id"]
            ).linhas()
        return {"horario": item["horario"], "pessoas": pessoas}

    def ultimos_por_filial(self) -> dict[tuple[str, str], dict]:
        """Último resultado e último envio bem-sucedido de cada par filial e tipo."""
        with self._sessao() as s:
            ultimos = s.executar(
                self._sql("SELECT i.* FROM {itens} i JOIN (SELECT MAX(id) AS id FROM {itens} GROUP BY filial, tipo) m ON i.id = m.id")
            ).linhas()
            envios = s.executar(
                self._sql("SELECT filial, tipo, MAX(horario) AS horario FROM {itens} WHERE status = 'enviado' GROUP BY filial, tipo")
            ).linhas()
        ultimo_envio = {(linha["filial"], linha["tipo"]): linha["horario"] for linha in envios}
        return {
            (item["filial"], item["tipo"]): {
                "status": item["status"],
                "mensagem": item["mensagem"],
                "horario": item["horario"],
                "quantidade": item["quantidade"],
                "ultimo_envio": ultimo_envio.get((item["filial"], item["tipo"])),
            }
            for item in ultimos
        }
