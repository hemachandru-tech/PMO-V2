# Risk & Gap Register

| Field | Value |
|---|---|
| Project | Tela (NL2SQL Business Intelligence & Reporting Platform) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L5 – Operational (Code Audits, Excel Trackers, Security Reviews, Production Backlog) |
| Sources analyzed | `docs/FlexCap_ToDo.xlsx`, `to-dos/backlog.md`, `CLAUDE.md`, `PlanFiles/03`, `PlanFiles/05`, `backend/app/api/app.py` |
| Related documents | [Architecture](architecture.md) · [Project Summary](project_summary.md) · [Requirements](requirements.md) · [Business Rules](business_rules.md) · [API Catalog](api_catalog.md) · [Data Dictionary](data_dictionary.md) |

---

## 1. Overview and Assessment Framework

This document tracks all identified risks, functional gaps, architectural vulnerabilities, and data quality issues across Tela. Each item is classified by risk domain, evaluated for likelihood and impact, assigned an actionable remediation recommendation (`REC-###`), and mapped to an operational owner.

### Risk Evaluation Matrix

```
       Critical │  RISK-001    RISK-002    RISK-003
                │  (Superuser) (Multi-wrk) (Leak Scan)
         High   │  GAP-001     RISK-004    RISK-005
                │  (Enum Fab)  (SSH Tunnel)(MySQL Debt)
  IMPACT Medium │  GAP-002     GAP-003     RISK-006
                │  (Entity Drop)(Date Conf) (Cold Start)
          Low   │  GAP-004     GAP-005     GAP-006
                │  (Relative Dt)(Ticket Dt) (BPSP Seed)
                └─────────────────────────────────────
                   Low         Medium      High
                             LIKELIHOOD
```

---

## 2. Summary Heat Map & Risk Counts

| Category | Open Items | Critical Impact | High Impact | Medium Impact | Low Impact |
|---|---|---|---|---|---|
| **Security & Privacy** | 4 | 2 | 1 | 1 | 0 |
| **Technical & Architecture** | 5 | 1 | 2 | 2 | 0 |
| **Functional & NL2SQL Quality** | 5 | 0 | 2 | 2 | 1 |
| **Data & Test Hygiene** | 4 | 0 | 1 | 2 | 1 |
| **Operational & Scaling** | 3 | 0 | 1 | 2 | 0 |
| **Total** | **21** | **3** | **7** | **9** | **2** |

---

## 3. Comprehensive Risk & Gap Inventory

### 3.1 Security & Privacy Risks

| Field | Required Content |
|---|---|
| **ID** | `RISK-001` |
| **Category** | Security & Privacy |
| **Description** | **Superuser SSH Remote Database Profile Vulnerability (`POSTREGS_SERVER`).** The remote PostgreSQL server profile connects as a database superuser (`postgres`). At this privilege level, database-level read-only backstops are absent; only application-level code validation protects against accidental DDL/DML. |
| **Evidence** | `CLAUDE.md §3:62-73`, `PlanFiles/03 §3` |
| **Status** | Potential Risk |
| **Likelihood** | Low (Access restricted by policy and script) |
| **Impact** | Critical |
| **Priority** | High |
| **Consequence** | If an injection vulnerability or parser bypass were exploited against this profile, the database could suffer data corruption or drops. |
| **Recommendation** | Link to `REC-001`: Provision a dedicated PostgreSQL read-only database user (`GRANT SELECT ON ALL TABLES IN SCHEMA public TO tela_ro;`) on the remote server and update connection credentials. |
| **Owner** | Database Administrator / DevOps |
| **Verification** | Execute test queries attempting `CREATE TABLE` and verify database-level refusal (`permission denied for schema`). |

---

| Field | Required Content |
|---|---|
| **ID** | `RISK-002` |
| **Category** | Security & Privacy |
| **Description** | **Outbound PII / Column Value Leak via Dashboard Axis Labels.** The scoped exception allowing computed aggregates for dashboard insights (`dashboard_context.py`) permits sending dimension labels to third-party LLMs. In poorly classified columns, high-cardinality names or PII could leak. |
| **Evidence** | `PlanFiles/03 §2.0a:79-82`, `backend/app/dashboard_context.py` |
| **Status** | Potential Risk |
| **Likelihood** | Medium |
| **Impact** | High |
| **Priority** | High |
| **Consequence** | Compliance violation (GDPR / PCI DSS) if customer personal data is included in dimension labels sent to OpenAI/Anthropic. |
| **Recommendation** | Link to `REC-002`: Enforce strict column tagging in the semantic layer (`is_pii: true`) and discard any dimension label from PII-flagged columns before building context in `dashboard_context.py`. |
| **Owner** | Security Lead / Backend Architect |
| **Verification** | Automated regression test passing PII-seeded sample data into `dashboard_context.py` and asserting zero egress. |

---

| Field | Required Content |
|---|---|
| **ID** | `RISK-003` |
| **Category** | Security & Privacy |
| **Description** | **Durable Storage of Plaintext Database Passwords in `connection.json`.** To support automatic connection restoration on server reboot, the backend stores database connection configurations (including passwords) in `backend/data/api/connection.json`. |
| **Evidence** | `docs/ARCHITECTURE.md:109`, `backend/app/api/stores.py:ConnectionStore` |
| **Status** | Confirmed Risk |
| **Likelihood** | Low (Protected by 0600 file permissions and gitignore) |
| **Impact** | High |
| **Priority** | Medium |
| **Consequence** | Host-level filesystem compromise exposes all connected database credentials. |
| **Recommendation** | Link to `REC-003`: Implement symmetric encryption (e.g. `cryptography.fernet`) for stored connection passwords using an encryption key sourced from an environment variable (`TELA_ENCRYPTION_KEY`). |
| **Owner** | Security Architect |
| **Verification** | Inspect `connection.json` on disk to verify password fields are encrypted ciphertext. |

---

| Field | Required Content |
|---|---|
| **ID** | `RISK-004` |
| **Category** | Security & Privacy |
| **Description** | **Widget Refresh Security Scope Gap.** Endpoint `POST /api/widgets/{id}/refresh` re-executes stored SQL with user filters but does not currently resolve the caller's principal or `Scope`, bypassing per-user column and row grants. |
| **Evidence** | `docs/ARCHITECTURE.md:138`, `backend/app/api/routers/widgets.py:102` |
| **Status** | Confirmed Defect |
| **Likelihood** | Medium |
| **Impact** | High |
| **Priority** | High |
| **Consequence** | A non-admin user could refresh a shared widget and receive data from rows or columns restricted by their personal grant. |
| **Recommendation** | Link to `REC-004`: Update `routers/widgets.py:refresh_widget` to inject `deps.get_scope` and pass the caller's scope through `query_guard.guarded_execute`. |
| **Owner** | Senior Backend Engineer |
| **Verification** | Unit test verifying that a non-admin user refreshing a widget with a restrictive row grant receives filtered rows only. |

---

### 3.2 Technical & Architectural Risks

| Field | Required Content |
|---|---|
| **ID** | `RISK-005` |
| **Category** | Technical & Architecture |
| **Description** | **Multi-Worker Concurrency Failure with Flat-File JSON Storage.** The application persists all system state in flat JSON files. Starting uvicorn with `--workers > 1` would cause race conditions, corrupting JSON files. |
| **Evidence** | `backend/app/api/app.py:102-105`, `TELA_V1_BASE_EXPERIENCE.md §16` |
| **Status** | Confirmed Risk (Mitigated by hard startup guard) |
| **Likelihood** | Medium |
| **Impact** | Critical |
| **Priority** | High |
| **Consequence** | The application cannot scale horizontally across multiple worker processes or Kubernetes pods in its current state. |
| **Recommendation** | Link to `REC-005`: In V2 architecture, migrate metadata stores (`UserStore`, `GrantStore`, `ConnectionStore`, `DashboardStore`) to an embedded SQLite or PostgreSQL backend with ACID transactions. |
| **Owner** | Solution Architect |
| **Verification** | Startup test verifying that `uvicorn --workers 2` terminates cleanly with a clear error message. |

---

| Field | Required Content |
|---|---|
| **ID** | `RISK-006` |
| **Category** | Technical & Architecture |
| **Description** | **Unverified Live MySQL and ClickHouse Production Debt.** While PostgreSQL and DuckDB (Excel) are live-verified, MySQL and ClickHouse executors have been verified primarily against mock drivers and test fixtures. |
| **Evidence** | `to-dos/backlog.md:17`, `docs/ARCHITECTURE.md §3.1` |
| **Status** | Technical Debt |
| **Likelihood** | High |
| **Impact** | Medium |
| **Priority** | Medium |
| **Consequence** | Potential dialect mismatch, unhandled driver errors, or session timeout syntax issues on live high-volume ClickHouse/MySQL clusters. |
| **Recommendation** | Link to `REC-006`: Execute the end-to-end test suite against live Dockerized MySQL 8.0 and ClickHouse server instances. |
| **Owner** | QA Lead / Backend Engineer |
| **Verification** | CI/CD pipeline step running `cross_engine_check.py` against live containerized database services. |

---

| Field | Required Content |
|---|---|
| **ID** | `RISK-007` |
| **Category** | Technical & Architecture |
| **Description** | **Connection Cold Start Query Latency.** When a user queries a saved database connection that is not currently active in memory, `ConnectionRegistry` must dynamically build the executor pool and parse the semantic layer, introducing 2–4 second delays. |
| **Evidence** | `TELA_V1_BASE_EXPERIENCE.md §16`, `backend/app/api/routing.py` |
| **Status** | Architectural Bottleneck |
| **Likelihood** | High |
| **Impact** | Medium |
| **Priority** | Medium |
| **Consequence** | Initial question latency spikes when switching databases or after restarting the server. |
| **Recommendation** | Link to `REC-007`: Implement asynchronous warm-up of saved connection executors and cached semantic layer ASTs in the background upon application startup. |
| **Owner** | Backend Engineer |
| **Verification** | Benchmark first-query response time on a non-active connection (< 1.0s target). |

---

### 3.3 Functional & NL2SQL Quality Gaps

| Field | Required Content |
|---|---|
| **ID** | `GAP-001` |
| **Category** | Functional & NL2SQL Quality |
| **Description** | **Confident Wrong Answer: LLM Fabricates Enum and Status Literals.** In supply chain and payments testing, the LLM generated plausible-looking but non-existent status literals (e.g., `status = 'Success'` instead of `status = 'SETTLED'`). |
| **Evidence** | `docs/FlexCap_ToDo.xlsx:Sheet1 (Issue #1)` |
| **Status** | Confirmed Gap |
| **Likelihood** | High |
| **Impact** | High |
| **Priority** | High |
| **Consequence** | Queries execute cleanly without database syntax errors, but return 0 rows or incorrect metrics, misleading business decision-makers. |
| **Recommendation** | Link to `REC-008`: Add a pre-execution value-grounding validator that extracts literal comparison targets and validates them against the distinct values dictionary in `ProfileStore`. |
| **Owner** | AI/NLP Engineer |
| **Verification** | Test case asking for "successful payouts" and verifying the generated SQL contains `status = 'SETTLED'`. |

---

| Field | Required Content |
|---|---|
| **ID** | `GAP-002` |
| **Category** | Functional & NL2SQL Quality |
| **Description** | **Named Entity Silently Dropped After Multi-Turn Follow-Ups.** During conversational follow-up questions, named entities present in initial turns (e.g. `Qantas payable-limit`, `Royal Airlines overdue`) were dropped from the SQL WHERE clause. |
| **Evidence** | `docs/FlexCap_ToDo.xlsx:Sheet1 (Issue #2)` |
| **Status** | Confirmed Gap |
| **Likelihood** | Medium |
| **Impact** | High |
| **Priority** | High |
| **Consequence** | Follow-up queries unintentionally expand scope to company-wide aggregates rather than the specific entity under discussion. |
| **Recommendation** | Link to `REC-009`: Enhance conversation history context injection in `planner.py` to assert that dimension entity filters from prior turns are preserved unless explicitly overridden by the user. |
| **Owner** | AI/NLP Engineer |
| **Verification** | Multi-turn regression suite verifying that an entity filter persists across at least 3 conversational turns. |

---

| Field | Required Content |
|---|---|
| **ID** | `GAP-003` |
| **Category** | Functional & NL2SQL Quality |
| **Description** | **Timestamp Confusion: Audit Creation Date vs Business Event Date.** The planner confused `created_date_time` (database ingestion audit timestamp) with `invoice_raised_date` / `txn_date` (real-world business event date). |
| **Evidence** | `docs/FlexCap_ToDo.xlsx:Sheet1 (Issue #3)` |
| **Status** | Confirmed Gap |
| **Likelihood** | High |
| **Impact** | Medium |
| **Priority** | High |
| **Consequence** | Batch-migrated data reflects artificial monthly spikes corresponding to ingestion days rather than real business activity. |
| **Recommendation** | Link to `REC-010`: Inject explicit semantic layer instructions distinguishing ingestion timestamps from event dates across all temporal tables. |
| **Owner** | Semantic Layer Curator |
| **Verification** | Test case: "Invoices raised in May 2026" generates SQL filtering strictly on `invoice_raised_date`. |

---

| Field | Required Content |
|---|---|
| **ID** | `GAP-004` |
| **Category** | Functional & NL2SQL Quality |
| **Description** | **Relative Date Phrasing Ignored.** Relative phrasing such as "this May" or "last quarter" was occasionally ignored, causing the model to default to the current calendar month. |
| **Evidence** | `docs/FlexCap_ToDo.xlsx:Sheet1 (Issue #4)` |
| **Status** | Confirmed Gap |
| **Likelihood** | Medium |
| **Impact** | Medium |
| **Priority** | Medium |
| **Consequence** | Users asking about historical seasonal performance receive irrelevant current-month data. |
| **Recommendation** | Link to `REC-011`: Add date-template substitution logic in `planner.py` that pre-calculates ISO date ranges for named quarters and past months. |
| **Owner** | Backend Engineer |
| **Verification** | Date test matrix verifying relative expressions across 12 monthly scenarios. |

---

| Field | Required Content |
|---|---|
| **ID** | `GAP-005` |
| **Category** | Functional & NL2SQL Quality |
| **Description** | **Entity Ambiguity with Duplicate Business Names.** When multiple business legal entities share similar names across multiple roles (e.g. a company acting as both Buyer and Supplier), the model guesses a join path instead of asking for clarification. |
| **Evidence** | `docs/FlexCap_ToDo.xlsx:Sheet1 (Issue #6)` |
| **Status** | Confirmed Gap |
| **Likelihood** | Medium |
| **Impact** | Medium |
| **Priority** | Medium |
| **Consequence** | Ambiguous cross-role metrics are returned without informing the user. |
| **Recommendation** | Link to `REC-012`: Implement ambiguity detection in `planner.py` that flags multi-role entities and sets `needs_clarification = true`. |
| **Owner** | Product Manager / NLP Engineer |
| **Verification** | Test case with duplicate entity names verifies user prompt asks for role disambiguation. |

---

### 3.4 Data & Test Hygiene Gaps

| Field | Required Content |
|---|---|
| **ID** | `GAP-006` |
| **Category** | Data & Test Hygiene |
| **Description** | **Empty BPSP Payout and Signatory Test Tables.** In the test environment, tables `payout_request_batch`, `payout_request_record`, and `authorized_signatory_details.expiry` contain sparse or empty data, blocking comprehensive verification of BPSP payout test cases. |
| **Evidence** | `docs/FlexCap_ToDo.xlsx:Sheet1 (Items #9, #10, #11)` |
| **Status** | Confirmed Gap |
| **Likelihood** | High |
| **Impact** | High |
| **Priority** | High |
| **Consequence** | Empty query results mask whether queries generated by the AI would succeed or fail against populated datasets. |
| **Recommendation** | Link to `REC-013`: Seed realistic synthetic records covering varied payout statuses (`PROCESSING`, `SETTLED`, `FAILED`) and signatory expiry dates. |
| **Owner** | QA / Test Engineer |
| **Verification** | Re-run BPSP payout test cases and verify non-zero result sets. |

---

## 4. Remediation Action Plan (Traceable Recommendations)

| Rec ID | Linked Risk/Gap | Proposed Action | Benefit | Priority | Target Milestone |
|---|---|---|---|---|---|
| `REC-001` | `RISK-001` | Provision dedicated read-only remote PostgreSQL role | Eliminates superuser write exposure | P0 (Must Have)| Immediate |
| `REC-002` | `RISK-002` | Enforce semantic layer PII column tagging | Prevents outbound PII leakage in dashboard labels | P0 (Must Have)| Sprint 6 |
| `REC-003` | `RISK-003` | Add AES/Fernet encryption for stored DB passwords | Hardens credentials at rest | P1 (Should Have)| Sprint 6 |
| `REC-004` | `RISK-004` | Add `get_scope` validation to `/api/widgets/{id}/refresh` | Resolves row-filter bypass in widget refreshes | P0 (Must Have)| Immediate |
| `REC-005` | `RISK-005` | Migrate JSON flat-file stores to SQLite / PostgreSQL | Enables horizontal scalability and ACID safety | P2 (Could Have)| V2 Architecture |
| `REC-006` | `RISK-006` | Run automated test suite against live MySQL/ClickHouse | Clears unverified database engine technical debt | P1 (Should Have)| Sprint 6 |
| `REC-007` | `RISK-007` | Pre-warm connection pools and semantic layers on boot | Eliminates 2-4 second query cold starts | P1 (Should Have)| Sprint 7 |
| `REC-008` | `GAP-001` | Add distinct-value grounding check before query approval | Eliminates fabricated status enum literals | P0 (Must Have)| Sprint 6 |
| `REC-009` | `GAP-002` | Assert entity preservation across conversation turns | Fixes entity drop in multi-turn chat | P0 (Must Have)| Sprint 6 |
| `REC-010` | `GAP-003` | Inject semantic directives distinguishing ingestion vs event dates | Prevents temporal aggregation errors | P0 (Must Have)| Sprint 6 |
| `REC-011` | `GAP-004` | Add deterministic date-template substitution in planner | Resolves relative date phrasing ambiguities | P1 (Should Have)| Sprint 7 |
| `REC-012` | `GAP-005` | Add clarification gate for multi-role entities | Eliminates silent guessing on duplicate names | P1 (Should Have)| Sprint 7 |
| `REC-013` | `GAP-006` | Seed synthetic test data for BPSP payouts and signatories | Unblocks comprehensive QA testing | P0 (Must Have)| Immediate |
