# Tela vs ground truth: 12 PMO questions, after the model builder (15 Nov 2026)

## Summary of all rounds

| Round | Model / planner | Pass |
|---|---|---|
| 1 | starter-pack model v1, hardcoded prompt, gemini-3.5-flash-lite | 1 / 12 |
| 2 | v3 from the model builder (rules + glossary), flash-lite | 5 / 12 |
| 3 | v4 (+ dashboard definitions), flash-lite | 4 / 12 |
| 4 | v4, planner gemini-3.5-flash | 3 / 12 (+2 partial) |
| 5 | v5 (rules + glossary) + post-SQL rule check, flash-lite, cache and conversations cleared | 7 / 12 (+2 partial) |
| **6** | **v7 (rules + glossary + TPG dashboard rules) + rule check, flash-lite, caches cleared** | **7 / 12 (+4 partial, 1 wrong)** |

## Round 7: v8, rules read twice and merged: 6 / 12 (+3 partial)

The builder now reads the documents twice (parts of 7,000 and 4,500 characters) and keeps, per term and table, the
checked rule of either read. Coverage went up: 121 rules (108 checked), the "open" population on all seven tables,
Amber back as a display rule, blanks shown as "(not set)" for CIO and region. End-to-end answers did not improve:

| # | v7 | v8 | Note |
|---|---|---|---|
| 1, 3, 4, 10, 11, 12 | ✅ | ✅ | |
| 2 | ◐ | ◐ | this run dropped reason and comment and showed the region group instead of the region |
| 5 | ◐ | ◐ | Amber in the overall column (9 rows); 7 Yellow remain in the other colour columns |
| 6 | ◐ | ❌ | one row: a blank project name with 12 |
| 7 | ✅ | ❌ | five bands right, but open projects instead of Active this run |
| 8 | ❌ | ❌ | |
| 9 | ◐ | ◐ | |

**Finding.** Answers for the same question now vary from run to run more than they improve: Q7 was exactly right
on v7 and wrong on v8, Q6 the reverse of better. Two causes, both measurable:
1. **More rules, more noise.** Display rules are enforced whenever their column is shown, so a derived "region group"
   replaced the plain region in Q2 and Q8 where nobody asked for it. A display rule should be enforced only when it
   relabels values of the same column (Yellow→Amber, blank→"(not set)"), not when it groups them.
2. **Run-to-run variation of the SQL model.** gemini-3.5-flash-lite writes different SQL for the same question and
   context on different runs.

**Recommended next step:** measure stability rather than single runs. Run each of the 12 questions 3 times per model
version and count a question right only when all 3 agree with the ground truth; fix the display-rule enforcement
above; keep the round-6 (v7) model as the baseline, since it scored best.

## Round 6: v7 with the TPG dashboard rules document

`scratchpad/TPG_dashboard_rules.md` (written as rules, Dataverse names) was added as the third document. Fixes this
round: a table written as one quoted name with a dot (`` `tpg.tpg_project` ``) is now recognised by the rule check (Q4
had slipped through), and the builder is told to keep a measure's record restriction inside its own rule.

| # | Ground truth | App | Result |
|---|---|---|---|
| 1 | 22 | 22 | ✅ |
| 2 | 8 On Hold + reason, hold date | 8 On Hold, comment, hold date, days on hold | ◐ no reason label |
| 3 | 88 | 88 | ✅ |
| 4 | 39, 20, 8, 5, 4, 3, 3 … | 39, 20, 8, 5, 4, 3, 3 … | ✅ |
| 5 | 95 open, 4 colours, Amber | 95 open, 3 colours, no Amber | ◐ issue KPI left out; the Amber display rule was held for review in this generation (no table given) |
| 6 | 4, 3, then 5 projects with 1 | the same 7 + Direct Clearing Upgrade (a Completed project) | ◐ counted change requests of a closed project too |
| 7 | 6 / 35 / 22 / 22 / 3 | 6 / 35 / 22 / 22 / 3 | ✅ Active only, five bands |
| 8 | 22 traced projects with legs | 95 open projects | ❌ the leg rules exist but the register (join + traced filter + legs) is not assembled |
| 9 | top 20 by lenses | 10 Strategic open requests | ◐ one lens only; the ranking across lenses is not built |
| 10 | 9 | 9 | ✅ |
| 11 | 16 | 16 | ✅ |
| 12 | 4 | 4 | ✅ |

**Where it stands.** The questions that depend on one table's rules are now right. What remains is multi-step logic:
a ranking over several conditions (Q9), a register that joins two tables and computes several legs (Q8), and
"per open project" across a join (Q6). The builder also varies run to run (Amber was extracted in round 5, not in
round 6). Next steps: generate twice and keep the union of checked rules, and add a rule kind for registers and
rankings that names its join and sort, so the check can enforce them too.

## Round 5: v5 + rule check, gemini-3.5-flash-lite: 7 / 12 (+2 partial)

Before the run: `chat.turn_events`, `chat.turns`, `chat.conversations` (and the tables that reference them),
`ops.result_cache_index` and Redis were emptied. `tpg_program` (added in Model studio, three open currency questions)
was removed from the draft and its questions dismissed before approving v5.

| # | Ground truth | App | Result | Note |
|---|---|---|---|---|
| 1 | 22 | 22 | ✅ | |
| 2 | 8 On Hold, reason, hold date | 8 On Hold, hold date, comment, days on hold | ◐ | the reason label column is not selected this run (it was in the run before) |
| 3 | 88 | 88 | ✅ | |
| 4 | open by CIO: Giselle Nightingale 39, Ilsa Abernethy 20, Greta Jarrold 4 … | same | ✅ | the rule check added the "Open Projects" population |
| 5 | 95 open projects, Amber | 95 rows, Amber | ✅ | population and Amber both enforced by the check |
| 6 | 4, 3, 1 … | 4, 3, 2 … | ◐ | top two right; still counts Initial Baseline PCRs (no rule makes that exclusion "always") |
| 7 | Active only, 5 bands | open projects, 3 bands | ❌ | "only Active" and the 5 bands are defined only in the dashboard file |
| 8 | 22 traced projects with journey legs | 95 open projects | ❌ | journey legs are defined only in the dashboard file |
| 9 | top 20 of the Strategic / Critical / High open requests | top 20 open requests by budget | ❌ | the lens ranking is defined only in the dashboard file |
| 10 | 9 | 9 | ✅ | |
| 11 | 16 | 16 | ✅ | the "critical path" rule brought in the tasks table |
| 12 | 4 | 4 | ✅ | Part A open requests + HLE / DOU / SOW gates |

**What made the difference: the post-SQL rule check** (`services/api/src/tela_api/ask/rule_check.py`). After the SQL
passes the safety checks it is compared with the model's rules: always-rules on the queried tables, the table's
population (unless the question names its own status), display rules, and rules the question names by phrase. What is
missing goes back to the model once, by name; the repaired SQL is kept only if it passes the checks and misses fewer
rules. No rule text is in code: everything comes from the approved model.

**The remaining three (Q7, Q8, Q9)** need definitions your two documents don't contain. A short document stating them
(which projects count for "days since last edited" and its bands, what a journey leg is, how the Top 20 is ranked),
written as rules rather than as dashboard widget text, would let the builder turn them into checked rules.

## Round 4: v4 with gemini-3.5-flash as planner: 3 / 12 (+2 partial)

| # | GT | App | Verdict | Why |
|---|---|---|---|---|
| 1 | 22 | 22 | ✅ | |
| 2 | 8 + reason, hold date | right 8 with names, no reason or hold date | ◐ | left out the reason and date columns the question asks for |
| 3 | 88 | 88 | ✅ | |
| 4 | open by CIO (39 …) | all projects (46 …) | ❌ | the "Open Projects" population rule was in the prompt and not applied |
| 5 | 95 open, Amber | Amber ✅ but 100 rows of all projects | ❌ | display rule applied now; population still not |
| 6 | 4, 3, 1 … | 4, 3, 2 … | ◐ | top two right; still counts Initial Baseline PCRs |
| 7 | Active, 5 bands | all projects, 4 bands | ❌ | dashboard definition ignored |
| 8 | journey legs | plain project list | ❌ | definition ignored |
| 9 | top 20 priority open requests | top 20 by budget, incl. Approved | ❌ | lens definition ignored |
| 10 | 9 | 9 | ✅ | |
| 11 | 16 | 88 | ❌ | ignored the "critical path" rule; counted Active projects |
| 12 | 4 | 7 | ❌ | starter-pack agreed term "Open demand requests" (old list) was not caught by the clash filter (term words differ from "Open Requests") |

**Conclusion.** The generated model is correct (rules, populations, displays, names, data date, value checks), and
the right rules reach the prompt. Answers still miss because the SQL model does not apply the rules it is given, with
either Gemini model. Prompt-only guidance has reached its limit: the next step must be enforced in code. After the SQL
is written, check it against the selected rules (a population, a display, a named term whose condition is absent from
the SQL) and send it back once with the specific omission; plus make the clash filter match agreed terms by
meaning, not exact words.

## Round 3: model v4 (rules + glossary + dashboard definitions): 4 / 12, worse than v3

v4 added a third document, `scratchpad/PMO_dashboard_definitions.md`, extracted from the dashboard HTML. Generation
read 249 rules; the column and value checks held 53 of them (25 used values the data never holds, such as
`statecodename = 'Open'`). Rule kinds were added (population, display), plus a dialect fact (never compare dates with
''), precedence of business rules over clashing agreed terms, documents read in the order picked, and one population
per table.

| # | v3 | v4 | v4 problem |
|---|---|---|---|
| 1 | ✅ | ✅ 22 | |
| 2 | ✅ | ❌ | right 8 projects, but dropped the reason and hold-date columns |
| 3 | ✅ | ✅ 88 | |
| 4 | ❌ | ❌ | ignored the "Open Projects" population rule that was in the prompt |
| 5 | ❌ | ❌ | same; ignored the Amber display rule |
| 6 | ❌ | ❌ | wrote values the column doesn't hold ('Approved', 'Rejected') |
| 7 | ❌ | ❌ | open instead of Active; made-up bands |
| 8 | ❌ | ❌ | ignored the journey-leg definitions |
| 9 | ❌ | ❌ | ignored the lens definitions |
| 10 | ✅ | ✅ 9 | |
| 11 | ✅ 16 | ❌ 40 | joined on name text; lost the brackets around an OR |
| 12 | ❌ | ❌ 11 | used the overridden open-request list, though the right rule was in the prompt |

**What this shows.** The right rules are now in the model and in the prompt. The misses are the SQL model not
following them, and they got worse as the prompt grew (v4 sends up to 40 rules per question). This is a model capacity
limit of gemini-3.5-flash-lite, which AI settings itself flags as below the recommended planner. Next steps, in order:
(1) switch the planner model to a stronger one in AI settings and rerun (no code change); (2) send fewer, more
relevant rules per question; (3) check generated SQL against the business rules after drafting (for example a
population that should have been applied) and repair once.

---


Ground truth: `scratchpad/groundtruth_tpg_12q.md` (TelaAdditional2.md + TelaGlossary2.md, data date 2026-10-25).
Correction: Q3 is **88**. Part B rule 1 ("bare 'active' = Active") is not one of the two Part A/B conflicts the
document lists, so it stands. The first report's 95 was wrong.
App: tela.localhost:8088, `tpg-mysql`, Direct SQL, gemini-3.5-flash-lite, **model v3 generated by the new model
builder** from both documents (54 checked rules, 282 described columns, 127 always-empty columns hidden).

**Score: 5 / 12 pass (was 1 / 12).**

| # | Ground truth | App (v3) | Verdict | Why it still fails |
|---|---|---|---|---|
| 1 | 22 | 22 | ✅ Pass | |
| 2 | 8 On Hold projects, reason, hold date | 8 rows, `tpg_subject` names, reason label, hold date, comment | ✅ Pass | |
| 3 | 88 | 88 | ✅ Pass | |
| 4 | open projects by CIO: Giselle Nightingale 39 … (not set) 2 | Giselle Nightingale 46 … blank 3 | ❌ | No open-project filter. The document says "how many projects" means open projects (Part B rule 1 / 35), but the builder did not extract that as an always-on default population. Blank not shown as "(not set)". |
| 5 | 95 open projects, Amber | 100 rows of all projects, "Yellow" | ❌ | Same missing default population; the row cap of 100 cuts it; Yellow→Amber display rule is not applied to Direct SQL output. Names are right now. |
| 6 | Streamlined Billing Track 809: 4, Zonal Archive Upgrade: 3, … | Zonal Archive Upgrade: 3 only | ❌ | Picked the document's "PCR in progress" rule (awaiting statuses, created in the last 28 days) instead of "Open change" minus Initial Baseline. The question fits both readings. |
| 7 | Active projects by days since edit, 5 bands | All projects, 4 bands, data date right | ❌ | "Only Active projects" and the 5 bands come from the dashboard, not the documents. |
| 8 | 22 projects with journey legs | 100 plain project rows | ❌ | "Journey legs" is defined only in the dashboard HTML, not in the documents. |
| 9 | Top 20 of 27 Strategic/Critical/High open requests | 1 row (demand priority filter) | ❌ | "Top 20 Strategic" lenses are defined only in the dashboard HTML. |
| 10 | 9 due in 90 days (5 / 7 / 9) | 9 | ✅ Pass | |
| 11 | 16 | 16 | ✅ Pass | |
| 12 | 4 | error | ❌ | Compared a datetime column with `''` (MySQL refuses it); the one run repair did not fix it. It also used the Part B open-request list. |

## What changed in the app

- **Model builder** (Add connection → Read schema → **Choose columns → Knowledge → Generate model**): the Admin picks
  tables and columns (select all / per table / per column), optionally picks or uploads documents, and the LLM writes
  descriptions and business rules. Every rule's SQL is parsed and checked against the real columns; anything else is
  held for review and never used. Columns with no value in any row are counted (no values read) and hidden. A preview
  shows rules, tables and columns; approval uses the existing approval page.
- **Direct SQL** no longer has PMO-specific instructions in code. It gets the data date, column descriptions, and all
  checked business rules of the tables it queries. On a database error it repairs once with the error code.
- **Answer card**: a "Context used" section shows the data date, tables, business rules applied, conversation memory
  and prompt size.

## What would move the remaining 7

1. Q4, Q5, Q7: the default population ("projects" means open projects). Rerunning the builder (its output varies run
   to run) or adding that one rule by hand in Model studio fixes these.
2. Q8, Q9, Q7 bands: add the dashboard tab definitions (Journey legs, Top 20 lenses, ageing bands) as a knowledge
   document, then regenerate.
3. Q12: a dialect rule for date columns (never compare with '').
4. Q6: one rule saying which definition "awaiting approval" uses.
5. Display rules (Yellow → Amber, "(not set)") in Direct SQL output.
