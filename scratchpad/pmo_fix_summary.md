# PMO Dashboard: what was fixed, before and after

## The test
We asked PMO Dashboard 30 questions about one of our databases (10 easy, 10 hard, 10 very hard) and checked each answer against the correct figure from the database itself.

## Before: 13 of 30 right
PMO Dashboard turns a question into SQL with an AI model, checks the SQL for safety, then runs it. Most wrong answers weren't the AI being "stupid": PMO Dashboard gave the AI poor information or blocked good answers.

| Problem | What happened |
|---|---|
| 1. Odd database names broke the SQL | The database name has a hyphen. PMO Dashboard showed it to the AI wrongly, so the AI wrote SQL that couldn't be read. |
| 2. A bug added the database name twice | Its safety check then couldn't recognise the table and refused the question. |
| 3. Retries were blind | When SQL failed, the AI was only told "it failed", never why, so its retry couldn't fix anything. |
| 4. The AI couldn't find the right table | Table search matched words exactly: "side-angle" never matched a table called "sideangle". |
| 5. The AI never saw real values | It had to guess whether completed rows say "done" or "C", or which column holds "coach". |
| 6. No database-specific rules | The AI used functions MySQL doesn't have. |
| 7. No retry when Google was busy | A "too many requests" reply from Google became a failed answer. |
| 8. Good answers blocked by a guess | PMO Dashboard guesses what kind of answer a question wants and refused correct SQL that didn't match the guess. |
| 9. Long value lists hidden | A column with 44 names showed none, so the AI guessed the wrong one. |

## The fixes (all generic)
1. Names are written the way each database needs them, for example with quotes around a name that contains a hyphen.
2. The double-name bug is fixed, and unusual names are cleaned up automatically before checking.
3. When SQL fails, the AI is told the exact reason, so its one retry can fix it.
4. Table search matches joined and split words ("side-angle" ↔ "sideangle", "deliveries" ↔ "delivery"). Small databases also list all their tables.
5. The AI is shown each column's real values, but only when the admin turns on "Sample field values", and never for names, emails, phone numbers or other personal data.
6. The AI is told each database's rules (MySQL, PostgreSQL, SQL Server, DuckDB).
7. When Google says "busy", it waits and tries again, up to twice.
8. If the answer's shape doesn't match the guess, the SQL still runs, with a note. Safety refusals are unchanged.
9. Longer lists of short codes (up to 100) are shown, with the ones that match the question first.
10. When queries compare a table to itself (self-join), the safety check properly links table names so column names are never lost.
11. Accurate database error messages: when a column is missing or unknown, it says so clearly instead of falsely claiming "the database structure changed".
12. LLM response reliability: JSON markdown blocks are safely extracted and thinking budgets are configured so responses never get cut short.

**Nothing is hard-coded.** The code contains no table names, column names, values or questions from our database. Everything is read from whatever database is connected, so the same fixes help any database. The new tests use invented names such as "shop-data" and "sidecam_run".

## After: 24 of 30 right (80.0% strict, 85.8% partial)

| Evaluation Stage | Easy (10) | Hard (10) | Very Hard (10) | Fully Right | Strict % | Partial % |
|---|---|---|---|---|---|---|
| Before Fixes | 7 / 10 | 5 / 10 | 1 / 10 | 13 / 30 | 43.3% | 49.2% |
| Round 1 (fixes 1–7) | 8 / 10 | 9 / 10 | 2 / 10 | 19 / 30 | 63.3% | 71.7% |
| Round 2 (fixes 8–9) | 8 / 10 | 9 / 10 | 5 / 10 | 22 / 30 | 73.3% | 79.2% |
| **Final Run (3.5 Flash Lite)** | **8 / 10** | **9 / 10** | **7 / 10** | **24 / 30** | **80.0%** | **85.8%** |
| Tela (for comparison) | 9 / 10 | 8 / 10 | 8 / 10 | 25 / 30 | 83.3% | 85.8% |

## What's still wrong (6 questions)
- **Ambiguous source data (E3, H9):** In the source database, `role` and `user_type` both contain the word "student" for different purposes. The AI filtered `role` instead of `user_type`. A one-line column description in the semantic layer settles this without code changes.
- **Percentile via Group Concat (M8):** The AI attempted a MySQL percentile calculation using string concatenation that exceeded buffer size.
- **Grouped summaries instead of scalar totals (E10, M4, M10):** The AI returned full breakdowns or delivery-level rows instead of a single rolled-up aggregate.

## Safety and quality
- All safety checks remain: read-only access, row-level permissions, signed queries.
- 1,439 automated tests pass. The only failing test failed before the changes too.
- All changes are in a local git repository, so every line can be reviewed with `git diff`. Nothing is published.

## Honest note
No AI-based system can guarantee 100% correct answers to every possible question. These fixes removed the platform's own mistakes. What remains is mostly the AI model's reasoning and unclear data.
