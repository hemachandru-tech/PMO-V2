# Feature comparison: Tela stack vs PMO Dashboard

Source: code reading of both codebases (no running). ✅ has, ⚠️ partial, ❌ missing.

| | Has | Partial | Missing |
|---|---|---|---|
| Tela | 24 | 6 | 28 |
| PMO Dashboard | 39 | 8 | 11 |

**Only Tela has:** ClickHouse; Median / percentile measures; Admin-tunable thresholds; PowerPoint; Runs without Docker

**Only PMO Dashboard has:** SQL Server; MongoDB; Dataverse; Immutable model versions; Answers without an LLM (rules planner); Weighted health score; Publish workflow with pre-checks and approval; Certification queue with look-alikes; In-app notifications; SSO (OIDC / Entra ID); SCIM user provisioning; MFA; Multi-tenant isolation; Permission request workflow; Secrets store; Signed query execution + separate runner; Database-backed metadata; Horizontal scaling; Background jobs; Metrics / tracing; Hosted CI pipeline; Backup / retention

**Neither has:** Financial actuals (spend); Resource capacity / utilisation; History / trend snapshots; Scheduled e-mail reports; Kubernetes / Helm; Localisation

| Area | Feature | Tela | PMO Dashboard | Evidence |
|---|---|---|---|---|
| Data sources | MySQL | ✅ | ✅ | Tela executors.py:56 (TLS off :72); PMO adapters/mysql.py (read-only proof, TLS verify-full) |
| Data sources | PostgreSQL | ✅ | ✅ | Tela executors.py:396; PMO adapters/postgres |
| Data sources | SQL Server | ❌ | ✅ | PMO adapters/sqlserver.py (pyodbc) |
| Data sources | MongoDB | ❌ | ✅ | PMO adapters (pymongo) |
| Data sources | Dataverse | ❌ | ⚠️ | PMO built against tests/fakes/dataverse_fake.py only |
| Data sources | ClickHouse | ✅ | ❌ | Tela clickhouse-connect (requirements.txt) |
| Data sources | Excel/CSV upload | ✅ | ✅ | Tela semantic upload-extract (DuckDB); PMO connections/files.py |
| Semantic model | Model built from introspection | ✅ | ✅ | Tela auto-approves every table (bootstrap.py); PMO draft->questions->approve (model/router.py) |
| Semantic model | Certified measure definitions with drafts | ✅ | ✅ | Tela routers/definitions.py:6-11; PMO governance/certification.py |
| Semantic model | Immutable model versions | ❌ | ✅ | PMO model/router.py:1-10 |
| Semantic model | Industry pack + name mapping | ⚠️ | ✅ | Tela: TPG map only in dashboards/app/model/ni_pmo.json; PMO packs/, packs/mapping.py, Pack Studio |
| Semantic model | Median / percentile measures | ⚠️ | ❌ | Tela only if LLM writes it; PMO MEASURE_UNAVAILABLE on MySQL (compiler.py:987-994) |
| Ask (NL) | LLM NL-to-SQL | ✅ | ✅ | Tela planner.py (only path); PMO Direct SQL mode ask/direct_sql.py |
| Ask (NL) | Answers without an LLM (rules planner) | ❌ | ✅ | Tela planner.py:343 fails; PMO ask/planner.py certified-first |
| Ask (NL) | Hosted LLM providers | ✅ | ✅ | Tela Gemini/OpenAI/Anthropic (llm_config.py); PMO + Azure OpenAI, Ollama (gateway providers) |
| Ask (NL) | PII masking / leak scan before LLM | ✅ | ✅ | Tela gateway.py; PMO tela_gateway/main.py:228-252 |
| Ask (NL) | Document knowledge / RAG | ⚠️ | ✅ | Tela embeddings for table retrieval only; PMO knowledge/* with pgvector |
| Ask (NL) | Refine / undo / clarify | ✅ | ✅ | Tela ask.py:151; PMO ask/router.py |
| Ask (NL) | Concurrent questions | ⚠️ | ⚠️ | Tela 1 globally (turns.py:136-140); PMO 8 per instance, in-process (ask/router.py:56) |
| PMO content | Ready-made PMO KPIs | ✅ | ✅ | Tela 225 cards (model/sql/kpi_*.json); PMO ~200 measures (packs/pmo) |
| PMO content | Ready-made dashboards | ✅ | ⚠️ | Tela 30 dashboards (dash_*.js); PMO starter blueprints (report_blueprints.yaml) |
| PMO content | Project / schedule / RAID | ✅ | ✅ | Tela calc.js:60-115, 83-87; PMO pack measures |
| PMO content | Weighted health score | ❌ | ✅ | PMO pmo_health_v1 35/25/25/15 (manifest.yaml:523-558) |
| PMO content | Financial actuals (spend) | ❌ | ❌ | Tela dash_del.js:249 says not in data; PMO budget/forecast only |
| PMO content | Resource capacity / utilisation | ❌ | ❌ | Both: timesheet hours only |
| PMO content | History / trend snapshots | ❌ | ❌ | Tela dash_exec.js:93; PMO report_blueprints.yaml:661-665 'unavailable' |
| PMO content | Drill-down to records | ✅ | ✅ | Tela ui.js:786-800; PMO /widgets/{id}/drill |
| PMO content | Admin-tunable thresholds | ✅ | ❌ | Tela /api/thresholds (pmo_server.py:445); none found in PMO |
| Authoring | Chat-built reports / dashboards | ✅ | ✅ | Tela dashboard_agent, custom_dashboards; PMO planner + canvas |
| Authoring | Publish workflow with pre-checks and approval | ❌ | ✅ | PMO content/dashboards.py (six pre-publish checks) |
| Authoring | Dashboard versions + restore | ✅ | ✅ | Tela api/dashboard_versions.py; PMO content/dashboards.py:793-819 |
| Authoring | Certification queue with look-alikes | ❌ | ✅ | PMO governance/certification.py, lookalike.py |
| Authoring | In-app notifications | ❌ | ✅ | PMO notifications (no e-mail) |
| Authoring | Scheduled e-mail reports | ❌ | ❌ | No SMTP code in either |
| Export | Excel | ✅ | ✅ | Tela routers/exports.py:31 (not wired into dashboards); PMO worker export/xlsx.py |
| Export | PDF | ✅ | ✅ | Tela exports.py; PMO worker export/pdf.py |
| Export | PowerPoint | ✅ | ❌ | Tela exports.py:31 pptx |
| Export | CSV of record lists | ✅ | ⚠️ | Tela ui.js:606 (no formula guard); PMO via xlsx |
| Identity & access | SSO (OIDC / Entra ID) | ❌ | ✅ | PMO identity/jwt.py:98-132; Tela local bcrypt accounts |
| Identity & access | SCIM user provisioning | ❌ | ✅ | PMO identity/scim.py |
| Identity & access | MFA | ❌ | ⚠️ | PMO delegates to the IdP; Tela none |
| Identity & access | Multi-tenant isolation | ❌ | ✅ | Tela tenant_id='default' (routers/auth.py:41); PMO Postgres RLS (0006_rls.py) |
| Identity & access | Row-level scope per user | ✅ | ✅ | Tela access.py:308 (bypass via Tela, pmo_access.py:8-9); PMO scope policies, fail-closed |
| Identity & access | Column / field hiding | ✅ | ✅ | Tela grants; PMO field policies |
| Identity & access | Permission request workflow | ❌ | ✅ | PMO identity/grant_requests.py |
| Identity & access | Login lockout / rate limit | ⚠️ | ✅ | Dashboards yes, Tela login none (routers/auth.py:32-43); PMO IdP + rate limits |
| Security | Secrets store | ❌ | ✅ | Tela plaintext JSON/.env; PMO OpenBao/Vault (file backend in dev) |
| Security | Signed query execution + separate runner | ❌ | ✅ | PMO envelope.py, connector-runner |
| Security | Tamper-evident audit log | ⚠️ | ✅ | Tela SQLite audit, trace without user id; PMO hash-chained (0005_ops.py) |
| Operations | Database-backed metadata | ❌ | ✅ | Tela JSON files (app.py:87-88); PMO Postgres |
| Operations | Horizontal scaling | ❌ | ⚠️ | Tela single worker enforced (app.py:111-113); PMO stateless API, in-process limits |
| Operations | Background jobs | ❌ | ✅ | PMO Celery worker + beat |
| Operations | Metrics / tracing | ❌ | ✅ | PMO Prometheus + OpenTelemetry |
| Operations | Hosted CI pipeline | ❌ | ⚠️ | Tela local scripts only; PMO ci.yml, never run on a hosted runner |
| Operations | Runs without Docker | ✅ | ❌ | Tela stack.sh; PMO compose only |
| Operations | Kubernetes / Helm | ❌ | ❌ | PMO deploy/helm/README.md only |
| Operations | Backup / retention | ❌ | ⚠️ | PMO retention_sweep stub (maintenance.py:29-33) |
| Operations | Localisation | ❌ | ❌ | No i18n in either |
