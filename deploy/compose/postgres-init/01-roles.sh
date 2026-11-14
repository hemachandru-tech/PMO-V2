#!/usr/bin/env bash
# First-start initialisation of the local PostgreSQL container.
# * tela_app: the application role. It does not own tables and cannot bypass RLS (US-006).
# * keycloak: separate role and database for the identity broker (db_scheme §2).
set -euo pipefail

app_pw="$(cat /run/secrets/tela_app_password)"
kc_pw="$(cat /run/secrets/keycloak_db_password)"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pw="$app_pw" -v kc_pw="$kc_pw" <<'SQL'
CREATE ROLE tela_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'app_pw';
CREATE ROLE keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'kc_pw';
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
