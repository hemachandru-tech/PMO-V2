# Connect a Source and Approve the Data Model — Sequence

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** (L1 — derived from S01 §4.1, J1 and J11; no implementation exists) |
| Related documents | [Architecture](../architecture.md) · [Project summary](../project_summary.md) · [Functional specification](../function_document.md) · [Business rules](../business_rules.md) · [Diagram index](README.md) |

## Purpose

Shows journeys J1 and J11: an admin adds a read-only connection, Tela verifies that it is read-only, introspects the schema without storing samples, the model assistant (AG-04) proposes links, measures and dimensions and asks explicit questions, the admin corrects the model in plain language, and approves it as a certified version. Starter pack detection and knowledge indexing follow approval.

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant Web as Web app
    participant API as API service
    participant Vault as Secrets store
    participant Runner as Connector runner
    participant Src as Source system
    participant Orch as Agent orchestrator
    participant GW as LLM gateway
    participant KS as Knowledge service
    participant DB as Metadata DB

    Admin->>Web: Fill connection form and click Test connection
    Web->>API: POST connection test
    API->>Vault: Store credential, receive secret reference
    API->>Runner: Test and verify read-only
    Runner->>Src: Connect with read-only identity
    Runner->>Src: Inspect grants or roles
    Src-->>Runner: Grants
    alt Write or admin privilege found
        Runner-->>API: Rejected - identity is not read-only
        API-->>Web: Show read-only failure message
    else Read-only verified
        Runner-->>API: Verified
        API->>DB: Save connection with secret reference
        API->>Runner: Introspect schema
        Runner->>Src: Read catalogue and optional column samples
        Src-->>Runner: Tables, columns, keys, sample values
        Runner-->>API: Catalogue plus transient samples
        API->>DB: Save connection objects without samples
        API->>Orch: Propose data model
        Orch->>GW: Schema, names and masked sample hints
        GW-->>Orch: Proposed links, measures, dimensions, questions
        Orch-->>API: Typed model proposal
        API-->>Web: Show proposal and explicit questions
        Admin->>Web: Correct in words, answer questions
        Web->>API: Corrections
        API->>Orch: Apply corrections
        Orch-->>API: Revised proposal
        Admin->>Web: Approve model
        Web->>API: Approve
        API->>DB: Create certified model version v1
        API->>KS: Index certified definitions
        API->>API: Check starter pack signatures
        API-->>Web: Offer starter pack if schema matches
    end
```

## Key interactions

- Credentials go straight to the secrets store; the metadata database keeps only a reference (ADR-004, NFR-013).
- The runner refuses connections whose identity can write (RULE-001). Samples used to propose labels are passed to the assistant only in masked form and are **not stored** (S01 §4.1, RULE-019).
- Approval creates an immutable model version; later corrections produce v2, v3 (FEAT-009, RULE-015).
- Only certified definitions are indexed into the knowledge service for retrieval (FEAT-014).
