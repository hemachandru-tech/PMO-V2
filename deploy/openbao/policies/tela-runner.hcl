# Connector runner (US-007, US-023; COMP-005). Reads source credentials at execution time; nothing else.
path "__MOUNT__/data/tenants/+/connections/*" {
  capabilities = ["read"]
}
# Library SSH keys used by tunnelled connections (ENH-004).
path "__MOUNT__/data/tenants/+/ssh_keys/*" {
  capabilities = ["read"]
}
