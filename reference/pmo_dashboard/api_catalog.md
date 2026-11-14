# API & Integration Catalog: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L3 (Implemented static source code) |
| Sources analyzed | `backend/*.py` route definitions, Pydantic schemas, FastAPI routers |
| Related documents | [Project Summary](project_summary.md), [Architecture](architecture.md), [Requirements](requirements.md), [Business Rules](business_rules.md), [Data Dictionary](data_dictionary.md), [Risk Register](risk_register.md) |

---

## 1. Overview & Transport Standards

The PMO Intelligence & Analytics backend is implemented in FastAPI (`backend/main.py`), listening on port 8000 by default and proxied through Vite (`/api/*`) in local development. In production bundles, FastAPI serves both API routes and static compiled assets (`frontend/dist`) under a unified origin.

### Transport & Protocol Conventions
- **Base URL**: `/api`
- **Content-Type**: `application/json` (requests and responses)
- **Authentication**: Deployment-level identity (`DEMO_CIO`). No authorization header is currently required; identity is resolved server-side from environment configuration (`principal = "cio:<DEMO_CIO>"`) or defaults to `"anonymous"`.
- **CORS Policy**: Configured in `main.py` allowing origins matching `http://(localhost|127\.0\.0\.1)(:\d+)?`.
- **Idempotency**: All `GET` endpoints and read-only query endpoints are idempotent. Custom Reports modification routes follow standard REST semantics (`POST`, `PUT`, `PATCH`, `DELETE`).

---

## 2. Table of Contents
- [1. Overview & Transport Standards](#1-overview--transport-standards)
- [2. Table of Contents](#2-table-of-contents)
- [3. System, Platform & Metadata Endpoints](#3-system-platform--metadata-endpoints)
- [4. Projects Workspace Report Endpoints](#4-projects-workspace-report-endpoints)
- [5. Demand Pipeline & Request Endpoints](#5-demand-pipeline--request-endpoints)
- [6. Ask TELA Natural Language Endpoints](#6-ask-tela-natural-language-endpoints)
- [7. Talk to Data & Chat Endpoints](#7-talk-to-data--chat-endpoints)
- [8. Custom Reports & Widget Management Endpoints](#8-custom-reports--widget-management-endpoints)

---

## 3. System, Platform & Metadata Endpoints

### `API-001`: Health Check
- **Path & Method**: `GET /api/health`
- **Purpose**: Verifies backend liveness and active database connectivity.
- **Request Parameters**: None.
- **Success Response (200 OK)**:
  ```json
  {
    "status": "success",
    "message": "Database connected"
  }
  ```
- **Error Response (200 OK / 503)**:
  ```json
  {
    "status": "error",
    "message": "Database credentials missing"
  }
  ```

---

### `API-002`: Deployment Metadata
- **Path & Method**: `GET /api/meta`
- **Purpose**: Provides client header with reporting as-of date, pinned CIO identity, and total database record counts.
- **Success Response (200 OK)**:
  ```json
  {
    "asOf": "2026-09-30",
    "asOfPinned": true,
    "projects": 100,
    "requests": 150,
    "demoCio": "Nomsa Osei"
  }
  ```

---

### `API-003`: Multi-Dimensional Filter Options
- **Path & Method**: `GET /api/filters`
- **Purpose**: Retrieves available distinct dropdown values for global filters scoped to the caller's authorized tenancy.
- **Query Parameters**:
  - `context` (string, optional, default `"PROJECTS"`): `"PROJECTS"` or `"REQUESTS"`.
  - `cio`, `region`, `portfolio`, `project_manager`, `priority`, `platform` (list of strings, optional).
- **Success Response (200 OK)**:
  ```json
  {
    "cios": ["Nomsa Osei"],
    "regions": ["East Africa", "Francophone", "KSA"],
    "portfolios": ["Core Banking", "Digital Channels"],
    "projectManagers": ["Ahmed Al-Mansoor", "Sarah Jenkins"],
    "priorities": ["Critical", "High", "Moderate", "Low"],
    "platforms": ["Network One", "Magnati", "Legacy / Other"]
  }
  ```

---

### `API-004`: Projects Portfolio Overview
- **Path & Method**: `GET /api/overview`
- **Purpose**: Delivers aggregated pulse KPIs, reported status distribution, calculated health summary, financial exposure, and demographic breakdowns for the Projects landing page.
- **Query Parameters**: Multi-value filter parameters (`region`, `portfolio`, `project_manager`, `priority`, `platform`).
- **Success Response (200 OK)**:
  ```json
  {
    "pulse": {
      "totalProjects": 25,
      "activeProjects": 16,
      "inDelivery": 13,
      "pastDue": 4,
      "scheduleRisk": 6,
      "staleCount": 2,
      "onHoldCount": 3,
      "estCost": 15200000.0,
      "appBudget": 16500000.0,
      "remainingBudget": 1300000.0,
      "budgetUtilization": 92.1,
      "overBudgetProjects": 3
    },
    "pmoMetrics": {
      "calculatedHealth": { "Green": 8, "Amber": 5, "Red": 2, "insufficient": 1 },
      "financialExposure": {
        "approvedBudget": 16500000.0,
        "forecastEac": 15200000.0,
        "variance": -1300000.0,
        "variancePct": -7.88,
        "overBudgetCount": 3,
        "projectsCompared": 15,
        "projectsExcluded": 1
      },
      "onHoldAging": { "under30": 1, "days30to60": 1, "over60": 1, "missingDate": 0 }
    }
  }
  ```

---

## 4. Projects Workspace Report Endpoints

All report endpoints accept `POST` with a JSON filter payload:
```json
{
  "cio": ["Nomsa Osei"],
  "region": ["East Africa"],
  "portfolio": [],
  "delivery_manager": [],
  "priority": [],
  "platform": []
}
```

| ID | Method & Path | Primary Module | Purpose & Returned Payload Summary |
|---|---|---|---|
| `API-005` | `POST /api/executive` | `backend/executive.py` | Executive Portfolio view: CIO & portfolio map, strategic initiatives health, delivery heatmap, ExCo priorities. |
| `API-006` | `POST /api/governance` | `backend/governance.py` | Governance Compliance: baselining compliance, budget code compliance, PM assignment rate, PCR volume, closure compliance. |
| `API-007` | `POST /api/delivery` | `backend/delivery.py` | Delivery & Milestones: upcoming delivery buckets (0–30, 31–60, 61–90 days), past due projects, stage completion distribution, schedule variance. |
| `API-008` | `POST /api/finance` | `backend/finance.py` | Financial Analysis: CAPEX budget vs forecast EAC, cost breakdown (internal, external, hardware, software, contingency). |
| `API-009` | `POST /api/flow` | `backend/flow.py` | Flow Metrics: flow velocity, throughput, work-in-progress (WIP) aging, Kanban board status columns. |
| `API-010` | `POST /api/projects` | `backend/projects.py` | Project list endpoint: returns tabular records with sorting, filtering, calculated health classification, and date flags. |
| `API-011` | `GET /api/projects/{project_number}` | `backend/projects.py` | Single project detail: complete record attributes, financial EAC vs budget, schedule baseline slip, and diagnostic health score details. |

---

## 5. Demand Pipeline & Request Endpoints

| ID | Method & Path | Primary Module | Purpose & Returned Payload Summary |
|---|---|---|---|
| `API-012` | `POST /api/requests` | `backend/requests.py` | Request list: paginated list of project requests with filters, stage dates, and approval states. |
| `API-013` | `POST /api/requests/overview` | `backend/requests.py` | Requests landing page metrics: total requests, open requests, state distribution, overdue governance actions. |
| `API-014` | `POST /api/requests/demand` | `backend/requests.py` | CIO & regional demand analysis: intake volume by business unit, region, and platform. |
| `API-015` | `POST /api/requests/discovery` | `backend/requests.py` | Stage 1 Discovery schedule: planned vs actual start/end dates and discovery duration variances. |
| `API-016` | `POST /api/requests/on-hold` | `backend/requests.py` | On-hold demand aging: analysis of intake requests placed on hold and hold reasons. |
| `API-017` | `POST /api/requests/budget-cost` | `backend/requests.py` | Commercial cost estimates: estimated total cost, setup revenue, and recurring baseline revenue. |
| `API-018` | `POST /api/requests/demand-effort` | `backend/requests.py` | Effort estimates: internal and external man-days requested across intake items. |
| `API-019` | `POST /api/requests/governance` | `backend/requests.py` | Request governance: budget code presence, PM assignment, and DARB exemption status. |
| `API-020` | `POST /api/requests/overdue-gates` | `backend/requests.py` | Overdue gate audit: list of open requests failing DOU, DARB, or SOW planned issuance dates. |
| `API-021` | `GET /api/requests/{request_id}` | `backend/requests.py` | Request detail: complete intake record attributes, commercial figures, and linked project ID. |

---

## 6. Ask TELA Natural Language Endpoints

### `API-022`: Ask TELA Engine Availability Status
- **Path & Method**: `GET /api/ask-tela/status`
- **Purpose**: Checks whether certified and exploratory natural language pipelines are active.
- **Success Response (200 OK)**:
  ```json
  {
    "certified": true,
    "exploratory": true
  }
  ```
- **Operational Logic**: `exploratory` is `true` only if `PMO_EXPLORATORY_ENABLED=1`, LLM provider package is importable, API key is present, and database grants check passes.

---

### `API-023`: Submit Natural Language Question
- **Path & Method**: `POST /api/ask-tela`
- **Purpose**: Primary conversational entry point for both certified and exploratory portfolio questions.
- **Concurrency Control**: Semaphore limited to maximum 4 concurrent requests (returns HTTP 429 when saturated).
- **Request Schema**:
  ```json
  {
    "question": "Show average forecast cost by platform",
    "conversation_id": "conv-uuid-1234",
    "history": [
      { "question": "How many active projects are there?", "status": "ok", "row_count": 13 }
    ],
    "filters": {
      "region": ["KSA"],
      "portfolio": [],
      "project_manager": [],
      "priority": [],
      "platform": []
    }
  }
  ```
- **Success Response (200 OK - Exploratory Example)**:
  ```json
  {
    "status": "ok",
    "kind": "exploratory",
    "label": "Exploratory — not an official PMO metric.",
    "answer": "Calculates the average forecast total cost (EAC) grouped by technology platform. 3 platforms compared.",
    "columns": [
      { "name": "platform", "type": "text", "label": "Platform", "format": "text" },
      { "name": "avg_cost", "type": "float", "label": "Average Cost (USD)", "format": "currency" }
    ],
    "rows": [
      { "platform": "Network One", "avg_cost": 921137.5 },
      { "platform": "Legacy / Other", "avg_cost": 845200.0 }
    ],
    "row_count": 2,
    "truncated": false,
    "chart": { "type": "bar", "x": "platform", "y": "avg_cost" },
    "kpi": null,
    "viz": { "type": "bar" },
    "suggestions": [
      "Show projects on Network One platform",
      "What is the total budget for KSA region?"
    ],
    "scope_view": {
      "text": "Region: KSA",
      "restrictedTo": "Nomsa Osei"
    },
    "as_of": "2026-09-30",
    "pin": {
      "token": "dGhpc19pc19hbl9vcGFxdWVfdG9rZW4...",
      "suggested_title": "Average forecast cost by platform"
    }
  }
  ```
- **Error Codes (200 OK with status="error" or 4xx/5xx)**:
  - `exploratory_disabled`: Exploratory flag is 0 or grant gate failed.
  - `planner_unavailable`: No LLM provider configured or key invalid.
  - `validation_error`: SQL validator rejected generated AST.
  - `scope_unsupported`: Filter requested does not exist on target table.
  - `timeout`: Execution exceeded 15 seconds.

---

## 7. Talk to Data & Chat Endpoints

| ID | Method & Path | Primary Module | Purpose & Returned Payload Summary |
|---|---|---|---|
| `API-024` | `GET /api/talk-to-data/templates` | `backend/talk_to_data.py` | Catalog of approved certified intent templates for the Ask TELA opening screen. |
| `API-025` | `POST /api/talk-to-data/briefing` | `backend/talk_to_data.py` | Pre-calculated executive briefing payload (portfolio size, budget variance, top risks) rendered on screen open. |
| `API-026` | `POST /api/talk-to-data/query` | `backend/talk_to_data.py` | Direct invocation of certified intent handlers by intent ID (`intent = "total_projects"`). |
| `API-027` | `POST /api/talk-to-data/suggestions` | `backend/talk_to_data.py` | Dynamic suggested questions tailored to current filter selections and date. |
| `API-028` | `GET /api/chat/status` | `backend/chat.py` | Liveness check for conversational multi-turn chat LLM provider. |
| `API-029` | `POST /api/chat` | `backend/chat.py` | Tool-calling conversational chat endpoint supporting multi-turn dialogue. |

---

## 8. Custom Reports & Widget Management Endpoints

All Custom Reports endpoints enforce ownership isolation by caller principal (`cio:<DEMO_CIO>` or `anonymous`). Any attempt to access, modify, or delete another principal's report returns HTTP 404.

### `API-030`: List User Custom Dashboards
- **Path & Method**: `GET /api/custom-reports/dashboards`
- **Purpose**: Lists all custom reports owned by the active principal.
- **Success Response (200 OK)**:
  ```json
  [
    {
      "id": "194512efc1ee4e3ba5354f99307b54e8",
      "name": "My CIO Portfolio",
      "created_at": "2026-10-16T11:27:16+00:00",
      "updated_at": "2026-10-16T11:29:11+00:00",
      "widget_count": 2
    }
  ]
  ```

---

### `API-031`: Create Custom Dashboard
- **Path & Method**: `POST /api/custom-reports/dashboards`
- **Request Body**: `{"name": "Q4 Strategic Review"}`
- **Constraints**: Dashboard names must be non-empty and unique per owner.
- **Success Response (201 Created)**: Returns dashboard object. Returns 409 Conflict if name exists.

---

### `API-032`: Get Dashboard Layout & Widgets
- **Path & Method**: `GET /api/custom-reports/dashboards/{id}`
- **Purpose**: Retrieves dashboard structure, widget positions (`position`, `span`), and widget metadata. Does **not** include raw query strings or data rows.
- **Success Response (200 OK)**:
  ```json
  {
    "id": "194512efc1ee4e3ba5354f99307b54e8",
    "name": "My CIO Portfolio",
    "items": [
      {
        "widget_id": "d6cfcd26048d4f51b326bc2ddeb668b6",
        "position": 0,
        "span": 2,
        "widget": {
          "id": "d6cfcd26048d4f51b326bc2ddeb668b6",
          "title": "Average forecast cost by platform",
          "question": "Show average forecast cost by platform",
          "source": "exploratory",
          "source_label": "Exploratory",
          "viz": "auto",
          "has_snapshot": true
        }
      }
    ]
  }
  ```

---

### `API-033`: Pin Ask TELA Answer
- **Path & Method**: `POST /api/custom-reports/pins`
- **Purpose**: Persists an Ask TELA answer as a reusable widget and places it on a custom report.
- **Request Body**:
  ```json
  {
    "pin_token": "dGhpc19pc19hbl9vcGFxdWVfdG9rZW4...",
    "title": "Platform Cost Comparison",
    "dashboard_id": "194512efc1ee4e3ba5354f99307b54e8",
    "new_dashboard_name": null
  }
  ```
- **Response Codes**:
  - `200 OK`: Widget pinned successfully.
  - `409 Conflict`: Definition is already pinned to this specific dashboard.
  - `410 Gone`: Pin token has expired ($> 1$ hour) or backend was restarted.

---

### `API-034`: Execute / Refresh Widget Data
- **Path & Method**: `POST /api/custom-reports/widgets/{id}/data`
- **Purpose**: Runs live computation for a single custom widget under the current server scope, or falls back to snapshot.
- **Request Body**:
  ```json
  {
    "filters": {
      "region": ["East Africa"]
    }
  }
  ```
- **Success Response (200 OK - Live Mode)**:
  ```json
  {
    "mode": "live",
    "data": {
      "columns": [...],
      "rows": [...],
      "chart": { "type": "bar", "x": "platform", "y": "avg_cost" },
      "kpi": null,
      "source_label": "Exploratory",
      "as_of": "2026-09-30"
    }
  }
  ```
- **Degraded Response (200 OK - Snapshot Fallback)**:
  ```json
  {
    "mode": "snapshot",
    "snapshot_at": "2026-10-16T11:27:16+00:00",
    "reason": "Exploratory queries disabled on server. Displaying saved snapshot.",
    "data": { ... }
  }
  ```

---

### `API-035`: Custom Reports CRUD Summary

| Method | Path | Purpose | Success Code |
|---|---|---|---|
| `PATCH` | `/api/custom-reports/dashboards/{id}` | Rename dashboard (`{"name": "New Name"}`) | 200 OK |
| `DELETE` | `/api/custom-reports/dashboards/{id}` | Delete dashboard (saved widgets retained in library) | 204 No Content |
| `POST` | `/api/custom-reports/dashboards/{id}/items` | Add existing saved widget (`{"widget_id": "..."}`) | 200 OK |
| `DELETE` | `/api/custom-reports/dashboards/{id}/items/{widget_id}` | Remove widget from dashboard only | 204 No Content |
| `PUT` | `/api/custom-reports/dashboards/{id}/layout` | Reorder & resize widgets (`{"items": [{"widget_id": "...", "span": 1\|2}]}`) | 200 OK |
| `GET` | `/api/custom-reports/widgets` | List user's saved widget library | 200 OK |
| `PATCH` | `/api/custom-reports/widgets/{id}` | Edit widget title or preferred visualization view (`auto`, `table`, `bar`, `line`, `kpi`) | 200 OK |
| `DELETE` | `/api/custom-reports/widgets/{id}` | Delete widget permanently from library and all reports | 204 No Content |
