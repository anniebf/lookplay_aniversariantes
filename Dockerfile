# Imagem do RPA Lookplay Aniversariantes: API + dashboard + comando de envio.
#
# O container não guarda estado: o histórico fica no Oracle (schema RPA) e toda a
# configuração chega por variáveis de ambiente (env_file do docker-compose).
# O driver oracledb roda em modo thin, sem precisar do Oracle Instant Client.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Cuiaba \
    LOG_EM_ARQUIVO=0

# tzdata: o histórico grava a hora local, então o fuso precisa existir dentro do container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências primeiro: a camada só é refeita quando o requirements.txt muda.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Entrada usada pelo Airflow: python main.py
COPY main.py .

# Usuário sem privilégios. A pasta dados só é usada no modo exemplo (SQLite).
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/dados \
    && chown app /app/dados
USER app

EXPOSE 8765

# Saudável = o servidor responde. 401 também conta: a API pede login.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u, urllib.error as e\ntry:\n u.urlopen('http://127.0.0.1:8765/api/status', timeout=4)\nexcept e.HTTPError as x:\n raise SystemExit(x.code != 401)"]

# Um único processo: a trava de execução fica no banco, mas o cache do painel fica na memória.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers"]
