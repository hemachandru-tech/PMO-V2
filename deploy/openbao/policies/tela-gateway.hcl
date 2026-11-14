# LLM gateway (US-007; COMP-006). Reads tenant model provider keys; nothing else.
path "__MOUNT__/data/tenants/+/llm/*" {
  capabilities = ["read"]
}
