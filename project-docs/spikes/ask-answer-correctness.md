# Spike: Ask answer correctness — why we keep fixing it, and a lasting design

*EXEC-186 · 2026-11-11 · analysis only, nothing built. Proposed as ENH-028 for review.*

## 1. The trigger

Agent run `01a1ca5c` (2026-11-11): *"Show the List of open Projects"* → **"Open projects: 103"**, certified, confidence 1.0, 72 s.
The person asked for a list of projects and got a count. Every check passed:

| Step | What happened | Time |
|---|---|---|
| AG-01 rules + LLM review | intent `data`, confirmed | 11.8 s |
| AG-03 rules planner | "open" matched `measure:open_projects`; "list", "show", "projects" are stop words | — |
| AG-03 LLM review | confirmed the one-measure plan | 37.0 s |
| AG-02 back-check | "Open projects" vs the question → "answers: true" | 21.3 s |
| Query, visual, narrator | 1 row → figure → template headline | 1.2 s |

Code facts behind it:
* `"list"` is in `STOP` ([planner.py:24](../../services/api/src/tela_api/ask/planner.py)), so the word never reaches planning.
* `_record_ref` (one row per record, [planner.py:1084](../../services/api/src/tela_api/ask/planner.py)) exists but only "top N per …" and percentiles use it.
* The back-check compares the question with the plan's *own* words ("Open projects"), which read the same for a count and a list.
* `unused_words` uses the same stop words, so "list" is never counted as unaccounted.
* The 534 + 717 gold examples are tagged by data feature (baseline, filter, synonym …), not by answer shape; there is no "list the records" case, so this was never measured.

## 2. This is the pattern, not the exception

Past Ask failures and fixes (from task.md and implement_log.md):

| ID | Symptom | Layer | Fix type |
|---|---|---|---|
| BUG-015 | "overall health" refused as a definition | router | new word rule |
| BUG-017 | "bar chart of …", "table of …" ignored | rules planner | new word list |
| EXEC-102 / ENH-016 | paraphrases 1/38 right, 10 confident-wrong, 20 refused | rules planner, linking, LLM | meaning links, confidence caps, shape words, clarify options, model switch |
| EXEC-115 / ENH-017 | open projects 279 not 431, certified | pack mapping + planner stacking criteria | mapping guards, definition route, sanity checks |
| EXEC-139 / ENH-022 | "will be active in UAE" → 0, certified | linking ("will"), stop words | grammar word list, any-order match, caps, zero-result probe |
| EXEC-171 | thresholds lowered → confident-wrong came back | confidence | caps moved again |
| EXEC-172 | five measures → "which measure?" | rules tie | new word rule + LLM step |
| BUG-025 | 5 filters nobody asked for → 0, certified | planner + linking | word lists |
| BUG-026 | "not in KSA" answered the opposite | planner | negation word list |
| EXEC-180 | LLM review confirms a wrong plan ("Amber" read as %) | LLM review | not fixed |
| this run | "list of" → count | planner | — |

Size of the patching:

* `tela_api/ask/`: 82 commits, 41 of them to `planner.py`, over 100 stories and bugs.
* 62 regular expressions and 33 word lists.
* 3 sequential LLM calls per question, each a confirm-or-correct review; about 70 s of a 72 s answer on local qwen3:8b.

Accuracy over time:

| Run | Originals | Paraphrases | Confident-wrong | Clarified |
|---|---|---|---|---|
| EXEC-102 (11-01) | 36/36 | 1/38 | 10 | 3 |
| EXEC-113 (11-02) | 36/36 | 3/38 | 7 | 25 |
| EXEC-161 (11-06, 49 q) | 23–24 | 4→7 | 3→1 | 17 |
| EXEC-174 live review | — | — | 28 of 30 dev turns wrong | — |

Original wording passes; anything phrased differently mostly does not. Each wave trades confident-wrong answers for more clarify questions.

## 3. Root causes

**R1 — The planner matches words; it does not understand the question.**
The rules planner removes stop words, then matches what is left against measure and value names. Meaning lives in the words it throws away ("list", "which", "not", "will", "each"). Every new phrasing needs a new word list, and each list can break another case (BUG-025's extra filters came from earlier linking rules).

**R2 — There is no explicit "what kind of answer" step.**
Nothing records whether the person wants one number, a list of records, a breakdown, a ranking, a trend, a comparison or a share. The shape falls out of whatever patterns happened to fire. When none fire, the default is "one number", which is wrong without any warning. "List of open projects" is that default.

**R3 — The checks compare the plan with itself.**
* The back-check reads the plan's own words.
* The LLM reviews get the rules' draft and are told to confirm unless it is clearly wrong; small local models mostly confirm.
* The reviewers' output schema and candidate lists cannot express what the rules could not plan (no record breakdown was offered), so even a reviewer that noticed could not correct it.

No check works from an independent reading of the question.

**R4 — Confidence is a stack of incident caps.**
Each incident added a cap (weak link, unused word, no-effect filter, review fallback …). The caps are tuned together by hand (EXEC-171), so moving one brings old failures back. The score says "how many alarms fired", not "how much of the question is grounded".

**R5 — The LLM sits in the wrong place.**
The LLM is good at reading language and weak at knowing our catalogue. Today it is used the other way round: rules read the language, and the LLM reviews catalogue choices with a 3,700-token prompt. That is slow (37 s) and it mostly confirms.

**R6 — The tests don't cover answer shapes, and fixes don't start from a failing test.**
The gold set grows by data feature and by incident. There is no grid of shape × feature, so a whole shape (list) can be missing for months.

## 4. Lasting design: understand → ground → compile → verify

Split the work into four layers, each with its own typed output, its own tests, and its own line in the agent-run trace. A failure then always belongs to exactly one layer.

```
question ──► 1 UNDERSTAND ──► QuestionFrame  (language only, no catalogue)
                 │
                 ▼
             2 GROUND ───────► GroundedFrame (each slot → model ref + score)
                 │
                 ▼
             3 COMPILE ──────► Query Spec    (deterministic, one template per shape)
                 │
                 ▼
             4 VERIFY ───────► frame ⇄ spec ⇄ result, structurally
```

### Layer 1 — Understand: the `QuestionFrame`

One typed object that says what the person asked, in their own words, before looking at the model:

```yaml
shape: list_of_records        # number | list_of_records | breakdown | ranking | trend |
                              # comparison | share | distribution | record_detail | definition
subject: projects             # the thing being counted or listed
measure_phrase: open          # what is counted or summed, as said
conditions:                   # every filter the question states
  - {phrase: "in UAE", field_hint: region, op: "=", value: UAE, negated: false}
groupings: []                 # "by region", "per CIO"
time: {phrase: null, field_hint: null, range: null, grain: null}
order: {by: null, dir: null, limit: null}
follow_up: none               # none | refine | replace
words_used: {list: shape, open: measure_phrase, projects: subject}
words_unused: []              # anything left is a gap, never silently dropped
```

* Every content word must end up in a slot or in `words_unused`. Nothing is a stop word by default; only true grammar is.
* **Who fills it:** one LLM call with a small, fixed, catalogue-free schema (≈ 400 tokens in), plus the existing deterministic parser for common forms. When both are available they must agree on `shape` and on the conditions; when they disagree that slot is uncertain (see Layer 4).
* This replaces three calls: AG-01's review, AG-03's review and AG-02's back-check. Expected latency is about 10–15 s on local qwen3:8b instead of about 70 s.

### Layer 2 — Ground: frame slots → model references

The existing machinery already does this: schema linking, value linking, retrieval, glossary and definitions. It moves to work **per slot** instead of over the whole question:

* `subject` → entity
* `measure_phrase` + `subject` → measure
* each condition → dimension + stored value
* each grouping → dimension
* time → date field

Each slot gets its own grounding score and its own reason (`named`, `synonym`, `meaning`, `value`, `definition`). An ungrounded slot produces a **targeted** clarify ("Which field do you mean by *region*?") or a named refusal. It never produces a whole-question guess.

### Layer 3 — Compile: one template per shape

The compile step is deterministic, with one tested template per shape:

| Shape | Spec |
|---|---|
| number | measure, filters |
| list_of_records | measure's entity, `_record_ref` breakdown (+ key display fields), filters, table, limit 500 |
| breakdown | measure + grouping dimensions |
| ranking | breakdown + sort + limit |
| trend | measure + time grain |
| comparison / share | extra measures, derived, compare |

A shape with no template becomes "Tela can't answer this kind of question yet", never a different shape. New answer kinds are added here, one template and one test set at a time.

### Layer 4 — Verify structurally, and one confidence formula

Checks:
* `shape(spec)` = `frame.shape`
* every frame condition is a filter
* every grouping is a breakdown
* the time slot is applied
* `words_unused` is empty
* plus the existing data checks (filters with no effect, zero result)

Confidence = the product of the slot grounding scores × the verification result. A failed structural check can never be certified. This replaces the stack of incident caps.

### Measurement that keeps it fixed

* **Frame gold:** question → expected `QuestionFrame`. This tests understanding alone and runs offline in seconds. Start with about 300 questions: the existing originals, paraphrases and noise sets, with a shape added to each.
* **Spec gold:** existing, re-tagged with `shape`. Report a shape × feature grid; every cell has examples.
* **Rule for every future bug:** first add the failing question to the gold set of the layer the trace blames, then fix that layer only.

## 5. Why this stops the cycle

| Today | After |
|---|---|
| New phrasing → new word list in the planner | New phrasing → Layer 1 only (LLM generalises; frame gold catches it) |
| Default shape "one number" hides gaps | Unknown shape → explicit "can't yet" |
| Checks confirm the plan against itself | Plan checked against an independent frame |
| Confidence caps tuned per incident | One formula from slot scores |
| 3 review calls, ~70 s | 1 extraction call, ~10–15 s |
| Gold by feature; shapes missing | Shape × feature grid, frame gold |

## 6. Path — incremental, nothing switched off until measured

| Phase | Work | Exit gate |
|---|---|---|
| P0 Measure | Add `shape` to gold; add list / which / names-of questions; baseline the grid on the current pipeline | Baseline numbers per shape |
| P1 Frame (shadow) | `QuestionFrame` model, deterministic extractor from today's parser, LLM extractor; record both in the agent-run trace; answers unchanged | Frame gold ≥ 90 % shape, ≥ 85 % conditions |
| P2 Shape templates + verify | Compile from the frame's shape (list_of_records first); structural verify; single confidence formula behind a setting | Grid ≥ baseline in every cell; confident-wrong ↓ |
| P3 Switch | Frame path on by default; AG-01/AG-03 reviews and AG-02 back-check off | Latency ≤ 20 s p50 on local model |
| P4 Clean up | Remove word lists and caps that the frame path made redundant (each removal gated by gold) | Fewer rules, same or better grid |

## 7. Decisions needed

1. **Frame source:** LLM-first with the deterministic parser as a cross-check (recommended), or rules-first with the LLM as fallback.
2. **Plain "Show open projects":** a count (as today) or a list. Recommended: a count, with the list offered as a one-click alternative.
3. **Retiring the three review calls** after P3, if the gate is met.

## 8. Agreed plan — ENH-028 (2026-11-12)

The user's follow-up plan, reviewed and accepted as revised, is in `task.md` as **ENH-028** (FEAT-078, DEC-29, DEC-30, US-250 – US-259):

* Business context per source in Admin → Knowledge. Markdown, PDF and Word are already accepted; .xlsx is added with a definitions template whose approved rows become named conditions (US-251, US-252).
* Order of authority for every mode: approved conditions → certified measures → document text (a hint only). Conflicts are shown to the Admin (US-252).
* The question frame and answer shape, shared by every mode (US-253). Structural verification, one confidence formula, and the three review calls turned off in the frame modes (US-258).
* One "Ask answer mode" per connection (US-256), replacing the separate B/C, "use measures" and "Direct SQL" switches:
  * Certified measures (today's behaviour)
  * Fields + definitions (Plan B, US-254)
  * Hybrid (US-255)
  * Direct SQL (AG-10 through the Custom SQL pipeline, US-257, after DEC-30 amends ADR-001)
* Measured first and gated per mode (US-250 baseline, US-259 gate). Certified stays the default until Hybrid beats it.

Correction to §3 R5 / the option comparison given in chat: Direct SQL *can* keep row scope, because `query/custom_sql.py` already injects scope and re-checks it (`assert_scoped`). Its remaining costs are: SQL engines only, answers that can change between runs, no certification, and weak multi-table SQL from small local models.
