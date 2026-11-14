# Business Rules and Calculation Catalog — Tela

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | L1 – Business only. Platform rules come from the Phase 1 scope (S01) and are otherwise Proposed. PMO pack rules are reference rules taken from secondary documents (S13) that describe another system's code; that code was not supplied, so none are Verified. |
| Sources analyzed | 31 files — see [project_summary.md](project_summary.md#source-inventory-and-coverage) |
| Related documents | [Project summary](project_summary.md) · [Architecture](architecture.md) · [Requirements](requirements.md) · [Function document](function_document.md) · [Technical document](technical_document.md) · [DB scheme](db_scheme.md) · [API catalog](api_catalog.md) · [Data dictionary](data_dictionary.md) · [Risk register](risk_register.md) |

## Table of contents

- [1. Conventions](#1-conventions)
- [2. Platform rules](#2-platform-rules) (RULE-001 to RULE-025)
- [3. PMO starter pack rules](#3-pmo-starter-pack-rules) (RULE-026 to RULE-039)
- [4. Decision tables](#4-decision-tables)
- [5. Conflicts between sources](#5-conflicts-between-sources)
- [6. Change log](#6-change-log)

---

## 1. Conventions

- **Status values:**
  - **Documented in scope**: stated in S01.
  - **Reference rule**: taken from a reference document describing another system (S13, S27, S02). Not verified, because no code was supplied.
  - **Proposed**: a design recommendation for Tela.
- Every rule is **Unknown** for implementation status, because no Tela code exists yet.
- Formulas are written in plain text. `asOf` means the resolved reporting date from [RULE-018](#rule-018-data-as-of-resolution).
- Worked examples are **Illustrative** unless a source is cited.
- Rules execute inside the query engine (COMP-004) and connector runner (COMP-005); see [architecture.md](architecture.md#component-architecture). Rules never run inside the LLM.

---

## 2. Platform rules

### RULE-001 Read-only connection verification

- **Statement:** A connection may execute queries only after Tela proves its identity cannot write. The check runs on Test, on first use after start-up, and every 60 s while the connection is in use. Any doubt disables execution for that connection (fail closed).
- **Inputs:** engine type; the source's privilege catalogue; a session option result.
- **Logic per engine (Proposed):**

  | Engine | Privilege check | Session backstop |
  |---|---|---|
  | PostgreSQL | `rolsuper = false`, and no INSERT, UPDATE, DELETE, TRUNCATE or CREATE on any schema or table reachable by the user (`has_table_privilege`, `has_schema_privilege`) | `SET default_transaction_read_only = on`, and every query in `BEGIN READ ONLY` |
  | MySQL | `SHOW GRANTS` parses fully and contains only `USAGE` and `SELECT` (plus `SHOW VIEW`); no `WITH GRANT OPTION` (S13 RULE-019) | `SET SESSION TRANSACTION READ ONLY` |
  | SQL Server | Not `sysadmin` or `db_owner`; `fn_my_permissions` shows no INSERT, UPDATE, DELETE, ALTER, CONTROL or EXECUTE on user objects | Transaction with `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`, statement-level validation (SQL Server has no session read-only flag) |
  | MongoDB | `connectionStatus` with `showPrivileges` lists only read actions (`find`, `listCollections`, `listIndexes`, `collStats`) | Only `aggregate` and `find` commands are issued; pipelines containing `$out` or `$merge` are never emitted |
  | Dataverse (TDS) | The application user's security role has Read privileges only (Inferred; verify in REC-006) | The TDS endpoint accepts read queries only (Inferred from Microsoft documentation; verify) |
  | Excel/CSV | Not applicable | DuckDB opened read-only over an immutable object version |

- **Worked example:** A MySQL test returns `GRANT SELECT, INSERT ON pmo.* TO 'tela'@'%'`. The parser finds INSERT, the verdict is `write_capable`, the connection status becomes `disabled_not_read_only`, and the Admin sees "This account can write. Provide a read-only account."
- **Boundary:** An unparseable grant line is treated as write-capable. A revoked privilege takes effect at the next 60-second check. An immediate re-check can be forced.
- **Configurable:** the check interval (minimum 15 s, maximum 300 s). The rule itself is fixed.
- **Source:** S01 §4.1, §6; S13 RULE-019 (reference); S26 REQ-014 (reference); S16 RISK-003 (cache window).
- **Status:** Documented in scope (read-only users); per-engine mechanics Proposed.
- **Related:** NFR-001, [technical_document.md](technical_document.md#connector-implementation).

### RULE-002 Single read-only statement validation

- **Statement:** Any SQL text that is executed, whether compiled from a spec or written as Custom SQL, must parse in the target dialect as exactly one statement. The root must be a SELECT, UNION or WITH…SELECT. The tree must contain no forbidden nodes.
- **Inputs:** SQL text; dialect (postgres, mysql, tsql, duckdb).
- **Logic:**
  1. `statements = parse(sql, dialect)`. If parsing fails, reject (`parse_error`).
  2. If the number of statements is not 1, reject (`multi_statement`, security event).
  3. If the root is not Select, Union or With-of-Select, reject (`not_select`, security event).
  4. If any node is in FORBIDDEN, reject (`forbidden_node`, security event). FORBIDDEN = Insert, Update, Delete, Merge, Create, Drop, Alter, Truncate, Grant, Revoke, Command, Transaction, Set, Use, Execute/Call, SELECT INTO, INTO OUTFILE, LOCK clauses (FOR UPDATE / FOR SHARE), plus the functions SLEEP, BENCHMARK, PG_SLEEP, LOAD_FILE, PG_READ_FILE, `xp_*`, OPENROWSET, OPENQUERY and DBLINK.
  5. Recursive CTEs, comments and schema-qualified system catalogues (`information_schema`, `pg_catalog`, `mysql.*`, `sys.*`) are rejected.
- **Worked example:** `SELECT COUNT(*) FROM projects; DROP TABLE projects` produces 2 statements. It is rejected as `multi_statement` and an audit security event is written. No connection is opened.
- **Boundary:** A trailing single semicolon is allowed and stripped. `SELECT *` is allowed only after the allow-list (RULE-003) expands it to approved, visible columns.
- **Configurable:** the FORBIDDEN list can be extended, never reduced.
- **Source:** S01 §4.7; S27 RULE-001 (reference); S13 RULE-017 (reference); S02 §2.
- **Status:** Documented in scope (single read-only SELECT); node list Proposed.

### RULE-003 Approved object allow-list

- **Statement:** A query may reference only entities and fields that are (a) in the approved model version, (b) allowed for the connection, and (c) visible to the caller's role (not hidden by a field policy).
- **Inputs:** referenced tables and columns; `model_versions` (DATA-015); `field_policies` (DATA-010).
- **Logic:** `referenced ⊆ approved ∩ connection_allowed ∩ visible(role)`. Otherwise reject (`not_allowed`, with the offending names in an Admin-only detail).
- **Worked example:** A CIO-role spec asks for `approved_budget_value_usd` while finance fields are hidden for CIOs. The rule rejects it, and the user sees "Approved budget is not available to your role."
- **Boundary:** Aliases and CTE names are resolved to their base objects before checking. Unknown identifiers are rejected and never guessed.
- **Source:** S01 §4.7, §6; S27 RULE-002 (reference); S11 §13.2 (41-column allow-list, reference).
- **Status:** Documented in scope.

### RULE-004 Mandatory scope injection

- **Statement:** Every reference to a scoped entity is replaced by a filtered derived table that applies the caller's row-scope predicates. After rewriting, an independent check confirms that no unscoped reference remains. Any failure refuses the query.
- **Inputs:** validated tree; scope policy (DATA-009) resolved for the user's role and claims.
- **Logic (SQL):** `FROM t AS a` becomes `FROM (SELECT <visible cols> FROM t WHERE <scope predicates>) AS a`. Then `assert_scoped(tree)`.
- **Logic (MongoDB):** the pipeline is prefixed with `{ $match: <scope filter> }`. Every `$lookup` gets a nested pipeline whose first stage is the foreign collection's scope `$match`.
- **Logic (Dataverse via the Web API fallback):** the scope is added as a mandatory `$filter` / FetchXML `<filter>` condition.
- **Worked example (from S13, reference):**
  - Model query: `SELECT platform, AVG(cost_total_usd) FROM projects GROUP BY platform`
  - Executed: `SELECT platform, AVG(cost_total_usd) FROM (SELECT ... FROM projects WHERE cio = 'Nomsa Osei') AS projects GROUP BY platform LIMIT 10000`
- **Null handling:** If a scope attribute is missing for the user (for example no CIO claim), refuse with "Your data scope could not be determined" (S27 RULE-004). Never run unscoped.
- **Admin exception:** Admin "all data" scope is an explicit policy (U4 in [requirements.md](requirements.md#ambiguous-contradictory-and-unmeasurable-requirements)) and is audited.
- **Source:** S01 §6; S13 RULE-018 (reference); S27 RULE-004 (reference).
- **Status:** Documented in scope (scope enforced in every query); mechanism Proposed (ADR-009).

### RULE-005 Filters narrow never widen

- **Statement:** The effective row set is the intersection of scope and all filters (page, shared, widget). A filter can never add rows outside scope.
- **Logic:** `effective = scope AND widget_filter AND (page_filter if follow_page_filters) AND shared_filter`. Filter values are passed only as bound parameters, never concatenated into the query.
- **Worked example:** Scope is region IN (KSA, GCC-UAE) and the user filters on region = East Africa. The effective set is empty, so the result is 0 rows with the note "Filter is outside your scope".
- **Boundary:** Filter dropdown options are themselves computed under scope, so out-of-scope values are not listed.
- **Source:** S01 §3 (Scope), §6; S12 REQ-004 (reference).
- **Status:** Documented in scope.

### RULE-006 Query row and time limits

- **Statement:** Every execution has a row cap and a timeout enforced at the source where possible, with a runner-side backstop.
- **Values:** Custom SQL is 10,000 rows and 20 s (S01). Widget and chat queries default to the same and can be configured lower per connection. Proposed payload cap: 10 MiB.
- **Logic:** If the query has no LIMIT, or a LIMIT above the cap, inject `LIMIT cap + 1`. If `cap + 1` rows return, truncate to `cap` and set `truncated = true`. Timeout mechanisms per engine: PostgreSQL `statement_timeout`; MySQL `MAX_EXECUTION_TIME`; SQL Server command timeout plus KILL on expiry; MongoDB `maxTimeMS`; Dataverse client timeout; DuckDB interrupt.
- **Worked example:** A query yields 25,000 rows. 10,001 are fetched, 10,000 are returned, and the UI shows "Showing first 10,000 rows (truncated)".
- **Boundary:** Exactly 10,000 rows is not truncated. Aggregates are capped on output rows, not on scanned rows.
- **Source:** S01 §4.7, §6. Conflicting values are listed in [§5](#5-conflicts-between-sources).
- **Status:** Documented in scope (Custom SQL); defaults for other queries Proposed.

### RULE-007 Page filter placeholder injection

- **Statement:** Custom SQL may contain one `:page_filters` placeholder in a WHERE or AND position. At run time it is replaced by the parameterised predicate of the active page and shared filters on fields the query exposes. With no active filters it becomes `1=1`.
- **Logic:** Parse the query, then locate the placeholder node, then substitute the predicate tree (bound parameters), then re-run RULE-002 to RULE-004.
- **Worked example:** `SELECT region, COUNT(*) FROM projects WHERE status='Active' AND :page_filters GROUP BY region` with page filter Priority = High becomes `... AND (priority = $1) ...` with $1 = 'High'.
- **Invalid input:** A placeholder used more than once, or inside a string literal, is rejected. A filter field missing from the query is reported in pre-publish checks as "filter does not reach widget".
- **Source:** S01 §4.7.
- **Status:** Documented in scope; parsing details Proposed.

### RULE-008 Visual suitability

- **Statement:** Only visuals that suit the result shape are offered. Unsuitable ones are shown disabled with the reason. A request for a misleading visual is refused and an alternative is offered.
- **Inputs:** column kinds (measure, categorical dimension, date dimension, status dimension); cardinality; additivity and sign of the measure.
- **Logic:** see the [visual suitability decision table](#41-visual-suitability).
- **Worked example:** A request for a "donut of monthly overdue go-lives" has a date breakdown, and a donut needs parts of a whole. It is refused: "Donut needs parts of a whole — showing columns by month instead" (S01 §4.6).
- **Boundary:** More than 7 slices disables donut. A negative or non-additive measure (averages, ratios) disables donut and stacked bars. Derived figures and shares label the measure they come from and are not series (US-176); several series on one axis need one unit. Window operation outputs (running total, moving avg, rank, percentile; US-177) are companions of the measure they operate on — they label the series rather than adding new ones. A running total naturally suits a trend line; rank/top N per group suits ranked bars or a table; percentile suits a table; buckets add a categorical dimension.
- **Configurable:** the slice limit (default 7) and the category limit for ranked bars (default 25, beyond which the visual switches to "top 25 + other").
- **Source:** S01 §4.4, §4.6, §4.11, §6; S27 RULE-006 (reference).
- **Status:** Documented in scope (principle and examples); thresholds Proposed.

### RULE-009 Certified-only publishing to roles

- **Statement:** A dashboard version can be published to a role only if every widget uses certified measures, dimensions and fields, and every Custom SQL widget is certified. Sharing with named people has no such requirement.
- **Logic:** `publishable = all(w.certification_state = 'certified' for w in widgets)`.
- **Worked example:** A dashboard with 12 widgets, one of which uses a personal "Core/Growth" grouping, is blocked: "1 widget uses an uncertified field — Request certification."
- **Source:** S01 §3, §4.7, §4.9.
- **Status:** Documented in scope.

### RULE-010 Certification queue entry

- **Statement:** A personal definition (grouping, band, join measure or Custom SQL widget) enters the Admin queue only when (a) the author requests certification, (b) it is shared with others, or (c) it is placed on a dashboard draft targeted at a role. Pure private use never enters the queue.
- **Logic:** see the [certification queue entry decision table](#44-certification-queue-entry).
- **Duplicate handling:** If a pending request already exists for the same definition hash, it is reused rather than duplicated.
- **Source:** S01 §4.8.
- **Status:** Documented in scope.

### RULE-011 Look-alike detection

- **Statement:** When a definition is reviewed, Tela flags certified fields that answer the "same question" but give a different number.
- **Logic (Proposed):**
  1. Candidates are certified measures with the same base entity and aggregation, or semantic similarity of name and description of at least 0.85 (pgvector cosine), or a shared synonym.
  2. Run the candidate and the item under the same scope and filters.
  3. Flag when the results differ by more than max(0.5%, 1 unit), and show the predicate diff (for example "item omits `current_state = 'Active'`").
- **Worked example (Illustrative):** A personal "Overdue" counts 7 and the certified "Overdue go-lives" counts 4. The diff shows the item includes On Hold projects. Suggested decision: Merge.
- **Source:** S01 §4.8.
- **Status:** Documented in scope (behaviour); thresholds Proposed.

### RULE-012 Grouping catch-all and not-set buckets

- **Statement:** A grouping maps listed values to named groups. Unlisted values, including new ones, go to the catch-all group (default name "Everything else"). NULL, empty and whitespace-only values go to "Not set".
- **Logic:** `CASE WHEN trim(v) IS NULL OR trim(v) = '' THEN 'Not set' WHEN v IN (...) THEN 'Core' WHEN v IN (...) THEN 'Growth' ELSE 'Everything else' END`.
- **Worked example:** Values A, B, C, D and NULL with Core = {A, B} and Growth = {C} give Core 2, Growth 1, Everything else 1 (D) and Not set 1.
- **Invalid input:** A value assigned to two groups is rejected at save time.
- **Configurable:** group names; whether matching is case-sensitive (default: case-insensitive, trimmed).
- **Source:** S01 §4.7.
- **Status:** Documented in scope.

### RULE-013 Band edge handling

- **Statement:** Bands are half-open ranges: the lower bound is inclusive and the upper bound exclusive. The last band is open-ended, or upper-inclusive if the author sets a maximum. The edge rule is shown in words.
- **Logic:** `band = first b where b.lower <= x < b.upper`. If x equals the closing maximum, it goes in the last band. NULL goes to "Not set". A value below the first lower bound goes to "Below <first lower>".
- **Worked example:** Bands 0–1M, 1M–5M and 5M+, with x = 1,000,000, give "1M–5M" (S01 §4.7: "exactly 1M goes into 1–5M").
- **Rounding:** Comparison uses the stored precision. Display labels use the field's format.
- **Invalid input:** Overlapping or gapped bands are rejected at save time with the exact conflicting edges named.
- **Source:** S01 §4.7.
- **Status:** Documented in scope (the example); generalisation Proposed.

### RULE-014 Join path and fan-out protection

- **Statement:** Joins use only links approved in the model. When a measure's entity sits on the "one" side of a one-to-many join, counts use DISTINCT on its key, and sums of its amounts are pre-aggregated before joining. Two independent one-to-many children are aggregated separately and then combined. Each widget reads one connection.
- **Logic:** the compiler builds the join graph from `model_links` (DATA-018), and:
  - one-to-many fan-out with COUNT becomes `COUNT(DISTINCT key)`;
  - SUM across fan-out becomes a CTE pre-aggregation (S27 RULE-007 to RULE-009 G1–G3, reference);
  - a cross-connection reference is rejected (`one_database_per_widget`).
- **Worked example (S27, reference):** Transaction AED 1,000 with 3 fee rows. A naive `SUM(amount)` gives 3,000. The compiled CTE form gives a gross of 1,000 and fees of 42.
- **Source:** S01 §4.7, §5; S27 RULE-007–009.
- **Status:** Documented in scope (approved links, distinct semantics); fan-out handling Proposed.

### RULE-015 Model version impact on widgets

- **Statement (Proposed, pending Q-007):** Each widget records the model version it was built on. When a new version is approved:
  - (a) widgets whose referenced fields and measure rules are unchanged move to the new version automatically;
  - (b) widgets whose measure rule changed are flagged "definition changed in vN" and keep the old version until the owner accepts the change;
  - (c) widgets referencing a removed field are marked broken.
  Published dashboards keep their pinned version until republished.
- **Worked example:** v2 changes "Overdue go-lives" to exclude a test region. A published dashboard on v1 keeps showing v1 and gets a "newer definition available" notice for the Admin.
- **Source:** S01 §8 Q7.
- **Status:** Proposed.

### RULE-016 Dashboard version numbering and restore

- **Statement:** Versions are integers starting at 1 per dashboard and incremented on each publish. Restoring version k creates version n+1 with k's content. Nothing is overwritten.
- **Worked example:** Published versions run v1 to v9. Restoring v7 creates v10 = copy of v7, and v9 stays in history.
- **Source:** S01 §4.9.
- **Status:** Documented in scope.

### RULE-017 Starter deletion effects

- **Statement:** When a starter report is deleted:
  - user copies stay and keep working;
  - each published dashboard that includes it gets a new draft without it, while the live version stays until republished;
  - the starter can be restored from the pack at any time.
- **Worked example:** A starter "Overdue go-lives" is in 2 published dashboards. Deleting it creates 2 drafts, each with a banner "Starter removed — republish to apply".
- **Source:** S01 §4.10.
- **Status:** Documented in scope.

### RULE-018 Data as-of resolution

- **Statement:** The as-of timestamp shown for a widget is the moment its result was read from the source:
  - **live** mode: query time;
  - **cached** mode: the time the cache entry was filled;
  - **Excel**: the file version upload time;
  - a source-declared freshness column, if configured, overrides these.
- **PMO reporting date:** Date-relative PMO rules use `asOf` = the pack's configured reporting date (S13 RULE-001 `PMO_AS_OF`, reference) or else the current date in the tenant time zone. A malformed configured date blocks pack queries with a configuration error.
- **Worked example:** A cache filled at 06:00 and viewed at 09:10 shows "Data as of 06:00" (S01 §8 Q1).
- **Source:** S01 §4.2, §4.3, §8 Q1; S13 RULE-001.
- **Status:** Documented in scope (display); mechanism Proposed (Q-001).

### RULE-019 LLM data egress minimisation

- **Statement:** By default, LLM prompts may contain the question, conversation summary, approved model metadata, glossary notes, gold examples and result column names and types. They may not contain row values, column samples, secrets or hidden fields.
- **Aggregate exception:** Ask-about-a-widget and narrative may send at most 50 aggregated result cells, with PII-tagged dimension labels removed (S26 REQ-008, reference). This is allowed only if the tenant's LLM policy enables it (Q-009).
- **Logic:** the gateway enforces a payload schema per agent; any field outside the schema is dropped and logged.
- **Worked example:** "Why is KSA highest?" sends {region: KSA, count: 4}, {region: GCC-UAE, count: 2} only if policy allows. Otherwise the answer is built from deterministic comparisons computed in code.
- **Source:** S01 §8 Q9; S02 §1; S26 REQ-007, REQ-008 (reference).
- **Status:** Proposed (pending Q-009).

### RULE-020 Result value masking

- **Statement:** Before results leave the runner, cells are masked by field classification:
  - secret-like columns (password, otp, token, secret, pin, cvv, apikey, credential, privatekey) become `[REDACTED]`;
  - emails become first character plus `***@domain`;
  - phone numbers keep the last 4 digits;
  - card-like numbers that pass a Luhn check become `[REDACTED_PAYMENT_DATA]`.
  Model field tags override name heuristics.
- **Worked example:** `nomsa.osei@example.com` is shown as `n***@example.com`, and `+971501234567` as `+*********4567`.
- **Source:** S02 §3 (reference).
- **Status:** Reference rule — Proposed for Tela.

### RULE-021 Pre-send leak scanning

- **Statement:** The LLM gateway scans every outgoing payload for API keys, connection strings, private keys, emails, phone numbers, card numbers and injection markers ("ignore previous instructions"). On a match the call is blocked, a security event is logged, and the feature falls back to a deterministic response.
- **Patterns (reference, S27 RULE-005):** `sk-[A-Za-z0-9]{20,}`, `sk-ant-[A-Za-z0-9_-]{20,}`, `AIza[0-9A-Za-z_-]{35}`, `postgres(ql)?://[^:]+:[^@]+@`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`, email, plus a Luhn card check.
- **Output side (S02 §1):** an LLM output containing `password=`, `api_key=` or "private key" is discarded.
- **Logging:** the gateway logs metadata only (tokens, latency, provider, verdict) and never prompt bodies.
- **Status:** Reference rule — Proposed.

### RULE-022 Grounded narrative

- **Statement:** The headline sentence and narrative are composed from values computed in code from the result (row count, total, max, min, top item, change versus the previous period). The narrator agent may only phrase pre-computed facts. Any number it outputs must match one of those facts, otherwise the sentence is replaced by a template sentence.
- **Worked example:** Facts {rows: 3, top: Network One 921,138; bottom: Network Flex 637,433}. Text: "3 platforms compared. Highest: Network One ($921,138); lowest: Network Flex ($637,433)." (S10 §5, reference)
- **Boundary:** An empty result gives "No records match the current scope and filters."
- **Source:** S01 §4.11; S12 REQ-026 (reference); S02 §1 (citation coverage ≥ 0.70 for document answers).
- **Status:** Documented in scope (principle); mechanism Proposed.

### RULE-023 Currency-safe aggregation

- **Statement:** A measure tagged as money with a currency field is never summed across currencies. The compiler adds the currency to the grouping, or filters to one currency, or (if an approved FX table exists in the model) converts.
- **Worked example (S27 RULE-010, reference):** 10,000 JOD, 50,000 AED and 2,000 USD give 3 rows, never "62,000".
- **Boundary:** A single-currency model (such as PMO USD fields) is unaffected.
- **Source:** S01 §4.1 (the model assistant asks "which currency"); S27 RULE-010.
- **Status:** Proposed.

### RULE-024 Pre-publish checks

- **Statement:** Publishing is blocked unless all checks pass for every chosen role. The current live version remains live while checks fail.
- **Logic:** see the [pre-publish checks table](#43-pre-publish-checks).
- **Source:** S01 §4.9.
- **Status:** Documented in scope.

### RULE-025 Drill authorisation and caps

- **Statement:** A drill-down runs a new scoped query: the mark's predicate AND scope AND filters, returning only visible fields. List results are paged (Proposed 100 per page, cap 1,000 rows; "open as a list" uses the RULE-006 cap). Record links use the connection's link template (Q-004).
- **Worked example:** A bar for KSA = 4 lists exactly those 4 projects, in scope, with links of the form `https://<org>.crm.dynamics.com/main.aspx?etn=tpg_project&id={project_id}` if configured (Illustrative).
- **Boundary:** If the drill count differs from the mark (for example because the cache is stale), a notice is shown and the drill result wins.
- **Source:** S01 §4.3, §8 Q4.
- **Status:** Documented in scope (drill); caps Proposed.

---

## 3. PMO starter pack rules

All rules in this section are **Reference rule (S13) — Proposed for PMO pack; needs STK-008 sign-off**.

They are written against the pack's logical fields. At install they bind either to Dataverse/TPG attributes (for example `tpgmea_planneddeploymentdate` maps to `planned_deployment_date`, per S15 §7) or to an equivalent extract. Missing fields leave the dependent measure uninstalled (REQ-024).

The S13 baseline counts (100 projects; 65 open; 13 in delivery; 3 on hold; 49 project created) are from a synthetic dataset and serve only as reconciliation fixtures.

### RULE-026 Open portfolio

- **Statement:** A project is open when its current state is Project Created, Active or On Hold. Closed projects are every other current state (Completed, Dropped, Cancelled…). Status (the record state, Active or Inactive) is not part of the rule. *DEC-24 (2026-11-07, US-202): the lifecycle definition, as the Dataverse model counts it. On TPG Dataverse every project is record-Active, completed or dropped (111 projects: 103 open, 7 Completed, 1 Dropped); on TPG MySQL 11 record-Inactive projects are in state Active (431 open by state, 420 record-Active). Pack 2.2.0; until 2.1.0 the rule was `status = 'Active'`.*
- **Example (S13):** 100 projects give 65 open and 35 inactive.
- **Null handling:** NULL status is excluded and counted in "status not set" for data quality.
- **Source:** S13 RULE-002. Fixed governed definition.

### RULE-027 In-delivery population

- **Statement:** A project is in delivery when `current_state = 'Active'`. Project Created and On Hold are open but not in delivery.
- **Example:** `status = Active` with `current_state = On Hold` is open = true and in delivery = false.
- **Source:** S13 RULE-003.

### RULE-028 Overdue go-live

- **Statement:** A project is overdue when `current_state = 'Active' AND planned_deployment_date < asOf AND actual_deployment_date IS NULL`.
- **Boundary:** A planned date equal to `asOf` is not overdue. On Hold and Project Created are never overdue.
- **Example (S13):** With asOf 2026-09-30, a project planned for 2026-09-29 and not deployed is overdue, with `days_past_planned = 1`.
- **Source:** S13 RULE-007.

### RULE-029 Schedule risk

- **Statement:** A project is at schedule risk when it is open and `schedule_kpi IN ('Red', 'Yellow')`. This is a subjective PM signal, distinct from overdue.
- **Example:** A project due in 3 months with schedule KPI Red is at risk = true and overdue = false.
- **Source:** S13 RULE-008.

### RULE-030 RAG contradiction

- **Statement:** A RAG contradiction is an open, undeployed project where either:
  - (A) `schedule_kpi = 'Green'` and `asOf - planned_deployment_date >= 15` days; or
  - (B) `schedule_kpi = 'Red'` and `planned_deployment_date - asOf >= 30` days.
- **Example:** Green, planned 2026-09-15, asOf 2026-09-30: 15 days, so it is flagged (S10 §5).
- **Boundary:** Exactly 15 or 30 days is flagged (≥). A NULL planned date is not evaluated.
- **Source:** S13 RULE-009.

### RULE-031 Stale project

- **Statement:** A project is stale when it is in delivery and `asOf - modified_on > 30` days.
- **Caveat:** `modified_on` changes on any edit, so this is not a status-report compliance measure. The 30-day threshold is provisional (S11 Q-002).
- **Configurable:** the threshold in days (default 30).
- **Source:** S13 RULE-010.

### RULE-032 Upcoming delivery horizons

- **Statement:** Undeployed open projects are bucketed by `planned_deployment_date`:
  - 0–30: `asOf <= planned <= asOf + 30`
  - 31–60: `asOf + 30 < planned <= asOf + 60`
  - 61–90: `asOf + 60 < planned <= asOf + 90`
  Use the planned deployment date, never `finish_date`.
- **Example:** asOf 2026-09-30 and planned 2026-10-30 (day 30) falls in 0–30. Planned 2026-10-31 falls in 31–60.
- **Source:** S13 RULE-011.

### RULE-033 Calculated health score

- **Statement:** For each open project, health = (sum of component score × weight over available components) / coverage. Weights: Schedule 0.35, Cost 0.25, Progress 0.25, Issues 0.15.
- **Schedule score:**
  - R = first baselined deployment date, or the planned date if not baselined.
  - Slip S, in days: if deployed, actual - R; if past due, asOf - min(planned, R); otherwise planned - R.
  - Bands: S ≤ 0 scores 100; 1–15 scores 75; 16–30 scores 50; 31–60 scores 25; above 60 scores 0.
- **Cost score:**
  - Valid only when budget > 0 and cost ≥ 0.
  - V = (cost - budget) / budget × 100.
  - Bands: V ≤ 0 scores 100; up to 10 scores 75; up to 25 scores 50; up to 50 scores 25; above 50 scores 0.
- **Progress score:**
  - Start = actual start, or else the created date. Total duration = planned - start, which must be above 0.
  - Expected percent E = clamp(elapsed / total × 100, 0, 100). Gap G = percent_complete - E.
  - Bands: G ≥ -5 scores 100; -15 ≤ G < -5 scores 75; -25 ≤ G < -15 scores 50; -50 ≤ G < -25 scores 25; G < -50 scores 0.
- **Issues score:** Green scores 100; Yellow or Amber scores 50; Red scores 0.
- **Classification:** Green ≥ 75; Amber 50–74.99; Red < 50.
- **Worked example (S13):**
  - Planned 2026-12-01, baseline 2026-11-01, asOf 2026-09-30, not due: S = 30, so schedule = 50.
  - Budget 100,000, cost 110,000: V = 10%, so cost = 75.
  - Start 2026-01-01: total 334 days, elapsed 272, E = 81.4, P = 50, G = -31.4, so progress = 25.
  - Issues Yellow, so issues = 50.
  - Score = 50×0.35 + 75×0.25 + 25×0.25 + 50×0.15 = 17.5 + 18.75 + 6.25 + 7.5 = **50.0 → Amber**.
- **Rounding:** The score is rounded to 1 decimal for display; classification uses the unrounded value. The percent_complete value must be in 0–100, otherwise the progress component is unavailable.
- **Source:** S13 RULE-004.

### RULE-034 Health evidence coverage

- **Statement:** Coverage C = sum of weights of the available components. If C < 0.60 the project is "Insufficient data" and no score is shown.
- **Combinations:**

  | Available components | C | Result |
  |---|---|---|
  | All four | 1.00 | Scored |
  | Schedule + Cost + Progress | 0.85 | Scored |
  | Schedule + Cost | 0.60 | Scored |
  | Schedule + Progress | 0.60 | Scored |
  | Schedule + Issues | 0.50 | Insufficient data |
  | Cost + Issues, or Progress + Issues | 0.40 | Insufficient data |
  | Any single component | ≤ 0.35 | Insufficient data |

- **Boundary:** C = 0.60 qualifies (≥).
- **Source:** S13 RULE-005.

### RULE-035 Health severe-condition overrides

- **Statement:** After the weighted score is computed, overrides apply in this order:
  1. schedule = 0 sets Red ("fatal delay");
  2. issues = 0 sets Red;
  3. cost = 0 sets Red ("severe overrun");
  4. cost = 25 with base Green caps the status at Amber.
  Overrides set `override_applied` and a reason.
- **Worked example (S13):** Schedule 100, cost 0 (60% overrun), progress 100, issues 100. Raw score = 35 + 0 + 25 + 15 = **75 (Green)**. The cost override makes it **Red**, with reason "Cost score is 0 (severe overrun)".
- **Source:** S13 RULE-006.

### RULE-036 Over-budget project

- **Statement:** A project is over budget when `cost_total_usd > approved_budget_value_usd AND approved_budget_value_usd > 0`. `cost_total_usd` is the forecast (EAC), **not actual spend**. Labels must say "Forecast cost (EAC)".
- **Boundary:** A NULL or 0 budget is never over budget. A NULL cost is not flagged.
- **Source:** S13 RULE-012; S12 REQ-013.

### RULE-037 Financial exposure

- **Statement:** Over open projects with budget > 0 and cost ≥ 0 (the comparable set K):
  - Total approved = sum of budget over K
  - Total forecast = sum of cost over K
  - Variance = forecast - approved
  - Variance % = variance / approved × 100 (null if approved = 0)
  - Projects excluded = open projects - |K|, shown alongside.
- **Example (S14 sample, reference):** Approved 16,500,000 and forecast 15,200,000 give variance -1,300,000 and -7.88%, with 15 compared and 1 excluded.
- **Rounding:** Percentages to 2 decimals; currency to whole units for display.
- **Source:** S13 RULE-013.

### RULE-038 Open demand request

- **Statement:** A request is open when `request_state IN ('New', 'Active', 'On Hold', 'Return to Requester')`. The terminal states are Approved, Rejected and Dropped.
- **Source:** S13 RULE-014.

### RULE-039 Overdue governance gates

- **Statement:** For open requests:
  - DOU is overdue when `dou_required = 'Yes' AND dou_planned_date < asOf AND dou_issued_date IS NULL`;
  - DARB is overdue when `darb_exempted = 'No' AND darb_review_date < asOf AND darb_approval_date IS NULL`;
  - SOW is overdue when `sow_planned_date < asOf AND sow_issued_date IS NULL`.
  HLE and BRD have no due date and cannot be overdue.
- **Example:** DOU required, planned 2026-09-20, not issued, asOf 2026-09-30: overdue by 10 days.
- **Boundary:** A planned date equal to asOf is not overdue.
- **Source:** S13 RULE-015.

---

## 4. Decision tables

### 4.1 Visual suitability

Applies to [RULE-008](#rule-008-visual-suitability). Rows are evaluated top to bottom; "table" is always allowed.

| Visual | Required result shape | Disabled reason shown when the shape does not fit |
|---|---|---|
| Figure (KPI) | Exactly 1 row × 1 measure | "Figure needs a single value" |
| Ranked bars | 1 categorical dimension + 1 measure, ≤ 25 categories (more becomes top 25 + Other); or, when asked for, one row of 2–6 measures in one unit and no breakdown, one bar per measure (EXEC-173) | "Ranked bars need one category and one measure, or one row of 2 to 6 measures in one unit" |
| Columns | 1 date dimension (bucketed by day, week, month or quarter) or an ordered category + 1 measure | "Columns need a date or ordered breakdown" |
| Trend / line | 1 date dimension + 1–6 measures in one unit, ≥ 3 time points | "Trend needs a date breakdown, and one unit for several lines" |
| Paired bars | 1 categorical dimension + exactly 2 comparable measures (same unit) | "Paired bars need two measures with the same unit" |
| Stacked status bars | 1 categorical dimension + 1 status dimension (≤ 6 states) + an additive count | "Stacked status bars need a status breakdown" |
| Donut | Parts of a whole: 1 categorical dimension, ≤ 7 slices, additive, non-negative measure, no date breakdown | "Donut needs parts of a whole (≤ 7 slices, additive values)" |
| Heat grid (US-157) | 2 categorical dimensions + 1 measure, ≤ 25 rows × ≤ 12 columns | "Heat grid needs two breakdowns and one measure (≤ 25 rows, ≤ 12 columns)" |
| Meter (US-157) | 1 row × 2 counts, the first within the second | "Meter needs a part and its whole: two counts, the first within the second" |
| Dumbbell (US-187) | 1 categorical dimension + exactly 2 measures in one unit, ≤ 25 items | "Dumbbell needs one category and two measures with the same unit (≤ 25 items)" |
| Waterfall / bridge (US-187) | 1 categorical dimension, 2 – 12 items, additive; 1 measure builds up to its total, 2 measures in one unit bridge from the first total to the second (each step = second − first) | "Waterfall needs one category and additive values (≤ 12 steps; two measures make a bridge)" |
| Bullet (US-187) | 1 measure with a target (the widget's tone, or the pack's threshold in Ask), 1 row or 1 category with ≤ 25 items, no date | "Bullet needs one measure with a target (≤ 25 items)" |
| Pareto (US-187) | 1 unordered categorical dimension, 3 – 25 items, 1 additive, non-negative measure | "Pareto needs one category and additive, non-negative values (3 to 25 items)" |
| Control chart (XmR, US-187) | 1 date dimension, ≥ 8 periods, 1 measure, no category | "Control chart needs a date breakdown with 8 or more periods and one measure" |
| Gantt (US-187) | 1 categorical dimension + 2 date dimensions (start, end), ≤ 40 rows | "Gantt needs one category with a start and an end date (≤ 40 rows)" |
| Funnel (US-187) | 3 – 8 stages that never grow: 1 row of counts in one unit, or 1 ordered category × 1 measure; non-negative | "Funnel needs 3 to 8 stages in order whose counts only fall" |
| Small multiples (US-187) | 1 date + 1 categorical dimension with 2 – 12 items + 1 measure | "Small multiples need a date and one category with 2 to 12 items, and one measure" |
| Calendar heatmap (US-188) | 1 day breakdown (days, not months or weeks) spanning 3 – 24 months + 1 measure, no category | "Calendar heatmap needs a day breakdown over 3 to 24 months and one measure" |
| Sankey (US-188) | 2 – 4 categorical dimensions (≤ 12 values each, ≤ 100 rows) + 1 additive, non-negative measure, no date | "Sankey needs 2 to 4 breakdowns (≤ 12 values each) and additive, non-negative values" |
| UpSet (US-188) | 2 – 6 yes/no dimensions and nothing else + 1 additive, non-negative count | "UpSet needs 2 to 6 yes/no breakdowns and a count" |
| Beeswarm (US-188) | 1 item breakdown (one row per value) + optionally 1 group (≤ 8 values) + 1 measure, 5 – 500 rows, no date | "Beeswarm needs one row per item (5 to 500) and one measure, in at most 8 groups" |
| Ridgeline / raincloud (US-188) | 1 item breakdown + 1 group with 3 – 8 values, ≥ 5 items per group on average, + 1 measure, ≤ 500 rows | "Ridgeline needs one row per item and a group with 3 to 8 values, 5 or more items each" |
| Time to event (US-188) | 1 item breakdown + 2 date dimensions (start, end; a blank end means still open) + optionally 1 group (≤ 6 values), 10 – 2,000 rows | "Time-to-event needs one row per item with a start and an end date (10 or more), in at most 6 groups" |
| Bump chart (US-188) | 1 date with ≥ 3 periods + 1 categorical dimension with 2 – 10 items + 1 measure | "Bump chart needs a date with 3 or more periods and one category with 2 to 10 items" |
| Treemap (US-188) | 1 – 2 categorical dimensions + 1 additive, non-negative measure, 2 – 200 rows, no date | "Treemap needs one or two breakdowns and additive, non-negative values (≤ 200 rows)" |
| Waffle (US-188) | Parts of a whole (1 category with 2 – 6 values, additive, non-negative) or a part and its whole (the meter's shape) | "Waffle needs parts of a whole (≤ 6 parts, additive values) or a part and its whole" |
| Table | Any shape | Never disabled |

Refusal substitutions: a donut on a date breakdown becomes columns; a donut with more than 7 slices becomes ranked bars; a trend with fewer than 3 points becomes columns; a figure over many rows becomes a table.

**Defaults (US-187).** The wave 1 visuals are offered beside the existing defaults and become the default only where nothing else fitted: a date × category result (before: a table) is shown as small multiples, and an item with a start and an end date as a Gantt. Everything a visual needs beyond the rows — waterfall steps, Pareto cumulative shares and the 80 % point, XmR mean and limits (mean ± 2.66 × average moving range, the lower limit not below zero for a measure that never is), bullet bands, funnel shares and drop-offs, the shared panel axis — is worked out by the API, never in the browser (COMP-001). Each visual keeps the table view; the Excel sheet adds those figures as a small table below the data.

**Defaults and figures (US-188).** Two new defaults, again only where a table was shown before: a result of yes/no breakdowns only is an UpSet, and three or four breakdowns with an additive count are a sankey. Everything else keeps its default and offers the wave 2 visual beside it (a daily trend offers the calendar, small multiples offer the bump chart, a Gantt offers time to event, ranked bars offer treemap, waffle and beeswarm, the meter offers the waffle). An "item" breakdown is one with one row per value (a project). The API works out: the calendar's four colour steps (their edges are the quartiles of the days with a value) and the monthly totals; sankey nodes (a value on two levels is two nodes) and links summed between neighbouring levels; UpSet set sizes and each combination's count, "none of these" kept so the bars add up; beeswarm and ridgeline quartiles, whiskers (1.5 × IQR) and the ridge's Gaussian density (Silverman's bandwidth, peak scaled to 1); the Kaplan–Meier estimate (an item with no end date is still open and counts until today, then leaves the risk set; items with no start, or ending before they start, are left out and counted; the median is the first day the share reaches 50 % or less); bump ranks (1 = highest, ties share the better rank); treemap shares; waffle cells out of 100 by largest remainder. The beeswarm's dot layout is drawn in the browser (presentation; no value moves).

**Direct labels and highlight-and-grey (US-188).** Several lines on a trend are named at their ends instead of in a legend, labels that would collide move apart (ECharts `labelLayout` `moveOverlap`; the D3 visuals do the same); a highlighted bar or point carries its value. When an answer names items (US-186), the named items keep their colour and the others turn grey on every visual that has items: bars, Pareto, waterfall, funnel, sankey, treemap, dumbbell, beeswarm, ridgeline, UpSet, time to event, bump and waffle.

**Paired measures and percentages (US-176).** A derived figure (ratio, difference, % of, % over) or a share of the total is a *companion* of the measure it is worked out from: it labels that measure's bars, line points or donut slices and is listed in the table, but it is never a series of its own. The rows above count series only:

| Result | Offered | Not offered, and why |
|---|---|---|
| 2 measures in one unit + their % of / % over, by 1 category | Paired bars, the percentage as the first series' label; table | Ranked bars (two series); donut |
| 1 additive measure + its share of the total, by 1 category | Ranked bars or donut, labelled with the share; table | — |
| 1 measure + its share, by a date | Columns or trend, labelled with the share | Donut (date breakdown) |
| 2–6 measures in different units (e.g. cost and a count) by a date or category | Table | Trend and paired bars: series share one axis, so they need one unit |
| Share of a non-additive measure (an average, a percentage) | — | Refused at validation: it has no share of a total |
| A difference across units, or a % of a measure in another unit | — | Refused at validation: "… are in different units" |

A zero or blank divisor gives a blank value, shown as "–", never an error.

### 4.2 Query gate

Evaluated in the connector runner for every execution: chat, widget, drill, filter options, export and Custom SQL.

| Read-only verified | Statements | Root and nodes clean | Objects allowed and visible | Scope resolvable | Rewrite verified | Outcome |
|---|---|---|---|---|---|---|
| No | – | – | – | – | – | Refuse: connection disabled (RULE-001) |
| Yes | ≠ 1 | – | – | – | – | Refuse + security event (RULE-002) |
| Yes | 1 | No | – | – | – | Refuse + security event |
| Yes | 1 | Yes | No | – | – | Refuse: not available to your role (RULE-003) |
| Yes | 1 | Yes | Yes | No | – | Refuse: scope undetermined (RULE-004) |
| Yes | 1 | Yes | Yes | Yes | No | Refuse + security event (fail closed) |
| Yes | 1 | Yes | Yes | Yes | Yes | Inject limits (RULE-006), mask (RULE-020), execute |

### 4.3 Pre-publish checks

Applies to [RULE-024](#rule-024-pre-publish-checks).

| # | Check | Pass condition | Inline fix offered |
|---|---|---|---|
| 1 | Certification | Every widget certified (RULE-009) | Request certification · Remove widget |
| 2 | Data per role | Every widget returns ≥ 1 row, or a deliberate empty state, for every target role (preview run) | Remove widget · Change role · Mark "empty is expected" |
| 3 | Field visibility | No widget depends on a field hidden for a target role, unless it is set to hide for that role | Hide for role · Remove |
| 4 | Filter reach | Every shared filter reaches every widget, or the widget is explicitly excluded | Map filter · Exclude widget |
| 5 | Model version | All widgets on current or compatible model versions (RULE-015) | Upgrade widget |
| 6 | Permission | The publisher holds `dashboard.publish` (Q-006) | Request publish |

All 6 must pass for all roles. Otherwise Publish is disabled and the live version is unchanged.

### 4.4 Certification queue entry

Applies to [RULE-010](#rule-010-certification-queue-entry).

| Explicit request | Shared with people | On a role-targeted draft | Enters queue? |
|---|---|---|---|
| Yes | any | any | Yes |
| No | Yes | any | Yes (marked "shared") |
| No | No | Yes | Yes (marked "publish-bound") |
| No | No | No | No |

---

## 5. Conflicts between sources

These are recorded side by side and left for a stakeholder decision.

| Topic | S01 scope | S12–S13 PMO reference | S27 Tela reference | S31 / S02 TelaKnowledge | Current Tela default | Decision owner |
|---|---|---|---|---|---|---|
| Row cap | 10,000 (Custom SQL) | 200 | 10,000 | 5,000 | 10,000 (configurable lower) | STK-001 (C3) |
| Timeout | 20 s (Custom SQL) | 15 s | 15 s | 30 s | 20 s | STK-001 (C3) |
| Payload cap | – | – | – | 1 MiB | 10 MiB (Proposed) | STK-007 |
| Who publishes to roles | Admin (prototype J9) | n/a (no publishing) | n/a | maker-checker (S31) | `dashboard.publish` permission, Admin by default | STK-008 (C7, Q-006) |
| Result persistence | Widget stores question, not numbers | Snapshot fallback persisted | Cached rows in widget | – | Short-TTL cache only (ADR-010) | Architect (C6) |
| Grant-check cache | – | 60 s | – | schema checksum per run | 60 s plus forced re-check (RULE-001) | Security |

## 6. Change log

| Date | Version | Summary |
|---|---|---|
| 2026-10-21 | 0.1 | Initial catalog: 25 platform rules, 14 PMO pack rules, 4 decision tables, conflicts table |
