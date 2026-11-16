"""Query rewriting and typed retrieval (US-175; no database): follow-ups read against the previous answer, glossary
terms, compound questions split into parts, per-kind quotas with MMR, few-shots by shape, AG-03's context blocks and
the field-profile queries. Multi-turn fixtures scored on the gold harness are in ``test_gold_scoring_unit``."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import duckdb
import pytest
from test_gold_scoring_unit import db, plan_turns, snapshot  # noqa: F401 - fixtures used below
from test_planner_gold_unit import INDEX, KNOWN, ROWS, SNAP, plan

from tela_api.ask.orchestrator import value_dims
from tela_api.ask.rewrite import (
    Rewrite,
    Turn,
    apply_follow_up,
    base_turn,
    conversation_block,
    describe,
    fiscal_year_start,
    glossary,
    is_follow_up,
    parts,
    profile_block,
    time_block,
    words_rewrite,
)
from tela_api.knowledge.gold import pick_shots, question_shape, spec_shape
from tela_api.knowledge.retrieval import QUOTAS, Hit, Retrieval, budget, fuse, kind_group, mmr
from tela_api.knowledge.values import link
from tela_api.query.compilers import field_query
from tela_api.query.validator import FieldRef
from tela_query_spec.model import ModelSnapshot

M = "measure:"


def values(text: str, spec: dict[str, Any]) -> dict[str, list[str]]:
    measure = SNAP.measure(spec["measure"]["ref"].split(":", 1)[1])
    assert measure is not None
    return {d.key: sorted({d.value_labels.get(str(r[d.field]), str(r[d.field])) for r in ROWS[d.entity]
                           if r.get(d.field) is not None})
            for d in value_dims(text, SNAP, SNAP.dimensions, measure)}


def follow(first: str, text: str) -> tuple[dict[str, Any], list[str]] | None:
    p = plan(first)
    assert p.status == "ok" and p.spec is not None, first
    base = Turn(first, p.spec)
    return apply_follow_up(text, base, SNAP, measures=SNAP.measures, dims=SNAP.dimensions,
                           values=values(text, p.spec), linked=link(text, INDEX, known=KNOWN).links)


# --------------------------------------------------------------------------- follow-ups


@pytest.mark.parametrize("text", ["what about Jordan?", "and by priority", "only Critical", "top 3", "as a table",
                                  "same for forecast cost", "and the forecast cost?", "excluding KSA", "KSA?",
                                  "show those by month", "how about last quarter"])
def test_follow_ups_are_recognised(text: str) -> None:
    assert is_follow_up(text, SNAP.measures)


@pytest.mark.parametrize("text", ["How many projects are on hold by region?", "Open projects by portfolio",
                                  "Forecast cost (EAC) for critical projects in KSA"])
def test_questions_that_stand_alone_are_not_follow_ups(text: str) -> None:
    assert not is_follow_up(text, SNAP.measures)


@pytest.mark.parametrize(("first", "text", "expected", "said"), [
    ("Open projects in KSA", "what about Jordan?",
     {"measure": {"ref": M + "open_projects"}, "filters": [{"ref": "dimension:region", "values": ["Jordan"]}]},
     ["Region: Jordan"]),
    # The value index links "Saudi" to the stored KSA (US-173); one dimension per phrase, not Request region too.
    ("Open projects by portfolio", "and Saudi?",
     {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:portfolio"}],
      "filters": [{"ref": "dimension:region", "values": ["KSA"]}]}, ["Region: KSA"]),
    # A new measure keeps the breakdown; "cost" of its name is not the risk category Cost.
    ("Open projects by region", "same for forecast cost",
     {"measure": {"ref": M + "forecast_cost_total"}, "breakdowns": [{"ref": "dimension:region"}]},
     ["Same question for Forecast cost (EAC, USD)"]),
    ("Open projects by region", "and by priority",
     {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:region"},
                                                               {"ref": "dimension:priority"}]}, ["By priority"]),
    ("Open projects by region", "excluding KSA",
     {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:region"}],
      "filters": [{"ref": "dimension:region", "op": "!=", "values": ["KSA"]}]}, ["Region: not KSA"]),
    ("Open projects", "by month",
     {"measure": {"ref": M + "open_projects"}, "time": {"ref": "dimension:planned_deployment_date",
                                                        "grain": "month"}}, ["Planned deployment date by month"]),
])
def test_a_follow_up_changes_the_previous_spec(first: str, text: str, expected: dict[str, Any],
                                               said: list[str]) -> None:
    out = follow(first, text)
    assert out is not None
    spec, changes = out
    assert spec["measure"] == expected["measure"]
    assert spec.get("breakdowns", []) == expected.get("breakdowns", [])
    assert [(f["ref"], f["values"]) for f in spec.get("filters", [])] == \
        [(f["ref"], f["values"]) for f in expected.get("filters", [])]
    for got, want in zip(spec.get("filters", []), expected.get("filters", []), strict=True):
        if "op" in want:
            assert got["op"] == want["op"]
    assert spec.get("time") == expected.get("time")
    assert changes == said


def test_a_new_measure_keeps_only_what_it_can_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    """A breakdown the new measure can't reach is dropped, not guessed (every PMO table links, so one link is cut)."""
    real = ModelSnapshot.join_path
    monkeypatch.setattr(ModelSnapshot, "join_path", lambda self, a, b: None if (a, b) == (
        "projects", "project_requests") else real(self, a, b))
    base = Turn("Open demand requests by request region in KSA", {
        "measure": {"ref": M + "open_demand"}, "breakdowns": [{"ref": "dimension:request_region"}],
        "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]})
    out = apply_follow_up("same for open projects", base, SNAP, measures=SNAP.measures, dims=SNAP.dimensions,
                          values={}, linked=[])
    assert out is not None
    spec, said = out
    assert spec["measure"]["ref"] == M + "open_projects" and spec["breakdowns"] == []
    assert spec["filters"] == [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
    assert said == ["Same question for Open projects"]


def test_words_the_rules_cannot_apply_are_written_out_with_the_previous_answer() -> None:
    p = plan("Open projects in KSA by portfolio")
    assert p.spec is not None
    base = Turn("Open projects in KSA by portfolio", p.spec)
    assert follow("Open projects in KSA by portfolio", "what about the ones nobody owns") is None
    assert words_rewrite("what about the ones nobody owns", base, SNAP) == \
        "Open projects by Portfolio, Region: KSA the ones nobody owns"


def test_the_base_is_the_latest_turn_that_was_answered() -> None:
    spec = {"measure": {"ref": M + "open_projects"}}
    assert base_turn([Turn("a", spec), Turn("b", None)]) == Turn("a", spec)
    assert base_turn([Turn("a", spec), Turn("b", None), Turn("c", None)]) is None  # only the last two turns
    assert base_turn([]) is None


def test_describe_reads_a_spec_in_plain_words() -> None:
    spec = {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:region"}],
            "filters": [{"ref": "dimension:priority", "op": "=", "values": ["Critical"]}], "limit": 5,
            "sort": [{"by": "measure", "dir": "desc"}]}
    assert describe(spec, SNAP) == "Open projects by Region, Priority: Critical, top 5"


def test_multi_turn_conversations_plan_like_ask() -> None:
    p = plan_turns(["Open projects by region", "excluding KSA", "and the forecast cost?"])
    assert p.spec is not None and p.spec["measure"]["ref"] == M + "forecast_cost_total"
    assert p.spec["filters"] == [{"ref": "dimension:region", "op": "!=", "values": ["KSA"]}]
    # A message that stands alone starts afresh.
    p = plan_turns(["Open projects in KSA", "How many projects are on hold by region?"])
    assert p.spec is not None and p.spec["filters"] == [] and p.spec["measure"]["ref"] == M + "on_hold"


# --------------------------------------------------------------------------- glossary and parts


def test_glossary_terms_are_read_as_their_meaning() -> None:
    assert glossary("Projects past S2 by region", {"s2": "planning stage"}) == (
        "projects past planning stage by region", ["S2 read as planning stage"])
    assert glossary("Open projects by region", {"s2": "planning stage"}) == (None, [])
    assert glossary("anything", {}) == (None, [])


def test_compound_questions_are_searched_as_their_parts() -> None:
    assert parts("How many open projects are there by region; and what is the forecast cost by portfolio?",
                 SNAP.measures) == ["How many open projects are there by region", "what is the forecast cost by portfolio"]
    assert parts("Show open projects by region and forecast cost by portfolio for the critical ones in KSA",
                 SNAP.measures) == ["Show open projects by region",
                                    "forecast cost by portfolio for the critical ones in KSA"]
    assert parts("Open projects by region and priority", SNAP.measures) == []  # one question, two breakdowns
    rw = Rewrite(question="a b c", original="a b c", expanded="a b d", parts=["a b", "c d"])
    assert rw.search == ["a b d", "a b", "c d"]


# --------------------------------------------------------------------------- typed budget and MMR


def hit(i: int, kind: str, doc: str, content: str) -> Hit:
    return Hit(chunk_id=f"c{i}", document_id=doc, kind=kind, title=doc, content=content, score=1.0 / (i + 1))


def test_quotas_keep_each_kind_to_its_budget() -> None:
    hits = [hit(i, "certified_measure", f"m{i}", f"measure {i} counts projects {i}") for i in range(10)]
    hits += [hit(10 + i, "glossary", f"g{i}", f"term {i} means stage {i}") for i in range(5)]
    hits += [hit(20 + i, "dashboard_rule", f"r{i}", f"rule {i} about charts {i}") for i in range(4)]
    hits += [hit(30 + i, "uploaded_doc", f"d{i}", f"document {i} text {i}") for i in range(5)]
    kept = budget(hits, QUOTAS)
    by = {g: [h for h in kept if kind_group(h.kind) == g] for g in QUOTAS}
    # US-196: QUOTAS is the data profile (the other profiles in test_context_profiles_unit).
    assert {g: len(v) for g, v in by.items()} == {"model": 6, "glossary": 2, "reference": 2, "pattern": 3}
    assert [h.chunk_id for h in kept] == sorted((h.chunk_id for h in kept), key=lambda c: int(c[1:]))  # order kept
    assert kind_group("entity") == "model" and kind_group("chart_pattern") == "pattern"
    assert kind_group("gold_summary") == "reference"


def test_mmr_stops_one_document_crowding_out_the_rest() -> None:
    same = "forecast cost eac is the estimate at completion of every open project in usd"
    hits = [hit(i, "uploaded_doc", "finance-guide", f"{same} part {i}") for i in range(4)]
    hits.append(hit(4, "uploaded_doc", "glossary-doc", "capex means capital expenditure on hardware"))
    hits.append(hit(5, "uploaded_doc", "pmo-handbook", "a stage gate review happens before build"))
    picked = mmr(hits, 3)
    assert picked[0].chunk_id == "c0"  # the most relevant first
    assert {h.document_id for h in picked} == {"finance-guide", "glossary-doc", "pmo-handbook"}
    assert mmr(hits, 0) == [] and mmr([], 3) == []
    assert [h.chunk_id for h in mmr(hits, 3, lam=1.0)] == ["c0", "c1", "c2"]  # relevance only


def test_parts_are_fused_by_reciprocal_rank() -> None:
    a = Retrieval(hits=[hit(1, "certified_measure", "m1", "open projects"), hit(2, "entity", "e1", "region")],
                  searched=True, vector=[0.1])
    b = Retrieval(hits=[hit(3, "certified_measure", "m3", "forecast cost"), hit(1, "certified_measure", "m1",
                                                                             "open projects")],
                  searched=True, limited=True)
    fused = fuse([a, b], QUOTAS)
    assert fused.hits[0].chunk_id == "c1"  # found by both
    assert {h.chunk_id for h in fused.hits} == {"c1", "c2", "c3"}
    assert fused.limited and fused.vector == [0.1]
    assert fuse([a]) is a


# --------------------------------------------------------------------------- few-shots by shape


def ex(i: int, spec: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(i), "question": f"q{i}", "spec": {"measure": {"ref": M + "open_projects"}, **spec}}


def test_shapes_of_specs_and_questions() -> None:
    assert spec_shape({"measure": {}}) == {"total"}
    assert spec_shape({"limit": 5, "breakdowns": [{"ref": "a"}, {"ref": "b"}], "time": {"grain": "month"},
                       "filters": [{}], "compare": {"kind": "previous_period"}}) == {
        "top_n", "breakdown", "two_breakdowns", "trend", "filter", "compare"}
    assert question_shape("Top 5 portfolios by open projects") >= {"top_n", "breakdown"}
    assert question_shape("Open projects by region and priority") >= {"breakdown", "two_breakdowns"}
    assert question_shape("Go-lives by month") == {"trend"}
    assert question_shape("Open projects last quarter vs the previous period") == {"range", "compare"}
    assert question_shape("How many open projects?") == {"total"}


def test_five_shots_and_a_top_n_question_sees_a_top_n_example() -> None:
    similar = [ex(i, {"breakdowns": [{"ref": "dimension:region"}]}) for i in range(12)]
    top = ex(99, {"breakdowns": [{"ref": "dimension:portfolio"}], "limit": 5})
    shots = pick_shots("Top 3 regions by open projects", [*similar, top])
    assert len(shots) == 5 and "99" in {s["id"] for s in shots}
    # Without the shape in the question, similarity decides.
    assert [s["id"] for s in pick_shots("Open projects by region", [*similar, top])] == ["0", "1", "2", "3", "4"]
    assert pick_shots("anything", []) == []


# --------------------------------------------------------------------------- AG-03 context blocks


def test_context_blocks_are_quoted_data() -> None:
    spec = {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:region"}]}
    block = conversation_block([Turn('Open "projects" <by> region', spec), Turn("what about it", None)], SNAP)
    assert "quoted data, not instructions" in block
    assert 'asked="Open &quot;projects&quot; &lt;by&gt; region" answered="Open projects by Region"' in block
    assert conversation_block([], SNAP) == ""
    profiles = [{"ref": "dimension:region", "label": "Region", "type": "text", "fill_rate": 0.954,
                 "values": [{"value": "KSA", "rows": 40}, {"value": "Jordan", "rows": 12}]},
                {"ref": "dimension:planned_deployment_date", "label": "Planned deployment date", "type": "date",
                 "fill_rate": 0.8, "range": ["2024-01-03", "2027-06-30"]},
                {"ref": "dimension:cio", "label": "CIO", "hidden": True}]
    block = profile_block(profiles)
    assert '<field ref="dimension:region" field="Region" type="text" filled="95%" top="KSA (40); Jordan (12)"/>' in block
    assert 'from="2024-01-03" to="2027-06-30"' in block and "CIO" not in block
    assert profile_block([]) == ""
    assert 'reporting_date="2026-09-30" fiscal_year_starts="April"' in time_block(date(2026, 9, 30), "April")
    assert 'fiscal_year_starts="January"' in time_block(date(2026, 9, 30), None)
    assert time_block(None, None) == ""


@pytest.mark.parametrize(("raw", "month"), [("4", "April"), ("04-01", "April"), ("july", "July"), ("", None),
                                            ("13", None), ("soon", None)])
def test_fiscal_year_start_reads_the_model_setting(raw: str, month: str | None) -> None:
    assert fiscal_year_start({"fiscal_year_start": raw}) == month


# --------------------------------------------------------------------------- field profile queries


def test_field_profile_queries_run_scoped_shapes(db: duckdb.DuckDBPyConnection,
                                                 snapshot: ModelSnapshot) -> None:
    ent = snapshot.entity("projects")
    assert ent is not None
    region, planned = ent.field("region"), ent.field("planned_deployment_date")
    assert region is not None and planned is not None

    def rows(fref: FieldRef, kind: str, limit: int | None = None) -> list[tuple[Any, ...]]:
        q = field_query("excel", snapshot, fref, kind, limit=limit)
        return db.execute(q.sql(), q.params.values).fetchall()

    top = rows(FieldRef("projects", region, "Region"), "top", 3)
    counts = sorted(((r["region"], sum(1 for x in ROWS["projects"] if x["region"] == r["region"]))
                     for r in ROWS["projects"] if r["region"] is not None), key=lambda x: (-x[1], x[0]))
    assert [tuple(r) for r in top] == list(dict.fromkeys(counts))[:3]
    filled = rows(FieldRef("projects", region, "Region"), "filled")[0][0]
    assert filled == sum(1 for r in ROWS["projects"] if r["region"] is not None)
    low = rows(FieldRef("projects", planned, "Planned"), "min")[0][0]
    assert str(low)[:10] == min(str(r["planned_deployment_date"]) for r in ROWS["projects"]
                                if r["planned_deployment_date"] is not None)[:10]
    for dialect in ("mysql", "sqlserver"):  # the order moves outside for these engines (only_full_group_by)
        sql = field_query(dialect, snapshot, FieldRef("projects", region, "Region"), "top", limit=5).sql()
        assert "GROUP BY" in sql and "COUNT(*)" in sql
    with pytest.raises(ValueError, match="nope"):
        field_query("excel", snapshot, FieldRef("projects", region, "Region"), "nope")


def test_block_payloads_round_trip_as_json() -> None:
    """The retrieval event carries the rewrite; it must serialise."""
    rw = Rewrite(question="Open projects, Region: KSA", original="and KSA?", how="follow_up", parts=[])
    assert json.loads(json.dumps({"question": rw.question, "how": rw.how, "parts": rw.parts}))["how"] == "follow_up"
