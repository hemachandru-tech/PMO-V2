# Custom SQL Check and Run

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Function document](../function_document.md#feat-031-custom-sql-widget) · [Business rules](../business_rules.md) · [Technical document](../technical_document.md) · [Diagram index](README.md) |

## Purpose

Shows how a hand-written or chat-drafted custom SQL widget is validated before it touches the source.

```mermaid
sequenceDiagram
    autonumber
    actor C as Creator
    participant WEB as Web app
    participant API as API service
    participant GW as LLM gateway
    participant QE as Query engine
    participant RUN as Connector runner
    participant SRC as Source database

    opt Draft with chat
        C->>WEB: Describe the question
        WEB->>API: Request SQL draft
        API->>GW: AG-10 SQL drafter prompt with approved schema only
        GW-->>API: Draft SQL text
        API-->>WEB: Draft shown in editor, not run
    end
    C->>WEB: Click Check and run
    WEB->>API: POST custom SQL check and run
    API->>QE: Validate
    QE->>QE: Parse with engine dialect
    QE->>QE: Exactly one read-only SELECT
    QE->>QE: Only allowed tables and views
    QE->>QE: Reject side-effect and timing functions
    alt Validation fails
        QE-->>API: Rejection with reason
        API-->>WEB: Show reason inline
    else Validation passes
        QE->>QE: Inject page filters at the page_filters placeholder
        QE->>QE: Inject creator scope as derived subqueries
        QE->>QE: Apply 10000 row cap
        QE->>RUN: Execute with 20 second timeout
        RUN->>SRC: Read-only session query
        SRC-->>RUN: Rows
        RUN-->>QE: Rows
        QE-->>API: Result preview
        API-->>WEB: Preview with Custom SQL badge
    end
    C->>WEB: Save as widget
    WEB->>API: Save widget, badge stays until certified
```

## Key interactions

- The drafter never runs SQL; a person must press Check and run.
- Validation order follows RULE-002, RULE-003, RULE-007, RULE-004 and RULE-006.
- The source identity is read-only regardless of validation (RULE-001), giving two independent layers.
- Custom SQL is unavailable on MongoDB connections in Phase 1 (ADR-014).
