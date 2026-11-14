# Custom Reports Widget State Lifecycle Diagram

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Architecture Label | **Verified (Current Implemented State)** |
| Related documents | [Architecture](../architecture.md), [Business Rules](../business_rules.md), [Diagram Index](README.md) |

---

## Purpose
This state machine diagram models the lifecycle of a Custom Reports widget, starting from natural language query evaluation in Ask TELA, through tokenized pinning, live execution, layout mutations, and snapshot degradation.

```mermaid
stateDiagram-v2
    [*] --> QueryAnswered : User submits Ask TELA question

    state QueryAnswered {
        [*] --> DataReturned : Answer with >0 rows produced
        DataReturned --> TokenIssued : Server issues opaque pin_token (TTL 1hr) in server memory
    }

    TokenIssued --> PinnedWidget : POST /api/custom-reports/pins {pin_token, dashboard_id}
    TokenIssued --> Expired : 1 hour elapses or backend restarts (HTTP 410 Gone)

    state PinnedWidget {
        [*] --> PersistedSQLite : Stored in SQLite (definition, owner, def_hash, snapshot)
        PersistedSQLite --> DisplayedOnDashboard : Associated in items table with position and span
    }

    state DashboardExecution {
        [*] --> FetchingWidgetData : User loads Custom Dashboard
        FetchingWidgetData --> AttemptLiveReExecution : POST /api/custom-reports/widgets/{id}/data
        
        state AttemptLiveReExecution {
            [*] --> GatingCheck : Check server PMO_EXPLORATORY_ENABLED & DB grants
            GatingCheck --> ScopedExecution : Gate passes -> re-scope with current DEMO_CIO & filters
            ScopedExecution --> LiveSuccess : Query executes within 15s timeout
            
            GatingCheck --> LiveFailed : Gate closed / DB error
            ScopedExecution --> LiveFailed : Timeout (>15s) / DB error
        }
        
        LiveSuccess --> RenderLive : Returns mode='live' with fresh database rows
        LiveFailed --> CheckSnapshot : Can live data be retrieved?
        
        state CheckSnapshot {
            [*] --> SnapshotAvailable : snapshot JSON exists in SQLite
            [*] --> SnapshotMissing : snapshot is null
        }

        SnapshotAvailable --> RenderSnapshot : Returns mode='snapshot' with "Snapshot" badge & timestamp
        SnapshotMissing --> RenderError : Returns error card with Retry
    }

    PinnedWidget --> DashboardExecution : On page load / user refresh
    
    state WidgetModifications {
        PinnedWidget --> LayoutUpdated : PUT /dashboards/{id}/layout (reorder, resize span 1|2)
        PinnedWidget --> ViewChanged : PATCH /widgets/{id} (switch to bar, line, table, kpi)
        PinnedWidget --> RemovedFromReport : DELETE /dashboards/{id}/items/{widget_id} (retained in library)
        PinnedWidget --> DeletedPermanently : DELETE /widgets/{id} (removed from library and all reports)
    }
```
