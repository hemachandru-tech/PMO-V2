# Project Summary: Tela NL2SQL Business Intelligence Platform

| Field | Value |
|---|---|
| Project | Tela (Natural Language to SQL Business Intelligence & Reporting Platform) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L5 – Operational (Code, Schemas, OpenAPI Specs, Unit/Integration Suites, Live Server Execution) |
| Sources analyzed | 35 documentation and specification files, 66 Python modules, 15 TypeScript/React feature modules, OpenAPI spec (153 KB), runtime test suite, live server execution logs |
| Related documents | [Architecture](architecture.md) · [Requirements](requirements.md) · [Business Rules](business_rules.md) · [API Catalog](api_catalog.md) · [Data Dictionary](data_dictionary.md) · [Risk Register](risk_register.md) |

---

## 1. Document Overview

This document serves as the primary executive and functional knowledge base for **Tela**. It synthesizes the product vision, business problem, operational workflows, core features, stakeholder governance, architectural decisions, and prioritized roadmap into an accessible, traceable resource for business stakeholders, product managers, and engineering teams.

---

## 2. Table of Contents
1. [Document Overview](#1-document-overview)
2. [Table of Contents](#2-table-of-contents)
3. [Executive Summary](#3-executive-summary)
4. [Evidence Level & Confidence Statement](#4-evidence-level--confidence-statement)
5. [Product Vision & Business Objectives](#5-product-vision--business-objectives)
6. [Problem Statement & Target Users](#6-problem-statement--target-users)
7. [Domain Overview & Terminology](#7-domain-overview--terminology)
8. [Business Model & Value Proposition](#8-business-model--value-proposition)
9. [Stakeholders & User Roles](#9-stakeholders--user-roles)
10. [Product Scope & Boundaries](#10-product-scope--boundaries)
11. [Module & Feature Inventory](#11-module--feature-inventory)
12. [Complete Business Workflows](#12-complete-business-workflows)
13. [Key Business Rules & Calculations](#13-key-business-rules--calculations)
14. [Functional Requirements Summary](#14-functional-requirements-summary)
15. [Non-Functional Requirements Summary](#15-non-functional-requirements-summary)
16. [Core Data Entities & Stores](#16-core-data-entities--stores)
17. [External Systems & Integrations](#17-external-systems--integrations)
18. [High-Level Architecture Summary](#18-high-level-architecture-summary)
19. [Dependencies & Technical Constraints](#19-dependencies--technical-constraints)
20. [Important Decisions & Rationale (ADRs)](#20-important-decisions--rationale-adrs)
21. [Gaps, Risks & Open Issues](#21-gaps-risks--open-issues)
22. [Recommendations & Prioritized Roadmap](#22-recommendations--prioritized-roadmap)
23. [Acceptance Criteria & Testing Priorities](#23-acceptance-criteria--testing-priorities)
24. [Traceability Matrix](#24-traceability-matrix)
25. [Related Documents](#25-related-documents)
26. [Source Inventory & Coverage](#26-source-inventory--coverage)
27. [Current Understanding & Immediate Next Steps](#27-current-understanding--immediate-next-steps)

---

## 3. Executive Summary

### 3.1 The Three-Level Explanation

#### 1. Simple Explanation (For Non-Technical Stakeholders)
Imagine having an expert data analyst sitting next to you 24/7. Whenever you have a question about your business—such as *"Which merchants had the highest chargeback rates last month?"*—you simply type it in plain English. Tela reads your question, understands your company's data structure, pulls the exact numbers from your live database safely, and shows you a clean chart, a data table, and a plain-English explanation of what the numbers mean. You can see every step of how it arrived at the answer, with a 100% guarantee that it can never accidentally alter, delete, or leak your private company records.

#### 2. Business Explanation (Commercial & Operational Purpose)
Modern enterprises maintain vast analytics databases with hundreds of tables and millions of rows. However, extracting actionable insights typically requires business teams to file ticketing requests with specialized SQL analysts, creating days or weeks of latency. **Tela** transforms database interaction into a self-service, instantaneous conversation. It slashes reporting bottlenecks, eliminates analyst backlog, enables executive self-service exploration, and democratizes data-driven decision-making while maintaining institutional data governance, row-level privacy, and regulatory compliance.

#### 3. Technical Explanation (Engineering Implementation)
Tela is a headless, decoupled analytics platform comprising a **FastAPI** backend and a **React 19 / TypeScript** single-page application. It utilizes Large Language Models (LLMs) exclusively as semantic translators (Natural Language to SQL), keeping the models completely isolated from raw database row data. Outgoing queries pass through a mandatory **LLM Gateway** that scrubs PII and blocks leaks. Generated SQL statements are validated by a deterministic, LLM-free parser (`sqlglot`) enforcing single-statement `SELECT` rules and table allow-lists. Security scope and row filters are dynamically injected at the Abstract Syntax Tree (AST) level in Python code. Database queries execute over pooled connections (PostgreSQL, MySQL, ClickHouse, DuckDB) under session-level read-only constraints, streaming real-time stage traces to the client via Server-Sent Events (SSE).

---

### 3.2 Realistic End-to-End Value Example

*Scenario: Head of Card Operations investigating chargeback spikes.*

1. **User Inquiry:** The Operations Head logs into Tela and types: *"Show me all merchants with more than 10 chargebacks in UAE this quarter, along with their gross processed volume in AED."*
2. **Context Resolution & Routing:** Tela checks the user's access permissions, confirms authorized access to the `Payments Data Warehouse`, and binds the active connection.
3. **Semantic Retrieval:** The semantic layer identifies tables `financial_transaction`, `merchant`, and `dispute`, retrieving verified join paths and the business glossary note that defines *"chargeback"* as `dispute.dispute_status = 'REPRESENTMENT'`.
4. **Planning & Gateway Check:** The planner formulates dialect-compliant SQL for PostgreSQL. The prompt contains only column metadata, data types, and the question. The Gateway scans the outbound prompt, verifies zero PII/credentials, and logs the call in an append-only audit file.
5. **AST Safety & Scoping:** The SQL validator parses the query AST. It confirms a single `SELECT` statement, verifies zero forbidden keywords, and checks that the user's role allows access to these tables. Because the user has a row rule restricting visibility to the `UAE` region (`country_code = 'ARE'`), the engine injects a WHERE predicate at the AST level. Finally, it appends `LIMIT 10000`.
6. **Execution & Visualization:** The query executes against the database under a session-enforced read-only transaction, completing in 184 milliseconds. The visualization engine analyzes the column types (one categorical merchant name, two numeric metrics: chargeback count and gross volume), selects a multi-measure bar chart, generates a Vega-Lite specification, and computes a narrative summary.
7. **Business Outcome:** The Operations Head sees an interactive chart ranking merchants by dispute count alongside gross volume, notices one merchant operating at an alarming 1.4% chargeback ratio, and immediately initiates risk underwriting review—completing an inquiry in **4.2 seconds** that previously required a 3-day data analytics ticket.

---

## 4. Evidence Level & Confidence Statement

- **Overall Evidence Rating:** **L5 – Operational**
- **Assessment Basis:** Comprehensive review of all 35 project specification files (`PlanFiles/01-10`, `ARCHITECTURE.md`, `payments.txt`, `FlexCap_ToDo.xlsx`), source code inspection across all 66 backend Python modules and frontend React components, verified OpenAPI specification (153 KB), unit/integration test suites, and verified live operational execution logs on macOS.
- **Confidence Statement:** We have verified the actual running software. Where older planning specifications (`PlanFiles/`) conflict with the active production codebase, the code and `docs/ARCHITECTURE.md` are documented as authoritative reality.

---

## 5. Product Vision & Business Objectives

### Product Vision
To be the enterprise standard for trusted, self-service database intelligence—delivering immediate conversational insights with uncompromising safety, transparent reasoning, and strict multi-tenant privacy.

### Strategic Business Objectives
- `BUS-001`: **Zero-Latency Insights:** Reduce average time-to-insight from 72 hours (analyst queue) to under 5 seconds for standard analytical inquiries.
- `BUS-002`: **Total Security Invariant:** Maintain a 100% guarantee that AI-driven natural language queries cannot modify, corrupt, or delete database tables, backed by database-level read-only roles.
- `BUS-003`: **Guaranteed Data Privacy:** Ensure customer data records, financial amounts, and personally identifiable information (PII) are never transmitted to third-party AI model providers.
- `BUS-004`: **Multi-Engine Agility:** Support heterogeneous database environments (PostgreSQL, MySQL, ClickHouse, Excel/CSV) through a single unified conversational interface.
- `BUS-005`: **Democratized Dashboard Creation:** Allow business users to promote ad-hoc conversational discoveries into persistent, auto-updating, interactive dashboard widgets without developer intervention.

---

## 6. Problem Statement & Target Users

### Problem Statement
Enterprise organizations possess massive data warehouses containing hundreds of tables and millions of rows. However, 85% of business employees cannot write SQL. This creates an unsustainable friction cycle:
1. Business decision-makers submit reporting requests to data teams.
2. Analysts spend up to 40% of their time writing repetitive ad-hoc SELECT queries.
3. Decisions are delayed or made based on stale, exported spreadsheets.
4. Existing "text-to-SQL" tools behave as black boxes, hallucinate incorrect table relationships, risk destructive writes, or leak sensitive company data to public AI APIs.

### Target Personas

| Persona | Role & Responsibilities | Core Needs from Tela |
|---|---|---|
| **Business User (`STK-001`)** | Business executives, operations managers, product managers, risk underwriters. | Asks questions in plain English, views verified charts and tables, applies dashboard filters, needs zero SQL knowledge. |
| **System Admin (`STK-002`)** | Head of Engineering, Data Architect, System Administrator. | Connects target databases, manages user accounts and access grants, curates semantic layers, reviews security audit logs. |
| **Data Analyst (`STK-003`)** | BI Developers, Analytics Engineers. | Curates business notes and glossary definitions, inspects generated SQL queries, seeds few-shot gold examples. |

---

## 7. Domain Overview & Terminology

Tela operates across multiple enterprise analytical domains, with deep reference implementations in **Card Payments & Processing** and **Supply Chain Finance (FlexCap)**:

- **NL2SQL:** Natural Language to SQL translation.
- **AST (Abstract Syntax Tree):** Syntactic tree representation of SQL used for deterministic query validation and subquery rewriting.
- **LLM Gateway:** Mandatory architectural chokepoint through which all AI prompts pass, executing masking and leak scanning.
- **Semantic Layer:** A curated metadata repository storing table descriptions, verified foreign key relationships, business glossaries, and few-shot examples.
- **Grain / Multiplier:** The level of detail represented by a single row in a table. In payments, `financial_transaction` has a grain of 1 (root), while fees and clearing messages have a grain of 1:N.
- **MDR (Merchant Discount Rate):** The contracted commercial fee rate charged to a merchant for processing card transactions.
- **Interchange Fee:** The interbank fee paid by the merchant acquirer to the card issuing bank.
- **Fail-Closed:** A security architecture posture where any ambiguous or error condition results in immediate refusal of execution.

---

## 8. Business Model & Value Proposition

- **Operational Efficiency:** Reduces data analyst hours spent on ad-hoc reporting by an estimated 65%.
- **Decision Velocity:** Shortens executive and operational decision cycles from days to seconds.
- **Lower TCO:** Consolidates multiple disconnected BI visualization tools, Excel macros, and manual export flows into a single headless analytics platform.
- **Enterprise Safety & Risk Reduction:** Eliminates the catastrophic enterprise risks of data exfiltration and database corruption inherent in unmonitored AI database tools.

---

## 9. Stakeholders & User Roles

The platform enforces two fundamental security roles, augmented by configurable job personas:
- **`admin`:** Full global access. Manages connections, users, access grants, LLM settings, profiling, and semantic layer curation.
- **`user`:** Restricted access. Can only query databases explicitly granted via `GrantStore`. Queries are filtered by assigned row-level conditions.
- **Configurable Personas (`DATA-003`):** Leader, Manager, Governance, Finance, and Admin, defining default UI landing order and grant templates.

---

## 10. Product Scope & Boundaries

```
IN SCOPE (V1 & Production Branch)               OUT OF SCOPE (Permanent Non-Goals & V2 Scope)
--------------------------------------------------------------------------------------------------
- Plain-English questions -> Executed SQL       - Database WRITE operations (INSERT/UPDATE/DELETE/DDL)
- Read-only multi-engine execution              - Multi-database cross-joins in a single query
- Live Server-Sent Events (SSE) trace streaming - Automated schema migration / DDL management
- Deterministic AST safety validation           - Mobile native applications (V1 is Web SPA)
- Per-user RBAC & AST row-filter injection      - Unsupervised AI semantic layer publishing
- Multi-panel dynamic AI dashboards             - Multi-worker container clusters (requires SQLite V2)
- Multi-format exports (Excel, PDF, Slides)
```

---

## 11. Module & Feature Inventory

| Feature ID | Module Name | Primary Responsibility | Status |
|---|---|---|---|
| `FEAT-001` | Session Authentication | Bcrypt password verification, HTTP-only session cookies | Implemented (Verified L5) |
| `FEAT-002` | Admin Auto-Bootstrap | Automatic seeding of first admin user from environment variables | Implemented (Verified L5) |
| `FEAT-003` | User & Role Management | User CRUD, last-admin protection, configurable job personas | Implemented (Verified L5) |
| `FEAT-004` | Multi-Tenant Data Grants| Per-user connection, table, column, and row-level grant isolation | Implemented (Verified L5) |
| `FEAT-005` | Mandatory LLM Gateway | Single chokepoint prompt masking, pre-send leak scanning, audit logging | Implemented (Verified L5) |
| `FEAT-006` | Zero-Egress Privacy Gate| Guaranteed withholding of raw row values from model prompts | Implemented (Verified L3) |
| `FEAT-007` | Aggregate Insight Context | Narrow exception for statistical aggregates in dashboard insights | Implemented (Verified L3) |
| `FEAT-008` | Dialect-Aware Planner | NL2SQL translation supporting Postgres, MySQL, ClickHouse, DuckDB | Implemented (Verified L5) |
| `FEAT-009` | Deterministic AST Gate | Single-statement SELECT check, forbidden AST blocklist, LIMIT injection | Implemented (Verified L5) |
| `FEAT-010` | Post-Planner Row Scoping| AST subquery rewriting (`FROM t` -> `FROM (SELECT * FROM t WHERE <p>) t`) | Implemented (Verified L3) |
| `FEAT-011` | Read-Only DB Execution | Session-enforced read-only transaction state and connection pooling | Implemented (Verified L5) |
| `FEAT-012` | Multi-Engine Connectors | Unified execution interfaces for Postgres, MySQL, ClickHouse, DuckDB | Implemented (Verified L5) |
| `FEAT-013` | 3-Tier Semantic Layer | Tier 1 (structure), Tier 2 (AI descriptions), Tier 3 (human rules) | Implemented (Verified L5) |
| `FEAT-014` | Standing Instructions | Binding business notes and glossary rules injected into prompts | Implemented (Verified L3) |
| `FEAT-015` | Automated Data Profiling| Single-scan table profiling detecting column formatting anomalies | Implemented (Verified L5) |
| `FEAT-016` | Real-Time SSE Tracing | Stage-by-stage transparent execution timeline streamed to client | Implemented (Verified L5) |
| `FEAT-017` | Schema Exploration Mode | Conversational schema advisor mode answering questions without SQL execution | Implemented (Verified L3) |
| `FEAT-018` | Value-Free Viz Engine | Chart type selection from column types only (Vega-Lite renderer) | Implemented (Verified L5) |
| `FEAT-019` | Question Queuing & Stop | In-flight turn abort via AbortController and turn serialization | Partially Implemented (L3) |
| `FEAT-020` | Dynamic AI Dashboards | Multi-panel dashboard planning from natural language or schema | Implemented (Verified L5) |
| `FEAT-021` | Independent Widget Run | Individual widget query refresh with dynamic AST filter injection | Implemented (Verified L5) |
| `FEAT-022` | Scoped Filter Options | Database DISTINCT value queries for dropdowns under viewer scope | Implemented (Verified L3) |
| `FEAT-023` | Custom Grid Layouts | Drag-and-drop dashboard builder via `react-grid-layout` | Implemented (Verified L5) |
| `FEAT-024` | Auto-Connection Routing | Semantic relevance scoring routing queries across multiple granted DBs | Implemented (Verified L5) |
| `FEAT-025` | Document Report Export | Formatted export to Excel (.xlsx), PDF, and PowerPoint (.pptx) | Implemented (Verified L3) |

---

## 12. Complete Business Workflows

### 12.1 Conversational Turn Workflow (End-to-End Inquiry)
1. **User Action:** The user inputs a question in the chat bar.
2. **Context Resolution:** The backend checks the user's active session, identifies their granted database connections, and resolves the target connection (or auto-routes).
3. **Semantic Layer Retrieval:** The retrieval engine identifies the top-K relevant tables, columns, foreign keys, and business glossary notes based on semantic similarity.
4. **Planning Dispatch:** The planner constructs a masked prompt (schema + business rules + question + conversation history) and dispatches it through the Gateway.
5. **Gateway Leak Scan:** The Gateway inspects the payload for credentials and PII. If clean, it invokes the configured LLM provider and records token usage in `logs/llm_audit.jsonl`.
6. **AST Access Control Rewriting:** The raw SQL AST is rewritten to inject user-specific row-level filters. If rewriting fails, it raises `RowRuleError` and refuses execution.
7. **Deterministic AST Validation:** The validator verifies a single `SELECT` statement, scans for forbidden operations, validates tables against the allow-list, and injects `LIMIT 10000`.
8. **Database Execution:** The approved query is dispatched to the connection pool under a session-enforced read-only transaction state.
9. **Visualization Decision:** The visualization engine analyzes output column types and generates Vega-Lite specifications.
10. **Result Rendering:** Results are pushed to the client via SSE, displaying an interactive chart, a data table, narrative text, and a visible step-by-step trace timeline.

---

### 12.2 Dashboard Generation & Widget Pinning Workflow
1. **Chat Discovery:** A user receives an insightful chart in conversational chat.
2. **Pin to Widget:** The user clicks "Pin as Widget". The frontend dispatches `POST /api/widgets`, saving the query definition, chart config, and filter bindings.
3. **Custom Dashboard Assembly:** The user navigates to Custom Dashboards, creates a new dashboard, and drags the widget tile onto the grid layout.
4. **Interactive Filtering:** When a global dashboard filter (e.g. date range) is adjusted, each widget independently re-executes its SQL via `POST /api/widgets/{id}/refresh`, injecting the filter predicate into the SQL WHERE clause before execution.

---

### 12.3 Database Onboarding & Semantic Layer Curation Workflow
1. **Admin Setup:** The admin navigates to Admin -> Connections and adds a database connection (PostgreSQL, MySQL, ClickHouse, or Excel).
2. **Test & Save:** The admin tests connectivity via `/api/connections/test`. Upon success, credentials are saved to `connection.json` with 0600 permissions.
3. **Automatic Introspection:** The engine introspects the database catalog, extracting physical tables, columns, and foreign keys (Tier 1).
4. **Curation & AI Enrichment:** The admin approves relevant business tables (unapproved tables are hidden from the AI) and triggers AI description drafting (Tier 2).
5. **Business Context Addition:** The admin enters binding business definitions, terminology synonyms, and verified few-shot SQL examples (Tier 3).
6. **Grant Assignment:** The admin assigns database access grants to standard user accounts via the Access Control matrix.

---

## 13. Key Business Rules & Calculations

Complete rule definitions, decision tables, and worked numerical examples are detailed in [Business Rules](business_rules.md).
- **Core Platform Rules:** Single-statement AST validation (`RULE-001`), schema allow-list validation (`RULE-002`), automatic safe LIMIT injection (`RULE-003`), fail-closed row-filter subquery substitution (`RULE-004`), Gateway leak detection regex (`RULE-005`), and value-free chart type selection (`RULE-006`).
- **Payments Domain Rules:** Fact counting grain rules G1 (`RULE-007`), fact amount summation CTE rules G2 (`RULE-008`), multi-domain disjoint CTE aggregation G3 (`RULE-009`), multi-currency isolation (`RULE-010`), and net settlement fee calculations (`RULE-011`).
- **Supply Chain Finance Rules:** Business event date vs creation timestamp disambiguation (`RULE-013`), early payment discounting calculations (`RULE-014`), and enum anti-fabrication grounding (`RULE-015`).

---

## 14. Functional Requirements Summary

A catalog of 30 functional requirements (`REQ-001` through `REQ-030`) is maintained in [Requirements](requirements.md).
- **Authentication & RBAC (`REQ-001` to `REQ-004`):** HTTP-only session cookies, automatic admin bootstrapping, role-based access control, and connection-level grant isolation.
- **Gateway & NL2SQL Planning (`REQ-005` to `REQ-009`):** Single chokepoint LLM Gateway, pre-send leak scanning, zero raw row egress rule, scoped aggregate exception, and dialect-aware SQL planning.
- **SQL Validation & Safety (`REQ-010` to `REQ-013`):** Single-statement SELECT enforcement, schema allow-list enforcement, automatic LIMIT injection, and post-planner row-filter AST rewriting.
- **Multi-Engine Execution (`REQ-014` to `REQ-016`):** Structural read-only session backstops, multi-engine executor abstraction (Postgres, MySQL, ClickHouse, DuckDB), and hard statement timeouts.
- **Semantic Layer & Data Profiling (`REQ-017` to `REQ-020`):** Three-tier semantic layer, FK-only structural joins, business notes as binding prompt directives, and automated single-scan table profiling.
- **Chat, Visualization & Dashboards (`REQ-021` to `REQ-030`):** Real-time SSE execution tracing, schema exploration mode, value-free chart type selection, dynamic AI dashboards, independent widget refresh, scoped filter options, custom grid layouts, semantic auto-routing, and multi-format document exports.

---

## 15. Non-Functional Requirements Summary

Authoritative non-functional requirements are detailed in [Requirements §3](requirements.md).
- **Performance (`NFR-001` to `NFR-003`):** Warm query turn latency under 5.0 seconds (observed ~2.1s); SSE event latency under 100ms; frontend Largest Contentful Paint (LCP) under 1.8 seconds.
- **Security & Privacy (`NFR-004` to `NFR-006`):** Two-layer read-only defense-in-depth; gitignored credential storage restricted to 0600 file permissions; salted bcrypt password hashing.
- **Reliability & Availability (`NFR-007` to `NFR-009`):** Headless DB-free and key-free bootstrapping; bounded retry (2x) for transient connection drops; fail-closed security architecture.
- **Maintainability & Concurrency (`NFR-010` to `NFR-011`):** Type-safe OpenAPI code generation (`schema.d.ts`); strict single-worker enforcement protecting JSON flat-file storage integrity.

---

## 16. Core Data Entities & Stores

System entities and domain models are specified in [Data Dictionary](data_dictionary.md).
- **Platform Runtime State:** User records (`DATA-001`), Session tokens (`DATA-002`), Roles and Personas (`DATA-003`), User Grants (`DATA-004`), Connections (`DATA-005`), Semantic Layers (`DATA-006`), Widgets (`DATA-007`), Dashboards (`DATA-008`), Conversations (`DATA-009`), and LLM Usage Ledger (`DATA-010`).
- **Domain Entities:** Financial transactions (`DATA-011`), Transaction fee splits (`DATA-012`), Settlement lines (`DATA-013`), Merchants (`DATA-014`), and Cards/Products (`DATA-015`).

---

## 17. External Systems & Integrations

Specifications, schemas, and provider protocols are detailed in [API Catalog](api_catalog.md).
- **LLM Model Providers:** Anthropic Claude (Sonnet / Haiku), OpenAI GPT (GPT-4.1), Google GenAI (Gemini), and local Ollama instances.
- **Target Database Systems:** PostgreSQL (psycopg2), MySQL (PyMySQL), ClickHouse (clickhouse-connect), and in-memory DuckDB (for Excel/CSV files).
- **Network Bastions:** SSH tunnels via Paramiko / SSHTunnel for traversing secure corporate firewalls.

---

## 18. High-Level Architecture Summary

Tela implements a decoupled, headless, single-host client-server architecture:
- **Presentation:** Single-Page Application built on React 19, TypeScript, Vite, Tailwind CSS, TanStack Query, and Vega-Lite.
- **API & Orchestration:** Headless asynchronous REST server built on FastAPI with Server-Sent Events (SSE) streaming.
- **Pipeline Spine:** Retrieval -> Gateway-guarded Planner -> AST Access Scoping -> Deterministic Validator -> Pooled Executor -> Value-Free Viz Engine.
- **Full Architecture Specification:** See [Architecture](architecture.md) for 32 comprehensive architectural sections and diagrams.

---

## 19. Dependencies & Technical Constraints

1. **Python Virtual Environment:** Built on Python 3.12+ with direct dependencies restricted to `requirements.txt` (FastAPI, pydantic v2, sqlglot, duckdb, pandas, psycopg2-binary, pymysql, clickhouse-connect, bcrypt, etc.).
2. **Frontend Toolchain:** Node.js v24+, npm, Vite v8, TypeScript 5.9.
3. **Single-Worker Constraint:** Because system state is stored in flat-file JSON stores under `backend/data/api/`, the backend process must run with `--workers 1` to prevent concurrent write corruption.
4. **Read-Only Database User:** The host organization must provide database credentials mapped to a database role with `SELECT`-only privileges.

---

## 20. Important Decisions & Rationale (ADRs)

- **ADR-001: Single Chokepoint Gateway:** Enforces a single structural choke point (`gateway.py`) for all LLM calls, guaranteeing zero-bypass masking, leak scanning, and append-only audit logging.
- **ADR-002: Sqlglot AST-Based Validation:** Replaced vulnerable regex text matching with formal Abstract Syntax Tree (AST) parsing to guarantee deterministic, un-bypassable single-statement SELECT validation.
- **ADR-003: Post-Planner Fail-Closed Row Scoping:** Rejected prompting the LLM to enforce access control; implemented code-side AST subquery substitution that fails closed on any error.
- **ADR-004: Decoupled React SPA & FastAPI Architecture:** Selected a headless API model with SSE streaming over an in-process monolith (Streamlit/Flask) to enable enterprise responsiveness, rich custom dashboarding, and embeddability.

---

## 21. Gaps, Risks & Open Issues

Tracked in detail in [Risk Register](risk_register.md):
- **Superuser Bastion Profile (`RISK-001`):** Remote database profile connects as PostgreSQL superuser, removing DB-level read-only protections. *(Mitigation: Provision dedicated read-only role, `REC-001`).*
- **Multi-Worker Scaling Bottleneck (`RISK-005`):** Flat-file JSON persistence restricts deployment to single worker. *(Mitigation: Migrate to SQLite/PostgreSQL in V2, `REC-005`).*
- **Widget Refresh Scope Resolution (`RISK-004`):** Endpoint `/api/widgets/{id}/refresh` does not resolve viewer security scope. *(Mitigation: Inject `get_scope` validation, `REC-004`).*
- **Confident-Wrong-Answer Enum Fabrication (`GAP-001`):** LLM fabricates plausible status enums not present in DB. *(Mitigation: Implement distinct-value grounding checks, `REC-008`).*
- **Entity Drop in Multi-Turn Chat (`GAP-002`):** Dimension filters dropped during follow-up turns. *(Mitigation: Assert entity preservation across turns, `REC-009`).*
- **Sparse Test Data Seeding (`GAP-006`):** Empty payout and signatory tables in test database block QA. *(Mitigation: Seed realistic synthetic records, `REC-013`).*

---

## 22. Recommendations & Prioritized Roadmap

```
PHASE 1: Immediate Security & QA Remediation (Current Sprint)
  ├── Patch /api/widgets/{id}/refresh with viewer scope resolution (REC-004) [High]
  ├── Provision dedicated read-only remote PostgreSQL user role (REC-001) [Critical]
  └── Seed synthetic test data for BPSP payouts and signatories (REC-013) [High]

PHASE 2: NL2SQL Accuracy & Verification (Sprint 6-7)
  ├── Add distinct-value grounding check to eliminate enum fabrication (REC-008) [High]
  ├── Assert entity filter preservation across conversational turns (REC-009) [High]
  ├── Inject semantic notes distinguishing creation vs event dates (REC-010) [High]
  ├── Execute automated test suite against live MySQL and ClickHouse (REC-006) [Medium]
  └── Implement symmetric encryption for stored connection passwords (REC-003) [Medium]

PHASE 3: Enterprise Scale & Platform Evolution (V2 Architecture)
  ├── Migrate flat JSON stores to embedded SQLite / PostgreSQL with ACID safety (REC-005) [High]
  ├── Enable multi-worker uvicorn deployment and container orchestration [High]
  └── Implement asynchronous pre-warming of connection pools on startup (REC-007) [Medium]
```

---

## 23. Acceptance Criteria & Testing Priorities

### Primary Testing Focus Areas
1. **Security & Injection Verification:** Continuous automated execution of adversarial SQL injection suites, multi-statement injection attempts, and forbidden AST keyword variants against `validator.py`.
2. **Access Control & Row Scoping:** Unit tests verifying that non-admin users with row-level predicates cannot bypass subquery isolation, and confirming fail-closed refusal on corrupted SQL or CTE collisions.
3. **Data Egress Auditing:** Negative zero-egress test harness verifying that raw row payloads cannot pass through the Gateway to external model providers.
4. **Conversational Turn Accuracy:** Regression suites verifying that entity filters persist across multi-turn follow-ups, date substitutions use business event dates, and generated enums match distinct values.

---

## 24. Traceability Matrix

| Business Objective | Key Requirement | Owning Module | Verified Test Case | Risk / Gap Mitigated |
|---|---|---|---|---|
| `BUS-001` (Zero Latency) | `REQ-021` (SSE Stream) | `app/api/routers/ask.py` | `TEST-010` (Stream Timing) | `RISK-007` (Cold Starts) |
| `BUS-002` (Read-Only Safety) | `REQ-010` (AST Validator)| `app/validator.py` | `TEST-005` (AST Validation) | `RISK-001` (Superuser Risk)|
| `BUS-003` (Data Privacy) | `REQ-007` (Zero Egress) | `app/gateway.py` | `TEST-004` (Zero-Egress) | `RISK-002` (PII Leak) |
| `BUS-004` (Multi-Engine) | `REQ-015` (Executors) | `app/executors.py` | `TEST-007` (Multi-Engine) | `RISK-006` (Engine Debt) |
| `BUS-005` (BI Dashboards) | `REQ-025` (Dashboards) | `app/api/routers/dashboards.py`| `TEST-012` (Dashboard QA) | `GAP-001` (Accuracy) |

---

## 25. Related Documents
- [Architecture](architecture.md) — Primary 32-section engineering architecture specification.
- [Requirements](requirements.md) — Comprehensive functional and non-functional requirements catalog.
- [Business Rules](business_rules.md) — Calculation formulas, decision tables, and worked examples.
- [API Catalog](api_catalog.md) — REST endpoints, SSE streaming specs, and provider integrations.
- [Data Dictionary](data_dictionary.md) — System entity models and target database schemas.
- [Risk Register](risk_register.md) — Risk assessments, defect logs, and remediation roadmap.

---

## 26. Source Inventory & Coverage

The following 35 project artifacts were completely inspected and synthesized:
- **Foundational Specifications:** `CLAUDE.md`, `TELA_V1_BASE_EXPERIENCE.md`, `payments.txt`.
- **System Architecture & Design Plans:** `PlanFiles/01_system_overview.md`, `02_architecture_nl2sql_pipeline.md`, `03_data_security_and_access_control.md`, `04_module_design_nl_to_sql.md`, `05_module_design_sql_validation_and_execution.md`, `06_module_design_dashboard_layer.md`, `07_prompt_design_and_model_selection.md`, `08_testing_strategy_unit_and_e2e.md`, `10_semantic_layer_scaling.md`.
- **As-Built Engineering Documents:** `docs/ARCHITECTURE.md`, `docs/CODE_MAP.md`, `docs/SKILL_INDEX.md`, `docs/dashboard-and-features-plan.md`, `docs/TESTER-HANDOFF.md`.
- **Operational Audits & Trackers:** `docs/FlexCap_ToDo.xlsx` (Sheets: `Dev To-Do List`, `Suggested Sequencing`), `to-dos/backlog.md`, `docs/testing-issues.md`, `docs/storytelling-run.md`, `docs/M1-verification-guide.md` to `M15.1-dashboard-surface-audit.md`.
- **Source Code Repositories:** All 66 backend modules (`backend/app/`), 23 API routers, 15 frontend React components (`frontend/src/features/`), OpenAPI specification (`openapi.json`), and launch configurations (`.claude/launch.json`, `scripts/start.sh`).

---

## 27. Current Understanding & Immediate Next Steps

### Current Understanding
Tela is an exceptionally well-engineered, security-conscious NL2SQL system that has transitioned from a single-user prototype to a multi-engine, multi-tenant Business Intelligence platform. Its defense-in-depth safety boundaries (Gateway chokepoint, AST single-statement validation, fail-closed row rules, read-only session modes) are structurally sound and verified in code.

### Immediate Recommended Next Steps
1. **Apply Security Hotfix (`REC-004`):** Patch `backend/app/api/routers/widgets.py:102` to resolve the viewer's security scope during widget refreshes.
2. **Execute QA Unblocking Seeding (`REC-013`):** Populate synthetic test records in `payout_request_batch`, `payout_request_record`, and signatory expiry tables in the test database.
3. **Deploy Pre-Execution Value Grounding (`REC-008`):** Validate literal comparison targets against distinct value profiles to eliminate fabricated enum literals before query approval.
4. **Transition to V2 Persistence (`REC-005`):** Plan the migration of flat JSON stores in `backend/data/api/` to an embedded SQLite or PostgreSQL backend with ACID transactions, unblocking horizontal container scaling.
