"""US-259 offline: the three rule-based Ask modes on the same US-250 gold questions (no database, no LLM).

Certified (today's rules planner), Fields + definitions (Plan B, US-254) and Hybrid (US-255) plan every draft example
of the PMO pack on the synthetic extract in DuckDB, graded by rows and answer shape as ``gold.evaluate_one`` grades
them, and reported per set and per shape. Direct SQL needs a model, so it has no offline score (US-259 live run).
A conversation's follow-up is planned by Certified as Ask plans it; Fields and Hybrid plan its last message as asked
(their follow-ups go through the rewrite, which needs the database). The floors are this run's (EXEC-203).
"""

from __future__ import annotations

from typing import Any

import duckdb
from test_fields_planner_unit import plan_b
from test_fields_planner_unit import run as run_adhoc
from test_gold_scoring_unit import db as db  # the extract in DuckDB (a pytest fixture)
from test_gold_scoring_unit import run, score
from test_gold_scoring_unit import snapshot as snapshot

from tela_api.ask import confidence
from tela_api.knowledge import scoring
from tela_api.knowledge.gold import pack_draft, spec_diff
from tela_query_spec.model import ModelSnapshot


def frame_score(db: duckdb.DuckDBPyConnection, duck: ModelSnapshot, e: dict[str, Any], *,
                hybrid: bool) -> dict[str, Any]:
    p = plan_b(e["question"], hybrid=hybrid)
    expected = e["spec"]
    actual = p.spec if p.status == "ok" else None
    shape = scoring.answer_shape(expected)
    wrong_shape = scoring.shape_diff(shape, actual)
    diff = spec_diff(expected, actual) + wrong_shape
    out: dict[str, Any] = {"question": e["question"], "tags": e.get("tags") or [], "spec_match": not diff,
                           "diff": diff, "values_linked": None, "retrieval": None, "shape": shape,
                           "status": p.status,
                           "confident_wrong": p.status == "ok" and p.confidence >= confidence.threshold(None)}
    if actual is None:
        return {**out, "passed": False, "error_class": scoring.classify(diff, status=p.status)}
    if not diff:
        return {**out, "passed": True, "error_class": None, "confident_wrong": False}
    want, got = run(db, duck, expected), run_adhoc(db, duck, actual)
    ok = not wrong_shape and scoring.frame_match(want, got, shape=shape, ordered=scoring.ordered_spec(expected))
    return {**out, "passed": ok, "confident_wrong": out["confident_wrong"] and not ok,
            "error_class": None if ok else scoring.classify(diff, expected=expected, actual=actual)}


def outcome(results: list[dict[str, Any]]) -> dict[str, int]:
    return {"right": sum(r["passed"] for r in results), "confident_wrong": sum(r["confident_wrong"] for r in results),
            "asked": sum(not r["passed"] and r["status"] == "clarify" for r in results),
            "refused": sum(not r["passed"] and r["status"] == "unsupported" for r in results),
            "total": len(results)}


def subset(results: list[dict[str, Any]], tag: str, *, without: str | None = None) -> dict[str, int]:
    return outcome([r for r in results if tag in r["tags"] and (without is None or without not in r["tags"])])


def test_the_rule_based_modes_on_the_same_questions(db: duckdb.DuckDBPyConnection,
                                                    snapshot: ModelSnapshot) -> None:
    examples = pack_draft()
    by_mode = {
        "certified": [score(db, snapshot, e["question"], e["spec"], e.get("tags") or [], e.get("turns"))
                      for e in examples],
        "fields": [frame_score(db, snapshot, e, hybrid=False) for e in examples],
        "hybrid": [frame_score(db, snapshot, e, hybrid=True) for e in examples],
    }
    report: dict[str, dict[str, Any]] = {}
    for mode, results in by_mode.items():
        originals = [r for r in results if not {"paraphrase", "noise", "exec186"} & set(r["tags"])]
        report[mode] = {"all": outcome(results), "originals": outcome(originals),
                        "paraphrases": subset(results, "paraphrase"), "lists": subset(results, "list"),
                        "counts": subset(results, "count")}
    print("\nmode       set          right  conf-wrong  asked  refused  total")
    for mode, sets in report.items():
        for name, o in sets.items():
            print(f"{mode:10} {name:12} {o['right']:5}  {o['confident_wrong']:10}  {o['asked']:5}  "
                  f"{o['refused']:7}  {o['total']:5}")
    assert {r["all"]["total"] for r in report.values()} == {len(examples)}
    for mode, floor in FLOORS.items():
        assert report[mode]["all"]["right"] >= floor["all"], (mode, report[mode]["all"])
        assert report[mode]["lists"]["right"] >= floor["lists"], (mode, report[mode]["lists"])


# EXEC-203 (2026-11-12), master da57dc7 + this branch, pack 2.2.1 / gold 0.10.0, rules frame, no LLM.
# Right / confident-wrong: Certified 447 / 120 (originals 124 / 1, paraphrases 37 / 46, lists 0 / 44, counts 40 / 4);
# Fields 240 / 54 (originals 66 / 16, paraphrases 10 / 6, lists 35 / 1, counts 34 / 2); Hybrid 244 / 50 (originals
# 67 / 15, paraphrases 10 / 6, lists 35 / 1, counts 34 / 2). Offline, Hybrid misses the gate (originals and
# paraphrases below Certified): Certified stays the default (DEC-29).
FLOORS = {"certified": {"all": 447, "lists": 0}, "fields": {"all": 240, "lists": 35},
          "hybrid": {"all": 244, "lists": 35}}
