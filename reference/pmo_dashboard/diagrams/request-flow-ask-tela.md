# Ask TELA Request Flow Sequence Diagram

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Architecture Label | **Verified (Current Implemented State)** |
| Related documents | [Architecture](../architecture.md), [Business Rules](../business_rules.md), [Diagram Index](README.md) |

---

## Purpose
This sequence diagram details the end-to-end request lifecycle when a user asks a question in the Ask TELA interface, highlighting the dual-path decision tree (Certified vs. Exploratory), security gates, AST validation, scope rewriting, and pin token issuance.

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Executive
    participant SPA as React Frontend (Ask TELA Screen)
    participant Adapter as tela_adapter.py (FastAPI)
    participant Service as tela_service.py (Orchestrator)
    participant Talk as talk_to_data.py (Certified Handlers)
    participant Planner as tela_planner.py & Provider
    participant Validator as tela_validator.py (sqlglot)
    participant Scope as tela_scope.py
    participant Executor as tela_executor.py
    participant DB as MySQL (tpg_synthetic)
    participant Viz as tela_viz.py

    User->>SPA: Types question (e.g. "Show average forecast cost by platform")
    SPA->>Adapter: POST /api/ask-tela {question, filters, history}
    Note over Adapter: Validates payload & acquires concurrency semaphore (max 4)
    Adapter->>Service: ask(question, context, filters)

    alt Certified Intent Match
        Service->>Talk: Exact match in talk_to_data.INTENTS?
        Talk-->>Service: Yes (matches registered metric)
        Service->>Talk: Execute certified handler(scope)
        Talk->>DB: Governed SQL query
        DB-->>Talk: Rows
        Talk-->>Service: Certified result (evidence cards, scope line)
    else Exploratory Natural Language Question
        Service->>Executor: Check gating preconditions (PMO_EXPLORATORY_ENABLED=1)
        Executor->>DB: SHOW GRANTS FOR current_user()
        DB-->>Executor: Grants list
        Executor-->>Service: Verified Read-Only Gate Passed (cached 60s)
        
        Service->>Planner: plan(question, context, schema allow-list)
        Planner->>Planner: Call LLM with submit_plan tool
        Planner-->>Service: Return generated SQL query

        Service->>Validator: validate(sql, allow_list)
        Note over Validator: AST check (single SELECT, approved tables/cols, no SLEEP/INTO)
        Validator-->>Service: Validation Result (Passed)

        Service->>Scope: apply_scope(sql, pinned_cio, filters)
        Note over Scope: Rewrites tables into derived subqueries WHERE cio = :pinned_cio
        Scope-->>Service: Scoped SQL Query

        Service->>Executor: run(scoped_sql, timeout=15s, limit=200)
        Executor->>DB: SELECT platform, AVG(cost_total_usd)... FROM (SELECT * FROM projects WHERE cio=...)
        DB-->>Executor: Raw database rows
        Executor-->>Service: ExecutionResult (rows, column_types)
    end

    Service->>Viz: decide(rows, columns, question)
    Note over Viz: Selects KPI card / Bar / Line / Table & generates factual explanation
    Viz-->>Service: Formatted visualization & follow-up suggestions

    Service->>Service: Generate server-side pin token (TTL 1 hour)
    Service-->>Adapter: Payload {status: ok, kind: exploratory, viz, pin: {token, title}}
    Adapter-->>SPA: JSON Response
    SPA-->>User: Renders chart / table with trust label ("Exploratory") and "Pin to Custom Reports" button
```
