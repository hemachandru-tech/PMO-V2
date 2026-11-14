# System Context Diagram: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Architecture Label | **Verified (Current Implemented State)** |
| Related documents | [Architecture](../architecture.md), [Project Summary](../project_summary.md), [Diagram Index](README.md) |

---

## Purpose
This diagram illustrates the primary external actors, system boundaries, integration interfaces, and external services interacting with the PMO Intelligence & Analytics platform.

```mermaid
flowchart TD
    subgraph Users["Primary Stakeholders"]
        CIO["Executive / Pinned CIO<br/>(Portfolio health, strategic priorities, Ask TELA)"]
        PMO["PMO Director & Analysts<br/>(Governance compliance, delivery tracking, custom reports)"]
        PM["Project Managers & Leads<br/>(Schedule variance, baseline tracking, drawer diagnostics)"]
    end

    subgraph Boundaries["PMO Intelligence & Analytics Platform"]
        SPA["Frontend Web Application<br/>(React 19, Tailwind CSS 4, Recharts 3)<br/>Runs in Executive Browser"]
        API["FastAPI Backend Application<br/>(Python 3.13, Uvicorn, SQLAlchemy, sqlglot)<br/>Stateless REST API & Analytics Engine"]
        SQLITE[("Custom Reports Database<br/>SQLite 3 File: data/custom_reports.sqlite3<br/>User Dashboards, Widgets, Snapshots")]
    end

    subgraph ExternalServices["External Infrastructure & Systems"]
        MYSQL[("Enterprise PMO Database<br/>MySQL 8.0 / Azure DB for PostgreSQL Target<br/>Tables: projects, project_requests")]
        LLM["External Language Model Providers<br/>(Google Gemini 2.5 Flash / OpenAI GPT-4.1 / Anthropic Claude 3.5)<br/>HTTPS REST API (Tool Calling)"]
        CRM["Enterprise Source Systems<br/>(Microsoft Dynamics CRM / TPG Project Online)<br/>Batch Data Extraction / Synthetic Dump"]
    end

    CIO -->|Interacts via Browser (HTTP/HTTPS)| SPA
    PMO -->|Interacts via Browser (HTTP/HTTPS)| SPA
    PM -->|Interacts via Browser (HTTP/HTTPS)| SPA

    SPA -->|REST API Calls /api/* (JSON)| API
    API -->|Read-Only Queries (Pool)| MYSQL
    API -->|CRUD Dashboards & Widgets| SQLITE
    API -->|Natural Language Planning (HTTPS)| LLM
    CRM -.->|Data Extraction & ETL| MYSQL
```
