# Ask Request Sequence

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md#agent-and-rag-architecture) · [Function document](../function_document.md#feat-018-ask-a-question) · [API catalog](../api_catalog.md) · [Diagram index](README.md) |

## Purpose

Traces a question from the ask bar to a live widget answer streamed back to the browser.

```mermaid
sequenceDiagram
    autonumber
    actor U as Creator or Viewer
    participant WEB as Web app
    participant API as API service
    participant ORC as Agent orchestrator
    participant KS as Knowledge service
    participant GW as LLM gateway
    participant QE as Query engine
    participant RUN as Connector runner
    participant SRC as Source database

    U->>WEB: Types a question
    WEB->>API: POST ask stream with question and page filters
    API->>API: Validate token, resolve role and scope
    API->>ORC: Start turn
    ORC->>KS: Retrieve approved model, measures, gold examples
    KS-->>ORC: Ranked context with citations
    ORC->>GW: AG-01 intent router prompt
    GW->>GW: Mask, leak scan, audit
    GW-->>ORC: Intent
    alt Meaning is ambiguous
        ORC->>GW: AG-02 clarifier prompt
        GW-->>ORC: Clarifying question
        ORC-->>WEB: SSE clarification event
    else Clear question
        ORC->>GW: AG-03 query-spec planner prompt
        GW-->>ORC: Typed query spec JSON
        ORC->>QE: Compile spec
        QE->>QE: Check measures and links are approved
        QE->>QE: Compile to engine dialect
        QE->>QE: Validate AST and inject scope
        QE->>RUN: Execute scoped query with limits
        RUN->>SRC: Read-only query
        SRC-->>RUN: Rows
        RUN-->>QE: Rows with column types
        QE-->>ORC: Result and definitions
        ORC->>ORC: AG-07 visual advisor picks suitable visual
        ORC->>ORC: AG-08 narrator builds sentence from result only
        ORC-->>WEB: SSE widget event with chart, definition, scope, as-of
    end
    WEB-->>U: Live widget with badges and refine chips
```

## Key interactions

- The planner returns a typed spec, not SQL (ADR-001). The query engine compiles and validates it.
- Scope comes from the token, never from the request body (RULE-004, RULE-005).
- The narrator may only state figures present in the result (RULE-022). Raw rows are not sent to the model (RULE-019).
- Stage events stream over SSE so the user sees progress.
