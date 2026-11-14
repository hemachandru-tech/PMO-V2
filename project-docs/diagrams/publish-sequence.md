# Publish Sequence

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Function document](../function_document.md#feat-039-publish-to-roles) · [Dashboard version lifecycle](dashboard-version-lifecycle.md) · [Business rules](../business_rules.md) · [Diagram index](README.md) |

## Purpose

Shows preview as role, pre-publish checks and creation of a new published version.

```mermaid
sequenceDiagram
    autonumber
    actor P as Publisher - Admin
    participant WEB as Web app
    participant API as API service
    participant QE as Query engine
    participant DB as Metadata database

    P->>WEB: Open publish dialog and choose roles
    WEB->>API: Preview as role or person
    API->>QE: Run every widget under that scope
    QE-->>API: Row counts, widgets shown or hidden with reasons
    API-->>WEB: Preview result
    WEB->>API: Run pre-publish checks
    API->>DB: Every widget certified?
    API->>QE: Every widget returns data for every chosen role?
    API->>API: Shared filters reach every widget?
    alt Any check fails
        API-->>WEB: Blocking issues with inline fixes
        Note over WEB,DB: Current live version stays live
    else All checks pass
        P->>WEB: Confirm publish
        WEB->>API: Publish
        API->>DB: Freeze draft as version N plus 1
        API->>DB: Mark previous version superseded
        API->>DB: Write audit event
        API-->>WEB: Published version number
    end
```

## Key interactions

- Publishing to a role requires every widget certified (RULE-009, RULE-024).
- Failing checks never take the live version down.
- Who may publish (Admin only or a separate publisher) is open (GAP-006, Q-006).
