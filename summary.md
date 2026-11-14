# Tela — Project Summary

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform (Prodian Infotech) |
| Document | Consolidated summary of scope, business requirements, functions and technical design |
| Version | 1.0 |
| Date | 2026-10-21 |
| Based on | `project-docs/` pack v0.1 (10 documents + 16 diagrams, dated 2026-10-21) |
| Companion files | [task.md](task.md) — delivery backlog · [implement_log.md](implement_log.md) — execution log |

> **Status of the product.** Nothing is built yet. The source pack is a **Level 1 (business-only) proposed design**: no source code, DDL, OpenAPI spec or deployment files exist. The technology stack is **stakeholder-mandated**; everything else (architecture, schema, APIs) is **Proposed** and needs sign-off. Where the pack leaves a decision open, this summary records the pack's **working position**, which [task.md](task.md) adopts by default and raises as an explicit decision task.

---

## Contents

1. [Executive summary](#1-executive-summary)
2. [Vision and business objectives](#2-vision-and-business-objectives)
3. [Problem, users and stakeholders](#3-problem-users-and-stakeholders)
4. [Scope](#4-scope)
5. [Business requirements](#5-business-requirements)
6. [Key business rules and calculations](#6-key-business-rules-and-calculations)
7. [Functional overview](#7-functional-overview)
8. [Technical design](#8-technical-design)
9. [Delivery roadmap](#9-delivery-roadmap)
10. [Risks, gaps and open decisions](#10-risks-gaps-and-open-decisions)
11. [Inconsistencies found in the source pack](#11-inconsistencies-found-in-the-source-pack)
12. [Source document map](#12-source-document-map)

---

## 1. Executive summary

**Tela** is a chat-first reporting and dashboard platform for Project Management Office (PMO) and delivery leadership. Admins connect existing data sources **read-only**. People ask questions in plain English and get back a live chart, table or figure. Answers can be refined in words, pinned as widgets, assembled into reports and dashboards, certified by the PMO admin, and published as versioned dashboards to roles that come from Microsoft (Entra ID) group membership.

The value chain is fixed:

```
Connection → Data model → Widget → Report → Dashboard → Published
```

Three principles run through everything:

1. **Read-only, no copies.** Tela never writes to, changes or copies source tables. Grouping, banding and joining happen inside the query.
2. **One certified definition per question.** Measures are written once, certified by the admin and reused everywhere. Definitions that look alike are flagged.
3. **Scope comes from identity.** Row scope and field hiding come from Microsoft group membership and are enforced in every query: dashboards, chat, drill-downs, exports and custom SQL. Filters narrow scope; they never widen it.

**Core design choice (ADR-001):** the LLM never writes executable SQL for answers. It produces a **typed query spec** against the certified data model. Deterministic code compiles that spec per engine (SQL dialects through sqlglot, MongoDB aggregation pipelines, DuckDB over Excel, Dataverse TDS), validates it, injects the viewer's scope and runs it read-only through an isolated connector runner.

**Size of the specification:** 13 modules · 51 features · 120 functional requirements · 28 non-functional requirements · 39 business rules · 69 API/integration contracts · 51 metadata tables in 9 schemas · 14 architecture decisions · 99 test cases · 22 risks · 15 gaps · 20 open questions.

---

## 2. Vision and business objectives

**Vision.** Anyone in the PMO chain of command can move from a business question to a trusted, role-scoped, explainable answer — and from a good answer to a governed dashboard — without SQL and without waiting for an analyst.

| ID | Objective | Measure of success (targets still to be agreed) |
|---|---|---|
| BUS-001 | Self-service answers from existing data without SQL | Share of recurring PMO questions answered in Tela; time-to-answer |
| BUS-002 | One certified definition per business question | Zero conflicting figures for the same question across widgets and chat |
| BUS-003 | Role-scoped access derived from Microsoft sign-in | Zero out-of-scope rows in adversarial tests on every surface |
| BUS-004 | Chat-built, governed reports and dashboards with versioned publishing | Only certified widgets publish to roles; every version restorable |
| BUS-005 | Connect heterogeneous sources read-only without copying data | Connectors live for PostgreSQL, MySQL, SQL Server, MongoDB, Excel/CSV, Dataverse |
| BUS-006 | Every figure traceable to a readable definition and data date | 100% of figures expose definition, scope, filters and "data as of" |
| BUS-007 | Fast time to value through certified starter packs | PMO pack installed and usable on first connection |
| BUS-008 | Easy, repeatable deployment as Docker services | Same images deploy to SaaS and customer-hosted environments |

**Value proposition:** faster and consistent PMO reporting, governed self-service, reuse of certified content through starter packs, and no data movement out of the client's systems. The commercial model (pricing, licensing) is not specified.

---

## 3. Problem, users and stakeholders

**Problem.** PMO reporting depends on analysts writing SQL or spreadsheet pivots. Different reports use different filters, so the same question ("how many go-lives are overdue?") yields different numbers. Self-reported RAG status hides slippage. Generic AI query tools guess at definitions, can leak data and are hard to govern.

**Target users:** CIOs, executives, PMO directors and analysts, delivery and project managers.

### Roles

| ID | Role | What they do | Main screens |
|---|---|---|---|
| STK-001 | **Admin (PMO)** | Connect sources, approve the data model, certify definitions and custom SQL, publish to roles, manage starter content | Connections, Model studio, Certification, Publish, Starter content, Admin |
| STK-002 | **Creator** (CIO, delivery manager, analyst) | Everything a Viewer can, plus chat-build widgets, reports and dashboards, personal groupings/bands/joins, custom SQL, share, request certification | Ask, Canvas, Field builder, Composer |
| STK-003 | **Viewer** | Open published dashboards, filter, view definitions, drill, export, ask in scope. Cannot build or pin | Home, Dashboard viewer |
| STK-004 | **Audience roles** (CIO, Executive, Delivery manager) | Publish targets and scope carriers, not permission levels | Published dashboards |
| STK-005 | Data source owner / DBA | Provide read-only identities and network paths, own source freshness | Connection setup |
| STK-006 | Identity and security owner | Entra app registration, group catalogue, conditional access | Keycloak / Entra |
| STK-007 | Platform operator (DevOps) | Deploy and run Docker services, backups, monitoring | Infrastructure |
| STK-008 | PMO business owner | Owns KPI definitions; signs off PMO pack rules | Certification, starter pack |
| STK-009 | Model-policy approver | Approves LLM providers and data handling | AI settings |

A person holds **one permission level** (Admin, Creator or Viewer) plus zero or more **audience roles**. A person with no mapped group gets no access (deny by default).

---

## 4. Scope

### In scope — Phase 1

- Connections and data model with a chat **model assistant**, approval and versioning; starter pack detection.
- **Six connectors:** PostgreSQL, MySQL, SQL Server (from the scope document); MongoDB, Excel/CSV, Microsoft Dataverse (stakeholder-mandated).
- **Knowledge index (pgvector RAG)** of the certified model, glossary, gold examples and chart patterns, used to ground agents and enhance charts.
- Home shelves and ask bar; dashboard viewer with filters, locked scope chip, definitions, drill and PDF/Excel export.
- Ask, clarify, refine and pin; report from one request; canvas editing by hand and by chat; chart integrity guard; drafts and preview as role.
- Personal groupings, bands, cross-table measures along approved links; custom SQL widgets with Check and run.
- Certification queue with look-alike detection and semantic checks; certify, merge, reject.
- Dashboard composition (tabs or grouped), publish to roles with pre-publish checks, versions and restore, share with named people.
- Starter content management and pack updates.
- Microsoft SSO with role and scope from groups; audit log; LLM provider settings and usage.
- **PMO starter pack** (certified PMO measures, dimensions and reports).

### Out of scope — later phases

- Joins across two sources inside one widget (a dashboard may mix sources; each widget reads one connection).
- Scheduled e-mail delivery.
- Write-back to source systems (would need a separate typed-command, approval and audit design).
- Status-over-time snapshots / history.

### Changed by stakeholder instruction (2026-10-21)

| Conflict | Original (scope.md) | Stakeholder instruction | Working position |
|---|---|---|---|
| C1 | File uploads out of scope | Excel required | Excel/CSV **in scope** as an Admin-managed, governed upload (confirm Q-011) |
| C2 | MySQL, PostgreSQL, SQL Server only | Add MongoDB, Excel, Dataverse "etc." | **All six** engines in scope; priority order to confirm (Q-012) |

### Mandated technology

React frontend · Python backend · PostgreSQL as the internal metadata database · pgvector for RAG · a Docker image per service · connectors including Dataverse.

---

## 5. Business requirements

The full catalogue (REQ-001…REQ-120, NFR-001…NFR-028) is in `project-docs/requirements.md`. Priority uses MoSCoW.

### 5.1 Functional requirements by module

| Module | REQ range | Count | Key requirements (all **Must** unless noted) |
|---|---|---|---|
| M01 Identity and access | REQ-001–008 | 8 | Microsoft (Entra) sign-in only, no local passwords; full token validation; role + audience role from explicit group mapping; row scope per role from trusted server policy; field hiding per role on every surface and in LLM context; no auto-grant of Admin (S); SCIM provisioning (S) |
| M02 Connections and data model | REQ-009–034 | 26 | Connection form with Test connection; six engines; extensible adapter contract (S); read-only verified before first use and periodically, fail closed; never write/change/copy source; secrets only in secrets store; introspection; samples never stored; model assistant proposes links/measures/dimensions/questions; plain-language corrections with diff; approve → immutable version v1, v2…; one connection per widget; joins only on approved links; starter pack detection and gap naming; Excel/CSV upload (≤50 MB proposed) with macro rejection; Dataverse via TDS then Web API; MongoDB via pipelines with leading scope `$match`; new connection usable without restart |
| M03 Knowledge and RAG | REQ-035–041 | 7 | Approved metadata indexed in pgvector per tenant/connection; only approved knowledge retrievable; document upload with review (S); hybrid retrieval (S); gold example library; chart enhancement advisor (S); retrieved text treated as untrusted (prompt-injection defence) |
| M04 Home | REQ-042–046 | 5 | Ask bar on every page; greeting with scope and as-of; four shelves (Starter, Published to my role, Mine, Shared with me); tile badges/version/counts; New report and Build with chat for Creators/Admins only |
| M05 Ask | REQ-047–060 | 14 | Answer is already a live widget; LLM produces a typed spec, never SQL; every query passes the query gate; clarify when ambiguity changes the result; name missing data instead of guessing; narrative numbers only from computed results; scope, filters and data date on every answer; refine by words or chips; refinement changes spec not rule; Undo; pin dialog with only suitable visuals; ask about a widget |
| M06 Reports | REQ-061–072 | 12 | One sentence → plan before build; certified measures preferred; gaps named; plan editable with diff and 12-column live preview; Build runs every widget once; 12-column snap-grid canvas; toolbar and sections; edit with chat (preview → apply → undo); refuse misleading charts with alternative; auto-saved drafts; preview as role |
| M07 Advanced fields | REQ-073–083 | 11 | Grouping with catch-all and "Not set"; bands with explicit edge rules; cross-table measures on approved links with distinct semantics; preview before save; personal fields not publishable until certified; custom SQL with Check and run (single read-only SELECT, allow-list, scope, `:page_filters`, 10,000 rows / 20 s); MongoDB pipeline rules (S); Custom SQL badge; request certification |
| M08 Certification | REQ-084–092 | 9 | Queue of groupings, bands, join measures and custom SQL; queue entry only when shared/role-bound/requested; item detail (rule, results, usage, SQL, dependencies); look-alike detection; semantic checks on SQL; Certify / Merge / Reject; every decision logged with snapshot; no self-certification (S); model approval logged |
| M09 Dashboards and publishing | REQ-093–101 | 9 | Ordered reports as Tabs or Grouped; shared filters with reach check; publish dialog with preview as role/person; pre-publish checks block with inline fixes; live version stays live on failure; immutable numbered versions, restore as new version; certified-only to roles; share with named people (view-only); publish permission configurable (Admin by default) |
| M10 Reading | REQ-102–110 | 9 | Viewer with tabs/groups, filter bar, locked scope chip; KPIs and charts update together; definition popover; drill to scoped records; record link target configurable (S); PDF and Excel export; exports re-check auth and are audited; data-as-of shown; Viewers can ask but not pin |
| M11 Starter content | REQ-111–114 | 4 | Starter list with state/visibility/copies; delete with explained effects and restore; pack update notification without touching user copies; copy a starter |
| M12 Platform administration | REQ-115–118 | 4 | Admin audit log (no row data or secrets); LLM provider per agent from approved list; usage ledger (S); freshness mode per connection with honest as-of |
| M13 PMO starter pack | REQ-119–120 | 2 | Certified PMO measures/dimensions bound to TPG/Dataverse fields; reports without data shipped disabled with the reason (S) |

### 5.2 Non-functional requirements

| ID | Category | Requirement / target | Status |
|---|---|---|---|
| NFR-001 | Security | Zero write/DDL privileges on any source identity; failed check disables the connection | Specified |
| NFR-002 | Security | Zero rows outside scope on dashboards, chat, drill, exports, custom SQL, filter options and cache | Specified |
| NFR-003 | Security | Hidden fields absent from 100% of responses, exports and LLM prompts | Specified |
| NFR-004 | Security | 100% of endpoints (except health) need a validated token; idle timeout proposed 8 h | Specified |
| NFR-005 | Performance/safety | 10,000 rows and 20 s per query (configurable lower per connection) | Specified |
| NFR-006 | Integrity | Same question, scope, filters and as-of → same number everywhere | Specified |
| NFR-007 | Auditability | 100% of certifications, approvals, publishes, restores, shares, exports logged with snapshot | Specified |
| NFR-008 | Transparency | Every figure has a definition; SQL visible to Creators/Admins; as-of on every widget | Specified |
| NFR-009 | Reversibility | Undo on every chat edit; drafts separate from published; restore for versions and starters | Specified |
| NFR-010 | Integrity | Zero unsuitable visuals offered as enabled | Specified |
| NFR-011 | Performance | Proposed: chat first token ≤ 2 s, complete ≤ 8 s p90 (warm); tab render ≤ 3 s p90 for ≤ 12 widgets; custom SQL overhead ≤ 0.5 s | Proposed (Q-010) |
| NFR-012 | Privacy | No raw source rows to the LLM by default; aggregates only if policy allows | Specified (policy Q-009) |
| NFR-013 | Security | Secrets only in OpenBao/Vault; rotation supported | Inferred |
| NFR-014 | Security | TLS 1.2+ on every hop; encryption at rest | Inferred |
| NFR-015 | Availability | Proposed 99.5% monthly | Proposed |
| NFR-016 | Scalability | Stateless horizontal scaling; proposed 200 concurrent viewers, 20 chat sessions per tenant | Proposed |
| NFR-017 | Operability | Correlation ID end to end; latency/error/denial/LLM-cost metrics | Inferred |
| NFR-018 | Resilience | Proposed RPO ≤ 24 h (≤ 15 min with WAL), RTO ≤ 4 h; quarterly restore drill | Proposed |
| NFR-019 | Deployability | Versioned Docker image per component; health endpoint, non-root, restart policy; Compose and Helm | Mandated |
| NFR-020 | Operability | Separate dev / UAT / prod identities, secrets and config; synthetic data outside prod | Specified |
| NFR-021 | Usability | WCAG 2.2 AA (proposed) | Proposed |
| NFR-022 | Usability | English UI; per-field number/date/currency formatting; tenant time zone | Proposed |
| NFR-023 | Security | Zero cross-tenant reads: `tenant_id` everywhere + PostgreSQL RLS | Inferred |
| NFR-024 | Maintainability | ≥ 80% coverage on query engine, validator, scope, connectors; negative tests for all safety rules; CI on every PR | Proposed |
| NFR-025 | Extensibility | New engine = one adapter + contract test suite | Mandated |
| NFR-026 | Source protection | Per-connection concurrency cap (proposed 4), timeouts, back-off, cancel on abort | Inferred |
| NFR-027 | Privacy | Configured retention per data class; verifiable deletion | Inferred |
| NFR-028 | Compatibility | Latest two versions of Edge/Chrome/Firefox/Safari; build ≥ 1280 px, read ≥ 768 px | Proposed |

---

## 6. Key business rules and calculations

Full catalogue with worked examples: `project-docs/business_rules.md`. Rules execute in the query engine and connector runner, **never inside the LLM**.

### 6.1 Platform rules (RULE-001 – RULE-025)

| Group | Rules | Summary |
|---|---|---|
| **Safety** | RULE-001 Read-only verification | Per-engine privilege check on Test, on first use and every 60 s; any doubt disables the connection |
| | RULE-002 Single read-only statement | Parse in dialect; exactly one statement; root must be SELECT/UNION/WITH…SELECT; deny-list of nodes and functions (DML, DDL, `INTO`, locks, `pg_sleep`, `xp_*`, `OPENROWSET`…) |
| | RULE-003 Approved object allow-list | Referenced objects ⊆ approved model ∩ connection allow-list ∩ fields visible to the role |
| | RULE-004 Mandatory scope injection | Every governed table becomes a scoped derived table (or leading `$match`); re-parse and assert; missing scope attribute → refuse |
| | RULE-005 Filters narrow never widen | Effective rows = scope ∩ widget ∩ page ∩ shared filters; bound parameters only |
| | RULE-006 Row and time limits | 10,000 rows (fetch cap+1 to detect truncation), 20 s timeout, proposed 10 MiB payload |
| | RULE-007 `:page_filters` placeholder | Replaced by the parameterised page/shared filter predicate, or `1=1` |
| **Governance** | RULE-008 Visual suitability | Decision table per visual (figure, ranked bars, columns, trend, paired bars, stacked status, donut ≤ 7 slices, table) with refusal substitutions |
| | RULE-009 Certified-only publishing | Every widget must be certified to publish to a role; sharing with people needs no certification |
| | RULE-010 Queue entry | Personal definitions enter the queue only if requested, shared, or on a role-targeted draft |
| | RULE-011 Look-alike detection | Same base/aggregation or ≥ 0.85 semantic similarity; flag when results differ by > max(0.5%, 1 unit) |
| | RULE-012 / 013 Grouping and bands | Catch-all "Everything else" and "Not set"; bands half-open (lower inclusive) — exactly 1M goes to 1M–5M |
| | RULE-014 Join path and fan-out | Approved links only; `COUNT(DISTINCT key)` and CTE pre-aggregation to prevent double counting; one connection per widget |
| | RULE-015 Model version impact | Widgets pin to a model version; unchanged widgets move up automatically, changed rules flagged, removed fields broken (Q-007) |
| | RULE-016 Dashboard versions | Integers from 1; restoring vK creates vN+1; nothing is overwritten |
| | RULE-017 Starter deletion | User copies keep working; affected dashboards get a new draft; live stays; restore from pack |
| | RULE-024 Pre-publish checks | 6 checks: certification, data per role, field visibility, filter reach, model version, publish permission |
| **Trust** | RULE-018 Data as-of | Live = query time; cached = cache fill time; Excel = upload time; PMO date rules use the pack's as-of date |
| | RULE-019 LLM egress minimisation | Prompts carry question, metadata, glossary, gold examples, column names/types; never rows, samples, secrets or hidden fields; ≤ 50 aggregate cells only if policy allows |
| | RULE-020 Result masking | Secret-named columns `[REDACTED]`; e-mails `n***@domain`; phones last 4 digits; Luhn-valid cards redacted |
| | RULE-021 Pre-send leak scan | Gateway blocks keys, connection strings, private keys, e-mails, cards and injection markers; logs metadata only |
| | RULE-022 Grounded narrative | Every number in the narrative must match a value computed in code, else a template sentence is used |
| | RULE-023 Currency-safe aggregation | Never sum across currencies: group by currency, filter, or convert with an approved FX table |
| | RULE-025 Drill caps | Drill = new scoped query; 100 per page, cap 1,000 rows |

### 6.2 PMO starter pack rules (RULE-026 – RULE-039)

Reference rules from the PMO dashboard documents; **need STK-008 sign-off**. `asOf` is one server reporting date, never the browser clock.

| Rule | Measure | Definition |
|---|---|---|
| RULE-026 | Open portfolio | `status = 'Active'` (covers Project Created, Active, On Hold) |
| RULE-027 | In delivery | `current_state = 'Active'` |
| RULE-028 | Overdue go-live | In delivery AND `planned_deployment_date < asOf` AND not deployed |
| RULE-029 | Schedule risk | Open AND `schedule_kpi IN ('Red','Yellow')` |
| RULE-030 | RAG contradiction | Green but ≥ 15 days late, or Red but ≥ 30 days away |
| RULE-031 | Stale project | In delivery and not modified for > 30 days (threshold provisional) |
| RULE-032 | Delivery horizons | Go-lives in 0–30, 31–60, 61–90 days (planned date, not finish date) |
| RULE-033–035 | Calculated health | See formula below |
| RULE-036 | Over budget | `cost_total_usd > approved_budget_value_usd > 0`; cost is **forecast (EAC), not spend** |
| RULE-037 | Financial exposure | Totals, variance and variance % over projects with budget > 0 and cost ≥ 0; excluded count shown |
| RULE-038 | Open demand | `request_state IN ('New','Active','On Hold','Return to Requester')` |
| RULE-039 | Overdue gates | DOU, DARB and SOW gates past planned date and not issued/approved |

**Calculated health (RULE-033–035).** Weighted score over available components: Schedule 35%, Cost 25%, Progress 25%, Issues 15%, divided by evidence coverage. Green ≥ 75, Amber 50–74.99, Red < 50. Coverage below 60% → "Insufficient data". Overrides: schedule = 0, issues = 0 or cost = 0 → Red; cost = 25 on a Green base → capped at Amber.

> Worked example: schedule 50, cost 75, progress 25, issues 50 → 17.5 + 18.75 + 6.25 + 7.5 = **50.0 → Amber**.

**Domain cautions.** The reference data has no spend, timesheets, milestones, RAID or snapshot history, so eight classic PMO reports ship **disabled with the gap named** (Portfolio Trends, Status Reporting Compliance, Schedule Compliance, Mandatory Fields, Milestone Slippage, Critical Path, RAID Summary, Resource Utilisation/Timesheets). "Reported status" (PM's RAG) and "Calculated health" are shown side by side, never merged.

---

## 7. Functional overview

Full specifications (main flow, alternatives, validations, worked examples, acceptance tests): `project-docs/function_document.md`.

### 7.1 Modules and features

| Module | Features |
|---|---|
| **M01 Identity and access** | FEAT-001 Microsoft sign-in · FEAT-002 Group to role and scope mapping · FEAT-003 Row scope and field hiding enforcement · FEAT-004 User and group provisioning (SCIM) |
| **M02 Connections and data model** | FEAT-005 Connection management · FEAT-006 Schema introspection · FEAT-007 Model assistant · FEAT-008 Plain-language model corrections · FEAT-009 Model approval and versioning · FEAT-010 Starter pack detection and install · FEAT-011 Excel and CSV sources · FEAT-012 Dataverse connector · FEAT-013 MongoDB connector |
| **M03 Knowledge and RAG** | FEAT-014 Semantic knowledge index · FEAT-015 Gold examples and evaluation set · FEAT-016 Chart enhancement suggestions |
| **M04 Home** | FEAT-017 Home shelves and ask bar |
| **M05 Ask** | FEAT-018 Ask a question · FEAT-019 Clarifying questions · FEAT-020 Refine in words · FEAT-021 Pin a widget · FEAT-022 Ask about a widget |
| **M06 Reports** | FEAT-023 Report from one request · FEAT-024 Report canvas · FEAT-025 Edit with chat · FEAT-026 Chart integrity guard · FEAT-027 Drafts and preview as role |
| **M07 Advanced fields** | FEAT-028 Grouping · FEAT-029 Bands · FEAT-030 Cross-table measures · FEAT-031 Custom SQL widget · FEAT-032 Request certification |
| **M08 Certification** | FEAT-033 Certification queue · FEAT-034 Item detail · FEAT-035 Look-alike detection · FEAT-036 Custom SQL semantic checks · FEAT-037 Certification decisions |
| **M09 Dashboards and publishing** | FEAT-038 Compose a dashboard · FEAT-039 Publish to roles · FEAT-040 Versions and restore · FEAT-041 Share with named people |
| **M10 Reading** | FEAT-042 Dashboard viewer · FEAT-043 Definition popover · FEAT-044 Drill to records · FEAT-045 Export to PDF and Excel |
| **M11 Starter content** | FEAT-046 Starter content management · FEAT-047 Starter pack updates |
| **M12 Platform administration** | FEAT-048 Audit log · FEAT-049 LLM provider settings and usage · FEAT-050 Data freshness and result cache |
| **M13 PMO starter pack** | FEAT-051 PMO starter pack content (12 certified measure groups, 13 dimensions, 11 active + 8 unavailable starter reports) |

### 7.2 User journeys

| Journey | Who | Flow |
|---|---|---|
| J1 Connect a database | Admin | New connection → test read-only → save → introspect (samples in memory only) → pack offered or model assistant proposes → correct in plain language → approve certified v1 → knowledge re-indexed |
| J2 Sign in and Home | All | Microsoft SSO → role and scope resolved → greeting with scope and as-of → four shelves |
| J3 Read a dashboard | Viewer | Tabs/groups → shared filter bar → locked scope chip → definition popover → drill → export → ask about a widget |
| J4 Ask, refine, pin | Creator | Question → intent → retrieval → (clarify) → typed spec → validate/compile/scope/limits → run → visual + grounded headline → refine with Undo → pin (suitable visuals only; spec stored, never numbers) |
| J5 Report from one request | Creator | One sentence → plan (KPIs, sections, widgets, filters, named gaps) → edit plan with diff → Build runs every widget once → draft on canvas |
| J6 Edit the canvas | Creator | 12-column grid, widget library, toolbar, sections; chat edits previewed → Apply/Cancel → Undo; drafts; preview as role |
| J7 Advanced fields and custom SQL | Creator | Grouping / band / cross-table measure with preview → save as Personal; custom SQL Check and run → Custom SQL badge → request certification |
| J8 Certify | Admin | Queue → item detail → look-alike and semantic findings → Certify / Merge / Reject → snapshot + audit → badges update everywhere |
| J9 Compose and publish | Creator → Admin | Order reports, tabs or grouped, shared filters → preview as role/person → pre-publish checks → version N+1 live → audience notified; restore creates a new version |
| J10 Manage starters | Admin | Hide per role, delete with explained effects, restore from pack, apply pack updates without touching user copies |
| J11 Bring another database | Admin | Repeat J1; new model queryable immediately after approval |

### 7.3 Permission summary

| Area | Admin | Creator | Viewer |
|---|---|---|---|
| Sign-in, Home, viewer, definitions, drill, ask (in scope), export | ✓ | ✓ | ✓ |
| Group mappings, scope/field policies, connections, model approval, starter content, audit, AI settings, knowledge | ✓ | — | — |
| Pin, reports, canvas, dashboards, personal fields, custom SQL, share, request certification | ✓ | ✓ (own) | — |
| Certify / merge / reject | ✓ | status of own requests | — |
| Publish to roles | ✓ | request only (Q-006) | — |

All data reads are scoped for every role, including Admin (proposed).

### 7.4 Screens

SCR-01 Home · SCR-02 Chat and answer card · SCR-03 Report canvas · SCR-04 Dashboard viewer · SCR-05 Field builder · SCR-06 Custom SQL editor · SCR-07 Certification queue and detail · SCR-08 Dashboard composer and publish dialog · SCR-09 Connections · SCR-10 Model studio · SCR-11 Starter content · SCR-12 Admin access · SCR-13 Admin platform.

### 7.5 State machines

| Entity | States |
|---|---|
| Connection | Draft → Testing → Connected ⇄ Introspecting / Incomplete; Error; Disabled; Deleted (only with no published dependants) |
| Data model version | Draft → Certified → Superseded; Draft → Discarded |
| Definition / widget certification | Personal → Pending → Certified / Merged / back to Personal (reject); Certified → Deprecated / Broken |
| Report / dashboard | Draft → Published → PublishedWithDraft → (Undo all / Republish) → Published; Archived |
| Dashboard version | Live → Superseded; Live → Withdrawn |
| Starter item | Active ⇄ Hidden; → Deleted → Active (restore); Unavailable → Active (data supplied) |

### 7.6 Notifications and messages

In-app notification centre (e-mail optional): certification requested/decided, dashboard published to my role, shared with me, starter deleted → draft created, pack update available, model approved, definition broken, connection error, export ready/failed, provisioning failure. 29 user-facing messages (MSG-001…MSG-029) are catalogued; messages never show SQL to Viewers or internal details to anyone, and every security refusal is audited.

---

## 8. Technical design

All of this section is **Proposed** except the mandated stack. Detail: `project-docs/architecture.md`, `technical_document.md`, `db_scheme.md`, `api_catalog.md`.

### 8.1 Technology stack

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript (strict), Vite, TanStack Query, Zustand, React Router, react-grid-layout (12 columns), **Apache ECharts** from an internal chart spec, Tailwind + Radix UI, react-hook-form + zod, OpenAPI-generated types, fetch-event-source for SSE, Vitest/RTL/Playwright |
| Backend | Python 3.12/3.13, **FastAPI** modular monolith (Gunicorn + Uvicorn), Pydantic v2, SQLAlchemy 2 async + Alembic, **sqlglot** (postgres, mysql, tsql, duckdb), Celery + Redis, pyjwt, structlog, OpenTelemetry, `uv` workspaces, ruff, mypy |
| Connector drivers | psycopg 3 · PyMySQL/mysqlclient · pyodbc + ODBC Driver 18 · pymongo · duckdb + openpyxl · pyodbc (Dataverse TDS with Entra token) · httpx + msal (Dataverse Web API) |
| AI | Provider-neutral LLM gateway with adapters for Azure OpenAI, Anthropic Claude, Google Gemini, local Ollama; embeddings `mxbai-embed-large` 1024-dim on Ollama by default |
| Data and infra | PostgreSQL 16 + pgvector (HNSW, cosine), Redis 7, OpenBao/Vault, MinIO/S3 or Azure Blob, Keycloak brokering Entra ID, Traefik/nginx, Prometheus/Grafana/Loki/OTel |
| Deployment | Docker image per service; Docker Compose profiles (`core`, `ai-local`, `observability`, `dev`); Helm for Kubernetes/SaaS; optional Azure mapping (Container Apps/AKS, Azure PostgreSQL Flexible, Key Vault, Blob, Azure OpenAI) |

### 8.2 Components

| ID | Component | Responsibility |
|---|---|---|
| COMP-001 | Web app | React SPA on nginx; renders charts; never computes figures or enforces access |
| COMP-002 | API service | FastAPI modular monolith: auth check, authorisation, all business operations, SSE |
| COMP-003 | Agent orchestrator | Module in API; runs agents AG-01…AG-10, validates every output against schemas |
| COMP-004 | Query engine | Module in API; spec validation, per-dialect compilation, custom SQL validation, scope injection, limits, statement hashing — **the only producer of executable queries** |
| COMP-005 | Connector runner | Separate container; executes signed read-only tasks; per-engine drivers, timeouts, row caps, masking; only component with network access to sources |
| COMP-006 | LLM gateway | Separate container; single chokepoint to model providers: masking, leak scan, budgets, schema check, metadata-only audit |
| COMP-007 | Knowledge service | Chunking, embedding, hybrid retrieval, review-gated corpus |
| COMP-008 / 009 | Worker / export renderer | Celery: introspection, indexing, exports (headless Chromium + openpyxl), cache warm, pack updates, SCIM reconcile, outbox relay, retention, nightly eval |
| COMP-010 | Keycloak | Brokers Entra ID (OIDC code + PKCE); authentication only |
| COMP-011 | PostgreSQL + pgvector | All metadata, governance, audit, vectors; RLS on `tenant_id` |
| COMP-012 – 017 | Redis, secrets store, object storage, embedding runtime, observability, reverse proxy | Supporting services |

**One shared query path.** Every surface — widgets, chat, drill, export, filter-value lists, custom SQL, certification preview, pre-publish checks, preview as role, cache — goes through the same `execute(spec, viewer)` path:

```
spec or custom SQL → validate → compile per engine → drop hidden fields → inject scope →
assert scoped → enforce limits → sign task → runner executes read-only → mask →
choose visual → grounded narrative
```

### 8.3 Agents (all typed JSON, validated by code, cannot execute anything)

| Agent | Purpose |
|---|---|
| AG-01 Intent router | Classify the turn (ask, refine, explain, build report, edit canvas, model help, out of scope) |
| AG-02 Clarifier | One clarifying question when ambiguity changes the result |
| AG-03 Query-spec planner | Question → `QuerySpec` over approved model IDs, or `unsupported{reason}` |
| AG-04 Model assistant | Propose links, cardinality, measures, dimensions, questions; apply admin corrections |
| AG-05 Report planner | Sentence → report plan with named gaps |
| AG-06 Canvas editor | Instruction → typed layout operations, previewed before apply |
| AG-07 Visual advisor | Suggest suitable visuals and enhancements (RULE-008 decides) |
| AG-08 Narrator | Headline from pre-computed facts only |
| AG-09 Certification reviewer | Explain look-alike and semantic differences (advisory) |
| AG-10 SQL drafter | Draft custom SQL; never auto-run |

### 8.4 RAG (pgvector)

- **Corpus:** approved model definitions, glossary notes, certified measures, gold examples, chart patterns, admin-uploaded documents (PMO charter, KPI handbook). New items are `pending_review`; only `approved` items are retrievable.
- **Pipeline:** extract → chunk (~800 tokens, 120 overlap; one entity per chunk for model definitions) → leak/PII/injection scan → embed (1024-dim) → `knowledge_chunks` with `vector(1024)` and `tsvector`.
- **Retrieval:** hybrid — top 30 HNSW cosine + top 30 full-text, merged by Reciprocal Rank Fusion (k = 60), top 8 with citations; always filtered by tenant, connection, model version and caller's visible fields.
- **Grounding:** cited IDs must be from the supplied set; citation coverage ≥ 0.70 or fall back to a deterministic answer.
- **Chart enhancement:** AG-07 uses retrieved chart patterns to suggest a better visual, reference lines, previous-period comparisons or top-N cuts as undoable chips.

### 8.5 Connectors

| Engine | Read-only enforcement | Query language | Scope injection |
|---|---|---|---|
| PostgreSQL | SELECT-only role + `default_transaction_read_only` + `statement_timeout` | PostgreSQL via sqlglot | Derived subquery |
| MySQL | `SHOW GRANTS` parse + `SET SESSION TRANSACTION READ ONLY` + `max_execution_time` | MySQL via sqlglot | Derived subquery |
| SQL Server | `db_datareader` only, `fn_my_permissions` check, `ApplicationIntent=ReadOnly`, ODBC timeout | T-SQL (`TOP`) | Derived subquery |
| MongoDB | `read` role via `connectionStatus`; only `aggregate`/`find`; `maxTimeMS` | Aggregation pipeline; no `$out/$merge/$function/$where/$accumulator` | Leading `$match`; scoped `$lookup` |
| Excel/CSV | DuckDB in memory over an immutable object version; external access off | DuckDB SQL | Derived subquery over sheet views |
| Dataverse | Application user with read-only security role; TDS endpoint is read-only (verify) | T-SQL subset via TDS, fallback OData/FetchXML via Web API | Derived subquery / `$filter` |

Adapter contract: `test_connection()`, `verify_read_only()`, `introspect()`, `capabilities()`, `compile()`, `execute()`, `cancel()`, `distinct_values()`. Custom SQL is not offered on MongoDB or Dataverse Web API mode in Phase 1.

### 8.6 Data architecture

Tela stores **metadata only**; source rows are never persisted (short-TTL, scope-keyed Redis cache only, default 15 minutes).

| Schema | Tables |
|---|---|
| `iam` | tenants, users, external_identities, idp_groups, idp_group_members, roles, role_permissions, group_role_mappings, scope_policies, field_policies |
| `conn` | connections (secret_ref only), connection_objects, file_sources |
| `model` | data_models, model_versions, model_entities, model_fields, model_links, measures, dimensions, model_questions, starter_packs, starter_pack_items |
| `defs` | personal_fields, custom_sql_definitions |
| `content` | widgets (spec only), reports, report_sections, report_items, dashboards, dashboard_reports, dashboard_versions, publications, shares, drafts_undo_log |
| `gov` | certification_requests, certification_decisions, lookalike_findings |
| `chat` | conversations, turns, turn_events |
| `rag` | knowledge_documents, knowledge_chunks (`vector(1024)`, HNSW + GIN), gold_examples |
| `ops` | audit_events (append-only, hash chain), llm_invocations, query_executions, result_cache_index, export_jobs, outbox_events, notifications |

Conventions: UUID v7 keys; `timestamptz` UTC; soft delete on restorable content; immutable approved model versions, dashboard versions, decisions and audit; JSONB specs validated against versioned schemas; RLS with `SET LOCAL app.tenant_id` per transaction. Migration plan: 13 Alembic revisions (`0001_extensions_util` … `0013_rls`).

**Reference PMO source schema** (synthetic MySQL extract of Dynamics CRM / TPG): `projects` (status, current_state, lifecycle, cio, region, priority, platform, KPIs, planned/baseline/actual dates, budget, EAC cost…) and `project_requests` (request_state, DOU/DARB/SOW gates, revenue estimates…). Logical names such as `tpgmea_projectnumber` show the live source is most likely **Dataverse**.

### 8.7 APIs

REST at `/api/v1` plus SSE for chat; Bearer tokens from Keycloak; tenant from token only; out-of-scope IDs return 404; cursor pagination; `Idempotency-Key` on creating POSTs; ETag/If-Match on drafts; stable error codes.

| Group | IDs |
|---|---|
| Identity and access (me, logout, SCIM, group mappings, scope/field policies) | API-001–006 |
| Connections (CRUD, test, update/disable, introspect, file upload) | API-007–011 |
| Data model and starter packs (models, assistant SSE, approve, pack match/install) | API-012–015 |
| Ask (turn stream, get turn, clarify, refine, undo) | API-016–020 |
| Widgets (CRUD/pin, run, suitable visuals) | API-021–023 |
| Reports (CRUD, plan, build, layout, chat edit, undo) | API-024–029 |
| Personal fields and custom SQL (fields, preview, check/run/draft, request certification) | API-030–033 |
| Certification (queue, item, decision) | API-034–036 |
| Dashboards and publishing (CRUD, preview, prechecks, publish, versions/restore, shares) | API-037–042 |
| Reading (published, definitions, drill, exports) | API-043–046 |
| Starter content, knowledge, platform (starter admin, pack updates, knowledge docs, gold examples, search, audit, LLM settings, usage, health, home, notifications) | API-047–057 |
| External integrations (Entra/Keycloak, Graph, LLMs, embeddings, six sources, object storage, secrets) | API-058–069 |

### 8.8 Security

- **Identity:** Entra ID via Keycloak (OIDC + PKCE); tokens in memory; JWT validated against the configured issuer only; users matched on immutable object ID, never e-mail.
- **Authorisation:** feature permissions (role → permission keys) plus data permissions (scope and field policies); privileged mappings need second-admin approval.
- **Execution:** API signs `{execution_id, tenant, connection, statement_sha256, scope_hash, limits, exp}` (Ed25519); runner re-verifies hash and signature and rejects schema-checksum drift.
- **Isolation:** separate Docker networks — only the runner reaches sources, only the gateway reaches LLM providers, only the proxy publishes a port.
- **Hardening:** non-root, read-only root filesystem, `no-new-privileges`, TLS on every hop (`sslmode=verify-full`, Redis TLS + AUTH), start-up refuses empty or placeholder keys, SBOM + Trivy + cosign in CI.
- **Audit:** append-only events for sign-in, connections, approvals, certification, publish/restore, shares, exports, policy denials, LLM calls (metadata only, never prompt text).

### 8.9 Operations and quality

- **Health:** `/healthz` and `/readyz` on every container; restart policies; non-zero exit on fatal start-up.
- **Observability:** OTel traces web → API → gateway/runner → source; structured JSON logs that never contain prompts, rows, SQL literals or secrets; Prometheus metrics and alerts (policy denials, leak blocks, timeouts, read-only failures, outbox lag, backup failure).
- **Resilience:** retries only on transient errors; idempotency keys; transactional outbox with monitored relay; LLM down → certified dashboards still work.
- **Backup/DR:** daily backups + PITR for metadata and Keycloak DBs, versioned object storage, secrets snapshots, tested restore runbook (targets open, Q-018).
- **Testing:** unit (compiler, validator, scope, suitability), security (500+ injection strings, scope isolation matrix, leak tests, tenant isolation), live connector containers for every engine, ground-truth evaluation suite, Playwright E2E for J1–J11, k6 load tests. Tests TEST-001–070 (functional) and TEST-071–099 (technical).
- **CI pipeline:** lint → types → unit (80% gate) → security suites → integration on Compose → migrations up/down → E2E → multi-arch image build, SBOM, Trivy, cosign → deploy dev (auto), UAT and prod (manual approval).

### 8.10 Architecture decisions (all Proposed)

| ADR | Decision |
|---|---|
| ADR-001 | Typed query spec instead of LLM-written SQL |
| ADR-002 | Pluggable connector adapters with per-engine read-only enforcement |
| ADR-003 | Keycloak broker with Entra ID; authorisation owned by Tela |
| ADR-004 | Single PostgreSQL metadata store with pgvector |
| ADR-005 | Docker image per service; Compose and Helm profiles |
| ADR-006 | Modular-monolith API plus separate runner, gateway and worker |
| ADR-007 | Apache ECharts from an internal chart spec |
| ADR-008 | Provider-neutral LLM gateway chokepoint |
| ADR-009 | Scope enforcement by AST rewrite plus source read-only backstop |
| ADR-010 | No persisted result snapshots; scope-keyed short-TTL cache |
| ADR-011 | Celery with Redis plus transactional outbox |
| ADR-012 | Excel and CSV via DuckDB over object-stored files |
| ADR-013 | Dataverse via TDS endpoint first, Web API fallback |
| ADR-014 | MongoDB via compiled aggregation pipelines |

---

## 9. Delivery roadmap

Phases have **exit criteria, not dates** (the source pack asks that discussed timelines not be used as commitments). The backlog for each phase is in [task.md](task.md).

| Phase | Goal | Main content | Exit criteria |
|---|---|---|---|
| **P0 Foundation** | Safe skeleton | Monorepo, Docker Compose stack, CI from day one, Keycloak + Entra sign-in, group → role/scope mapping, metadata DB with migrations and RLS, secrets store, LLM gateway skeleton, audit baseline, health checks | Sign-in works; role and scope resolved; all containers healthy; migrations repeatable; CI green |
| **P1 Governed read path** | One source, trusted answers | PostgreSQL/TPG connector (Dataverse spike in parallel), introspection, model assistant, approval v1, PMO starter pack, spec-first query engine with validator and scope injection, runner, freshness/cache, Home, dashboard viewer, definitions, drill, ask with certified answers and clarifications, ground-truth suite | Zero out-of-scope rows in adversarial tests; metric parity across surfaces; accuracy threshold met (Q-017) |
| **P2 Authoring and governance** | Build and publish | Refine and pin, ask about a widget, report from one request, canvas, chat edit, chart guard, drafts/preview, groupings, bands, joins, custom SQL, certification queue and decisions, compose, publish, versions, sharing, exports, starter management, knowledge index and visual advisor, audit viewer, AI settings UI | J4–J10 acceptance tests pass |
| **P3 Breadth and hardening** | More sources, scale | MySQL, SQL Server, MongoDB, Excel/CSV, Dataverse (TDS + Web API), SCIM, pack updates, SaaS multi-tenant hardening, Helm, backup/DR, performance tuning, accessibility audit | Connector conformance suites pass on every engine; tenant isolation tests pass; NFR targets met |

**Immediate actions before real data:** confirm the LLM policy in writing (REC-002); decide the freshness model (REC-001); provision dedicated read-only source identities (REC-005); spike Dataverse TDS/Web API (REC-006) and MongoDB compilation (REC-007); define the role catalogue and group mappings (REC-009); confirm reuse rights for reference code (REC-016).

---

## 10. Risks, gaps and open decisions

### 10.1 Top risks

| ID | Risk | Priority | Mitigation |
|---|---|---|---|
| RISK-001 | Scope leakage across surfaces | P0 · Critical | One shared scoped query path; scope hash in cache keys; isolation test matrix (REC-004) |
| RISK-002 | Confidently wrong answers (fabricated values, dropped filters, wrong date field, fan-out) | P0 | Typed spec bound to certified measures; grounded values; ground-truth suite (REC-003, REC-010) |
| RISK-003 | Unapproved LLM data handling | P0 · Critical | Written policy first; gateway allow-list; LLM off/local until approved (REC-002) |
| RISK-009 | Credential compromise | P0 · Critical | Secrets store only; dedicated read-only identities; secret scanning (REC-005) |
| RISK-010 | Custom SQL validation bypass | P0 · Critical | AST validator per dialect, fuzzing, DB-level read-only backstop |
| RISK-014 | Prompt injection via metadata and documents | P0 | Review-gated corpus, quoting retrieved text as data, injection scanning (REC-014) |
| RISK-015 | Stale or cross-scope cache | P0 | Cache key includes tenant, connection, model version, statement hash, scope hash |
| RISK-021 | Tenant isolation in SaaS | P0 if SaaS · Critical | RLS + tenant in every key/prefix + isolation tests (REC-020) |
| RISK-004 | Dataverse endpoint constraints | P1 | Time-boxed spike (REC-006) |
| RISK-005 / 006 | MongoDB semantics; Excel uploads copy data | P1 | Spike (REC-007); governed upload with retention (REC-008) |
| RISK-008 | Source system load | P1 | Freshness model, cache, per-connection concurrency caps |
| RISK-012 | Model version changes break widgets | P1 | Version-pin widgets (REC-011) |
| RISK-019 | Operational resilience gaps (lesson from references) | P1 | Health checks, restart policies, CI from day one (REC-012) |

### 10.2 Open questions and adopted working positions

| ID | Question | Working position used in task.md |
|---|---|---|
| Q-001 | Refresh model per connection | Live with 15-minute scope-keyed cache; "source as-of" mode available |
| Q-002 | How many starter packs; who authors them | PMO pack only in Phase 1 |
| Q-003 | Fixed or configurable roles; group mapping semantics | Fixed Admin/Creator/Viewer + configurable audience roles; highest level wins; scope = union; hidden fields = intersection; Admin is also scoped |
| Q-004 | Drill record link target | Read-only Tela record panel; configurable URL template later |
| Q-005 | Edit-sharing | View-only |
| Q-006 | Who may publish | Admin only (`dashboards.publish`); Creators "request publish" |
| Q-007 | Model version impact on widgets | RULE-015 pinning policy |
| Q-008 | Export limits | Per-role export permission, row caps, 24 h retention, 15-minute links |
| Q-009 | LLM providers and data handling | No client data to any provider until written approval; local Ollama for dev; aggregates off |
| Q-010 | Performance targets | NFR-011 proposed targets as baseline |
| Q-011 | Excel/CSV in Phase 1 | In scope, Admin upload only, ≤ 50 MB |
| Q-012 | Connector priority | PostgreSQL/TPG pilot → Dataverse (after spike) → SQL Server → MySQL → Excel/CSV → MongoDB |
| Q-013 | Where group mappings live | Configured once by Admin in Tela (FEAT-002) |
| Q-014 | SaaS vs customer-hosted | Build customer-hosted Compose first; keep SaaS-ready (tenant_id + RLS) |
| Q-015 | Dataverse environment access | Request early; spike decides TDS vs Web API |
| Q-016 | Embedding hosting | Local Ollama `mxbai-embed-large` (1024-dim) |
| Q-017 | Accuracy threshold for UAT | To agree with STK-008 before UAT |
| Q-018 | Audit and conversation retention | Audit ≥ 1 year, turns 180 days, logs 90 days (proposed) |
| Q-019 | Reuse rights for reference code | Reuse ideas only, not code, until confirmed |
| Q-020 | Write-back in future | Excluded from Phase 1 |

### 10.3 Gaps

GAP-001 freshness model · GAP-002 role catalogue · GAP-003 starter pack authoring · GAP-004 drill link target · GAP-005 share level · GAP-006 publishing rights · GAP-007 export limits · GAP-008 performance targets · GAP-009 **production PMO schema not supplied** · GAP-010 accuracy thresholds · GAP-011 file source conflict · GAP-012 connector list conflict · GAP-013 group mapping conflict · GAP-014 backup/DR targets · GAP-015 localisation, time zone and currency.

---

## 11. Inconsistencies found in the source pack

These are small contradictions between the source documents, noticed while building this summary. Each one becomes a clean-up task in [task.md](task.md).

| # | Inconsistency | Where | Suggested resolution |
|---|---|---|---|
| I-1 | The deployment diagram shows a separate `knowledge` container (port 8300), but the architecture puts the knowledge service inside the API (retrieval) and worker (indexing) | `diagrams/deployment-docker.md` vs `architecture.md` COMP-007 | Keep it as a module (ADR-006); update the diagram |
| I-2 | Permission keys are named differently: `dashboards.publish`, `widgets.create`, `customsql.use` in the technical document vs `content.publish`, `widget.create`, `custom_sql.create` in the DB seed data | `technical_document.md` vs `db_scheme.md` §8 | Adopt one catalogue (the technical document's, also used by the API catalogue) |
| I-3 | Connection status values differ: `Connected`, `Introspecting`, `Incomplete` in the state machine vs `draft, testing, active, error, disabled` in the DB enumeration | `function_document.md` vs `db_scheme.md` §8 | Align the enumeration with the state machine |
| I-4 | Model version status is `Certified` in the functional spec but `approved` in the DB enumeration; the model lifecycle diagram also mentions "proposed" and "retired" | `function_document.md`, `db_scheme.md`, `diagrams/model-version-lifecycle.md` | Agree one lifecycle and use it everywhere |
| I-5 | Pre-publish checks: the functional spec and the publish dialog list 3 checks; RULE-024 lists 6 | `function_document.md` FEAT-039 vs `business_rules.md` §4.3 | Implement all 6 (a superset); update the UI spec |

---

## 12. Source document map

| Need | Document |
|---|---|
| Product overview, objectives, roadmap, open questions | `project-docs/project_summary.md` |
| Requirements REQ-001–120, NFR-001–028 | `project-docs/requirements.md` |
| Business rules RULE-001–039 with worked examples | `project-docs/business_rules.md` |
| Feature specifications FEAT-001–051, screens, states, messages, tests TEST-001–070 | `project-docs/function_document.md` |
| Architecture, components, security, agents/RAG, deployment, ADRs | `project-docs/architecture.md` |
| Stack, repository layout, code-level design, config, CI, tests TEST-071–099 | `project-docs/technical_document.md` |
| Database schema, ER, indexes, migrations, reference PMO schema | `project-docs/db_scheme.md` |
| API and integration contracts API-001–069 | `project-docs/api_catalog.md` |
| Business terms, classification, retention, source of truth | `project-docs/data_dictionary.md` |
| Risks, gaps and recommendations | `project-docs/risk_register.md` |
| Mermaid diagrams | `project-docs/diagrams/` |
