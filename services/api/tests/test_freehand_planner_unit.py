"""Free-hand planner (ENH-008 US-164; FEAT-060 / FEAT-018), no database server.

Each question is planned the way ``Orchestrator.plan_for`` does for a person with free-hand access — the rules
planner, then ``freehand.choose`` — against the PMO pack bound to the synthetic TPG fixture. The free-hand rule is
then validated with the grant, compiled for DuckDB and run on the fixture's rows (the US-163 fixtures), and the
number is compared with the same rule written in Python. The gold set and its paraphrases must plan exactly as
without free-hand access (certified first).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

import duckdb
import pytest
from test_adhoc_rules_unit import AS_OF, PROJECTS, TODAY, _run, _validate, db, snapshot  # noqa: F401 - fixtures
from test_planner_gold_unit import PARAPHRASES, REQUESTS, SNAP, plan

from tela_api.ask import freehand
from tela_api.ask.freehand import FreehandUnsupported, build_rule, choose, glossary_terms, rule_from_planner
from tela_api.ask.planner import Plan, PlannerOut
from tela_api.knowledge.gold import pack_draft, spec_diff
from tela_api.knowledge.guidance import glossary_chunks, load_glossary
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

STAGES = ["planning_stage_end_date", "design_stage_end_date", "build_stage_end_date", "test_stage_end_date",
          "implementation_stage_end_date", "warranty_stage_end_date"]
GLOSSARY = glossary_terms([c["content"] for c in glossary_chunks(load_glossary("pmo_tpg"), SNAP)])
VISIBLE = {e.key: {f.key for f in e.fields} for e in SNAP.entities}
VALUES = {"projects": {"status": ["Active", "Inactive"], "lifecycle_phase": sorted({p["lifecycle_phase"]
                                                                                    for p in PROJECTS})}}
PROBLEM = ("How many projects (any state) are past the Plan stage, meaning projects that have at least one stage "
           "planned end date and whose S2 plan planned end date is blank or before today?")


def freehand_plan(question: str, *, visible: dict[str, set[str]] | None = None) -> Plan:
    rules = plan(question)
    chosen = choose(question, SNAP, rules, measures=SNAP.measures, dims=SNAP.dimensions, visible=visible or VISIBLE,
                    glossary=GLOSSARY, values=VALUES)
    return chosen or rules


def _set(v: Any) -> bool:
    return v is not None and v != ""


def _before(d: date | None, day: date = TODAY) -> bool:
    return d is not None and d < day


# (question, expected rows: a number, or {breakdown value: number}); the Python rule over the fixture's rows.
CASES: list[tuple[str, Any]] = [
    ("How many projects have a budget code and whose planned deployment date is before today?",
     sum(1 for p in PROJECTS if _set(p["budget_code"]) and _before(p["planned_deployment_date"]))),
    ("How many projects whose actual deployment date is blank and planned deployment date is in the past?",
     sum(1 for p in PROJECTS if p["actual_deployment_date"] is None and _before(p["planned_deployment_date"]))),
    ("Total approved budget of projects whose status is Active and percent complete is less than 50",
     sum(float(p["approved_budget_value_usd"] or 0) for p in PROJECTS
         if p["status"] == "Active" and p["percent_complete"] is not None and p["percent_complete"] < 50)),
    ("How many projects by region whose warranty and closure planned end is upcoming?",
     dict(Counter(p["region"] for p in PROJECTS
                  if p["warranty_stage_end_date"] is not None and p["warranty_stage_end_date"] > TODAY))),
    ("How many projects without a budget code",
     sum(1 for p in PROJECTS if not _set(p["budget_code"]) and p["budget_code"] is None)),
    ("How many projects whose closure date is within the last 30 days",
     sum(1 for p in PROJECTS if p["closure_date"] is not None
         and TODAY - timedelta(days=30) <= p["closure_date"] <= TODAY)),
    ("How many requests whose discovery planned end is before today",
     sum(1 for r in REQUESTS if _before(r["stage1_discovery_planned_end_date"]))),
    ("How many projects whose lifecycle phase is not one of planning, initiation",
     sum(1 for p in PROJECTS if p["lifecycle_phase"] is not None
         and p["lifecycle_phase"] not in ("Planning", "Initiation"))),
    ("Average percent complete of projects whose on hold since is set",
     (lambda xs: sum(xs) / len(xs))([p["percent_complete"] for p in PROJECTS
                                     if p["on_hold_date"] is not None and p["percent_complete"] is not None])),
    ("How many projects with at least one of the stage planned end dates overdue",
     sum(1 for p in PROJECTS if any(_before(p[s]) for s in STAGES))),
    ("How many projects whose planned deployment date is on or before 2026-06-30 and no budget code",
     sum(1 for p in PROJECTS if p["planned_deployment_date"] is not None
         and p["planned_deployment_date"] <= date(2026, 6, 30) and p["budget_code"] is None)),
]


def _rows(snap: ModelSnapshot, con: duckdb.DuckDBPyConnection, spec: dict[str, Any]) -> Any:
    vs = _validate(snap, parse_spec({"model_version_id": snap.model_version_id,
                                     "connection_id": snap.connection_id, **spec})[0])
    assert vs.certified is False
    rows = _run(con, vs)
    if spec.get("breakdowns"):
        return {r[0]: r[1] for r in rows if r[1]}
    return rows[0][0]


def _close(a: Any, b: Any) -> bool:
    if isinstance(b, dict):
        return a == b
    return abs(float(a or 0) - float(b)) < 1e-6


# --------------------------------------------------------------------------- the problem question (D1)


def test_problem_question_answers_certified_with_the_rule_beside_it(
        snapshot: ModelSnapshot, db: duckdb.DuckDBPyConnection) -> None:  # noqa: F811
    p = freehand_plan(PROBLEM)
    assert p.status == "ok" and p.spec is not None and not p.freehand
    assert p.spec["measure"] == {"ref": "measure:baselining_in_scope"}  # the certified "Past planning" (D1)
    assert p.beside is not None and p.beside["kind"] == "freehand"
    adhoc = p.beside["spec"]["measure"]["adhoc"]
    assert adhoc["label"] == "Projects past the plan stage"
    assert adhoc["where"] == {"all": [
        {"any": [{"field": s, "op": "not_null"} for s in STAGES]},
        {"any": [{"field": "planning_stage_end_date", "op": "is_null"},
                 {"field": "planning_stage_end_date", "op": "<", "value": {"today": True}}]}]}
    assert "Planning stage planned end is before today" in adhoc["plain_rule"]
    assert any("S2 read as planning stage" in t for t in p.interpreted)
    expected = sum(1 for r in PROJECTS if any(r[s] is not None for s in STAGES)
                   and (r["planning_stage_end_date"] is None or r["planning_stage_end_date"] < TODAY))
    assert 0 < expected < len(PROJECTS)
    assert _rows(snapshot, db, p.beside["spec"]) == expected
    # One chip per condition, each with its place in the rule.
    assert [c["path"] for c in p.beside["chips"]] == [f"0.{i}" for i in range(6)] + ["1.0", "1.1"]


def test_without_the_glossary_s2_is_not_guessed() -> None:
    out = choose(PROBLEM, SNAP, plan(PROBLEM), measures=SNAP.measures, dims=SNAP.dimensions, visible=VISIBLE,
                 glossary={})
    assert out is not None and out.status == "unsupported" and "s2 plan planned end date" in (out.reason or "")


@pytest.mark.parametrize(("question", "expected"), CASES, ids=[c[0][:60] for c in CASES])
def test_free_hand_questions(question: str, expected: Any, snapshot: ModelSnapshot,  # noqa: F811
                             db: duckdb.DuckDBPyConnection) -> None:  # noqa: F811
    p = freehand_plan(question)
    assert p.status == "ok" and p.freehand and p.spec is not None, (p.status, p.reason)
    assert p.spec["measure"]["ref"] == "measure:_adhoc"
    got = _rows(snapshot, db, p.spec)
    assert _close(got, expected), (got, expected, p.spec["measure"]["adhoc"]["plain_rule"])


# --------------------------------------------------------------------------- certified first


def test_gold_set_plans_the_same_with_free_hand_on() -> None:
    cases = [(e["question"], e["spec"]) for e in pack_draft() if not e.get("held_out")] + PARAPHRASES
    def spec_of(q: str) -> dict[str, Any] | None:
        p = freehand_plan(q)
        return p.spec if p.status == "ok" else None

    changed = [(q, diff) for q, spec in cases if (diff := spec_diff(spec, spec_of(q)))]
    assert changed == []


def test_certified_name_without_rule_words_stays_certified() -> None:
    p = freehand_plan("How many projects are past planning?")
    assert not p.freehand and p.spec is not None and p.spec["measure"]["ref"] == "measure:baselining_in_scope"


def test_free_hand_shows_the_similar_certified_measure() -> None:
    p = freehand_plan("How many projects past planning whose planned deployment date is blank?")
    assert p.freehand and p.beside is not None and p.beside["kind"] == "certified"
    assert p.beside["ref"] == "measure:baselining_in_scope"


# --------------------------------------------------------------------------- refusals


def test_unknown_or_hidden_field_is_refused_never_guessed() -> None:
    p = freehand_plan("How many projects whose flux capacitor reading is blank?")
    assert p.status == "unsupported" and p.freehand and "flux capacitor" in (p.reason or "")
    hidden = {**VISIBLE, "projects": VISIBLE["projects"] - {"budget_code"}}
    p = freehand_plan("How many projects without a budget code and whose closure date is blank", visible=hidden)
    assert p.status == "unsupported"
    with pytest.raises(FreehandUnsupported):
        build_rule("How many projects whose budget code is blank", SNAP, visible=hidden, dims=SNAP.dimensions)


def test_ambiguous_field_is_refused() -> None:
    with pytest.raises(FreehandUnsupported, match="Name the field"):
        build_rule("How many projects whose stage planned end is blank", SNAP, visible=VISIBLE, dims=SNAP.dimensions)


def test_sql_text_is_never_a_rule() -> None:
    p = freehand_plan("How many projects whose name is blank; DROP TABLE projects; --")
    if p.status == "ok":
        assert "drop" not in str(p.spec).lower()


def test_glossary_terms_read_from_knowledge() -> None:
    assert GLOSSARY["s2"] == "planning stage"
    terms = glossary_terms(["# Glossary\n- **RAID**: risks, actions, issues and decisions\nS3 = design stage"])
    assert terms == {"raid": "risks, actions, issues and decisions", "s3": "design stage"}


# --------------------------------------------------------------------------- AG-03 v2 output


def test_language_model_rule_is_checked_not_repaired() -> None:
    good = PlannerOut.model_validate({"adhoc": {"entity": "projects", "agg": "count", "label": "Late",
                                                "where": {"field": "planned_deployment_date", "op": "<",
                                                          "value": {"today": True}}}})
    rule = rule_from_planner(freehand.AdhocOut.model_validate(good.adhoc), SNAP, VISIBLE)
    assert rule.adhoc["plain_rule"] == "Count of projects where Planned deployment date is before today."
    bad = [
        {"entity": "projects", "agg": "count", "label": "x", "where": {"field": "secret_sauce", "op": "is_null"}},
        {"entity": "projects", "agg": "count", "label": "x", "where": {"sql": "1=1"}},
        {"entity": "projects", "agg": "sum", "label": "x"},
        {"entity": "nowhere", "agg": "count", "label": "x"},
    ]
    for b in bad:
        with pytest.raises(FreehandUnsupported):
            rule_from_planner(freehand.AdhocOut.model_validate(b), SNAP, VISIBLE)
    hidden = {**VISIBLE, "projects": VISIBLE["projects"] - {"planned_deployment_date"}}
    with pytest.raises(FreehandUnsupported):
        rule_from_planner(freehand.AdhocOut.model_validate(good.adhoc), SNAP, hidden)


def test_chip_edit_changes_one_condition() -> None:
    p = freehand_plan("How many projects whose planned deployment date is before today and budget code is set")
    assert p.spec is not None
    adhoc = p.spec["measure"]["adhoc"]
    changed = freehand.set_condition(adhoc, "1", {"field": "budget_code", "op": "is_null"}, SNAP)
    assert changed["where"]["all"][1] == {"field": "budget_code", "op": "is_null"}
    assert "Budget code is blank" in changed["plain_rule"]
    with pytest.raises(FreehandUnsupported):
        freehand.set_condition(adhoc, "0", {"field": "budget_code", "op": "<"}, SNAP)
