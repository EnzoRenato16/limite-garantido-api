#!/usr/bin/env bash
# Sobe tudo na sua máquina, sem Docker: migrações, parceiro de exemplo, worker e API.
# Usa as variáveis do .env (por padrão, um arquivo SQLite). Depois abra:
#
#   http://localhost:8000/demo   a página de demonstração
#   http://localhost:8000/docs   a documentação da API
#
# Ctrl+C derruba os dois processos.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env

uv run alembic upgrade head
uv run python -m app.seed

uv run python -m app.worker &
WORKER=$!
trap 'kill "$WORKER" 2>/dev/null || true' EXIT

uv run uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
