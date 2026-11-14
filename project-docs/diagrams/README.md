# Diagram Index

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | L1 – Business only; every diagram is **Proposed** (no implementation evidence) |
| Sources analyzed | 31 files — see [project_summary.md](../project_summary.md#source-inventory-and-coverage) |
| Related documents | [Project summary](../project_summary.md) · [Architecture](../architecture.md) · [Function document](../function_document.md) · [Technical document](../technical_document.md) · [DB scheme](../db_scheme.md) · [Risk register](../risk_register.md) |

This folder holds one Mermaid diagram per file. The entity relationship diagram is not here: it lives in [db_scheme.md](../db_scheme.md#entity-relationship-diagram), which owns the database structure.

| Diagram | Type | Purpose | Label | Referenced by |
|---|---|---|---|---|
| [System context](system-context.md) | Flowchart | Actors, Tela boundary and external systems | Proposed | architecture.md, project_summary.md |
| [Component architecture](component-architecture.md) | Flowchart | Docker services and their COMP IDs | Proposed | architecture.md, technical_document.md |
| [Ask request sequence](ask-request-sequence.md) | Sequence | Question to live widget, end to end | Proposed | architecture.md, function_document.md |
| [Custom SQL check and run](custom-sql-check-and-run.md) | Sequence | Validation and execution of a custom SQL widget | Proposed | function_document.md, technical_document.md |
| [Certification sequence](certification-sequence.md) | Sequence | Request, review, look-alike detection, decision | Proposed | function_document.md |
| [Publish sequence](publish-sequence.md) | Sequence | Preview as role, pre-publish checks, version publish | Proposed | function_document.md |
| [RAG indexing flow](rag-indexing-flow.md) | Flowchart | Knowledge ingestion, review gate, hybrid retrieval | Proposed | architecture.md, technical_document.md |
| [Docker deployment](deployment-docker.md) | Flowchart | Containers, networks, ports, volumes | Proposed | architecture.md, technical_document.md |
| [Connector adapters](connector-adapters.md) | Flowchart | Spec compilation per engine and read-only enforcement | Proposed | architecture.md, technical_document.md |
| [Widget definition lifecycle](widget-definition-lifecycle.md) | State | Personal to certified field and widget states | Proposed | function_document.md, business_rules.md |
| [Dashboard version lifecycle](dashboard-version-lifecycle.md) | State | Draft, checks, published versions, restore | Proposed | function_document.md, business_rules.md |
| [Auth and scope flow](auth-scope-flow.md) | Sequence | Entra sign-in through Keycloak to scope resolution | Proposed | architecture.md, technical_document.md |
| [Connect and model sequence](connect-and-model-sequence.md) | Sequence | Read-only connection test, introspection, model assistant, approval (J1, J11) | Proposed | architecture.md |
| [Model version lifecycle](model-version-lifecycle.md) | State | Proposed, draft, certified, superseded and retired model versions | Proposed | architecture.md |
