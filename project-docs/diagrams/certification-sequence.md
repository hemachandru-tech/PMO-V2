# Certification Sequence

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Function document](../function_document.md#feat-037-certification-decisions) · [Business rules](../business_rules.md) · [Widget definition lifecycle](widget-definition-lifecycle.md) · [Diagram index](README.md) |

## Purpose

Shows how a personal grouping, band, join measure or custom SQL widget becomes certified, merged or rejected.

```mermaid
sequenceDiagram
    autonumber
    actor C as Creator
    actor A as Admin
    participant API as API service
    participant QE as Query engine
    participant ORC as Agent orchestrator
    participant DB as Metadata database

    C->>API: Request certification for an item
    API->>DB: Create certification request, state Pending
    API->>QE: Compute results across all data
    API->>ORC: AG-09 reviewer compares with certified fields
    ORC->>QE: Run look-alike comparisons on same base
    QE-->>ORC: Result differences
    ORC-->>API: Look-alike findings and semantic diffs
    API->>DB: Store findings
    A->>API: Open queue item
    API-->>A: Rule, results, usage, SQL, dependencies, findings
    alt Certify
        A->>API: Certify
        API->>DB: Record decision with definition snapshot
        API->>DB: Update badges everywhere it is used
    else Merge into existing field
        A->>API: Merge into certified field
        API->>DB: Repoint widgets to the existing field
        API->>C: Notify author
    else Reject or send back
        A->>API: Reject with note
        API->>C: Notify author with note
    end
```

## Key interactions

- Items enter the queue only when shared or placed on something going to a role (RULE-010).
- Look-alike detection compares definitions and results, not only names (RULE-011).
- Every decision is logged with who, when and the definition at that moment (NFR-007).
