# Tela API (US-007; COMP-013). Writes source credentials and model provider keys, and never reads them back:
# a credential change is a KV v2 merge patch, so the API has no "read" on any secret path.
# __MOUNT__ is the KV v2 mount (TELA_SECRETS_MOUNT); deploy tooling substitutes it.
path "__MOUNT__/data/tenants/+/connections/*" {
  capabilities = ["create", "update", "patch"]
}
path "__MOUNT__/data/tenants/+/llm/*" {
  capabilities = ["create", "update", "patch"]
}
# Shared SSH key library (ENH-004): private keys and certificates for tunnels.
path "__MOUNT__/data/tenants/+/ssh_keys/*" {
  capabilities = ["create", "update", "patch"]
}
path "__MOUNT__/metadata/tenants/+/connections/*" {
  capabilities = ["delete"]
}
path "__MOUNT__/metadata/tenants/+/llm/*" {
  capabilities = ["delete"]
}
path "__MOUNT__/metadata/tenants/+/ssh_keys/*" {
  capabilities = ["delete"]
}
