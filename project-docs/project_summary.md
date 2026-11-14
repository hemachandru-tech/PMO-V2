# Project Summary — Tela, Conversational PMO Analytics and Dashboard Platform

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform (Prodian Infotech) |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | **L1 – Business only** for the product being built. Reference designs (S10–S31) are secondary documents describing other systems; no source code, DDL, OpenAPI spec or deployment file for Tela was supplied. Technology stack is stakeholder-mandated (React, Python, PostgreSQL + pgvector, Docker, connector list); everything else is **Proposed**. |
| Sources analyzed | 31 files — see [Source inventory and coverage](#source-inventory-and-coverage) |
| Related documents | [Architecture](architecture.md) · [Functional specification](function_document.md) · [Proposed technical design](technical_document.md) · [Database schema](db_scheme.md) · [Requirements](requirements.md) · [Business rules](business_rules.md) · [API catalog](api_catalog.md) · [Data dictionary](data_dictionary.md) · [Risk register](risk_register.md) · [Diagram index](diagrams/README.md) |

---

## Table of contents

1. [Executive summary](#executive-summary)
2. [Evidence level and confidence](#evidence-level-and-confidence)
3. [Product vision and business objectives](#product-vision-and-business-objectives)
4. [Problem statement and target users](#problem-statement-and-target-users)
5. [Product explained at three levels](#product-explained-at-three-levels)
6. [End-to-end value example](#end-to-end-value-example)
7. [Domain overview](#domain-overview)
8. [Glossary](#glossary)
9. [Business model and value proposition](#business-model-and-value-proposition)
10. [Stakeholders and user roles](#stakeholders-and-user-roles)
11. [Product scope and boundaries](#product-scope-and-boundaries)
12. [Module and feature inventory](#module-and-feature-inventory)
13. [Business workflows](#business-workflows)
14. [Key business rules and calculations](#key-business-rules-and-calculations)
15. [Functional requirements](#functional-requirements)
16. [Non-functional requirements](#non-functional-requirements)
17. [Core data entities](#core-data-entities)
18. [External systems and integrations](#external-systems-and-integrations)
19. [High-level architecture summary](#high-level-architecture-summary)
20. [Dependencies and constraints](#dependencies-and-constraints)
21. [Important decisions and rationale](#important-decisions-and-rationale)
22. [Gaps, risks and conflicts](#gaps-risks-and-conflicts)
23. [Open questions](#open-questions)
24. [Roadmap and recommendations](#roadmap-and-recommendations)
25. [Acceptance criteria and testing priorities](#acceptance-criteria-and-testing-priorities)
26. [Traceability](#traceability)
27. [Related documents](#related-documents)
28. [Source inventory and coverage](#source-inventory-and-coverage)
29. [Current understanding and next steps](#current-understanding-and-next-steps)
30. [Change log](#change-log)

---

## Executive summary

**Tela** is a chat-first reporting and dashboard platform for Project Management Office (PMO) and delivery leadership. Admins connect existing data sources **read-only**; people ask questions in plain English and Tela answers with a live chart, table or figure. Answers can be refined in words, pinned as widgets, assembled into reports and dashboards, certified by the PMO admin, and published as versioned dashboards to Microsoft-group-based roles (S01 §1, §4).

The value chain is fixed by the scope document (S01 §1):

```
Connection → Data model → Widget → Report → Dashboard → Published
```

Three principles run through every part of the product:

1. **Read-only, no copies.** Tela never writes to, changes or copies source tables. Grouping, banding and joining happen in the query only (S01 §4.1, §4.7, §6).
2. **One certified definition per question.** Measures are written once, certified by the admin, and reused by every widget and chat answer; look-alike definitions are flagged (S01 §3, §4.8).
3. **Scope comes from identity.** Role and row scope come from Microsoft sign-in group membership and are enforced in every query — dashboards, chat, drill-downs, exports and custom SQL. Filters can narrow scope, never widen it (S01 §2, §6).

**What is new compared with the scope document.** The stakeholder instruction of 2026-10-21 mandates React + Python, PostgreSQL as the internal database, pgvector for retrieval-augmented generation (RAG), Docker images per service, and source connectors for PostgreSQL, MySQL, MongoDB, Excel and **Microsoft Dataverse** (plus SQL Server from S01). Two of these conflict with S01 (file uploads were out of scope; only three relational engines were listed) and are recorded as gaps, not silently resolved ([Gaps, risks and conflicts](#gaps-risks-and-conflicts)).

**Key finding.** The PMO reference data is extracted from Microsoft Dynamics CRM (field names such as `tpgmea_projectnumber`, S15 §7). Dynamics 365 data lives in Dataverse, so the Dataverse connector is very likely the direct, governed path to live TPG data — replacing the batch MySQL extract used by the reference PMO dashboard (**Inferred**; confirm with STK-005, Q-015).

**Recommended direction (Proposed).** Build Tela as a set of Docker services around a FastAPI modular monolith, with a **spec-first query engine**: the LLM proposes a typed query specification against the certified data model; deterministic code compiles it per engine (SQL dialects via sqlglot, MongoDB aggregation pipelines, DuckDB over Excel, Dataverse TDS), validates it, injects scope and executes it read-only. The LLM never writes executable SQL for answers and never sees raw rows by default ([ADR-001](architecture.md#architecture-decision-records)). Start with the PMO starter pack on one source, then add authoring, certification and publishing, then the remaining connectors.

> The product is **not** implemented. This pack is a proposed design built on the scope document and on documentation of three reference systems whose code was not supplied.

---

## Evidence level and confidence

| Area | Evidence level | What it rests on | What cannot be confirmed |
|---|---|---|---|
| Product scope, roles, journeys J1–J11 | L1 | S01 (derived from the "Tela I clickable journeys" prototype), S04–S09 | Real user behaviour; final role catalogue |
| PMO metrics for the starter pack | L1 (reference) | S13 business rules of the PMO reference app; S15 field mapping | That the rules match the client's charter; production schema |
| NL2SQL, guardrails, RAG patterns | L1 (reference) | S02, S24–S31 describe other systems; their own claims of L3–L5 are second-hand | Whether that code is reusable or behaves as documented |
| Identity, provisioning, charting options | L1 | S03 research synthesis | Tenant-specific Entra configuration |
| Technology stack | Stakeholder-mandated | Instruction 2026-10-21 | Versions, sizing |
| Architecture, APIs, schema, infrastructure | **Proposed** | This pack | Everything at runtime |

**Artifacts that would raise confidence** (priority order): (1) Dataverse environment details or the current TPG schema/DDL (GAP-009); (2) approved LLM-provider policy (Q-009); (3) Entra tenant group catalogue and scope attributes (Q-003); (4) source repositories of pmo_dashboard, Tela NL2SQL and TelaKnowledge with reuse rights (Q-019); (5) the clickable prototype HTML (`user_journery.html`, cited in S05 but not supplied here); (6) agreed accuracy, performance and freshness targets (Q-001, Q-010, Q-017).

---

## Product vision and business objectives

**Vision.** Anyone in the PMO chain of command can move from a business question to a trusted, role-scoped, explainable answer — and from a good answer to a governed dashboard — without SQL and without waiting for an analyst.

| ID | Objective | Measure of success (to agree) |
|---|---|---|
| BUS-001 | Self-service answers from existing data without SQL | Share of recurring PMO questions answered in Tela; time-to-answer (target TBD, Q-010) |
| BUS-002 | One certified definition per business question | Zero conflicting figures for the same question across widgets and chat (S01 §6 Consistency) |
| BUS-003 | Role-scoped access derived from Microsoft sign-in | Zero out-of-scope rows in adversarial tests across all surfaces |
| BUS-004 | Chat-built, governed reports and dashboards with versioned publishing | Dashboards published to roles only with certified widgets; restore works for every version |
| BUS-005 | Connect heterogeneous sources read-only without copying data | Connectors live for PostgreSQL, MySQL, SQL Server, MongoDB, Excel/CSV, Dataverse |
| BUS-006 | Every figure traceable to a readable definition and data date | 100 % of figures expose definition, scope, filters and "data as of" |
| BUS-007 | Fast time to value through certified starter packs | PMO pack installed and usable on first connection |
| BUS-008 | Easy, repeatable deployment as Docker services | Same images deploy to SaaS and customer-hosted environments |

Baselines and numeric targets are not supplied; they are listed as open questions rather than invented.

---

## Problem statement and target users

**Problem.** PMO reporting today depends on analysts writing SQL or spreadsheet pivots, and different reports use different filters, so the same question ("how many go-lives are overdue?") yields different numbers. Self-reported RAG status hides slippage (S10 §4). Generic AI query tools guess at definitions, can leak data and are hard to govern (S24 §6).

**Target users.** Chief Information Officers (CIOs), executives, PMO directors and analysts, delivery and project managers (S01 §2, S10 §7).

---

## Product explained at three levels

**Simple.** A dashboard you can talk to. Ask "which go-lives are overdue in KSA?" and you get the chart, the rule behind the number, and the date of the data — and you only ever see the projects you are allowed to see.

**Business.** Tela standardises PMO measures (overdue go-lives, calculated health, forecast versus approved budget, governance gates), lets creators build and share their own views, and lets the PMO admin certify the definitions that official dashboards rely on. Published dashboards are versioned and can be rolled back.

**Technical.** A React single-page app talks to a Python FastAPI service. An agent orchestrator turns questions into typed query specifications, grounded by a pgvector knowledge index of the certified data model and gold examples. A query engine compiles each specification for the target engine, validates it, injects the viewer's row scope and runs it through an isolated read-only connector runner. Metadata lives in PostgreSQL; secrets in OpenBao or Vault; uploaded files and exports in S3-compatible object storage. Every component ships as a Docker image.

---

## End-to-end value example

*Illustrative — names and figures are invented in the style of S01 and S10.*

1. **Connect.** The PMO admin adds the TPG Dataverse environment as a read-only connection. Tela reads the schema, recognises the PMO pack, and installs certified measures (Open portfolio, Overdue go-lives, Calculated health, Financial exposure) and starter reports. The model assistant asks whether cancelled projects count as open; the admin answers "no" and approves model v1.
2. **Read.** A CIO signs in with Microsoft. Her home page shows "Published to my role" with the *Portfolio Overview* dashboard. A locked scope chip reads "CIO: Nomsa Osei". Data as of 06:00.
3. **Ask.** She types "overdue go-lives by region". Tela returns a ranked bar chart with the headline "4 go-lives are overdue; KSA has 2", the certified rule (state Active, planned go-live before the as-of date, not yet deployed), her scope and the data date — badged **Certified**.
4. **Refine and pin.** "Only critical priority" narrows the spec; she pins it to her *Weekly review* report.
5. **Govern.** An analyst builds a "Core vs Growth" grouping of portfolios and asks for certification. The admin sees that a similar certified grouping exists (look-alike), merges it, and the analyst's widgets switch to the certified field.
6. **Publish.** The admin previews the dashboard as the Delivery manager role, sees finance fields hidden for that role, passes all pre-publish checks and publishes v9. A week later v9 is replaced by v10; v9 stays restorable.

Outcome: the weekly executive pack is a live dashboard; every figure is the certified one, and nobody sees data outside their scope.

---

## Domain overview

Tela serves **PMO portfolio governance**: delivery projects (lifecycle state, planned versus baselined go-live, forecast cost (EAC) versus approved budget, reported RAG versus calculated health) and **demand intake** (requests moving through DOU, DARB and SOW governance gates before becoming projects). The reference PMO dataset has two tables — `projects` and `project_requests` — sourced from Dynamics CRM (S15). Key domain cautions carried from the reference (S13):

- `cost_total_usd` is a **forecast (EAC)**, not actual spend; there is no spend, timesheet, milestone, RAID or snapshot data, so eight classic PMO reports must stay disabled with the gap named (S10 §8).
- "Reported status" (PM's RAG) and "Calculated health" (objective score) are different and must both be shown.
- All ageing uses one server "as-of" date, never the browser clock.

Detailed term definitions, classifications and owners are in [data_dictionary.md](data_dictionary.md#business-terms).

---

## Glossary

| Term | Meaning |
|---|---|
| Connection | A read-only link to one data source (PostgreSQL, MySQL, SQL Server, MongoDB, Excel/CSV file, Dataverse) (S01 §1). |
| Data model | What the data means: entities, links with cardinality, measures, dimensions. Chat proposes; admin approves as a numbered version (S01 §4.1). |
| Measure | Something counted or summed, with its rule written once (S01 §3). |
| Dimension | Something you group by (Region, Programme, Lifecycle phase). |
| Widget | One live chart, table or figure that stores the *question* (spec), never the numbers (S01 §1). |
| Query spec | Tela's typed description of a widget's question: measure, breakdown, filters, limit, sort, visual (Proposed, ADR-001). |
| Report | A page of widgets on a 12-column grid with sections, headline figures and page filters. |
| Dashboard | Several reports as tabs or grouped on one page with one shared filter bar. |
| Published | A dashboard version made live for chosen roles, with preview and rollback. |
| Certified | Approved by the PMO admin; same question gives the same number everywhere. |
| Personal | Made by one person; shareable; must be certified before publishing to a role. |
| Custom SQL | A hand-written or chat-drafted read-only SELECT widget, badged until certified. |
| Scope | The rows a person may see, set by role; filters narrow, never widen. |
| Starter | Ready-made certified reports installed with a pack; can be copied, hidden, deleted and restored. |
| Look-alike | A definition similar to an existing certified one that would give a different answer to the same question. |
| RAG | Retrieval-augmented generation: grounding agent prompts with retrieved, approved knowledge (pgvector). |
| Gold example | A verified question → query spec (or SQL) pair used for grounding and evaluation. |
| EAC | Estimate at completion — forecast total cost. |
| DOU / DARB / SOW | Document of Understanding / Design Architecture Review Board / Statement of Work — demand governance gates (S10 §6). |
| TDS endpoint | Dataverse's read-only SQL (Tabular Data Stream) interface (to verify against Microsoft documentation). |

---

## Business model and value proposition

A commercial model (pricing, licensing, who pays) is **not specified in the source**. The value proposition established by the sources is: faster, consistent PMO reporting; governed self-service; reuse of certified content through starter packs; and no data movement out of the client's systems (S01, S05). S07 also asks that Tela be benchmarked against Power BI/Tableau before committing; that comparison is not repeated here and remains a stakeholder decision.

---

## Stakeholders and user roles

| ID | Stakeholder / role | Responsibilities | Primary surfaces |
|---|---|---|---|
| STK-001 | Admin (PMO) | Connect sources, approve data model, certify definitions and custom SQL, publish to roles, manage starter content (S01 §2) | Connections, Model assistant, Certification queue, Publish, Starter content |
| STK-002 | Creator (CIO, delivery manager, analyst) | Everything a viewer can, plus chat-build widgets/reports/dashboards, personal groupings, bands, joins, custom SQL, share, request certification | Ask, Canvas, Fields, Compose |
| STK-003 | Viewer | Open published dashboards, filter, view definitions, drill, ask in scope; cannot build or pin | Home, Dashboard viewer |
| STK-004 | Audience business roles (CIO, Executive, Delivery manager) | Targets of publishing; determine scope and field hiding | Published dashboards |
| STK-005 | Data source owner / DBA | Provide read-only identities, approve objects, own source freshness | Connection setup |
| STK-006 | Identity and security owner | Entra app registration, group catalogue, conditional access | Keycloak / Entra |
| STK-007 | Platform operator (DevOps) | Deploy and run Docker services, backups, monitoring | Infrastructure |
| STK-008 | PMO business owner | Owns KPI definitions; signs off the PMO pack rules | Certification, Starter pack |
| STK-009 | Model-policy approver | Approves LLM providers and data handling (named "Vijay" in S05/S06) | LLM settings |

The full role × feature × action matrix is in [function_document.md](function_document.md#roles-and-permission-matrix).

---

## Product scope and boundaries

**In scope — Phase 1** (S01 §4, plus stakeholder additions):

- Connections and data model with chat model assistant, approval and versioning; starter pack detection.
- Connectors: PostgreSQL, MySQL, SQL Server (S01); MongoDB, Excel/CSV, Dataverse (stakeholder-mandated).
- Knowledge index (pgvector) of the certified model, glossary, gold examples and chart patterns to ground agents and enhance charts (stakeholder-mandated; Proposed design).
- Home shelves, ask bar, reading dashboards with filters, locked scope, definitions, drill and PDF/Excel export.
- Ask, refine and pin; report from one request; canvas editing by hand and by chat; chart integrity guard; drafts and preview as role.
- Personal groupings, bands, cross-table measures along approved links; custom SQL widgets with check and run.
- Certification queue with look-alike detection and semantic checks; certify, merge, reject.
- Dashboard composition (tabs or grouped), publish to roles with pre-publish checks, versions and restore, share with named people.
- Starter content management and pack updates.
- Microsoft SSO with role and scope from groups; audit; LLM provider settings.

**Out of scope — later phases** (S01 §5): joins across two sources in one widget; scheduled e-mail delivery; write-back to source systems; status-over-time snapshots. Write-back, if ever added, needs a separate typed-command, approval and audit design (S06 §11).

**Changed by stakeholder instruction:** "File uploads as a data source" (S01 §5) is now **in scope** as the Excel/CSV connector — pending confirmation (Q-011).

---

## Module and feature inventory

Full specifications: [function_document.md — Feature specifications](function_document.md#feature-specifications).

| Module | Features | Journeys |
|---|---|---|
| M01 Identity and access | FEAT-001 Microsoft sign-in · FEAT-002 Group to role and scope mapping · FEAT-003 Row scope and field hiding enforcement · FEAT-004 User and group provisioning | J2 |
| M02 Connections and data model | FEAT-005 Connection management · FEAT-006 Schema introspection · FEAT-007 Model assistant · FEAT-008 Plain-language model corrections · FEAT-009 Model approval and versioning · FEAT-010 Starter pack detection and install · FEAT-011 Excel and CSV sources · FEAT-012 Dataverse connector · FEAT-013 MongoDB connector | J1, J11 |
| M03 Knowledge and RAG | FEAT-014 Semantic knowledge index · FEAT-015 Gold examples and evaluation set · FEAT-016 Chart enhancement suggestions | All |
| M04 Home | FEAT-017 Home shelves and ask bar | J2 |
| M05 Ask | FEAT-018 Ask a question · FEAT-019 Clarifying questions · FEAT-020 Refine in words · FEAT-021 Pin a widget · FEAT-022 Ask about a widget | J4 |
| M06 Reports | FEAT-023 Report from one request · FEAT-024 Report canvas · FEAT-025 Edit with chat · FEAT-026 Chart integrity guard · FEAT-027 Drafts and preview as role | J5, J6 |
| M07 Advanced fields | FEAT-028 Grouping · FEAT-029 Bands · FEAT-030 Cross-table measures · FEAT-031 Custom SQL widget · FEAT-032 Request certification | J7 |
| M08 Certification | FEAT-033 Certification queue · FEAT-034 Certification item detail · FEAT-035 Look-alike detection · FEAT-036 Custom SQL semantic checks · FEAT-037 Certification decisions | J8 |
| M09 Dashboards and publishing | FEAT-038 Compose a dashboard · FEAT-039 Publish to roles · FEAT-040 Versions and restore · FEAT-041 Share with named people | J9 |
| M10 Reading | FEAT-042 Dashboard viewer · FEAT-043 Definition popover · FEAT-044 Drill to records · FEAT-045 Export to PDF and Excel | J3 |
| M11 Starter content | FEAT-046 Starter content management · FEAT-047 Starter pack updates | J10 |
| M12 Platform administration | FEAT-048 Audit log · FEAT-049 LLM provider settings and usage · FEAT-050 Data freshness and result cache | — |
| M13 PMO starter pack | FEAT-051 PMO starter pack content | J1 |

Specification status: features in M02 (except the three new connectors), M04–M11 are **explicitly specified** in S01; M01 is partially specified (SSO and scope named; mapping and provisioning inferred from S03); M03, FEAT-011–013, FEAT-048–051 are **stakeholder-mandated or inferred**.

---

## Business workflows

Step-by-step flows and Mermaid diagrams are in [function_document.md](function_document.md#end-to-end-workflows) and [diagrams/](diagrams/README.md).

| Workflow | Who | Before Tela | With Tela |
|---|---|---|---|
| Onboard a source (J1, J11) | Admin | DBA hands over extracts; analysts reverse-engineer meaning | Read-only connection, chat-proposed model, explicit questions about ambiguous meaning, approve v1, optional starter pack |
| Read a dashboard (J3) | Viewer | Static slide decks, weekly | Live tabs, shared filters, locked scope, definition on click, drill, export |
| Ask, refine, pin (J4) | Creator | Ticket to analyst | Answer is already a live widget; refine in words; pin with only suitable visuals |
| Report from one request (J5) | Creator | Hours of building | Plan first (gaps named, not guessed), edit plan in words, build validates every widget |
| Govern definitions (J7, J8) | Creator → Admin | Conflicting spreadsheet logic | Personal fields applied in query only; certification queue with look-alike and semantic checks; certify / merge / reject logged |
| Publish (J9) | Creator builds, Admin publishes | E-mail attachments | Preview as role or person; blocking pre-publish checks; numbered versions; restore |
| Manage starters (J10) | Admin | — | Hide per role, delete with explained effects, restore, pack updates that never touch user copies |

Bottlenecks and controls still missing at L1: who may publish (GAP-006), data freshness (GAP-001), export limits (GAP-007).

---

## Key business rules and calculations

Full catalog with worked examples: [business_rules.md](business_rules.md).

- **Safety rules (RULE-001 to RULE-007):** verified read-only identity per connection; a single read-only statement for custom SQL; only approved objects; mandatory scope injection that fails closed; filters intersect scope; 10,000 rows and 20 seconds per query (S01 §4.7); page filters injected at `:page_filters`.
- **Governance rules (RULE-008 to RULE-017, RULE-024):** only suitable visuals; only certified widgets publish to roles; personal definitions enter the queue only when shared or publish-bound; look-alike detection; grouping catch-all and "Not set"; band edges (exactly 1M goes into 1–5M); joins only along approved links with fan-out protection; version numbering and restore as a new version; starter deletion effects; pre-publish checks.
- **Trust rules (RULE-018 to RULE-023, RULE-025):** "data as of" resolution; LLM egress minimisation (no raw rows by default); result masking; pre-send leak scanning; narratives grounded in returned values only; no cross-currency sums; drill authorisation and caps.
- **PMO pack rules (RULE-026 to RULE-039, reference rules from S13, need STK-008 sign-off):** open portfolio, in delivery, overdue go-live, schedule risk, RAG contradiction, stale project, delivery horizons, 4-factor calculated health (Schedule 35 %, Cost 25 %, Progress 25 %, Issues 15 %; Green ≥ 75, Amber 50–74, Red < 50; at least 60 % evidence coverage; severe-condition overrides), over budget, financial exposure, open demand, overdue DOU/DARB/SOW gates.

Illustrative health example (from S13): schedule 50, cost 75, progress 25, issues 50 → 50 × 0.35 + 75 × 0.25 + 25 × 0.25 + 50 × 0.15 = 17.5 + 18.75 + 6.25 + 7.5 = **50.0 → Amber**.

---

## Functional requirements

120 functional requirements (REQ-001 to REQ-120), grouped by module, are owned by [requirements.md](requirements.md#functional-requirements):

| Module | Range | Module | Range |
|---|---|---|---|
| M01 Identity and access | REQ-001–008 | M08 Certification | REQ-084–092 |
| M02 Connections and data model | REQ-009–034 | M09 Dashboards and publishing | REQ-093–101 |
| M03 Knowledge and RAG | REQ-035–041 | M10 Reading | REQ-102–110 |
| M04 Home | REQ-042–046 | M11 Starter content | REQ-111–114 |
| M05 Ask | REQ-047–060 | M12 Platform administration | REQ-115–118 |
| M06 Reports | REQ-061–072 | M13 PMO starter pack | REQ-119–120 |
| M07 Advanced fields | REQ-073–083 | | |

---

## Non-functional requirements

28 NFRs are owned by [requirements.md](requirements.md#non-functional-requirements). The ones sourced directly from S01 §6 are: read-only data safety (NFR-001), scope on every surface (NFR-002), field-level hiding (NFR-003), Microsoft SSO (NFR-004), query limits (NFR-005), definition consistency (NFR-006), auditability (NFR-007), transparency (NFR-008), reversibility (NFR-009) and chart integrity (NFR-010). Responsiveness (NFR-011) has only prototype observations — report build ~6 s and custom SQL ~0.4 s on sample data — and the targets are **to be confirmed** (Q-010). Deployability as Docker services (NFR-019) and an extensible connector framework (NFR-025) are stakeholder-mandated.

---

## Core data entities

Tela stores **metadata only**; source rows are never persisted (Proposed, ADR-010). The full proposed schema (51 tables in nine PostgreSQL schemas) is in [db_scheme.md](db_scheme.md#table-catalog).

| Schema | Holds | Examples |
|---|---|---|
| iam | Tenants, users, identities, groups, roles, scope and field policies | DATA-001–010 |
| conn | Connections (secret references only), introspected objects, uploaded file sources | DATA-011–013 |
| model | Data models, versions, entities, fields, links, measures, dimensions, starter packs | DATA-014–023 |
| defs | Personal fields, custom SQL definitions | DATA-024–025 |
| content | Widgets, reports, sections, dashboards, versions, publications, shares, undo log | DATA-026–035 |
| gov | Certification requests, decisions, look-alike findings | DATA-036–038 |
| chat | Conversations, turns, turn events | DATA-039–041 |
| rag | Knowledge documents, chunks with `vector(1024)` embeddings, gold examples | DATA-042–044 |
| ops | Audit, LLM invocations, query executions, cache index, export jobs, outbox, notifications | DATA-045–051 |

---

## External systems and integrations

Boundary detail: [architecture.md](architecture.md#connector-architecture); contracts: [api_catalog.md](api_catalog.md).

| System | Purpose | Notes |
|---|---|---|
| Microsoft Entra ID (via Keycloak) | Sign-in, group claims | OIDC authorization code + PKCE (S03 §5.1); group overage and mapping to verify |
| Microsoft Graph (optional) | Group lookup / provisioning support | Only where SCIM is insufficient (S03) |
| SCIM 2.0 (optional) | User and group provisioning from Entra | FEAT-004, Should |
| LLM providers via gateway | Planning, clarification, narratives, model assistant | Provider not approved yet (Q-009); candidates per S26/S31: Azure OpenAI, Anthropic Claude, Google Gemini, local Ollama |
| Embedding runtime | Vectors for the knowledge index | 1024-dimension default (mirrors S31); local or provider (Q-016) |
| PostgreSQL, MySQL, SQL Server | Relational sources | SELECT-only identities plus session read-only and timeouts |
| MongoDB | Document source | Read role; compiled aggregation pipelines; no `$out`, `$merge`, server-side JavaScript |
| Excel / CSV | File sources | Stored in object storage; queried read-only through DuckDB (ADR-012) |
| Microsoft Dataverse | Dynamics 365 / TPG data | TDS (read-only SQL) endpoint first, Web API (OData/FetchXML) fallback (ADR-013); environment enablement and limits to verify |
| Object storage | Uploaded files, export files | S3-compatible (MinIO) or Azure Blob |
| Secrets store | Source credentials, provider keys | OpenBao or HashiCorp Vault; never in the database or prompts |

---

## High-level architecture summary

See [architecture.md](architecture.md) for components, ADRs, security, deployment, and agent/RAG design.

- **Services (Docker images):** web (React SPA on nginx), api (FastAPI modular monolith containing the agent orchestrator and query engine), connector-runner (isolated read-only execution), llm-gateway (masking, leak scan, audit, provider adapters), worker (Celery: introspection, indexing, exports, cache refresh, pack updates), Keycloak, PostgreSQL 16 + pgvector, Redis, OpenBao/Vault, MinIO, embedding runtime, observability stack, reverse proxy.
- **Query path (one shared path for every surface):** spec or custom SQL → compile per engine → validate → inject scope and field policy → enforce limits → execute read-only → mask → visualise → grounded narrative.
- **Knowledge path:** approved model definitions, glossary, gold examples, chart patterns and reviewed documents → chunk → embed → pgvector HNSW + full-text → hybrid retrieval filtered by tenant and connection → agent prompts with citations.

---

## Dependencies and constraints

- **Mandated:** React frontend, Python backend, PostgreSQL internal database, pgvector for RAG, Docker images per service, connector coverage including Dataverse.
- **Source access:** client environment is restricted; read-only identities and network paths must be granted by STK-005 (S09 RISK-013).
- **LLM policy:** no client data or schema may go to a model provider before written approval by STK-009 (S06 §9).
- **Identity:** Microsoft Entra tenant, app registration and group claims from STK-006.
- **Reference reuse:** reuse rights for the three reference codebases are unknown (Q-019); this pack reuses their *ideas*, not code.
- **No timelines:** S04 records that discussed delivery durations must not be used as commitments; this pack gives phases and exit criteria only.

---

## Important decisions and rationale

Full records: [architecture.md — Architecture decision records](architecture.md#architecture-decision-records). All are **Proposed**.

| ADR | Decision | Why |
|---|---|---|
| ADR-001 | Typed query spec instead of LLM-written SQL | S01 says the engine writes the SQL; S02 compiles typed plans; removes a class of injection and hallucination errors |
| ADR-002 | Pluggable connector adapters with per-engine read-only enforcement | Six engines with different query languages and safety controls |
| ADR-003 | Keycloak broker with Entra ID, app-owned authorization | S03 recommendation; supports other IdPs later |
| ADR-004 | Single PostgreSQL metadata store with pgvector | Mandated; avoids SQLite and JSON-file limits seen in S16 and S30 |
| ADR-005 | Docker images for every service, Compose and Helm profiles | Mandated; same images for SaaS and customer-hosted (S03 §4) |
| ADR-006 | Modular monolith API plus separate runner, gateway and worker | Simple to build (S03) while isolating risky execution and LLM egress |
| ADR-007 | Apache ECharts rendering from an internal chart spec | S03 recommendation; resolves conflict C5 |
| ADR-008 | Provider-neutral LLM gateway chokepoint | S24 ADR-001, S02; policy undecided |
| ADR-009 | Scope enforcement by AST rewrite plus source read-only backstop | S13 RULE-018, S27 RULE-004; defence in depth |
| ADR-010 | No persisted result snapshots; scope-keyed short-TTL cache | S01 "never the numbers"; resolves conflict C6 |
| ADR-011 | Celery with Redis for background jobs | S03 "Celery or equivalent" |
| ADR-012 | Excel and CSV via DuckDB over object-stored files | S24 Tela pattern |
| ADR-013 | Dataverse via TDS endpoint first, Web API fallback | Read-only SQL path keeps the same compiler and validator |
| ADR-014 | MongoDB via compiled aggregation pipelines | No SQL; scope as a leading `$match` |

---

## Gaps, risks and conflicts

Full register (22 risks, 15 gaps, 20 recommendations): [risk_register.md](risk_register.md).

**Source conflicts (recorded, not resolved):**

| # | Conflict | Sources | Handling |
|---|---|---|---|
| C1 | File uploads out of scope vs Excel required | S01 §5 vs stakeholder | Excel/CSV treated as in scope; confirm (GAP-011, Q-011) |
| C2 | Three relational engines vs a wider list | S01 §4.1 vs stakeholder | All six included; priority order to confirm (GAP-012, Q-012) |
| C3 | Row/time limits differ | S01 10,000 / 20 s; S13 200 / 15 s; S27 10,000 / 15 s; S31 5,000 / 30 s | Adopt S01 values (Proposed) |
| C4 | "Nothing set up by hand" vs needing a group mapping | S01 §2 | One-time admin mapping or claim-derived scope (GAP-013, Q-013) |
| C5 | Chart library | S10 Recharts; S24 Vega-Lite; S03 ECharts | ECharts (ADR-007) |
| C6 | Snapshot fallback vs "never the numbers" | S20 vs S01 §1 | No snapshots (ADR-010) |
| C7 | Admin publishes vs separate governor role | S01 J9 vs S05 | Open (GAP-006, Q-006) |

**Highest-priority risks:** scope leakage across surfaces (RISK-001); confidently wrong answers such as fabricated status values or dropped entities (RISK-002, lessons from S30); unapproved LLM data handling (RISK-003); Dataverse endpoint constraints (RISK-004); credential compromise (RISK-009); prompt injection through metadata and documents in the RAG index (RISK-014).

---

## Open questions

| ID | Question | Owner | Source |
|---|---|---|---|
| Q-001 | What is the refresh model and cadence per connection ("data as of 06:00" implies a schedule or cache)? | STK-001, STK-005 | S01 §8.1 |
| Q-002 | How many starter packs ship in Phase 1 (only PMO?) and who authors and versions them? | STK-008 | S01 §8.2 |
| Q-003 | Are roles fixed or configurable, and how do Microsoft groups map to roles and scope? | STK-006 | S01 §8.3 |
| Q-004 | Where do drill record links point — a Tela record panel or the source system (e.g. Dynamics form)? | STK-001 | S01 §8.4 |
| Q-005 | Can shared items be edit-shared or only view-only? | STK-008 | S01 §8.5 |
| Q-006 | Can creators publish to roles, or only admins? Is a separate governor role needed? | STK-008 | S01 §8.6, S05 |
| Q-007 | How do certified model changes (v1 → v2) affect existing widgets and published dashboards? | STK-001 | S01 §8.7 |
| Q-008 | What are export row limits and formatting rules for PDF and Excel? | STK-008, STK-006 | S01 §8.8 |
| Q-009 | What data (schema, samples, results) may be sent to which approved LLM providers, with what retention? | STK-009 | S01 §8.9, S06 §9 |
| Q-010 | What are production query, build and page-load targets? | STK-008 | S01 §8.10 |
| Q-011 | Confirm Excel/CSV sources in Phase 1 despite S01 §5. Size limits and retention of uploaded files? | Sponsor | Conflict C1 |
| Q-012 | Full connector list and priority order (is "etc." Oracle, ClickHouse, Snowflake, REST APIs?) | Sponsor | Conflict C2 |
| Q-013 | Where are group-to-role/scope mappings configured — in Tela admin, Keycloak, or Entra app roles? | STK-006 | Conflict C4 |
| Q-014 | SaaS, customer-hosted, or both for Phase 1? Which cloud (Azure assumed by S06 §13)? | Sponsor | S03 §4, S06 §13 |
| Q-015 | Dataverse access: TDS endpoint enabled? Application user and security role? Which tables are in scope? | STK-005 | Inferred from S15 |
| Q-016 | Embedding model hosting: local runtime or provider API? Which model? | STK-009 | S31 |
| Q-017 | What answer-accuracy threshold must the ground-truth suite reach for UAT? | STK-008 | S07 §7 |
| Q-018 | Audit and conversation retention periods? | STK-006 | — |
| Q-019 | May code from pmo_dashboard, Tela NL2SQL or TelaKnowledge be reused, and under what licence or ownership? | Sponsor | S05 Q-002 |
| Q-020 | Is write-back a future committed phase or excluded? | Sponsor | S05 Q-011, S01 §5 |

---

## Roadmap and recommendations

Phases have exit criteria, not dates (per S04). Detailed recommendations: [risk_register.md — Recommendations](risk_register.md#recommendations).

| Phase | Goal | Main content | Exit criteria |
|---|---|---|---|
| P0 Foundation | Safe skeleton | Docker Compose stack; Keycloak + Entra sign-in; metadata DB with migrations and RLS; LLM gateway; health checks, restart policies, CI from day one (REC-012) | Sign-in works; role and scope resolved; all containers healthy; CI green |
| P1 Governed read path | One source, trusted answers | One connector (Dataverse or PostgreSQL TPG extract — REC-019), introspection, model assistant, approval v1, PMO starter pack, spec-first query engine with validator and scope injection (REC-003, REC-004), dashboard viewer, definitions, drill, ask with certified answers, ground-truth suite (REC-010) | Zero out-of-scope rows in adversarial tests; metric parity across surfaces; accuracy threshold met (Q-017) |
| P2 Authoring and governance | Build and publish | Refine and pin, report from one request, canvas and chat edits, groupings, bands, joins, custom SQL, certification queue with look-alikes, compose, publish, versions, sharing, exports, starter management | J4–J10 acceptance tests pass |
| P3 Breadth and hardening | More sources, scale | Remaining connectors (MySQL, SQL Server, MongoDB, Excel/CSV), SCIM, pack updates, SaaS multi-tenant hardening, Helm, performance tuning | Connector read-only tests pass on every engine; tenant isolation tests pass (REC-020) |

**Immediate actions:** confirm the LLM policy in writing (REC-002); decide the freshness model (REC-001); provision dedicated read-only source identities (REC-005); spike Dataverse TDS and Web API (REC-006) and MongoDB compilation (REC-007); define the role catalogue and group mappings (REC-009); confirm reuse rights (REC-016).

---

## Acceptance criteria and testing priorities

Journey-level acceptance (from S01 §7) is the baseline; detailed Given/When/Then tests TEST-001 to TEST-070 are in [function_document.md](function_document.md#acceptance-tests), and technical tests TEST-071 to TEST-099 in [technical_document.md](technical_document.md).

Testing priorities:

1. **Scope isolation on every surface** — dashboards, chat, drill, export, custom SQL, cache, filter dropdowns — with CIO and delivery-manager personas and adversarial prompts.
2. **Read-only enforcement per engine** — write attempts must fail at both the validator and the source.
3. **Definition consistency** — the same question gives the same number in widgets, chat answers and exports.
4. **Answer accuracy** — ground-truth suite by question type; failure categories tracked (fabricated values, dropped entities, wrong date column — lessons from S30).
5. **Governance flows** — uncertified widgets block publishing; restore creates a new version; merges switch widgets over.
6. **Resilience** — containers expose health checks and restart; failed jobs retry idempotently.

---

## Traceability

| Objective | Features | Key rules | Key requirements | Main risks |
|---|---|---|---|---|
| BUS-001 | FEAT-018–022, FEAT-023 | RULE-008, RULE-022 | M05, M06 | RISK-002 |
| BUS-002 | FEAT-009, FEAT-033–037, FEAT-051 | RULE-009–011 | M08 | RISK-011 |
| BUS-003 | FEAT-001–004 | RULE-004, RULE-005, RULE-025 | M01, NFR-002, NFR-003 | RISK-001, RISK-007 |
| BUS-004 | FEAT-024–027, FEAT-038–041 | RULE-016, RULE-024 | M06, M09 | RISK-012, RISK-013 |
| BUS-005 | FEAT-005, FEAT-006, FEAT-011–013 | RULE-001–003, RULE-014 | M02, NFR-001, NFR-025 | RISK-004–006, RISK-008 |
| BUS-006 | FEAT-043, FEAT-050 | RULE-018, RULE-022 | M10, NFR-008 | RISK-015 |
| BUS-007 | FEAT-010, FEAT-046, FEAT-047, FEAT-051 | RULE-017, RULE-026–039 | M11, M13 | GAP-009 |
| BUS-008 | Platform | — | NFR-019, NFR-020 | RISK-019 |

Feature-level traceability (FEAT → REQ → RULE → DATA → API → TEST) is in [function_document.md](function_document.md#feature-traceability).

---

## Related documents

| Document | What it holds |
|---|---|
| [architecture.md](architecture.md) | Proposed architecture: components, connectors, security, agents and RAG, deployment, ADRs |
| [function_document.md](function_document.md) | Functional specification of all 51 features, permissions, workflows, screens, state machines, tests |
| [technical_document.md](technical_document.md) | Proposed technical design: stack, repository, modules, connectors, agents, configuration, CI/CD |
| [db_scheme.md](db_scheme.md) | Proposed metadata database schema, ER diagrams, indexes, migrations; reference PMO source schema |
| [requirements.md](requirements.md) | REQ-001–120 and NFR-001–028 |
| [business_rules.md](business_rules.md) | RULE-001–039 with worked examples and decision tables |
| [api_catalog.md](api_catalog.md) | API-001–069 endpoint and integration contracts |
| [data_dictionary.md](data_dictionary.md) | Business terms, classification, retention, source-of-truth map |
| [risk_register.md](risk_register.md) | RISK, GAP and REC register |
| [diagrams/README.md](diagrams/README.md) | Index of standalone Mermaid diagrams |

---

## Source inventory and coverage

All 31 supplied files are Markdown and were read in full. No file was inaccessible. Formats such as DOCX, XLSX and HTML mentioned *inside* the references (for example `user_journery.html`, `Field details for Prodian_*.xlsx`, `PMO_Report_Builder_Sprint_Plan.docx`) were **not** supplied and were not inspected; facts about them are second-hand.

| Code | File | Role | Used for |
|---|---|---|---|
| S01 | scope.md | **Primary specification** — Phase 1 scope from the prototype J0–J11 | Product, roles, glossary, features, NFRs, acceptance, open questions |
| S02 | guardrails.md | Reference — TelaKnowledge guardrail notes (Go/Python code paths quoted, code not supplied) | LLM output safety, citation coverage, read-only transactions, schema checksum, signed statements, masking, production-intent blocking, review lifecycle |
| S03 | research_analysis.md | Research synthesis | Keycloak + Entra, SCIM, app-owned RBAC, tenancy, hybrid hosting, FastAPI/PostgreSQL/Vault stack, ECharts/Perspective/Vega-Lite |
| S04 | product_documents/README.md | Decision pack index | "No timelines" instruction; pack status |
| S05 | product_documents/project_summary.md | Decision pack | Current state, roles, workflows, open decisions Q-001–012 of that pack |
| S06 | product_documents/architecture.md | Decision pack | Proposed context, query safety controls, data-level authorisation, write-back separation |
| S07 | product_documents/options_and_plan.md | Decision pack | Options A/B/C, gates, acceptance criteria to agree |
| S08 | product_documents/requirements.md | Decision pack | Earlier REQ/NFR set and ambiguities |
| S09 | product_documents/risk_register.md | Decision pack | Earlier risks and gaps |
| S10 | pmo_dashboard/project_summary.md | Reference — PMO dashboard app | PMO domain, problem statement, personas, features, disabled-report table |
| S11 | pmo_dashboard/architecture.md | Reference | Ask TELA dual path, five sandbox gates, SQLite store, ADRs, Azure target |
| S12 | pmo_dashboard/requirements.md | Reference | PMO requirements, NFRs and rewrites of ambiguous terms |
| S13 | pmo_dashboard/business_rules.md | Reference | PMO pack rules RULE-026–039 |
| S14 | pmo_dashboard/api_catalog.md | Reference | Report endpoints and payload shapes |
| S15 | pmo_dashboard/data_dictionary.md | Reference | `projects` / `project_requests` schema and Dynamics CRM mapping |
| S16 | pmo_dashboard/risk_register.md | Reference | Lessons: no auth, shared DB account, SQLite single point of failure, grant cache window |
| S17 | pmo_dashboard/diagrams/README.md | Reference | Diagram index |
| S18 | pmo_dashboard/diagrams/system-context.md | Reference | Context diagram |
| S19 | pmo_dashboard/diagrams/component-architecture.md | Reference | Component diagram |
| S20 | pmo_dashboard/diagrams/custom-reports-lifecycle.md | Reference | Widget snapshot-fallback lifecycle |
| S21 | pmo_dashboard/diagrams/request-flow-ask-tela.md | Reference | Ask TELA sequence |
| S22 | pmo_dashboard/diagrams/entity-relationship.md | Reference | ER of MySQL and SQLite stores |
| S24 | tela_nl2sql/project_summary.md | Reference — Tela NL2SQL | Features, workflows, roadmap |
| S25 | tela_nl2sql/architecture.md | Reference | Gateway chokepoint, AST validator, row rewrite, multi-engine executors, SSE, turn state machine |
| S26 | tela_nl2sql/requirements.md | Reference | Requirement patterns with Given/When/Then |
| S27 | tela_nl2sql/business_rules.md | Reference | Validation, row rules, leak scanner, visual decision table, grain rules |
| S28 | tela_nl2sql/api_catalog.md | Reference | Endpoint patterns, external integrations |
| S29 | tela_nl2sql/data_dictionary.md | Reference | Grants, semantic layer, widget/dashboard entities |
| S30 | tela_nl2sql/risk_register.md | Reference | Lessons: widget refresh scope bypass, enum fabrication, entity drop, date confusion, JSON single worker |
| S31 | tela_knowledge/project_analysis_report.md | Reference — TelaKnowledge | Agents, hybrid RAG on pgvector HNSW 1024-dim, Keycloak/OPA/Vault/MinIO/Redis, operational-resilience lessons |

S23 is not assigned (the pmo_dashboard set has 13 files, S10–S22).

**Limitations:** no Tela code, schema, API specification, deployment configuration, prototype HTML, test results or operational data; no web access during authoring, so provider-specific facts (Dataverse TDS and Web API limits, Entra group-claim behaviour) are flagged "verify against current Microsoft documentation".

---

## Current understanding and next steps

**Established (L1):** what the product must do (S01), for whom, under which guardrails; the PMO measures the starter pack should carry (reference rules); the stack the stakeholder mandates.

**Not established:** source schemas in production, freshness model, role catalogue and mappings, LLM policy, performance targets, reuse rights, and whether the reference designs behave as their documents claim.

**Next steps:**

1. Review this pack with STK-001, STK-008 and the sponsor; answer Q-001, Q-003, Q-006, Q-009, Q-011, Q-012.
2. Obtain Dataverse environment access or the current TPG schema (GAP-009) and run the Dataverse spike (REC-006).
3. Approve the ADRs or record alternatives; then start Phase P0.
4. Build the ground-truth question set with the PMO business owner before any accuracy claims (REC-010).

---

## Change log

| Date | Version | Sources | Summary |
|---|---|---|---|
| 2026-10-21 | 0.1 | S01–S31 + stakeholder instruction of 2026-10-21 | First generation of the documentation pack |
