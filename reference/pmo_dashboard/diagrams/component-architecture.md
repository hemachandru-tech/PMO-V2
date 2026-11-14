# Component Architecture Diagram: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Architecture Label | **Verified (Current Implemented State)** |
| Related documents | [Architecture](../architecture.md), [Project Summary](../project_summary.md), [Diagram Index](README.md) |

---

## Purpose
This diagram details the internal modules, routers, service pipelines, security boundaries, and storage connectors within the FastAPI application and React single-page frontend.

```mermaid
flowchart TB
    subgraph Client["Client Browser (React SPA)"]
        UI_NAV["Navigation & Shell<br/>(Sidebar, Header, AsOf, FilterBar)"]
        UI_REPORTS["Governed Reports<br/>(Executive, Governance, Delivery, Finance, Flow)"]
        UI_DEMAND["Demand Pipeline<br/>(Lifecycle, Discovery, Commercial, Intake)"]
        UI_TELA["Ask TELA Screen<br/>(KPI Cards, Charts, Tables, Pin Control)"]
        UI_CUSTOM["Custom Reports Engine<br/>(Dashboard Editor, Widget Grid, Layout Manager)"]
    end

    subgraph BackendApp["FastAPI Backend (backend/main.py)"]
        ROUTERS["HTTP Routers & Middleware<br/>(CORS, Load-shedding Semaphore, Filter Builder)"]
        
        subgraph GovernedAnalytics["Governed Analytics Tier"]
            REP_MODULES["Report Modules<br/>(executive.py, governance.py, delivery.py, finance.py, flow.py, requests.py)"]
            METRICS_ENG["Canonical Metrics Engine<br/>(metrics.py)"]
            HEALTH_ENG["Objective Health Model<br/>(health.py: 4-factor scoring & date flags)"]
        end

        subgraph NaturalLanguageEngine["Ask TELA Dual Engine"]
            TELA_ADAPTER["API Adapter<br/>(tela_adapter.py: Request validation & concurrency gate)"]
            TELA_SERVICE["Orchestrator<br/>(tela_service.py: Certified routing vs Exploratory dispatch)"]
            
            subgraph CertifiedFlow["Certified Path"]
                TALK_DATA["Intent Catalog & Handlers<br/>(talk_to_data.py, talk_compose.py)"]
            end

            subgraph ExploratoryFlow["Exploratory Pipeline"]
                PLANNER["LLM Planner<br/>(tela_planner.py, chat_providers.py)"]
                VALIDATOR["AST SQL Validator<br/>(tela_validator.py: sqlglot MySQL dialect)"]
                SCOPE["Scope Rewriter<br/>(tela_scope.py: Derived table injection)"]
                EXECUTOR["Read-Only Executor<br/>(tela_executor.py: Grants check, timeouts, row caps)"]
            end
            
            VIZ_RESOLVER["Visualization Resolver<br/>(tela_viz.py: KPI / Bar / Line / Table formatting)"]
        end

        subgraph CustomReportsEngine["Custom Reports Persistence Tier"]
            CR_SERVICE["Custom Reports Router & Logic<br/>(custom_reports.py: Principal isolation & pin tokens)"]
        end
    end

    subgraph DataStorage["Data Storage Layer"]
        MYSQL[("MySQL: tpg_synthetic<br/>(projects, project_requests)<br/>Connection Pool: PyMySQL")]
        SQLITE[("SQLite: custom_reports.sqlite3<br/>(dashboards, widgets, items)<br/>WAL Journal Mode")]
    end

    subgraph ExternalLLM["External LLM API"]
        PROVIDER["Google Gemini / OpenAI / Anthropic"]
    end

    %% Client to Backend
    UI_NAV --> ROUTERS
    UI_REPORTS --> ROUTERS
    UI_DEMAND --> ROUTERS
    UI_TELA --> ROUTERS
    UI_CUSTOM --> ROUTERS

    %% Routers dispatch
    ROUTERS --> REP_MODULES
    ROUTERS --> TELA_ADAPTER
    ROUTERS --> CR_SERVICE

    %% Governed flow
    REP_MODULES --> METRICS_ENG
    REP_MODULES --> HEALTH_ENG
    REP_MODULES -->|SQL SELECT| MYSQL

    %% Ask TELA flow
    TELA_ADAPTER --> TELA_SERVICE
    TELA_SERVICE -->|Certified Intent| TALK_DATA
    TALK_DATA --> METRICS_ENG
    TALK_DATA --> HEALTH_ENG
    TALK_DATA -->|SQL SELECT| MYSQL

    TELA_SERVICE -->|Exploratory Question| PLANNER
    PLANNER <-->|Tool Call Prompt / Plan| PROVIDER
    PLANNER -->|Generated SQL| VALIDATOR
    VALIDATOR -->|Valid AST| SCOPE
    SCOPE -->|Scoped Derived Table SQL| EXECUTOR
    EXECUTOR -->|Read-Only SELECT (Max 15s, Max 200 rows)| MYSQL
    
    TELA_SERVICE --> VIZ_RESOLVER
    VIZ_RESOLVER --> TELA_ADAPTER

    %% Custom Reports flow
    CR_SERVICE <-->|Opaque Tokens & Layout CRUD| SQLITE
    CR_SERVICE -.->|Re-execute Live Definition| TELA_SERVICE
```
