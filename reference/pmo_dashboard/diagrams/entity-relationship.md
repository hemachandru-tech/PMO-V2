# Entity-Relationship Diagram: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Architecture Label | **Verified (Current Implemented State)** |
| Related documents | [Data Dictionary](../data_dictionary.md), [Architecture](../architecture.md), [Diagram Index](README.md) |

---

## Purpose
This diagram documents the logical data model across the MySQL enterprise database and the SQLite custom reports database.

```mermaid
erDiagram
    PROJECT_REQUESTS ||--o| PROJECTS : "converts to (project_request_id)"
    DASHBOARDS ||--o{ ITEMS : "contains"
    WIDGETS ||--o{ ITEMS : "displayed in"

    PROJECTS {
        varchar project_id PK "UUID"
        varchar project_number UK "Business identifier"
        varchar name "Project title"
        varchar portfolio "Portfolio domain"
        varchar program "Programme title"
        varchar status "Active (Open) vs Inactive"
        varchar current_state "Active, On Hold, Project Created, etc."
        varchar lifecycle_phase "Delivery lifecycle stage"
        varchar cio "Assigned CIO (Tenancy)"
        varchar region "Geographic region"
        varchar delivery_manager "Delivery Manager"
        varchar priority "Critical, High, Moderate, Low"
        varchar platform "Platform: Network One, Flex, Magnati"
        varchar key_priority_project "Yes/No (ExCo Priority)"
        float percent_complete "Percent Complete (0-100)"
        varchar overall_kpi "Reported status color"
        varchar schedule_kpi "Reported schedule color"
        varchar cost_kpi "Reported cost color"
        varchar issue_kpi "Reported issue severity"
        date planned_deployment_date "Planned go-live date"
        date first_baselined_deployment_date "First baselined date"
        date actual_deployment_date "Actual deployment date"
        date actual_project_start_date "Start date"
        date on_hold_date "Date put on hold"
        decimal approved_budget_value_usd "Approved budget (USD)"
        decimal cost_total_usd "Forecast total cost EAC (USD)"
        int approved_pcr_counter "Count of approved PCRs"
        varchar project_request_id FK "Originating request ID"
        datetime modified_on "Last record modification"
    }

    PROJECT_REQUESTS {
        varchar project_request_id PK "UUID"
        varchar project_number "Converted project number"
        varchar title "Demand title"
        varchar request_state "New, Active, On Hold, Return, etc."
        varchar cio "Assigned CIO"
        varchar region "Geographic region"
        varchar priority "Business priority"
        date stage1_discovery_planned_start_date "Discovery planned start"
        date stage1_discovery_planned_end_date "Discovery planned end"
        date stage1_discovery_actual_end_date "Discovery actual end"
        varchar dou_required "Yes/No"
        date dou_planned_date "DOU due date"
        date dou_issued_date "DOU issued date"
        varchar darb_exempted "Yes/No"
        date darb_review_date "DARB review date"
        date darb_approval_date "DARB approval date"
        date sow_planned_date "SOW planned date"
        date sow_issued_date "SOW issued date"
        decimal approved_budget_value_usd "Approved budget"
        decimal cost_total_usd "Estimated cost EAC (USD)"
        int efforts_internal_mandays "Internal effort (days)"
        int efforts_external_mandays "External effort (days)"
    }

    DASHBOARDS {
        text id PK "UUID"
        text owner "Principal: cio:<CIO> or anonymous"
        text name "Custom dashboard name"
        text created_at "ISO-8601 UTC timestamp"
        text updated_at "ISO-8601 UTC timestamp"
    }

    WIDGETS {
        text id PK "UUID"
        text owner "Principal: cio:<CIO> or anonymous"
        text title "Widget title"
        text question "Original user question"
        text source "certified or exploratory"
        text definition "JSON: intent ID or un-scoped SELECT"
        text def_hash "SHA-256 hash of definition"
        text viz "auto, table, bar, line, kpi"
        text filters "JSON: narrowing filters at pin"
        text snapshot "JSON: cached payload"
        text snapshot_at "Timestamp of snapshot"
        text created_at "ISO-8601 UTC timestamp"
        text updated_at "ISO-8601 UTC timestamp"
    }

    ITEMS {
        text dashboard_id PK,FK "Dashboard foreign key"
        text widget_id PK,FK "Widget foreign key"
        integer position "Display order position"
        integer span "Grid span: 1 (half) or 2 (full)"
    }
```
