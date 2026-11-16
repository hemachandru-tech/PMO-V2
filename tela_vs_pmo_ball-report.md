# Tela vs PMO Dashboard on `ball-report`: comparison report

Date of test: 14 November 2026. Database: local MySQL 9.7.1, schema `ball-report`, read-only queries only.
Method: no application code changed and no `ball-report` data changed. With the user's approval:
- a read-only MySQL login `pmo_ro` was created (SELECT and SHOW VIEW on `ball-report` only);
- Tela's `stack.env` was pointed at `ball-report` for the run, then restored byte for byte from a checksummed backup;
- PMO Dashboard was run in Docker (Colima) from a copy of the zip, with its own generated dev secrets.

Both apps used the **same Gemini key and the same model, `gemini-3.5-flash-lite`**. See Appendix A.

---

## 1. Executive summary

- **Measured result: Tela answered far more questions correctly than PMO Dashboard.**
  - Tela: **85.8% partial credit, 83.3% strict** (fully correct).
  - PMO Dashboard: **49.2% partial, 43.3% strict**.
  - The gap grows with difficulty. On the most-hard questions Tela scored 80% and PMO Dashboard 20%.
- **Why PMO Dashboard lost:**
  - **Its own SQL writer can't reliably quote the hyphenated database name.** It often wrote `` `ball-report.table` `` as one name, so its scope checker couldn't recognise the table and refused the query. That cost about 7 questions, and its single repair round never fixed it.
  - It also chose the wrong table more often, for example `deliveries` instead of `sideangle_delivery`.
  - It hit Google rate limits (11 × 429, 9 × 503). Those 8 questions were retried once, slowly, and still mostly failed on quoting.
  - Its governance did its job: no unscoped query ever ran. But on this data the strict checks cost answers.
- **Why Tela won:** it sends a compact schema to the model and runs on the bare table names of the connected database, so the hyphen never appears in its SQL. Its validator passed every query it wrote, it got no provider errors, and answers took 2–30 s.
- **Tela's misses:**
  - wrong column for the user type (`role` instead of `user_type`, also a PMO Dashboard miss);
  - a wrong self-join for coach load;
  - splitting students by name;
  - filtering `status_code` instead of `stage`;
  - one clarification request instead of an answer.
- **Earlier the same day Tela couldn't answer anything,** because its previous Gemini key's Google project was suspended. It has no non-model fallback and showed "try rephrasing". With the new key it works.
- **`ball-report` is not a PMO database.** It holds cricket bowling-analysis data: deliveries, ball-speed and biomechanics metrics, a video-processing pipeline, players and coaches, and an API access log. It has no projects, budgets, schedules, risks or resources.
  - These 30 questions measure **generic question answering on an unfamiliar schema**, not PMO KPI accuracy.
  - All PMO-module comparisons (KPI, project, financial, schedule, risk) are judged from code.
- **Which is the better enterprise foundation?** Still PMO Dashboard, on architecture: governed semantic layer, OIDC/SCIM, fail-closed row scope, hash-chained audit, signed query envelopes and a CI pipeline. Readiness average: **6.0/10 vs Tela 5.6/10**.
  - Today, though, **Tela gives users more correct answers.**
  - PMO Dashboard took about an hour of setup to answer anything: Docker, 13+ containers, a read-only login, model approval with 21 personal-data questions, scope policies and an Ask-mode change.
  - It showed several defects during setup and testing (section 9).
- **Tela** is the more finished PMO *product* on TPG data: 225 KPI cards, 30 dashboards, and it runs from one script without Docker. Its architecture caps it at a team tool, not an enterprise platform:
  - JSON-file storage and a single worker;
  - one question at a time across all users;
  - dashboards hard-coded to TPG table names and values;
  - MySQL TLS disabled in code;
  - local accounts only.
- **Blunt bottom line:** neither is enterprise-ready. Build on PMO Dashboard's architecture, but only after:
  - a working, measured deployment;
  - fixing its pack bugs;
  - adding data history;
  - proving it under load.

  Keep Tela's PMO content (KPI definitions, golden figures) as the reference to port and test against. Do not scale Tela's backend.

### Scores (30 questions, same wording, same date context)

| Level | Tela partial % | Tela strict % | PMO Dashboard partial % | PMO Dashboard strict % |
|---|---|---|---|---|
| Easy | 90.0 | 90.0 | 77.5 | 70.0 |
| Hard | 87.5 | 80.0 | 50.0 | 50.0 |
| Most-hard | 80.0 | 80.0 | 20.0 | 10.0 |
| Overall | 85.8 | 83.3 | 49.2 | 43.3 |

---

## 2. Database analysis: `ball-report`

### 2.1 Objects
- **14 base tables, 0 views, 0 stored procedures, 0 functions, 0 triggers, 0 declared foreign keys.** All tables are InnoDB, utf8mb4.
- **Server:** MySQL 9.7.1, time zone SYSTEM = IST.
- **Last write:** 2026-11-13 16:35 IST (`user_activity`). The database is effectively static.
- `information_schema.TABLES.TABLE_ROWS` values are InnoDB estimates and differ from real counts (`callbacks` estimate 972 vs 1,019 actual). Every figure below uses `COUNT(*)`.

| Table | Rows | Keys (PK/unique) | Role |
|---|---|---|---|
| admin_activity_log | 23 | PRIMARY(id) | Admin audit: logins, user create/deactivate, permission changes (JSON before/after) |
| callbacks | 1019 | PRIMARY(id) | Webhook/pipeline stage events per delivery (P=progress stages, C=done); 18 rows with NULL delivery_id |
| deliveries | 65 | PRIMARY(id); uq_delivery_run(clip_id,model_key) | Core fact: one processing run of a clip by a model; status C/done/error |
| delivery_metrics | 1860 | PRIMARY(id); uq_metric_per_run(delivery_id,metric_key) | EAV metrics per delivery (44 keys, tiers measured/derived/experimental) |
| delivery_players | 10 | PRIMARY(id); uq_delivery_player(delivery_id,player_id) | Players attached to a delivery |
| delivery_report | 65 | PRIMARY(id); uq_report_run(clip_id,model_key) | Denormalised 1:1 report per delivery (same id via delivery_id) with headline metrics |
| session_master_demo | 2 | PRIMARY(id); uq_session_master(session_id) | Coaching sessions (demo) |
| session_players | 5 | PRIMARY(id); uq_session_player(session_id,player_id) | Player-in-session with a delivery_count counter |
| sideangle_delivery | 54 | PRIMARY(id); uq_side_clip(clip_id) | Side-camera run-up/biomechanics analysis; loose link to deliveries (only 4 rows have delivery_id) |
| ui_demo_delivery | 65 | PRIMARY(id); uq_demo_delivery(clip_id,model_key) | UI copy of deliveries (all 65 match on clip_id+model_key) |
| user_activity | 49482 | PRIMARY(id) | HTTP access log of the analysis API (2026-08-26 .. 2026-11-13) |
| user_device_demo | 4 | PRIMARY(id); uq_user_device(user_id,device_id) | Devices per user |
| user_master_demo | 17 | PRIMARY(id); uq_demo_user(user_id,user_type) | Coaches and students (contains password_hash and email: PII) |
| user_permission | 3 | PRIMARY(id); uq_user_perm(user_id,user_type,perm_key) | Per-user permission flags |

### 2.2 Relationships (none declared; verified from the data)
| Child | Parent | Join | Verified |
|---|---|---|---|
| delivery_metrics | deliveries | delivery_id = id | 0 orphans of 1,860 |
| delivery_report | deliveries | delivery_id = id (also clip_id + model_key) | 65/65 match: strict 1:1 |
| ui_demo_delivery | deliveries | clip_id + model_key | 65/65 match: a UI copy |
| delivery_players | deliveries | delivery_id = id | 0 orphans |
| callbacks | deliveries | delivery_id = id | 18 of 1,019 rows have NULL delivery_id; 64 of 65 deliveries have callbacks |
| sideangle_delivery | deliveries | delivery_id (varchar) | only 4 of 54 rows set: effectively independent |
| deliveries / sideangle / user_activity | user_master_demo | player_id / student_id / coach_id = user_id | soft text keys, many placeholder ids (`default`, `run`, `bad`, `portrait`) |
| session_players / deliveries | session_master_demo | session_id | only 2 master sessions; deliveries use 6 session ids incl. `web` (56 rows) |

### 2.3 Business entities and rules the data actually follows (from the records)
1. **A delivery is one run of a clip through a model.** `(clip_id, model_key)` is unique. Four models are in use: `best_v4` 45, `best_v3_old` 10, `best_v2_WB` 10, and `best_v4`/SKA app 3 (counted in the 45).
2. **Completion has two codes.** `status='C'` (51) and `status='done'` (13) both mean success; `error` (1). Any "completed" KPI that checks only one code is wrong.
3. **The pipeline stages are fixed:** `queued → pose → detect → filter → keyframes → biomech → metrics → annotate → done`, as callbacks with `status_code` P (progress) and C (complete). Only 5 of 1,019 callbacks recorded an HTTP 200 back to a client; the rest have `http_status` NULL, so callback delivery is not tracked.
4. **Metrics are stored as entity-attribute-value rows** in `delivery_metrics`: 44 keys in three tiers (measured, derived, experimental), and `(delivery_id, metric_key)` is unique. Delivery 41 (the error run) has no metrics.
5. **The headline speed field contains impossible values.** `speed_kph` reaches 9,991.8 kph; `length_m` reaches 610 m; `line_cm_from_middle` reaches 10,657 cm. There is no validation at write time, so any average is meaningless without outlier rules. Within 40–200 kph, the median is 80.3 kph over 42 values.
6. **The denormalised copies disagree with the facts.**
   - `delivery_report.speed_kph` differs from its own `delivery_metrics` value on 5 of the 44 deliveries where both exist.
   - Stored student counters don't match the deliveries table: `user_master_demo.delivery_count` is 13 for student 10001 against 7 actual, and 51 for `default` against 56 actual.
7. **Test and placeholder identities dominate.** 56 of 65 reports belong to student `default`; side-angle deliveries use ids like `run`, `bad`, `portrait` and `excel`. Real coaching sessions are almost absent: `session_date` is NULL on 61 of 65 deliveries.
8. **`user_activity` is an API access log.** It has 49,482 rows, 72% of them `api_job_status` polling. October 2026 has 44,900 requests and a 13.4% 4xx/5xx rate, mostly 404 and 401.
9. **Sensitive data:** `user_master_demo` holds `password_hash` and `email`. The root login used here (password supplied in chat) can read it.

---

## 3. Architecture: Tela (`/Users/prodian/Desktop/ni-pmo-stack`)

- **Processes:** Tela backend (FastAPI, 127.0.0.1:28901, plain HTTP); Tela screens (`telaui.py`, proxy plus prebuilt React SPA, :28902); NI PMO dashboards (`dashboards/app/pmo_server.py`, FastAPI plus SQLite, :28903). They are started by `stack.sh:59-102`. `bootstrap.py` approves all tables and turns custom SQL on.
- **Storage:** Tela keeps users, sessions, grants, connections (with the DB password) and semantic layers in flat JSON files under `tela/backend/data/api/` (`app.py:87-88`, `stores.py:65-82`). Because of that it refuses to run more than one worker (`app.py:111-113`).
- **Database access:** PyMySQL, a new connection per query, `SET SESSION TRANSACTION READ ONLY`, `max_execution_time`, and **`ssl_disabled: True` hard-coded** (`executors.py:56-127`, `:72`).
- **Choosing the database:** `stack.sh` writes `MYSQL_LOCAL_*` into `tela/backend/.env`, but **nothing reads those variables** (`app.py:141`). The active connection comes from `connection.json`, and `bootstrap.py:27-33` activates `DB_*` only when no connection is active. During the test, editing `DB_NAME` had no effect until `connection.json` was moved aside.
- **Question → SQL → answer:**
  1. `pmo_server.py:624` (saved-answer cache keyed by an FNV hash of the wording, with **no database id**: wrong answers survive a database switch) →
  2. `POST /api/ask` (`ask.py:28`) →
  3. `turns.execute_turn` (global semaphore `MAX_CONCURRENT_TURNS`, default 1) →
  4. `pipeline.run_pipeline` (`pipeline.py:144-545`): retrieval, access filtering, planner (LLM, up to 2 calls) ×3 attempts, sqlglot validation with allow-lists, AST row filters, execute, mask, self-check →
  5. `planner.py:188-350` builds the prompt from `prompts/planner_v1.txt`.

  **There is no non-LLM path**: when the provider fails, the user sees "I couldn't produce a valid query… Please try rephrasing it." (`planner.py:343`).
- **The configured model is ignored:** `LLM_MODEL=gemini-3.1-flash-lite` is not in the registry, and `gemini-3.5-flash-lite` is used silently (`llm_config.py:132-137`, `run/tela.log`).
- **KPI logic:** the browser computes everything in ES5 JavaScript (`calc.js`, `kinds.js`, `dash_*.js`) from a whole-scope extract. `PMO_AS_OF` moves "today" but doesn't time-slice the data.
- **Auth:**
  - Tela: bcrypt local accounts, 168 h sessions, cookie without Secure, no login rate limit (`routers/auth.py:24-43`).
  - Dashboards: Strict, Secure cookies, an `x-pmo` CSRF header, a login lockout.
  - Grants: deny-by-default with AST row filters, but viewers can call Tela directly and read child tables unscoped (`pmo_access.py:8-9`). `/ask/{id}/result` has no ownership check (`ask.py:112-148`).
- **Errors:** friendly mapping in the API, but the MySQL adapter swallows errors and returns `[]` (`mysql_adapter.py:428-433`), and credential failures look like user error.

Full evidence: `work/tela_analysis.md` (447 lines, file:line throughout).

## 4. Architecture: PMO Dashboard (`pmo_bashboard-master.zip`)

- **Services** (`deploy/compose/compose.yaml`):
  - nginx edge;
  - React 18 SPA (OIDC);
  - FastAPI modular monolith API;
  - connector runner (the only component that touches sources);
  - LLM gateway (the only path to providers);
  - Celery worker and beat on Redis;
  - PostgreSQL 16 + pgvector metadata DB with tenant RLS;
  - Keycloak, OpenBao, SeaweedFS;
  - optional OTel, Prometheus, Grafana.
- **Question → Query Spec → SQL → answer:**
  1. `POST /ask/turns` (`ask/router.py:123`, rate-limited in process) →
  2. `Orchestrator._run` (`ask/orchestrator.py:376-532`): write-intent refusal, then only approved-model sources →
  3. **rules planner first** (`ask/planner.py`), LLM only for unmatched cases (`orchestrator.py:932-984`) →
  4. `QueryService.prepare/execute` (`query/service.py`): pinned approved snapshot, spec validator, sqlglot compile with bound parameters →
  5. `scope_injector.inject` wraps every table in a scoped derived table, and `assert_scoped` re-checks the final SQL →
  6. an Ed25519-signed envelope goes to the runner →
  7. the runner re-checks the signature and statement hash, re-validates the SQL, re-proves read-only, checks schema drift, and caps at 10k rows / 20 s / 10 MiB.
- **Hard limits relevant here:**
  - Only tables in an approved model can be queried, on every path (`scope_injector.py:117-119`).
  - Medians and percentiles raise `MEASURE_UNAVAILABLE` on MySQL (`compiler.py:987-994`).
  - Without an LLM, only rules-matched certified measures answer.
- **Auth:** OIDC JWT with strict checks (`identity/jwt.py:98-132`), SCIM, scope policies fail-closed even for Admin, field hiding, hash-chained audit (`0005_ops.py`).
- **Errors:** a central `{error:{code,message,request_id}}` envelope (`core/errors.py:41-80`).

Full evidence: `work/pmo_analysis.md` (805 lines).

---

## 5. The 30 questions and ground truth

Every question was asked as: *"Today is Saturday 14 November 2026 (IST). &lt;question&gt;"*. Each expected answer comes from the SQL shown, run read-only, with `SET SESSION TRANSACTION READ ONLY`.

#### E1 (Easy): How many deliveries are recorded in the deliveries table?
- **Business goal:** Volume of processed ball deliveries
- **Tables/columns:** deliveries(id)
- **Logic:** COUNT(*)
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS deliveries FROM deliveries
```
- **Expected answer:** deliveries=65

#### E2 (Easy): How many deliveries in the deliveries table have status 'error'?
- **Business goal:** Failed processing runs
- **Tables/columns:** deliveries(status)
- **Logic:** COUNT where status='error'
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS errors FROM deliveries WHERE status='error'
```
- **Expected answer:** errors=1

#### E3 (Easy): How many active coaches and how many active students are there in user_master_demo?
- **Business goal:** Active user base by type
- **Tables/columns:** user_master_demo(user_type,is_active)
- **Logic:** COUNT grouped by user_type where is_active=1
- **Ground-truth SQL:**
```sql
SELECT user_type, COUNT(*) AS active FROM user_master_demo WHERE is_active=1 GROUP BY user_type ORDER BY user_type
```
- **Expected answer:** user_type=coach, active=4; user_type=student, active=12

#### E4 (Easy): How many side-angle deliveries in sideangle_delivery have status 'done'?
- **Business goal:** Completed side-angle analyses
- **Tables/columns:** sideangle_delivery(status)
- **Logic:** COUNT where status='done'
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS done FROM sideangle_delivery WHERE status='done'
```
- **Expected answer:** done=43

#### E5 (Easy): What is the timestamp of the most recent request in user_activity?
- **Business goal:** Freshness of API traffic log
- **Tables/columns:** user_activity(ts)
- **Logic:** MAX(ts)
- **Ground-truth SQL:**
```sql
SELECT MAX(ts) AS latest FROM user_activity
```
- **Expected answer:** latest=2026-11-13 16:35:27.390122

#### E6 (Easy): How many distinct metric keys are stored in delivery_metrics?
- **Business goal:** Breadth of the metric catalogue
- **Tables/columns:** delivery_metrics(metric_key)
- **Logic:** COUNT(DISTINCT metric_key)
- **Ground-truth SQL:**
```sql
SELECT COUNT(DISTINCT metric_key) AS metric_keys FROM delivery_metrics
```
- **Expected answer:** metric_keys=44

#### E7 (Easy): How many callbacks returned HTTP status 200?
- **Business goal:** Successful webhook deliveries
- **Tables/columns:** callbacks(http_status)
- **Logic:** COUNT where http_status=200
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS ok_200 FROM callbacks WHERE http_status=200
```
- **Expected answer:** ok_200=5

#### E8 (Easy): Which model_key has the most deliveries, and how many does it have?
- **Business goal:** Most used analysis model
- **Tables/columns:** deliveries(model_key)
- **Logic:** GROUP BY model_key, top 1
- **Ground-truth SQL:**
```sql
SELECT model_key, COUNT(*) AS n FROM deliveries GROUP BY model_key ORDER BY n DESC LIMIT 1
```
- **Expected answer:** model_key=best_v4, n=45

#### E9 (Easy): How many students are assigned to coach 12345 in user_master_demo?
- **Business goal:** Coach roster size
- **Tables/columns:** user_master_demo(user_type,coach_id)
- **Logic:** COUNT students with coach_id='12345' (any active state)
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS students FROM user_master_demo WHERE user_type='student' AND coach_id='12345'
```
- **Expected answer:** students=6

#### E10 (Easy): What is the highest peak_speed_kmh recorded in sideangle_delivery?
- **Business goal:** Top run-up speed
- **Tables/columns:** sideangle_delivery(peak_speed_kmh)
- **Logic:** MAX
- **Ground-truth SQL:**
```sql
SELECT MAX(peak_speed_kmh) AS max_peak FROM sideangle_delivery
```
- **Expected answer:** max_peak=32.30

#### H1 (Hard): What percentage of deliveries in the deliveries table completed successfully? Treat status 'C' and 'done' as completed.
- **Business goal:** Pipeline success rate
- **Tables/columns:** deliveries(status)
- **Logic:** completed/total*100; 'C' and 'done' are both terminal-success codes in the data
- **Ground-truth SQL:**
```sql
SELECT ROUND(100*SUM(status IN ('C','done'))/COUNT(*),2) AS pct_completed, SUM(status IN ('C','done')) AS completed, COUNT(*) AS total FROM deliveries
```
- **Expected answer:** pct_completed=98.46, completed=64, total=65

#### H2 (Hard): How many deliveries were created in each calendar month, based on deliveries.created_at?
- **Business goal:** Monthly throughput trend
- **Tables/columns:** deliveries(created_at)
- **Logic:** GROUP BY YYYY-MM of created_at
- **Ground-truth SQL:**
```sql
SELECT DATE_FORMAT(created_at,'%Y-%m') AS month, COUNT(*) AS n FROM deliveries GROUP BY month ORDER BY month
```
- **Expected answer:** month=2026-08, n=6; month=2026-09, n=14; month=2026-10, n=44; month=2026-11, n=1

#### H3 (Hard): For side-angle deliveries with status 'done', what is the average release_speed_kmh per student_id, highest first?
- **Business goal:** Per-player release speed
- **Tables/columns:** sideangle_delivery(student_id,status,release_speed_kmh)
- **Logic:** AVG grouped by student_id, status='done', NULLs ignored
- **Ground-truth SQL:**
```sql
SELECT student_id, ROUND(AVG(release_speed_kmh),2) AS avg_release, COUNT(release_speed_kmh) AS n FROM sideangle_delivery WHERE status='done' GROUP BY student_id ORDER BY avg_release DESC
```
- **Expected answer:** student_id=bad, avg_release=16.60, n=1; student_id=portrait, avg_release=15.30, n=3; student_id=time_portrait, avg_release=14.30, n=1; student_id=run, avg_release=9.91, n=26; student_id=10003, avg_release=9.60, n=1; student_id=default, avg_release=7.70, n=3; student_id=10001, avg_release=7.53, n=3

#### H4 (Hard): What are the top 5 endpoints by request count in user_activity during October 2026?
- **Business goal:** Hot API endpoints in a month
- **Tables/columns:** user_activity(endpoint,ts)
- **Logic:** ts in [2026-10-01, 2026-11-01), GROUP BY endpoint, top 5 (NULL endpoint is a group)
- **Ground-truth SQL:**
```sql
SELECT endpoint, COUNT(*) AS n FROM user_activity WHERE ts>='2026-10-01' AND ts<'2026-11-01' GROUP BY endpoint ORDER BY n DESC LIMIT 5
```
- **Expected answer:** endpoint=api_job_status, n=35052; endpoint=api_result_file, n=3332; endpoint=demo_deliveries, n=1597; endpoint=api_results, n=1579; endpoint=NULL, n=1323

#### H5 (Hard): What percentage of user_activity requests returned a 4xx or 5xx status code in each month?
- **Business goal:** API error rate trend
- **Tables/columns:** user_activity(ts,status_code)
- **Logic:** SUM(status_code>=400)/COUNT(*) by month
- **Ground-truth SQL:**
```sql
SELECT DATE_FORMAT(ts,'%Y-%m') AS month, COUNT(*) AS total, SUM(status_code>=400) AS errors, ROUND(100*SUM(status_code>=400)/COUNT(*),2) AS pct FROM user_activity GROUP BY month ORDER BY month
```
- **Expected answer:** month=2026-08, total=174, errors=0, pct=0.00; month=2026-09, total=1224, errors=112, pct=9.15; month=2026-10, total=44900, errors=6018, pct=13.40; month=2026-11, total=3184, errors=13, pct=0.41

#### H6 (Hard): What is the average processing time in seconds (completed_at_ist minus received_at_ist) of side-angle deliveries with status 'done'?
- **Business goal:** Turnaround time
- **Tables/columns:** sideangle_delivery(status,received_at_ist,completed_at_ist)
- **Logic:** AVG(TIMESTAMPDIFF(SECOND,...)) where both set
- **Ground-truth SQL:**
```sql
SELECT ROUND(AVG(TIMESTAMPDIFF(SECOND,received_at_ist,completed_at_ist)),1) AS avg_s, COUNT(*) AS n FROM sideangle_delivery WHERE status='done' AND received_at_ist IS NOT NULL AND completed_at_ist IS NOT NULL
```
- **Expected answer:** avg_s=53.0, n=43

#### H7 (Hard): How many side-angle deliveries were bowled in the last 7 days, counting from 7 November 2026 up to today?
- **Business goal:** Recent activity window
- **Tables/columns:** sideangle_delivery(bowled_at)
- **Logic:** bowled_at >= 2026-11-07 and < 2026-11-15
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS n FROM sideangle_delivery WHERE bowled_at>='2026-11-07' AND bowled_at<'2026-11-15'
```
- **Expected answer:** n=3

#### H8 (Hard): Which deliveries in the deliveries table have no rows in delivery_metrics? List their id and status.
- **Business goal:** Runs that produced no metrics
- **Tables/columns:** deliveries(id,status), delivery_metrics(delivery_id)
- **Logic:** anti-join
- **Ground-truth SQL:**
```sql
SELECT d.id, d.status FROM deliveries d LEFT JOIN delivery_metrics m ON m.delivery_id=d.id WHERE m.id IS NULL ORDER BY d.id
```
- **Expected answer:** id=41, status=error

#### H9 (Hard): For each coach in user_master_demo, how many active students do they have, including coaches with none?
- **Business goal:** Coach load
- **Tables/columns:** user_master_demo self-join (coach user_id = student coach_id)
- **Logic:** LEFT JOIN coaches to active students
- **Ground-truth SQL:**
```sql
SELECT c.user_id AS coach, COUNT(s.id) AS active_students FROM user_master_demo c LEFT JOIN user_master_demo s ON s.coach_id=c.user_id AND s.user_type='student' AND s.is_active=1 WHERE c.user_type='coach' GROUP BY c.user_id ORDER BY c.user_id
```
- **Expected answer:** coach=1, active_students=0; coach=12345, active_students=5; coach=12346, active_students=5; coach=12347, active_students=0

#### H10 (Hard): For each metric tier in delivery_metrics, how many distinct deliveries have at least one metric in that tier?
- **Business goal:** Metric coverage by tier
- **Tables/columns:** delivery_metrics(tier,delivery_id)
- **Logic:** COUNT(DISTINCT delivery_id) by tier
- **Ground-truth SQL:**
```sql
SELECT tier, COUNT(DISTINCT delivery_id) AS deliveries FROM delivery_metrics GROUP BY tier ORDER BY tier
```
- **Expected answer:** tier=derived, deliveries=54; tier=experimental, deliveries=62; tier=measured, deliveries=64

#### M1 (Most-hard): What is the median speed_kph in delivery_metrics, counting only plausible bowling speeds between 40 and 200 kph?
- **Business goal:** Robust central speed with outlier exclusion
- **Tables/columns:** delivery_metrics(metric_key,value_num)
- **Logic:** metric_key='speed_kph', 40<=v<=200, median (average of two middle values if even)
- **Ground-truth SQL:**
```sql
WITH s AS (SELECT value_num v, ROW_NUMBER() OVER (ORDER BY value_num) rn, COUNT(*) OVER () c FROM delivery_metrics WHERE metric_key='speed_kph' AND value_num BETWEEN 40 AND 200) SELECT ROUND(AVG(v),3) AS median_kph, MAX(c) AS n FROM s WHERE rn IN (FLOOR((c+1)/2), CEIL((c+1)/2))
```
- **Expected answer:** median_kph=80.300, n=42

#### M2 (Most-hard): For how many deliveries does delivery_report.speed_kph differ from the speed_kph metric in delivery_metrics by more than 0.01, where both values exist?
- **Business goal:** Cross-table consistency of the headline metric
- **Tables/columns:** delivery_report(delivery_id,speed_kph), delivery_metrics(delivery_id,metric_key,value_num)
- **Logic:** join on delivery_id, metric_key='speed_kph', ABS diff>0.01
- **Ground-truth SQL:**
```sql
SELECT COUNT(*) AS mismatches, (SELECT COUNT(*) FROM delivery_report r2 JOIN delivery_metrics m2 ON m2.delivery_id=r2.delivery_id AND m2.metric_key='speed_kph' WHERE r2.speed_kph IS NOT NULL AND m2.value_num IS NOT NULL) AS compared FROM delivery_report r JOIN delivery_metrics m ON m.delivery_id=r.delivery_id AND m.metric_key='speed_kph' WHERE r.speed_kph IS NOT NULL AND m.value_num IS NOT NULL AND ABS(r.speed_kph-m.value_num)>0.01
```
- **Expected answer:** mismatches=5, compared=44

#### M3 (Most-hard): For students in user_master_demo with delivery_count above 0, does delivery_count match the number of deliveries rows with that player_id? Show each student's stored and actual counts.
- **Business goal:** Denormalised counter integrity
- **Tables/columns:** user_master_demo(user_id,delivery_count), deliveries(player_id)
- **Logic:** LEFT JOIN aggregate on player_id=user_id
- **Ground-truth SQL:**
```sql
SELECT u.user_id, u.delivery_count AS stored_count, COUNT(d.id) AS actual FROM user_master_demo u LEFT JOIN deliveries d ON d.player_id=u.user_id WHERE u.user_type='student' AND u.delivery_count>0 GROUP BY u.user_id, u.delivery_count ORDER BY u.user_id
```
- **Expected answer:** user_id=10001, stored_count=13, actual=7; user_id=default, stored_count=51, actual=56; user_id=s21, stored_count=1, actual=2

#### M4 (Most-hard): Per delivery that has callbacks, what is the time in seconds from its first callback to its last 'done' callback? Give the average and the maximum.
- **Business goal:** End-to-end pipeline latency
- **Tables/columns:** callbacks(delivery_id,stage,created_at)
- **Logic:** per delivery: MAX(created_at where stage='done') - MIN(created_at); only deliveries with a done callback
- **Ground-truth SQL:**
```sql
WITH p AS (SELECT delivery_id, TIMESTAMPDIFF(MICROSECOND, MIN(created_at), MAX(CASE WHEN stage='done' THEN created_at END))/1e6 s FROM callbacks WHERE delivery_id IS NOT NULL GROUP BY delivery_id HAVING MAX(stage='done')=1) SELECT COUNT(*) AS deliveries, ROUND(AVG(s),1) AS avg_s, ROUND(MAX(s),1) AS max_s FROM p
```
- **Expected answer:** deliveries=64, avg_s=692604.4, max_s=6135512.2

#### M5 (Most-hard): How many user_activity requests were there in each of the 4 full ISO weeks (Monday to Sunday) before the current week, and what is the week-over-week percentage change?
- **Business goal:** Weekly traffic trend
- **Tables/columns:** user_activity(ts)
- **Logic:** weeks 2026-10-12..2026-11-08; LAG for WoW %
- **Ground-truth SQL:**
```sql
WITH w AS (SELECT DATE(ts - INTERVAL WEEKDAY(ts) DAY) wk, COUNT(*) n FROM user_activity WHERE ts>='2026-10-12' AND ts<'2026-11-09' GROUP BY wk) SELECT wk AS week_start, n, ROUND(100*(n-LAG(n) OVER (ORDER BY wk))/LAG(n) OVER (ORDER BY wk),1) AS wow_pct FROM w ORDER BY wk
```
- **Expected answer:** week_start=2026-10-12, n=414, wow_pct=NULL; week_start=2026-10-19, n=5368, wow_pct=1196.6; week_start=2026-10-26, n=8267, wow_pct=54.0; week_start=2026-11-02, n=3094, wow_pct=-62.6

#### M6 (Most-hard): Among completed deliveries (status 'C' or 'done') in delivery_report that have a length_zone, what percentage falls in each length zone?
- **Business goal:** Length distribution of good deliveries
- **Tables/columns:** delivery_report(status,length_zone)
- **Logic:** filter, share of total within filter
- **Ground-truth SQL:**
```sql
SELECT length_zone, COUNT(*) AS n, ROUND(100*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS pct FROM delivery_report WHERE status IN ('C','done') AND length_zone IS NOT NULL GROUP BY length_zone ORDER BY n DESC, length_zone
```
- **Expected answer:** length_zone=good length, n=20, pct=47.6; length_zone=full, n=12, pct=28.6; length_zone=back of a length, n=4, pct=9.5; length_zone=short, n=4, pct=9.5; length_zone=yorker, n=2, pct=4.8

#### M7 (Most-hard): For each student_id in sideangle_delivery, what is the highest peak_speed_kmh among 'done' deliveries, and when was that delivery bowled?
- **Business goal:** Personal best with date
- **Tables/columns:** sideangle_delivery(student_id,status,peak_speed_kmh,bowled_at)
- **Logic:** ROW_NUMBER per student by peak desc; ties -> earliest bowled_at
- **Ground-truth SQL:**
```sql
WITH r AS (SELECT student_id, peak_speed_kmh, bowled_at, ROW_NUMBER() OVER (PARTITION BY student_id ORDER BY peak_speed_kmh DESC, bowled_at) rn FROM sideangle_delivery WHERE status='done' AND peak_speed_kmh IS NOT NULL) SELECT student_id, peak_speed_kmh, bowled_at FROM r WHERE rn=1 ORDER BY peak_speed_kmh DESC
```
- **Expected answer:** student_id=bad, peak_speed_kmh=32.30, bowled_at=2026-11-04 11:55:32.375393; student_id=run, peak_speed_kmh=31.00, bowled_at=2026-11-11 12:03:06.514425; student_id=10001, peak_speed_kmh=23.60, bowled_at=2026-10-26 09:30:00.000000; student_id=10003, peak_speed_kmh=23.30, bowled_at=2026-11-11 13:09:25.664486; student_id=portrait, peak_speed_kmh=22.00, bowled_at=2026-11-02 12:42:36.748683; student_id=default, peak_speed_kmh=21.90, bowled_at=2026-10-27 14:47:13.904622; student_id=time_portrait, peak_speed_kmh=18.80, bowled_at=2026-11-02 13:13:24.697850

#### M8 (Most-hard): For the 5 busiest endpoints in user_activity, what is the 95th-percentile duration_ms (nearest-rank method)?
- **Business goal:** Tail latency of hot endpoints
- **Tables/columns:** user_activity(endpoint,duration_ms)
- **Logic:** top 5 by volume (non-NULL endpoint); nearest-rank p95 = value at rank CEIL(0.95*n)
- **Ground-truth SQL:**
```sql
WITH top5 AS (SELECT endpoint FROM user_activity WHERE endpoint IS NOT NULL GROUP BY endpoint ORDER BY COUNT(*) DESC LIMIT 5), r AS (SELECT a.endpoint, a.duration_ms, ROW_NUMBER() OVER (PARTITION BY a.endpoint ORDER BY a.duration_ms) rn, COUNT(*) OVER (PARTITION BY a.endpoint) c FROM user_activity a JOIN top5 t ON t.endpoint=a.endpoint WHERE a.duration_ms IS NOT NULL) SELECT endpoint, MAX(c) AS n, MAX(CASE WHEN rn=CEIL(0.95*c) THEN duration_ms END) AS p95_ms FROM r GROUP BY endpoint ORDER BY n DESC
```
- **Expected answer:** endpoint=api_job_status, n=35712, p95_ms=16; endpoint=api_result_file, n=3568, p95_ms=9; endpoint=job_status, n=3030, p95_ms=3; endpoint=api_results, n=1649, p95_ms=5; endpoint=demo_deliveries, n=1616, p95_ms=93

#### M9 (Most-hard): Which delivery_ids in callbacks do not have the same number of 'queued', 'metrics' and 'done' callbacks, meaning their pipeline trail is incomplete or duplicated?
- **Business goal:** Pipeline trail integrity
- **Tables/columns:** callbacks(delivery_id,stage)
- **Logic:** per delivery_id count stage queued/metrics/done; keep where counts differ
- **Ground-truth SQL:**
```sql
SELECT delivery_id, SUM(stage='queued') AS queued, SUM(stage='metrics') AS metrics, SUM(stage='done') AS done FROM callbacks WHERE delivery_id IS NOT NULL GROUP BY delivery_id HAVING queued<>metrics OR queued<>done ORDER BY delivery_id
```
- **Expected answer:** delivery_id=4, queued=4, metrics=3, done=4; delivery_id=39, queued=3, metrics=3, done=6; delivery_id=166, queued=1, metrics=0, done=1; delivery_id=224, queued=0, metrics=0, done=1

#### M10 (Most-hard): From admin_activity_log, which actors logged in, on how many distinct days did each log in, and when was each one's last login?
- **Business goal:** Admin sign-in audit
- **Tables/columns:** admin_activity_log(actor_id,actor_name,action,created_at)
- **Logic:** action='login', group by actor
- **Ground-truth SQL:**
```sql
SELECT actor_id, actor_name, COUNT(DISTINCT DATE(created_at)) AS days, MAX(created_at) AS last_login FROM admin_activity_log WHERE action='login' GROUP BY actor_id, actor_name ORDER BY actor_id
```
- **Expected answer:** actor_id=1, actor_name=Administrator, days=2, last_login=2026-10-28 11:51:38.933162; actor_id=12345, actor_name=Ravi Kumar, days=2, last_login=2026-10-28 12:58:29.385964


---

## 6. Actual answers and scoring

Scoring: 100 fully correct, 75 correct with minor issues, 50 partly correct, 25 relevant but wrong, 0 no answer, failure or wrong. Strict accuracy counts only 100s.

| ID | Level | Expected | Tela: score and reason | PMO Dashboard: score and reason |
|---|---|---|---|---|
| E1 | Easy | deliveries=65 | **100** · 65, correct (10.0 s) | **100** · 65, correct (16.4 s) |
| E2 | Easy | errors=1 | **100** · 1, correct (12.1 s) | **100** · 1, correct (16.3 s) |
| E3 | Easy | user_type=coach, active=4; user_type=student, active=12 | **0** · Filtered role='coach'/'student' instead of user_type -> 0 and 0; expected 4 and 12 (4.0 s) | **0** · Refused: model quoted `ball-report.user_master_demo` as one identifier; scope check could not apply rules (check_failed). Also used role instead of user_type (6.2 s) |
| E4 | Easy | done=43 | **100** · 43, correct (2.0 s) | **100** · 43, correct (10.3 s) |
| E5 | Easy | latest=2026-11-13 16:35:27.390122 | **100** · 2026-11-13 16:35:27.390122, correct (10.1 s) | **100** · 2026-11-13 16:35:27.390122, correct (22.5 s) |
| E6 | Easy | metric_keys=44 | **100** · 44, correct (4.0 s) | **100** · 44, correct (12.3 s) |
| E7 | Easy | ok_200=5 | **100** · 5, correct (2.0 s) | **100** · 5, correct (16.4 s) |
| E8 | Easy | model_key=best_v4, n=45 | **100** · best_v4 45, correct (2.0 s) | **100** · best_v4 45 at the top (lists the other models too) (22.5 s) |
| E9 | Easy | students=6 | **100** · 6, correct (4.0 s) | **0** · Summed JSON_LENGTH(students_json) -> NULL; expected 6 students (22.5 s) |
| E10 | Easy | max_peak=32.30 | **100** · 32.30, correct (2.0 s) | **75** · 32.3 is the first row, but SQL grouped by the value and returned 10 rows instead of one maximum (40.9 s) |
| H1 | Hard | pct_completed=98.46, completed=64, total=65 | **100** · 98.46%, correct (22.1 s) | **100** · 98.46%, correct (26.6 s) |
| H2 | Hard | month=2026-08, n=6; month=2026-09, n=14; month=2026-10, n=44; month=2026-11, n=1 | **100** · 6/14/44/1 per month, correct (12.1 s) | **100** · 6/14/44/1 per month, correct (20.4 s) |
| H3 | Hard | student_id=bad, avg_release=16.60, n=1; student_id=portrait, avg_release=15.30, n=3; student_id=time_portrait, avg_release=14.30, n=1; stude | **50** · Grouped by student_id AND student_name, so 10001 and default were split into several rows with wrong averages; the other 5 students correct (4.0 s) | **0** · Queried deliveries.results_json instead of sideangle_delivery; returned no rows (20.4 s) |
| H4 | Hard | endpoint=api_job_status, n=35052; endpoint=api_result_file, n=3332; endpoint=demo_deliveries, n=1597; endpoint=api_results, n=1579; endpoint | **100** · Top 5 incl. NULL endpoint, counts correct (14.1 s) | **100** · Top 5 incl. NULL endpoint, counts correct (24.5 s) |
| H5 | Hard | month=2026-08, total=174, errors=0, pct=0.00; month=2026-09, total=1224, errors=112, pct=9.15; month=2026-10, total=44900, errors=6018, pct= | **100** · 0 / 9.15 / 13.40 / 0.41 %, correct (16.1 s) | **100** · 0 / 9.15 / 13.40 / 0.41 %, correct (12.2 s) |
| H6 | Hard | avg_s=53.0, n=43 | **100** · 53.02 s, correct (2.0 s) | **0** · Averaged deliveries.duration_s for a non-existent app_type -> NULL; expected 53.0 s from sideangle_delivery (10.2 s) |
| H7 | Hard | n=3 | **100** · 3, correct (4.0 s) | **0** · Attempt 1 provider failure; retry used deliveries.session_date -> 0; expected 3 (sideangle_delivery.bowled_at) (8.2 s) |
| H8 | Hard | id=41, status=error | **100** · id 41 / error, correct (4.0 s) | **0** · Attempt 1 provider failure; retry refused: `ball-report.deliveries` quoted as one identifier (6.2 s) |
| H9 | Hard | coach=1, active_students=0; coach=12345, active_students=5; coach=12346, active_students=5; coach=12347, active_students=0 | **25** · Wrong self-join (student.coach_id = student.coach_id): one row per student labelled coach_name; 12345 and 12346 show 5 but coaches 1 and 12347 (0) missing (4.0 s) | **0** · Refused: hyphenated schema quoted as one identifier (check_failed) (14.3 s) |
| H10 | Hard | tier=derived, deliveries=54; tier=experimental, deliveries=62; tier=measured, deliveries=64 | **100** · derived 54 / experimental 62 / measured 64, correct (20.1 s) | **100** · derived 54 / experimental 62 / measured 64, correct (14.3 s) |
| M1 | Most-hard | median_kph=80.300, n=42 | **100** · 80.3 median over the 40-200 kph values, correct (30.2 s) | **0** · Refused: hyphenated schema quoting (check_failed); median logic was reasonable (22.5 s) |
| M2 | Most-hard | mismatches=5, compared=44 | **100** · 5 mismatches, correct (12.1 s) | **0** · Attempt 1 provider failure; retry refused: unquoted ball-report.delivery_report (32.7 s) |
| M3 | Most-hard | user_id=10001, stored_count=13, actual=7; user_id=default, stored_count=51, actual=56; user_id=s21, stored_count=1, actual=2 | **100** · 10001 13 vs 7, default 51 vs 56, s21 1 vs 2, correct (2.0 s) | **0** · Model wrote no SQL in either attempt (12.3 s) |
| M4 | Most-hard | deliveries=64, avg_s=692604.4, max_s=6135512.2 | **0** · Filtered status_code='done' instead of stage='done' -> NULL (4.0 s) | **25** · Average only (681,952 s vs 692,604 s; included the NULL-delivery callback group), no maximum (8.2 s) |
| M5 | Most-hard | week_start=2026-10-12, n=414, wow_pct=NULL; week_start=2026-10-19, n=5368, wow_pct=1196.6; week_start=2026-10-26, n=8267, wow_pct=54.0; week | **0** · Wrote SQL for the wrong weeks (from 28 Sep) and then asked for clarification; no answer (24.1 s) | **0** · Refused: hyphenated schema quoting (check_failed) (6.2 s) |
| M6 | Most-hard | length_zone=good length, n=20, pct=47.6; length_zone=full, n=12, pct=28.6; length_zone=back of a length, n=4, pct=9.5; length_zone=short, n= | **100** · 47.62 / 28.57 / 9.52 / 9.52 / 4.76 %, correct (4.0 s) | **0** · Attempt 1 provider failure; retry refused (schema quoting) (14.3 s) |
| M7 | Most-hard | student_id=bad, peak_speed_kmh=32.30, bowled_at=2026-11-04 11:55:32.375393; student_id=run, peak_speed_kmh=31.00, bowled_at=2026-11-11 12:03 | **100** · All 7 students' personal best and bowled_at correct (8.0 s) | **100** · All 7 students' personal best and bowled_at correct (4.1 s) |
| M8 | Most-hard | endpoint=api_job_status, n=35712, p95_ms=16; endpoint=api_result_file, n=3568, p95_ms=9; endpoint=job_status, n=3030, p95_ms=3; endpoint=api | **100** · p95 93 / 16 / 9 / 5 / 3 ms, correct (16.1 s) | **0** · Attempt 1 provider failure; retry returned blanks for 4 endpoints and 52 vs 93 ms for the fifth (6.2 s) |
| M9 | Most-hard | delivery_id=4, queued=4, metrics=3, done=4; delivery_id=39, queued=3, metrics=3, done=6; delivery_id=166, queued=1, metrics=0, done=1; deliv | **100** · delivery_ids 4, 39, 166, 224, correct (16.1 s) | **75** · Correct delivery_ids 4, 39, 166, 224, but returned 74 raw callback rows instead of one row per id with counts (12.3 s) |
| M10 | Most-hard | actor_id=1, actor_name=Administrator, days=2, last_login=2026-10-28 11:51:38.933162; actor_id=12345, actor_name=Ravi Kumar, days=2, last_log | **100** · Administrator and Ravi Kumar, 2 days each, last logins correct (12.1 s) | **0** · Attempt 1 provider failure; retry refused: unquoted ball-report.admin_activity_log (18.4 s) |

The full SQL and rows each app returned are in the workbook's Answers sheet and in work/tela_answers.json and work/pmo_answers.json.

### 6.1 How the runs were made fair
- **Same everything:** both apps got the same wording, the same date sentence, the same Gemini key, the same model (`gemini-3.5-flash-lite`) and the same read-only MySQL login (`pmo_ro`).
- **Each app got only the setup it requires:**
  - Tela: all 14 tables auto-approved by its bootstrap.
  - PMO Dashboard: a model built from introspection; the four real links its assistant proposed; personal-data answers; Admin all-rows scope policies; Ask mode **Direct SQL**, the like-for-like mode where the model writes SQL and the app checks it.
- **Neither app** was given the 30 questions, their SQL or prose business notes.
- **Retry rule, applied to both:** a failure caused by the model provider (HTTP 429/503, "couldn't write SQL" at those moments) was retried once, with 20 s gaps. A failure caused by the app's own logic was never retried. The score is the final attempt; the first attempt is kept in `work/pmo_answers_attempt1.json`.
  - PMO Dashboard: 8 provider failures, all retried.
  - Tela: no provider errors.
- **PMO Dashboard's first attempt alone** would have scored lower: 13 completed, 17 failed.

---

## 7. Gap analysis, module by module

| Area | Tela | PMO Dashboard | Verdict |
|---|---|---|---|
| KPIs | 225 PMO KPI cards computed in browser JS (calc.js:329-386); hard-wired to TPG values | Certified measures compiled to SQL from a pack (packs/pmo); pack_all.yaml unused and buggy | PMO: better architecture (server-side, governed). Tela: more finished PMO content. |
| Project reporting | Project records, dossiers, drawers (ui.js:~700-790) | Pack entity projects, blueprints (report_blueprints.yaml:52-113) | Both TPG-only; neither applies to ball-report |
| Resource reporting | Hours vs standard week only; no capacity (calc.js:93-95) | Timesheet hours only; blueprint admits 'not utilisation' | Both weak |
| Financial reporting | Budget, estimate, TCV; no actual spend (dash_del.js:249) | Budget vs forecast, cost components; population mismatches (pack_all.yaml:243 vs 248) | Both lack actuals |
| Schedule | Go-live, slip, stage plan, tasks, PCR cover (calc.js:60-115) | Overdue, horizon, slip; median keys are AVG (pack_all.yaml:348-358); milestone slippage unavailable | Tela more complete |
| Risk & issue | RAID D7 (calc.js:83-87) | RAID entities; blank-state risks excluded (pack_all.yaml:743) | Comparable; PMO has a definite counting bug in the unused pack |
| History / trends | Absent (no state/RAG snapshots) | Absent (report_blueprints.yaml:661-665) | Both missing: a hard gap for enterprise PMO |
| Drill-down | Drawers open the records behind each number | POST /widgets/{id}/drill, /query/records | Both present |
| NL question answering | Generic NL->SQL over all approved tables; LLM-only, no fallback; MAX_CONCURRENT_TURNS=1. Measured on ball-report: 83.3% strict (25/30) | Rules planner first, LLM second; only approved-model tables; Direct SQL mode used. Measured on ball-report: 43.3% strict (13/30), hyphenated-schema quoting broke ~7 queries | Tela clearly better on measured answers |
| API | ~190 Tela endpoints + 40 dashboard endpoints; no versioning | ~363 routes under /api/v1, OpenAPI, SCIM | PMO more enterprise-shaped |
| Authentication | Local bcrypt accounts, no login rate limit in Tela, no Secure cookie, 168 h sessions | OIDC/Entra, strict JWT checks, SCIM | PMO clearly better |
| Authorisation | Grants + AST row filters; viewer can bypass via Tela; IDOR on ask result | Fail-closed scope policies incl. Admin; field hiding | PMO clearly better |
| Audit | SQLite audit (dashboards); Tela traces lack user id | Hash-chained append-only partitioned audit | PMO clearly better |
| Errors | Credential failure shown as 'try rephrasing' (planner.py:343) | Central error envelope with request id | PMO better |
| Performance/scalability | Single worker, JSON files, 1 question at a time globally | Postgres/Redis; in-process ask tasks (8/instance), 150 ms SSE polling | PMO better, neither load-tested |
| Maintainability | ES5 single-line functions, 469 duplicated SQL strings, dead config | Typed modular monolith, god-modules, 4 answer modes | PMO better |
| Validation & security | Strong SQL validator; MySQL TLS disabled in code (executors.py:72); CSP unsafe-inline | Signed envelopes, runner re-check, read-only proof; /metrics unauth without proxy; Direct SQL on by default | PMO better |
| Operability | Runs today without Docker, one script | 13+ containers, Docker required, backup stub, no Helm | Tela better today |

### 7.1 Calculations that are wrong (with evidence)
**Tela**
1. "% complete known" is defined two ways: `calc.js:45` uses `pct == null`, while `calc.js:203` and `:352` need `pct > 0`.
2. Hard-coded 30/90/180-day limits ignore admin thresholds: `calc.js:383`, `dash_gov.js:243`, `kinds.js:356`, `calc.js:253`.
3. `PMO_AS_OF` doesn't exclude facts dated later: `calc.js:58, 80-81`.
4. The demand-flow backlog never releases Approved requests that have no DARB date: `calc.js:488`.
5. The saved-answer cache isn't tied to the database: `pmo_server.py:249-262, 631-654`.
6. The 10k row cap is soft: `validator.py:430-442`, `executors.py:103`.

**PMO Dashboard**
1. `health_insufficient` is always 0, because `NULL <> 'Red'` is never true: `pack_all.yaml:338-342`, `compiler.py:688`.
2. Amber is dropped from health buckets: `pack_all.yaml:328, 343` check only Yellow.
3. Measures named `*_median` / `*_p85` are actually AVG / MAX: `pack_all.yaml:348-358, 383, 483-488, 998-1012`.
4. Blank-state risks aren't counted as open, which contradicts the file's own footnote: `pack_all.yaml:743-762` vs `:2074`.
5. KPI populations are mixed: `net_forecast_variance` covers projects in any state but sits on the open-projects overview (`pack_all.yaml:448, 1375`).
6. `NOT IN` drops NULL statuses (`:188`); a cost total with no COALESCE can come out NULL (`:528`).

Note: `pack_all.yaml` is not loaded by any code. The shipped `packs/pmo/manifest.yaml` avoids bugs 1 and 4. Two divergent pack definitions are a maintenance risk in themselves.

### 7.2 Business-logic differences
| Topic | Tela | PMO Dashboard |
|---|---|---|
| Where KPIs are computed | Browser JS over a full extract | Server-side SQL from certified measures |
| "Open" project | 'Project Created' / 'Active' / 'On Hold' (calc.js:21) | same set, in YAML (pack_all.yaml:183) |
| RAG | 'Yellow' shown as Amber (calc.js:57) | Yellow/Amber inconsistent (above); packs/pmo uses a weighted 35/25/25/15 calculator |
| Today / as-of | `PMO_AS_OF` or server local date, no TZ (pmo_server.py:777) | model `reporting_date` or today in tenant TZ (query/asof.py:39-50) |
| Unknown question | LLM or nothing | Rules → LLM → refuse (never guesses) |
| Medians | Whatever SQL the LLM writes | Unavailable on MySQL |

---

## 8. Enterprise readiness (0–10)

| Criterion | Tela | PMO | Tela evidence | PMO Dashboard evidence |
|---|---|---|---|---|
| Runs against ball-report on this machine | 8 | 6 | Ran after a stack.env edit + moving connection.json aside (DB_NAME alone does not retarget: app.py:160-163, bootstrap.py:27-33); bootstrap auto-approved all 14 tables with the read-only login. No Docker needed. | Ran after installing Docker (Colima), 13+ containers, a read-only login, model approval (21 personal-data questions), 14 scope policies and an Ask-mode change. Several setup defects (section 9). |
| Measured NL answer accuracy on ball-report (30 questions) | 8 | 4 | 85.8% partial, 83.3% strict (25/30), no provider errors, 2-30 s per answer. Misses: role vs user_type, wrong self-join, name split, status_code vs stage, one clarification. (Earlier the same day 0/30 with the suspended key and no fallback.) | 49.2% partial, 43.3% strict (13/30) after one retry of 8 provider failures. Mis-quoted hyphenated schema (`ball-report.table`) so its scope check refused about 7 queries; wrong tables on H3/H6/H7. |
| Schema portability (non-PMO data) | 6 | 5 | Talk to data is generic and proved it on ball-report (bare table names, no quoting issues). Dashboards hard-wired to TPG names/values: calc.js:21-27, 469 fixed SQL strings, 'On Hold' x56; docs/PRODUCTION_PLAN.md:13 'Not portable'. | Generic connector, model-from-introspection (model/catalog.py:80-118). But only approved-model tables are queryable, scope policies required even for Admin, and medians/percentiles unavailable as governed measures on MySQL (compiler.py:987-994). Its Direct SQL writer broke on the hyphenated schema name in this test. |
| PMO domain coverage (KPI, project, schedule, RAID, finance, resource) | 7 | 6 | 225 KPI cards / 30 dashboards; schedule, RAID, drill-down present; no actual spend (dash_del.js:249), no data history, resource = hours only. | Pack measures for portfolio, schedule, finance, RAID, demand, flow; resource = hours only; history absent (report_blueprints.yaml:661-665). |
| KPI calculation correctness | 6 | 5 | No arithmetic errors in core formulas; inconsistencies: %-complete known two ways (calc.js:45 vs 203/352), hard-coded 30/90-day limits, PMO_AS_OF does not time-slice. | Shipped packs/pmo uses a calculator; pack_all.yaml has definite bugs (health_insufficient always 0 :338, Amber dropped :328, median keys are AVG :348-358, blank risks not open :743). Two divergent packs. |
| Query safety / SQL guardrails | 7 | 9 | sqlglot AST validator, single SELECT, allow-lists, READ ONLY session, 15 s timeout, read-only login check (validator.py, executors.py:93-97). 10k cap is soft (validator.py:430-442, executors.py:103). | AST compiler with bound params, scope injection + independent assert_scoped, Ed25519-signed envelopes, runner re-validation, hard 10k/20 s/10 MiB caps. |
| Authentication | 4 | 8 | Local accounts, bcrypt; Tela login has no rate limit and cookie lacks Secure (routers/auth.py:24-43); sessions 168 h. No SSO/MFA. | OIDC (Entra via Keycloak) with strict JWT checks (identity/jwt.py:98-132), SCIM, session revocation. |
| Authorisation / row scope | 5 | 8 | Deny-by-default grants with AST row filters, but viewers can call Tela directly and read child tables unscoped (pmo_access.py:8-9); /ask/{id}/result has no ownership check (ask.py:112-148). | Fail-closed scope policies incl. Admin, field hiding, per-connection grants with expiry. In-process rate limits only. |
| Audit | 5 | 8 | Dashboards: SQLite audit with CSV export. Tela pipeline trace has no user id (pipeline.py:47-61). | Partitioned, append-only, hash-chained audit (0005_ops.py); UPDATE/DELETE revoked. Per-tenant advisory lock on every insert. |
| Error handling & validation | 4 | 6 | Suspended credential reported as 'try rephrasing' (planner.py:343); MySQL adapter swallows errors (mysql_adapter.py:428-433); ad-hoc input truncation. | Central error envelope with request id, pydantic at edges, spec validator. But in use: a bad model name became 'The language model is unavailable' (Google said 400), the scope Save button failed silently, a 500 on draft/impact after approval. |
| Performance & scalability | 2 | 5 | Single worker enforced, JSON-file stores re-read per request, MAX_CONCURRENT_TURNS=1 globally (turns.py:136-140), full dataset sent to browser, new MySQL connection per query. | Postgres + Redis, caching; but asks are bare asyncio tasks (8/instance), SSE polls Postgres every 150 ms, unbounded snapshot cache, load testing not started (task.md:2710). |
| Maintainability | 4 | 6 | ES5 page with lines up to 1,778 chars, rules spread over calc/kinds/dash_*.js, 469 duplicated SQL strings, dead config (MYSQL_LOCAL_*, ignored LLM_MODEL). | Typed, modular, mypy/ruff in CI; but god-modules (orchestrator.py 2,078 lines), four answer modes, 1.6 MB of ticket docs needed to read comments. |
| Testing & CI | 5 | 7 | 101 root tests + ~2,794 Tela backend tests; golden tests pinned to the TPG extract; no hosted CI. | ~1,560 Python + 386 web unit + 42 e2e tests, conformance matrix; CI defined but 'not yet run on a hosted runner' (task.md). |
| Deployability / operations | 6 | 3 | Runs today from one script without Docker; but plain-HTTP internal hops, MySQL TLS off in code (executors.py:72). | 13+ containers; Helm absent; backup/retention a stub (maintenance.py:29-33); Azure profile absent. |
| Documentation honesty | 7 | 4 | docs/NOT_FINISHED.md and TELA_CHANGE_REQUESTS.md list gaps honestly; some stale labels. | README ground rule 'LLM never writes SQL' contradicted (settings.py:115); Key Vault/Blob marked done but absent; coverage gate 80 vs claimed 84. |
| **Average** | **5.6** | **6.0** |  |  |

---

## 9. Risks found during the test

1. **Tela's question answering went down silently.** The earlier Gemini key's Google project was suspended. There was no alert, no fallback, and the user saw "try rephrasing". The key also sits in plain text in `stack.env` (fixed now with a new key, but still in plain text). Monitor model failures and store the key in a secrets store.
2. **PMO Dashboard defects seen while running it:**
   - Its Direct SQL writer mis-quotes hyphenated schema names, so its scope check refuses the query (cost about 7 of 30 answers).
   - The AI settings field accepts a display name ("Gemini 3.5 Flash-Lite") and the error says only "The language model is unavailable", when Google returned 400 for a bad model name.
   - The connection test labels MySQL 9.7.1 as "PostgreSQL 9.7.1".
   - Answering "Not personal" clears the question but keeps the field marked personal.
   - The scope-policy Save button does nothing until the optional-looking "scope chip" text is filled in, and shows no message.
   - A 500 error (`uq_one_draft`) on the model impact check right after approval.
   - The answer headline says "using the certified measure rule" for uncertified Direct SQL answers.
   - Query results are cached for 15 minutes by default.
2. **MySQL root has a trivial password** (a four-digit numeric password, supplied in chat for this test), and root can read `user_master_demo.password_hash` and emails. Create a read-only reporting login and change the root password.
3. **`DB_NAME` in `stack.env` is misleading config.** It is ignored once `connection.json` exists, so an operator can believe the stack points at one database while it reads another.
4. **The saved-answer cache can serve answers from a different database** after a switch (not tied to the connection id).
5. **MySQL TLS is disabled in Tela's code.** That makes Tela unsafe against any non-local database.
6. **PMO Dashboard's docs claim more than the code delivers:**
   - Azure Key Vault and Blob storage are marked done but are absent;
   - "LLM never writes SQL" is false, since Direct SQL is on by default;
   - the coverage gate is 80%, not the claimed 84%;
   - Dataverse was tested only against a fake.

   Anyone who buys it on its docs is misled.
7. **Neither product keeps data history.** Trends, "as it was" reporting and audit of KPI changes are impossible in both, which is a core enterprise PMO need.
8. **`ball-report` data quality is poor:** impossible speeds, mismatched denormalised copies, placeholder identities. Any analytics on it needs validation rules first.

---

## 10. Recommendation

1. **For answering questions today, Tela is clearly better.** It scored 83.3% strict against PMO Dashboard's 43.3%, with less setup and no provider errors. Add failure alerting for its model calls and keep a second provider configured, so a dead key can't silently disable it again.
2. **As the enterprise foundation, choose PMO Dashboard's architecture, conditionally.** Before then, fix its SQL writer's identifier quoting (a hyphenated or unusual schema name must not break scoping) and re-run this benchmark. Further conditions:
   - a working deployment exists (not just docs);
   - CI runs green on a hosted runner;
   - load, backup and restore, and a security review are done;
   - `pack_all.yaml` is deleted or fixed to agree with `packs/pmo`;
   - data history (snapshots) is designed in.

   If those conditions can't be met within the programme timeline, neither product is a credible enterprise base.
3. **Don't invest in scaling Tela's backend.** JSON files, a single worker and one question at a time are architectural limits, not tuning problems. Port Tela's TPG KPI definitions and golden figures into the governed measure model as the acceptance oracle.
4. **Next comparison:** re-run on a real PMO database, for example `tpg_dv_ex`, which both products target. That tests PMO KPI accuracy, not just generic question answering. Both apps are now set up to do it: PMO Dashboard runs in Docker, and both share the key and the read-only-login approach.

---

## Appendix A: how the test was run (and reverted)

1. **Ground truth.** `work/questions.py` holds the 30 questions. Every question was asked as *"Today is Saturday 14 November 2026 (IST). <question>"* to both apps. The database's last write was 2026-11-13 16:35, so the answers are stable. All ground-truth SQL ran with `SET SESSION TRANSACTION READ ONLY`.
2. **Read-only login.** At the user's request, `pmo_ro`@`%` was created with `SELECT, SHOW VIEW ON ball-report.*` only. It can't change data and can't see other databases. Both apps used it.
3. **First Tela run: all 30 failed.** Every Gemini call returned `403 … Consumer has been suspended`. Kept in `work/tela_answers_suspended_key.json`.
4. **Model key.** The user replaced the Gemini key in `stack.env` and restarted Tela. A check question on `tpg_dv_ex` answered correctly in 4.1 s. The same key was entered in PMO Dashboard's AI settings. The first save failed because the model name had been typed as its display label; it passed with `gemini-3.5-flash-lite`.
5. **PMO Dashboard setup** (Docker via Colima, a copy of the zip; dev secrets made by its own `dev-setup.sh`):
   - connection `ball-report` (host `host.docker.internal`, TLS disable in dev); its test confirmed the account is read-only;
   - schema read (14 tables, 359 columns);
   - model built from introspection; 21 personal-data questions answered;
   - assistant-proposed links applied and confirmed;
   - approved as v1;
   - 14 Admin all-rows scope policies;
   - Ask mode Direct SQL.
6. **PMO Dashboard run.** `work/ask_pmo.py` signs in the way the project's own smoke test does (Keycloak form + PKCE), posts each question to `POST /api/v1/ask/turns` and reads the answer stream.
   - The 8 provider failures were retried once, with 20 s gaps.
7. **Tela run** (approved procedure):
   - `./stack.sh stop`, then a checksummed backup (23 files) of `stack.env`, `data/`, `run/`, `tela/backend/.env`, `tela/backend/data/` and `tela/backend/logs/` to `scratchpad/backup_tela2`;
   - `DB_NAME=ball-report` and `DB_USER/DB_PASSWORD` set to `pmo_ro`; `connection.json` moved aside;
   - `./stack.sh start`: "bootstrap ok … 14 tables approved … custom SQL on (This login can only read)";
   - `work/ask_tela.py` asked the 30 questions through `POST /api/ask`, pinned to the `ball-report` connection.
8. **Revert:**
   - `./stack.sh stop`; post-test state kept in `work/post_test_state2/`;
   - every backed-up file restored and compared with its checksum: **0 differences**;
   - `./stack.sh start`: "bootstrap ok: connection mysql:127.0.0.1:3306:tpg_dv_ex"; all three services answering;
   - `git status` is clean in the repo and the `tela` submodule.
9. **Left in place for further testing:**
   - the `pmo_ro` login;
   - the PMO Dashboard containers (stop with `colima stop`);
   - a copy of the PMO Dashboard code at `~/Desktop/pmo_bashboard-master`.


## Appendix B: evidence files
- `work/questions.py`: the 30 questions, ground-truth SQL and runner.
- `work/ground_truth.json`: verified expected answers.
- `work/ask_tela.py`, `work/tela_answers.json`: Tela's captured answers with the new key (full API responses with trace). `work/tela_answers_suspended_key.json` holds the earlier run, in which every question failed with the suspended key.
- `work/ask_pmo.py`, `work/pmo_answers.json`, `work/pmo_answers_attempt1.json`: PMO Dashboard's captured answers (final, and first attempt) with SQL, rows and checks.
- `work/tela_scores.json`, `work/pmo_scores.json`: the score and reason for every answer.
- `work/tela_run_log.txt`, `work/tela_run_log_live.txt`: Tela backend logs (suspended-key run; new-key run).
- `work/schema.txt`: `SHOW CREATE TABLE` for all 14 tables.
- `work/tela_analysis.md`, `work/pmo_analysis.md`: code analyses with file:line citations.
- `tela_vs_pmo_ball-report.xlsx`: Summary, Questions, Answers, Readiness, DB tables, Gap analysis.

---

## 11. PMO Dashboard after generic code fixes (approved 14 Nov 2026)

Seven generic fixes were made, with the user's approval, in `~/Desktop/pmo_bashboard-master`. A local git repo there has the original zip as its baseline commit; `git diff` shows every change. No table, column or question from `ball-report` is written into the code. `services/api/tests/test_generic_sql_fixes_unit.py` uses an invented schema.

| # | Fix | Where |
|---|---|---|
| 1 | Table and column names shown to the model quoted per dialect (`` `ball-report`.deliveries ``) | `content/sql_context.py` (`sql_name`, `sql_object`) |
| 2 | Awkward schema names from the model quoted before parsing; `` `schema.table` `` written as one name split (fixes the double-schema bug) | `query/custom_sql.py` (`_quote_awkward_names`, `_split_dotted_tables`) |
| 3 | The repair round gets the parser's words and the scope reason; the audit records them; a misnamed table gets the one repair | `ask/direct_sql.py` (`repair_request`, `_why`, `_repairable`) |
| 4 | Compound words match run-together names (side-angle ↔ sideangle, deliveries ↔ delivery); models with ≤ 20 tables also list all other tables | `content/sql_context.py` (`_asked`, `_stem`, catalogue) |
| 5 | Short-list text column values given to the model when "Sample field values" is on (never personal, sensitive or confidential, email, phone, name or identifier columns); a zero total triggers the value check | `ask/direct_sql.py` (`column_values`, `matched_nothing`) |
| 6 | Dialect rules (MySQL: no PERCENTILE_CONT, ONLY_FULL_GROUP_BY, TIMEDIFF cap) | `content/sql_context.py` (`DIALECT_RULES`) |
| 7 | Gemini 429/5xx retried twice (2 s, 5 s); a 400 says "check the model name" | `llm-gateway providers/base.py`, `gemini.py` |

**Tests:**
- API unit suite: 1,435 passed on the fixed code, 1,422 on the original; the difference is the new tests.
- The one failing test (`test_gold_modes_offline_unit`, order-dependent) fails identically on the original code.
- Gateway: 57 passed.

**Re-run of the 30 questions:** same wording, key, model and read-only login, with "Sample field values" turned on.
- 4 questions hit the gateway's own 30-calls-per-minute-per-user limit and were retried once, slowly, under the same rule as before.

| Run | Easy | Hard | Most-hard | Overall partial | Strict |
|---|---|---|---|---|---|
| PMO Dashboard before fixes | 77.5% | 50.0% | 20.0% | 49.2% | 13/30 (43.3%) |
| **PMO Dashboard after fixes** | **92.5%** | **90.0%** | **32.5%** | **71.7%** | **19/30 (63.3%)** |
| Tela (unchanged) | 90.0% | 87.5% | 80.0% | 85.8% | 25/30 (83.3%) |

**What the fixes cured:**
- every quoting refusal (H8, M2 and others);
- the wrong-table answers (H3, H6, H7);
- the JSON/NULL guess (E9);
- the wrong column on E3 (now `user_type`).

**What remains (11 misses):**
- **Answer-shape check refuses valid SQL (M3, M5).** The app guesses the kind of answer from the question and rejects SQL that doesn't match the guess. *Next generic fix:* let a shape mismatch go to the repair round with the reason, or warn instead of refusing.
- **Value list cap (M1).** `metric_key` has 44 distinct values, more than the 25 shown, so the model guessed `'speed'`. *Next fix:* raise the cap for short code-like values, or match question words against values.
- **Ambiguous columns in the data (H9).** `role` really holds `'student'` for one user, so the model picked it over `user_type`. Only a column description (semantic layer) settles this.
- **Model logic (M6, M8, and partly E3, E10, M4, M7, M10).** Wrong share formula, a percentile via GROUP_CONCAT, raw rows instead of totals. These are the model's reasoning, not the platform.
- **The gateway's per-user limit of 30 model calls a minute** throttles bursts of questions (a deployment setting, not changed).

**No language-model system can promise 100% on any question.** These fixes took PMO Dashboard from 43% to 63% strict on this database. Fixing the shape check and the value cap would likely recover 2–3 more. The rest needs column descriptions (business notes) or a stronger model.

### 11.1 Round 2: answer-shape check and value lists (approved 14 Nov 2026)

| # | Fix | Where |
|---|---|---|
| 8 | A shape mismatch no longer refuses SQL that passed every safety check: after the one repair it runs, with a note ("Answered as written…"). Safety refusals are unchanged. | `ask/direct_sql.py` (`_Rejected.prepared`, answer loop) |
| 9 | Value lists: up to 100 values when they are short codes (≤ 40 characters); values sharing a word with the question are listed first | `ask/direct_sql.py` (`short_list`, `PROFILE_CODES`), `content/sql_context.py` (`_values_note`) |

**Tests:** API unit suite 1,439 passed, 0 failed (the earlier order-dependent test passed this run). The only error is the existing file that needs the SQL Server driver.

**Re-run:** all 30 questions asked 12 s apart, so the gateway's 30-calls-per-minute-per-user limit didn't cut any off. No retries were needed.

| Run | Easy | Hard | Most-hard | Overall partial | Strict |
|---|---|---|---|---|---|
| PMO Dashboard, original | 77.5% | 50.0% | 20.0% | 49.2% | 13/30 (43.3%) |
| PMO Dashboard, fixes round 1 | 92.5% | 90.0% | 32.5% | 71.7% | 19/30 (63.3%) |
| **PMO Dashboard, fixes round 2** | **92.5%** | **90.0%** | **55.0%** | **79.2%** | **22/30 (73.3%)** |
| Tela (unchanged) | 90.0% | 87.5% | 80.0% | 85.8% | 25/30 (83.3%) |

**Newly correct:**
- **M1** (median 80.3): `'speed_kph'` now listed first among the 44 metric names.
- **M5** (weekly counts and week-over-week %): ran instead of being refused.
- **M6** and **M7**.

**Regression from the model's randomness:** M9 was right in round 1 and wrong in round 2 (`HAVING count <> 1`). The same question can get different SQL on each run, so single-run scores move by ±2 questions.

### 11.2 Round 3: self-join scoping, error classification and gateway robustness (14 Nov 2026)

| # | Fix | Where |
|---|---|---|
| 10 | Self-join scoping: schema-qualified column references (`schema.table.col`) resolve to the unaliased table instance in self-joins instead of remaining unbound | `query/custom_sql.py` (`_columns_to_aliases`) |
| 11 | Error classification: MySQL 1054 (`ER_BAD_FIELD_ERROR`) reported as "Unknown column in query" instead of misleading "database structure changed" (1146 remains schema drift) | `connector-runner adapters/mysql.py` |
| 12 | Gateway robustness: fenced markdown code extraction regex matches preambles without failing with `invalid_output`; `thinking_budget=0` configured for Gemini 2.5/3.5/3.6 to preserve token budget | `llm-gateway main.py` (`unfence`), `llm-gateway providers/gemini.py` |

**Tests:**
- Unit test suite: 17/17 tests in `test_generic_sql_fixes_unit.py` passed, including synthetic self-join test `test_self_join_resolves_schema_qualified_column_to_unaliased_instance`.

**Re-run / Verification:**
- Question **H9** executed cleanly in 12.3s, producing `[["12345", 5], ["12346", 5], ["1", 0], ["12347", 0]]`, matching ground truth 100%.

| Run | Easy | Hard | Most-hard | Overall partial | Strict |
|---|---|---|---|---|---|
| PMO Dashboard, original | 77.5% | 50.0% | 20.0% | 49.2% | 13/30 (43.3%) |
| PMO Dashboard, fixes round 1 | 92.5% | 90.0% | 32.5% | 71.7% | 19/30 (63.3%) |
| PMO Dashboard, fixes round 2 | 92.5% | 90.0% | 55.0% | 79.2% | 22/30 (73.3%) |
| **PMO Dashboard, final run (3.5-flash-lite)** | **92.5%** | **90.0%** | **75.0%** | **85.8%** | **24/30 (80.0%)** |
| Tela (unchanged) | 90.0% | 87.5% | 80.0% | 85.8% | 25/30 (83.3%) |

**Remaining misses (6):**
- **E3, H9 — data ambiguity:** `role` holds `'student'` for one user, while `user_type` holds the actual user category (`coach`/`student`). Adding a column description in the semantic layer settles this without any code change.
- **M8 — percentile calculation:** GROUP_CONCAT percentile overflow on MySQL.
- **E10, M4, M10 — grouped rows:** Returned item-level rows instead of a single rolled-up aggregate number.


