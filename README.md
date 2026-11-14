# Tela — Conversational PMO Analytics & Dashboard Platform

Chat-first, governed PMO reporting: connect sources read-only, ask in plain English, get scoped
and explainable answers, and publish certified, versioned dashboards to Microsoft-group roles.

| Document | Purpose |
|---|---|
| [summary.md](summary.md) | Scope, business requirements, functions and technical design |
| [task.md](task.md) | Delivery backlog: phases → epics → stories → tasks |
| [implement_log.md](implement_log.md) | Log of each execution: work done, tokens, cost, duration |
| [project-docs/](project-docs/) | Source documentation pack (requirements, rules, architecture, schema, APIs, risks) |

**Status:** P0 (Foundation) and P1 (governed read path) are implemented: PostgreSQL connections, the model
studio with its assistant, the PMO starter pack, Home, Ask and the dashboard viewer with definitions and drill.
P2's core authoring slice is implemented: refine/undo/pin in Ask, the report planner and 12-column canvas with
chat edit, dashboards with the six pre-publish checks, Admin publishing, versions and restore, view-only
sharing, the audit log viewer and notifications. EPIC-07/08 have their API: personal groupings, bands and
cross-table measures, checked custom SQL through the scoped path, and the certification queue with look-alikes
and decisions (their screens come with EPIC-UX). Still open in P2: exports (EPIC-10 part 2), starter content admin (EPIC-11), knowledge/RAG (EPIC-03) and
the AI settings screens. See task.md for what is done and next.

## Repository layout

```text
apps/web/                 React + TypeScript SPA (Vite, Tailwind + shadcn/ui from design tokens), OIDC sign-in; /dev/ui component catalogue in dev builds
services/api/             FastAPI modular monolith (identity, access, connections, model, packs, query engine)
services/llm-gateway/     The only path to LLM providers: policy, masking, leak scan, metadata ledger
services/connector-runner/ Isolated read-only execution of signed statements (PostgreSQL adapter)
workers/                  Celery worker + beat: outbox relay, partitions, retention
packages/tela-common/     Shared: UUID v7, error codes, redacting logs, secrets, object store, service tokens,
                          Ed25519 execution envelopes
packages/query-spec/      The Query Spec and model snapshot contract (Python + generated TypeScript types)
packs/pmo/                PMO starter pack manifest and the synthetic TPG fixture generator
migrations/               Alembic revisions (explicit SQL) for the metadata database
deploy/compose/           Docker Compose stack, edge proxy, dev secrets script
deploy/docker/            Multi-stage Dockerfile for the Python services
deploy/keycloak/          Keycloak realm template for local development
tests/smoke/              End-to-end checks against a running stack
```

## Run the stack locally

Needs Docker. Everything runs in containers; only the edge proxy publishes a port, bound to 127.0.0.1:8088.

```bash
./deploy/compose/dev-setup.sh                      # generate local secrets and the dev realm (git-ignored)
docker compose -f deploy/compose/compose.yaml --env-file deploy/compose/secrets/.env \
    --profile core --profile dev up -d --build     # `dev` adds the synthetic sample TPG source
open http://tela.localhost:8088
```

The `dev` profile's `sample-tpg` source is reachable only by the connector runner. Connect to it (as Admin,
through the API) with host `sample-tpg`, database `tpg`, user `tela_ro`, the password in
`deploy/compose/secrets/sample_tpg_ro_password` and TLS mode `disable` (development only). Then introspect,
install the PMO pack, approve the model and add scope policies; `tests/smoke/test_p1_read_path.py` does
exactly this and checks the reference counts and a CIO's scope. In the browser, Admins use **Admin · Data** for
connections and the model studio; everyone gets Home, Ask and the starter dashboards.

**More sources (P3).** The `dev` profile also seeds the same synthetic extract on `sample-mysql` (MySQL 8.4),
`sample-mssql` (SQL Server 2022, amd64) and `sample-mongo` (MongoDB 7), and runs `dataverse-fake`, a fake
Dataverse environment (DEC-09 access is pending). Each compose service comment lists its connection settings;
an Excel or CSV source is created by uploading a file in **Admin · Data → Add connection**.
`tests/smoke/test_p3_sources.py` takes every engine through the governed path. With several sources, Ask goes
to the one named in the question or used in the conversation, and otherwise offers a choice.

**Authoring (P2).** Sign in as `nomsa` (Creator): **Build with chat** on Home plans a report from one sentence,
**Build** creates it and opens the canvas (drag, resize or use the ←→↑↓ −+ buttons; edit with chat; Undo / Undo all).
**Pin** on an answer adds it to a report. **Dashboards** composes reports into a dashboard; Creators **Request
publish**, and `admin` publishes to roles after the six checks pass. `tests/smoke/test_p2_authoring.py` and
`apps/web/e2e/authoring.spec.ts` run these journeys against the stack.

**Language models (optional).** No model is used until an Admin chooses one in Admin → AI settings (DEC-01). The
local profile allow-lists Ollama on the host (`http://host.docker.internal:11434`) and the hosted providers approved
under DEC-01: Anthropic, OpenAI, Azure OpenAI and Gemini (narrow the list with `TELA_LLM_ALLOWED_PROVIDERS`). A hosted
provider needs the tenant's API key (stored in OpenBao, never shown again); Azure also needs the resource endpoint.
For Ollama, run `ollama pull llama3.2:3b`, then
`PUT /api/v1/admin/llm-settings` `{"provider": "ollama", "models": {"planner": "llama3.2:3b"}}`. Totals and counts
reach a model only when `TELA_LLM_ALLOW_AGGREGATES` (default `true`) and the tenant's toggle are both on; rows never
do. Every agent has a deterministic fallback, so Tela works the same without a model.

Sign in as `admin`, `nomsa` (Creator + CIO audience role) or `viewer`. Their passwords are in
`deploy/compose/secrets/dev_*_password`. Stop the stack with `docker compose ... down` (add `-v` to wipe the data).

The local profile uses Keycloak in dev mode, OpenBao in dev mode and SeaweedFS as the S3-compatible
store. Other environments use production-mode Keycloak brokering Microsoft Entra ID, a sealed
OpenBao/Vault, and a managed object store.

## Develop and test

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22 and pnpm 9.

```bash
uv sync                                    # Python workspace
uv run ruff check . && uv run mypy packages/tela-common/src services/api/src services/llm-gateway/src \
    services/connector-runner/src workers/src

# Integration tests need a migrated PostgreSQL with a non-owner application role:
docker run -d --name tela-pg-test -e POSTGRES_USER=tela_owner -e POSTGRES_PASSWORD=owner_pw \
    -e POSTGRES_DB=tela -p 55432:5432 pgvector/pgvector:pg16 -c max_connections=500
docker exec tela-pg-test psql -U tela_owner -d tela -c "CREATE ROLE tela_app LOGIN PASSWORD 'app_pw' NOBYPASSRLS"
export TELA_MIGRATE_DATABASE_URL=postgresql://tela_owner:owner_pw@localhost:55432/tela
export TELA_TEST_OWNER_DATABASE_URL=$TELA_MIGRATE_DATABASE_URL
export TELA_TEST_APP_DATABASE_URL=postgresql://tela_app:app_pw@localhost:55432/tela
uv run alembic upgrade head
caffeinate -i uv run pytest -q -n 6 --dist worksteal   # ≈ 6 min (macOS: caffeinate stops idle sleep, which
#   expires the tests' 5-minute tokens mid-run); add TELA_SMOKE=1 with the stack running for tests/smoke
# -n N: N workers, each on its own copy of the migrated database (tela_gw0 …, made from `tela` as a template on
# every run, so keep no session open on `tela` while it starts) and its own source database (tpg_gw0 …).
# Six workers need ≈ 300 connections, hence max_connections=500. Leave out -n (and add --cov) for one process.

# Connector conformance matrix (US-117): PostgreSQL, MySQL, SQL Server and MongoDB containers with the
# synthetic extract, run inside an image that has Microsoft ODBC Driver 18. Covers the contract suite, the
# dialect parity of the query engine (every pack shape equals PostgreSQL), Excel/CSV through DuckDB, and the
# Dataverse adapter against tests/fakes/dataverse_fake.py. The SQL Server image is amd64 (Rosetta on Apple silicon).
scripts/conformance.sh up && scripts/conformance.sh test && scripts/conformance.sh down

cd apps/web && pnpm install && pnpm typecheck && pnpm lint && pnpm test
pnpm exec playwright install chromium && pnpm e2e   # browser journeys against the running stack
pnpm tokens                                          # regenerate src/design/tokens.css after tokens.json changes
```

## Ground rules (from the design)

- The LLM never writes executable SQL and never sees raw rows by default; every query goes through
  one scoped execution path (ADR-001, ADR-009, REC-004).
- The application database role does not own tables and cannot bypass row-level security.
- Secrets live only in the secrets store; the database holds references. Nothing sensitive is logged.
- Provider SDKs may be imported only inside `services/llm-gateway` (enforced in CI).
