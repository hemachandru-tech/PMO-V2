# Risk & Gap Register: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L3 (Implemented static source code & test results) with L4 (Configuration) |
| Sources analyzed | `README.md:509-660`, `backend/`, `Docs/Scalability Notes.docx`, `Docs/PMO Application-Infra,Auth.docx` |
| Related documents | [Project Summary](project_summary.md), [Architecture](architecture.md), [Requirements](requirements.md), [Business Rules](business_rules.md), [API Catalog](api_catalog.md), [Data Dictionary](data_dictionary.md) |

---

## 1. Overview & Risk Assessment Framework

This register tracks all identified technical deficiencies, architecture gaps, security exposures, and operational risks across the PMO Intelligence & Analytics platform.

### Evaluation Criteria
- **Likelihood**: Low (rare/difficult to provoke), Medium (possible in normal usage), High (expected in standard operation).
- **Impact**: Low (minor inconvenience), Medium (degraded feature or workflow), High (loss of capability or data integrity), Critical (security compromise or systemic failure).
- **Status Classification**:
  - **Confirmed Gap**: A requirement or capability demonstrably absent from the code.
  - **Documentation Gap**: Information absent from documentation, though possibly present in external environments.
  - **Potential Risk**: An architectural condition that could cause failure under specific conditions.
  - **Unverified Concern**: A reported issue requiring runtime re-validation.

---

## 2. Risk & Gap Summary Matrix

| Category | Total Issues | Critical Impact | High Impact | Medium Impact | Low Impact |
|---|---|---|---|---|---|
| Security & Privacy | 3 | 1 | 2 | 0 | 0 |
| Data & Reporting | 2 | 0 | 2 | 0 | 0 |
| Technical Architecture | 3 | 0 | 1 | 2 | 0 |
| Operational & Infrastructure | 3 | 0 | 2 | 1 | 0 |
| Functional & Testing | 3 | 0 | 1 | 2 | 0 |
| **Total** | **14** | **1** | **8** | **5** | **0** |

---

## 3. Comprehensive Risk & Gap Inventory

| ID | Category | Title & Specific Description | Evidence | Status | Likelihood | Impact | Priority | Consequence | Concrete Recommendation | Owner | Verification Method |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `RISK-001` | Security | **Absence of User Authentication & Multi-User RBAC**: The platform has no user login, JWT tokens, session cookies, or password mechanisms. Tenancy identity is pinned at deployment level via `DEMO_CIO`. | `backend/main.py:22`, `README.md:537-538`, `Docs/PMO Application-Infra,Auth.docx` | Confirmed Gap | High | Critical | Must (P0) | All users reaching the application share the identical CIO identity, dashboard permissions, and custom reports. True individual data privacy is impossible. | Integrate Microsoft Entra ID (Azure AD) with OAuth2/OIDC Bearer tokens (`REC-001`). Extract user principal and RBAC groups from JWT claims. | Security Architect / Tech Lead | Penetration test & multi-user session verification |
| `RISK-002` | Security | **Shared MySQL Database Account for Dashboards & Natural Language Execution**: In default local configuration, `DB_USER` is itself the read-only account, sharing a single credential set across core dashboards and exploratory TELA queries. | `.env:4`, `README.md:539`, `backend/tela_executor.py:120` | Potential Risk | Medium | High | Must (P1) | If a DBA erroneously grants write privileges to `DB_USER`, the exploratory TELA execution boundary is weakened (reliant solely on AST validation). | Mandate separate database credentials (`PMO_RO_DB_USER` / `PMO_RO_DB_PASSWORD`) with verified read-only grants on dedicated connection pool (`REC-002`). | Lead DBA / Backend Eng | Database grant audit script & integration tests |
| `RISK-003` | Security | **60-Second Grant Verification Cache Window**: `ReadOnlyExecutor` caches the result of `SHOW GRANTS` for 60 seconds to avoid per-query database overhead. | `backend/tela_executor.py:55-65`, `README.md:540` | Potential Risk | Low | High | Should (P2) | If an account is granted write or admin privileges, TELA will continue executing queries under that account for up to 60 seconds before noticing. | Reduce TTL to 15 seconds, or execute session-level `SET SESSION TRANSACTION READ ONLY` unconditionally on every checkout (`REC-003`). | Backend Eng | Automated grant change invalidation test |
| `RISK-004` | Operational | **SQLite Single-File Storage for Custom Reports**: Custom dashboards, widgets, and layout positions are persisted in a local SQLite file (`data/custom_reports.sqlite3`). | `backend/custom_reports.py:48-89`, `data/custom_reports.sqlite3` | Potential Risk | High | High | Must (P1) | Containerized cloud deployments (e.g. Azure Container Apps / App Service) will lose all custom reports upon pod restarts or horizontal scaling unless persistent volumes are mapped. | Migrate Custom Reports store to relational PostgreSQL flexible server or Azure Cosmos DB (`REC-004`). | Cloud Architect / DevOps | Multi-instance failover and horizontal scaling tests |
| `RISK-005` | Data Integrity | **Absence of Actual Spend, Invoice & Timesheet Data**: Source schema holds only `cost_total_usd` (EAC forecast) and `approved_budget_value_usd`. There is no actual cost, ledger, invoice, or timesheet data. | `data/tpg_synthetic_mysql.sql`, `backend/metrics.py:76`, `Docs/CANONICAL_METRICS.md:69` | Confirmed Gap | High | High | Must (P1) | Users or executives may mistakenly interpret "Forecast Cost" as historical spend, leading to inaccurate financial governance decisions. | Preserve explicit disclaimers in UI tooltips, decline actual spend NL queries (`x_actual_spend`), and document schema prerequisites for financial integration (`REC-005`). | Product Owner / PMO Lead | UI audit & definitions verification (`definitions.js`) |
| `RISK-006` | Data Integrity | **Absence of Historical Portfolio Snapshots**: The database stores current-state records with creation and modification timestamps, but no point-in-time portfolio history. | `frontend/src/navigation.js:216`, `backend/main.py:98` | Confirmed Gap | Medium | High | Should (P2) | Portfolio trend analysis over time (e.g. historical S-curves, monthly burn, velocity) cannot be reconstructed truthfully. | Implement daily/weekly snapshot archiving table or Event Sourcing audit log in the data ingestion pipeline (`REC-006`). | Data Eng / DBA | Historical backfill and trend report test |
| `RISK-007` | Functional / QA | **22 Scope-Sensitive Backend Tests Failing Under Pinned CIO**: 22 unit/integration tests in `backend/tests/` expect unscoped whole-database counts (e.g. 100 projects) and fail when `DEMO_CIO` is active (25 projects). | `README.md:594, 598-600` | Confirmed Gap | High | Medium | Should (P2) | CI/CD pipelines cannot run cleanly with a pinned test configuration without masking real regression failures. | Refactor test fixtures to run in isolated mock contexts or parameterize assertions to evaluate against the active scope (`REC-007`). | QA Lead / Backend Eng | Full `pytest` suite execution passing 100% |
| `RISK-008` | Technical | **Missing Python Dependencies in Local Environment**: Optional provider dependencies (e.g., `anthropic`) fail unit tests when executed in an incomplete virtual environment. | `README.md:600`, `backend/requirements.txt` | Confirmed Gap | Medium | Low | Should (P3) | Developer friction and confusing test output during onboarding or container builds. | Harmonize `requirements.txt` and `requirements-dev.txt`, and bundle automated pre-commit environment validation (`REC-008`). | Tech Lead | Clean virtualenv installation and pytest run |
| `RISK-009` | Technical | **Shadowing of Python Standard / PyPI Package Names**: The module `backend/requests.py` shadows the widely-used PyPI `requests` HTTP library name. | `backend/requests.py`, `README.md:679` | Potential Risk | Low | Medium | Could (P3) | Third-party libraries (e.g., LangSmith, pytest plugins) that import `requests` crash on startup by picking up the local report router instead. | Rename `backend/requests.py` to `backend/demand_requests.py` across backend imports and router registrations (`REC-009`). | Backend Eng | Pytest execution without `PYTEST_DISABLE_PLUGIN_AUTOLOAD` |
| `RISK-010` | Operational | **Large Monolithic Frontend Bundle**: Frontend builds into a single JavaScript bundle exceeding 1.2 MB without code splitting. | `frontend/dist/`, `README.md:373, 595` | Potential Risk | Medium | Medium | Should (P2) | Slower Largest Contentful Paint (LCP) and initial load times for executives on mobile or constrained networks. | Implement dynamic route-level lazy loading (`React.lazy()` / `Suspense`) in `App.jsx` (`REC-010`). | Frontend Lead | Vite build output chunk analysis ($< 500$ KB) |
| `GAP-001` | Functional | **8 Disabled Dashboards Due to Absent Source Data**: Eight standard PMO dashboards (Portfolio Trends, Status Reporting, Schedule Compliance, Mandatory Fields, Milestones, Critical Path, RAID, Timesheets) are commented out in navigation. | `frontend/src/navigation.js:215-256`, `frontend/src/components/ReportUnavailable.jsx` | Confirmed Gap | High | High | Could (P3) | Stakeholders expecting full-suite PMO capabilities experience missing functionality until enterprise telemetry lands. | Implement mock data adapters or phased schema migrations to support these reports as underlying tables are provisioned (`REC-011`). | Product Owner / Enterprise Architect | Feature toggle verification & schema delivery milestones |
| `GAP-002` | Technical | **In-Memory Pin Tokens Lost on Server Restart**: Ask TELA pin tokens (`pin_token`) are maintained in an in-memory dictionary with a 1-hour TTL. | `backend/custom_reports.py:53-56` | Confirmed Gap | High | Low | Could (P3) | A backend restart or autoscaling container deployment invalidates pin tokens, returning HTTP 410 Gone to users attempting to pin recent answers. | Persist pin tokens with expiration timestamps in Redis or the SQLite/PostgreSQL metadata store (`REC-012`). | Backend Eng | Server restart pin validation test |
| `GAP-003` | Operational | **Absence of Centralized Observability & Distributed Tracing**: Application currently relies on standard Python `logging` and console outputs without structured OpenTelemetry traces or Prometheus metrics. | `backend/*.py` | Documentation Gap | Medium | Medium | Should (P2) | Operational teams lack visibility into LLM latency, token costs, database query bottlenecks, and error rates in production. | Integrate OpenTelemetry instrumentation for FastAPI and SQLAlchemy, exporting traces to Azure Monitor / Application Insights (`REC-013`). | DevOps / SRE | Azure Monitor dashboard validation |
| `GAP-004` | Documentation | **Outdated Documentation in `Docs/ASK_TELA_NATIVE.md`**: Document still asserts that the executor refuses accounts with name `DB_USER` and caches grants for 5 minutes (code now accepts verified accounts and caches for 60s). | `Docs/ASK_TELA_NATIVE.md`, `README.md:295-298` | Documentation Gap | Low | Low | Should (P3) | Confusion for infrastructure engineers configuring read-only accounts. | Update `Docs/ASK_TELA_NATIVE.md` to reflect current executor grant validation behavior (`REC-014`). | Technical Writer | Document review against `backend/tela_executor.py` |
