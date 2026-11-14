# Proposed Technical Design

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | L1 – Business only for the product. Stack elements marked **Mandated** come from the stakeholder instruction of 2026-10-21; everything else is **Proposed**. Reference systems (S10–S31) are secondary evidence: their documents describe code we did not receive. |
| Sources analyzed | 31 files — see [project_summary.md](project_summary.md#source-inventory-and-coverage) |
| Related documents | [Project summary](project_summary.md) · [Architecture](architecture.md) · [Function document](function_document.md) · [DB scheme](db_scheme.md) · [API catalog](api_catalog.md) · [Requirements](requirements.md) · [Business rules](business_rules.md) · [Data dictionary](data_dictionary.md) · [Risk register](risk_register.md) · [Diagrams](diagrams/README.md) |

> **Read this first.** No source code, DDL, OpenAPI file, Dockerfile or pipeline was supplied for Tela. This document is a design for the team to build, not a description of a system that exists. Versions are written as "Proposed, pin at implementation". Sections that would normally cite code say "Not available — source code not supplied".

## Table of contents

- [Purpose, scope and evidence](#purpose-scope-and-evidence)
- [Technology stack](#technology-stack)
- [Repository structure](#repository-structure)
- [Module and component internals](#module-and-component-internals)
- [Query engine implementation](#query-engine-implementation)
- [Connector implementation](#connector-implementation)
- [Agent implementation](#agent-implementation)
- [RAG implementation](#rag-implementation)
- [LLM gateway implementation](#llm-gateway-implementation)
- [API implementation](#api-implementation)
- [Authentication and authorization implementation](#authentication-and-authorization-implementation)
- [Business logic implementation](#business-logic-implementation)
- [Data access layer](#data-access-layer)
- [Background jobs and events](#background-jobs-and-events)
- [Third-party integrations at code level](#third-party-integrations-at-code-level)
- [Configuration and environment variables](#configuration-and-environment-variables)
- [Error handling, logging and observability](#error-handling-logging-and-observability)
- [Security implementation](#security-implementation)
- [Build and deployment pipeline](#build-and-deployment-pipeline)
- [Local development setup](#local-development-setup)
- [Testing setup](#testing-setup)
- [Coding conventions](#coding-conventions)
- [Code versus specification discrepancies](#code-versus-specification-discrepancies)
- [Lessons from reference systems](#lessons-from-reference-systems)
- [Technical debt, risks and recommendations](#technical-debt-risks-and-recommendations)
- [Open technical questions](#open-technical-questions)
- [Change log](#change-log)

---

## Purpose, scope and evidence

This document says how Tela is to be built, configured and run at code level. [architecture.md](architecture.md) owns *what* the components are and *why* (COMP-001…COMP-017, ADR-001…ADR-014). This document does not repeat those diagrams; it links to them.

| Technical area | Evidence | Basis |
|---|---|---|
| Frontend framework (React + TypeScript) | Mandated | Stakeholder instruction 2026-10-21 |
| Backend language (Python) | Mandated | Stakeholder instruction 2026-10-21 |
| Metadata DB PostgreSQL, RAG on pgvector | Mandated | Stakeholder instruction 2026-10-21 |
| Docker image per service | Mandated | Stakeholder instruction 2026-10-21 |
| Source connectors (PostgreSQL, MySQL, SQL Server, MongoDB, Excel/CSV, Dataverse) | Mandated list; SQL Server from S01 §4.1 | Stakeholder instruction + S01 (see GAP-012) |
| Frameworks, libraries, versions | Proposed | Informed by S03, S10–S31 |
| Code structure, modules, interfaces | Proposed | This document |
| Runtime behaviour, performance | Unknown | No L5 evidence |

---

## Technology stack

Choices follow the mandate where one exists. Elsewhere each choice lists the alternative and the trade-off. **All versions: Proposed, pin at implementation** (choose current stable releases and lock them in `uv.lock` / `pnpm-lock.yaml`).

### Frontend (COMP-001)

| Concern | Proposed choice | Alternative | Trade-off / reason | Source |
|---|---|---|---|---|
| UI framework | React 18 or 19 + TypeScript (strict) | — | Mandated | User instruction |
| Build | Vite | Next.js | SPA behind API is enough; no SSR need. Reference systems use Vite (S10, S24) | S10, S24 |
| Server state | TanStack Query | Redux Toolkit Query | Cache keyed per request; simple invalidation after publish/certify | S24 |
| Client state | Zustand (small stores: canvas selection, undo stack view) | Redux | Less boilerplate | Proposed |
| Routing | React Router | TanStack Router | Familiar; matches S10 | S10 |
| Grid canvas | react-grid-layout, 12 columns | gridstack.js | 12-col snap grid required (S01 §4.6); S24 uses react-grid-layout | S01, S24 |
| Charts | Apache ECharts via `echarts-for-react` or a thin wrapper | Vega-Lite (S24), Recharts (S10) | ADR-007: broad catalogue, canvas/SVG, good with large series; chart spec stays ours | S03 §14–16 |
| Styling | Tailwind CSS + Radix UI primitives (accessible) | MUI | Radix gives accessible dialogs/popovers for definition popover and pin dialog | Proposed (REC-017) |
| Forms / validation | react-hook-form + zod (types generated from OpenAPI) | Formik | zod mirrors Pydantic schemas | Proposed |
| API types | `openapi-typescript` generated `schema.d.ts`; never hand-edited | Orval | S26 NFR-010 practice | S26 |
| SSE | `@microsoft/fetch-event-source` (supports headers, abort) | native EventSource | Bearer token header needed | Proposed |
| Tests | Vitest + React Testing Library; Playwright for E2E | Jest/Cypress | Matches S24/S31 | S24, S31 |

### Backend (COMP-002…COMP-009)

| Concern | Proposed choice | Alternative | Trade-off / reason | Source |
|---|---|---|---|---|
| Language | Python 3.12 or 3.13 | — | Mandated Python | User instruction |
| Web framework | FastAPI + Uvicorn (Gunicorn with Uvicorn workers in prod) | Litestar | Same as S03, S10, S24; async, OpenAPI out of the box | S03 §3 |
| Schemas | Pydantic v2 | attrs/msgspec | Generates OpenAPI, validates every agent output | S03, S24 |
| ORM / migrations | SQLAlchemy 2 (async, `asyncpg` or `psycopg` 3) + Alembic | SQLModel, raw SQL | Typed access + versioned migrations (S03 §3) | S03 |
| SQL parsing | sqlglot (dialects postgres, mysql, tsql, duckdb) | sqlparse (not an AST validator) | S13, S27 both use sqlglot ASTs; regex is unsafe (S25 ADR-002) | S13, S27 |
| Jobs | Celery + Redis broker (ADR-011) | Dramatiq, arq, RQ | Mature retries, beat scheduler, routing to queues | S03 §3 |
| Cache / rate limit | Redis 7 | Memcached | One dependency serves broker, cache, limits | S31 |
| Secrets | OpenBao or HashiCorp Vault (`hvac` client) | Azure Key Vault (customer-hosted Azure) | S03 §3, S31; adapter keeps both possible | S03 |
| Object storage | S3 API (`boto3`) against MinIO or Azure Blob (via `azure-storage-blob`) | Local volume (dev only) | Excel uploads and exports (ADR-012) | S31 |
| PDF export | Playwright headless Chromium rendering a print route | WeasyPrint | Pixel-identical charts (ECharts renders in browser) | Proposed |
| Excel export | openpyxl | XlsxWriter | Read and write; one sheet per widget + definitions sheet (S01 §4.3) | S26 |
| LLM SDKs | Only inside COMP-006: `openai` (Azure OpenAI + OpenAI), `anthropic`, `google-genai`, Ollama HTTP | LiteLLM | Thin own adapters keep the chokepoint auditable (ADR-008) | S24, S26 |
| Embeddings | `mxbai-embed-large` (1024-dim) on Ollama as default; provider embeddings configurable | `bge-m3`, Azure OpenAI `text-embedding-3-large` (needs dimension 1024 via `dimensions`) | Mirrors S31; local model avoids egress (Q-016) | S31 |
| Identity | Keycloak (OIDC code + PKCE), Entra ID as first IdP (ADR-003) | Direct Entra via MSAL | S03 Part A; keeps other IdPs possible | S03 |
| JWT | `pyjwt[crypto]` with JWKS cache | `python-jose` | Maintained, small | Proposed |
| Policy | In-code permission evaluator (keys) | OPA (S31) | OPA adds a service; revisit when policies grow | S31 |
| Observability | OpenTelemetry SDK (FastAPI, SQLAlchemy, Celery, httpx instrumentation), `structlog` JSON logs | Datadog agent | Vendor-neutral (S11 REC-007) | S11 |
| Packaging | `uv` workspaces | Poetry | Fast lockfile; monorepo friendly | Proposed |
| Lint / type | ruff, mypy (strict on `packages/*`), eslint, tsc `--noEmit` | flake8, pyright | Standard | Proposed |

### Connector drivers (COMP-005)

| Engine | Driver (Proposed) | Dialect for sqlglot | Notes |
|---|---|---|---|
| PostgreSQL | `psycopg` 3 (sync in runner, pooled) | `postgres` | Reference S24 used psycopg2 |
| MySQL / MariaDB | `PyMySQL` (pure Python) or `mysqlclient` (faster, needs libmysqlclient) | `mysql` | S10, S24 used PyMySQL |
| SQL Server / Azure SQL | `pyodbc` + Microsoft ODBC Driver 18 for SQL Server; `pymssql` as fallback | `tsql` | ODBC 18 defaults to encrypted connections |
| MongoDB | `pymongo` | n/a (aggregation pipeline) | ADR-014 |
| Excel / CSV | `duckdb` + `openpyxl` (xlsx reading) or DuckDB `excel`/`spatial` extension; `pandas` only for type sniffing | `duckdb` | ADR-012 |
| Dataverse (SQL) | `pyodbc` + ODBC Driver 18 against the TDS endpoint, Entra access token injected via `SQL_COPT_SS_ACCESS_TOKEN` | `tsql` (restricted subset) | ADR-013; **verify against current Microsoft documentation (web lookup unavailable during authoring)** |
| Dataverse (Web API) | `httpx` + `msal` (client-credentials) — OData v4 `$select/$filter/$apply` or FetchXML | n/a | Fallback when TDS is disabled or unsupported |

### Infrastructure

| Concern | Proposed | Notes |
|---|---|---|
| Metadata DB | PostgreSQL 16 + pgvector (0.8 line used by S31) | Mandated PostgreSQL + pgvector; image e.g. `pgvector/pgvector:pg16` — pin digest |
| Containers | Docker images per service, OCI, multi-stage, non-root | Mandated |
| Local / customer-hosted | Docker Compose with profiles | ADR-005 |
| Cluster | Helm chart (Kubernetes / AKS) | ADR-005; optional |
| Edge | Traefik (compose) or nginx ingress (K8s), TLS | COMP-017 |

---

## Repository structure

Proposed monorepo. One Python workspace, one pnpm workspace.

```text
tela/
├── apps/
│   └── web/                         # COMP-001 React SPA
│       ├── src/
│       │   ├── app/                 # routes, providers, auth bootstrap
│       │   ├── features/
│       │   │   ├── home/            # FEAT-017 shelves, ask bar
│       │   │   ├── ask/             # FEAT-018..022 chat, spec chips, pin dialog
│       │   │   ├── reports/         # FEAT-023..027 plan, canvas, chat edit
│       │   │   ├── fields/          # FEAT-028..032 grouping, bands, joins, custom SQL
│       │   │   ├── certification/   # FEAT-033..037
│       │   │   ├── dashboards/      # FEAT-038..045 compose, publish, viewer, drill, export
│       │   │   ├── admin/           # connections, model assistant, mappings, starter, settings, audit
│       │   │   └── knowledge/       # FEAT-014..016 documents, gold examples
│       │   ├── charts/              # chart-spec -> ECharts option renderer
│       │   ├── api/                 # generated schema.d.ts + client
│       │   └── print/               # print-only routes used by PDF export
│       └── e2e/                     # Playwright
├── services/
│   ├── api/                         # COMP-002 FastAPI modular monolith (+ COMP-003, COMP-004 as modules)
│   │   └── tela_api/
│   │       ├── main.py              # app factory, routers, middleware
│   │       ├── core/                # settings, db session, tenancy, errors, telemetry
│   │       ├── identity/            # FEAT-001..004: JWT, principals, SCIM, mappings, policies
│   │       ├── connections/         # FEAT-005,006,011..013 metadata side (no source execution)
│   │       ├── model/               # FEAT-007..010 data model, versions, starter packs
│   │       ├── ask/                 # FEAT-018..022 turns, SSE, agent orchestrator (COMP-003)
│   │       ├── query/               # COMP-004 spec compiler, validator, scope injector
│   │       ├── content/             # widgets, reports, dashboards, publications, shares
│   │       ├── governance/          # certification queue, look-alikes, decisions
│   │       ├── export/              # export job API
│   │       ├── knowledge/           # COMP-007 retrieval + indexing API
│   │       └── admin/               # audit, LLM settings proxy, usage, starter admin
│   ├── connector-runner/            # COMP-005 isolated execution service
│   │   └── tela_runner/
│   │       ├── main.py              # internal HTTP (mTLS) API: /execute, /introspect, /test, /cancel
│   │       ├── adapters/            # postgres.py mysql.py sqlserver.py mongodb.py excel.py dataverse_tds.py dataverse_webapi.py
│   │       └── guards.py            # read-only verification, limits, result masking (RULE-020)
│   └── llm-gateway/                 # COMP-006
│       └── tela_gateway/
│           ├── main.py              # internal API: /generate, /embed
│           ├── providers/           # azure_openai.py anthropic.py gemini.py ollama.py
│           ├── masking.py scan.py budget.py audit.py
├── workers/                         # COMP-008 / COMP-009 Celery app
│   └── tela_worker/
│       ├── celery_app.py
│       └── tasks/                   # introspect, index, export_pdf, export_xlsx, cache_warm, pack_update, scim_reconcile, outbox_relay, retention
├── packages/
│   ├── query-spec/                  # shared Pydantic models + JSON Schema for Query Spec (ADR-001)
│   ├── chart-spec/                  # shared chart spec models (Python) + generated TS types
│   └── tela-common/                 # logging, tracing, error codes, ids (uuid7)
├── migrations/                      # Alembic (one history; schemas iam, conn, model, defs, content, gov, chat, rag, ops)
├── packs/
│   └── pmo/                         # FEAT-051 starter pack manifest, measures, reports (versioned)
├── deploy/
│   ├── compose/                     # compose.yaml + profiles + .env.example (no secrets)
│   ├── helm/tela/                   # chart, values.yaml, values-saas.yaml, values-customer.yaml
│   └── keycloak/                    # realm export template (no secrets)
├── tests/                           # cross-service integration and security suites
└── .github/workflows/               # CI (see Build and deployment pipeline)
```

Entry points (Proposed): `tela_api.main:create_app`, `tela_runner.main:app`, `tela_gateway.main:app`, `tela_worker.celery_app:app`, `apps/web/src/main.tsx`.

---

## Module and component internals

Each module in `services/api` follows ports-and-adapters lightly: `router.py` (HTTP), `service.py` (use cases), `repo.py` (SQLAlchemy), `schemas.py` (Pydantic), `events.py` (outbox). Modules talk through service interfaces, never through each other's tables. S31 shows strict hexagonal layering works well (architecture scored 88/100) but 24 microservices hurt operations (35/100); hence one API process with internal module boundaries (ADR-006).

| Module | Responsibilities | Key classes / functions (Proposed) | Depends on | Features |
|---|---|---|---|---|
| `core` | Settings, async DB session with tenant context, error envelope, request ID, OTel | `Settings`, `get_session()`, `TenantContext`, `ApiError` | — | all |
| `identity` | Validate JWT, build `Principal`, resolve roles and `Scope`, SCIM endpoints, mappings | `JwtVerifier`, `PrincipalResolver.resolve(token)`, `ScopeResolver.for_principal(p, connection_id)`, `ScimService` | core | FEAT-001…004 |
| `connections` | Connection metadata, secret refs, test/introspect orchestration via runner, file uploads | `ConnectionService.create()`, `RunnerClient.test()`, `RunnerClient.introspect()`, `FileSourceService.register_upload()` | identity, runner | FEAT-005, 006, 011…013 |
| `model` | Data model drafts, versions, links, measures, dimensions, assistant questions, pack install | `ModelService.propose()`, `ModelService.apply_correction()`, `ModelService.approve() -> ModelVersion`, `PackInstaller.match(schema)` | connections, knowledge, ask(AG-04) | FEAT-007…010, 051 |
| `ask` (COMP-003) | Turns, SSE stream, agent orchestration, refine, clarify, pin | `TurnRunner.run(turn)`, `Orchestrator.step()`, `SpecEditor.apply(chips)` | query, knowledge, gateway client | FEAT-018…022 |
| `query` (COMP-004) | Spec validation, compile to native, custom SQL validator, scope injection, cache keying | `SpecValidator`, `SqlCompiler(dialect)`, `MongoCompiler`, `CustomSqlValidator`, `ScopeInjector`, `CacheKey` | model, identity | FEAT-018, 028…031, 042…044 |
| `content` | Widgets, reports, sections, items, dashboards, drafts/undo, versions, publications, shares | `WidgetService.run()`, `ReportService.plan()/build()`, `DashboardService.publish()`, `UndoLog` | query, governance | FEAT-021, 023…027, 038…041 |
| `governance` | Certification requests, look-alike detection, semantic checks, decisions, propagation | `CertificationService.enqueue()`, `LookAlikeDetector.find()`, `SemanticDiff.compare()`, `DecisionService.decide()` | query, content | FEAT-032…037 |
| `export` | Create export jobs, authorise, hand to worker, signed download URLs | `ExportService.request()` | content, query | FEAT-045 |
| `knowledge` (COMP-007) | Index documents/model artefacts, hybrid retrieval, review gate | `Indexer.index()`, `Retriever.search(q, k, filters)` | gateway (embed) | FEAT-014…016 |
| `admin` | Audit queries, LLM settings, usage, starter admin, health | `AuditQuery`, `SettingsService`, `StarterAdmin` | all | FEAT-046…050 |

**Connector runner (COMP-005).** Separate container with network egress only to approved source hosts. It holds the only code that touches source databases. The API never opens a source connection. Requests are signed by the API (see [Security implementation](#security-implementation)). It fetches credentials from the secrets store at execution time using its own role; the API never sees passwords after creation.

**LLM gateway (COMP-006).** Separate container with egress only to approved LLM/embedding endpoints. Only place where provider SDKs are imported. A CI check fails the build if `openai`, `anthropic`, `google.genai` or `ollama` is imported elsewhere (S27 RULE-005 practice, S25 ADR-001).

**Worker (COMP-008/009).** Same image family as API (shared code) with Chromium added for PDF. Queues: `default`, `index`, `export`, `introspect`, `maintenance`.

---

## Query engine implementation

### Query Spec (ADR-001)

Agents never write SQL for normal widgets. AG-03 returns a **Query Spec** against the approved model version; code compiles it. Shared model in `packages/query-spec`.

```json
{
  "spec_version": "1",
  "model_version_id": "0192f3c1-...-mv",
  "connection_id": "0192f3a0-...-cn",
  "measure": { "ref": "measure:overdue_go_lives" },
  "extra_measures": [],
  "breakdowns": [ { "ref": "dimension:region" } ],
  "time": { "ref": "dimension:planned_deployment_date", "grain": "month", "range": { "relative": "last_6_months" } },
  "filters": [
    { "ref": "dimension:priority", "op": "in", "values": ["Critical", "High"] }
  ],
  "personal_fields": [],
  "sort": [ { "by": "measure", "dir": "desc" } ],
  "limit": 10,
  "compare": null,
  "visual": { "type": "ranked_bar" },
  "follow_page_filters": true
}
```

Validation (`SpecValidator`): every `ref` must exist in the pinned model version (RULE-003); breakdowns must be reachable through approved links (RULE-014); `limit` ≤ cap (RULE-006); visual must be suitable (RULE-008); filters on hidden fields for this principal are rejected (FEAT-003).

**Several measures, derived figures and shares (US-176).** `measures: [a, b, …]` (up to 6 since EXEC-172, was 4) is the same as `measure` + `extra_measures`; every measure counts the same entity, so they are aggregated in one `GROUP BY`. `derived` (up to 3) works out a figure from two of the answer's certified measures, row by row: `ratio` (a ÷ b), `difference` (a − b, same unit), `percent_of` (a ÷ b × 100, same unit) and `percent_change` ((a − b) ÷ b × 100, same unit); a zero or blank divisor gives NULL. `share_of_total` lists additive measures (counts, sums) whose share of the total is added as `<measure>__share` (percent); it needs a breakdown. Sort by `derived:<key>` or `share:<measure>`. The rules read a list sharing one noun ("internal, external, contingency, hardware and software cost", `planner.measure_list`, EXEC-172) as that many measures, each item naming exactly one measure by its words plus the noun; the list's words are not read as filters. When named measures tie and the rules would ask "Which measure do you mean?", AG-03 plans the question first (`orchestrator._tied_measures`) and its plan is answered when it uses a tied measure; otherwise Ask asks. RULE-023's "By 'cost' do you mean …?" is never sent to AG-03. One row of 2–6 measures in one unit with no breakdown may be ranked bars when asked for ("… as a bar", EXEC-173; the default stays the table): the chart spec has no `x`, and the browser (`chartData.measureBars`) turns the row into one row per measure, named without the words they all share ("Internal" … under "Forecast cost (USD)"), so ECharts, Recharts and Plot draw ordinary ranked bars; a bar filters nothing. The pin dialog's fixes (`content/common.breakdown_fixes`) keep every measure: with extra measures only a table by a breakdown, paired bars for two measures in one unit and a trend for up to four are offered.

**Which words filter (BUG-025, EXEC-174).** The rules planner turns a word that matches a stored value into a filter only when it means that value (`planner._unnamed`, `_every_value`). A value of a table with many rows per row of the measure's table ("Schedule" of Risk category, "Cost" of PCR change type, for a count of projects), or a yes/no value ("no" of Key priority project), filters only when the question names its field (its label, or a word of it other than status/state/type/category/…: "reported" for Reported status) or its table ("risks"). A parent table's values and the measure's own table's need no name ("CAPEX change requests", "Active projects"). A value said inside another measure's label names that figure ("approved" of "approved budget"), unless the label is only the value, its field and a table noun ("reported amber", "schedule risk"). A filter on every value of its field ("Green, Amber, Red" as a score's bands) is dropped; the rules step loads the value list of any field filtered on two or more values so it can tell (`Orchestrator._rules`). A loose link (partial, spelling, meaning: "complete" → Completed) adds nothing to a field whose values the question lists. In the value index (`knowledge/values.link`) a partial match never uses a number word ("one" of "Network One") or a word under 4 letters of a synonym ("out" of "close out"; "uae" of the name "GCC - UAE" stays). Each word left out is listed in the answer's "Read as" lines ("“schedule” was not read as Risk category: it is a value of risks, which the question doesn't name"). A `with …` link phrase longer than 6 words is not one (`LINK_PHRASE_WORDS`).

**Negation (BUG-026, EXEC-175).** A value said after "not", "non", "no", "without", "excluding", "except" or "never" (articles and "in"/"from"/"on" may sit between) is left out instead of kept: `!=` for one value, `not_in` for several, with the chip "Region: not KSA" (`planner.negated_values`, `_add_filter`); values listed after it ("not in KSA or Jordan", "except Active or On Hold") are left out too. "other than", "apart from", "aside from" and "besides" are read as "except" first (`except_words`), so "other" is not the value Other. A measure asked the other way round ("projects not on hold", "with no budget code"; `planner._turned`) is its table's plain count (`stated.base_measure`) with the rule's values left out when the rule is listed values on one field with a dimension ("Read “not on hold” as Projects with current state not On Hold"); any other rule (a field being set, several fields) is not answered: "I can't count “no budget code” yet: “With a budget code” counts the opposite, and no certified measure counts the rest." A measure whose own name holds the negation ("not yet started", "Requests with no budget yet") is as asked. Negation words and "but" are planning words, never values. A "not" filter leaves out blank values too (SQL `NOT IN`), as Refine's do.

```json
{ "measures": [ { "ref": "measure:forecast_cost_total" }, { "ref": "measure:approved_budget_total" } ],
  "derived": [ { "key": "over_budget", "op": "percent_change", "of": "measure:forecast_cost_total", "to": "measure:approved_budget_total" } ],
  "breakdowns": [ { "ref": "dimension:cio" } ], "sort": [ { "by": "derived:over_budget", "dir": "desc" } ] }
```

The SQL compilers wrap the grouped query: `SELECT d.*, <derived>, CAST(x AS DOUBLE) / NULLIF(SUM(x) OVER (), 0) * 100 AS x__share FROM (…) AS tela_d`, so a share's total covers every group before a top N, and derived keys are quoted identifiers. MongoDB computes the same in `$addFields`, summing a share's total by `$group`/`$unwind`/`$replaceRoot` (no `$setWindowFields`, which the pipeline guard does not allow). Dataverse Web API mode reports derived figures and shares Unavailable (it already has one measure per query); TDS mode compiles as T-SQL. Result columns of derived figures and shares carry `of` (the measure column they come from) and the answer's `definitions` list them with their plain rule.

**Window operations (US-177).** The spec can carry one or more window operations that compile as an outer query over the grouped result, reusing the `FROM (q) AS tela_w` wrapping pattern:

- `top_n_per_group` — `ROW_NUMBER() OVER (PARTITION BY group ORDER BY measure) <= n` to keep the top/bottom N rows within each breakdown group. It needs a finer breakdown inside the group: "top 3 projects per programme" is planned as programme + `field:projects.name` (the entity's name field), so each programme holds one row per project.
- `running_total` — cumulative `SUM` along the time grain (`ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`). Without `per` it restarts in each group of the other breakdowns. Requires a time grain; rejects non-additive measures.
- `moving_avg` — `AVG` over a sliding window of 2–24 periods along the time grain, grouped like `running_total`; rejects non-additive measures (an average of averages or percentages misleads).
- `rank` — `RANK() OVER (ORDER BY measure)`, optionally partitioned per breakdown.
- `bucket` — `NTILE(k)` with k ∈ 2..10, labelled "Top quartile", "2nd quartile", … for k=4 or "Top N%", … for other k.
- `percentile` — P50, P85 or P90 of the rows in each `per` group, exact on every engine. DuckDB uses `quantile_cont`. PostgreSQL has no windowed `PERCENTILE_CONT` and MySQL has none at all, so PostgreSQL, MySQL and SQL Server compute the same value from positions: an inner layer (`tela_p`) adds each value's 0-based position among the group's non-null values (`ROW_NUMBER() - 1`) and the non-null count; the outer layer interpolates `v(floor h) + (h - floor h)·(v(ceil h) - v(floor h))` with `h = q·(n - 1)`, in `DOUBLE`. For a record-level value the planner adds the record breakdown ("median forecast cost by programme" → programme + project name, `per` programme), so rows stay one per project with the programme's percentile beside them.
- `having` — post-aggregate filter compiled as an outer `WHERE` on the measure column.

Window orders are the same on every engine: empty values last (a `CASE WHEN x IS NULL` key, since PostgreSQL sorts NULLs first when descending), then the other breakdowns as a tiebreak for `ROW_NUMBER`, `NTILE` and the time windows (`RANK` keeps ties). Window output columns are companions (they carry `of` pointing at their source measure) and new measure or dimension columns with labels and formats. MongoDB and Dataverse Web API use a DuckDB post-step: the grouped rows are fetched first, then a local DuckDB in-memory table evaluates the window SQL. The table takes each column's type from the values the source returned (MongoDB periods stay ISO text) and the source's row order is kept. Windows run over the scoped, fetched rows, after the source's own `limit`. Limits: `top_n_per_group.n` ≤ 50, `moving_avg.window` ≤ 24, `bucket.k` ≤ 10; these are enforced at parse time by the spec model.

### SQL compilation

`SqlCompiler(dialect)` builds a sqlglot expression tree (never string concatenation; values are bound parameters):

1. Root entity = measure's base entity. Joins only along `model_links` approved in this version.
2. When a breakdown sits on the "many" side of a link from the measure's root, the compiler pre-aggregates in a CTE (grain rule; S27 RULE-007…009 G1–G3, RULE-014). Count measures use `COUNT(DISTINCT root_pk)` when any 1:N join is present.
3. Measures with a currency dimension are grouped by currency unless the measure defines a conversion (RULE-023).
4. Personal fields (groupings, bands) compile to `CASE` expressions (RULE-012, RULE-013).
5. Output → `ScopeInjector` → `LIMIT` → dialect SQL text + params.

Illustrative output (PostgreSQL), for the spec above, scope "region IN ('KSA','GCC - UAE')":

```sql
SELECT p.region AS region, COUNT(*) AS overdue_go_lives
FROM (SELECT * FROM projects WHERE region IN (%(s0)s, %(s1)s)) AS p
WHERE p.current_state = 'Active'
  AND p.planned_deployment_date < %(as_of)s
  AND p.actual_deployment_date IS NULL
  AND p.priority IN (%(f0)s, %(f1)s)
GROUP BY p.region
ORDER BY overdue_go_lives DESC
LIMIT 10
```

(Measure body from RULE-028; illustrative.)

### Mongo compilation (ADR-014)

`MongoCompiler` emits an aggregation pipeline. Scope is always the **first** stage:

```json
[
  { "$match": { "region": { "$in": ["KSA", "GCC - UAE"] } } },
  { "$match": { "current_state": "Active", "actual_deployment_date": null,
                "planned_deployment_date": { "$lt": { "$date": "2026-10-21T00:00:00Z" } },
                "priority": { "$in": ["Critical", "High"] } } },
  { "$group": { "_id": "$region", "overdue_go_lives": { "$sum": 1 } } },
  { "$sort": { "overdue_go_lives": -1 } },
  { "$limit": 10 }
]
```

Links to other collections compile to `$lookup` only when approved; `$unwind` is followed by a regroup on the root `_id` to protect counts. Allowed stages: `$match $project $addFields $set $group $sort $limit $skip $lookup $unwind $count $facet $bucket $bucketAuto $replaceRoot`. Forbidden anywhere (including nested): `$out $merge $function $accumulator $where $planCacheSetFilter` and `$lookup` with a `pipeline` touching an unapproved collection.

### Custom SQL validator pipeline (FEAT-031, RULE-002, RULE-004, RULE-006, RULE-007)

Order matters; every step fails closed with a stable error code.

| Step | Check | Error code |
|---|---|---|
| 1 | Replace `:page_filters` with a sentinel identifier so the text parses | `sql_placeholder_misplaced` |
| 2 | `sqlglot.parse(sql, read=dialect)`; parse errors rejected | `sql_parse_error` |
| 3 | Exactly one statement | `sql_multiple_statements` |
| 4 | Root is `exp.Select`, `exp.Union` of selects, or `exp.With` ending in select | `sql_not_select` |
| 5 | Walk tree; reject `Insert Update Delete Merge Create Drop Alter TruncateTable Command Grant Transaction Set Use Into` (SELECT INTO), `Lock`, table-valued functions not allow-listed, functions on deny-list (`pg_sleep`, `sleep`, `benchmark`, `load_file`, `xp_cmdshell`, `openrowset`, `opendatasource`, `dblink`, `lo_import`, `pg_read_file`, `waitfor`) | `sql_forbidden_node` |
| 6 | All table references (outside CTE names) ∈ connection allow-list; no schema/database qualification except approved schemas; CTE names may not shadow governed tables (S27 RULE-004 edge case) | `sql_object_not_allowed` |
| 7 | Columns resolved against model fields; hidden fields for principal rejected | `sql_field_hidden` |
| 8 | Scope rewrite: each governed table → `(SELECT * FROM t WHERE <scope preds>) AS t`; re-parse and assert no unscoped governed table remains (S13 RULE-018 `assert_scoped`) | `scope_rewrite_failed` |
| 9 | Replace sentinel with compiled page-filter predicate (or `1=1` when none / widget not following page filters) | — |
| 10 | Wrap: `SELECT * FROM (<query>) q LIMIT 10001` (dialect-aware: `TOP` for T-SQL) to detect truncation at 10,000 | — |
| 11 | Compute `statement_sha256`; API signs `{sha, connection_id, principal, exp}` with HMAC (or Ed25519, S02) | — |
| 12 | Runner re-verifies signature and hash before execution; read-only session; timeout 20 s | `signature_invalid`, `query_timeout` |

### Scope injection

`ScopeInjector` takes `Scope = {entity -> [Predicate]}` resolved from `scope_policies` (DATA-009) and the principal's group claims. Predicates are typed (`field`, `op`, `values` or `attr` reference). Unresolvable attribute → refuse (`scope_unresolved`). Filters from the page are **ANDed** after scope, never replacing it (RULE-005). Same injector is used for widgets, chat answers, drill lists, exports, filter-value lookups and custom SQL — one code path (REC-004).

### Result post-processing

Runner returns typed columns and rows. Then: masking (RULE-020: secret-named columns → `[REDACTED]`, emails → `v***@domain`, phones last 4 digits — S02), field-hiding projection, truncation flag, `as_of` stamp (RULE-018). Rows go to the browser; to the LLM only aggregates under RULE-019.

### Caching (ADR-010)

Key = `sha256(tenant | connection | model_version | compiled_statement_hash | scope_hash | page_filter_hash | as_of_bucket)`. Value = result JSON in Redis, TTL default 15 min (`TELA_CACHE_TTL_SECONDS`), per-connection override. Never written to PostgreSQL; DATA-048 stores only key metadata for invalidation. Invalidation on model version approve, scope policy change, connection edit, manual refresh.

---
## Connector implementation

### Adapter contract (ADR-002)

```python
from typing import Protocol, Any
from dataclasses import dataclass

@dataclass(frozen=True)
class Limits:
    max_rows: int = 10_000          # RULE-006
    timeout_s: int = 20             # RULE-006
    max_bytes: int = 32 * 1024**2   # Proposed

@dataclass(frozen=True)
class NativeQuery:
    engine: str                     # postgres | mysql | sqlserver | mongodb | excel | dataverse_tds | dataverse_webapi
    text: str | None                # SQL text (SQL engines)
    params: dict[str, Any]
    pipeline: list[dict] | None     # MongoDB
    target: str | None              # collection / entity set
    statement_sha256: str

class ConnectorAdapter(Protocol):
    engine: str
    def test_connection(self) -> "TestResult": ...                 # reachability + auth + read-only check
    def verify_read_only(self) -> "ReadOnlyVerdict": ...           # RULE-001, fail closed
    def introspect(self, include_samples: bool = False) -> "Catalog": ...  # samples never persisted (S01 §4.1)
    def compile(self, spec: "CompiledPlan") -> NativeQuery: ...    # engine-specific final rendering
    def execute(self, q: NativeQuery, limits: Limits, scope_hash: str) -> "ResultSet": ...
    def cancel(self, execution_id: str) -> None: ...
```

`verify_read_only()` runs at test time, at runner start for each pooled connection, and is cached for at most 60 s (S13 RULE-019 used 60 s; S16 RISK-003 warns about the window, so every checkout **also** sets a session read-only flag where the engine supports it).

### Per-engine read-only enforcement

| Engine | Account the customer must provide | Session controls set by adapter | Verification in `verify_read_only()` | Timeout / cap mechanism |
|---|---|---|---|---|
| PostgreSQL | Role with `CONNECT`, `USAGE` on approved schemas, `SELECT` on approved tables/views only; no ownership | `SET default_transaction_read_only = on`; `SET statement_timeout = '20s'`; `SET idle_in_transaction_session_timeout` | Query `has_table_privilege` for INSERT/UPDATE/DELETE/TRUNCATE on approved objects (must be false); `rolsuper`, `rolcreaterole`, `rolcreatedb` false | `statement_timeout`; fetch `max_rows+1` |
| MySQL | User with `SELECT` on approved tables only | `SET SESSION TRANSACTION READ ONLY`; `SET SESSION max_execution_time = 20000` | Parse `SHOW GRANTS FOR CURRENT_USER()`; only `USAGE ON *.*` and `SELECT` on approved objects; reject `WITH GRANT OPTION` (S13 RULE-019) | `max_execution_time` (SELECT only) + client read timeout |
| SQL Server / Azure SQL | Contained user or login in `db_datareader` or explicit `SELECT` grants; no `db_datawriter`, `db_owner`, `db_ddladmin` | Connection string `ApplicationIntent=ReadOnly` when an availability-group readable secondary exists; `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`; `SET LOCK_TIMEOUT` | `SELECT * FROM fn_my_permissions(NULL,'DATABASE')` must not contain INSERT/UPDATE/DELETE/ALTER/CONTROL/EXECUTE | ODBC query timeout attribute 20 s; `TOP (max_rows+1)` |
| MongoDB | User with built-in `read` role on the database only | `readPreference=secondaryPreferred` (optional); `maxTimeMS=20000` on every `aggregate`; `allowDiskUse` false by default | `connectionStatus` with `showPrivileges`; reject any action outside `find`, `listCollections`, `listIndexes`, `collStats`, `dbStats` | `maxTimeMS`; `$limit max_rows+1` appended |
| Excel / CSV | No account. File stored in object storage (ADR-012) | DuckDB in-memory connection per execution; file mounted read-only; `SET enable_external_access=false` after load; `SET lock_configuration=true` | Inherent: no source to write to; validator still requires single SELECT | DuckDB interrupt after 20 s; `LIMIT max_rows+1` |
| Dataverse TDS | Entra app registration + Dataverse **application user** with a custom security role granting Read only on approved tables | TDS endpoint is read-only by design | Check the application user's role privileges via Web API (`RetrievePrincipalAccess` / role privileges) — no Create/Write/Delete/Append | Query timeout 20 s client side; endpoint has its own limits |
| Dataverse Web API | Same application user | GET only; adapter has no code path for POST/PATCH/DELETE (except `$batch` disabled) | Same as above | `httpx` timeout 20 s; `Prefer: odata.maxpagesize`; stop at max_rows |

### Dataverse specifics (ADR-013, RISK-004)

> **Verify against current Microsoft documentation (web lookup unavailable during authoring).** The points below are the authors' understanding and must be confirmed in the REC-006 spike.

- The TDS (SQL) endpoint is **read-only**, must be **enabled per environment** by an administrator (environment setting), and accepts a **subset of T-SQL** (no DML; limited functions; some column types not exposed).
- It listens on **port 1433** at `<org>.crm.dynamics.com` (regional host varies, e.g. `crm4`), authenticates with **Entra ID** tokens only.
- It enforces its own **query timeout and result-size limits**; large exports must page.
- Dataverse security roles still apply to the application user, which gives a second read-only layer.
- **Web API** (OData v4, `/api/data/v9.2/`) supports `$select`, `$filter`, `$expand`, `$apply` (aggregations) and **FetchXML** (`?fetchXml=`); it is subject to **service protection throttling** (HTTP 429 with `Retry-After`). Adapter must honour `Retry-After` and cap retries at 2.
- Table names: TDS exposes logical names (e.g. `tpg_project`); model introspection reads `EntityDefinitions` metadata from the Web API to get display names, option-set labels and relationships (`ManyToOneRelationships`). Choice (option-set) columns need label resolution — TDS exposes `<column>name` virtual columns for labels (verify).
- Compilation: `SqlCompiler("tsql")` with a **Dataverse profile** that removes unsupported constructs (e.g. some window functions, CTE support — verify) and falls back to Web API `$apply`/FetchXML for aggregations when TDS is disabled.
- Authentication code (Proposed):

```python
import msal, struct, pyodbc

def dataverse_tds_connect(org_host: str, tenant_id: str, client_id: str, secret: str) -> pyodbc.Connection:
    app = msal.ConfidentialClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant_id}",
                                             client_credential=secret)
    token = app.acquire_token_for_client(scopes=[f"https://{org_host}/.default"])["access_token"]
    raw = token.encode("utf-16-le")
    attrs = {1256: struct.pack(f"<I{len(raw)}s", len(raw), raw)}   # SQL_COPT_SS_ACCESS_TOKEN
    return pyodbc.connect(f"Driver={{ODBC Driver 18 for SQL Server}};Server={org_host},1433;Encrypt=yes;",
                          attrs_before=attrs, timeout=20)
```

(`secret` is read from the secrets store at call time and never logged.)

### MongoDB specifics (ADR-014, RISK-005)

- Introspection samples up to N documents per collection (default 200, in memory only) to infer a field map with types and presence ratio; nested paths flattened as `a.b.c`; arrays flagged. Samples are discarded after the draft is built (S01 §4.1).
- The model assistant asks explicit questions for polymorphic fields (S01 §4.1 "explicit questions").
- Custom SQL is **not offered** on MongoDB connections in Phase 1 (no SQL dialect). Proposed: Custom "pipeline" widgets are out of scope until certified-use demand exists (Q-012).

### Excel and CSV specifics (ADR-012, RISK-006)

- Upload via API-011 → virus/size check (max 50 MB Proposed) → object storage key `tenant/{t}/files/{file_id}/v{n}.xlsx` → worker introspects each sheet as a table (header row detection, type sniffing). Re-upload creates a new file version; model versions pin the file version.
- Execution: runner downloads to tmpfs, `CREATE VIEW sheet_x AS SELECT * FROM read_xlsx(...)` (or openpyxl → Arrow → DuckDB), then executes the compiled `duckdb` dialect SQL; tmp files removed after execution.
- Retention policy and classification at upload (REC-008).

### Connection pooling

Runner keeps per-connection pools (size 2–5, Proposed; configurable per connection to protect sources — RISK-008) with idle timeout 5 min and `pool_pre_ping`. Cold start (S30 RISK-007 reported 2–4 s) mitigated by warming pools for connections used in the last 24 h at runner start.

---

## Agent implementation

All agents live in `services/api/tela_api/ask/agents/` (COMP-003) and call the gateway through `GatewayClient.generate(task, messages, schema)`. Each agent:

- receives a **context pack** built by code (model excerpt, retrieved knowledge with citation IDs, conversation summary, principal's visible fields only);
- must return JSON matching a Pydantic schema (tool/function calling where the provider supports it; JSON mode otherwise);
- is retried **once** on schema violation with the validation error appended (S25 "retry malformed JSON once"); second failure → typed `agent_failed` event, user sees a plain message;
- never receives raw rows (RULE-019) except AG-08/AG-09 which may receive **aggregates** under the policy switch `TELA_LLM_ALLOW_AGGREGATES` (default off until Q-009 is answered).

| Agent | Input | Output schema (abridged) | Deterministic checks after output | Features |
|---|---|---|---|---|
| AG-01 Intent router | question, conversation summary, page context | `{intent: ask|refine|explain|build_report|edit_canvas|model_help|out_of_scope, target_widget_id?, confidence}` | Unknown intent → ask AG-02. With LLM review on (US-242): one `intent` call reviews the rules' intent and source; a different source asks which with both, a `data` question read as a definition or report asks which was meant, any other intent leaves the rules'; a write refusal is never sent | FEAT-018, 020, 022, 025 |
| AG-02 Clarifier | question, candidate interpretations | `{needs_clarification: bool, question, options:[{label, spec_patch}]}` | max 1 clarification per turn before answering (Proposed). With LLM review on (US-241): the model back-check runs on every plan (a mismatch caps the score at 0.5), and one `clarify` call words the reading question and labels; the readings never change, a label failing the wording check keeps the rules' label | FEAT-019 |
| AG-03 Query-spec planner | question, 5–10 linked candidates (US-181 schema linking: measures, dimensions, values, time words, scored; AG-03 picks candidates from the compact catalogue only on a miss), gold examples | `QuerySpec` or `{unsupported: reason, missing_data}` | reference precheck, `SpecValidator` and compiler (`QueryService.check`); a refusal goes back as typed errors ("dimension:x is not reachable from measure:y", "value 'KSA' not found; closest: …" from the person's scope), at most two repairs; every reason logged in `rag.plan_repairs`, top reasons at `GET /knowledge/plan-repairs` | FEAT-018, 020, 023, 064 |
| AG-04 Model assistant | catalog (names/types/keys; samples only if allowed), admin message | `ModelPatch {links[], measures[], dimensions[], renames[], descriptions[], questions[]}` | Links must reference existing columns; cardinality checked by count query on source (`COUNT(DISTINCT fk)` vs `COUNT(*)`) executed by runner. **Pack mode (US-237, task `pack_author`):** drafts a pack stage by stage (entities and fields, links, measures, dimensions, synonyms, knowledge) from the read schema with the template pack's items as worked examples and Tela's rules draft as the starting point; every item is held to its schema, the draft's names and Tela's own value profile (filter values are never invented), compiled and counted on the source, and only accepted items enter the draft (`packs/studio/author*.py`, API-074). DEC-27: values reach the model only with *send samples* on and a local provider | FEAT-007, 008 |
| AG-05 Report planner | request sentence, model, certified measures | `ReportPlan {headline_figures[], sections:[{title, widgets:[{spec, visual}]}], page_filters[], gaps[]}` | each spec validated; gaps preserved verbatim (S01 §4.5) | FEAT-023 |
| AG-06 Canvas editor | instruction, current layout JSON | `LayoutOps [{op: move|resize|remove|add_filter|set_visual|set_limit, target, args}]` | ops applied to a copy → diff preview; grid bounds 12 cols | FEAT-025 |
| AG-07 Visual advisor | result column kinds, cardinalities, question | `{visual, alternatives[], reason}` | RULE-008 decides; agent may only choose among suitable | FEAT-016, 021, 026 |
| AG-08 Narrator | result **summary statistics** computed in code | `{headline, sentences:[{text, fact_refs[]}]}` | every number in text must match a computed fact (RULE-022); otherwise template sentence used | FEAT-018, 022 |
| AG-09 Certification reviewer | two definitions (plain-language + compiled SQL), diff computed in code | `{explanation, risk_level}` | advisory only; decision is human | FEAT-035, 036 |
| AG-10 SQL drafter | question, allowed tables/columns | `{sql}` | draft only — user must press Check and run (validator pipeline) | FEAT-031 |

**Editable prompts and skillsets (ENH-030, US-264 / US-265).** Each agent's system prompts are listed in one catalogue (`tela_api/agents/catalog.py`: key, agent, task, where it is used; the default text is read from the constant in the agent's module, so the code keeps the defaults). Admins edit them on Admin → Platform → Agents (API-077); every save is a version in `ops.agent_prompt_versions` (no text = the default). Admins also give an agent skills, Markdown notes in `ops.agent_skills`. `run_agent` applies both to every call: each default found in the system message is swapped for the tenant's current version, and the agent's skills that are on follow under "Skills from your Admin", before the JSON-schema instruction, so neither can change the output schema, and every deterministic check above still runs. Template prompts (AG-04 pack author, DEC-27) are read-only. Overrides are cached 30 s per process; a failure to read them uses the defaults. The `llm_call` step of the run log names the edited prompts and skills used (`detail.tuned`).

**Orchestration.** `Orchestrator.step()` is a small state machine (states mirror the SSE events in [api_catalog.md](api_catalog.md#api-016-ask-stream)): `intent → (clarify)? → retrieval → plan → compile → validate → execute → viz → narrative → complete`. Each transition writes a `turn_events` row (DATA-041) and an SSE event. No agent can call another agent; only the orchestrator sequences them (S31 "17-agent state graph" is a cautionary scale reference — keep Phase 1 to ten narrow agents).

**Confidence (US-182, US-193).** Every plan has one score against the person's threshold (assume first 0.25, balanced 0.5, ask freely 0.75 since EXEC-171, the setting `TELA_ASK_CONFIDENCE_THRESHOLDS`; `ask/confidence.py`). Every cap came down 0.1 with the thresholds, so a flagged plan still asks. AG-03 plans: model confidence, vote agreement, value-link scores and the back-translation of the plan's plain words. Rules plans (`confidence.check_rules`, US-193): the same back-translation, plus the words of the question the plan doesn't use — a money word with a measure that is not an amount, "percentage" with a count (not a "15%" threshold, a ratio or a share of total), an open qualifier ("pending", "live") on a measure that counts every row, a "by …" breakdown or a number the plan leaves out — any of which caps the score at 0.4; coverage of the question's own words (under half, with model words left over, or under three quarters for a measure matched by meaning, or half its name unsaid when retrieval chose it) caps at 0.45; "open" (or "live", "pending" …) on a count or a sum of every row (Forecast cost: internal adds up every project) with no filter on open states caps at 0.4 too (EXEC-172: a CAPEX filter no longer hides it); a lead of under 0.1 over the next linked measure, when the measure isn't named outright, caps at 0.6 (0.45 for a meaning pick). Below the threshold the plan becomes one question with its readings (the measures of the kind asked for, then the next linked ones); one reading is answered with a note.

**Evaluation.** `tests/eval/` holds the gold set (FEAT-015, DATA-044): question → expected spec (or expected result for a fixed fixture DB) → pass/fail by measure, breakdown, filters, visual. Run nightly and on prompt/model change; threshold set by Q-017.

---

## RAG implementation

**Purpose.** Ground agents in the approved meaning of data and in the organisation's own conventions — "use RAG for knowledge to make each dashboard dynamic and enhance the chart" (stakeholder note on S31).

| Corpus (`knowledge_documents.kind`) | Source | Review gate | Used by |
|---|---|---|---|
| `model_definition` | Approved model versions: entity/field descriptions, measures' plain-language rules, synonyms | Auto-approved on model approval | AG-03, AG-05, AG-08 |
| `glossary` | Admin glossary / business notes | Admin approval | AG-02, AG-03 |
| `certified_measure` | Certified measures and fields with their rule text | Certified only | AG-03, AG-09 (look-alike) |
| `gold_example` | Question → spec pairs (DATA-044) | Admin/analyst approval | AG-03 few-shot |
| `chart_pattern` | Curated "question shape → best visual" patterns and PMO pack conventions | Admin approval | AG-07 (FEAT-016) |
| `uploaded_doc` | Admin-uploaded PDFs/DOCX/MD (PMO charter, KPI handbook) | Default `pending_review` → `approved` (S02 knowledge review lifecycle) | AG-02, AG-03, AG-05 |

**Pipeline (worker queue `index`).** Extract text (pypdf/pdfplumber for PDF, python-docx, markdown) → normalise → chunk (≈ 800 tokens, 120 overlap; model definitions chunked one entity per chunk) → leak/PII scan (same patterns as gateway) → embed via gateway `/embed` (1024-dim, default `mxbai-embed-large`) → upsert `knowledge_chunks` (DATA-043) with `tsv tsvector` and `embedding vector(1024)`.

**Index.** `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)` (Proposed defaults) plus GIN on `tsv`. Every query filters `tenant_id`, `status = 'approved'`, and optionally `connection_id`/`model_version_id`.

**Hybrid retrieval.** Top 30 by vector distance + top 30 by `ts_rank_cd`, merged with Reciprocal Rank Fusion (k = 60), top 8 returned with `chunk_id` citations. Set `hnsw.ef_search` = 40 per session.

**Intent-aware context (US-196).** Ask's planning retrieval keeps a quota per kind group from the question's intent profile — *data* (model 6, patterns 3, glossary 2, documents and answered questions 2), *definition* (glossary 4, model 4, reference 3, patterns 1; the definition panel) and *explain* for a "why" question (reference 4, model 6, glossary 2, patterns 1). AG-03's knowledge block is written by section — measure definitions, dimensions and fields, business rules and terms, patterns, answered questions, documents — in the profile's order, each with its share of the 6,000 characters; a section's unused share goes to the chunks that didn't fit, and a section's first chunk is cut to fit rather than dropped. The certified-measure chunks of the first five linked measures are always written first, so a long document or entity can't push a measure definition out.

**Fields + definitions planner — Plan B (US-254, ENH-028).** When the source's Ask answer mode is `fields` (US-256: `conn.connections.ask_mode`, set in Connections > Ask mode; it replaced the `TELA_ASK_MODE` setting) `Orchestrator.plan_for` hands the question to `_fields_plan`: the question made to stand alone (US-175), its frame (US-253: rules + one LLM `frame` call when the tenant has a provider; the shadow step is then not recorded again), then `ask/fields_planner.plan`. No catalogue matching, no AG-01/AG-03 reviews, no AG-02 back-check. Each slot is grounded on its own, with a score and a reason: subject → entity (its label or last word; a subject that is an approved term — "overdue go-lives" — gives its table and the term; "which region has the most open projects" makes region the grouping); each condition's words, longest piece first → an approved named condition (US-252 order of authority: the stored conditions, plus the rule of every certified count measure whose term has none stored yet — conditions are written when knowledge is indexed), else stored values (the value index, US-173; words it misses are looked up in the person's scoped value lists of the subject's fields), else a numeric field ("budget over 1m"; "on hold for more than 90 days" on a date → before today − 90 days); filler ("right now", "in total") is read past, the name of a field whose value was read is used up, any other word is refused by name; words the frame left unread are read the same way, and those still unread lower the score (× 0.6 each) and certification. Groupings → dimensions or text fields (a date grouping beside a grain or period is the time's date); time → the named date field, the entity's only date dimension, or the person's choice; the measure phrase → an aggregation and a numeric field ("forecast cost" is the label without its parenthesis), else a count. One template per shape on an ad-hoc measure (US-163): number; list_of_records (breakdowns by the record's name/title and number fields, `having count > 0` so records outside the conditions are not listed with 0, table visual); breakdown; ranking (sort by the measure, limit N or 10, records without the value filtered out); trend; comparison (previous period / last year, or values side by side); share (`share_of_total`; "what share are in UAE" → every region's share). Distribution, record detail and definition have no template: "Tela can't answer this kind of question yet". An ungrounded subject, grouping, time or measure field asks about that slot only (`kind: field`, key `fields:<slot>:<phrase>`, the choice kept in the conversation's memory); an ungrounded condition is refused naming its words. A term whose rule reads a calculated value (health, a ratio) answers through its certified measure alone and is refused when combined. Certified (W5) only when every slot was named, a synonym, an approved term, a stored value or a choice, and the measure part is a certified measure (the same count/total with no rule, or one whose rule is exactly the conditions); `complete.fields` carries the conditions used with their source (business-context sheet, approved document, certified measure, stored value, field), shown under "Conditions used" in the Ask side panel. The ad-hoc measure runs under the answer mode, not the person's free-hand grant (`QueryContext.fields`, set by the orchestrator and by the stream and turn read-back for a `planner = fields` turn); scope, hidden fields and limits apply as for every query. Offline on the PMO extract (US-250 gold, 622 examples, rules frame, no LLM): 246 right, 65 confident-wrong, 171 asked, 123 refused (today's rules planner: 447 right, 120 confident-wrong); lists 36/44 (rules 0/44), count twins 35/44 (rules 40/44). Most misses name a certified measure in its own words — Hybrid's job (US-255).

**Direct SQL mode (US-257, ENH-028; DEC-30).** A source whose Ask answer mode is `direct_sql` (SQL engines only, while `TELA_ASK_DIRECT_SQL` is on) skips planning: `Orchestrator._direct_sql` builds the question frame (`direct_frame`: the rules' reading and, when the tenant has a provider, the LLM's `frame` call, US-253; recorded as a "Question frame" step of the agent run, so the shadow step skips these turns), and `ask/direct_sql.answer` sends AG-10 (`sql_draft`) the approved schema, the frame's kind of answer (`SHAPE_RULES`) and the approved named conditions the question says, written as SQL. The draft goes through the Custom SQL pipeline (validator, allow-list, hidden fields, scope injection, `assert_scoped`, limits) plus a shape check on the statement's columns; one repair for a slip, none for a write, forbidden node, several statements, hidden field or scope failure; then it runs once on the scoped path. A follow-up carries the earlier question in AG-10's words ("This follows the earlier question: …"); when only the rules read it, a refinement ("only in UAE") is framed together with the earlier question so it keeps its kind of answer. The answer is never certified: `complete` carries `badge: custom_sql`, the SQL, `publishable: false`, `can_pin: false`; the Ask card shows "Custom SQL · not certified", the rows, the SQL and the publish note, has no Pin, and offers **Open in Custom SQL** (the SQL handed to `/sql/new`, not saved), whose widgets go on a published dashboard only once certified (RULE-009). A refusal shows the refused SQL.

**Gold runs per answer mode (US-259).** `POST /knowledge/gold-examples/evaluate` and `tela-admin gold-run` take `ask_mode` (`certified`, `fields`, `hybrid`, `direct_sql`): the run answers every example in that mode on copies of the sources (`gold.in_mode`; a source that can't — Direct SQL on MongoDB, a mode not built — gets an error row naming why), and the sources' own setting is unchanged. Without a mode the run follows each source's mode and is stamped with it when they share one (migration 0057 made `rag.eval_runs.ask_mode` nullable: null = sources in different modes, not any mode's score). Direct SQL has no spec, so `gold.evaluate_direct` grades its rows against the expected spec's (`scoring.direct_match`: each row as the set of its values, in order only when the spec sorts and limits; a list as the same records — the expected first column, rows counted 0 left out, is one of the answer's columns; a midnight timestamp is its date; an expected result with nothing in it never matches) and the frame's kind of answer against the example's; it can't be plan-only, its queries are logged as `evaluation`, and a wrong answer counts as confident-wrong (Direct SQL never asks). Connections > Ask shows each mode's latest run on the source, counting the source's questions in a run over every source too; the Gold screen has an "Answer mode to evaluate" picker and names the mode a run measured. Fields and Hybrid answers are ad-hoc measures that never equal a certified spec, so a figure is graded by its value and a list as the same records (`scoring.frame_match`, as Direct SQL); other shapes keep the two-row, non-zero rule. `test_gold_modes_offline_unit.py` runs Certified, Fields and Hybrid on the 622 US-250 examples offline (rules frame, no LLM): 447 / 240 / 244 right, 120 / 54 / 50 confident-wrong; Hybrid's originals (67) and paraphrases (10) are below Certified's (124, 37), so Certified stays the default.

**Clarification options (US-195).** When Ask asks which measure was meant — a tie on words, or a plan below the person's confidence threshold (US-182) — the options come from schema linking's candidates (words, meaning from the retrieved certified measures, qualifiers; US-192) and from the measures whose rule the question's date words describe (`ask/options.py`: a lateness word such as "past" or "missed" and a date named by its field or a dimension over it link the measures whose rule puts that date before the reporting date — "past their deployment date" → Overdue go-lives). Options are on the tables the question names (or the first candidate's) first; another table's only when named, or to make two options; generic totals last. A reading of an unnamed table is dropped while two others remain, so the plan's own reading may not be offered. The gold run records whether the expected measure was offered (`eval_results.clarify_right`).

**Reranker (US-174, decision US-197).** The gateway can call a local cross-encoder (TEI, `bge-reranker-v2-m3`) on the fused top 30, but it stays **off** (`TELA_RERANK_URL` unset): on re-indexed knowledge it lowered measure recall on the held-out paraphrases and took 45–160 s per question on the dev Mac (EXEC-106). Turn it on only after a measured gain on the US-190 sets.

**Curation queue from misses (US-198).** `POST /knowledge/curation/collect` reads Ask's misses once each (`rag.curation_evidence`, migration 0042): refused questions, clarification choices (value choices stay value synonyms, US-173), plans AG-03 repaired (`rag.plan_repairs`) and down-votes marked wrong or "not what I asked". Each becomes a proposal in `rag.curation_proposals`, one per connection, kind and wording, with the questions behind it: a **measure synonym** for the words the model doesn't know ("stalled projects"; the Admin picks the measure among those Ask considered), a **glossary term** for an unknown abbreviation in capitals, or a **gold example** (question → spec) from a repair or a down-vote, whose spec the Admin corrects first. Held-out questions (the pack's `held_out` examples, gold examples tagged `paraphrase` or imported held out) are never proposed (W2). Nothing applies until an Admin approves it in Admin → Knowledge → From misses, each through its own gate: a synonym is written to the model and approved as its next version (`model.approve`, shared with free-hand certification in `model/change.py`), so linking reads it by word and, after re-indexing, by meaning; a term goes to the connection's approved "Glossary from Ask misses" document; a gold example is saved through the gold endpoint and the down-votes behind it are marked fixed. A dismissed wording is not proposed again.

**Named conditions and order of authority (US-252, DEC-29).** `rag.named_conditions` (migration 0055) stores each business term once per connection and model version as a runnable condition: term, `term_key`, synonyms, entity, `condition` in the measure-condition format (`tela_query_spec.expr.Condition` JSON), `source` (`document` — an approved US-203 definition; `definition_sheet` — a US-251 spreadsheet row; `pack_measure` — a certified count measure's own `where`, approved with the model on indexing, never over a definition's condition of the same term), `status` (proposed, approved, retired), `measure_ref`, `conflict`, `prefer`. Approving, rejecting or retiring a definition keeps its condition in step (`conditions.from_definition`); re-reading a document retires the conditions of definitions no longer approved (`settle`); a new current model version carries them after the definitions (`conditions.carry_forward`), keeping the Admin's choice while the rule is unchanged, a condition whose field the model lost waiting as `proposed` with its gap. **Order of authority** (`conditions.authority`, read by every mode): approved condition → certified measure → document text (an LLM hint only). A condition and the certified measure of the same term that select different rows are a **conflict** (`conflict`: both rules and a row count each, from the approval's trial counts or `POST /knowledge/conditions/{id}/check`, under the Admin's scope); until the Admin chooses (`POST /knowledge/conditions/{id}/choose` `{use: condition|measure}`, audit `knowledge.condition_chosen`) Ask counts by the measure and notes the conflict (US-205 badge `definition_conflict`). In Certified mode the US-204 route reads its definitions through their conditions (`apply_to_definitions`): a held conflict is not applied, "use the measure" drops it, "use the condition" applies it as before. Definitions approved before migration 0055 were backfilled with `prefer = 'condition'`, keeping their US-204 behaviour. A condition is offered to a person only when they can see every field it reads; counts run through the query service under their row scope. Admin → Knowledge › Conditions lists them.

**Business-context spreadsheet (US-251).** Admin → Knowledge also takes one `.xlsx` per source (magic `PK\x03\x04`, ≤ `knowledge_max_upload_mb`, a connection required) in the template's single sheet with the fixed header `term | meaning | table | field | operator | values | notes` (`GET /knowledge/business-context/template`; `tela_common.business_sheet`, the `sheet` extra = openpyxl). The header is checked on upload (400 `reason: bad_template`, nothing stored). The worker writes one chunk per row (`metadata.row`, `metadata.sheet` = the cells), so rows are scanned (an instruction-like row flags the document and approval needs the acknowledgement, as before), embedded and reviewed like any passage. On indexing, `definitions.propose` reads a sheet's rows with `interpret_row` instead of the prose patterns: table by key, label or source table; field by key, label or source column; operator `= ≠ in not_in blank not_blank < > between before_today after_today` (`=`/`≠` read as in/not in); values split on `;` (or lines, or commas); listed values matched to their stored spelling through the value index, an unknown one kept as a gap with the closest stored value ("Current state has no value “Actve” (closest: “Active”)"); a number or date compared only on a number or date field. A row that fails stays as text with its reason; a row with no rule is text only. Approval adds the data check (a listed value no row has) and the approver's view (a table out of their scope or a hidden field keeps it as text). The definitions' `rule` takes the wider operators (`definitions.condition` → `InSet`, `NullCheck`, `Compare` with a literal or `today`, `All` for between); the US-204 route still applies only listed-value rules. An approved row becomes a `definition_sheet` named condition (US-252). Markdown, text, PDF and Word keep the prose path.

**Grounding checks.** When an agent cites knowledge, cited IDs must be a subset of supplied IDs; coverage of claims with citations ≥ 0.70 for explanatory text (S02 `validateCitations` threshold) or the explanation is replaced by a deterministic one built from definitions (S02 deterministic fallback).

**Prompt-injection defence (RISK-014, REC-014).** Retrieved text is wrapped as quoted data with an instruction hierarchy; documents containing instruction-like patterns ("ignore previous instructions", tool-call syntax) are flagged at indexing and held in review.

---

## LLM gateway implementation

`services/llm-gateway` exposes internal `POST /generate` and `POST /embed` (mTLS or signed service token).

1. **Policy check**: task type allowed for tenant; provider/model allowed (Q-009); per-tenant monthly token budget (`TELA_LLM_BUDGET_TOKENS_MONTHLY`) and per-user rate (Redis token bucket).
2. **Masking**: replace user names/emails in prompt with placeholders where they appear in context packs; strip column sample values unless the connection setting `allow_samples_to_llm=true`.
3. **Pre-send leak scan (RULE-021)**: regexes for API keys (`sk-[A-Za-z0-9]{20,}`, `sk-ant-[A-Za-z0-9\-_]{20,}`, `AIza[0-9A-Za-z\-_]{35}`), connection strings (`(postgres(ql)?|mysql|mongodb(\+srv)?|sqlserver)://[^:\s]+:[^@\s]+@`), private keys (`-----BEGIN [A-Z ]*PRIVATE KEY-----`), emails, phone numbers, Luhn-valid card numbers (S02, S27). Match → `LeakBlocked`, no outbound call.
4. **Provider adapter**: Azure OpenAI, Anthropic, Google Gemini, Ollama. Common interface `generate(messages, json_schema, max_tokens, temperature=0) -> (text, usage)`; timeouts 30 s (S28), 1 retry on 5xx/429 with jitter.
5. **Output safety**: reject output containing secrets patterns or "ignore previous instructions" (S02 `safe(result)`).
   **Schema check**: when the caller sends a JSON schema, one surrounding Markdown code fence (```` ```json … ``` ````) is removed before the answer is parsed and checked (BUG-027: hosted providers outside `llm_constrained_providers`, e.g. Gemini, fence their JSON); the returned `text` is the unfenced JSON, the run-log trace keeps the reply as it came. Prose around a fence is not stripped and still fails.
6. **Audit (DATA-046)**: metadata only — tenant, user, task, provider, model, prompt hash, token counts, latency, verdict. **No prompt or answer text in logs** (S02 `logInvocation`).

---

## API implementation

Endpoint contracts are owned by [api_catalog.md](api_catalog.md#endpoint-catalog). Implementation conventions:

- Base path `/api/v1`; JSON; errors as `{"error": {"code", "message", "request_id", "details"}}`; RFC 9457 `application/problem+json` optional.
- Every router dependency chain: `request_id` → `principal = Depends(get_principal)` → `require(permission_key)` → `tenant_context(session)`.
- Pagination: cursor-based (`?cursor=&limit=`, max 100).
- Idempotency: `Idempotency-Key` header honoured on POST that create things (publish, export, pin, upload); stored 24 h in Redis.
- Optimistic concurrency: `ETag`/`If-Match` on report layout, dashboard draft, model draft (`version` column).
- SSE: `GET /api/v1/ask/turns/{turn_id}/stream` with `text/event-stream`, heartbeat every 15 s, `Last-Event-ID` resume from `turn_events`.
- Rate limits (Proposed): ask 30/min/user, custom SQL run 20/min/user, export 10/hour/user; 429 with `Retry-After`.

---

## Authentication and authorization implementation

**Login (FEAT-001, ADR-003).** SPA uses OIDC Authorization Code + PKCE against Keycloak realm (per tenant in SaaS, or one realm customer-hosted — S03 §4); Keycloak brokers to Microsoft Entra ID. Access token (JWT, 5–15 min) in memory; refresh via Keycloak refresh token with rotation. No tokens in `localStorage`.

**JWT validation (`identity/jwt.py`).** Verify signature with JWKS from the **configured** issuer only (never the token's `iss` to fetch keys — S03 §10), `aud = tela-api`, `exp/nbf`, `typ` = access token, allowed algs `RS256/ES256`. Map `sub` + IdP alias to `external_identities` (DATA-003); never by email alone (S03 §5.2).

**Groups.** Keycloak mapper forwards Entra group object IDs (`groups` claim). Entra "group overage" (too many groups) → Keycloak/IdP must call Graph; Proposed: SCIM provisioning (FEAT-004) keeps `idp_group_members` current so the API does not depend on token size (RISK-007).

**Principal and permissions.** `Principal{user_id, tenant_id, roles[], permissions[], groups[]}` built from `group_role_mappings` (DATA-008) → `roles` (DATA-006) → `role_permissions` (DATA-007). Permission keys (Proposed):

| Key | Admin | Creator | Viewer |
|---|---|---|---|
| `dashboards.view`, `definitions.view`, `drill.use`, `ask.use` | ✓ | ✓ | ✓ |
| `export.use` | ✓ | ✓ | ✓ (subject to Q-008 policy) |
| `widgets.create`, `reports.create`, `dashboards.create`, `fields.create`, `share.create`, `certification.request` | ✓ | ✓ | — |
| `customsql.use` | ✓ | ✓ | — |
| `connections.manage`, `model.edit`, `model.approve`, `starter.manage`, `mappings.manage`, `policies.manage` | ✓ | — | — |
| `certification.decide`, `dashboards.publish` | ✓ | — (GAP-006) | — |
| `audit.read`, `settings.manage`, `knowledge.manage` | ✓ | — | — |

**Scope resolution (FEAT-003).** `ScopeResolver.for_principal(p, connection_id)` returns entity predicates from `scope_policies` matched by role/group; multiple policies for the same entity are **OR-combined within the principal's grants** (union of what they may see) and then ANDed with filters; no policy for a governed entity = **deny** (fail closed). Field hiding from `field_policies` (DATA-010) removes fields from the model excerpt sent to agents, from spec validation and from result projection.

**Tenant isolation (NFR-023).** Every request opens a transaction and runs `SET LOCAL app.tenant_id = :tid`; PostgreSQL RLS policies `USING (tenant_id = current_setting('app.tenant_id')::uuid)` on all tenant tables (S03 §4 defence in depth). The app DB role is not table owner and has `NOBYPASSRLS`. Connection pooler in transaction mode is compatible because `SET LOCAL` is transaction-scoped.

---

## Business logic implementation

Where each RULE will live (Proposed; details in [business_rules.md](business_rules.md)):

| Rules | Location |
|---|---|
| RULE-001 | `connector-runner/adapters/*.verify_read_only` |
| RULE-002, RULE-003, RULE-007 | `api/query/custom_sql.py` (`CustomSqlValidator`), `api/query/spec.py` |
| RULE-004, RULE-005 | `api/query/scope.py` (`ScopeInjector`) |
| RULE-006 | `api/query/limits.py` + runner `Limits` |
| RULE-008 | `packages/chart-spec/suitability.py` (shared with web via generated table) |
| RULE-009, RULE-024 | `api/content/publish.py` (`PrePublishChecks`) |
| RULE-010, RULE-011 | `api/governance/queue.py`, `api/governance/lookalike.py` |
| RULE-012, RULE-013, RULE-014 | `api/query/compiler/*` |
| RULE-015 | `api/model/versions.py` + `api/content/widgets.py` |
| RULE-016 | `api/content/versions.py` |
| RULE-017 | `api/admin/starter.py` |
| RULE-018 | `api/query/asof.py` |
| RULE-019, RULE-021 | `llm-gateway/masking.py`, `scan.py`; context builders in `api/ask/context.py` |
| RULE-020 | `connector-runner/guards.py` |
| RULE-022 | `api/ask/agents/narrator.py` (fact check) |
| RULE-023 | `api/query/compiler/currency.py` |
| RULE-025 | `api/content/drill.py` |
| RULE-026…RULE-039 | **Data, not code**: `packs/pmo/measures/*.yaml` compiled by the query engine (FEAT-051) |

---

## Data access layer

- SQLAlchemy 2 async engine to COMP-011 (`asyncpg` driver), pool size 10/overflow 10 per API replica (Proposed); PgBouncer (transaction mode) optional in Helm.
- Unit of work per request; outbox rows (DATA-050) written in the same transaction as state changes; relay task publishes to Celery (fixes S31 "outbox written but never drained").
- Repositories return domain models; no ORM objects leak to routers.
- JSONB for specs/layouts validated by Pydantic before write; `spec_version` field enables migrations of stored specs.
- pgvector via `pgvector.sqlalchemy.Vector(1024)`.
- Structure and indexes: see [db_scheme.md](db_scheme.md#table-catalog).

---

## Background jobs and events

| Task | Queue | Trigger | Schedule | Retries | Failure handling |
|---|---|---|---|---|---|
| `introspect_connection` | introspect | API-010 / connection created | on demand | 2, backoff 30 s | status `failed` + notification to admin |
| `index_knowledge` | index | model approved, doc uploaded/approved, gold example saved | on demand | 3 | chunk-level errors recorded; doc stays `pending_review` |
| `export_pdf`, `export_xlsx` | export | API-046 | on demand | 1 | job `failed`, user notified (DATA-051) |
| `warm_cache` | maintenance | after publish; daily | Proposed 05:30 tenant TZ (Q-001) | 0 | skip |
| `pack_update_check` | maintenance | beat | daily | 2 | notification only |
| `scim_reconcile` | maintenance | beat | hourly | 2 | alert on drift |
| `outbox_relay` | default | beat | every 2 s | ∞ (idempotent) | dead-letter table after 10 attempts with admin view |
| `retention_sweep` | maintenance | beat | daily | 1 | audit event |
| `eval_gold_set` | maintenance | beat / prompt change | nightly | 0 | report to admin |

All tasks idempotent (keyed by entity ID + version). Celery `acks_late=True`, `task_reject_on_worker_lost=True`, visibility timeout > longest task.

---

## Third-party integrations at code level

| Integration | Client | Timeouts / retries | Error mapping | API ID |
|---|---|---|---|---|
| Keycloak / Entra | browser `oidc-client-ts`; API `pyjwt` JWKS cache 10 min | JWKS fetch 5 s, 2 retries | 401 `token_invalid` | API-058 |
| Microsoft Graph (optional) | `msgraph-sdk` or `httpx` | 10 s, honour 429 | mapping job warning | API-059 |
| LLM providers | adapters in COMP-006 | 30 s, 1 retry | `planner_unavailable`, `llm_timeout` | API-060 |
| Embeddings | Ollama HTTP / provider SDK | 20 s, 2 retries | indexing retry | API-061 |
| Source DBs | runner adapters | 20 s statement | `source_unreachable`, `query_timeout`, `read_only_violation` | API-062…067 |
| Object storage | `boto3` / `azure-storage-blob` | 30 s, 3 retries | `storage_unavailable` | API-068 |
| Secrets | `hvac` (OpenBao/Vault) or Azure Key Vault SDK | 5 s, 2 retries; lease renew | `secrets_unavailable` (fail closed) | API-069 |

---

## Configuration and environment variables

No secret values appear here. Secrets are injected from the secrets store or orchestrator secrets as files (`*_FILE` convention).

| Variable | Service | Purpose | Default (Proposed) | Required in |
|---|---|---|---|---|
| `TELA_ENV` | all | `local`, `dev`, `uat`, `prod` | `local` | all |
| `TELA_DATABASE_URL_FILE` | api, worker | Path to file holding metadata DB DSN | — | all |
| `TELA_DB_POOL_SIZE` | api, worker | SQLAlchemy pool size | `10` | optional |
| `TELA_REDIS_URL_FILE` | api, worker, runner, gateway | Redis DSN file | — | all |
| `TELA_OIDC_ISSUER` | api, web | Keycloak realm issuer URL | — | all |
| `TELA_OIDC_AUDIENCE` | api | Expected `aud` | `tela-api` | all |
| `TELA_OIDC_CLIENT_ID` | web | Public SPA client id | `tela-web` | all |
| `TELA_RUNNER_URL` | api, worker | Connector runner base URL (internal) | `http://connector-runner:8100` | all |
| `TELA_GATEWAY_URL` | api, worker | LLM gateway base URL (internal) | `http://llm-gateway:8200` | all |
| `TELA_SERVICE_SIGNING_KEY_FILE` | api, runner | Key used to sign execution requests | — | all |
| `TELA_SECRETS_BACKEND` | runner, gateway | `openbao`, `vault`, `azure_keyvault`, `file` (dev only) | `openbao` | all |
| `TELA_SECRETS_ADDR` | runner, gateway | Secrets store address | — | dev/uat/prod |
| `TELA_OBJECT_STORE_ENDPOINT` | api, worker, runner | S3-compatible endpoint | — | all |
| `TELA_OBJECT_STORE_BUCKET` | api, worker, runner | Bucket for files/exports | `tela` | all |
| `TELA_QUERY_MAX_ROWS` | api, runner | Row cap (RULE-006) | `10000` | optional |
| `TELA_QUERY_TIMEOUT_SECONDS` | api, runner | Statement timeout (RULE-006) | `20` | optional |
| `TELA_DRILL_MAX_ROWS` | api | Drill list cap (RULE-025) | `1000` | optional |
| `TELA_CACHE_TTL_SECONDS` | api | Result cache TTL (ADR-010) | `900` | optional |
| `TELA_READONLY_CHECK_TTL_SECONDS` | runner | Read-only verdict cache | `60` | optional |
| `TELA_LLM_PROVIDER` | gateway | `azure_openai`, `anthropic`, `gemini`, `ollama` | none (LLM disabled) | when LLM on |
| `TELA_LLM_MODEL_PLANNER` / `TELA_LLM_MODEL_NARRATOR` | gateway | Model per task | — | when LLM on |
| `TELA_LLM_ALLOW_AGGREGATES` | api, gateway | Allow aggregate results to LLM (RULE-019) | `false` | optional |
| `TELA_LLM_BUDGET_TOKENS_MONTHLY` | gateway | Per-tenant budget | `0` (=unlimited, set before prod) | prod |
| `TELA_EMBED_PROVIDER` / `TELA_EMBED_MODEL` / `TELA_EMBED_DIM` | gateway | Embedding config | `ollama` / `mxbai-embed-large` / `1024` | all |
| `TELA_ASK_CONCURRENCY_PER_USER` | api | Concurrent turns per user | `2` | optional |
| `TELA_ASK_CONCURRENCY_PER_INSTANCE` | api | Concurrent turns per replica | `8` | optional |
| `TELA_UPLOAD_MAX_MB` | api | Excel/CSV max size | `50` | optional |
| `TELA_OTEL_EXPORTER_OTLP_ENDPOINT` | all | Traces/metrics endpoint | `http://otel-collector:4317` | dev/uat/prod |
| `TELA_LOG_LEVEL` | all | Log level | `INFO` | optional |
| `TELA_CORS_ORIGINS` | api | Allowed origins | web origin only | all |

---

## Error handling, logging and observability

- **Error codes** are stable strings (catalogue in `packages/tela-common/errors.py`): `token_invalid`, `forbidden`, `scope_unresolved`, `scope_rewrite_failed`, `sql_*` (see validator table), `read_only_violation`, `query_timeout`, `row_cap_reached` (warning, not error), `source_unreachable`, `planner_unavailable`, `leak_blocked`, `agent_failed`, `uncertified_widget`, `precheck_failed`, `rate_limited`. User-facing messages live in [function_document.md](function_document.md).
- **Logs**: JSON via structlog; fields `ts, level, service, request_id, trace_id, tenant_id, user_id(hashed), event, duration_ms, code`. Never: SQL parameters values, result rows, prompts, tokens, passwords (S12 NFR-012, S02).
- **Traces**: OTel spans across web → api → gateway/runner → source; span attributes include `connection.engine`, `query.sha256`, `rows`, `cache.hit`.
- **Metrics** (Prometheus): `tela_turn_duration_seconds`, `tela_query_duration_seconds{engine}`, `tela_validation_rejections_total{code}`, `tela_scope_denials_total`, `tela_llm_tokens_total{provider,model}`, `tela_cache_hit_ratio`, `tela_export_jobs_total{status}`, `tela_outbox_lag_seconds`.
- **Health**: every container exposes `/healthz` (liveness, no dependencies) and `/readyz` (DB, Redis, secrets reachable). Fatal start-up errors exit non-zero (S31 P0 "silent fatal exits").

---

## Security implementation

| Control | Implementation (Proposed) | Reference |
|---|---|---|
| Input validation | Pydantic on every body; spec/layout JSON schema; upload MIME + magic bytes | — |
| Output encoding | React escaping; no `dangerouslySetInnerHTML`; markdown text notes rendered with sanitiser (DOMPurify) | — |
| SQL safety | spec compilation with bound params; custom SQL validator pipeline; runner re-verifies signed hash | S02, S13, S27 |
| Read-only | per-engine table above; two layers (code + source) | S26 NFR-004 |
| Service-to-service | API→runner and API→gateway: mTLS in Helm; signed short-lived service JWT in Compose | S31 signed task envelopes |
| Execution envelope | `{execution_id, tenant, connection, statement_sha256, scope_hash, limits, exp}` signed (Ed25519 key per environment; **no all-zero placeholder keys**: start-up refuses empty/zero keys — S31 P0) | S02, S31 |
| Secrets | Connection passwords written once to secrets store via API → never returned; `connections.secret_ref` only | S30 RISK-003 lesson |
| Encryption | TLS on all hops including PostgreSQL (`sslmode=verify-full`), Redis (TLS + AUTH), Keycloak production mode (S31 transport gaps); disk encryption by platform; object storage SSE | S31 |
| Containers | non-root UID, `read_only: true` root fs + tmpfs, `no-new-privileges`, dropped capabilities, distroless/slim bases | S31 |
| Network | internal services not published on `0.0.0.0` (S31 P1); only edge publishes 443 | S31 |
| Dependencies | `pip-audit`, `pnpm audit`, Trivy image scan, SBOM (Syft) in CI | — |
| Admin actions | audit events (DATA-045) for connection, model approve, certification, publish, mapping, policy, export, settings | S01 §6 |
| Production-intent blocking | runner rejects any non-read operation regardless of origin (S02 §4 pattern) | S02 |

---

## Build and deployment pipeline

### Docker images (ADR-005)

| Image | Base (Proposed) | Port | Healthcheck | Restart | Notes |
|---|---|---|---|---|---|
| `tela-web` | `nginx:alpine` (unprivileged variant) | 8080 | `GET /healthz` | `unless-stopped` | static build, CSP headers |
| `tela-api` | `python:3.13-slim` | 8000 | `/healthz`, `/readyz` | `unless-stopped` | Gunicorn + Uvicorn workers; Alembic migrations run as separate job |
| `tela-migrate` | same as api | — | exit code | `no` | `alembic upgrade head`; must finish before api starts |
| `tela-connector-runner` | `python:3.13-slim` + ODBC Driver 18 | 8100 (internal) | `/healthz` | `unless-stopped` | egress restricted to source hosts |
| `tela-llm-gateway` | `python:3.13-slim` | 8200 (internal) | `/healthz` | `unless-stopped` | egress restricted to LLM hosts |
| `tela-worker` | api image + Playwright Chromium | — | `celery inspect ping` | `unless-stopped` | queues default/index/export/introspect/maintenance |
| `tela-beat` | api image | — | process | `unless-stopped` | single replica |
| third-party | `pgvector/pgvector:pg16`, `redis:7`, `quay.io/keycloak/keycloak`, `openbao/openbao`, `minio/minio`, `ollama/ollama`, `otel/opentelemetry-collector`, `traefik` | — | vendor | `unless-stopped` | pin by digest |

### Compose profiles (Proposed)

- `core`: web, api, migrate, runner, gateway, worker, beat, postgres, redis, keycloak, openbao, minio, traefik
- `ai-local`: ollama (embeddings, optional local LLM)
- `observability`: otel-collector, prometheus, grafana, loki
- `dev`: mailpit, sample source DBs (postgres, mysql, mssql, mongo) seeded with synthetic PMO data (never client rows — S05)

### CI (GitHub Actions or Azure DevOps — Proposed)

1. Lint & format: ruff, eslint, prettier.
2. Types: mypy (strict for `packages/`), `tsc --noEmit`, OpenAPI → `schema.d.ts` regenerated and diffed (fails if stale).
3. Unit tests: pytest (with coverage gate, Proposed 80 % for `query/`, `identity/`, runner guards), vitest.
4. Security suites: validator fuzzing, scope isolation, gateway leak tests (TEST-071…TEST-080).
5. Integration: compose `dev` profile in CI; connector tests against live containers for Postgres, MySQL, SQL Server, MongoDB, DuckDB (S30 RISK-006 lesson: no mock-only engines). Dataverse tests run only in a nightly job with a sandbox environment (secrets from CI vault).
6. Migrations: `alembic upgrade head` on empty DB + downgrade one step + upgrade.
7. E2E: Playwright on J1–J11 happy paths.
8. Build images (multi-arch amd64/arm64), SBOM, Trivy scan (fail on critical), sign (cosign).
9. Deploy: dev auto; uat manual approval; prod manual approval with change record.

### Release and rollback

Semantic versioning; DB migrations **expand → migrate → contract** across two releases so rollback of app image is always possible without DB downgrade. Helm `rollback` for app; PITR for metadata DB (GAP-014).

---

## Local development setup

All commands **Proposed** (no repository exists yet).

```bash
# prerequisites: Docker Desktop / Engine, Node 22+, pnpm, Python 3.13, uv
git clone <repo> tela && cd tela
cp deploy/compose/.env.example deploy/compose/.env          # fill local-only values
docker compose -f deploy/compose/compose.yaml --profile core --profile dev --profile ai-local up -d
uv sync && uv run alembic upgrade head
uv run uvicorn tela_api.main:create_app --factory --reload --port 8000
pnpm -C apps/web install && pnpm -C apps/web dev            # http://localhost:5173
uv run pytest -q && pnpm -C apps/web test
```

Seed: `uv run tela-seed --pack pmo --source dev-postgres` (Proposed CLI) loads synthetic PMO tables shaped like the S15 reference schema (`projects`, `project_requests`).

---

## Testing setup

| Layer | Framework | Location | Notes |
|---|---|---|---|
| Python unit | pytest, hypothesis | `services/*/tests/unit` | pure functions: compiler, validator, scope injector, suitability |
| Python integration | pytest + testcontainers / compose | `tests/integration` | real engines |
| Security | pytest + curated corpora | `tests/security` | SQL injection corpus, scope bypass, leak patterns |
| Frontend | vitest + RTL | `apps/web/src/**/*.test.tsx` | |
| E2E | Playwright | `apps/web/e2e` | J1–J11 |
| Eval | custom runner | `tests/eval` | gold set (FEAT-015) |
| Load | k6 or Locust | `tests/load` | NFR-011 once targets set (Q-010) |

Functional acceptance tests TEST-001…TEST-070 are in [function_document.md](function_document.md#acceptance-tests). Technical tests:

| ID | Area | Given / When / Then |
|---|---|---|
| TEST-071 | Validator | Given 500+ injection strings (stacked queries, comments, `UNION` to system tables, `SELECT INTO`, `pg_sleep`, `WAITFOR`, unicode homoglyphs) / When validated for each dialect / Then all rejected with a `sql_*` code and none reach the runner |
| TEST-072 | Validator | Given CTE named like a governed table / When validated / Then `scope_rewrite_failed` |
| TEST-073 | Scope | Given two principals with disjoint region scope / When the same widget runs / Then result sets are disjoint and cache keys differ |
| TEST-074 | Scope | Given a filter value outside scope / When applied / Then result empty, never widened |
| TEST-075 | Scope | Given drill, export, filter-values, chat follow-up and custom SQL / When run by restricted user / Then all apply the same predicates (single code path) |
| TEST-076 | Scope | Given entity without any scope policy / When queried by non-admin / Then denied |
| TEST-077 | Read-only | Given a Postgres account with INSERT grant / When `verify_read_only` / Then connection marked unsafe and execution refused |
| TEST-078 | Read-only | Same for MySQL (`SHOW GRANTS`), SQL Server (`fn_my_permissions`), MongoDB (`connectionStatus`) |
| TEST-079 | Gateway | Given prompt containing a connection string or API key / When `/generate` / Then `leak_blocked`, no outbound request (asserted with network mock) |
| TEST-080 | Gateway | Given any agent call / When audited / Then log has no prompt or completion text |
| TEST-081 | Compiler | Given count measure with 1:N breakdown / When compiled / Then `COUNT(DISTINCT root_pk)` or pre-aggregated CTE |
| TEST-082 | Compiler | Given amount measure with currency dimension and no conversion / When compiled without currency breakdown / Then grouped by currency or rejected |
| TEST-083 | Compiler | Given band at exact edge value 1,000,000 / When compiled / Then falls in 1M–5M band |
| TEST-084 | Mongo | Given pipeline / When compiled / Then first stage is scope `$match` and no forbidden stage present |
| TEST-085 | Excel | Given uploaded workbook with 3 sheets / When introspected / Then 3 entities and file version pinned |
| TEST-086 | Dataverse | Given sandbox env with TDS enabled / When introspected and a measure run / Then results match Web API `$apply` aggregate |
| TEST-087 | Dataverse | Given Web API 429 / When adapter retries / Then honours `Retry-After`, max 2 retries |
| TEST-088 | Limits | Given query returning 12,000 rows / When executed / Then 10,000 rows + `truncated=true` |
| TEST-089 | Limits | Given query exceeding 20 s / When executed / Then cancelled at source and `query_timeout` |
| TEST-090 | Signing | Given tampered SQL text after validation / When runner receives it / Then `signature_invalid` |
| TEST-091 | Tenant | Given two tenants / When API queries with tenant A context / Then RLS returns zero tenant-B rows even with a crafted ID |
| TEST-092 | RAG | Given gold questions / When retrieval runs / Then recall@8 ≥ threshold (set with Q-017) |
| TEST-093 | RAG | Given document with injection text / When indexed / Then held in review and excluded from retrieval |
| TEST-094 | RAG | Given narrator output citing unknown chunk ID / When checked / Then replaced with deterministic text |
| TEST-095 | Narrator | Given result stats / When narrative contains a number not in facts / Then rejected (RULE-022) |
| TEST-096 | Containers | Given compose up / When a dependency is down / Then `/readyz` fails, `/healthz` passes, container restarts on crash, exit code non-zero on fatal start |
| TEST-097 | Outbox | Given 1,000 state changes / When relay runs / Then all published exactly-once-effective, lag metric < 5 s |
| TEST-098 | Migrations | Given previous release DB / When upgrade then app rollback / Then old app still runs (expand/contract) |
| TEST-099 | Load | Given target concurrency (Q-010) / When k6 run / Then p95 within NFR-011 targets and source pools never exceed configured size |

---

## Coding conventions

Proposed: Python — ruff defaults + `from __future__ import annotations`, typed public functions, no bare `except`, domain errors subclass `TelaError` with `code`. TypeScript — strict, no `any` in `features/`, server state only through TanStack Query hooks. IDs are UUID v7. Time in UTC `timestamptz`; display in user's time zone (GAP-015). Feature folders mirror FEAT IDs in README comments for traceability.

---

## Code versus specification discrepancies

**Not available — source code not supplied.** When code exists, record each discrepancy here as: `code path:lines` ↔ `FEAT/REQ/RULE ID`, description, decision.

---

## Lessons from reference systems

Design guardrails taken from defects reported in reference documents (secondary evidence):

| Reference finding | Source | Guardrail in this design |
|---|---|---|
| Widget refresh endpoint re-ran SQL without the viewer's scope | S30 RISK-004 | One `ScopeInjector` path for every execution surface; TEST-075 |
| Flat JSON state files forced single worker | S30 RISK-005 | All state in PostgreSQL; stateless replicas |
| SQLite custom-report store was a single point of failure | S16 RISK-004 | No SQLite; PostgreSQL with backups |
| In-memory pin tokens lost on restart | S16 GAP-002 | Turn results persisted in `turns` (DATA-040); pin by turn ID |
| Shared DB credential for dashboards and NL queries | S16 RISK-002 | Dedicated read-only account per connection; verified at test |
| 60 s grant cache window | S16 RISK-003 | Cache plus session-level read-only flag on every checkout |
| No auth, deployment-pinned CIO | S16 RISK-001 | OIDC from day one |
| Plaintext DB passwords on disk | S30 RISK-003 | Secrets store only |
| Superuser remote profile | S30 RISK-001 | `verify_read_only` refuses superuser/owner accounts |
| Missing health checks/restart policies; exit code 0 on fatal | S31 §5–6 | Healthchecks, restart policies, non-zero exit; TEST-096 |
| Outbox never drained | S31 §6.2 | `outbox_relay` task + lag metric; TEST-097 |
| `sslmode=disable`, Redis without auth, Keycloak dev mode | S31 §5 | TLS everywhere; production-mode Keycloak in all non-local envs |
| Ports bound to 0.0.0.0 | S31 §6.2 | Only edge publishes ports |
| No CI | S31 §6.2 | CI from first commit (REC-012) |
| MySQL/ClickHouse verified only on mocks | S30 RISK-006 | Live container tests for every engine |
| Enum fabrication; entity dropped in follow-ups; creation vs event date | S30 GAP-001…003 | Spec refs only to model fields; dimension values validated against distinct values; spec carries prior filters forward explicitly (FEAT-020); model marks event date fields |
| Module name shadowing `requests` | S16 RISK-009 | Naming rule: never shadow stdlib/PyPI names |

---

## Technical debt, risks and recommendations

No technical debt exists yet (no code). Implementation risks (full register in [risk_register.md](risk_register.md#risk-register)):

- RISK-004 Dataverse endpoint constraints → REC-006 spike before committing Dataverse dates.
- RISK-005 MongoDB semantic mismatch → REC-007 spike on the PMO-like nested document sample.
- RISK-010 Custom SQL validation bypass → sqlglot dialect gaps (especially `tsql`); fuzzing TEST-071 and deny-by-default node list.
- RISK-015 Cache cross-scope → scope hash in key; TEST-073.
- RISK-019 Operational resilience → healthchecks and CI from day one (REC-012).
- RISK-020 RAG drift → nightly eval (TEST-092).
- Complexity risk: seven first-party images. Mitigation: shared base image, single repo, one version number for all.

---

## Open technical questions

| ID | Question | Why it matters |
|---|---|---|
| Q-001 | Refresh model per connection: live with cache vs scheduled cache warm ("data as of 06:00") | Cache TTL design, warm job |
| Q-009 | Approved LLM providers and whether aggregates may be sent | `TELA_LLM_ALLOW_AGGREGATES`, provider adapters to build first |
| Q-010 | Production performance targets | Pool sizes, replica counts, TEST-099 |
| Q-012 | Final connector list and priority (e.g. Oracle, Snowflake, BigQuery later?) | Adapter backlog |
| Q-014 | SaaS, customer-hosted or both in Phase 1 | Keycloak realm strategy, Helm vs Compose focus |
| Q-015 | Dataverse environment access: TDS enabled? app registration allowed? | ADR-013 viability |
| Q-016 | Embedding model hosting local vs provider | Ollama footprint vs egress |
| Q-019 | Reuse rights for reference code (pmo_dashboard, Tela, TelaKnowledge) | Could replace parts of this design with existing code |
| — | Do we need OPA for policies in Phase 1? | Adds a service; in-code evaluator proposed |
| — | Custom pipeline widgets for MongoDB? | Phase 1 excludes them |

---

## Change log

| Date | Version | Sources added | Summary |
|---|---|---|---|
| 2026-10-21 | 0.1 | S01–S31 | First proposed technical design |
