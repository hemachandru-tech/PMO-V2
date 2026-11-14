# Architecture Diagram Index: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Related documents | [Project Summary](../project_summary.md), [Architecture](../architecture.md) |

---

## Diagram Catalog

This directory contains standalone, high-fidelity Mermaid diagrams modeling the system architecture, component boundaries, execution flows, and data lifecycle of the PMO Intelligence & Analytics platform.

| Diagram File | Title | Type | Evidence Label | Primary Purpose |
|---|---|---|---|---|
| [system-context.md](system-context.md) | System Context Diagram (C4 Level 1) | C4 Context | **Verified** | Depicts external actors (PMO Directors, CIOs, Project Managers), external LLM providers, source CRM systems, and database boundaries. |
| [component-architecture.md](component-architecture.md) | Container & Component Architecture (C4 Level 2/3) | Component Architecture | **Verified** | Details the React SPA, Vite proxy, FastAPI router modules, certified vs exploratory pipelines, and dual-database storage. |
| [request-flow-ask-tela.md](request-flow-ask-tela.md) | Ask TELA Dual-Path Sequence Diagram | Sequence Diagram | **Verified** | Traces end-to-end question flow: intent classification, AST validation, scope rewriting, read-only grant check, and tokenized pin issuance. |
| [entity-relationship.md](entity-relationship.md) | Entity-Relationship Diagram | ER Diagram | **Verified** | Illustrates database schemas across MySQL (`projects`, `project_requests`) and SQLite (`dashboards`, `widgets`, `items`). |
| [custom-reports-lifecycle.md](custom-reports-lifecycle.md) | Custom Reports State & Execution Lifecycle | State Machine | **Verified** | Models the widget lifecycle from Ask TELA pin token creation through live execution, layout mutations, and snapshot degradation. |
