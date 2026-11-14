# Component Architecture Diagram

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md#component-architecture) · [Technical document](../technical_document.md) · [Diagram index](README.md) |

## Purpose

Shows the Docker services (COMP IDs) and how requests and data move between them.

```mermaid
flowchart TB
    USER["Browser"]

    subgraph Edge["Edge"]
        PROXY["COMP-017 Reverse proxy and TLS"]
    end

    subgraph App["Application services"]
        WEB["COMP-001 Web app - React SPA on nginx"]
        API["COMP-002 API service - FastAPI modular monolith"]
        ORCH["COMP-003 Agent orchestrator - module in API"]
        QE["COMP-004 Query engine - compile, validate, scope"]
        RUNNER["COMP-005 Connector runner - read-only execution"]
        GW["COMP-006 LLM gateway"]
        KS["COMP-007 Knowledge service - RAG"]
        WORKER["COMP-008 Background worker"]
        EXPORT["COMP-009 Export renderer - in worker image"]
        KC["COMP-010 Identity broker - Keycloak"]
    end

    subgraph Data["Data services"]
        PGM[("COMP-011 PostgreSQL 16 with pgvector")]
        REDIS[("COMP-012 Redis")]
        VAULT["COMP-013 Secrets store - OpenBao or Vault"]
        OBJ[("COMP-014 Object storage")]
        EMB["COMP-015 Embedding model runtime"]
    end

    OBS["COMP-016 Observability stack"]
    SRC[("Client sources")]
    LLM["LLM provider"]
    ENTRA["Microsoft Entra ID"]

    USER --> PROXY
    PROXY --> WEB
    PROXY --> API
    PROXY --> KC
    KC --> ENTRA
    API --> ORCH
    ORCH --> KS
    ORCH --> GW
    ORCH --> QE
    API --> QE
    QE --> RUNNER
    RUNNER --> SRC
    RUNNER --> VAULT
    RUNNER --> OBJ
    GW --> LLM
    KS --> PGM
    KS --> EMB
    API --> PGM
    API --> REDIS
    WORKER --> REDIS
    WORKER --> PGM
    WORKER --> QE
    WORKER --> KS
    EXPORT --> OBJ
    API -.-> OBS
    RUNNER -.-> OBS
    GW -.-> OBS
    WORKER -.-> OBS
```

## Key interactions

- The API service is the only public application endpoint besides the SPA and Keycloak.
- Only the connector runner holds source credentials (fetched from COMP-013) and opens source connections. Every call to it arrives through the query engine's scoped path (REC-004).
- Only the LLM gateway talks to model providers (ADR-008).
- The worker runs introspection, indexing, cache warming, exports and pack updates from Redis queues (ADR-011).
