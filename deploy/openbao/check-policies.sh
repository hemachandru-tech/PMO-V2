#!/bin/sh
# Check what each service identity may do in the secrets store (US-007). Exits non-zero on any surprise.
# Local profile:
#   docker run --rm --network tela_app -e BAO_ADDR=http://openbao:8200 -e TELA_SECRETS_MOUNT=secret \
#     -v "$PWD/deploy/compose/secrets:/run/secrets:ro" -v "$PWD/deploy/openbao:/init:ro" \
#     --entrypoint sh quay.io/openbao/openbao:2.1.0 /init/check-policies.sh
# Writes and then deletes one probe secret under a fixed probe tenant id.
set -u
mount="${TELA_SECRETS_MOUNT:-tela}"
creds_dir="${CREDS_DIR:-/run/secrets}"
probe="tenants/00000000-0000-4000-8000-00000000abcd"
failed=0

login() {
  BAO_TOKEN= bao write -field=token auth/approle/login \
    role_id="$(cat "$creds_dir/bao_$1_role_id")" secret_id="$(cat "$creds_dir/bao_$1_secret_id")"
}
expect() { # expect allow|deny <label> <command...>
  want="$1"; label="$2"; shift 2
  if "$@" >/dev/null 2>&1; then got=allow; else got=deny; fi
  if [ "$got" = "$want" ]; then echo "ok    $want $label"; else echo "FAIL  $label: wanted $want, got $got"; failed=1; fi
}

BAO_TOKEN="$(login api)"; export BAO_TOKEN
expect allow "api writes a connection secret"  bao kv put -mount="$mount" "$probe/connections/probe" username=u password=p
expect allow "api patches it"                  bao kv patch -method=patch -mount="$mount" "$probe/connections/probe" password=p2
expect deny  "api reads it"                    bao kv get -mount="$mount" "$probe/connections/probe"
expect allow "api writes a provider key"       bao kv put -mount="$mount" "$probe/llm/provider" api_key=k
expect deny  "api reads a provider key"        bao kv get -mount="$mount" "$probe/llm/provider"
expect deny  "api writes outside tenants/"     bao kv put -mount="$mount" probe/x a=b
expect allow "api writes a library SSH key"    bao kv put -mount="$mount" "$probe/ssh_keys/probe" private_key=k
expect deny  "api reads a library SSH key"     bao kv get -mount="$mount" "$probe/ssh_keys/probe"

BAO_TOKEN="$(login runner)"
expect allow "runner reads a connection secret" bao kv get -mount="$mount" "$probe/connections/probe"
expect deny  "runner writes it"                 bao kv put -mount="$mount" "$probe/connections/probe" username=x
expect deny  "runner reads a provider key"      bao kv get -mount="$mount" "$probe/llm/provider"
expect allow "runner reads a library SSH key"   bao kv get -mount="$mount" "$probe/ssh_keys/probe"
expect deny  "runner writes a library SSH key"  bao kv put -mount="$mount" "$probe/ssh_keys/probe" private_key=x
expect deny  "runner lists tenants"             bao kv list -mount="$mount" tenants

BAO_TOKEN="$(login gateway)"
expect allow "gateway reads a provider key"     bao kv get -mount="$mount" "$probe/llm/provider"
expect deny  "gateway reads a connection secret" bao kv get -mount="$mount" "$probe/connections/probe"
expect deny  "gateway reads a library SSH key"   bao kv get -mount="$mount" "$probe/ssh_keys/probe"

BAO_TOKEN="$(login api)"
expect allow "api deletes the connection secret" bao kv metadata delete -mount="$mount" "$probe/connections/probe"
expect allow "api deletes the provider key"      bao kv metadata delete -mount="$mount" "$probe/llm/provider"
expect allow "api deletes the library SSH key"   bao kv metadata delete -mount="$mount" "$probe/ssh_keys/probe"
exit "$failed"
