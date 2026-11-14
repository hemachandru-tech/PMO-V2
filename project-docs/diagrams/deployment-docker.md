# Docker Deployment Diagram

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md#deployment-architecture) · [Technical document](../technical_document.md#build-and-deployment-pipeline) · [Diagram index](README.md) |

## Purpose

Shows the containers, Docker networks, exposed ports and persistent volumes for a single-host Compose profile. A Helm profile uses the same images (ADR-005).

```mermaid
flowchart LR
    INTERNET["Users - HTTPS 443"]

    subgraph EdgeNet["Network edge"]
        PROXY["proxy - Traefik or nginx - port 443"]
    end

    subgraph AppNet["Network app - internal only"]
        WEB["web - nginx serving SPA - 8080"]
        API["api - FastAPI - 8000"]
        RUN["connector-runner - 8100"]
        GW["llm-gateway - 8200"]
        KS["knowledge - 8300"]
        WK["worker - Celery"]
        BEAT["scheduler - Celery beat"]
        KC["keycloak - 8443"]
    end

    subgraph DataNet["Network data - internal only"]
        PG[("postgres with pgvector - 5432")]
        RD[("redis - 6379")]
        BAO["openbao - 8200"]
        MINIO[("minio - 9000")]
        OLL["ollama embeddings - 11434"]
    end

    subgraph Volumes["Persistent volumes"]
        V1["pg_data"]
        V2["minio_data"]
        V3["bao_data"]
        V4["ollama_models"]
    end

    EGRESS["Egress to client sources and approved LLM"]

    INTERNET --> PROXY
    PROXY --> WEB
    PROXY --> API
    PROXY --> KC
    API --> RUN
    API --> GW
    API --> KS
    API --> PG
    API --> RD
    WK --> RD
    WK --> PG
    WK --> MINIO
    BEAT --> RD
    RUN --> BAO
    RUN --> MINIO
    KS --> PG
    KS --> OLL
    KC --> PG
    RUN --> EGRESS
    GW --> EGRESS
    PG --- V1
    MINIO --- V2
    BAO --- V3
    OLL --- V4
```

## Key interactions

- Only the proxy publishes a host port. Internal ports bind to Docker networks, not 0.0.0.0 on the host (lesson from S31 §6.2).
- Only the connector runner and LLM gateway have egress; this simplifies firewall rules.
- Every container needs a health check and restart policy (REC-012). Port numbers are illustrative defaults.
