# syntax=docker/dockerfile:1

# As imagens-base podem ser trocadas por um espelho ou registro interno, sem editar
# este arquivo:  docker build --build-arg IMAGEM_PYTHON=meu.registro/python:3.12-slim .
ARG IMAGEM_PYTHON=python:3.12-slim
ARG IMAGEM_UV=ghcr.io/astral-sh/uv:0.11.32

FROM ${IMAGEM_UV} AS uv

# Etapa 1: instala as dependências exatamente como estão no uv.lock.
FROM ${IMAGEM_PYTHON} AS dependencias
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Etapa 2: imagem final, sem uv, sem cache e sem rodar como root.
FROM ${IMAGEM_PYTHON}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=dependencias /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER app
EXPOSE 8000
# A mesma imagem serve a API (padrão), o worker (`python -m app.worker`) e as rotinas
# (`python -m app.rotinas tudo`).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
