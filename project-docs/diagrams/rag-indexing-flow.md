# RAG Indexing and Retrieval Flow

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md#agent-and-rag-architecture) · [Technical document](../technical_document.md#agent-implementation) · [DB scheme](../db_scheme.md) · [Diagram index](README.md) |

## Purpose

Shows how knowledge enters the pgvector index and how agents retrieve it safely.

```mermaid
flowchart TB
    subgraph Inputs["Knowledge sources"]
        M["Approved data model - measures, dimensions, links"]
        G["Gold question examples"]
        P["Starter pack definitions"]
        D["Uploaded reference documents"]
        CP["Chart pattern library"]
    end

    REVIEW{"Review gate - approved?"}
    PEND["Held as pending review - not retrievable"]
    CHUNK["Chunk and attach metadata - tenant, connection, model version, sensitivity"]
    SAN["Sanitise instruction-like text"]
    EMBED["Embed with configured model - 1024 dimensions default"]
    STORE[("PostgreSQL knowledge_chunks - vector HNSW index and tsvector index")]

    Q["Agent query from orchestrator"]
    VEC["Vector search - cosine"]
    KW["Keyword search - full text"]
    RRF["Merge with reciprocal rank fusion"]
    FILTER["Filter by tenant, connection, scope and model version"]
    CITE["Citation check - context items carry source IDs"]
    OUT["Context for prompt via LLM gateway"]

    M --> REVIEW
    G --> REVIEW
    P --> REVIEW
    D --> REVIEW
    CP --> REVIEW
    REVIEW -->|"No"| PEND
    REVIEW -->|"Yes"| SAN
    SAN --> CHUNK
    CHUNK --> EMBED
    EMBED --> STORE

    Q --> VEC
    Q --> KW
    STORE --> VEC
    STORE --> KW
    VEC --> RRF
    KW --> RRF
    RRF --> FILTER
    FILTER --> CITE
    CITE --> OUT
```

## Key interactions

- Nothing is retrievable until approved, following the TelaKnowledge review lifecycle (S02 §5). Certified model items are approved by definition.
- Hybrid retrieval (vector plus keyword) mirrors the TelaKnowledge reference (S31 §3.1); embedding dimension and model are configurable (Q-016).
- Source data rows are never indexed.
