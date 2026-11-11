#!/usr/bin/env bash
# Sobe (ou derruba) um Postgres local, sem Docker, para rodar a suíte de integração.
#
#   scripts/pg_local.sh subir     -> inicia em localhost:${PGPORT:-54329}
#   scripts/pg_local.sh derrubar  -> para e apaga os dados
#
# Depois: export DATABASE_URL_TESTE=postgresql+psycopg://lg:lg@localhost:54329/lg_teste
set -euo pipefail

PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
PGDATA="${PGDATA:-${TMPDIR:-/tmp}/lg-pgdata}"
PGPORT="${PGPORT:-54329}"

# initdb se recusa a rodar como root; nesse caso usamos o usuário "postgres" do sistema.
como_postgres() {
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u postgres -- "$@"
  else
    "$@"
  fi
}

case "${1:-}" in
  subir)
    if [ ! -d "$PGDATA" ]; then
      mkdir -p "$PGDATA"
      [ "$(id -u)" -eq 0 ] && chown postgres "$PGDATA"
      como_postgres "$PG_BIN/initdb" -D "$PGDATA" --auth=trust --username=postgres \
        --encoding=UTF8 --locale=C.UTF-8 >/dev/null
    fi
    como_postgres "$PG_BIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -c listen_addresses=localhost" \
      -l "$PGDATA/postgres.log" -w start
    como_postgres "$PG_BIN/psql" -p "$PGPORT" -U postgres -h localhost -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lg') THEN
    CREATE ROLE lg LOGIN PASSWORD 'lg';
  END IF;
END $$;
SELECT 'CREATE DATABASE lg_teste OWNER lg' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lg_teste')\gexec
SELECT 'CREATE DATABASE lg_dev OWNER lg' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lg_dev')\gexec
SQL
    echo "Postgres local em localhost:$PGPORT (bancos lg_teste e lg_dev, usuário lg/lg)"
    ;;
  derrubar)
    como_postgres "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast stop || true
    rm -rf "$PGDATA"
    ;;
  *)
    echo "uso: $0 subir|derrubar" >&2
    exit 2
    ;;
esac
