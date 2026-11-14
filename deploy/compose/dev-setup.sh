#!/usr/bin/env bash
# Generate local-development secrets and render the Keycloak dev realm.
# Everything goes to deploy/compose/secrets/ (git-ignored). Re-running keeps existing secrets.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
secrets="$here/secrets"
mkdir -p "$secrets"
chmod 700 "$secrets"

gen() { # gen <name> [bytes]
  local file="$secrets/$1"
  if [[ ! -s "$file" ]]; then
    openssl rand -base64 "${2:-32}" | tr -d '\n=+/' | cut -c1-40 > "$file"
  fi
  chmod 644 "$file"  # read by non-root container users; the directory itself is 700 on the host
}

for s in postgres_owner_password tela_app_password keycloak_db_password redis_password \
         keycloak_admin_password s3_secret_key s3_runner_secret_key bao_token dev_admin_password dev_admin2_password dev_creator_password \
         dev_viewer_password sample_tpg_owner_password sample_tpg_ro_password sample_mysql_root_password \
         sample_mysql_ro_password sample_mssql_ro_password sample_mongo_root_password sample_mongo_ro_password; do
  gen "$s"
done
gen service_signing_key 48
# OpenBao AppRole credentials, one pair per service (US-007): only openbao-init sees the root token.
for svc in api runner gateway; do gen "bao_${svc}_role_id"; gen "bao_${svc}_secret_id"; done
# SQL Server's sa password must mix character classes.
[[ -s "$secrets/sample_mssql_sa_password" ]] || { gen sample_mssql_sa_password_raw; printf 'Sa1!%s' "$(cat "$secrets/sample_mssql_sa_password_raw")" > "$secrets/sample_mssql_sa_password"; rm -f "$secrets/sample_mssql_sa_password_raw"; chmod 644 "$secrets/sample_mssql_sa_password"; }

# Ed25519 key pair for execution envelopes (US-021): the API signs, the runner only verifies.
if [[ ! -s "$secrets/execution_signing_key" ]]; then
  openssl genpkey -algorithm ed25519 -out "$secrets/execution_signing_key"
  openssl pkey -in "$secrets/execution_signing_key" -pubout -out "$secrets/execution_verify_key"
fi
chmod 644 "$secrets/execution_signing_key" "$secrets/execution_verify_key"

# Synthetic TPG extract for the dev profile's sample source (never client data).
python3 "$here/../../packs/pmo/fixtures/tpg_synthetic.py" --sql "$here/sample-tpg/02-data.sql" >/dev/null
python3 "$here/../../packs/pmo/fixtures/tpg_synthetic.py" --dialect mysql --sql "$here/sample-mysql/01-data.sql" >/dev/null
python3 "$here/../../packs/pmo/fixtures/tpg_synthetic.py" --dialect tsql --sql "$here/sample-mssql/02-data.sql" >/dev/null
python3 "$here/../../packs/pmo/fixtures/tpg_synthetic.py" --dialect dataverse --sql "$here/sample-mssql/03-dataverse.sql" >/dev/null
python3 "$here/../../packs/pmo/fixtures/tpg_synthetic.py" --dialect mongosh --sql "$here/sample-mongo/01-data.js" >/dev/null

pw() { cat "$secrets/$1"; }

printf 'postgresql://tela_app:%s@postgres:5432/tela' "$(pw tela_app_password)" > "$secrets/database_url"
printf 'postgresql://tela_owner:%s@postgres:5432/tela' "$(pw postgres_owner_password)" > "$secrets/migrate_database_url"
printf 'redis://:%s@redis:6379/0' "$(pw redis_password)" > "$secrets/redis_url"
chmod 644 "$secrets"/database_url "$secrets"/migrate_database_url "$secrets"/redis_url

export TELA_WEB_ORIGIN="${TELA_WEB_ORIGIN:-http://tela.localhost:8088}"
export TELA_DEV_ADMIN_PASSWORD="$(pw dev_admin_password)"
export TELA_DEV_ADMIN2_PASSWORD="$(pw dev_admin2_password)"
export TELA_DEV_CREATOR_PASSWORD="$(pw dev_creator_password)"
export TELA_DEV_VIEWER_PASSWORD="$(pw dev_viewer_password)"
python3 - "$here/../keycloak/realm-tela.template.json" "$secrets/realm-tela.json" <<'PY'
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()
text = re.sub(r"\$\{([A-Z_]+)\}", lambda m: os.environ[m.group(1)], text)
open(dst, "w").write(text)
PY
chmod 644 "$secrets/realm-tela.json"

cat > "$secrets/.env" <<EOF
POSTGRES_OWNER_PASSWORD=$(pw postgres_owner_password)
KEYCLOAK_DB_PASSWORD=$(pw keycloak_db_password)
KEYCLOAK_ADMIN_PASSWORD=$(pw keycloak_admin_password)
BAO_DEV_ROOT_TOKEN_ID=$(pw bao_token)
EOF
chmod 600 "$secrets/.env"

cat > "$secrets/s3.json" <<JSON
{"identities": [{"name": "tela", "credentials": [{"accessKey": "tela", "secretKey": "$(pw s3_secret_key)"}],
  "actions": ["Read", "Write", "List", "Tagging"]},
 {"name": "tela-runner", "credentials": [{"accessKey": "tela-runner", "secretKey": "$(pw s3_runner_secret_key)"}],
  "actions": ["Read:tela"]}]}
JSON
chmod 644 "$secrets/s3.json"

echo "Secrets ready in $secrets"
echo "Dev sign-in users (realm 'tela'): admin / nomsa (creator + CIO) / viewer"
echo "  passwords: see $secrets/dev_*_password"
