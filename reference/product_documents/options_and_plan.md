# PMO Analytics Platform — Options Assessment and Delivery Plan

| Field | Value |
|---|---|
| Project | PMO Analytics & Conversational Dashboard Platform |
| Document status | Draft for decision |
| Version | 0.1 |
| Last updated | 2026-10-19 |
| Evidence level | L1–L2; decision framework based on meeting notes and journey prototype |
| Sources analyzed | Meeting scope; `user_journery.html` |
| Related documents | [Project summary](project_summary.md), [Architecture](architecture.md), [Requirements](requirements.md), [Risk register](risk_register.md) |

## 1. Executive recommendation

**Recommended direction to investigate first: Option B — reuse Tela/Insights for the tested talk-to-data capability and integrate it with the useful PMO experience.** This is a hypothesis, not a final commitment: it depends on source access, maintainability, licensing/IP, connector fit, deployment constraints and the ability to enforce the client's data-level permissions.

Run a bounded comparison against **Option C — Power BI/Tableau** for read-only dashboards and governed analytics. Keep **Option A — harden the PMO app** as a fallback if the existing PMO code is materially simpler to productionize than the hybrid.

Do not use the meeting's suggested delivery durations as commitments; the user explicitly asked that timelines not be taken. The plan below is organized by workstream and exit criteria, not dates or week counts.

## 2. Options

### Option A — Productionize the PMO app

**Description:** Keep the current PMO app as the product foundation and rebuild/harden the incomplete talk-to-data experience.

**Strengths**
- One cohesive product experience and codebase if the current app is maintainable.
- Full control over UX, workflow and future write-back.
- Fewer cross-product seams at runtime.

**Weaknesses / risks**
- The current talk-to-data capability is reported as unreliable.
- Recreating tested schema retrieval, query validation, ground-truth evaluation and connector behavior may duplicate existing Tela work.
- “Vibe-coded” prototype code may need significant refactoring, testing and security review.
- Production readiness cannot be estimated without repository inspection.

**Best fit if:** the PMO code is cleanly structured, reusable, testable and its current shortcomings are mostly presentation-layer issues rather than fundamental backend gaps.

### Option B — PMO UX + Tela/Insights analytics backend

**Description:** Retain the PMO experience where it works, integrate the tested Tela/Insights question-to-data and chat-to-dashboard capabilities, and establish a clear contract between them.

**Strengths**
- Potentially reuses stronger query grounding, tests, ground truth and data connections.
- Keeps PMO-specific navigation and report experience.
- May reduce duplicate backend work and improve answer quality sooner.
- Allows the backend and UX to evolve separately if API boundaries are clean.

**Weaknesses / risks**
- Integration can be costly if authentication, data models, UI state or deployment assumptions differ.
- Product ownership, code access, license and deployment model must be established.
- Duplicate metadata, inconsistent permissions or split audit trails could undermine governance.
- A hybrid may feel fragmented without explicit UX ownership.

**Best fit if:** Tela can be reused, the tested backend is materially stronger, and it can enforce the same identity and data-scope policy as the PMO application.

### Option C — Power BI or Tableau

**Description:** Use an established BI product for dashboards, filters, governed datasets, access control and supported AI-assisted analytics; build custom workflow extensions only where needed.

**Strengths**
- Mature reporting, visualization, sharing and governance capabilities.
- Potentially less custom chart/report infrastructure to maintain.
- Strong candidate for standard read-only dashboards and Microsoft-centric environments.
- Lets the team focus on business definitions and data governance instead of rebuilding common BI primitives.

**Weaknesses / risks**
- Product journeys and UX may not match the supplied custom canvas and chat-to-report flows.
- Copilot/AI capabilities, licensing, connector support and tenant policy need validation.
- Chat-driven authoring, custom certification workflow and fine-grained per-widget governance may require adaptation.
- Write-back with plan/approve/audit is not a safe assumption of out-of-box BI capability and may require a separate application/service.

**Best fit if:** the priority is governed read-only analytics and most journeys can be satisfied by configuration rather than custom UI. Evaluate Power BI first given the Microsoft environment, but compare Tableau only if there is a concrete reason.

## 3. Decision matrix

Scores are provisional, 1 = weak and 5 = strong. They are hypotheses based on the supplied notes, not verified product benchmarks.

| Criterion | Weight | A: PMO hardening | B: PMO + Tela | C: Power BI/Tableau |
|---|---:|---:|---:|---:|
| Talk-to-data maturity / test assets | 20% | 2 | 4 | 3 |
| Fit to custom journeys and canvas | 15% | 4 | 4 | 2 |
| Data-level access potential | 20% | 3 | 3 | 4 |
| Reuse / speed to validated read-only value | 15% | 2 | 4 | 4 |
| Custom write-back extensibility | 10% | 4 | 4 | 2 |
| Operational maintainability | 10% | 2 | 3 | 4 |
| Vendor/platform fit | 5% | 3 | 3 | 5 |
| Low integration complexity | 5% | 4 | 2 | 4 |
| **Weighted score / 5** | **100%** | **2.80** | **3.55** | **3.35** |

The score is intentionally not a final decision. Validate the largest uncertainties: security enforcement, code reuse, connector compatibility, licensing, cost, and journey coverage. Option B wins the initial hypothesis; Option C remains a serious alternative for read-only scope.

## 4. Recommended decision process

### Gate 1 — Evidence and reuse assessment
- Obtain PMO and Tela/Insights repositories, ownership/licensing terms and build/run instructions.
- Map the existing code to J1–J11; distinguish implemented, partial, prototype-only and absent behavior.
- Inspect the talk-to-data evaluation corpus, ground truth, test results and query safety controls.
- Identify supported connectors, auth model, API contracts and deployment constraints.
- Verify whether row-level policy is enforced in backend/source or only implied in prompts/UI.

**Exit:** evidence-backed comparison and a documented recommendation. If Tela's tested backend cannot be reused or cannot satisfy authorization requirements, reassess A vs C.

### Gate 2 — Read-only data and security proof
- Confirm source platform and a supported read-only access method.
- Build synthetic schema/data in the approved Azure sandbox.
- Define the semantic model for the bounded dataset and obtain business sign-off on KPI definitions.
- Demonstrate role-specific data access using at least a CIO and project-manager persona.
- Run positive, negative and adversarial query tests.

**Exit:** a user cannot retrieve data outside their permitted scope, including via chat, filters, drill-down, exports or alternate phrasings.

### Gate 3 — Core analytics and authoring proof
- Implement/validate the key dashboard, ask/refine/pin, report build/edit, certification and publish flows.
- Validate chart selection and accessible fallbacks.
- Compare answers with the ground-truth set and record error categories.
- Confirm versioning, role preview and audit behavior.

**Exit:** agreed acceptance criteria pass for the frozen priority journeys; unsupported features are explicitly deferred.

### Gate 4 — Production readiness
- Complete threat/security review, identity mapping, source credential handling, monitoring, backups, rollback and operational ownership.
- Complete UAT, accessibility checks, data reconciliation and performance tests.
- Obtain formal approval from business/data/security owners.

**Exit:** release approval and an operational runbook.

### Gate 5 — Write-back discovery and approval
- Treat write-back as a separate workstream with typed business commands and a plan/approve/execute/audit flow.
- Confirm allowed fields, validation rules, record identity, approval policy, delete policy and recovery behavior.
- Prototype only against synthetic data until security and business owners approve.

**Exit:** signed write-back threat model, command catalog and acceptance suite before production mutation is enabled.

## 5. Work breakdown and ownership

| Workstream | Key activities | Primary owner (proposed) | Dependencies / exit artifact |
|---|---|---|---|
| Product/journey definition | Reconcile meeting notes with J1–J11; identify MVP journeys; define exclusions and acceptance criteria | Product/BA + client PMO | Frozen journey backlog and decision log |
| Code and product assessment | Review PMO and Tela repositories, test suites, licenses, architecture, API seams | Tech lead / architect | Evidence matrix and option recommendation |
| Data and semantic model | Confirm source, schema, measures, joins, filters, freshness and data owners | Data owner + BA | Approved data dictionary/model |
| Identity and data access | SSO, group mapping, row/column scope, object permissions, export policy | Security/identity + backend lead | Access-control matrix and negative test results |
| Connector and query safety | Read-only connection, query planner/validator, limits, errors, audit | Backend/data engineer | Connector contract and query-safety test suite |
| UX and dashboard experience | Home, default dashboards, chat clarification, widgets, report canvas | UX/front-end lead | Tested flows mapped to J1–J11 |
| Certification and publishing | Draft/review/certify/publish/version/role-preview workflows | Product + platform lead | Governance state model and acceptance tests |
| Quality and evaluation | Ground truth, regression, authorization, data reconciliation, performance, accessibility | QA lead | Test strategy, baselines and release evidence |
| Infrastructure and operations | Azure sandbox, secrets, CI/CD, logging, alerts, backup/restore, rollback | Platform/DevOps | Environment diagram and runbook |
| Write-back (later approval) | Typed commands, preview, approvals, transactional execution, audit, idempotency | Backend lead + business owner + security | Separate approved design and test plan |

## 6. Scope recommendation

### Initial read-only product
Prioritize:
- Microsoft SSO and server-side authorization.
- A confirmed read-only connector to the approved bounded data model.
- A small, business-approved semantic catalog and KPI definitions.
- Role-specific access enforced beyond the UI.
- Priority certified dashboards and filters.
- Talk-to-data with clarification, validated query execution and chart/table/card output.
- Pin answer to widget, save/edit report, role preview, certification and publish.
- Audit events and testable failure states.

### Defer unless explicitly approved
- Broad support for every table in the client's full schema.
- A promise of approximately 100 chart types before the actual catalog is defined.
- Arbitrary custom SQL for all creators without separate permission and review.
- Production write-back, especially delete.
- Advanced caching/ClickHouse/temporary analysis infrastructure before workload measurement.
- Cross-source joins until source semantics and data ownership are clear.

## 7. Acceptance criteria to agree

1. **Identity:** unauthenticated users cannot access protected data or APIs.
2. **Data scope:** CIO and project-manager test identities receive only their permitted records, even when prompts attempt to broaden scope.
3. **Query safety:** generated proposals are parsed, policy-checked and executed only through a read-only identity; unsafe or unsupported requests are rejected safely.
4. **Correctness:** an agreed ground-truth suite reports accuracy by question type and failure category; threshold to be set by business owner.
5. **Clarification:** ambiguous questions prompt a targeted clarification instead of silently choosing a consequential interpretation.
6. **Traceability:** answer displays its filters/period/definitions and a traceable source/query reference appropriate to the audience.
7. **Authoring:** creator can pin a result, edit layout, save a draft and request certification.
8. **Governance:** only authorized roles can certify/publish; viewers cannot access drafts or unpublished content.
9. **Versioning:** edits do not silently mutate a published version; republish is auditable.
10. **Exports:** permissions are rechecked at export time and exports follow client policy.
11. **Operations:** failures are observable and actionable; no secrets or unnecessary row-level sensitive data are logged.
12. **Write-back gate:** no production mutations until the separate command/approval/audit design is approved.

## 8. Decisions needed from stakeholders

- Name/owner and reuse rights for Tela/Insights.
- Source platform and supported read-only connector.
- Initial tables/views, joins and business definitions.
- Identity groups and row-level policy mapping.
- LLM provider/data-handling policy (confirm with Vijay).
- Certifier/publisher responsibilities and separation of duties.
- Priority dashboards and required chart types.
- Export/share restrictions.
- Whether write-back is part of the committed product or a later separately approved release.
- Measurable acceptance thresholds for answer accuracy, response time and availability.

## 9. Immediate next actions

1. Request the repositories, data structure, journey source and final PMO dashboard build described in the meeting notes.
2. Create a feature-to-code evidence matrix for J1–J11.
3. Review Tela's ground-truth tests and compare them with the client's initial business questions.
4. Hold a data/security design session to settle source, role mapping and model policy.
5. Build a synthetic sandbox proof with a deliberately restricted dataset.
6. Present the option decision and unresolved risks for approval before committing to a delivery schedule.
