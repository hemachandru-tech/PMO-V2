# Authentication and Scope Flow

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Label | **Proposed** |
| Related documents | [Architecture](../architecture.md#security-architecture) · [Technical document](../technical_document.md) · [Function document](../function_document.md#roles-and-permission-matrix) · [Diagram index](README.md) |

## Purpose

Shows Microsoft sign-in through the Keycloak broker and how the API turns identity into role and data scope.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant WEB as Web app
    participant KC as Keycloak broker
    participant EN as Microsoft Entra ID
    participant API as API service
    participant DB as Metadata database

    U->>WEB: Open Tela
    WEB->>KC: Authorization code flow with PKCE
    KC->>EN: Redirect to Microsoft sign-in
    EN-->>KC: ID token with object ID and groups
    KC->>KC: Map identity and broker groups
    KC-->>WEB: Authorization code
    WEB->>KC: Exchange code for access token
    KC-->>WEB: Access token
    WEB->>API: Request with bearer access token
    API->>API: Validate signature, issuer, audience, expiry
    API->>DB: Look up user by tenant and external object ID
    API->>DB: Resolve groups to roles via group role mappings
    API->>DB: Load scope policies and field policies
    API->>API: Build principal with role, scope predicates, hidden fields
    API->>DB: Set tenant context for row-level security
    API-->>WEB: Response within role and scope
```

## Key interactions

- Users are matched on stable directory identifiers, never on e-mail alone (S03 §5.2).
- Group membership never grants Admin without an explicit, approved mapping (S03 §9, RISK-007).
- Scope predicates are attached to the principal and injected by the query engine on every surface (RULE-004). Where mappings are configured is open (GAP-013).
