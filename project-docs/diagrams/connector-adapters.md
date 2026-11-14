# Connector Adapters Diagram

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md#connector-architecture) · [Technical document](../technical_document.md#connector-implementation) · [Diagram index](README.md) |

## Purpose

Shows how one typed query spec is compiled for each source engine, and where read-only access is enforced.

```mermaid
flowchart TB
    SPEC["Typed query spec - measure, breakdown, filters, limit, visual"]
    SQLIN["Custom SQL text - SQL engines only"]
    SCOPE["Scope and page filters from token and request"]

    COMPILE["Query engine compiler"]
    VALID["AST validator per dialect"]
    INJECT["Scope injector - fail closed"]

    SPEC --> COMPILE
    SQLIN --> VALID
    COMPILE --> VALID
    VALID --> INJECT
    SCOPE --> INJECT

    INJECT --> PGA["PostgreSQL adapter - postgres dialect"]
    INJECT --> MYA["MySQL adapter - mysql dialect"]
    INJECT --> MSA["SQL Server adapter - tsql dialect"]
    INJECT --> DVA["Dataverse adapter"]
    INJECT --> MGA["MongoDB adapter - aggregation pipeline"]
    INJECT --> XLA["Excel and CSV adapter - DuckDB"]

    DVA --> TDS["TDS endpoint - read-only T-SQL subset"]
    DVA --> WAPI["Web API - OData or FetchXML fallback"]

    PGA --> RO1["Read-only session and SELECT-only role"]
    MYA --> RO2["Read-only session and SELECT-only user"]
    MSA --> RO3["db_datareader role and read-only intent"]
    TDS --> RO4["Endpoint is read-only by design"]
    WAPI --> RO5["GET requests only with read-only security role"]
    MGA --> RO6["read role and stage allow-list"]
    XLA --> RO7["Read-only DuckDB views over stored file"]
```

## Key interactions

- One spec, many compilers: measures and links come from the approved model, so every engine answers the same definition (ADR-001, ADR-002).
- Each adapter has two read-only layers: code validation and a database-level identity (RULE-001, REC-005).
- Dataverse capabilities and limits are assumptions until the spike (REC-006, ADR-013). MongoDB uses compiled pipelines with a mandatory first scope stage (ADR-014).
