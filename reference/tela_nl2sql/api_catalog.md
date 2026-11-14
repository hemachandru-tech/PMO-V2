# API & Integration Catalog

| Field | Value |
|---|---|
| Project | Tela (NL2SQL Business Intelligence & Reporting Platform) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L5 – Operational (FastAPI Router Implementation, OpenAPI Spec 153KB, Runtime Execution Logs) |
| Sources analyzed | `frontend/src/api/openapi.json`, `backend/app/api/routers/*.py`, `backend/app/api/app.py`, `frontend/src/api/client.ts` |
| Related documents | [Architecture](architecture.md) · [Project Summary](project_summary.md) · [Requirements](requirements.md) · [Business Rules](business_rules.md) · [Data Dictionary](data_dictionary.md) · [Risk Register](risk_register.md) |

---

## 1. Overview and API Governance

Tela provides a modular, headless RESTful API backend built on **FastAPI**, with real-time Server-Sent Events (SSE) streaming for pipeline execution traces. All application routes are prefixed with `/api`.

### Authentication & Authorization Model
- **Authentication:** Enforced at the router level by dependency `deps.get_principal`, which validates an HTTP-only session cookie against `SessionStore`.
- **Exemptions:** `/health` (system monitoring) and `/api/auth/login` (session initiation) are open.
- **Role Enforcement:** Administrative endpoints enforce `require_admin`, returning `403 Forbidden` for standard users.
- **Tenant & Connection Binding:** Connection-dependent endpoints invoke `deps.bind_connection_for`, ensuring callers can only query databases granted in `GrantStore`.

---

## 2. API Endpoint Inventory

### 2.1 Core Natural Language & Streaming Pipeline (`ask.py`)

#### API-001: Submit Natural Language Question
- **Method & Path:** `POST /api/ask`
- **Purpose:** Submits a natural language query for processing by the NL2SQL pipeline or schema exploration engine.
- **Consumer:** Frontend Chat Interface (`features/chat/useChat.ts`)
- **Access / Role:** Authenticated (`admin` or `user`)
- **Request Schema (`AskRequest`):**
  ```json
  {
    "question": "What is the total transaction volume in AED this month?",
    "conversation_id": "optional-uuid-string",
    "connection_id": "optional-connection-id",
    "auto_route": true,
    "mode": "ask"
  }
  ```
  *(Mode values: `"ask"` for NL2SQL execution, `"explore"` for schema explanation)*
- **Response Status:** `202 Accepted`
- **Response Schema (`AskResponse`):**
  ```json
  {
    "turn_id": "turn-9c17dcc7-a4be-47d2-a7d9",
    "conversation_id": "conv-2c538690-2603-4a4d-802f",
    "stream_url": "/api/ask/turn-9c17dcc7-a4be-47d2-a7d9/stream",
    "status": "processing"
  }
  ```
- **Error Codes:** `400 Bad Request` (empty question), `401 Unauthorized`, `403 Forbidden` (no connection grant).
- **Idempotency:** Non-idempotent.

#### API-002: Stream Pipeline Execution Trace
- **Method & Path:** `GET /api/ask/{turn_id}/stream`
- **Purpose:** Establishes an SSE stream delivering stage-by-stage execution progress, timestamps, and pipeline events.
- **Consumer:** Frontend Trace Panel (`stream.ts`)
- **Access / Role:** Authenticated (Owner of `turn_id`)
- **Protocol:** `text/event-stream`
- **Event Types Emitted:**
  - `intent`: Initial question parsing and mode determination.
  - `routing`: Target database resolution (if auto-routing is enabled).
  - `retrieval`: Semantic layer top-K table and column identification.
  - `planning`: LLM prompt dispatch and raw SQL generation.
  - `validation`: Deterministic AST validation, forbidden keyword check, LIMIT injection.
  - `execution`: Database driver query execution and execution duration.
  - `viz`: Vega-Lite chart type selection based on column types.
  - `complete`: Final turn payload indicator.
  - `error`: Pipeline failure or security rejection event.
- **Timeout:** 60-second connection timeout.

#### API-003: Retrieve Turn Result
- **Method & Path:** `GET /api/ask/{turn_id}/result`
- **Purpose:** Fetches the completed dataset, generated SQL, chart specification, and narrative summary.
- **Consumer:** Frontend Result View (`ResultView.tsx`)
- **Access / Role:** Authenticated (Owner of `turn_id`)
- **Response Schema:**
  ```json
  {
    "turn_id": "turn-9c17dcc7",
    "status": "success",
    "sql": "SELECT currency, SUM(amount) AS total_volume FROM financial_transaction WHERE txn_date >= '2026-10-01' GROUP BY currency LIMIT 10000",
    "columns": [{"name": "currency", "type": "categorical"}, {"name": "total_volume", "type": "numeric"}],
    "rows": [{"currency": "AED", "total_volume": 452900.50}],
    "row_count": 1,
    "duration_ms": 142,
    "truncated": false,
    "viz_config": {
      "chart_type": "bar",
      "x_field": "currency",
      "y_field": "total_volume",
      "spec": { "$schema": "https://vega.github.io/schema/vega-lite/v5.json", ... }
    },
    "narrative": "Total transaction volume for AED in October 2026 reached AED 452,900.50 across 1 currency group."
  }
  ```
- **Error Codes:** `404 Not Found` (turn expired or invalid), `401 Unauthorized`.

---

### 2.2 Authentication & Identity (`auth.py`)

#### API-004: User Authentication Login
- **Method & Path:** `POST /api/auth/login`
- **Purpose:** Authenticates user credentials and establishes an HTTP-only session cookie.
- **Access / Role:** Open (Public)
- **Request Schema:** `{"username": "admin", "password": "change-me-before-first-run"}`
- **Response Status:** `200 OK` (Sets cookie `tela_session=<session_id>; HttpOnly; SameSite=Lax; Path=/`)
- **Response Schema:**
  ```json
  {
    "user_id": "usr-admin-001",
    "username": "admin",
    "role": "admin",
    "tenant_id": "default",
    "default_connection_id": "clickhouse:localhost:9000:rschdb"
  }
  ```
- **Error Codes:** `401 Unauthorized` (Invalid credentials).

#### API-005: User Session Logout
- **Method & Path:** `POST /api/auth/logout`
- **Purpose:** Terminates the active session and clears the session cookie.
- **Access / Role:** Authenticated
- **Response Status:** `200 OK` (Overwrites cookie with `Max-Age=0`).

#### API-006: Inspect Active Session
- **Method & Path:** `GET /api/auth/session`
- **Purpose:** Returns the authenticated user's profile, role, and capabilities. Used as the frontend login gate check.
- **Access / Role:** Authenticated (Returns `401 Unauthorized` if unauthenticated).

---

### 2.3 User & Access Management (`users.py`, `roles.py`, `grants.py`)

#### API-007: User Administration CRUD
- **Endpoints:**
  - `GET /api/users` — List all registered users (Admin only)
  - `POST /api/users` — Create user with username, password, role (`admin`|`user`), optional persona (Admin only)
  - `PATCH /api/users/{id}` — Update password, role, active status, or persona (Admin only)
  - `DELETE /api/users/{id}` — Delete user (Guards: prevents deleting self or the last remaining admin)
- **Error Codes:** `400 Bad Request` (Last admin deletion guard), `403 Forbidden`, `404 Not Found`.

#### API-008: Role & Persona Configuration
- **Endpoints:**
  - `GET /api/roles` — Returns list of roles (ID and Name) for all authenticated users.
  - `GET /api/roles/config` — Returns complete role definitions with capabilities and grant templates (Admin only).
  - `POST /api/roles` — Create new custom role definition (Admin only).
  - `PATCH /api/roles/{id}` — Update role permissions and grant templates (Admin only).
  - `DELETE /api/roles/{id}` — Remove role (Refuses with `409 Conflict` if currently assigned to any user).

#### API-009: Per-User Access Grants (P2.3)
- **Endpoints:**
  - `GET /api/grants/{user_id}` — Retrieve connection, table, column, and row-level grants for a user (Admin only).
  - `PUT /api/grants/{user_id}` — Save or replace access grants; validates structured row filter conditions (Admin only).
  - `DELETE /api/grants/{user_id}` — Revoke all grants for user (Admin only).
  - `GET /api/grants/schema` — Returns approved tables and columns for a specified connection to populate the admin grant configuration UI.

#### API-010: Attribute Values Lookup
- **Method & Path:** `GET /api/attributes/values`
- **Purpose:** Fetches up to 200 distinct values of an approved table/column to populate row-filter dropdowns in the access control UI. Runs through `query_guard.guarded_execute` with an allow-list containing strictly that table and column.
- **Access / Role:** Admin only.

---

### 2.4 Database Connections & Management (`connections.py`)

#### API-011: List Saved Connections
- **Method & Path:** `GET /api/connections/saved`
- **Purpose:** Returns list of non-secret connection descriptors (id, name, engine, host, port, database, active status, needs_reconnect).
- **Access / Role:** Authenticated (Filtered to user's granted connections).

#### API-012: Test Database Connection
- **Method & Path:** `POST /api/connections/test`
- **Purpose:** Tests database reachability, credentials, and read-only status without saving.
- **Access / Role:** Admin only.
- **Request Schema:**
  ```json
  {
    "engine": "postgres",
    "host": "localhost",
    "port": 5432,
    "database": "alumnye",
    "username": "postgres_ro",
    "password": "secret",
    "use_ssh": false
  }
  ```
- **Response Schema:** `{"success": true, "message": "Connection established successfully (read-only verified)"}`.

#### API-013: Activate Database Connection
- **Method & Path:** `POST /api/connections/activate`
- **Purpose:** Sets the specified connection as the globally active connection, builds the executor, and loads its semantic layer.
- **Access / Role:** Admin only.

#### API-014: Inspect Bound Active Connection
- **Method & Path:** `GET /api/connections/active`
- **Purpose:** Returns the active database descriptor bound to the caller's session context.
- **Access / Role:** Authenticated.

---

### 2.5 Widgets & Visualizations (`widgets.py`)

#### API-015: Widget CRUD
- **Endpoints:**
  - `GET /api/widgets` — Returns all saved widgets and their cached columns/rows.
  - `POST /api/widgets` — Pin a chat query result as a widget (`title`, `query_definition`, `viz_config`, `filter_schema`).
  - `PATCH /api/widgets/{id}` — Update widget title, layout, or visualization preferences.
  - `DELETE /api/widgets/{id}` — Delete a saved widget.

#### API-016: Refresh Widget Query
- **Method & Path:** `POST /api/widgets/{id}/refresh`
- **Purpose:** Re-executes a widget's stored SQL with optional filter overrides. Filters are injected as AST predicates via `access.py`, re-validated through `validator.py`, and re-executed under the viewer's security scope.
- **Access / Role:** Authenticated.
- **Request Schema:** `{"filters": [{"field": "region", "operator": "eq", "value": "APAC"}]}`.

---

### 2.6 Dashboards (`dashboards.py`, `custom_dashboards.py`)

#### API-017: AI Auto-Generate Dashboard
- **Method & Path:** `GET /api/dashboard/auto`
- **Purpose:** Prompts the LLM to plan a comprehensive multi-panel dashboard from the active connection's semantic layer, returning validated panel queries and chart configurations.
- **Access / Role:** Authenticated.

#### API-018: Execute Dashboard Panels
- **Method & Path:** `POST /api/dashboard/run`
- **Purpose:** Executes all SQL queries across all panels on a dashboard, applying global dashboard filter values via AST injection.
- **Access / Role:** Authenticated.

#### API-019: Save and Retrieve Dashboards
- **Endpoints:**
  - `POST /api/dashboard/save` — Save or update dashboard definition.
  - `GET /api/dashboard/saved` — List saved dashboards for active connection.
  - `GET /api/custom-dashboards` — List user custom drag-and-drop dashboards.
  - `POST /api/custom-dashboards` — Create or update custom dashboard layout coordinates (`react-grid-layout`).

#### API-020: Fetch Database Filter Options (S1.7)
- **Method & Path:** `POST /api/dashboard/filter-values`
- **Purpose:** Queries the database (`SELECT DISTINCT`, max 200 rows) under the viewer's active scope and row rules to populate filter dropdown options.
- **Access / Role:** Authenticated.
- **Request Schema:** `{"filter_ids": ["flt-region"], "depends_on": {"flt-country": "UAE"}}`
- **Response Schema:** `{"flt-region": ["Dubai", "Abu Dhabi", "Sharjah"]}`.

#### API-021: Dashboard AI Insight Chat (M2)
- **Method & Path:** `POST /api/dashboards/chat`
- **Purpose:** Conversational chat over dashboard data. Transmits **computed aggregates only** via `dashboard_context.py` through the Gateway; never transmits raw row data.
- **Access / Role:** Authenticated.

---

### 2.7 Semantic Layer Curation (`semantic.py`)

#### API-022: Table and Column Approval
- **Endpoints:**
  - `GET /api/semantic-layer/tables` — Returns all introspected tables and their approval status.
  - `POST /api/semantic-layer/approve` — Approve or reject specific tables/columns, controlling LLM visibility.

#### API-023: AI Description Enrichment
- **Method & Path:** `POST /api/semantic-layer/enrich`
- **Purpose:** Triggers LLM batch drafting of business descriptions and synonyms for approved tables and columns (`description_drafter.py`).
- **Access / Role:** Admin only.

#### API-024: Business Notes & Glossary CRUD
- **Endpoints:**
  - `GET /api/semantic-layer/business-notes` — Fetch curated business rules and terminology definitions.
  - `POST /api/semantic-layer/business-notes` — Create or update business notes injected into planner prompts.
  - `GET /api/semantic-layer/examples` — Fetch verified few-shot question->SQL examples.
  - `POST /api/semantic-layer/examples` — Save verified few-shot question->SQL examples.

---

### 2.8 Data Profiling (`profiling.py`)

#### API-025: Trigger Table Profiling Scan
- **Method & Path:** `POST /api/profiling/run`
- **Purpose:** Runs a single-scan profiling query across approved tables (capped at 25 tables), deriving data condition flags (`mixed_case`, `untrimmed`, `numeric_as_text`, `null_like_values`).
- **Access / Role:** Admin only.

#### API-026: Fetch Table Profiles
- **Endpoints:**
  - `GET /api/profiling` — Returns summary profile flags for all scanned tables.
  - `GET /api/profiling/{table}` — Returns detailed column condition metrics for a specific table.
  - `DELETE /api/profiling` — Clears profile cache (called automatically upon schema re-introspection).

---

### 2.9 Reporting & System Settings (`exports.py`, `settings.py`, `usage.py`)

#### API-027: Multi-Format Report Export
- **Method & Path:** `POST /api/exports/generate`
- **Purpose:** Generates formatted document downloads (.xlsx, .pdf, .pptx) for query result sets.
- **Access / Role:** Authenticated.

#### API-028: LLM Provider Configuration
- **Endpoints:**
  - `GET /api/settings/llm` — Returns active LLM provider, available model registry, and per-role models (sql, summarise, analyse).
  - `POST /api/settings/llm` — Updates active provider and API keys; recorded in audit log. (Admin only).

#### API-029: LLM Usage & Token Cost Metrics
- **Method & Path:** `GET /api/usage`
- **Purpose:** Returns cumulative token usage, prompt counts, and estimated financial costs tracked in `usage.json`.
- **Access / Role:** Authenticated.

---

## 3. External Integrations Inventory

| Integration Name | Type | Protocol | Auth Mechanism | Purpose | Rate Limits & Timeouts | Fallback / Failure Mode |
|---|---|---|---|---|---|---|
| **Anthropic Claude** | LLM Provider | HTTPS / REST | Bearer API Key | NL2SQL planning (Sonnet), semantic drafting (Haiku) | Configurable per provider; 30s timeout | Retry once on malformed JSON; fail closed on timeout |
| **OpenAI GPT** | LLM Provider | HTTPS / REST | Bearer API Key | Alternative planner & narrative synthesis (GPT-4.1) | Provider default; 30s timeout | Fallback error to user trace |
| **Google GenAI** | LLM Provider | HTTPS / REST | API Key | Alternative planner (Gemini 2.5/3.0) | Provider default; 30s timeout | Fallback error to user trace |
| **Ollama** | Local LLM | HTTP / REST | None | Local offline inference | Host hardware dependent; 60s timeout | Disables provider if unreachable |
| **PostgreSQL** | Target DB | TCP / psycopg2 | User / Password / SSL | Primary relational analytics engine | Connection pool (5-10); 15s query timeout | Read-only error mapped to ExecutorError |
| **MySQL** | Target DB | TCP / PyMySQL | User / Password | Relational database source | Connection pool (5-10); 15s query timeout | Read-only session enforcement |
| **ClickHouse** | Target DB | TCP (9000) / HTTP | User / Password | High-scale columnar analytics engine | Pool (5-10); 30s max_execution_time | Native ClickHouse error parsing |
| **DuckDB** | In-Memory DB | In-Process C++ | Local File / Memory | Local execution for uploaded Excel/CSV files | Single-process memory; 30s timeout | Clears temp table on connection close |
| **SSH Bastion** | Tunnel | SSH / Paramiko | Private Key / Password | Secure tunnel for remote database access | 30s tunnel connection timeout | Reconnect retry (2x) |
