#!/usr/bin/env bash
# Connector conformance matrix (US-117): throwaway engine containers with synthetic data only.
#
#   scripts/conformance.sh up      start PostgreSQL, MySQL, SQL Server and MongoDB test containers
#   scripts/conformance.sh test    run the conformance and dialect parity suites in an image with ODBC Driver 18
#   scripts/conformance.sh down    remove the containers and the network
#
# The SQL Server image is amd64-only (Rosetta on Apple silicon, about 2 GB of memory).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
net=tela-conformance
image="${TELA_CONFORMANCE_IMAGE:-tela-conformance:local}"

up() {
  docker network inspect "$net" >/dev/null 2>&1 || docker network create "$net" >/dev/null
  start tela-conf-postgres -e POSTGRES_USER=conf_owner -e POSTGRES_PASSWORD=conf_owner_pw postgres:16-alpine
  start tela-conf-mysql -e MYSQL_ROOT_PASSWORD=root_pw_local mysql:8.4.10
  start tela-conf-mssql --platform linux/amd64 -e ACCEPT_EULA=Y -e 'MSSQL_SA_PASSWORD=Sa_pw_local_1!' \
    mcr.microsoft.com/mssql/server:2022-latest
  start tela-conf-mongo -e MONGO_INITDB_ROOT_USERNAME=root -e MONGO_INITDB_ROOT_PASSWORD=root_pw_local mongo:7.0
  echo "waiting for the engines..."
  for _ in $(seq 1 90); do
    if docker exec tela-conf-postgres pg_isready -U conf_owner >/dev/null 2>&1 \
       && docker exec tela-conf-mysql mysqladmin ping -uroot -proot_pw_local --silent >/dev/null 2>&1 \
       && docker logs tela-conf-mssql 2>&1 | grep -q "ready for client connections" \
       && docker exec tela-conf-mongo mongosh --quiet --eval 'db.runCommand({ping: 1}).ok' >/dev/null 2>&1; then
      echo "ready"; return 0
    fi
    sleep 2
  done
  echo "engines did not become ready" >&2; exit 1
}

start() {
  local name="$1"; shift
  docker ps -a --format '{{.Names}}' | grep -qx "$name" && { docker start "$name" >/dev/null; return; }
  docker run -d --name "$name" --network "$net" "$@" >/dev/null
}

test_() {
  docker build -q -f "$here/deploy/docker/python.Dockerfile" --target conformance \
    -t "$image" "$here" >/dev/null
  docker run --rm --network "$net" -v "$here:/src:ro" -w /src \
    -e TELA_TEST_POSTGRES_URL=postgresql://conf_owner:conf_owner_pw@tela-conf-postgres:5432 \
    -e TELA_TEST_MYSQL_URL=mysql://root:root_pw_local@tela-conf-mysql:3306 \
    -e 'TELA_TEST_MSSQL_URL=mssql://sa:Sa_pw_local_1!@tela-conf-mssql:1433' \
    -e TELA_TEST_MONGO_URL=mongodb://root:root_pw_local@tela-conf-mongo:27017 \
    "$image" python -m pytest -q -p no:cacheprovider services/connector-runner/tests \
    services/api/tests/test_dialect_parity.py "$@"
}

down() {
  docker rm -f tela-conf-postgres tela-conf-mysql tela-conf-mssql tela-conf-mongo >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}

case "${1:-}" in
  up) up ;;
  test) shift; test_ "$@" ;;
  down) down ;;
  *) echo "usage: $0 up|test|down" >&2; exit 2 ;;
esac
