# Requirements Specification

| Field | Value |
|---|---|
| Project | Tela (NL2SQL Business Intelligence & Reporting Platform) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L5 – Operational (Code, Specs, Configurations, Unit/Integration Tests, Live Server Logs) |
| Sources analyzed | 35 source documents, 66 code modules, OpenAPI specification, test suite, runtime execution logs |
| Related documents | [Architecture](architecture.md) · [Project Summary](project_summary.md) · [Business Rules](business_rules.md) · [API Catalog](api_catalog.md) · [Data Dictionary](data_dictionary.md) · [Risk Register](risk_register.md) |

---

## 1. Overview and Requirements Governance

This document establishes the authoritative functional and non-functional requirements for the **Tela** platform. Requirements are structured hierarchically by domain module, assigned unique identifiers (`REQ-###` for functional, `NFR-###` for non-functional), mapped to MoSCoW priorities, and anchored with testable acceptance criteria using the Given-When-Then structure.

### Priority Definition
- **Must Have (M):** Mandatory for system operation, security, or data integrity.
- **Should Have (S):** Critical for full business capability; workaround exists only with substantial manual effort.
- **Could Have (C):** Desirable enhancement that improves user experience or administrative convenience.
- **Won't Have (W) [v1/v2 Scope]:** Explicitly deferred or permanently excluded (non-goals).

---

## 2. Functional Requirements Catalog

### 2.1 Authentication, Roles & Multi-Tenancy

#### REQ-001: Session-Cookie Authentication
- **Description:** The system must authenticate users via HTTP-only, secure, samesite session cookies following successful username and bcrypt-hashed password verification.
- **Source:** `docs/ARCHITECTURE.md §3.2`, `backend/app/api/routers/auth.py`, `TELA_V1_BASE_EXPERIENCE.md §3.1`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `DATA-001` (UserStore), `DATA-002` (SessionStore)
- **Linked Features:** `FEAT-001`, `API-004`, `TEST-001`
- **Acceptance Criteria:**
  - *Given* an unauthenticated user submits valid credentials to `/api/auth/login`,
  - *When* the backend verifies the bcrypt password hash,
  - *Then* an HTTP-only session cookie is set, a 200 OK response is returned with the user profile, and subsequent requests resolve the authenticated `Principal`.
  - *Given* an invalid password or expired session,
  - *When* accessing protected `/api/*` endpoints,
  - *Then* the server must immediately reject the request with HTTP 401 Unauthorized.

#### REQ-002: Automatic Admin Bootstrap
- **Description:** On startup, if no user exists in `UserStore`, the backend must automatically seed an initial administrative user using environment variables `TELA_ADMIN_USER` and `TELA_ADMIN_PASSWORD`.
- **Source:** `backend/app/api/app.py:134-150`, `CLAUDE.md §5`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-001`
- **Linked Features:** `FEAT-002`, `TEST-002`
- **Acceptance Criteria:**
  - *Given* a fresh deployment with an empty `backend/data/api/users.json`,
  - *When* the FastAPI application starts with `TELA_ADMIN_USER` and `TELA_ADMIN_PASSWORD` defined,
  - *Then* an admin user record is created with role `admin`, and startup proceeds without requiring unauthenticated setup endpoints.

#### REQ-003: Role-Based Access Control (RBAC) & Persona Management
- **Description:** The system must enforce two primary security roles (`admin` and `user`), while supporting configurable job personas (Leader, Manager, Governance, Finance, Admin) that provide default capabilities and grant templates.
- **Source:** `backend/app/api/routers/roles.py`, `docs/ARCHITECTURE.md §3.2`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-001`
- **Linked Features:** `FEAT-003`, `API-010`
- **Acceptance Criteria:**
  - *Given* a non-admin user authenticated with security role `user`,
  - *When* the user attempts to access admin routers (`/api/users`, `/api/connections`, `/api/roles/config`, `/api/grants/*`),
  - *Then* the system returns HTTP 403 Forbidden.
  - *Given* an admin attempting to delete a role assigned to active users,
  - *When* `DELETE /api/roles/{id}` is called,
  - *Then* the server returns HTTP 409 Conflict.

#### REQ-004: Connection-Level Grant Isolation
- **Description:** Standard users must only be able to query, browse, or auto-route across database connections explicitly granted to them via `GrantStore`.
- **Source:** `backend/app/access.py`, `backend/app/api/deps.py:bind_connection_for`, `TELA_V1_BASE_EXPERIENCE.md §3.2`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-001`, `DATA-004` (GrantStore)
- **Linked Features:** `FEAT-004`, `TEST-003`
- **Acceptance Criteria:**
  - *Given* a user with grants exclusively for Connection A,
  - *When* the user sends a query targeting Connection B,
  - *Then* `bind_connection_for` immediately raises HTTP 403/503 and no executor or semantic layer is allocated.

---

### 2.2 NL2SQL Planning, Gateway & Safety Boundary

#### REQ-005: Single Chokepoint LLM Gateway
- **Description:** All LLM invocations across all system features must route exclusively through `app/gateway.py`. Direct invocation of LLM provider SDKs or `generate_content` bypasses are strictly prohibited.
- **Source:** `CLAUDE.md §3`, `PlanFiles/03_data_security_and_access_control.md §2.1`, `backend/app/gateway.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** None
- **Linked Features:** `FEAT-005`, `ADR-001`, `TEST-004`
- **Acceptance Criteria:**
  - *Given* any prompt dispatched to an external LLM provider,
  - *When* evaluated by `gateway.generate()`,
  - *Then* the payload is masked, scanned by regex/fingerprint leak scanners, logged in append-only audit logs, and blocked if sensitive tokens are detected.

#### REQ-006: Pre-Send Leak Detection & Blocking
- **Description:** The Gateway must scan outgoing payloads for sensitive strings (API keys, connection strings, emails, phone numbers, PII) and immediately block the call if a leak is detected, registering a Security Event.
- **Source:** `PlanFiles/03 §2.1`, `backend/app/gateway.py:scan_leak`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-005`
- **Linked Features:** `FEAT-005`, `RULE-005`, `RISK-001`
- **Acceptance Criteria:**
  - *Given* a generated prompt containing an OpenAI, Anthropic, or database credential key or email regex match,
  - *When* scanned by `gateway.py`,
  - *Then* the call raises `LeakBlockedError`, execution halts, and no HTTP request leaves the host.

#### REQ-007: Zero Raw Row Data Egress Rule
- **Description:** Raw row data, cell values, and unaggregated table dumps must never be transmitted across the LLM boundary under any circumstances.
- **Source:** `PlanFiles/03 §2`, `CLAUDE.md §3`, `backend/app/planner.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-005`
- **Linked Features:** `FEAT-006`, `RULE-006`
- **Acceptance Criteria:**
  - *Given* an NL2SQL planning request,
  - *When* the prompt is constructed by `planner.py`,
  - *Then* only table names, column names, column data types, business descriptions, and approved few-shot SQL examples are included in the prompt context.

#### REQ-008: Scoped Aggregate Exception for Dashboard Insights
- **Description:** For dashboard narrative insights and comparisons, the backend may transmit computed aggregates (min, max, count, distinct count, mean, delta) through `dashboard_context.py` only if computed in Python and stripped of PII labels.
- **Source:** `PlanFiles/03 §2.0a`, `backend/app/dashboard_context.py`, `CLAUDE.md §3`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-005`, `REQ-007`
- **Linked Features:** `FEAT-007`, `API-022`
- **Acceptance Criteria:**
  - *Given* a dashboard insight request,
  - *When* `dashboard_context.py` builds the prompt,
  - *Then* individual row payloads are withheld, high-cardinality/PII labels are discarded, and only numeric aggregates are sent through the Gateway.

#### REQ-009: Dialect-Aware NL2SQL Generation
- **Description:** The planner must emit SQL tailored to the active database engine's dialect (PostgreSQL, MySQL, ClickHouse, DuckDB) using dialect directives in the planner prompt template.
- **Source:** `backend/app/planner.py`, `backend/prompts/planner_v1.txt`, `PlanFiles/04`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-007`
- **Linked Features:** `FEAT-008`, `RULE-007`
- **Acceptance Criteria:**
  - *Given* a question requiring date truncation on a ClickHouse connection,
  - *When* the planner generates SQL,
  - *Then* it outputs `toStartOfMonth(col)` rather than PostgreSQL's `DATE_TRUNC('month', col)`.

---

### 2.3 SQL Validation & Safety Gate

#### REQ-010: Pure-Read Single-Statement Enforcement
- **Description:** Every query generated by the LLM or submitted for execution must pass through `app/validator.py` using `sqlglot` AST parsing. Queries must consist of exactly one `SELECT` or `WITH ... SELECT` statement.
- **Source:** `PlanFiles/05 §2`, `backend/app/validator.py`, `CLAUDE.md §3`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** None
- **Linked Features:** `FEAT-009`, `RULE-001`, `ADR-002`, `TEST-005`
- **Acceptance Criteria:**
  - *Given* any SQL statement containing multiple statements (e.g., `SELECT 1; DROP TABLE users;`) or non-SELECT operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`, `MERGE`, `CALL`),
  - *When* evaluated by `validator.validate()`,
  - *Then* `approved` is returned as `False`, `is_security_event` is set to `True`, and execution is refused before touching the database driver.

#### REQ-011: Strict Schema & Table Allow-List Enforcement
- **Description:** The validator must verify that every table and column referenced in the query AST exists in the active connection's approved semantic layer subset.
- **Source:** `backend/app/validator.py:validate`, `PlanFiles/05 §2.4`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-010`
- **Linked Features:** `FEAT-009`, `RULE-002`
- **Acceptance Criteria:**
  - *Given* a planner hallucination that invents a non-existent table `payroll_master`,
  - *When* validated against the allow-list,
  - *Then* the query is rejected with `rejection_stage='allow_list'` and never executed.

#### REQ-012: Automatic Safe LIMIT Injection
- **Description:** If an approved query lacks a `LIMIT` clause, the validator must inject a dialect-appropriate `LIMIT 10000` (or configured threshold) into the executable SQL AST while preserving the original query for audit display.
- **Source:** `backend/app/validator.py:_inject_limit`, `PlanFiles/05 §2.6`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-010`
- **Linked Features:** `FEAT-009`, `RULE-003`
- **Acceptance Criteria:**
  - *Given* an approved query `SELECT * FROM financial_transaction`,
  - *When* validated,
  - *Then* `sql_to_execute` is modified to `SELECT * FROM financial_transaction LIMIT 10000`, and `truncated` is flagged if row count reaches 10,000.

#### REQ-013: Post-Planner Row-Filter AST Injection & Fail-Closed Behavior
- **Description:** The system must enforce row-level access control in code by rewriting query ASTs (`FROM t` -> `FROM (SELECT * FROM t WHERE <predicate>) t`). If the rewrite cannot be performed safely or if an attribute cannot be resolved, the query must fail closed and refuse execution.
- **Source:** `PlanFiles/03 §5.1`, `backend/app/access.py:apply_row_filters`, `backend/app/query_guard.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-003`, `REQ-010`
- **Linked Features:** `FEAT-010`, `RULE-004`, `ADR-003`, `TEST-006`
- **Acceptance Criteria:**
  - *Given* a user with a row rule `merchant_id = 'M100'`,
  - *When* the user runs a query touching `financial_transaction`,
  - *Then* the query is rewritten so only rows where `merchant_id = 'M100'` can be returned.
  - *Given* an unparseable query, a CTE colliding with a governed table name, or an unresolvable user attribute,
  - *When* `apply_row_filters` is invoked,
  - *Then* it raises `RowRuleError`, returns `status='rejected'`, `rejection_stage='row_filter'`, and marks a security event without executing the SQL.

---

### 2.4 Multi-Engine Database Execution

#### REQ-014: Structural Read-Only Session Backstop
- **Description:** Database execution engines must explicitly enforce read-only execution at the database driver session level (e.g., `SET SESSION TRANSACTION READ ONLY` on PostgreSQL/MySQL), preventing writes even if the code-level validator were bypassed.
- **Source:** `PlanFiles/03 §3`, `backend/app/executor.py`, `backend/app/executors.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-010`
- **Linked Features:** `FEAT-011`, `ADR-004`
- **Acceptance Criteria:**
  - *Given* a live database connection in PostgreSQL or MySQL,
  - *When* a connection is checked out from the pool,
  - *Then* a read-only transaction state is applied before executing any statement.

#### REQ-015: Multi-Engine Executor Abstraction
- **Description:** The backend must provide unified execution interfaces (`dialect`, `execute()`, `close()`) supporting PostgreSQL, MySQL, ClickHouse, and in-memory DuckDB (for Excel/CSV files).
- **Source:** `backend/app/executors.py:build_executor`, `docs/ARCHITECTURE.md §3.1`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5 for Postgres & DuckDB; L3 for MySQL/ClickHouse)
- **Dependencies:** None
- **Linked Features:** `FEAT-012`, `TEST-007`
- **Acceptance Criteria:**
  - *Given* an uploaded Excel spreadsheet,
  - *When* activated as a connection,
  - *Then* each worksheet is loaded as a table in DuckDB and queries execute using DuckDB SQL syntax.

#### REQ-016: Statement Timeout Enforcement
- **Description:** All database executions must enforce a hard statement timeout (default 15–30 seconds) at the driver/server level to prevent long-running queries from exhausting connection pools.
- **Source:** `PlanFiles/05 §4`, `backend/app/executor.py`, `backend/app/executors.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-014`
- **Linked Features:** `FEAT-011`, `NFR-004`
- **Acceptance Criteria:**
  - *Given* a complex query scanning millions of unindexed rows,
  - *When* execution time exceeds the timeout threshold,
  - *Then* the driver terminates the query server-side and returns an `ExecutorTimeoutError` to the pipeline.

---

### 2.5 Semantic Layer, Introspection & Data Profiling

#### REQ-017: Three-Tier Semantic Layer Model
- **Description:** The semantic layer must maintain: Tier 1 (physical schema, columns, PK/FK relationships via introspection), Tier 2 (LLM-drafted business names, descriptions, synonyms), and Tier 3 (human-curated glossary definitions, business notes, few-shot gold SQL examples).
- **Source:** `PlanFiles/10 §3`, `backend/app/semantic_layer.py`, `backend/app/dynamic_semantic_layer.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** None
- **Linked Features:** `FEAT-013`, `API-028`
- **Acceptance Criteria:**
  - *Given* an admin connecting a new database,
  - *When* schema introspection runs,
  - *Then* Tier 1 tables and columns are extracted without calling an LLM, and Tier 2 draft descriptions can be generated in bulk upon admin request.

#### REQ-018: FK-Only Structural Joins Constraint
- **Description:** Structural joins in Tier 1 must strictly originate from verified foreign key constraints in the database schema. The system must never heuristically auto-invent non-FK join paths.
- **Source:** `PlanFiles/10 §4`, `backend/app/schema_introspection.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-017`
- **Linked Features:** `FEAT-013`, `RULE-008`
- **Acceptance Criteria:**
  - *Given* an introspected database with missing FK constraints between `order` and `customer`,
  - *When* building the structural semantic layer,
  - *Then* no join path is auto-synthesized; it is flagged for Tier 3 human definition.

#### REQ-019: Connection Business Notes as Binding Prompt Directives
- **Description:** Admin-entered business notes and glossary definitions must be injected into planner and explorer prompts as binding instructions that override model default assumptions.
- **Source:** `backend/app/standing_instructions.py`, `docs/ARCHITECTURE.md §3.1`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-017`
- **Linked Features:** `FEAT-014`, `RULE-009`
- **Acceptance Criteria:**
  - *Given* a business note specifying: "Revenue means invoice_amount excluding tax",
  - *When* the user asks "Show total revenue",
  - *Then* the planner prompt includes this binding note and the model generates `SUM(invoice_amount)` excluding tax columns.

#### REQ-020: Data Profiling and Column Condition Detection
- **Description:** The backend must provide automated data profiling (`profiling.py`) that scans tables for column data anomalies (`mixed_case`, `untrimmed`, `numeric_as_text`, `null_like_values`, `high_cardinality`) in a single read-only scan per table, injecting dialect-specific formatting imperatives into planner prompts.
- **Source:** `backend/app/profiling.py`, `backend/app/api/routers/profiling.py`, `docs/ARCHITECTURE.md §3.1`
- **Priority:** Should Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-010`, `REQ-014`
- **Linked Features:** `FEAT-015`, `API-025`
- **Acceptance Criteria:**
  - *Given* an admin triggers profiling via `/api/profiling/run`,
  - *When* column condition flags are detected on `customer_name` (e.g. `untrimmed`),
  - *Then* subsequent planner prompts include directives instructing the model to apply `TRIM()` when filtering on `customer_name`.

---

### 2.6 Chat Interface, Streaming & Visualization

#### REQ-021: Server-Sent Events (SSE) Execution Tracing
- **Description:** The `/api/ask` pipeline must stream stage-by-stage execution progress (intent, routing, retrieval, planning, validation, execution, visualization) to the frontend via SSE in real time.
- **Source:** `PlanFiles/02 §1.7`, `backend/app/api/routers/ask.py`, `backend/app/api/turns.py`, `frontend/src/api/stream.ts`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-005`, `REQ-010`, `REQ-014`
- **Linked Features:** `FEAT-016`, `API-001`, `API-002`
- **Acceptance Criteria:**
  - *Given* a user question submitted to `/api/ask`,
  - *When* the frontend connects to `/api/ask/{id}/stream`,
  - *Then* trace events for each stage arrive sequentially, rendering the step-by-step progress trail in the UI.

#### REQ-022: Schema Exploration Mode
- **Description:** The system must support a dedicated "Explore" mode (`explorer.py`) that answers natural language questions about table schema, column definitions, and data availability without executing SQL against the live database.
- **Source:** `backend/app/explorer.py`, `backend/app/api/routers/ask.py:mode=explore`
- **Priority:** Should Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-007`, `REQ-017`
- **Linked Features:** `FEAT-017`, `API-001`
- **Acceptance Criteria:**
  - *Given* an inquiry submitted with `mode: "explore"`,
  - *When* processed by the pipeline,
  - *Then* a grounded explanation and optional suggested SQL are returned, `sql_executed` is null, and zero queries touch the database.

#### REQ-023: Value-Free Chart Type Decision Engine
- **Description:** The visualization engine (`viz.py`) must determine appropriate chart specifications (bar, line, scatter, pie, histogram, density, box, heatmap, treemap, or table) based strictly on column data types, cardinalities, and query semantics, never by inspecting sensitive raw row values.
- **Source:** `PlanFiles/06 §6`, `backend/app/viz.py`, `backend/app/charts.py`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** None
- **Linked Features:** `FEAT-018`, `RULE-010`
- **Acceptance Criteria:**
  - *Given* query results with one categorical column and two numeric metrics,
  - *When* evaluated by `viz.decide_viz()`,
  - *Then* a multi-measure bar or scatter chart is selected and a valid Vega-Lite specification is produced.

#### REQ-024: Question Queuing & Cancellation
- **Description:** The chat UI must support aborting in-flight pipeline turns via `AbortController` and queuing successive user questions sequentially.
- **Source:** `to-dos/backlog.md:P3.6`, `frontend/src/features/chat/useChat.ts`
- **Priority:** Should Have
- **Status:** Partially Implemented (Verified L3)
- **Dependencies:** `REQ-021`
- **Linked Features:** `FEAT-019`
- **Acceptance Criteria:**
  - *Given* an in-flight query stream,
  - *When* the user clicks Stop,
  - *Then* the frontend aborts the SSE connection and stops waiting for completion.

---

### 2.7 Dashboards, Custom Layouts & Interactive Filtering

#### REQ-025: AI-Planned Dynamic Dashboard Generation
- **Description:** The system must generate multi-panel dashboards from natural language prompts (`/api/dashboard/auto`) or semantic layer structures, emitting validated read-only SQL queries and visualization specifications.
- **Source:** `backend/app/api/routers/dashboards.py:373`, `PlanFiles/06`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-005`, `REQ-010`, `REQ-023`
- **Linked Features:** `FEAT-020`, `API-018`
- **Acceptance Criteria:**
  - *Given* an admin or user requesting a dashboard,
  - *When* `/api/dashboard/auto` runs,
  - *Then* multiple panels with distinct chart types and valid SQL queries are generated, validated, executed, and rendered in a responsive grid layout.

#### REQ-026: Independent Dashboard Widget Refresh & Re-Validation
- **Description:** Each widget or panel on a dashboard must support independent re-querying (`/api/widgets/{id}/refresh`), with any applied filters injected as AST predicates and re-validated before execution.
- **Source:** `backend/app/api/routers/widgets.py:102`, `PlanFiles/06 §7`
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-010`, `REQ-014`
- **Linked Features:** `FEAT-021`, `API-016`
- **Acceptance Criteria:**
  - *Given* an existing saved widget,
  - *When* refreshed with a date range filter,
  - *Then* the SQL WHERE clause is rewritten using `sqlglot`, re-validated against forbidden keywords, and re-executed.

#### REQ-027: Database-Backed Scope-Aware Filter Options
- **Description:** Dashboard filter dropdown options must be queried directly from the database (`SELECT DISTINCT`, capped at 200) under the viewer's active scope and row rules, never by sending filter options to an LLM.
- **Source:** `backend/app/filter_options.py`, `backend/app/api/routers/dashboards.py:/dashboard/filter-values`
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-013`, `REQ-014`
- **Linked Features:** `FEAT-022`, `API-021`
- **Acceptance Criteria:**
  - *Given* a user viewing a dashboard with a region filter,
  - *When* fetching options from `/api/dashboard/filter-values`,
  - *Then* the distinct values query is filtered by the user's row grants, ensuring unauthorized values are not even listed as selectable options.

#### REQ-028: Custom Drag-and-Drop Dashboard Layouts
- **Description:** Users must be able to create custom dashboards (`custom_dashboards.py`) using `react-grid-layout`, repositioning, resizing, pinning chat widgets, and saving layout states.
- **Source:** `backend/app/api/routers/custom_dashboards.py`, `frontend/src/features/dashboard/DashboardGrid.tsx`
- **Priority:** Should Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-025`
- **Linked Features:** `FEAT-023`, `API-023`
- **Acceptance Criteria:**
  - *Given* a user in edit mode on a custom dashboard,
  - *When* resizing or dragging a widget tile and clicking Save,
  - *Then* updated layout coordinates (`x`, `y`, `w`, `h`) are persisted to `backend/data/api/custom_dashboards.json`.

---

### 2.8 Automatic Connection Routing

#### REQ-029: Semantic Layer Cross-Connection Auto-Routing
- **Description:** When auto-routing is enabled, the backend must dynamically evaluate user queries against all granted database connections, score semantic relevance across approved tables, and route execution to the optimal connection.
- **Source:** `backend/app/api/routing.py`, `TELA_V1_BASE_EXPERIENCE.md §7`, `backend/app/api/turns.py`
- **Priority:** Should Have
- **Status:** Implemented (Verified L5)
- **Dependencies:** `REQ-004`, `REQ-017`
- **Linked Features:** `FEAT-024`, `API-001`
- **Acceptance Criteria:**
  - *Given* a user with grants for a Payments DB and an HR DB with Auto-Route enabled,
  - *When* asking "Show me chargeback volume by scheme",
  - *Then* the router assigns highest relevance to the Payments DB, executes the turn there, and flags `routed_to: <connection_id>` in the response.

---

### 2.9 Reporting & Multi-Format Export

#### REQ-030: PDF, Excel, and Slide Export Engine
- **Description:** The system must export query result tables, dashboard summaries, and narrative reports to Excel (`.xlsx` via openpyxl), PDF (via reportlab), and PowerPoint slides (via python-pptx).
- **Source:** `backend/app/report/export.py`, `backend/app/api/routers/exports.py`
- **Priority:** Could Have
- **Status:** Implemented (Verified L3)
- **Dependencies:** `REQ-023`
- **Linked Features:** `FEAT-025`, `API-027`
- **Acceptance Criteria:**
  - *Given* a query result set in the chat or dashboard view,
  - *When* the user requests an Excel export,
  - *Then* the backend generates and streams a formatted `.xlsx` workbook containing column headers and data rows.

---

## 3. Non-Functional Requirements Catalog

### 3.1 Performance & Latency

#### NFR-001: End-to-End Chat Turn Response Time
- **Description:** For a warm connection on a cached semantic layer, the complete NL2SQL pipeline (retrieval -> planner -> validator -> execution -> viz decision) must stream initial trace tokens within 1.5 seconds and complete within 5.0 seconds for standard queries scanning under 1 million rows.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5: Observed turn latency ~2.1s on local dev)
- **Verification Method:** Performance test harness / telemetry

#### NFR-002: Real-Time SSE Stream Latency
- **Description:** Server-Sent Event updates representing stage transitions must be emitted with less than 100ms latency from backend stage initiation.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Verification Method:** Automated API integration tests

#### NFR-003: UI Initial Page Load & Rendering
- **Description:** The Vite frontend production bundle must achieve Largest Contentful Paint (LCP) under 1.8 seconds on standard broadband connections.
- **Priority:** Should Have
- **Status:** Implemented (Verified L3: Bundle size 1.68 MB uncompressed, 531 KB gzipped)
- **Verification Method:** Lighthouse audit / Chrome DevTools MCP

---

### 3.2 Security, Privacy & Compliance

#### NFR-004: Defense-in-Depth Read-Only Isolation
- **Description:** The system must guarantee that write statements (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`, `MERGE`, `CALL`) cannot execute against target databases, enforced independently by both code AST validation and database-level session transaction modes.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Verification Method:** Negative penetration tests with SQL injection benchmarks

#### NFR-005: Credential Storage & Confidentiality
- **Description:** Passwords for database connections must be stored exclusively in gitignored storage with filesystem permissions restricted to 0600. Passwords and API keys must never appear in log files, trace streams, or client API responses.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Verification Method:** Static code security analysis and regex audit on `logs/`

#### NFR-006: Password Hashing Standard
- **Description:** User authentication passwords must be hashed using `bcrypt` with appropriate work factors (minimum 12 rounds). Plaintext passwords must never be persisted or logged.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Verification Method:** Automated authentication unit tests

---

### 3.3 Reliability, Availability & Fault Tolerance

#### NFR-007: Headless DB-Free and Key-Free Bootstrapping
- **Description:** The FastAPI backend must boot cleanly and answer `/health` requests even when no database connection is active and no LLM API key is configured.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Verification Method:** Clean container start test without `.env` variables

#### NFR-008: Transient Database Connection Retry
- **Description:** The database executor pool must retry transient connection drops up to 2 times with exponential backoff, while immediately failing without retry on syntax, semantic, or permission errors.
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Verification Method:** Mocked network failure integration tests

#### NFR-009: Fail-Closed Access Control Architecture
- **Description:** If a security rule, row filter, or user grant evaluation encounters an unhandled exception, syntax parsing error, or ambiguous state, the query must be refused immediately rather than executed unfiltered.
- **Priority:** Must Have
- **Status:** Implemented (Verified L3)
- **Verification Method:** Unit tests passing corrupted SQL to `apply_row_filters`

---

### 3.4 Maintainability & Extensibility

#### NFR-010: Type-Safe Schema Generation
- **Description:** Frontend TypeScript types must be kept in synchronization with backend Pydantic models by generating `schema.d.ts` from OpenAPI specifications (`npm run gen:types`). Hand-editing `schema.d.ts` is strictly prohibited.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5)
- **Verification Method:** CI pre-commit check comparing generated types with git tree

#### NFR-011: Strict Thread-Safe Single-Process Storage Invariant
- **Description:** Because runtime state is stored in flat JSON files (`backend/data/api/*.json`), the server must detect and refuse to start with multi-worker configurations (`--workers > 1`), protecting against race conditions and file corruption.
- **Priority:** Must Have
- **Status:** Implemented (Verified L5: Verified check in `app.py:102-105`)
- **Verification Method:** Automated startup assertion test

---

## 4. Requirements Traceability Matrix

| Requirement ID | Module / Area | Linked Feature | Primary Rule | Linked Test / Verification | Status |
|---|---|---|---|---|---|
| `REQ-001` | Auth | `FEAT-001` | `RULE-011` | `TEST-001` | Implemented |
| `REQ-002` | Auth | `FEAT-002` | `RULE-012` | `TEST-002` | Implemented |
| `REQ-003` | Roles | `FEAT-003` | `RULE-013` | `TEST-001` | Implemented |
| `REQ-004` | Tenancy | `FEAT-004` | `RULE-014` | `TEST-003` | Implemented |
| `REQ-005` | Gateway | `FEAT-005` | `RULE-005` | `TEST-004` | Implemented |
| `REQ-006` | Gateway | `FEAT-005` | `RULE-005` | `TEST-004` | Implemented |
| `REQ-007` | Gateway | `FEAT-006` | `RULE-006` | `TEST-004` | Implemented |
| `REQ-008` | Gateway | `FEAT-007` | `RULE-006` | `TEST-004` | Implemented |
| `REQ-009` | Planner | `FEAT-008` | `RULE-007` | `TEST-005` | Implemented |
| `REQ-010` | Validator | `FEAT-009` | `RULE-001` | `TEST-005` | Implemented |
| `REQ-011` | Validator | `FEAT-009` | `RULE-002` | `TEST-005` | Implemented |
| `REQ-012` | Validator | `FEAT-009` | `RULE-003` | `TEST-005` | Implemented |
| `REQ-013` | Access | `FEAT-010` | `RULE-004` | `TEST-006` | Implemented |
| `REQ-014` | Executor | `FEAT-011` | `RULE-001` | `TEST-007` | Implemented |
| `REQ-015` | Executor | `FEAT-012` | `RULE-015` | `TEST-007` | Implemented |
| `REQ-016` | Executor | `FEAT-011` | `RULE-016` | `TEST-007` | Implemented |
| `REQ-017` | Semantic | `FEAT-013` | `RULE-008` | `TEST-008` | Implemented |
| `REQ-018` | Semantic | `FEAT-013` | `RULE-008` | `TEST-008` | Implemented |
| `REQ-019` | Semantic | `FEAT-014` | `RULE-009` | `TEST-008` | Implemented |
| `REQ-020` | Profiling | `FEAT-015` | `RULE-017` | `TEST-009` | Implemented |
| `REQ-021` | Chat / SSE | `FEAT-016` | `RULE-018` | `TEST-010` | Implemented |
| `REQ-022` | Explorer | `FEAT-017` | `RULE-019` | `TEST-010` | Implemented |
| `REQ-023` | Viz / Charts | `FEAT-018` | `RULE-010` | `TEST-011` | Implemented |
| `REQ-024` | Chat UI | `FEAT-019` | `RULE-020` | `TEST-010` | Partial |
| `REQ-025` | Dashboards | `FEAT-020` | `RULE-021` | `TEST-012` | Implemented |
| `REQ-026` | Widgets | `FEAT-021` | `RULE-022` | `TEST-012` | Implemented |
| `REQ-027` | Dashboards | `FEAT-022` | `RULE-023` | `TEST-012` | Implemented |
| `REQ-028` | Custom Dash | `FEAT-023` | `RULE-024` | `TEST-012` | Implemented |
| `REQ-029` | Routing | `FEAT-024` | `RULE-025` | `TEST-013` | Implemented |
| `REQ-030` | Export | `FEAT-025` | `RULE-026` | `TEST-014` | Implemented |
| `NFR-001` | Performance | System | N/A | Telemetry | Implemented |
| `NFR-002` | Performance | Chat / SSE | N/A | Telemetry | Implemented |
| `NFR-003` | Performance | Frontend | N/A | Lighthouse | Implemented |
| `NFR-004` | Security | DB / Safety | `RULE-001` | Pentest | Implemented |
| `NFR-005` | Security | Config | `RULE-005` | Audit | Implemented |
| `NFR-006` | Security | Auth | `RULE-011` | Unit Test | Implemented |
| `NFR-007` | Reliability | Startup | N/A | Container Test | Implemented |
| `NFR-008` | Reliability | Executor | N/A | Integration | Implemented |
| `NFR-009` | Reliability | Access | `RULE-004` | Unit Test | Implemented |
| `NFR-010` | Maintainability| Tooling | N/A | CI Hook | Implemented |
| `NFR-011` | Concurrency | Storage | N/A | Startup Check | Implemented |

---

## 5. Ambiguous & Contradictory Requirements Analysis

| ID | Original Ambiguous Statement | Conflict / Issue | Measurable Proposed Rewrite |
|---|---|---|---|
| `AMB-001` | "Multi-tenant access control is out of scope for v1 (PlanFiles/01 §4), but implemented in P2.3 (backlog.md)." | PlanFiles/01 states single-user only, but production branch shipped full granular per-user RBAC and row-filter grants. | **Resolved:** System operates with single-server multi-tenancy. Standard users are restricted by `GrantStore` and `bind_connection_for`, while multi-tenant database sharing uses AST row-level predicates. |
| `AMB-002` | "Dashboard filters re-slice data on the client (PlanFiles/06 §7)." | Client-side re-slicing contradicts the trust boundary (row rules wouldn't apply, large datasets wouldn't scale). | **Resolved:** All filter changes trigger `POST /api/widgets/{id}/refresh` or `/api/dashboard/run`, re-entering the server-side validator and re-querying the database under the viewer's security scope. |
| `AMB-003` | "Semantic layer requires 100% upfront human approval (PlanFiles/02 §1.2 vs PlanFiles/10 §3)." | Upfront approval is impossible at 300+ tables; PlanFiles/10 establishes tiered derivation. | **Resolved:** Tier 1 (structure) and Tier 2 (AI descriptions) are automatically generated upon introspection; human curation is reserved for Tier 3 business definitions, non-FK joins, and few-shot examples. |
| `AMB-004` | "Superuser SSH bastion DB access (`POSTREGS_SERVER`)." | A superuser connection eliminates DB-level read-only protections, leaving only the validator. | **Resolved:** Superuser remote profiles are strictly restricted to scripted read-only checks (`cross_engine_check.py`). Production connections must use dedicated read-only database accounts. |
