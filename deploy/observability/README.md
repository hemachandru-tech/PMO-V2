# Observability profile (US-010)

```bash
export TELA_OTEL_ENDPOINT=http://otel-collector:4317        # services send traces here
docker compose -f deploy/compose/compose.yaml --env-file deploy/compose/secrets/.env \
  --profile core --profile observability up -d --build
```

Grafana: http://localhost:3000 (`admin` / `TELA_GRAFANA_PASSWORD`, default `admin`, bound to localhost). Folder **Tela**
holds the *Tela overview* dashboard; Prometheus, Tempo and Loki are provisioned data sources, and a log line's
`trace_id` opens its trace.

| Signal | Path |
|---|---|
| Metrics | `/metrics` on api (8000), llm-gateway (8200), connector-runner (8100) → Prometheus (15 s). Internal network only; the edge proxy answers 404. |
| Traces | OTLP gRPC → collector → Tempo. api, llm-gateway, connector-runner (FastAPI + httpx), worker (Celery + httpx), api/gateway SQL statements (never parameter values). |
| Logs | Docker `json-file` logs → collector `filelog` → Loki. One JSON line per event with `request_id` and `trace_id`. |
| Correlation | The edge proxy mints `x-request-id`; services bind it to logs and forward it (API → gateway/runner, worker → API). A worker task's id is its request id. |
| Alerts | `alerts.yml`: outbox lag / relay silent, target down, 5xx rate, p95 latency, auth-failure spike, scope denials, export failures, gateway leak blocks and provider errors. No Alertmanager yet (see task.md). |

Outbox lag: the worker relay reads `ops.f_outbox_lag_seconds()` (the API role cannot) and stores it in Redis for 30 s;
the API exposes it as `tela_outbox_lag_seconds`, and `tela_outbox_relay_up` is 0 when no sample arrived.

The collector reads every container's log file, so non-Tela containers appear in Loki as `unknown_service`.
Logs are redacted at the source (`tela_common.logging`); the collector deletes `authorization`, `password`, `prompt`,
`completion` and `rows` again as a second layer.
