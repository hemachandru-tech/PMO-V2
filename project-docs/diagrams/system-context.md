# System Context Diagram

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md) · [Project summary](../project_summary.md) · [Diagram index](README.md) |

## Purpose

Shows who uses Tela, which external systems it depends on, and the trust boundaries between them.

```mermaid
flowchart LR
    subgraph People["People"]
        ADMIN["Admin - PMO"]
        CREATOR["Creator - CIO, delivery manager, analyst"]
        VIEWER["Viewer"]
    end

    subgraph TelaBoundary["Tela platform - trusted zone"]
        TELA["Tela web app and services"]
    end

    subgraph Identity["Identity"]
        ENTRA["Microsoft Entra ID"]
    end

    subgraph Sources["Client data sources - read-only"]
        PG[("PostgreSQL")]
        MY[("MySQL")]
        MS[("SQL Server")]
        MONGO[("MongoDB")]
        DV[("Microsoft Dataverse")]
        XL["Excel and CSV uploads"]
    end

    subgraph AI["Model providers - untrusted boundary"]
        LLM["Approved LLM provider or local model"]
        EMB["Embedding model"]
    end

    ADMIN --> TELA
    CREATOR --> TELA
    VIEWER --> TELA
    TELA -->|"OIDC via Keycloak broker"| ENTRA
    TELA -->|"Read-only queries"| PG
    TELA -->|"Read-only queries"| MY
    TELA -->|"Read-only queries"| MS
    TELA -->|"Read-only aggregation pipelines"| MONGO
    TELA -->|"TDS endpoint or Web API"| DV
    XL -->|"Governed upload"| TELA
    TELA -->|"Schema, definitions, questions only"| LLM
    TELA -->|"Approved knowledge text"| EMB
```

## Key interactions

- All people sign in with Microsoft; role and scope come from group membership.
- Tela only reads from sources. It never writes, alters schemas or copies tables. Excel/CSV is the exception: the uploaded file is stored under governance (ADR-012).
- Model providers receive schema, definitions and questions, never raw rows (RULE-019). Which provider is allowed is open (Q-009).
