#!/bin/sh
# Service identities for the secrets store (US-007; COMP-013; API-069).
#
# Enables AppRole, writes one policy per service from deploy/openbao/policies and gives each service a role
# whose role_id and secret_id come from mounted files, so the API, runner and gateway never hold the root
# token. Idempotent: the local profile runs it on every `compose up` (dev-mode OpenBao keeps nothing on
# restart). Other profiles run the same steps from their deploy tooling (Helm, US-121).
#
# Env: BAO_ADDR, BAO_TOKEN_FILE (an operator token), TELA_SECRETS_MOUNT (KV v2 mount, default "tela"),
#      POLICY_DIR, CREDS_DIR (holds bao_<service>_role_id and bao_<service>_secret_id).
set -eu

BAO_TOKEN="$(cat "${BAO_TOKEN_FILE:?}")"
export BAO_TOKEN
mount="${TELA_SECRETS_MOUNT:-tela}"
policy_dir="${POLICY_DIR:-/policies}"
creds_dir="${CREDS_DIR:-/run/secrets}"

if ! bao secrets list -format=json | grep -q "\"$mount/\""; then
  bao secrets enable -path="$mount" -version=2 kv
fi
if ! bao auth list -format=json | grep -q '"approle/"'; then
  bao auth enable approle
fi

for svc in api runner gateway; do
  role="tela-$svc"
  sed "s|__MOUNT__|$mount|g" "$policy_dir/$role.hcl" | bao policy write "$role" - >/dev/null
  # One-hour tokens, renewed by logging in again; secret_ids do not expire here (rotated by redeploying).
  bao write -f "auth/approle/role/$role" token_policies="$role" token_ttl=1h token_max_ttl=4h \
    secret_id_ttl=0 secret_id_num_uses=0 token_type=service >/dev/null
  bao write -f "auth/approle/role/$role/role-id" role_id="$(cat "$creds_dir/bao_${svc}_role_id")" >/dev/null
  secret_id="$(cat "$creds_dir/bao_${svc}_secret_id")"
  if ! bao write -format=json "auth/approle/role/$role/secret-id/lookup" secret_id="$secret_id" 2>/dev/null \
      | grep -q '"data"'; then
    bao write -f "auth/approle/role/$role/custom-secret-id" secret_id="$secret_id" >/dev/null
  fi
  echo "openbao: $role ready"
done
