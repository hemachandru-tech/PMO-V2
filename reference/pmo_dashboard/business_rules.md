# Business Rules & Calculation Catalog: PMO Intelligence & Analytics

| Field | Value |
|---|---|
| Project | PMO Intelligence & Analytics (Prodian / TELA PMO) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L3 (Implemented static source code) with L1/L2 (Specifications) |
| Sources analyzed | `backend/health.py`, `backend/metrics.py`, `backend/tela_*.py`, `backend/custom_reports.py`, `Docs/CANONICAL_METRICS.md` |
| Related documents | [Project Summary](project_summary.md), [Architecture](architecture.md), [Requirements](requirements.md), [API Catalog](api_catalog.md), [Data Dictionary](data_dictionary.md), [Risk Register](risk_register.md) |

---

## 1. Overview & Governing Principles

This catalog documents the mathematical definitions, algorithmic rules, decision boundaries, and edge-case behaviors governing portfolio analytics in the PMO Intelligence & Analytics platform.

### Core Calculation Axioms
1. **Single Source of Truth**: All calculation predicates reside in `backend/metrics.py` and `backend/health.py`. No screen or API endpoint may implement an inline divergence.
2. **Reported Status $\neq$ Calculated Health**: Self-reported PM RAG ratings (`overall_kpi`) are never overwritten by algorithmic health; both are presented side-by-side.
3. **Forecast (EAC) $\neq$ Spend**: `cost_total_usd` represents Estimate at Completion (EAC) / Forecast. The system possesses **no actual spend** data.
4. **Objective Time Reference**: All aging and date rules evaluate against a single system `asOf` date, never the user's browser clock.
5. **No Invented Data**: When data is missing, the system reports `None` or `Insufficient data`, never defaulting missing values to favorable scores.

---

## 2. Table of Contents
- [1. Overview & Governing Principles](#1-overview--governing-principles)
- [2. Table of Contents](#2-table-of-contents)
- [3. Portfolio Population & Scope Rules](#3-portfolio-population--scope-rules)
- [4. Project Health Scoring Model (Detailed Algorithmic Specification)](#4-project-health-scoring-model-detailed-algorithmic-specification)
- [5. Schedule, Delivery & Aging Rules](#5-schedule-delivery--aging-rules)
- [6. Financial & Budget Rules](#6-financial--budget-rules)
- [7. Demand & Request Governance Rules](#7-demand--request-governance-rules)
- [8. Natural Language & Query Execution Gating Rules](#8-natural-language--query-execution-gating-rules)
- [9. Custom Reports & Widget Lifecycle Rules](#9-custom-reports--widget-lifecycle-rules)

---

## 3. Portfolio Population & Scope Rules

### `RULE-001`: As-Of Date Resolution
- **Plain-English Statement**: The reporting reference date is determined at backend startup. If `PMO_AS_OF` is set in the environment, all database sessions pin their session timestamp to that date at 12:00:00 UTC. Otherwise, the database's live `CURDATE()` is utilized.
- **Inputs**: Environment variable `PMO_AS_OF` (format `YYYY-MM-DD`).
- **Calculation Formula**:
  $$\text{asOf} = \begin{cases} \text{date.fromisoformat}(\text{PMO\_AS\_OF}) & \text{if PMO\_AS\_OF is defined} \\ \text{CURDATE}() & \text{otherwise} \end{cases}$$
- **Worked Example**:
  - `PMO_AS_OF = "2026-09-30"`
  - Result: Every call to `asOf` yields `2026-09-30`. Database executes `SET TIMESTAMP = UNIX_TIMESTAMP('2026-09-30 12:00:00')`.
- **Edge Cases & Nulls**: A malformed date string (e.g. `2026-02-31`) causes the backend to fail fast on startup via `date.fromisoformat(AS_OF)` to prevent partial query runs.
- **Configurability**: Configurable via `.env`. Status: **Implemented** (`backend/main.py:21-61`).

---

### `RULE-002`: Open Portfolio Population (`metrics.ACTIVE`)
- **Plain-English Statement**: An "Open Project" is defined strictly by the TPG status flag `status = 'Active'`. In the enterprise dataset, this maps precisely to three lifecycle states: `Project Created`, `Active`, and `On Hold`. Closed states (`Completed`, `Dropped`, `Cancelled`, `Archived`, `Expired`) are marked `status = 'Inactive'`.
- **Inputs**: `projects.status`.
- **SQL Predicate**: `status = 'Active'`
- **Population Impact**:
  - In the baseline dataset: 65 Open projects (13 In-Delivery Active, 3 On Hold, 49 Project Created), 35 Inactive projects $\rightarrow$ 100 Total projects.
- **Configurability**: Fixed governed definition. Status: **Implemented** (`backend/metrics.py:17`).

---

### `RULE-003`: In-Delivery Portfolio Population (`metrics.IN_DELIVERY`)
- **Plain-English Statement**: Active in-delivery projects represent the open portfolio actively undergoing execution. Projects in `Project Created` (intake/planning) and `On Hold` (execution suspended) are open but excluded from in-delivery execution metrics.
- **Inputs**: `projects.current_state`.
- **SQL Predicate**: `current_state = 'Active'`
- **Worked Example**:
  - A project has `status = 'Active'` and `current_state = 'On Hold'`.
  - Open Project evaluation: True.
  - In-Delivery evaluation: False.
- **Configurability**: Fixed governed definition. Status: **Implemented** (`backend/metrics.py:21`).

---

## 4. Project Health Scoring Model (Detailed Algorithmic Specification)

### `RULE-004`: 4-Factor Calculated Project Health
- **Plain-English Statement**: The calculated health of an open project is an objective composite score (0–100) combining four weighted operational dimensions: Schedule (35%), Cost (25%), Progress (25%), and Issues (15%).
- **Weights**:
  $$W_{\text{sched}} = 0.35, \quad W_{\text{cost}} = 0.25, \quad W_{\text{prog}} = 0.25, \quad W_{\text{iss}} = 0.15$$
- **Component 1: Schedule Score (35%)**:
  - Reference Date ($R$): First baselined deployment date (`first_baselined_deployment_date`). If unbaselined, planned deployment date (`planned_deployment_date`).
  - Slip Days ($S$):
    - If Deployed (`actual_deployment_date` present): $S = \text{actual} - R$
    - If Past Due (`asOf` > planned and undeployed): $S = \text{asOf} - \min(\text{planned}, R)$
    - If Not Yet Due: $S = \text{planned} - R$
  - Schedule Score Bands:
    $$S_{\text{sched}} = \begin{cases} 100 & \text{if } S \le 0 \text{ days} \\ 75 & \text{if } 0 < S \le 15 \text{ days} \\ 50 & \text{if } 15 < S \le 30 \text{ days} \\ 25 & \text{if } 30 < S \le 60 \text{ days} \\ 0 & \text{if } S > 60 \text{ days} \end{cases}$$
- **Component 2: Cost Score (25%)**:
  - Evaluates forecast total cost (`cost_total_usd`) against approved budget (`approved_budget_value_usd`).
  - Condition: Valid only if $\text{budget} > 0$ and $\text{cost} \ge 0$.
  - Variance Percentage: $V = \frac{\text{cost} - \text{budget}}{\text{budget}} \times 100\%$
  - Cost Score Bands:
    $$S_{\text{cost}} = \begin{cases} 100 & \text{if } V \le 0\% \\ 75 & \text{if } 0\% < V \le 10\% \\ 50 & \text{if } 10\% < V \le 25\% \\ 25 & \text{if } 25\% < V \le 50\% \\ 0 & \text{if } V > 50\% \end{cases}$$
- **Component 3: Progress Score (25%)**:
  - Evaluates actual percent complete (`percent_complete`, $0 \le P \le 100$) against expected linear time elapsed.
  - Start Date: `actual_project_start_date` or `created_on`.
  - Total Duration: $D_{\text{total}} = \text{planned} - \text{start}$ (must be $> 0$).
  - Elapsed Duration: $D_{\text{elapsed}} = \text{asOf} - \text{start}$.
  - Expected Percent: $E = \min(100.0, \max(0.0, \frac{D_{\text{elapsed}}}{D_{\text{total}}} \times 100.0))$.
  - Progress Gap: $G = P - E$ (percentage points).
  - Progress Score Bands:
    $$S_{\text{prog}} = \begin{cases} 100 & \text{if } G \ge -5\% \\ 75 & \text{if } -15\% \le G < -5\% \\ 50 & \text{if } -25\% \le G < -15\% \\ 25 & \text{if } -50\% \le G < -25\% \\ 0 & \text{if } G < -50\% \end{cases}$$
- **Component 4: Issues Score (15%)**:
  - Evaluates reported issue severity (`issue_kpi`).
  - Scoring:
    $$S_{\text{iss}} = \begin{cases} 100 & \text{if } \text{issue\_kpi} = \text{'Green'} \\ 50 & \text{if } \text{issue\_kpi} \in (\text{'Yellow'}, \text{'Amber'}) \\ 0 & \text{if } \text{issue\_kpi} = \text{'Red'} \end{cases}$$

---

### `RULE-005`: Evidence Coverage & Insufficient Data Classification
- **Plain-English Statement**: An overall project health score is calculated only when available components represent at least 60% of the total model weight (`MIN_COVERAGE = 0.60`). If evidence coverage is $< 60\%$, the project is classified as "Insufficient data" and no synthetic score is assigned.
- **Coverage Formula**:
  $$C = \sum_{k \in \text{Available}} W_k$$
- **Qualifying Combinations ($C \ge 0.60$)**:
  - Schedule + Cost + Progress + Issues ($1.00$) $\rightarrow$ Full Confidence.
  - Schedule + Cost + Progress ($0.85$) $\rightarrow$ Partial Confidence.
  - Schedule + Cost ($0.60$) $\rightarrow$ Partial Confidence.
  - Schedule + Progress ($0.60$) $\rightarrow$ Partial Confidence.
- **Disqualifying Combinations ($C < 0.60$) $\rightarrow$ "Insufficient data"**:
  - Schedule + Issues ($0.50$)
  - Cost + Issues ($0.40$)
  - Progress + Issues ($0.40$)
  - Any single isolated component (e.g., Schedule only $0.35$).
- **Normalized Score Formula**:
  $$\text{RawScore} = \frac{\sum_{k \in \text{Available}} S_k \times W_k}{C}$$
- **Base Classification Thresholds**:
  - Score $\ge 75 \rightarrow$ Green
  - $50 \le \text{Score} < 75 \rightarrow$ Amber
  - Score $< 50 \rightarrow$ Red
- **Worked Numerical Example**:
  - Project inputs:
    - Planned: `2026-12-01`, Baseline: `2026-11-01`, asOf: `2026-09-30`. Not yet due. Slip $S = 30$ days $\rightarrow S_{\text{sched}} = 50$.
    - Budget: $\$100,000$, Cost: $\$110,000$. Overrun $10\% \rightarrow S_{\text{cost}} = 75$.
    - Start: `2026-01-01`. Total duration 334 days. Elapsed 272 days. Expected $E = 81.4\%$. Actual $P = 50\%$. Gap $G = -31.4\% \rightarrow S_{\text{prog}} = 25$.
    - Issue KPI: `Yellow` $\rightarrow S_{\text{iss}} = 50$.
  - Coverage: $0.35 + 0.25 + 0.25 + 0.15 = 1.00 \ge 0.60$ (Qualifies).
  - Raw Score:
    $$\text{Score} = (50 \times 0.35) + (75 \times 0.25) + (25 \times 0.25) + (50 \times 0.15) = 17.5 + 18.75 + 6.25 + 7.5 = 50.0$$
  - Base Status: Amber ($50.0 \ge 50$).
- Status: **Implemented** (`backend/health.py:27-38, 245-280`).

---

### `RULE-006`: Severe Condition Overrides
- **Plain-English Statement**: Even if the weighted average yields a Green or Amber score, acute failure in any single critical dimension triggers an automatic status override.
- **Override Trigger Hierarchy**:
  1. If Schedule Score $S_{\text{sched}} == 0$ (slip $> 60$ days): Status overridden to **Red** ("Schedule score is 0 (fatal delay)").
  2. If Issues Score $S_{\text{iss}} == 0$ (`issue_kpi == 'Red'`): Status overridden to **Red** ("Issue KPI is Red").
  3. If Cost Score $S_{\text{cost}} == 0$ (overrun $> 50\%$): Status overridden to **Red** ("Cost score is 0 (severe overrun)").
  4. If Cost Score $S_{\text{cost}} == 25$ (overrun between $25\%$ and $50\%$) and Base Status is Green: Status capped at **Amber** ("Cost score is 25 (significant overrun caps status at Amber)").
- **Worked Example**:
  - A project has perfect Schedule (100), Progress (100), and Issues (100), but Cost overrun is $60\%$ ($S_{\text{cost}} = 0$).
  - Raw Score: $(100 \times 0.35) + (0 \times 0.25) + (100 \times 0.25) + (100 \times 0.15) = 35 + 0 + 25 + 15 = 75.0$ (Base Green).
  - Severe Condition: $S_{\text{cost}} == 0$ fires.
  - Final Result: Status = **Red**; `health_override_applied = True`; `health_override_reason = "Cost score is 0 (severe overrun)"`.
- Status: **Implemented** (`backend/health.py:281-290`).

---

## 5. Schedule, Delivery & Aging Rules

### `RULE-007`: Overdue Go-Live (Past Due)
- **Plain-English Statement**: Identifies active projects whose planned go-live date has passed without actual deployment.
- **Logic**:
  - Current state must be exactly `'Active'`.
  - `planned_deployment_date` must be strictly before `asOf` ($\text{planned} < \text{asOf}$). A planned date equal to `asOf` is **not** overdue.
  - `actual_deployment_date` must be `NULL` or empty.
- **Exclusions**: Projects in `Project Created` and `On Hold` are not in delivery and are never flagged as overdue.
- **SQL Predicate**:
  ```sql
  current_state = 'Active' AND planned_deployment_date < CURDATE() AND actual_deployment_date IS NULL
  ```
- **Worked Example**:
  - `asOf` = `2026-09-30`. Project planned date = `2026-09-29`, actual = `NULL`, state = `Active`.
  - Result: Flagged Overdue Go-Live; `days_past_planned = 1`.
- Status: **Implemented** (`backend/metrics.py:26-30`, `backend/health.py:130-142`).

---

### `RULE-008`: Schedule Risk
- **Plain-English Statement**: Represents subjective early warning indicators where the Project Manager flags concern on the project schedule.
- **Logic**: Open project (`status = 'Active'`) where `schedule_kpi IN ('Red', 'Yellow')`.
- **Distinction**: Schedule Risk is distinct from Overdue Go-Live:
  - An undeployed project due in 3 months with high technical complexity may have `schedule_kpi = 'Red'` (Schedule Risk = True, Overdue Go-Live = False).
  - An unmanaged project past due whose PM neglected to update the KPI may have `schedule_kpi = 'Green'` (Schedule Risk = False, Overdue Go-Live = True).
- Status: **Implemented** (`backend/metrics.py:34`).

---

### `RULE-009`: RAG Contradiction Early Warning
- **Plain-English Statement**: Detects data quality anomalies where reported schedule color directly contradicts physical calendar dates.
- **Conditions**:
  1. Project is open (`status = 'Active'`) and undeployed (`actual_deployment_date IS NULL`).
  2. Condition A: `schedule_kpi = 'Green'` AND $\text{asOf} - \text{planned} \ge 15\text{ days}$ (reported Green, but over 2 weeks late).
  3. Condition B: `schedule_kpi = 'Red'` AND $\text{planned} - \text{asOf} \ge 30\text{ days}$ (reported Red, but planned date is over a month away).
- **SQL Predicate**:
  ```sql
  status = 'Active' AND actual_deployment_date IS NULL AND (
    (schedule_kpi = 'Green' AND DATEDIFF(CURDATE(), planned_deployment_date) >= 15) OR
    (schedule_kpi = 'Red' AND DATEDIFF(planned_deployment_date, CURDATE()) >= 30)
  )
  ```
- Status: **Implemented** (`backend/metrics.py:54-58`).

---

### `RULE-010`: Stale Projects
- **Plain-English Statement**: Identifies active in-delivery projects that have not had any administrative record updates for more than 30 days.
- **Logic**: `current_state = 'Active'` AND $\text{DATEDIFF}(\text{asOf}, \text{modified\_on}) > 30$.
- **Governance Note**: `modified_on` reflects any system change, not formal status report submission. Threshold of 30 days is provisional pending PMO charter sign-off.
- Status: **Implemented** (`backend/metrics.py:36-41`).

---

### `RULE-011`: Upcoming Delivery Horizons
- **Plain-English Statement**: Groups undeployed open projects into non-overlapping future go-live horizons based on `planned_deployment_date`.
- **Buckets**:
  - `0–30 Days`: $\text{asOf} \le \text{planned} \le \text{asOf} + 30\text{ days}$
  - `31–60 Days`: $\text{asOf} + 30\text{ days} < \text{planned} \le \text{asOf} + 60\text{ days}$
  - `61–90 Days`: $\text{asOf} + 60\text{ days} < \text{planned} \le \text{asOf} + 90\text{ days}$
- **Date Integrity**: Uses `planned_deployment_date`, never `finish_date` (which represents post-warranty administrative closure).
- Status: **Implemented** (`backend/metrics.py:89-106`).

---

## 6. Financial & Budget Rules

### `RULE-012`: Over Budget Row Condition
- **Plain-English Statement**: A project is over budget when its forecast total cost (EAC) exceeds its approved budget, provided an approved budget actually exists.
- **Formula**:
  $$\text{cost\_total\_usd} > \text{approved\_budget\_value\_usd} \quad \text{AND} \quad \text{approved\_budget\_value\_usd} > 0$$
- **Edge Cases**:
  - If `approved_budget_value_usd` is `NULL` or $0$, the project is **never** flagged as over budget (a non-existent budget cannot be mathematically exceeded).
  - If `cost_total_usd` is `NULL`, the project is not flagged.
- Status: **Implemented** (`backend/metrics.py:80`).

---

### `RULE-013`: Financial Exposure & Population Exclusion Rule
- **Plain-English Statement**: Portfolio financial exposure compares approved budget against forecast total cost strictly across projects that possess **both** a positive approved budget and a non-negative forecast.
- **Inputs**: Population of open projects (`metrics.ACTIVE`).
- **Calculation Formula**:
  - Comparable Set $K = \{p \in \text{Open} \mid \text{budget}(p) > 0 \land \text{cost}(p) \ge 0\}$
  - $\text{Total Approved} = \sum_{p \in K} \text{budget}(p)$
  - $\text{Total Forecast} = \sum_{p \in K} \text{cost}(p)$
  - $\text{Variance} = \text{Total Forecast} - \text{Total Approved}$
  - $\text{Variance Pct} = \begin{cases} \frac{\text{Variance}}{\text{Total Approved}} \times 100\% & \text{if Total Approved} > 0 \\ \text{null} & \text{otherwise} \end{cases}$
  - $\text{Excluded Projects Count} = |\text{Open}| - |K|$
- **Rationale**: A project with missing budget would otherwise add its entire forecast to portfolio variance, while a project with missing forecast would masquerade as an artificial cost savings. Excluded projects are explicitly displayed in `projectsExcluded`.
- Status: **Implemented** (`backend/metrics.py:133-160`).

---

## 7. Demand & Request Governance Rules

### `RULE-014`: Open Demand Requests
- **Plain-English Statement**: A project request is classified as open while awaiting decision or intake progression.
- **States**: `request_state IN ('New', 'Active', 'On Hold', 'Return to Requester')`.
- **Terminal States**: `Approved` (converted to delivery project), `Rejected`, `Dropped`.
- Status: **Implemented** (`backend/metrics.py:61`).

---

### `RULE-015`: Overdue Governance Action Gates on Requests
- **Plain-English Statement**: Identifies open requests whose governance review milestones have elapsed without documented approval or issuance.
- **Evaluated Gates**:
  1. **DOU (Document of Understanding)**:
     - Rule: `dou_required = 'Yes'` AND `dou_planned_date < asOf` AND `dou_issued_date IS NULL`.
  2. **DARB (Design Architecture Review Board)**:
     - Rule: `darb_exempted = 'No'` AND `darb_review_date < asOf` AND `darb_approval_date IS NULL`.
  3. **SOW (Statement of Work)**:
     - Rule: `sow_planned_date < asOf` AND `sow_issued_date IS NULL`.
- **Exclusions**: HLE (High-Level Estimate) and BRD (Business Requirements Document) have no planned due dates in the schema and cannot be mathematically overdue.
- Status: **Implemented** (`backend/metrics.py:67-74`, `backend/requests.py:270-310`).

---

## 8. Natural Language & Query Execution Gating Rules

### `RULE-016`: NL Intent Routing Gating
- **Plain-English Statement**: In Ask TELA (`POST /api/ask-tela`), incoming questions are evaluated against an exact catalogue of certified intents (`talk_to_data.INTENTS`).
- **Routing Decision**:
  - If exact match or canonical suggestion $\rightarrow$ Route to governed certified handler.
  - If no certified match $\rightarrow$ Route to exploratory LLM planner pipeline.
  - If certified handler fails $\rightarrow$ Return error payload with code; **never fall through to LLM SQL generation**.
- Status: **Implemented** (`backend/tela_service.py:120-170`).

---

### `RULE-017`: Exploratory SQL AST Validation Rules
- **Plain-English Statement**: All model-generated SQL must pass abstract syntax tree validation via `sqlglot` (MySQL dialect) before execution.
- **Deny-List Rules**:
  1. Exactly one SQL statement permitted (no semicolons or multi-statement chains).
  2. AST root must be `exp.Select` or `exp.Union`. DDL (`CREATE`, `DROP`, `ALTER`) and DML (`INSERT`, `UPDATE`, `DELETE`) are rejected.
  3. Disallow file system functions (`LOAD_FILE`), timing attack vectors (`SLEEP`, `BENCHMARK`), schema reflection (`DATABASE`, `USER`), and administrative commands.
  4. Disallow schema qualification (e.g. `mysql.user` or `information_schema.tables`).
  5. Disallow wildcard projections (`SELECT *`). Columns must be explicitly named from the allow-list.
  6. Maximum row limit `LIMIT 200` must be present or automatically injected.
- Status: **Implemented** (`backend/tela_validator.py:35-110`).

---

### `RULE-018`: Server-Side Mandatory Scope Injection
- **Plain-English Statement**: Every governed table reference (`projects`, `project_requests`) in exploratory SQL is rewritten into a filtered subquery applying the pinned CIO and active dashboard filters.
- **Transformation Template**:
  ```sql
  -- Original model query:
  SELECT platform, AVG(cost_total_usd) FROM projects GROUP BY platform

  -- Rewritten AST executed:
  SELECT platform, AVG(cost_total_usd)
  FROM (
    SELECT * FROM projects
    WHERE cio = 'Nomsa Osei'
      AND region IN ('East Africa')
  ) AS projects
  GROUP BY platform
  LIMIT 200
  ```
- **Independent Assertion**: After rewriting, the SQL tree is re-parsed and re-checked by `assert_scoped()` to ensure no unscoped table alias exists.
- Status: **Implemented** (`backend/tela_scope.py:35-85`).

---

### `RULE-019`: Read-Only Connection Verification
- **Plain-English Statement**: Exploratory queries are executed through `ReadOnlyExecutor`. Before running any query, the executor inspects `SHOW GRANTS` on the current connection.
- **Gating Policy**:
  - `SHOW GRANTS` must parse completely and contain strictly `USAGE ON *.*` and `SELECT` on approved tables (`<database>.projects`, `<database>.project_requests`).
  - Any administrative privilege, write grant, database-wide grant, or `WITH GRANT OPTION` causes the gate to fail-closed (`ExploratoryDisabled`).
  - Grants check is cached for 60 seconds. Grant failures drop cache immediately.
- Status: **Implemented** (`backend/tela_executor.py:90-180`).

---

## 9. Custom Reports & Widget Lifecycle Rules

### `RULE-020`: Opaque Token Memory Lifecycle
- **Plain-English Statement**: When a user asks an eligible question in Ask TELA that returns data, the server generates a cryptographically random token (`secrets.token_urlsafe(32)`).
- **Token Properties**:
  - Maps to the un-scoped SQL definition and metadata in server memory (`_pins` dict).
  - Time-To-Live (TTL): 3,600 seconds (1 hour).
  - Memory Bound: Maximum 500 active pin tokens in LRU cache.
  - Client Exposure: Browser receives only the token string; raw SQL is never exposed.
  - HTTP 410 Gone returned if token is expired or server restarted.
- Status: **Implemented** (`backend/custom_reports.py:53-56, 260-310`).

---

### `RULE-021`: Widget Re-Execution & Snapshot Fallback
- **Plain-English Statement**: Pinned widgets render live data by default on every dashboard load.
- **Execution Policy**:
  1. Retrieve stored un-scoped definition from SQLite.
  2. Certified widget: Re-execute registered handler under caller's current `asOf` and filters.
  3. Exploratory widget: Re-validate SQL, re-inject current server `DEMO_CIO`, execute via `ReadOnlyExecutor`.
  4. If execution succeeds: Return fresh data with `mode = 'live'`.
  5. If execution fails (e.g. exploratory gate disabled, timeout, DB connectivity error):
     - If `snapshot` exists in SQLite: Return cached snapshot with `mode = 'snapshot'`, `snapshot_at` timestamp, and failure message.
     - If no snapshot exists: Return HTTP 500 / error card.
- Status: **Implemented** (`backend/custom_reports.py:440-495`).
