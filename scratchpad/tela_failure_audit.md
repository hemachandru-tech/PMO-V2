# Tela failure audit: `ball-report` benchmark (14 Nov 2026)

**Scope.** Tela's five misses out of 30 (E3, H3, H9, M4, M5), run on the new Gemini key (`gemini-3.5-flash-lite`) with the read-only `pmo_ro` login.

**Evidence:**
- Tela's API responses with traces: `work/tela_answers.json`;
- its pipeline trace and app log: `work/post_test_state2/logs/`;
- the code in `tela/backend/app/`;
- re-running Tela's own SQL read-only as `pmo_ro`.

## Summary

| Q | Score | What went wrong | Root-cause class | Tela caught it? |
|---|---|---|---|---|
| E3 | 0 | Filtered `role='coach'` instead of `user_type`, giving 0 and 0 | Value grounding: no column descriptions or values sent | **No.** The self-check skips multi-row results |
| H3 | 50 | Grouped by `student_id, student_name`, splitting two students | Model logic: added a grouping key the question didn't ask for | No. No check for duplicated keys |
| H9 | 25 | Self-join on `s.coach_id = u.coach_id` from the student side | Model logic: wrong join | No. Confidence 0.9 |
| M4 | 0 | Looked for `'done'` in `status_code` (values P/C) instead of `stage`, giving NULL | Value grounding | **No.** The self-check skips an aggregate over a subquery |
| M5 | 0 | Wrong weeks, then MySQL error 1055 three times, then a misleading "which table?" | Date reasoning + **real DB error hidden from the model** + misleading message | Tried 3 times, blind |

Two misses are the model's own reasoning (H3, H9). The other three were made worse by **gaps in Tela itself**, which are fixable:
1. **Nothing describes a newly connected database.** Bootstrap approves tables and nothing more.
2. **The answer self-check has blind spots.**
3. **MySQL error text is thrown away** before the retry loop sees it.
4. **The final message misleads the user.**

## Per question

### E3: active coaches and students (expected 4 and 12; got 0 and 0)
```sql
SELECT 'Active Coaches' AS label, COUNT(*) AS value FROM user_master_demo WHERE LOWER(role) = 'coach' AND is_active = 1
UNION ALL
SELECT 'Active Students' AS label, COUNT(*) AS value FROM user_master_demo WHERE LOWER(role) = 'student' AND is_active = 1
```
- **What the model saw:** the retrieval trace shows `user_type` and `role` with **empty descriptions** and no example values. Tela had never profiled `ball-report`, because `bootstrap.py` only approves tables.
- **The data:** `role` is mostly NULL, otherwise admin / head_coach / student; `user_type` is always coach or student.
- **Why it wasn't caught:** the self-check re-asks when an answer "matched nothing". But `answer_check._matched_nothing` (`answer_check.py:126-135`) returns False unless the result is a **single row** with no `GROUP BY`. `UNION ALL` gave 2 rows, both 0, so there was no re-ask.
- **Confidence reported:** 1.0, with no caveats.

### H3: average release speed per student (half right)
```sql
SELECT student_id, student_name, AVG(release_speed_kmh) ... GROUP BY student_id, student_name
```
- The question said "per student_id". The model added `student_name`.
- The same id has different names on different rows (10001 is "Sathish" and NULL; `default` is "Test", "Sathish" and NULL), so two students were split into several rows with wrong averages. The other 5 are correct.
- **Why it wasn't caught:** no check compares the grouping with the entity the question asked about. Confidence 1.0.
- **Contributing factor:** poor data quality (inconsistent names for one id).

### H9: active students per coach (expected 1→0, 12345→5, 12346→5, 12347→0)
```sql
FROM user_master_demo u LEFT JOIN user_master_demo s ON s.coach_id = u.coach_id AND s.is_active = 1
WHERE LOWER(u.user_type) = 'student' OR NOT u.coach_id IS NULL GROUP BY u.coach_id, u.full_name
```
- It should start from coaches and join `s.coach_id = u.user_id`. Instead it starts from students, labels student names as `coach_name`, and drops the coaches who have no students.
- The question's own wording ("including coaches with none") went unmet, and nothing checks for that.
- Confidence 0.9.

### M4: first callback to last "done" (got NULL, NULL)
```sql
... MAX(CASE WHEN LOWER(c.status_code) = 'done' THEN c.created_at END) AS last_done_at
    FROM delivery_report d JOIN callbacks c ON d.delivery_id = c.delivery_id ...
```
- `status_code` holds only `P` and `C`; "done" is a value of `stage`. As with E3, the model never saw either column's values.
- **Why it wasn't caught:** the result was one row of NULLs, but `_matched_nothing` also requires a `WHERE` on the **outer** query (`answer_check.py:130`). Here the `WHERE`/`HAVING` sit inside a subquery, so there was no re-ask.
- **Latent bug even with the right column:** `TIME_TO_SEC(TIMEDIFF(...))` caps at 838:59:59, about 3.0 million seconds. The real maximum is 6.1 million seconds, so the maximum would still have been wrong.

### M5: requests in the last 4 ISO weeks (no answer)
1. **Wrong period.** The model filtered `2026-09-28 … 2026-10-26`. The 4 full weeks before the week of Sat 14 Nov are 12 Oct – 8 Nov. Even if the SQL had run, the answer would have been wrong.
2. **The SQL failed on MySQL.** Re-running Tela's SQL as `pmo_ro`: `ERROR 1055 … contains nonaggregated column 'ball-report.user_activity.ts' … incompatible with sql_mode=only_full_group_by`. It selects `CAST(YEARWEEK(ts,3) AS CHAR)` but groups by `YEARWEEK(ts,3)`, and MySQL treats those as different expressions.
3. **The retries were blind.**
   - `executors.py:47-53` replaces every MySQL error except timeouts and permissions with **"The query could not be executed."**
   - The retry loop (`pipeline.py:476-483`) sends that sentence to the model as "the database returned this error".
   - `app.log` shows exactly that: `Pipeline self-correcting (attempt 1/3): The query could not be executed.`
   - The model never learned it was a GROUP BY problem, so 3 attempts made 3 failures.
4. **The message to the user is wrong.** After 3 failures, `pipeline.py:487-497` always returns "I may have used the wrong table or column name" and lists tables, whatever the real error was. The SQL clearly used `user_activity`.
5. **Nobody could diagnose it afterwards.** The pipeline trace stores only `"error": "QueryError"`, and the log only the generic sentence. I had to re-run the SQL to find error 1055.

Also: Tela's planner prompt (`prompts/planner_v1.txt`) says nothing about MySQL's `ONLY_FULL_GROUP_BY`. The date context came only from the question text; Tela doesn't put a reporting date into the prompt itself.

## Tela defects found (fix list, most valuable first)

| # | Defect | Evidence | Effect | Fix |
|---|---|---|---|---|
| 1 | Real MySQL / ClickHouse error text discarded before self-correction | `executors.py:47-53`, `:133-139`; `pipeline.py:476-483`; app.log | Self-correction can't fix syntax or GROUP BY errors on MySQL | Pass the DB message (minus secrets) to the planner; keep the friendly text only for the user |
| 2 | Misleading final message after failed retries | `pipeline.py:487-497` | User told "wrong table" for any error | Say what failed (e.g. "MySQL rejected the grouping"); clarify only for real grounding errors |
| 3 | No value grounding for a new database | trace: empty descriptions; `bootstrap.py` approves tables only | Wrong-column filters (E3, M4) | Run profiling/value sampling after connecting, or send top-N distinct values for low-cardinality columns |
| 4 | Self-check skips multi-row all-zero results | `answer_check.py:132-135` (`len(rows) != 1`) | E3's 0/0 shown as fact | Treat "every numeric cell 0/NULL" as matched-nothing for any small result |
| 5 | Self-check skips aggregates whose filter is in a subquery | `answer_check.py:130` (outer `WHERE` required) | M4's NULL/NULL shown as fact | Check the whole tree for WHERE/HAVING, and treat an all-NULL aggregate row as empty |
| 6 | Uncalibrated confidence | 1.0 on E3, H3 and M5's attempts; 0.9 on H9, M4 | Users can't tell a wrong answer from a right one | Lower confidence when the self-check flags, or don't show the model's self-rating |
| 7 | DB error not kept in the trace | pipeline_trace.jsonl has only `"QueryError"` | Failures can't be diagnosed | Store the error code and message (redacted) in the trace |
| 8 | No dialect guidance for strict GROUP BY | `planner_v1.txt` | Avoidable 1055 errors | One rule: "MySQL ONLY_FULL_GROUP_BY: group by exactly the selected expressions" |

## Would configuration alone have helped? (no code change)
- **E3 and M4: very likely.** Add one-line descriptions in Tela's semantic layer:
  - `user_master_demo.user_type` = coach / student;
  - `callbacks.stage` = pipeline step incl. done;
  - `callbacks.status_code` = P / C.

  Or run Tela's profiling on `ball-report`.
- **H3 and H9: possibly.** A business note ("a student is identified by student_id; a coach's students have coach_id = coach.user_id") would likely help. Not guaranteed.
- **M5: no.** It needs defect 1 fixed; the date-range error is the model's own.

## Is the scoring fair?
- Yes. Each miss produced a wrong number or no number for what was asked.
- Partial credit was given where part of the answer was right: H3 at 50 (5 of 7 students right) and H9 at 25 (right counts for two coaches, wrong structure).
- With defects 1, 3, 4 and 5 fixed, a realistic Tela score on this benchmark would be about 27–29 of 30. That is an estimate, not a measurement.
