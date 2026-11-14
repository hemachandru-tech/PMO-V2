# PMO Analytics & Conversational Dashboard Platform — Project Summary

| Field | Value |
|---|---|
| Project | PMO Analytics & Conversational Dashboard Platform (working name) |
| Document status | Draft for review |
| Version | 0.1 |
| Last updated | 2026-10-19 |
| Evidence level | L2 for intended journeys/UI prototype; L1 for meeting scope; implementation evidence not supplied |
| Sources analyzed | Meeting scope supplied in chat; `user_journery.html`; master documentation-analysis prompt |
| Related documents | [Architecture](architecture.md), [Options and plan](options_and_plan.md), [Requirements](requirements.md), [Risk register](risk_register.md) |

## Table of contents
- [Executive summary](#executive-summary)
- [Product vision](#product-vision)
- [Users and governance](#users-and-governance)
- [Functional inventory](#functional-inventory)
- [Key workflows](#key-workflows)
- [Data and security](#data-and-security)
- [Recommended direction](#recommended-direction)
- [Open decisions](#open-decisions)
- [Source coverage and limitations](#source-coverage-and-limitations)

## Executive summary

The project aims to deliver a governed PMO analytics experience: users sign in with Microsoft identity, view role-appropriate dashboards, ask questions of enterprise project/finance data, refine answers, pin results as reusable widgets, assemble reports, and publish certified content to defined audiences. A later capability may let authorized users propose and execute controlled changes to TPG records through chat.

The current PMO demo is described as rapidly built, running on synthetic data and a small subset of tables. Its talk-to-data capability is not considered reliable. The separate Tela/Insights product is described as having a more mature tested talk-to-data backend, test cases, ground truth, and database connections. The supplied HTML is a clickable journey prototype, not proof of production implementation.

**Recommended starting hypothesis:** reuse the strongest tested parts of Tela/Insights for semantic retrieval, query generation and evaluation; reuse the PMO experience where it already provides useful user journeys; and compare that hybrid against Power BI / Tableau before committing. Do not make a final build-vs-buy decision until a short code, connector, licensing, security and UX assessment is complete.

## Product vision

A user should be able to move from a business question to a trusted, role-appropriate, explainable view of PMO performance without needing to know SQL or navigate multiple reporting tools.

### Product explained at three levels

- **Simple:** A governed dashboard assistant that answers project and finance questions in charts, tables and reports.
- **Business:** Standardizes project portfolio reporting, reduces manual report preparation, and lets creators build reusable views while governance roles certify and publish them.
- **Technical:** A web application combining enterprise identity, a metadata/semantic model, controlled read-only data access, an LLM-assisted question-to-query pipeline, query validation, execution, visualization, report composition, publishing and auditability.

## Business outcomes to validate

1. Reduce time spent preparing recurring PMO reports.
2. Increase reuse of certified dashboards and measures.
3. Improve consistency of project, budget, spend and status definitions.
4. Let users answer ad-hoc questions without broadening data access.
5. Preserve accountable human approval for published content and any future write-back.

Metrics and baselines are not supplied; agree measurable targets before acceptance testing.

## Current state and evidence

| Area | What the supplied material supports | Confidence |
|---|---|---|
| PMO demo | Exists; built quickly with synthetic data and a handful of tables, per meeting notes | Meeting-note assertion |
| Tela/Insights | Described as having more mature tested talk-to-data assets | Meeting-note assertion; code not supplied |
| Journeys | HTML prototype covers J1–J11 and contains clickable screens/callouts | Inspected HTML source; execution not independently tested |
| Data | Client environment is described as restricted; meeting notes say scope should focus on a small TPG subset | Meeting-note assertion; schema not supplied |
| Production readiness | Not established by the supplied files | Unknown |
| Runtime performance/security | Not measured or verified | Unknown |

## Users and governance

| Role | Intended capability | Controls to clarify |
|---|---|---|
| Admin | Configure connections/data model; manage starter content; potentially create/edit/use analytics | Least privilege, credential management, model approval, audit |
| Creator | Ask questions, create/edit reports and dashboards, arrange widgets, request certification | Draft ownership, shared assets, query and data permissions |
| Governor / certifier | Review and certify measures/models/content; approve publication | Separation of duties, rejection reasons, version history |
| Viewer | View published content, use permitted filters/drill-downs, export only when allowed | Row/column-level access, export restrictions, share controls |
| Data/platform owner | Own source availability, data definitions and access policies | Not clearly mapped to an application role |
| Business owner / PMO | Own KPI definitions, acceptance and adoption | Named accountable owner not confirmed |

The supplied prototype also shows examples of “Preview as role”, “Hide from Viewer”, “Show to all”, “Request certification”, “Republish”, and starter content management. Treat these as intended interactions until implementation is inspected.

## Functional inventory

The HTML prototype identifies 11 journeys:

| ID | Journey | Main intent |
|---|---|---|
| J1 | Connect a database | Admin configures a read-only connection and proposes/approves a data model |
| J2 | First open / Home | Show starter, published and personally owned/shared content |
| J3 | Read a dashboard | Read certified dashboards with filters, definitions and drill-down |
| J4 | Ask, refine and pin | Turn a conversational answer into a widget |
| J5 | Build a report from one request | Plan first, then create a report |
| J6 | Edit the report canvas | Modify by chat or direct manipulation |
| J7 | Grouping, joins, custom SQL | Create richer analytics and manage measures/groupings/SQL widgets |
| J8 | Certify | Review and approve or reject content/model |
| J9 | Compose and publish | Choose report tabs vs grouped page, then publish a version to roles |
| J10 | Manage starter content | Hide, show, delete and restore starter items |
| J11 | Bring another database | Add another source and build on it |

Additional requested scope from the meeting notes: Microsoft SSO; approximately 30 predefined dashboards; chart auto-selection; report filters and PDF export/share/publish; role-specific content; data-level access; later write-back through plan/approve/audit. “Approximately 100 chart types” is an aspiration from the meeting notes, not a verified list in the HTML. The HTML shows a limited illustrative selection and labels some chart options as unavailable.

## Key workflows

### Talk-to-data (proposed)
1. User asks a question.
2. System detects intent and whether the question is sufficiently specific.
3. If ambiguity affects meaning, ask a clarifying question before querying.
4. Retrieve approved schema, business definitions, measures and relevant examples.
5. Generate a query constrained to approved objects and the user's security context.
6. Parse and validate the query against policy.
7. Execute through a read-only identity with timeout and resource limits.
8. Validate the result shape and produce a chart/table/card with the question, filters, definitions and data freshness.
9. Let the user refine, pin, save, or discard the result.
10. Log query metadata, policy decisions and outcome without unnecessarily storing sensitive free text.

This is a proposed flow based on the meeting notes; implementation is not verified.

### Create and publish a report
1. Creator describes desired report.
2. System proposes a plan: widgets, measures, filters, layout and source.
3. Creator reviews and applies the plan.
4. Creator edits canvas by chat or direct manipulation.
5. Creator previews as a target role and validates numbers/filters.
6. Creator requests certification.
7. Governor reviews definitions, query behavior, access behavior and presentation.
8. Authorized publisher publishes a version to selected roles.
9. Changes create a new version; viewers see only the currently published approved version.

The final versioning and separation-of-duty rules need confirmation.

### Future write-back (separate phase)
Chat should first create a proposed change plan, show target records and before/after values, validate fields and business rules, then require explicit approval by an authorized person. Only then should a narrowly scoped write service perform the change transactionally and idempotently, with audit and post-change verification. Do not expose arbitrary LLM-generated INSERT/UPDATE/DELETE against production tables.

## Data and security

- Start with a confirmed, bounded dataset (the notes mention a small subset, roughly 6–10 tables, but exact scope is unresolved).
- Copy table/column names and relationships into a synthetic sandbox; never copy client production rows into a non-approved environment.
- Confirm whether the source is a database, Clarity, an API, or another data platform; connector protocol and MCP suitability are undecided.
- Microsoft SSO is requested, but authentication alone does not enforce data-level authorization.
- Enforce row-level and column-level access in the trusted backend/data layer, not only by hiding UI elements or prompting the LLM.
- Use a read-only database identity and allow-listed views/tables for read-only analytics.
- Free-text risk reasons may contain sensitive information; classify and minimize them, and apply row-level controls before retrieval and before response generation.
- Confirm Microsoft Foundry / Claude / Copilot / other model policy with Vijay before sending client data or schema to any model provider.
- Keep credentials in an approved secret store; do not place them in prompts, browser code or logs.
- Audit access decisions, query metadata, report certification/publication and exports.

## Recommended direction

**Preferred hypothesis: Option B (hybrid reuse), subject to a technical spike.** Reuse the tested Tela/Insights talk-to-data pipeline if its code, licensing, ownership and deployment model are suitable; reuse the PMO demo's useful UI journeys and improve UX/UI. Benchmark this against Option C (Power BI/Tableau) for read-only analytics, and retain Option A (harden PMO app) only if the code assessment shows the core is maintainable.

Do not build a local analytics engine. Use the client's supported data platform and a controlled connector. Do not treat write-back as a simple extension of read-only SQL: it needs a distinct command/approval/audit architecture.

## Open decisions

- Q-001: What is the authoritative name and owner of “Tela/Insights”?
- Q-002: Can the Tela backend and PMO code be accessed, reused, deployed and modified under acceptable licensing/IP terms?
- Q-003: What is the source platform (TPG, Clarity, API, database, or combination), and what read-only interfaces are supported?
- Q-004: Which exact tables, views, columns, joins and definitions are in the initial approved data model?
- Q-005: Who owns KPI definitions and certifies semantic model changes?
- Q-006: Which identity claims/groups map to CIO, project manager and other data scopes?
- Q-007: What does “governor” mean organizationally, and is it distinct from admin/publisher?
- Q-008: Which LLM/model endpoints are approved, under what retention/training/data-residency conditions?
- Q-009: Which export/share channels are allowed and what data-loss-prevention controls apply?
- Q-010: Which chart types are genuinely required at launch versus future library capability?
- Q-011: Is write-back in the committed product scope or a separately approved later phase?
- Q-012: What are acceptance thresholds for answer accuracy, latency, access isolation and report correctness?

## Source coverage and limitations

Inspected the supplied meeting scope in the message, the HTML source (`user_journery.html`, 144,529 bytes; title “Tela I Clickable Journeys”; J1–J11 navigation and UI labels), and the supplied master prompt. The HTML was structurally parsed for headings, buttons and journey navigation; the interactive JavaScript was not executed in a browser. No source-code repository, schema, API specification, deployment configuration, test report or runtime telemetry was supplied. Accordingly, architecture and implementation assessments are proposed, not verified.

## Related documents

- [Architecture](architecture.md)
- [Options and plan](options_and_plan.md)
- [Requirements](requirements.md)
- [Risk register](risk_register.md)
