# Product & Project Knowledge Base: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L3 (Implemented static source code) with L4 (Configuration) and L1/L2 (Specifications) |
| Sources analyzed | 25 sources: source code, configuration, DOCX research/sprint plans, XLSX field sheets, MySQL dump |
| Related documents | [Architecture](architecture.md), [Requirements](requirements.md), [Business Rules](business_rules.md), [API Catalog](api_catalog.md), [Data Dictionary](data_dictionary.md), [Risk Register](risk_register.md), [Diagram Index](diagrams/README.md) |

---

## Table of Contents
- [1. Executive Summary](#1-executive-summary)
- [2. Evidence Level & Confidence Statement](#2-evidence-level--confidence-statement)
- [3. Product Vision & Business Objectives](#3-product-vision--business-objectives)
- [4. Problem Statement & Multi-Level Explanation](#4-problem-statement--multi-level-explanation)
- [5. Realistic End-to-End Value Workflow](#5-realistic-end-to-end-value-workflow)
- [6. Domain Overview & Enterprise Concepts](#6-domain-overview--enterprise-concepts)
- [7. Stakeholders & User Persona Roles](#7-stakeholders--user-persona-roles)
- [8. Product Scope & Explicit Boundaries](#8-product-scope--explicit-boundaries)
- [9. Module & Feature Inventory](#9-module--feature-inventory)
- [10. Complete Business Workflows (User Journeys)](#10-complete-business-workflows-user-journeys)
- [11. Key Business Rules & Calculations Summary](#11-key-business-rules--calculations-summary)
- [12. Functional & Non-Functional Requirements Summary](#12-functional--non-functional-requirements-summary)
- [13. Core Data Entities & Dynamics CRM Mappings](#13-core-data-entities--dynamics-crm-mappings)
- [14. External Systems & Integrations Summary](#14-external-systems--integrations-summary)
- [15. High-Level Architecture Summary](#15-high-level-architecture-summary)
- [16. Dependencies & Constraints](#16-dependencies--constraints)
- [17. Key Decisions & Rationale (ADR Summary)](#17-key-decisions--rationale-adr-summary)
- [18. Gaps, Risks & Open Questions Summary](#18-gaps-risks--open-questions-summary)
- [19. Recommendations & Prioritized Roadmap](#19-recommendations--prioritized-roadmap)
- [20. Acceptance Criteria & Testing Priorities](#20-acceptance-criteria--testing-priorities)
- [21. Comprehensive Traceability Matrix](#21-comprehensive-traceability-matrix)
- [22. Source Inventory & Coverage](#22-source-inventory--coverage)
- [23. Current Understanding & Immediate Next Steps](#23-current-understanding--immediate-next-steps)

---

## 1. Executive Summary

**PMO Intelligence & Analytics** is a decision-support and portfolio governance platform designed for Project Management Office (PMO) executives, Chief Information Officers (CIOs), and enterprise delivery leaders. Built on a single-page React frontend and a Python FastAPI backend, the platform translates raw delivery and demand-intake records extracted from enterprise CRM systems into governed executive dashboards, canonical health metrics, conversational data intelligence (**Ask TELA**), and user-built **Custom Reports**.

The application enforces a single governed source of truth across all reporting surfaces. Rather than allowing project managers or generative AI models to improvise metric calculations, all portfolio figures (such as calculated health, financial exposure, overdue go-lives, and governance gate completions) are computed through shared server-side functions. To support ad-hoc analytical inquiry, the platform integrates **Ask TELA**, a hybrid natural-language engine that routes official queries to certified deterministic handlers while safely delegating exploratory analytics to a sandboxed large language model (LLM) operating over a read-only database boundary.

> **Current Implementation State**: Functionally implemented and browser-validated for core executive dashboards, certified metrics, exploratory Ask TELA queries, and Custom Reports persistence. However, the system is **not production-ready** due to the absence of user authentication (tenancy is currently pinned at deployment level to `DEMO_CIO`), shared database account defaults, and open test failures.

---

## 2. Evidence Level & Confidence Statement

The analysis across this knowledge base is conducted under **Evidence Level L3 (Implemented Static Source Code) with L4 (Configuration) and L1/L2 (Design Documents)**:
- **L3 Evidence Established**: Full inspection of the backend Python codebase (`backend/*.py`), frontend React components (`frontend/src/`), database dumps (`data/tpg_synthetic_mysql.sql`), and SQLite storage (`data/custom_reports.sqlite3`).
- **L4 Evidence Established**: Configuration inspection of `.env`, `backend/requirements.txt`, and `frontend/package.json`.
- **L1/L2 Evidence Established**: Analysis of 4 Microsoft Word documents (`PMO Application-Infra,Auth.docx`, `Scalability Notes.docx`, `PMO_Report_Builder_Sprint_Plan.docx`, `PMO_TELA_Integration_Research_Findings_and_Feasibility_Assessment.docx`) and 3 Excel workbooks (`Field details for Prodian_*.xlsx`, `Plan.xlsx`).

**Confidence Limits**:
1. All conclusions regarding code execution, SQL validation, metric formulas, and Custom Reports persistence are **Verified** against implemented source code.
2. Production cloud infrastructure (Azure App Service, Entra ID, PostgreSQL Flexible Server) is documented as **Proposed Target Design** based on research findings.
3. Live operational behavior under concurrent executive load has not been measured (L5 evidence is absent).

---

## 3. Product Vision & Business Objectives

### Product Vision
To empower technology executives and PMO directors with instantaneous, reliable portfolio visibility and self-service analytics, eliminating manual reporting latency while maintaining strict enterprise governance and data accuracy.

### Core Business Objectives (`BUS-###`)
- `BUS-001` (Single Source of Portfolio Truth): Unify disparate project status reports and demand requests into a standardized, governed data repository.
- `BUS-002` (Eliminate Subjective Reporting Bias): Replace self-reported Project Manager RAG status with an objective, algorithmic 4-factor health score.
- `BUS-003` (Governed Self-Service Intelligence): Enable non-technical executives to query portfolio data using natural language without bypassing governance or risking SQL injection.
- `BUS-004` (Executive Personalization): Allow leaders to pin certified and exploratory analytical widgets into personalized Custom Reports that persist across sessions.
- `BUS-005` (Demand-to-Delivery Pipeline Visibility): Provide continuous governance over demand intake, stage 1 discovery, approval gates (DOU, DARB, SOW), and conversion into active delivery projects.
- `BUS-006` (Data Honesty & Transparency): Prevent organizational misinformation by explicitly flagging data gaps, disclaiming forecast figures vs. actual spend, and disabling unsupportable reports.

---

## 4. Problem Statement & Multi-Level Explanation

### The Problem
Large technology organizations track hundreds of complex software delivery projects and incoming client demand requests across multiple regions and platforms. Traditional PMO operations suffer from three fatal friction points:
1. **Reporting Lag & Inconsistency**: Compiling weekly executive slide decks takes days. Different dashboards show contradictory project health counts because each spreadsheet or report uses conflicting SQL filters.
2. **Subjective RAG Bias**: Project managers routinely report their projects as "Green" until days before go-live, masking severe schedule slips and cost overruns until it is too late for executive intervention.
3. **Ad-Hoc Query Paralysis**: When a CIO asks a basic portfolio question ("What is our average forecast cost by platform in KSA?"), PMO analysts must spend hours writing SQL or pivot tables. Conversely, unconstrained AI query tools risk hallucinating metrics or leaking confidential data.

---

### Product Explanation at Three Levels

#### 1. Simple Explanation (For Non-Technical Stakeholders)
Imagine an intelligent control room for a Chief Information Officer. Instead of waiting for weekly PowerPoint slides that might hide project delays, the CIO opens a web screen showing the exact, honest state of all projects. If they want to know something specific—like which projects are falling behind in East Africa—they simply type the question in plain English. The system instantly returns a clean chart and explanation. If the answer is valuable, the CIO clicks "Pin" to save it to their personal executive dashboard.

#### 2. Business & Operational Explanation (For Business Analysts & PMO Leaders)
PMO Intelligence & Analytics is a portfolio decision platform that bridges the gap between project delivery and executive governance. It ingests records from enterprise CRM systems and applies strict, agreed-upon PMO business rules. It computes objective project health based on dates, budgets, and milestone baselines rather than relying on self-reported optimism. It tracks incoming client demand through governance milestones (DOU, DARB, SOW) and highlights commercial exposure. Every metric shown in Ask TELA matches the official dashboard figures perfectly, ensuring zero discrepancies during executive committee reviews.

#### 3. Technical Explanation (For Architects & Software Engineers)
The system is a decoupled, single-origin web platform consisting of a React 19 SPA and a Python FastAPI backend. It queries a read-only MySQL database populated by Dynamics CRM exports. Governed dashboards and certified natural language queries execute deterministic Python algorithms (`metrics.py`, `health.py`). For exploratory questions, an LLM planner generates single-statement SQL that is validated via `sqlglot` AST parsing, restricted to an allow-list of 41 columns, rewritten into filtered derived subqueries applying the tenant's pinned CIO, and executed on a verified read-only connection with a 15-second timeout and 200-row limit. Custom dashboards and layout geometries are persisted in an isolated SQLite database using Write-Ahead Logging (WAL).

---

## 5. Realistic End-to-End Value Workflow

The following scenario illustrates how the platform creates executive value in practice:

1. **Morning Briefing Review**:
   - Nomsa Osei, CIO for Africa & Middle East, logs into the PMO Intelligence portal.
   - The global header instantly confirms the deployment scope: **"Data as of 2026-09-30 | Pinned CIO: Nomsa Osei"**.
   - On the **Projects Overview** screen, Nomsa observes that while self-reported status shows 10 Green and 3 Amber projects, the **Calculated Health** pulse reveals 2 projects flagged as **Red** and 1 flagged as **Insufficient data**.
2. **Drill-Down Investigation**:
   - Nomsa opens the **Project Drawer** for project `PRJ-00084` ("Core Banking Switch Upgrade").
   - The diagnostic breakdown reveals that although the PM reported a Green Schedule KPI, the planned go-live date (`2026-09-15`) is 15 days past due without deployment, triggering `RULE-009` (RAG Contradiction) and severe override `RULE-006` (Schedule score = 0 $\rightarrow$ Red).
3. **Ad-Hoc Inquiry via Ask TELA**:
   - Wanting to understand broader platform cost exposures, Nomsa clicks **Ask TELA** and types:
     > *"Show average forecast cost by platform"*
   - The system checks certified intents (no exact match), passes the query to the exploratory pipeline, validates the generated SQL, rewrites it to scope strictly to `cio = 'Nomsa Osei'`, and runs the query within 850 ms.
   - The UI presents a clean horizontal Bar Chart with a trust label: **"Exploratory — not an official PMO metric"** and a factual explanation: *"3 platforms compared. Highest: Network One ($921,138); lowest: Network Flex ($637,433)."*
4. **Curating a Custom Executive Report**:
   - Nomsa clicks **"Pin to Custom Reports"**, selects her existing dashboard **"Q4 Portfolio Review"**, and saves the widget.
   - Opening **Custom Reports** from the sidebar, the newly pinned widget appears alongside her certified KPI cards.
   - When refreshed next week, the widget will re-execute live under her current scope, ensuring her personalized review dashboard remains permanently up to date.

---

## 6. Domain Overview & Enterprise Concepts

### Key Domain Terminology
- **Open Portfolio**: All projects with TPG status `status = 'Active'`, encompassing lifecycle states `Project Created`, `Active`, and `On Hold`.
- **In Delivery**: Open projects with current state strictly `current_state = 'Active'`. Represents active delivery execution.
- **Reported Status**: The subjective Red/Amber/Green indicator recorded by the project manager (`overall_kpi`).
- **Calculated Health**: An objective 0–100 composite score derived from Schedule (35%), Cost (25%), Progress (25%), and Issues (15%), requiring $\ge 60\%$ evidence coverage.
- **Forecast Cost (EAC)**: Estimate at Completion represented by `cost_total_usd`. The system maintains **no actual spend** data.
- **First Baselined Date**: The earliest approved commitment date for deployment; serves as the fixed anchor for measuring schedule slippage.
- **Overdue Go-Live (Past Due)**: Active in-delivery project whose planned deployment date is strictly before the reporting as-of date and actual deployment is null.
- **RAG Contradiction**: A data discrepancy where reported KPI indicates Green while dates indicate severe lateness, or vice versa.
- **Stale Project**: An active in-delivery project with no system modifications (`modified_on`) for over 30 days.
- **Governance Gate Actions**: Regulatory milestones on demand requests:
  - **DOU**: Document of Understanding between PMO and requesting business unit.
  - **DARB**: Design Architecture Review Board approval.
  - **SOW**: Statement of Work sign-off.

---

## 7. Stakeholders & User Persona Roles

| Role ID | Persona / Stakeholder | Core Operational Needs | Primary System Surfaces Used |
|---|---|---|---|
| `STK-001` | **Chief Information Officer (CIO)** | Portfolio health pulse, strategic initiative progress, financial EAC exposure, ad-hoc inquiry without analyst delays. | Executive Dashboards, Ask TELA, Custom Reports, Project Drawer. |
| `STK-002` | **PMO Director** | Governance compliance, baselining adherence, PM assignment rates, PCR change control, demand intake pipeline. | Governance Dashboard, Pipeline Reports, Audit Exports. |
| `STK-003` | **Portfolio & Delivery Manager** | Milestone delivery horizons (30/60/90 days), past due project mitigation, stage completion tracking, schedule variances. | Delivery Dashboard, Flow / Kanban Board, Filter Bar. |
| `STK-004` | **Demand & Commercial Lead** | Intake conversion rates, discovery schedule variances, commercial revenue projections, requested man-day effort. | Requests Workspace (Demand, Discovery, Commercial, Intake Board). |
| `STK-005` | **Lead System Architect & DBA** | Data model integrity, read-only security boundaries, AST validation gates, query timeouts, cloud migration feasibility. | Backend API, Database Gating (`tela_executor.py`), Configuration (`.env`). |

---

## 8. Product Scope & Explicit Boundaries

### In-Scope Functional Capabilities
- Dual workspace navigation: **Projects** (delivery) and **Requests** (demand intake).
- 5 Governed Project Dashboards: Executive, Governance, Delivery, Finance, Flow.
- 5 Governed Demand Dashboards: Pipeline, Demand, Commercial, Governance, Flow.
- Global multi-dimensional filter bar (Region, Portfolio, PM, Priority, Platform).
- Comprehensive Slide-Over Project Drawer with diagnostic health scoring and baseline slip.
- In-process **Ask TELA** natural language interface (certified intents + sandboxed exploratory SQL).
- **Custom Reports** engine: widget pinning, drag-and-drop layout, live re-execution, and snapshot fallback.
- Server-side multi-tenancy enforcement pinned to `DEMO_CIO`.

### Explicit Exclusions & Disabled Reports
To prevent misleading executives with ungrounded speculation, 8 dashboards are explicitly **disabled** in `frontend/src/navigation.js` because the enterprise CRM schema does not contain the required data:

| Disabled Report | Specific Reason for Exclusion | Required Data to Re-Enable |
|---|---|---|
| **Portfolio Trends** | No historical portfolio snapshots exist; only creation dates are stored. | Point-in-time snapshot archiving table (`REC-006`). |
| **Status Reporting Compliance** | No status submission logs exist; `modified_on` updates on any row edit. | Formal weekly status report submission log. |
| **Schedule Compliance** | No approved-schedule indicator exists; planned date is not an approval. | Explicit schedule baseline approval field or log. |
| **Mandatory Fields** | No authoritative mandatory field governance policy is defined in charter. | PMO charter sign-off on mandatory attributes per stage. |
| **Milestone Slippage** | No project milestone records exist; lifecycle stages are not milestones. | Dedicated `milestones` table with baseline/forecast dates. |
| **Critical Path** | No activity-level schedule network or dependency data exists. | Project schedule network / task dependency table. |
| **RAID Summary** | No risk, assumption, issue, or dependency log items exist. | Dedicated enterprise RAID register table. |
| **Resource Utilization & Timesheets** | No capacity, allocation, booked hours, or timesheet line items exist. | Enterprise timesheet and resource allocation system. |

---

## 9. Module & Feature Inventory

| Feature ID | Feature Name | Primary Subsystem | Business Capability | Linked Requirements |
|---|---|---|---|---|
| `FEAT-001` | Dual Workspace Switching | Frontend Shell | Instant toggling between delivery portfolio (`/projects/*`) and intake demand (`/requests/*`). | `REQ-001` |
| `FEAT-002` | Centralized As-Of Clock | Backend Core | Single reference date (`PMO_AS_OF` or `CURDATE()`) applied across all aging calculations. | `REQ-002` |
| `FEAT-003` | Multi-Dimensional Filtering | Frontend / Backend | Dynamic multi-select filtering scoped to caller's tenancy boundary. | `REQ-003`, `REQ-004` |
| `FEAT-004` | Disabled Report Guarding | Navigation | UI routing guard displaying informative data gap explanations instead of empty charts. | `REQ-005` |
| `FEAT-005` | Diagnostic Project Drawer | UI Component | Detailed project slide-over showing financial EAC, schedule variance, and health evidence. | `REQ-006` |
| `FEAT-006` | Objective Project Health Model | Governed Analytics | 4-factor scoring (Schedule 35%, Cost 25%, Progress 25%, Issues 15%) with 60% coverage gate. | `REQ-008`, `REQ-009`, `REQ-010` |
| `FEAT-007` | Dual-Path NL Routing | Ask TELA | Deterministic routing of certified queries to reviewed handlers; exploratory to LLM. | `REQ-016`, `REQ-018` |
| `FEAT-008` | Transparent Trust Labels | Ask TELA | Visual attribution badges ("Certified PMO metric" vs "Exploratory — not official"). | `REQ-017` |
| `FEAT-009` | Structured LLM Planning | Ask TELA | Tool-calling prompt forcing LLMs to return strict JSON plans without seeing database rows. | `REQ-019` |
| `FEAT-010` | AST Security & Allow-List | Ask TELA | Syntactic SQL AST validation blocking DDL/DML, functions, and restricting to 41 columns. | `REQ-020`, `REQ-021` |
| `FEAT-011` | Mandatory Scope Injection | Ask TELA | Automated rewriting of query tables into derived subqueries with server-pinned filters. | `REQ-022`, `REQ-041` |
| `FEAT-012` | Read-Only Account Gating | Ask TELA | Connection boundary failing closed unless MySQL `SHOW GRANTS` is verifiably read-only. | `REQ-023` |
| `FEAT-013` | Adaptive Visualization | Ask TELA | Automatic selection of KPI card, Bar chart, Line chart, or Table based on data shape. | `REQ-025`, `REQ-026` |
| `FEAT-014` | Contextual Follow-Up Engine | Ask TELA | Generation of syntactically complete follow-up question suggestions. | `REQ-027` |
| `FEAT-015` | Opaque Pin Token System | Custom Reports | Server-side memory tokens (TTL 1hr) allowing safe pinning without client SQL exposure. | `REQ-028`, `REQ-029`, `REQ-030` |
| `FEAT-016` | Live Re-Execution & Fallback | Custom Reports | Pinned widgets re-run live on page load, falling back to cached snapshot if offline. | `REQ-031`, `REQ-032` |
| `FEAT-017` | Dashboard Layout Editor | Custom Reports | Drag-and-drop sequencing, half/full width toggling, view switching, and renaming. | `REQ-033` |
| `FEAT-018` | Official Dashboard Isolation | Custom Reports | Architectural lock preventing custom reports from writing to official dashboards. | `REQ-034` |
| `FEAT-019` | Decoupled SQLite Store | Custom Reports | Dedicated persistence file (`custom_reports.sqlite3`) ensuring zero write risk to MySQL. | `REQ-035` |
| `FEAT-020` | Demand Pipeline Tracking | Requests Workspace | Intake tracking across 7 demand states and stage 1 discovery variances. | `REQ-036`, `REQ-039` |
| `FEAT-021` | Overdue Gate Audit | Requests Workspace | Automated auditing of overdue DOU, DARB, and SOW milestone governance dates. | `REQ-037` |
| `FEAT-022` | Demand-to-Project Conversion | Requests Workspace | Tracking conversion rates from initial intake request to active delivery project. | `REQ-038` |

---

## 10. Complete Business Workflows (User Journeys)

### Workflow 1: Executive Portfolio Health Review & Drill-Down
```
[Executive Opens Overview]
         |
         v
[Inspects Pulse Cards: Total, In Delivery, Overdue, EAC Budget Variance]
         |
         v
[Examines Health Pulse: Compares Reported Status (PM RAG) vs Calculated Health (Model)]
         |
         v
[Clicks Discrepant Project in Table] --> [Slide-Over Project Drawer Opens]
                                                   |
                                                   v
                         [Reviews Schedule Slip vs Baseline, EAC Cost Variance, & Drivers]
```

### Workflow 2: Conversational Ask TELA Querying & Pinning
```
[User Navigates to Ask TELA]
         |
         v
[Types Natural Language Question: "What are the top 5 projects by forecast cost?"]
         |
         v
[Backend Evaluates Intent]:
   - Match Certified? --> Execute deterministic handler
   - Exploratory?    --> Gating Check -> LLM Plan -> AST Validate -> Scope Rewrite -> Read-Only Run
         |
         v
[Screen Renders Formatted Bar Chart + Factual Summary + Trust Label ("Exploratory")]
         |
         v
[User Clicks "Pin to Custom Reports"] --> [Selects "Executive Briefing Dashboard"]
         |
         v
[Server Validates Opaque Pin Token & Stores Widget in SQLite with Snapshot]
```

### Workflow 3: Custom Report Customization & Re-Execution
```
[User Opens "Custom Reports" in Sidebar]
         |
         v
[Loads Dashboard: Queries SQLite for Widget Definitions & Geometry]
         |
         v
[Attempts Live Re-Execution per Widget under Current DEMO_CIO & Global Filters]
   - Live Success?  --> Render Fresh Live Data (mode='live')
   - Live Failure?  --> Render Cached Historical Snapshot with "Snapshot" Badge (mode='snapshot')
         |
         v
[User Customizes: Reorders Widgets, Toggles Width (Half/Full), Edits View (Chart to Table)]
         |
         v
[Layout Persisted to SQLite Items Table]
```

---

## 11. Key Business Rules & Calculations Summary

All business rules are formally defined in [business_rules.md](business_rules.md). Essential operational rules:

1. **4-Factor Project Health (`RULE-004`)**:
   $$\text{Score} = (S_{\text{sched}} \times 0.35) + (S_{\text{cost}} \times 0.25) + (S_{\text{prog}} \times 0.25) + (S_{\text{iss}} \times 0.15)$$
   - Minimum coverage threshold: $C \ge 0.60$ (`RULE-005`). Below $0.60 \rightarrow$ "Insufficient data".
   - Status classifications: Green ($\ge 75$), Amber ($50–74$), Red ($< 50$).
   - Overrides (`RULE-006`): Fatal delay ($S_{\text{sched}}=0$), severe overrun ($S_{\text{cost}}=0$), or Red issue KPI overrides status to **Red**. Significant overrun ($S_{\text{cost}}=25$) caps status at **Amber**.
2. **Overdue Go-Live (`RULE-007`)**:
   $$\text{current\_state} = \text{'Active'} \quad \land \quad \text{planned\_deployment\_date} < \text{asOf} \quad \land \quad \text{actual\_deployment\_date IS NULL}$$
3. **Forecast vs. Approved Budget (`RULE-012`, `RULE-013`)**:
   - `cost_total_usd` is EAC forecast, **never actual spend**.
   - Over Budget Row Rule: $\text{cost\_total\_usd} > \text{approved\_budget\_value\_usd} \land \text{budget} > 0$.
   - Financial Exposure compares projects strictly with both positive budget and forecast. Excluded records tracked in `projectsExcluded`.
4. **Demand Overdue Gates (`RULE-015`)**:
   - DOU: `dou_required = 'Yes'` $\land$ `dou_planned_date < asOf` $\land$ `dou_issued_date IS NULL`.
   - DARB: `darb_exempted = 'No'` $\land$ `darb_review_date < asOf` $\land$ `darb_approval_date IS NULL`.
   - SOW: `sow_planned_date < asOf` $\land$ `sow_issued_date IS NULL`.

---

## 12. Functional & Non-Functional Requirements Summary

Full requirement specifications are detailed in [requirements.md](requirements.md).

### Summary Statistics
- **Functional Requirements (`REQ-001` to `REQ-045`)**: 45 formal specifications covering navigation, governed metrics, Ask TELA pipelines, Custom Reports, demand management, and access scope.
- **Non-Functional Requirements (`NFR-001` to `NFR-014`)**: 14 specifications governing least privilege database gating, 15-second timeouts, 200-row limits, AST parsing, PII data minimization, and tenanted scope enforcement.
- **Ambiguous Requirements Rewritten**: 5 legacy requirements with ungrounded terms ("delayed", "spend", "team status") rewritten into verified, measurable definitions.

---

## 13. Core Data Entities & Dynamics CRM Mappings

Full entity attributes, data types, and mappings are detailed in [data_dictionary.md](data_dictionary.md).

### Primary Entities
1. **`projects` (MySQL `tpg_synthetic`)**: 100 enterprise delivery projects. Enforces tenancy via `cio`. Contains financial EAC forecasts, baselines, and reported KPI colors.
2. **`project_requests` (MySQL `tpg_synthetic`)**: 150 intake demand requests. Tracks stage 1 discovery, conversion flags, and governance gate dates (DOU, DARB, SOW).
3. **`dashboards` (SQLite `custom_reports.sqlite3`)**: User custom dashboard containers owned by `cio:<DEMO_CIO>` or `anonymous`.
4. **`widgets` (SQLite `custom_reports.sqlite3`)**: Reusable analytical widgets storing un-scoped queries, display preferences, and snapshot caches.
5. **`items` (SQLite `custom_reports.sqlite3`)**: Join table managing widget positioning and grid geometry (spans 1 and 2).

---

## 14. External Systems & Integrations Summary

Full endpoint specifications and schemas are detailed in [api_catalog.md](api_catalog.md).

### External Systems
- **Microsoft Dynamics CRM / TPG Project Online**: Upstream enterprise source of truth. Data is currently ingested via batch database extract (`data/tpg_synthetic_mysql.sql`).
- **External LLM Providers**: Outbound HTTPS REST tool-calling integration supporting Google Gemini 2.5 Flash, OpenAI GPT-4.1, and Anthropic Claude 3.5.
- **Enterprise MySQL 8.0**: Read-only database connection pool accessed via SQLAlchemy 2.1 / PyMySQL.

---

## 15. High-Level Architecture Summary

Full technical architecture, component interactions, and deployment topology are detailed in [architecture.md](architecture.md).

- **Frontend**: Single-Page Application built with React 19, React Router 7, Vite 8, Tailwind CSS 4, Recharts 3, and Lucide icons.
- **Backend**: Python 3.13 FastAPI application exposing RESTful JSON endpoints.
- **Analytics Engine**: Deterministic calculations in `metrics.py` and `health.py`.
- **Ask TELA Engine**: Dual-path natural language orchestrator with `sqlglot` AST validation, automated scope rewriting, and verified read-only execution.
- **Custom Reports Engine**: SQLite WAL database managing user-owned dashboards and offline snapshot fallbacks.

---

## 16. Dependencies & Constraints

### Software & Environment Dependencies
- **Runtime**: Python 3.13+ and Node.js 24+.
- **Backend Libraries**: `fastapi==0.142`, `uvicorn==0.54`, `sqlalchemy==2.1`, `pymysql`, `sqlglot`, `pydantic==2`, `google-genai` / `openai` / `anthropic`.
- **Frontend Dependencies**: `react==19`, `react-router==7`, `vite==8`, `tailwindcss==4`, `recharts==3`, `lucide-react`, `jspdf`, `xlsx`.

### Operational Constraints
- Single MySQL connection string in local environment (`.env`).
- In-memory pin token cache (TTL 3,600 seconds) lost on backend restart (`GAP-002`).
- Monolithic frontend build bundle exceeding 1.2 MB (`RISK-010`).

---

## 17. Key Decisions & Rationale (ADR Summary)

Full Architecture Decision Records are documented in [architecture.md#20-architecture-decision-records-adrs](architecture.md#20-architecture-decision-records-adrs):
- `ADR-001` (In-Process TELA Integration): Chose in-process FastAPI modules over a separate microservice to eliminate network latency, simplify connection pooling, and unify scope enforcement.
- `ADR-002` (Dual-Tier Storage Isolation): Chose SQLite for Custom Reports, keeping the core MySQL enterprise database strictly read-only and eliminating write corruption risks.
- `ADR-003` (AST Derived Subquery Scope Rewriting): Chose `sqlglot` AST transformation to replace table references with scoped derived subqueries, making SQL injection tenant bypasses mathematically impossible.
- `ADR-004` (Server-Side Opaque Memory Pin Tokens): Chose short-lived memory tokens for widget creation so the browser never receives or submits raw SQL strings.

---

## 18. Gaps, Risks & Open Questions Summary

Detailed risk ratings, impact assessments, and mitigation owners are maintained in [risk_register.md](risk_register.md).

### Top 5 Critical Risks & Gaps
1. **`RISK-001` (Critical - Security)**: Absence of user login and multi-user RBAC. Tenancy identity is pinned globally via `DEMO_CIO`. Mitigation: Integrate Microsoft Entra ID (`REC-001`).
2. **`RISK-002` (High - Security)**: Shared database credentials across dashboards and exploratory TELA queries in default setup. Mitigation: Enforce dedicated read-only account (`REC-002`).
3. **`RISK-004` (High - Operational)**: SQLite single-file storage for Custom Reports creates a single point of failure in cloud containers. Mitigation: Migrate to Azure PostgreSQL (`REC-003`).
4. **`RISK-005` (High - Data Integrity)**: Absence of actual spend and timesheet records; `cost_total_usd` is strictly EAC forecast. Mitigation: Maintain explicit UI tooltips and disclaimers.
5. **`RISK-007` (Medium - QA)**: 22 scope-sensitive backend tests failing under pinned `DEMO_CIO`. Mitigation: Parameterize tests for active scope (`REC-005`).

---

## 19. Recommendations & Prioritized Roadmap

| Phase | Milestone | Priority | Actions & Recommendations |
|---|---|---|---|
| **Phase 1: Immediate Remediation** | Hardening & Test Cleanliness | P0 / P1 | - Refactor 22 scope-sensitive tests to enable clean CI/CD (`REC-005`).<br/>- Enforce dedicated read-only MySQL account (`REC-002`).<br/>- Implement route-level code splitting in React SPA (`REC-004`).<br/>- Rename `backend/requests.py` to `backend/demand_requests.py` (`REC-006`). |
| **Phase 2: Cloud Foundation** | Azure Provisioning & Auth | P0 | - Provision Azure App Service & Azure Database for PostgreSQL Flexible Server.<br/>- Integrate Microsoft Entra ID (Azure AD) OAuth2/OIDC authentication (`REC-001`).<br/>- Migrate Custom Reports storage to PostgreSQL (`REC-003`). |
| **Phase 3: Telemetry & Disabled Reports** | Data Ingestion Expansion | P2 | - Provision automated daily portfolio snapshot archiving table (`REC-006`).<br/>- Ingest timesheet and milestone telemetry to re-enable 8 disabled dashboards (`GAP-001`).<br/>- Instrument OpenTelemetry and Azure Monitor distributed tracing (`REC-007`). |

---

## 20. Acceptance Criteria & Testing Priorities

### Primary Verification Priorities
1. **Security Boundary Enforcement**: Verify that any exploratory SQL attempting DDL, DML, or cross-tenant access is rejected with HTTP 422 or empty results.
2. **Metric Parity Across Surfaces**: Assert 100% agreement between Overview pulse tiles, Executive report tables, and Ask TELA certified answers under identical filters.
3. **Graceful Offline Degradation**: Assert that disabling the exploratory gate causes Custom Report widgets to display cached snapshots with visible "Snapshot" badges rather than crashing.
4. **Full Test Suite Cleanliness**: Achieve 100% pass rate across the full pytest suite (resolving current 24 test failures).

---

## 21. Comprehensive Traceability Matrix

The matrix below maps business objectives (`BUS`), stakeholders (`STK`), requirements (`REQ`), features (`FEAT`), business rules (`RULE`), architecture components (`COMP`), and risks (`RISK`):

| Business Objective | Primary Stakeholder | Functional Requirement | Feature ID | Business Rule | Architecture Component | Addressed Risk |
|---|---|---|---|---|---|---|
| `BUS-001` (Single Source of Truth) | `STK-002` (PMO Director) | `REQ-007` | `FEAT-006` | `RULE-002`, `RULE-003` | `COMP-002` (Metrics Engine) | `RISK-005` |
| `BUS-002` (Eliminate RAG Bias) | `STK-001` (CIO) | `REQ-008`, `REQ-009` | `FEAT-006` | `RULE-004`, `RULE-005`, `RULE-006` | `COMP-002` (Health Model) | `RISK-005` |
| `BUS-003` (Governed Self-Service NL) | `STK-001` (CIO) | `REQ-016`, `REQ-020` | `FEAT-007`, `FEAT-010` | `RULE-016`, `RULE-017`, `RULE-018` | `COMP-004` (Ask TELA Engine) | `RISK-002` |
| `BUS-004` (Custom Executive Reports) | `STK-001` (CIO) | `REQ-028`, `REQ-031` | `FEAT-015`, `FEAT-016` | `RULE-020`, `RULE-021` | `COMP-003` (Custom Reports) | `RISK-004` |
| `BUS-005` (Demand Pipeline Visibility)| `STK-004` (Demand Lead) | `REQ-036`, `REQ-037` | `FEAT-020`, `FEAT-021` | `RULE-014`, `RULE-015` | `COMP-002` (Requests Router) | `RISK-006` |
| `BUS-006` (Data Honesty & Transparency)| `STK-003` (Delivery Lead) | `REQ-005`, `REQ-013` | `FEAT-004` | `RULE-009`, `RULE-012` | `COMP-001` (Navigation Guard) | `GAP-001` |

---

## 22. Source Inventory & Coverage

All 25 project source files have been inventoried, analyzed, and traced:

```
PMO-UpdatedLatest/
├── backend/
│   ├── main.py, metrics.py, health.py, executive.py, governance.py, delivery.py,
│   │   finance.py, flow.py, projects.py, requests.py, talk_to_data.py, talk_compose.py,
│   │   chat.py, chat_providers.py, tela_adapter.py, tela_service.py, tela_planner.py,
│   │   tela_schema.py, tela_validator.py, tela_scope.py, tela_executor.py, tela_viz.py,
│   │   custom_reports.py (100% Inspected & Traced)
├── frontend/src/
│   ├── App.jsx, navigation.js, main.jsx, Overview.jsx, Executive.jsx, Delivery.jsx,
│   │   Finance.jsx, Governance.jsx, components/*, lib/* (100% Inspected & Traced)
├── data/
│   ├── tpg_synthetic_mysql.sql (MySQL Schema & Inserts Inspected)
│   └── custom_reports.sqlite3 (SQLite Schema & Tables Inspected)
└── Docs/
    ├── CANONICAL_METRICS.md, TALK_TO_DATA.md, ASK_TELA_NATIVE.md, Slides 0-5.md (Inspected)
    ├── PMO Application-Infra,Auth.docx, Scalability Notes.docx (Inspected via XML parser)
    ├── PMO_Report_Builder_Sprint_Plan.docx, PMO_TELA_Integration_Research_Findings.docx (Inspected)
    └── Field details for Prodian_Project.xlsx, Field details for Prodian_Request.xlsx, Plan.xlsx (Inspected)
```

---

## 23. Current Understanding & Immediate Next Steps

### Summary of Understanding
The PMO Intelligence & Analytics codebase is a sophisticated, functioning system with rigorous metric definitions, defensive SQL validation for generative AI, and a practical user personalization architecture. Its primary vulnerability lies in the gap between application-level logic and enterprise operational infrastructure (authentication, database account isolation, and persistent cloud storage).

### Immediate Next Steps for Engineering Teams
1. **Fix Failing Test Suites**: Apply `REC-005` to parameterize scope in `backend/tests/` and verify that `pytest` passes 100%.
2. **Execute Frontend Route Splitting**: Apply `REC-004` to split `frontend/src/App.jsx` and eliminate the $> 1.2$ MB bundle warning.
3. **Provision Dedicated Read-Only DB Account**: Run DBA script in `Generate READONLY DB Credentials.md` and update `.env` with separate `PMO_RO_DB_*` credentials (`REC-002`).
4. **Initiate Entra ID Integration**: Begin Sprint 1 tasks from `Docs/PMO_Report_Builder_Sprint_Plan.docx` to integrate Microsoft Entra ID JWT authentication (`REC-001`).
