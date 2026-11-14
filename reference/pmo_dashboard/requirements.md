# Requirements Catalog: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Document status | In review |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L3 (Implemented static source code) with L4 (Configuration) and L1/L2 (Specifications) |
| Sources analyzed | 25 sources (codebase, `.env`, DOCX research/sprint plans, XLSX field sheets, MySQL dump) |
| Related documents | [Project Summary](project_summary.md), [Architecture](architecture.md), [Business Rules](business_rules.md), [API Catalog](api_catalog.md), [Data Dictionary](data_dictionary.md), [Risk Register](risk_register.md) |

---

## 1. Document Overview

This document provides the formal requirements baseline for the **PMO Intelligence & Analytics** platform. Requirements are organized by functional domain, platform capability, and quality attributes (non-functional requirements).

### Traceability Prefixes
- `REQ-###`: Functional Requirement
- `NFR-###`: Non-Functional Requirement
- `BUS-###`: Business Objective
- `FEAT-###`: System Feature
- `RULE-###`: Governed Business Rule / Calculation

### Priority Classification (MoSCoW)
- **Must Have (M)**: Critical for baseline operational integrity, governance compliance, or security gating.
- **Should Have (S)**: High-value operational requirement; required for general production readiness.
- **Could Have (C)**: Desirable feature for enhanced usability or advanced analytics.
- **Won't Have (This Release) (W)**: Explicitly deferred (e.g., reports requiring absent telemetry data).

---

## 2. Table of Contents
- [1. Document Overview](#1-document-overview)
- [2. Table of Contents](#2-table-of-contents)
- [3. Functional Requirements: Core Workspace & Navigation](#3-functional-requirements-core-workspace--navigation)
- [4. Functional Requirements: Governed Metrics & Reporting](#4-functional-requirements-governed-metrics--reporting)
- [5. Functional Requirements: Ask TELA Natural Language Querying](#5-functional-requirements-ask-tela-natural-language-querying)
- [6. Functional Requirements: Custom Reports & Widget Pinning](#6-functional-requirements-custom-reports--widget-pinning)
- [7. Functional Requirements: Demand Pipeline & Request Management](#7-functional-requirements-demand-pipeline--request-management)
- [8. Functional Requirements: Data Scope, Filtering & Access Gating](#8-functional-requirements-data-scope-filtering--access-gating)
- [9. Non-Functional Requirements](#9-non-functional-requirements)
- [10. Ambiguous and Unmeasurable Requirements with Proposed Rewrites](#10-ambiguous-and-unmeasurable-requirements-with-proposed-rewrites)

---

## 3. Functional Requirements: Core Workspace & Navigation

| ID | Module / Area | Description | Priority | Evidence / Status | Source Location | Linked IDs |
|---|---|---|---|---|---|---|
| `REQ-001` | Workspace Navigation | The system must provide dual distinct workspace views: "Projects" (in-flight portfolio delivery) and "Requests" (demand pipeline intake), switchable via a global sidebar toggle. | Must | Implemented & Verified | `frontend/src/components/Sidebar.jsx`, `frontend/src/navigation.js` | `FEAT-001`, `BUS-001` |
| `REQ-002` | As-Of Date Synchronization | Every metric calculation, aging duration, and horizon check must evaluate against a centralized server-provided `asOf` date rather than the client browser clock. | Must | Implemented & Verified | `backend/main.py:21`, `backend/health.py:41`, `frontend/src/lib/useAsOf.js` | `RULE-001`, `FEAT-002` |
| `REQ-003` | Global Filter Bar | The UI must offer a multi-dimensional global filter bar covering Region, Portfolio, Project Manager (Delivery Manager), Priority, and Platform. | Must | Implemented & Verified | `frontend/src/components/FilterBar.jsx`, `backend/main.py:168` | `FEAT-003`, `RULE-002` |
| `REQ-004` | Filter Population Constraints | Filter selections must only narrow data within the caller's authorized scope and must never widen access beyond the deployment-pinned CIO. | Must | Implemented & Verified | `backend/main.py:125-131`, `backend/tela_scope.py:45` | `FEAT-003`, `NFR-005` |
| `REQ-005` | Disabled Report Guarding | Any report whose required underlying data does not exist in the source schema (e.g., Portfolio Trends, Status Reporting, Schedule Compliance, Mandatory Fields, Milestones, Critical Path, RAID, Timesheets) must be disabled in navigation and guarded against direct URL execution. | Must | Implemented & Verified | `frontend/src/navigation.js:215-256`, `frontend/src/components/ReportUnavailable.jsx` | `FEAT-004`, `BUS-004` |
| `REQ-006` | Project Detail Drawer | The system must allow clicking any project across dashboard tables to open a slide-over Project Drawer showing full metadata, financial breakdown, schedule variance, governance status, and calculated health diagnostics. | Should | Implemented & Verified | `frontend/src/components/ProjectDrawer.jsx`, `backend/projects.py:35` | `FEAT-005` |

---

## 4. Functional Requirements: Governed Metrics & Reporting

| ID | Module / Area | Description | Priority | Evidence / Status | Source Location | Linked IDs |
|---|---|---|---|---|---|---|
| `REQ-007` | Single Metric Truth | All dashboard screens, project drawers, and natural language query handlers must derive metrics from a single canonical backend module (`metrics.py` / `health.py`) without duplicating predicates in frontend code. | Must | Implemented & Verified | `backend/metrics.py`, `backend/health.py`, `Docs/CANONICAL_METRICS.md` | `RULE-003`, `BUS-002` |
| `REQ-008` | Reported Status vs Calculated Health Separation | The system must explicitly distinguish between "Reported Status" (subjective RAG color recorded by the PM) and "Calculated Health" (objective 4-factor weighted score) across all UI surfaces and payloads. | Must | Implemented & Verified | `backend/health.py:1-16`, `backend/metrics.py:14-25`, `Docs/CANONICAL_METRICS.md` | `RULE-004`, `BUS-003` |
| `REQ-009` | Health Scoring Coverage Gating | The calculated health model must require at least 60% evidence coverage (`MIN_COVERAGE = 0.60`). If evidence is below 60%, the project status must be classified as "Insufficient data" rather than calculating an artificial score. | Must | Implemented & Verified | `backend/health.py:27-34, 263-273` | `RULE-004`, `FEAT-006` |
| `REQ-010` | Severe Condition Health Overrides | The calculated health engine must automatically override a calculated Green/Amber status to Red if Schedule score is 0, Issue KPI is Red, or Cost score is 0. A Cost score of 25 must cap overall status at Amber. | Must | Implemented & Verified | `backend/health.py:281-290` | `RULE-004` |
| `REQ-011` | Objective Overdue Go-Live | The system must flag projects as "Overdue go-live" based strictly on dates: current state Active, planned deployment date strictly before `asOf`, and actual deployment date null. Projects in "On Hold" or "Project Created" must not be flagged. | Must | Implemented & Verified | `backend/metrics.py:26-30`, `backend/health.py:130-142` | `RULE-005` |
| `REQ-012` | Schedule KPI Contradiction Flag | The system must identify data quality discrepancies where a project's reported Schedule KPI is Green but it is 15+ days past planned go-live, or Red while planned go-live is 30+ days away. | Should | Implemented & Verified | `backend/metrics.py:54-58` | `RULE-006` |
| `REQ-013` | Financial Exposure & Forecast Truth | The system must treat `cost_total_usd` strictly as Forecast Cost / Estimate at Completion (EAC) and compare it against `approved_budget_value_usd`. The system must never label this figure as "actual spend". | Must | Implemented & Verified | `backend/metrics.py:76-82, 133-160`, `Docs/CANONICAL_METRICS.md:69-91` | `RULE-007`, `NFR-008` |
| `REQ-014` | On-Hold Aging Horizon | Projects and requests in "On Hold" state must be aged objectively in days from `on_hold_date` to `asOf`. Records missing `on_hold_date` must be flagged as "missing date" rather than assigned 0 days. | Should | Implemented & Verified | `backend/metrics.py:25`, `backend/requests.py:210` | `RULE-008` |
| `REQ-015` | Upcoming Delivery Horizons | The Delivery dashboard must segment upcoming project go-lives into non-cumulative horizon buckets: 0–30 days, 31–60 days, and 61–90 days based on `planned_deployment_date`. | Should | Implemented & Verified | `backend/metrics.py:86-106`, `backend/delivery.py` | `RULE-009` |

---

## 5. Functional Requirements: Ask TELA Natural Language Querying

| ID | Module / Area | Description | Priority | Evidence / Status | Source Location | Linked IDs |
|---|---|---|---|---|---|---|
| `REQ-016` | Dual-Path NL Routing | Natural language questions submitted to `POST /api/ask-tela` must be routed deterministically: matching certified intents are handled by governed handlers; all other questions proceed to the exploratory LLM pipeline. | Must | Implemented & Verified | `backend/tela_service.py:120-170`, `backend/talk_to_data.py` | `FEAT-007`, `BUS-005` |
| `REQ-017` | Trust Classification Labels | Every NL query answer must return a visible trust classification: "Certified PMO metric — the same figure the dashboards show" or "Exploratory — not an official PMO metric". | Must | Implemented & Verified | `backend/tela_service.py:180`, `backend/tela_viz.py:45` | `FEAT-008`, `NFR-009` |
| `REQ-018` | Certified Fail-Closed Behavior | If a certified metric handler encounters an error or missing data, it must return a failure or explicit reason card and must never fall through to the exploratory SQL generator. | Must | Implemented & Verified | `backend/tela_service.py:145-155` | `FEAT-007`, `NFR-006` |
| `REQ-019` | Structured LLM Planning | The exploratory planner must prompt the configured LLM provider (Anthropic, OpenAI, or Gemini) to output a structured plan via tool calling (`submit_plan`) containing SQL query, clarification request, or unsupported notification. | Must | Implemented & Verified | `backend/tela_planner.py:80-140`, `backend/chat_providers.py` | `FEAT-009` |
| `REQ-020` | SQL AST Validation Gate | All exploratory SQL generated by the model must be validated using AST parsing (`sqlglot`). The validator must enforce a single statement, read-only SELECT, no schema-qualification, no comments, no subqueries into unapproved tables, and no disallowed functions. | Must | Implemented & Verified | `backend/tela_validator.py:35-110` | `FEAT-010`, `NFR-007` |
| `REQ-021` | Schema Table & Column Allow-List | The SQL generator and validator must enforce an explicit allow-list restricting queries exclusively to `projects` and `project_requests` tables, and only permitted columns. Sensitive/personal fields must be omitted. | Must | Implemented & Verified | `backend/tela_schema.py:1-110`, `backend/tela_validator.py:115` | `FEAT-010`, `NFR-004` |
| `REQ-022` | Automatic Scope Rewriting | Every governed table reference in exploratory SQL must be automatically rewritten into a scoped derived table: `(SELECT * FROM projects WHERE cio = :pinned_cio AND <filters>)`. | Must | Implemented & Verified | `backend/tela_scope.py:35-85` | `FEAT-011`, `NFR-005` |
| `REQ-023` | Read-Only Account Verification Gate | Exploratory query execution must fail-closed unless the database connection account's real grants (`SHOW GRANTS`) are verified to contain strictly `USAGE ON *.*` and `SELECT` on approved tables. | Must | Implemented & Verified | `backend/tela_executor.py:90-180` | `FEAT-012`, `NFR-001` |
| `REQ-024` | Execution Clamping & Timeouts | Exploratory queries must be enforced with a hard row limit (maximum 200 rows) and an execution timeout (maximum 15 seconds) using server-side session controls. | Must | Implemented & Verified | `backend/tela_executor.py:210-240` | `NFR-002`, `NFR-003` |
| `REQ-025` | Adaptive Visualization Formatting | Query results must be analyzed automatically to select optimal visualization: single aggregate → KPI card; categorical comparison → Bar chart; date/time series → Line chart; records/multi-column → Table. | Should | Implemented & Verified | `backend/tela_viz.py:110-180`, `frontend/src/components/talk/` | `FEAT-013` |
| `REQ-026` | Factual Natural Language Summaries | Answer explanation sentences must be strictly synthesized from returned result rows (row counts, extrema, totals) without model speculation or hallucination. | Should | Implemented & Verified | `backend/tela_viz.py:190-230` | `FEAT-013` |
| `REQ-027` | Contextual Follow-Up Suggestions | For every successful query, the system must generate syntactically complete, contextual follow-up questions tailored to the result shape. | Could | Implemented & Verified | `backend/tela_viz.py:240-280` | `FEAT-014` |

---

## 6. Functional Requirements: Custom Reports & Widget Pinning

| ID | Module / Area | Description | Priority | Evidence / Status | Source Location | Linked IDs |
|---|---|---|---|---|---|---|
| `REQ-028` | Pin to Custom Reports | Users must be able to pin eligible Ask TELA query answers (both certified metrics and exploratory results) directly to an existing custom dashboard or a newly created dashboard. | Must | Implemented & Verified | `backend/custom_reports.py:260-310`, `frontend/src/components/customReports/` | `FEAT-015`, `BUS-006` |
| `REQ-029` | Server-Side Opaque Pin Tokens | Pinned definitions must be issued as short-lived, opaque server-side memory tokens (TTL 1 hour). The browser client must never receive or submit raw SQL. | Must | Implemented & Verified | `backend/custom_reports.py:53, 265`, `backend/tela_service.py:210` | `FEAT-015`, `NFR-007` |
| `REQ-030` | Duplicate Pin Detection | The system must prevent duplicate widgets on the same report. Attempting to pin an identical definition to the same dashboard must return HTTP 409 Conflict. | Should | Implemented & Verified | `backend/custom_reports.py:285-295` | `FEAT-015` |
| `REQ-031` | Live Re-Execution by Default | Pinned widgets displayed on custom dashboards must re-execute dynamically on load against current data and current server scope. | Must | Implemented & Verified | `backend/custom_reports.py:440-495`, `backend/tela_service.py:260` | `FEAT-016` |
| `REQ-032` | Snapshot Fallback Degradation | If live re-execution fails (e.g., exploratory gate disabled or upstream database timeout), the widget must display its saved snapshot with an explicit "Snapshot" badge and timestamp. | Must | Implemented & Verified | `backend/custom_reports.py:475-490`, `frontend/src/components/customReports/WidgetCard.jsx` | `FEAT-016`, `NFR-003` |
| `REQ-033` | Dashboard Layout & Arrangement | Custom dashboards must support re-ordering widgets, toggling span width (half-width / full-width), editing widget titles, and switching visualization modes (auto, table, bar, line, kpi). | Should | Implemented & Verified | `backend/custom_reports.py:350-410`, `frontend/src/components/customReports/CustomDashboard.jsx` | `FEAT-017` |
| `REQ-034` | Official Dashboard Isolation | Custom Reports operations must be strictly restricted to custom dashboards; pinning or modifying official PMO dashboards (Executive, Governance, Delivery, Finance, Flow) must be programmatically impossible. | Must | Implemented & Verified | `backend/custom_reports.py:8, 280`, `backend/tests/test_custom_reports.py` | `FEAT-018`, `NFR-006` |
| `REQ-035` | SQLite Persistence Isolation | Custom Reports metadata, dashboards, widgets, and layouts must be persisted in an isolated SQLite database (`data/custom_reports.sqlite3`), completely separated from the PMO MySQL database. | Must | Implemented & Verified | `backend/custom_reports.py:48-89` | `FEAT-019`, `COMP-003` |

---

## 7. Functional Requirements: Demand Pipeline & Request Management

| ID | Module / Area | Description | Priority | Evidence / Status | Source Location | Linked IDs |
|---|---|---|---|---|---|---|
| `REQ-036` | Request Lifecycle Tracking | The Requests workspace must track demand records across states (`New`, `Active`, `On Hold`, `Return to Requester`, `Approved`, `Rejected`, `Dropped`). | Must | Implemented & Verified | `backend/requests.py:45-95`, `frontend/src/components/requests/` | `FEAT-020` |
| `REQ-037` | Overdue Governance Gates (DOU, DARB, SOW) | The system must calculate overdue governance gates on open requests for DOU (planned date passed & unissued), DARB (review date passed & unapproved), and SOW (planned date passed & unissued). | Must | Implemented & Verified | `backend/metrics.py:67-74`, `backend/requests.py:270-310` | `RULE-010`, `FEAT-021` |
| `REQ-038` | Demand Conversion Tracking | The system must calculate conversion rates of project requests into approved delivery projects by tracking `converted_count` and linking `project_request_id` to `projects`. | Should | Implemented & Verified | `backend/requests.py:150-180`, `frontend/src/components/requests/DemandConversion.jsx` | `FEAT-022` |
| `REQ-039` | Stage 1 Discovery Schedule | The system must track discovery schedule variances for requests by comparing `stage1_discovery_actual_end_date` against `stage1_discovery_planned_end_date`. | Should | Implemented & Verified | `backend/requests.py:185-205` | `RULE-011`, `FEAT-023` |
| `REQ-040` | Commercial Demand Effort & Cost | The system must summarize requested commercial metrics: internal/external mandays, setup revenue, recurring revenue, and total estimated cost (EAC) per request. | Should | Implemented & Verified | `backend/requests.py:220-250` | `FEAT-024` |

---

## 8. Functional Requirements: Data Scope, Filtering & Access Gating

| ID | Module / Area | Description | Priority | Evidence / Status | Source Location | Linked IDs |
|---|---|---|---|---|---|---|
| `REQ-041` | Server-Side CIO Scoping | When `DEMO_CIO` is configured in the environment, all queries (dashboards, overview, details, certified metrics, exploratory SQL, and custom report refreshes) must be scoped to that CIO on the server. | Must | Implemented & Verified | `backend/main.py:22, 127`, `backend/tela_scope.py:20` | `FEAT-025`, `NFR-005` |
| `REQ-042` | Client CIO Override Invalidation | The backend must actively ignore and strip any client-supplied CIO parameter when `DEMO_CIO` is active. | Must | Implemented & Verified | `backend/main.py:127-130`, `backend/tela_adapter.py:35` | `FEAT-025`, `NFR-005` |
| `REQ-043` | Custom Reports Principal Isolation | Custom report retrieval, creation, modification, and deletion must filter strictly by the caller's principal (`cio:<DEMO_CIO>` or `anonymous`). Cross-principal access must return HTTP 404. | Must | Implemented & Verified | `backend/custom_reports.py:62-67, 131-134` | `FEAT-026`, `NFR-005` |
| `REQ-044` | Grant Cache Window Limitation | The database grant verification cache must not exceed 60 seconds. Any privilege revoked at the database level must take effect immediately. | Should | Implemented & Verified | `backend/tela_executor.py:55-65` | `NFR-001` |
| `REQ-045` | Static Asset Single-Origin Serving | When `frontend/dist/index.html` exists, FastAPI must serve the built SPA bundle and static assets directly as a catch-all route, enabling single-origin production deployment. | Should | Implemented & Verified | `backend/main.py:540-555` | `COMP-001`, `COMP-002` |

---

## 9. Non-Functional Requirements (NFRs)

| ID | Category | Requirement Description | Target Metric / Threshold | Verification Method | Status |
|---|---|---|---|---|---|
| `NFR-001` | Security: Least Privilege | Exploratory database connections must be restricted to verified read-only accounts (`SHOW GRANTS` == `USAGE ON *.*` + `SELECT` on 2 tables). | 0 write privileges permitted; fail-closed | Automated inspection in `test_tela_native.py` | Verified |
| `NFR-002` | Performance: Query Timeout | Server-side database execution for exploratory queries must enforce a hard statement timeout. | Maximum 15 seconds | Session `MAX_EXECUTION_TIME = 15000` | Verified |
| `NFR-003` | Reliability: Resource Clamping | Exploratory queries and report exports must enforce a maximum row ceiling to prevent client/server memory exhaustion. | Maximum 200 rows per query | Injected `LIMIT 200` via AST parser | Verified |
| `NFR-004` | Privacy: Data Minimization | Personally identifiable information (PII), free-text commentary, customer contact details, and document URLs must be excluded from LLM context and query schemas. | 100% exclusion of disallowed columns | Allow-list verification in `tela_schema.py` | Verified |
| `NFR-005` | Security: Tenancy Isolation | Multi-tenancy must be enforced at the data layer using mandatory server-side predicate injection. | 0 cross-CIO data leakage | Derived table rewriting in `tela_scope.py` | Verified |
| `NFR-006` | Integrity: Governed Metrics | Official dashboard figures and certified natural language answers must match 100% when evaluated under identical filter scopes and dates. | 0 variance between surfaces | Metric parity tests (`test_metric_consistency.py`) | Verified |
| `NFR-007` | Security: SQL Injection Defense | Natural language query execution must never execute unparsed, unvalidated raw SQL strings. | 100% AST validation via `sqlglot` | Validator test suite (`test_tela_native.py`) | Verified |
| `NFR-008` | Transparency: Data Semantics | All cost metrics must be explicitly labelled as Forecast Cost / EAC, with tooltips stating that actual spend records do not exist. | 100% coverage across UI tiles and charts | UI inspection & `frontend/src/lib/definitions.js` | Verified |
| `NFR-009` | Usability: Trust Attribution | All system outputs must display unambiguous trust metadata: Certified (official), Exploratory (ad-hoc), or Snapshot (cached). | Present on 100% of answer cards & widgets | Visual inspection & component contracts | Verified |
| `NFR-010` | Availability: Graceful Degradation | Upstream failures in LLM providers, database timeouts, or exploratory gate locks must not crash dashboard rendering. | 100% isolated error cards & snapshot fallbacks | Error boundary & mock test validation | Verified |
| `NFR-011` | Maintainability: Modular Separation | Custom Reports storage (SQLite) and business metric computation must remain decoupled from core MySQL delivery queries. | 0 imports of dashboard modules in `custom_reports.py` | Static code analysis | Verified |
| `NFR-012` | Auditability: Operations Logging | Administrative, execution, and security gating failures must be logged with structured error codes without exposing SQL or credentials. | No credentials or raw SQL in error payloads | Exception handling in `tela_executor.py` | Verified |
| `NFR-013` | Concurrency: Rate Limiting | The natural language query endpoint must prevent runaway concurrent LLM/SQL invocations. | Maximum 4 concurrent requests in flight; HTTP 429 | Semaphore in `tela_adapter.py` | Verified |
| `NFR-014` | Portability: Cross-Platform DB | Database abstraction must allow seamless migration from MySQL to Azure PostgreSQL Flexible Server. | Standard ANSI SQL dialect compatibility | Architecture review & sprint plans | Proposed |

---

## 10. Ambiguous and Unmeasurable Requirements with Proposed Rewrites

| Original Ambiguous Requirement | Source | Defect / Problem | Proposed Measurable Rewrite | Status |
|---|---|---|---|---|
| "The system shall display overall project health." | `Docs/Slides 0-5.md` | Ambiguous whether health refers to self-reported PM RAG or objective algorithmic score. | "The system shall present two distinct indicators: (1) `Reported Status` reflecting the PM's recorded `overall_kpi`, and (2) `Calculated Health` derived from the weighted 4-factor formula (Schedule 35%, Cost 25%, Progress 25%, Issues 15%) requiring $\ge 60\%$ evidence coverage." | Adopted in `health.py` |
| "The platform should show project spend and budget remaining." | Early PRD / Notes | Untrue to the schema: no actual spend or invoice tables exist in TPG synthetic data. | "The platform shall compare `Approved Budget (USD)` against `Forecast Cost (EAC)` derived from `cost_total_usd`. It shall explicitly indicate that actual spend, burn rate, and budget-vs-actual are unavailable." | Adopted in `metrics.py` |
| "Highlight delayed projects across the portfolio." | Legacy PMO Spec | Unclear definition: subjective PM Schedule KPI vs objective calendar due date. | "The platform shall retire the ambiguous term 'Delayed'. It shall provide: (1) `Past Due (Overdue Go-Live)` for active projects where `planned_deployment_date < asOf` and `actual_deployment_date IS NULL`, and (2) `Schedule Risk` for open projects with `schedule_kpi IN ('Red', 'Yellow')`." | Adopted in `metrics.py` |
| "Users should be able to ask anything about their portfolio." | Marketing / Vision Slides | Unbounded natural language expectation; could imply arbitrary DDL/DML, full semantic reasoning, or open database access. | "The system shall allow natural language queries over an allow-list of 41 columns across `projects` and `project_requests`. Certified intents shall map to governed handlers; exploratory queries shall generate a single SELECT with max 200 rows and 15s timeout." | Adopted in `tela_planner.py` |
| "Reports should reflect real-time team status." | Presentation Slides | Unmeasurable: team status reports do not exist; `modified_on` updates on any row edit. | "The platform shall calculate `Stale Projects` as active in-delivery projects whose `modified_on` timestamp is $> 30$ days prior to `asOf`. The `Status Reporting Compliance` report shall remain disabled until a status submission log is provisioned." | Adopted in `navigation.js` |
