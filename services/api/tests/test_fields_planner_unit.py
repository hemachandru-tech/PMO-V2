"""US-254 Plan B offline: the rules' question frame grounded on the PMO pack's model, compiled by each shape's
template and run on the synthetic TPG extract (DuckDB). No database, no LLM."""

from __future__ import annotations

from typing import Any

import duckdb
import pmo_oracle as oracle
import pytest
from test_gold_scoring_unit import AS_OF
from test_gold_scoring_unit import db as db  # the extract in DuckDB (a pytest fixture)
from test_planner_gold_unit import INDEX, KNOWN, PROJECTS, SNAP
from test_pmo_catalogue_unit import _catalog

from tela_api.ask import fields_planner as fp
from tela_api.ask.frame import QuestionFrame, rules_frame
from tela_api.ask.planner import LinkedValue, Plan
from tela_api.knowledge import conditions as named
from tela_api.knowledge import scoring
from tela_api.knowledge.gold import pack_draft
from tela_api.knowledge.values import link
from tela_api.packs.manifest import bind, discover, load_manifest, match
from tela_api.query.asof import pin_dates
from tela_api.query.compiler import SqlCompiler
from tela_api.query.validator import SpecValidator
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

CERTIFIED = [m for m in SNAP.measures if m.status == "certified"]
ALL = {e.key: {f.key for f in e.fields} for e in SNAP.entities}
OPEN = [p for p in oracle.PROJECTS if oracle.RULES["open_projects"](p)]


def plan_b(question: str, *, memory: dict[str, str] | None = None, visible: dict[str, set[str]] | None = None,
           rows: list[dict[str, Any]] | None = None, linked: list[LinkedValue] | None = None,
           hybrid: bool = False, frame: QuestionFrame | None = None) -> Plan:
    """As ``Orchestrator._fields_plan``: frame, approved conditions (stored rows, else the certified measures'),
    linked values, the person's visible fields."""
    vis = visible or ALL
    frame = frame or rules_frame(question, subjects=fp.subjects(SNAP))
    dims = [d for d in SNAP.dimensions if d.field in vis.get(d.entity, set())]
    conds = [r for r in fp.measure_conditions(CERTIFIED, rows or []) if named.visible(r, vis)]
    ctx = fp.Context(snap=SNAP, visible=vis, dims=dims, measures=CERTIFIED, conditions=conds,
                     linked=link(question, INDEX, known=KNOWN).links if linked is None else linked,
                     memory=memory or {}, question=question)
    return fp.plan(frame, ctx, hybrid=hybrid)


def _duck() -> ModelSnapshot:
    """The same model bound to the extract's tables in DuckDB (as the gold harness's ``snapshot``)."""
    manifest = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    binding = bind(manifest, match(manifest, _catalog()), reporting_date=AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v1", "connection_id": "c1", "engine": "excel",
                                         **binding.definition})


DUCK = _duck()


def run(con: duckdb.DuckDBPyConnection, _: ModelSnapshot, spec: dict[str, Any]) -> list[tuple[Any, ...]]:
    """As the gold harness runs a spec, with ad-hoc measures allowed (Plan B runs under the answer mode)."""
    snap = DUCK
    qs = parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id,
                     **{k: v for k, v in spec.items() if k != "compare"}})[0]
    vs = pin_dates(SpecValidator(snap, freehand=True).validate(qs), as_of=AS_OF, today=AS_OF)
    compiled = SqlCompiler("duckdb").compile(vs, as_of=AS_OF)
    return con.execute(compiled.sql(), compiled.params.values).fetchall()


def ok(p: Plan) -> dict[str, Any]:
    assert p.status == "ok" and p.spec is not None, (p.status, p.reason, p.question)
    return p.spec


def fields(p: Plan) -> dict[str, Any]:
    return (p.signals or {})["fields"]


# --------------------------------------------------------------------------- the stories' questions


def test_the_01a1ca5c_question_is_a_list_of_every_open_project(db: duckdb.DuckDBPyConnection) -> None:
    p = plan_b("Show the List of open Projects")
    spec = ok(p)
    assert scoring.answer_shape(spec) == "list_of_records"
    assert spec["breakdowns"] == [{"ref": "field:projects.name"}, {"ref": "field:projects.project_number"}]
    rows = run(db, SNAP, spec)
    assert len(rows) == len(OPEN) == 65 and {r[-1] for r in rows} == {1}  # none of the 35 closed ones with a 0
    assert {r[0] for r in rows} == {p_["name"] for p_ in OPEN}
    assert fields(p)["certified"] and fields(p)["certified_by"] == "measure:open_projects"  # the same rule exactly
    (used,) = fields(p)["conditions_used"]
    assert used["term"] == "Open projects" and used["source"] == "pack_measure"


def test_how_many_open_projects_in_uae(db: duckdb.DuckDBPyConnection) -> None:
    p = plan_b("How many open projects in UAE")
    spec = ok(p)
    assert scoring.answer_shape(spec) == "number"
    assert run(db, SNAP, spec) == [(sum(1 for x in OPEN if x["region"] == "GCC - UAE"),)] == [(11,)]
    # the pack's approved synonym names "GCC - UAE" (in Ask without the value index it is a partial match: not
    # certified, see test_fields_planner_api)
    assert [s["how"] for s in fields(p)["slots"] if s["slot"] == "condition"] == ["synonym", "definition"]
    assert fields(p)["certified"]
    partial = plan_b("How many open projects in UAE",
                     linked=[LinkedValue("region", "GCC - UAE", "GCC - UAE", "uae", "partial", 0.8)])
    assert not fields(partial)["certified"] and "partial" in fields(partial)["not_certified_because"]


@pytest.mark.parametrize(("question", "keep"), [
    ("How many open projects not in UAE", lambda x: x["region"] != "GCC - UAE"),
    ("open projects except KSA or Jordan", lambda x: x["region"] not in ("KSA", "Jordan")),
])
def test_negation(db: duckdb.DuckDBPyConnection, question: str, keep: Any) -> None:
    spec = ok(plan_b(question))
    got = run(db, SNAP, spec)
    want = sum(1 for x in OPEN if keep(x))
    assert (got[0][0] if scoring.answer_shape(spec) == "number" else len(got)) == want


def test_a_numeric_condition(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(plan_b("How many projects with approved budget over 1m"))
    assert spec["measure"]["adhoc"]["where"] == {"field": "approved_budget_value_usd", "op": ">", "value": 1000000}
    want = sum(1 for x in PROJECTS if (x.get("approved_budget_value_usd") or 0) > 1_000_000)
    assert run(db, SNAP, spec) == [(want,)]


def test_a_total_of_a_field(db: duckdb.DuckDBPyConnection) -> None:
    p = plan_b("total forecast cost of open projects")
    spec = ok(p)
    adhoc = spec["measure"]["adhoc"]
    assert (adhoc["agg"], adhoc["field"]) == ("sum", "cost_total_usd")
    want = sum(float(x["cost_total_usd"] or 0) for x in OPEN)
    assert float(run(db, SNAP, spec)[0][0]) == pytest.approx(want)


# --------------------------------------------------------------------------- one template per shape


def test_breakdown(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(plan_b("open projects by region"))
    assert scoring.answer_shape(spec) == "breakdown" and spec["breakdowns"] == [{"ref": "dimension:region"}]
    assert dict(run(db, SNAP, spec))["KSA"] == sum(1 for x in OPEN if x["region"] == "KSA")


def test_ranking_of_records(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(plan_b("top 3 projects by forecast cost"))
    assert scoring.answer_shape(spec) == "ranking" and spec["limit"] == 3
    assert spec["sort"] == [{"by": "measure", "dir": "desc"}]
    assert spec["filters"] == [{"ref": "field:projects.cost_total_usd", "op": "not_null", "values": []}]
    got = [float(r[-1]) for r in run(db, SNAP, spec)][:3]  # the query service applies the limit
    assert got == sorted((float(x["cost_total_usd"]) for x in PROJECTS if x["cost_total_usd"] is not None),
                         reverse=True)[:3]


def test_ranking_of_groups(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(plan_b("which region has the most open projects"))
    assert spec["breakdowns"] == [{"ref": "dimension:region"}] and spec["limit"] == 1
    assert scoring.answer_shape(spec) == "ranking"
    (_, n) = run(db, SNAP, spec)[0]
    assert n == max(sum(1 for x in OPEN if x["region"] == r) for r in {x["region"] for x in OPEN})


def test_trend(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(plan_b("projects by planned deployment date by month"))
    assert spec["time"] == {"ref": "dimension:planned_deployment_date", "grain": "month"}
    assert scoring.answer_shape(spec) == "trend"
    assert sum(r[-1] for r in run(db, SNAP, spec)) == sum(1 for x in PROJECTS if x["planned_deployment_date"])


def test_trend_on_a_named_date() -> None:
    spec = ok(plan_b("projects created by month"))
    assert spec["time"] == {"ref": "field:projects.created_on", "grain": "month"}


def test_comparison_with_the_previous_period() -> None:
    spec = ok(plan_b("open projects by planned deployment date this month compared with last month"))
    assert spec["compare"] == {"kind": "previous_period"} and scoring.answer_shape(spec) == "comparison"


def test_share(db: duckdb.DuckDBPyConnection) -> None:
    spec = ok(plan_b("share of open projects by region"))
    assert spec["share_of_total"] == ["measure:_adhoc"] and scoring.answer_shape(spec) == "share"
    run(db, SNAP, spec)
    spec = ok(plan_b("what percentage of projects are in UAE"))
    assert spec["breakdowns"] == [{"ref": "dimension:region"}] and "where" not in spec["measure"]["adhoc"]


@pytest.mark.parametrize("question", ["distribution of forecast cost", "tell me about project Atlas"])
def test_a_shape_without_a_template_is_refused(question: str) -> None:
    p = plan_b(question)
    assert p.status == "unsupported" and "can't answer this kind of question yet" in (p.reason or "")


# --------------------------------------------------------------------------- slots it can't read


def test_an_unknown_term_is_refused_by_name() -> None:
    p = plan_b("How many zorbified projects")
    assert p.status == "unsupported" and "“zorbified”" in (p.reason or "")


def test_an_unknown_subject_asks_which_records_then_uses_the_choice() -> None:
    p = plan_b("How many zorbs")
    assert p.status == "clarify" and p.question is not None
    assert p.question["key"] == "fields:subject:zorbs" and p.question["kind"] == "field"
    assert {o["value"] for o in p.question["options"]} <= {f"entity:{e.key}" for e in SNAP.entities}
    spec = ok(plan_b("How many zorbs", memory={"fields:subject:zorbs": "entity:projects"}))
    assert spec["measure"]["adhoc"] == {**spec["measure"]["adhoc"], "entity": "projects", "agg": "count"}


def test_an_unknown_grouping_asks_which_field() -> None:
    p = plan_b("open projects by flavour")
    assert p.status == "clarify" and p.question is not None
    assert p.question["question"] == "Which field do you mean by “flavour”?"
    spec = ok(plan_b("open projects by flavour", memory={"fields:grouping:flavour": "dimension:platform"}))
    assert spec["breakdowns"] == [{"ref": "dimension:platform"}]


def test_hidden_fields_are_never_used() -> None:
    hidden = {**ALL, "projects": ALL["projects"] - {"current_state", "name"}}
    p = plan_b("Show the List of open Projects", visible=hidden)
    assert p.status == "unsupported" and "“open”" in (p.reason or "")  # the term reads a field they can't see
    spec = ok(plan_b("list of projects in UAE", visible=hidden))
    assert all("field:projects.name" != b["ref"] for b in spec["breakdowns"])


# --------------------------------------------------------------------------- order of authority, certification


def _sheet_row(cond: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"id": "c1", "term": "Open projects", "term_key": "open project", "synonyms": [], "entity": "projects",
            "condition": cond, "source": "definition_sheet", "measure_ref": "measure:open_projects",
            "status": "approved", "conflict": None, "prefer": None, **kw}


def test_an_approved_definition_comes_before_the_measure(db: duckdb.DuckDBPyConnection) -> None:
    active = {"field": "current_state", "op": "=", "value": "Active"}
    p = plan_b("How many open projects", rows=[_sheet_row(active)])
    spec = ok(p)
    assert spec["measure"]["adhoc"]["where"] == active
    assert fields(p)["conditions_used"][0]["source"] == "definition_sheet"
    assert run(db, SNAP, spec) == [(sum(1 for x in PROJECTS if x["current_state"] == "Active"),)]


def test_an_unsettled_conflict_uses_the_certified_measure() -> None:
    active = {"field": "current_state", "op": "=", "value": "Active"}
    p = plan_b("How many open projects", rows=[_sheet_row(active, conflict={"measure": {}})])
    assert ok(p)["measure"]["adhoc"]["where"]["op"] == "in"  # the measure's rule
    assert fields(p)["conditions_used"][0]["source"] == "pack_measure"
    assert "until an Admin chooses" in next(s["reason"] for s in fields(p)["slots"] if s["slot"] == "condition")


def test_a_value_found_by_meaning_is_not_certified() -> None:
    meant = [LinkedValue("region", "KSA", "KSA", "saudi", "meaning", 0.55)]
    p = plan_b("How many projects in saudi", linked=meant)
    ok(p)
    assert not fields(p)["certified"] and p.confidence == pytest.approx(0.55)


def test_a_count_equal_to_a_certified_measure_is_certified() -> None:
    p = plan_b("How many projects")
    assert fields(p)["certified"] and fields(p)["certified_by"] == "measure:total_projects"
    assert p.confidence == 1.0


# --------------------------------------------------------------------------- the US-250 list questions


def test_gold_lists_and_their_counts(db: duckdb.DuckDBPyConnection) -> None:
    """The 44 EXEC-186 list questions and their 44 count twins (US-250), planned by Plan B. A list is right when it
    names the same records as the gold spec's (its records with a count above 0), a count when the figure is the
    same; both must be the right kind of answer. Today's rules planner: lists 0/44, counts 40/44 (EXEC-189).
    Plan B (EXEC-195): lists 36, counts 35; the misses are refused by name (pack wording such as "Overdue DOU gates"
    for "overdue for DOU") or pick another field holding the same value (project vs delivery manager). With the
    US-258 check (EXEC-200): lists 35, counts 34 — "go live in the next 30 days" now asks about "go live" (no field
    or synonym says so; the date was the only one), as "Red health" asks about "health" (read as Reported status
    while a Calculated health exists)."""
    right = {"list": 0, "count": 0}
    for e in pack_draft():
        tag = next((t for t in ("list", "count") if t in (e.get("tags") or [])), None)
        if tag is None:
            continue
        p = plan_b(e["question"])
        if p.status != "ok" or p.spec is None or scoring.answer_shape(p.spec) != scoring.answer_shape(e["spec"]):
            continue
        want, got = run(db, SNAP, e["spec"]), run(db, SNAP, p.spec)
        same = {r[0] for r in want if r[-1]} == {r[0] for r in got if r[-1]} if tag == "list" else want == got
        right[tag] += same
    assert right["list"] >= 35 and right["count"] >= 34, right


def test_words_of_one_qualifier_are_each_read(db: duckdb.DuckDBPyConnection) -> None:
    """"open critical projects": both the term and the value, never one of them."""
    spec = ok(plan_b("How many open critical projects are there?"))
    assert run(db, SNAP, spec) == [(sum(1 for x in OPEN if x["priority"] == "Critical"),)]


def test_an_age_on_a_date_field(db: duckdb.DuckDBPyConnection) -> None:
    from datetime import timedelta

    spec = ok(plan_b("How many projects have been on hold for more than 90 days"))
    assert spec["measure"]["adhoc"]["where"] == {"field": "on_hold_date", "op": "<",
                                                 "value": {"today": True, "offset_days": -90}}
    want = sum(1 for x in PROJECTS if x.get("on_hold_date") and x["on_hold_date"] < AS_OF - timedelta(days=90))
    assert run(db, SNAP, spec) == [(want,)]


def test_a_calculated_term_answers_through_its_measure_and_is_never_combined() -> None:
    spec = ok(plan_b("How many projects have insufficient data for calculated health?"))
    assert spec["measure"] == {"ref": "measure:health_insufficient"}
    p = plan_b("How many projects in KSA have insufficient data for calculated health?")
    assert p.status == "unsupported" and "calculated value" in (p.reason or "")


def test_an_unread_word_is_asked_about_then_left_out_uncertified() -> None:
    """US-258: a word nothing read fails the check (EXEC-195 answered with confidence x0.6): Ask asks whether to
    answer without it; told so, it answers, not certified."""
    p = plan_b("How many projects flibbertigibbet")
    assert p.status == "clarify" and p.question is not None and p.confidence == 0
    assert p.question["key"] == "fields:check_words:flibbertigibbet"
    assert p.question["options"] == [{"id": "1", "label": "Answer without “flibbertigibbet”", "value": "ignore"}]
    p = plan_b("How many projects flibbertigibbet", memory={p.question["key"]: "ignore"})
    ok(p)
    assert not fields(p)["certified"] and "as you chose" in fields(p)["not_certified_because"]
    assert fields(p)["check"] == {"passed": True, "failures": [], "left_out": ["flibbertigibbet"]}
    assert "Left out “flibbertigibbet”, as you chose" in p.interpreted
