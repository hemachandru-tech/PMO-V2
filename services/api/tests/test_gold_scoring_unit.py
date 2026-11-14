"""Gold set 2.0 scoring (US-184; FEAT-064), no database server.

Two parts:

* the pure scoring in ``knowledge/scoring.py`` — result comparison, error taxonomy, retrieval metrics, summary;
* an offline execution harness: the PMO pack bound to the synthetic extract, every draft gold question planned
  by the rules planner (as ``test_planner_gold_unit`` does), the planned and the expected spec both compiled for
  DuckDB and run, and the expected answers checked against the independent oracle (``pmo_oracle``) where it can
  compute them. This is the fast loop for gold-set authoring: a new question whose expected spec disagrees with the
  oracle, or that the planner answers with different rows, fails here before the live suite runs.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import pmo_oracle as oracle
import pytest
import tpg_synthetic
import yaml
from test_planner_gold_unit import INDEX, KNOWN, PARAPHRASES, ROWS, SNAP, plan
from test_pmo_catalogue_unit import _catalog

from tela_api.ask import confidence
from tela_api.ask.orchestrator import value_dims
from tela_api.ask.planner import Plan
from tela_api.ask.rewrite import Turn, apply_follow_up, base_turn, is_follow_up, words_rewrite
from tela_api.knowledge import scoring
from tela_api.knowledge.gold import pack_draft, spec_diff
from tela_api.knowledge.values import link
from tela_api.packs.manifest import bind, discover, load_manifest, match
from tela_api.query.compiler import SqlCompiler
from tela_api.query.validator import SpecValidator
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

AS_OF: date = tpg_synthetic.AS_OF
M = "measure:"

# --------------------------------------------------------------------------- pure scoring


def test_same_result_ignores_row_order_and_number_types() -> None:
    a = [["KSA", 3], ["Jordan", Decimal("2.0000001")]]
    b = [("Jordan", 2.0), ("KSA", 3.0)]
    assert scoring.same_result(a, b)
    assert not scoring.same_result(a, b, ordered=True)
    assert not scoring.same_result(a, [("KSA", 3)])
    assert scoring.same_result([{"region": "KSA", "n": 1}], [["KSA", 1]])  # dict rows read in column order


def test_one_figure_empty_or_zero_results_prove_nothing() -> None:
    assert not scoring.informative([])
    assert not scoring.informative([[9]])
    assert not scoring.informative([["KSA", 0], ["Jordan", None]])
    assert scoring.informative([["KSA", 0], ["Jordan", 4]])


def test_error_taxonomy() -> None:
    exp = {"measure": {"ref": M + "open_projects"},
           "filters": [{"ref": "dimension:region", "op": "=", "values": ["Saudi Arabia"]}]}
    assert scoring.classify([], status="ok", expected=exp, actual=exp) is None
    assert scoring.classify([{"path": "spec"}], status="clarify") == "over_clarification"
    assert scoring.classify([{"path": "spec"}], status="unsupported") == "linking"
    assert scoring.classify([], status="error") == "error"
    assert scoring.classify([{"path": "measure"}, {"path": "filters"}], expected=exp, actual={}) == "measure"
    # Right field, wrong value: the words were not linked to the stored value.
    ksa = {**exp, "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}
    assert scoring.classify([{"path": "filters"}], expected=exp, actual=ksa) == "linking"
    assert scoring.classify([{"path": "filters"}], expected=exp, actual={**exp, "filters": []}) == "filter"
    assert scoring.classify([{"path": "time"}], expected=exp, actual=exp) == "time"
    assert scoring.classify([{"path": "breakdowns"}, {"path": "limit"}], expected=exp, actual=exp) == "shape"
    assert scoring.classify([{"path": "result"}], expected=exp, actual=exp) == "result"
    assert set(scoring.TAXONOMY) >= {"linking", "measure", "filter", "time", "shape", "over_clarification"}


def test_value_linking() -> None:
    exp = {"measure": {"ref": M + "open_projects"},
           "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA", "Jordan"]}]}
    assert scoring.values_linked(exp, {"filters": [{"ref": "dimension:region", "op": "=",
                                                    "values": ["Jordan", "KSA"]}]}) is True
    assert scoring.values_linked(exp, {"filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}) \
        is False
    assert scoring.values_linked(exp, None) is False
    assert scoring.values_linked({"measure": {"ref": M + "open_projects"}}, None) is None
    # Right answer without a filter on that field (open projects = projects with status Active): not counted.
    status = {"measure": {"ref": M + "total_projects"},
              "filters": [{"ref": "dimension:status", "op": "=", "values": ["Active"]}]}
    assert scoring.values_linked(status, {"measure": {"ref": M + "open_projects"}}, passed=True) is None
    assert scoring.values_linked(status, {"measure": {"ref": M + "open_projects"}}) is False


def _meta(measure: str | None = None, dims: list[str] | None = None) -> dict[str, Any]:
    if measure:
        return {"kind": "certified_measure", "measure": measure}
    return {"kind": "entity", "dimensions": dims or []}


def test_retrieval_scores() -> None:
    exp = {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:region"}]}
    # Measure first, the entity with the dimension second: perfect.
    best = scoring.retrieval_scores([_meta("open_projects"), _meta(dims=["region", "cio"])], exp)
    assert best["measure_rank"] == 1 and best["reciprocal_rank"] == 1.0 and best["ndcg_at_8"] == 1.0
    assert best["recall"] == {"measure": 1.0, "dimension": 1.0}
    # Measure third, after two look-alikes; the same measure twice adds nothing.
    late = scoring.retrieval_scores([_meta("in_delivery"), _meta(dims=["region"]), _meta("open_projects"),
                                     _meta("open_projects")], exp)
    assert late["measure_rank"] == 3 and late["reciprocal_rank"] == pytest.approx(0.3333, abs=1e-4)
    assert 0 < late["ndcg_at_8"] < 1
    # Past the top 8, or missing: no rank, zero.
    missing = scoring.retrieval_scores([_meta("in_delivery")] * 8 + [_meta("open_projects")], exp)
    assert missing["measure_rank"] is None and missing["reciprocal_rank"] == 0.0 and missing["ndcg_at_8"] == 0.0
    assert missing["recall"] == {"measure": 0.0, "dimension": 0.0}
    # A free-hand rule has no measure chunk to find.
    adhoc = scoring.retrieval_scores([], {"measure": {"ref": "measure:_adhoc"}})
    assert adhoc["reciprocal_rank"] is None and adhoc["ndcg_at_8"] is None


def test_summary() -> None:
    rows = [
        {"passed": True, "spec_match": True, "tags": ["baseline"], "error_class": None, "values_linked": None,
         "retrieval": scoring.retrieval_scores([_meta("open_projects")], {"measure": {"ref": M + "open_projects"}})},
        {"passed": True, "spec_match": False, "tags": ["baseline", "alias"], "error_class": None,
         "values_linked": True, "retrieval": None},
        {"passed": False, "spec_match": False, "tags": ["alias"], "error_class": "linking", "values_linked": False,
         "retrieval": scoring.retrieval_scores([_meta("x"), _meta("open_projects")],
                                               {"measure": {"ref": M + "open_projects"}})},
    ]
    s = scoring.summarise(rows)
    assert (s["total"], s["passed"], s["spec_matched"]) == (3, 2, 1)
    assert s["pass_rate"] == 0.6667 and s["spec_match_rate"] == 0.3333
    assert s["by_tag"]["alias"] == {"total": 2, "passed": 1, "spec_matched": 0, "pass_rate": 0.5}
    assert s["by_error"] == {"linking": 1}
    assert s["mrr"] == 0.75 and s["recall_at_8"] == 1.0
    assert s["value_linking"] == {"total": 2, "correct": 1, "rate": 0.5}


def test_outcomes_and_clarification_options() -> None:
    """US-190: each result's outcome, and whether a clarification offered the expected measure."""
    assert [scoring.outcome(s, p) for s, p in (("ok", True), ("ok", False), ("clarify", False),
                                                ("unsupported", False), ("error", False), (None, False))] == [
        "right", "wrong", "clarified", "refused", "error", "error"]
    exp = {"measure": {"ref": M + "overdue_go_lives"}}
    measure_q = {"options": [{"id": "a", "measure": M + "overdue_dou"}, {"id": "b", "measure": M + "overdue_go_lives"}]}
    assert scoring.offered_right(exp, measure_q) is True
    reading_q = {"options": [{"id": "a", "value": "reading:0"}], "readings": [{"measure": {"ref": M + "schedule_risk"}}]}
    assert scoring.offered_right(exp, reading_q) is False
    assert scoring.offered_right(exp, {**reading_q, "readings": [*reading_q["readings"], exp]}) is True
    assert scoring.offered_right(exp, None) is None


def test_required_recall_from_linking_and_retrieval() -> None:
    exp = {"measure": {"ref": M + "on_hold"}, "breakdowns": [{"ref": "dimension:hold_reason"}],
           "time": {"ref": "dimension:planned_deployment_date", "grain": "month"}}
    # Linking offered the measure, retrieval the reason dimension: everything was there, from one or the other.
    r = scoring.required_recall(exp, ["measure:on_hold", "measure:open_projects", "dimension:planned_deployment_date"],
                                [_meta("open_projects"), _meta(dims=["hold_reason"])])
    assert r == {"measure": {"linked": True, "retrieved": False},
                 "dimensions": {"linked": 0.5, "retrieved": 0.5}, "found": True}
    # Neither found the measure: not found, whatever the dimensions.
    miss = scoring.required_recall(exp, ["dimension:hold_reason"], [_meta("open_projects")] * 8 + [_meta("on_hold")])
    assert miss is not None and miss["measure"] == {"linked": False, "retrieved": False} and not miss["found"]
    assert scoring.required_recall({"measure": {"ref": "measure:_adhoc"}}, [], []) is None


def test_scores_per_path_and_per_set() -> None:
    def r(path: str | None, status: str, passed: bool, tags: list[str], **kw: Any) -> dict[str, Any]:
        return {"planner": path, "status": status, "passed": passed, "spec_match": passed, "tags": tags,
                "error_class": None if passed else "measure", "retrieval": None, "values_linked": None,
                "confident_wrong": kw.get("sure", False), "clarify_right": kw.get("right"),
                "required": kw.get("req"), "latency_ms": kw.get("ms")}
    found = {"measure": {"linked": True, "retrieved": True}, "dimensions": None, "found": True}
    lost = {"measure": {"linked": False, "retrieved": True}, "dimensions": None, "found": True}
    rows = [
        r("rules", "ok", True, ["baseline"], req=found, ms=100),
        r("rules", "ok", True, ["paraphrase"], req=found, ms=120),
        r("rules", "ok", False, ["paraphrase", "money"], sure=True, req=lost, ms=90),
        r("llm", "unsupported", False, ["paraphrase"], req=lost, ms=40000),
        r("rules", "clarify", False, ["paraphrase"], right=True, ms=200),
        r("rules", "clarify", False, ["look_alike"], right=False, ms=150),
        r(None, "error", False, ["paraphrase"]),
    ]
    s = scoring.summarise(rows)
    assert list(s["by_path"]) == ["rules", "llm", "none"]
    llm = s["by_path"]["llm"]
    assert llm["outcomes"] == {"refused": 1} and llm["refusal_rate"] == 1.0 and llm["latency_ms"] == {
        "p50": 40000, "p95": 40000, "max": 40000}
    orig, para = s["by_set"]["original"], s["by_set"]["paraphrase"]
    assert orig["outcomes"] == {"right": 1, "clarified": 1}  # on the original set a clarification is over-asking
    assert (para["total"], para["passed"], para["confident_wrong"], para["clarify_right"]) == (5, 1, 1, 1)
    assert para["outcomes"] == {"right": 1, "wrong": 1, "clarified": 1, "refused": 1, "error": 1}
    assert para["confident_wrong_rate"] == 0.2 and para["refusal_rate"] == 0.2
    assert para["required_recall"] == {"total": 3, "found": 1.0, "measure_linked": 0.3333, "measure_retrieved": 1.0}
    assert set(para["by_path"]) == {"rules", "llm", "none"}
    # Only originals: no paraphrase group.
    assert set(scoring.by_set(rows[:1])) == {"original"}


# --------------------------------------------------------------------------- answer shape (US-250)

REC = "field:projects.name"


def test_answer_shape_of_each_kind() -> None:
    """One spec per kind of answer; the order decides mixed specs (a ranking by record is a ranking)."""
    m = {"measure": {"ref": M + "open_projects"}}
    region = {"ref": "dimension:region"}
    cases = {
        "number": [m, {**m, "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]},
                   {**m, "derived": [{"op": "ratio", "of": "a", "to": "b"}], "extra_measures": [m["measure"]],
                    "breakdowns": []},
                   {**m, "breakdowns": [{"ref": REC}], "percentile": {"p": "p50", "per": None}}],
        "list_of_records": [{**m, "breakdowns": [{"ref": REC}]},
                            {**m, "breakdowns": [{"ref": "field:tpg_project.tpg_projectname"}]},
                            {**m, "breakdowns": [{"ref": "field:tpg_risk.tpg_title"}]}],
        "breakdown": [{**m, "breakdowns": [region]}, {**m, "breakdowns": [region], "having": {"op": ">", "value": 5}},
                      {**m, "breakdowns": [region, {"ref": REC}], "percentile": {"p": "p50", "per": "x"}}],
        "ranking": [{**m, "breakdowns": [region], "sort": [{"by": "measure", "dir": "desc"}], "limit": 5},
                    {**m, "breakdowns": [region], "rank": {}},
                    {**m, "breakdowns": [region, {"ref": REC}], "top_n_per_group": {"by": "x", "n": 3}}],
        "trend": [{**m, "time": {"ref": "dimension:planned_deployment_date", "grain": "month"}}],
        "comparison": [{**m, "time": {"ref": "dimension:planned_deployment_date", "grain": "month"},
                        "compare": {"to": "previous_period"}},
                       {**m, "extra_measures": [{"ref": M + "on_hold"}]}],
        "share": [{**m, "breakdowns": [region], "share_of_total": ["open_projects"]}],
        "distribution": [{**m, "bucket": {"field": "x"}}],
    }
    for want, specs in cases.items():
        assert [scoring.answer_shape(s) for s in specs] == [want] * len(specs), want
    assert scoring.answer_shape(None) is None
    assert set(scoring.SHAPES) >= set(cases) | {"record_detail", "definition"}


def test_record_refs_are_read_from_the_ref() -> None:
    yes = ["field:projects.name", "field:project_requests.title", "field:pcrs.pcr_number", "field:projects.project_id",
           "field:tpg_project.tpg_projectname", "field:tpg_projectrequests.tpg_title", "field:tpg_actionitem.tpg_title"]
    no = ["dimension:region", "field:projects.region", "field:risks.project_name", "field:tpg_risk.tpg_projectname",
          "measure:open_projects", None, "field:name"]
    assert [scoring.is_record_ref(r) for r in yes] == [True] * len(yes)
    assert [scoring.is_record_ref(r) for r in no] == [False] * len(no)


def test_a_count_for_a_list_is_a_shape_failure() -> None:
    """EXEC-186: "Show the list of open projects" answered "Open projects: 103" is wrong, whatever the measure."""
    want = {"measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": REC}]}
    got = {"measure": {"ref": M + "open_projects"}}
    diff = scoring.shape_diff("list_of_records", got)
    assert diff == [{"path": "answer_shape", "expected": "list_of_records", "actual": "number"}]
    assert scoring.classify(diff, expected=want, actual=got) == "shape"
    assert scoring.shape_diff("list_of_records", want) == [] and scoring.shape_diff("number", None) == []


def _graded(shape: str | None, status: str, passed: bool, tags: list[str], sure: bool = False) -> dict[str, Any]:
    return {"shape": shape, "status": status, "passed": passed, "tags": tags, "confident_wrong": sure,
            "spec_match": passed, "error_class": None, "retrieval": None, "values_linked": None}


def test_shape_grid_totals_equal_the_run_totals() -> None:
    rows = [
        _graded("number", "ok", True, ["exec186", "count"]),
        _graded("list_of_records", "ok", False, ["exec186", "list"], sure=True),
        _graded("list_of_records", "clarify", False, ["exec186", "list"]),
        _graded("list_of_records", "unsupported", False, ["list"]),
        _graded("breakdown", "ok", True, ["breakdown"]),
        _graded(None, "error", False, []),
    ]
    s = scoring.summarise(rows)
    g = s["shape_grid"]
    assert list(g["rows"]) == ["number", "list_of_records", "breakdown", "unknown"]  # in SHAPES order
    assert g["rows"]["list_of_records"] == {"total": 3, "right": 0, "confident_wrong": 1, "clarified": 1,
                                            "refused": 1, "wrong": 1, "error": 0}
    assert g["cells"]["list_of_records"]["exec186"]["total"] == 2 and g["cells"]["unknown"] == {
        "untagged": {"total": 1, "right": 0, "confident_wrong": 0, "clarified": 0, "refused": 0, "wrong": 0,
                     "error": 1}}
    # Every result is in exactly one row; the rows add up to the run, and each tag's cells to its by_tag total.
    for key in ("total", "right", "confident_wrong", "clarified", "refused"):
        assert sum(r[key] for r in g["rows"].values()) == g["total"][key]
    assert g["total"]["total"] == s["total"] and g["total"]["right"] == s["passed"]
    for tag, t in s["by_tag"].items():
        assert sum(c.get(tag, {"total": 0})["total"] for c in g["cells"].values()) == t["total"]
    text = scoring.grid_text(g)
    assert "list_of_records" in text and "0/3  cw 1 cl 1 rf 1" in text and text.splitlines()[-1].startswith("all")
    assert "shape_grid" not in scoring.summarise([{k: v for k, v in r.items() if k != "shape"} for r in rows])


GOLD_FILES = [Path(__file__).resolve().parents[3] / p for p in (
    "packs/pmo/gold_examples.yaml", "yaml-packages/dataverse_pack_tpg/gold_examples.yaml")]


@pytest.mark.parametrize("path", GOLD_FILES, ids=["pmo", "dataverse"])
def test_every_gold_example_names_its_shape(path: Path) -> None:
    """Both gold files: every example says what kind of answer it expects, the same kind its spec gives, and the
    EXEC-186 set has at least 40 list questions, each beside a count twin on the same measure and filters."""
    examples = yaml.safe_load(path.read_text())["examples"]
    wrong = [(e["question"], e.get("shape"), scoring.answer_shape(e["spec"])) for e in examples
             if e.get("shape") not in scoring.SHAPES or e["shape"] != scoring.answer_shape(e["spec"])]
    assert wrong == []
    assert len({e["question"].lower() for e in examples}) == len(examples)
    lists = [e for e in examples if "exec186" in e["tags"] and "list" in e["tags"]]
    counts = [e for e in examples if "exec186" in e["tags"] and "count" in e["tags"]]
    assert len(lists) >= 40 and len(counts) == len(lists)
    assert all(e.get("held_out") for e in lists + counts)
    assert [{k: v for k, v in e["spec"].items() if k != "breakdowns"} for e in lists] == [c["spec"] for c in counts]
    assert {e["shape"] for e in lists} == {"list_of_records"} and {e["shape"] for e in counts} == {"number"}
    # A list is one row per record of the measure's own entity, by a field that entity has.
    manifest = yaml.safe_load((path.parent / "manifest.yaml").read_text())
    entity_of = {m["key"]: m["entity"] for m in manifest["measures"]}
    fields = {e["key"]: {f["key"] for f in e["fields"]} for e in manifest["entities"]}
    for e in lists:
        entity, field = e["spec"]["breakdowns"][0]["ref"].removeprefix("field:").split(".")
        measure = e["spec"]["measure"]["ref"].removeprefix("measure:")
        assert entity == entity_of[measure] and field in fields[entity], e["question"]
    print(f"\n{path.parent.name}: {dict(Counter(e['shape'] for e in examples))}")


# --------------------------------------------------------------------------- offline execution harness


@pytest.fixture(scope="module")
def snapshot() -> ModelSnapshot:
    manifest = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    binding = bind(manifest, match(manifest, _catalog()), reporting_date=AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v1", "connection_id": "c1", "engine": "excel",
                                         **binding.definition})


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("\n".join(line for line in tpg_synthetic.DDL.splitlines() if not line.startswith("DROP TABLE")))
    for table, cols, rows in tpg_synthetic.tables():
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in rows])
    return con


def run(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, spec: dict[str, Any]) -> list[tuple[Any, ...]]:
    qs = parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id,
                     **{k: v for k, v in spec.items() if k != "compare"}})[0]
    compiled = SqlCompiler("duckdb").compile(SpecValidator(snap).validate(qs), as_of=AS_OF)
    return db.execute(compiled.sql(), compiled.params.values).fetchall()


def _values(text: str, spec: dict[str, Any]) -> dict[str, list[str]]:
    measure = SNAP.measure(spec["measure"]["ref"].split(":", 1)[1])
    assert measure is not None
    return {d.key: sorted({d.value_labels.get(str(r[d.field]), str(r[d.field])) for r in ROWS[d.entity]
                           if r.get(d.field) is not None})
            for d in value_dims(text, SNAP, SNAP.dimensions, measure)}  # as ``Orchestrator._values``


def plan_turns(messages: list[str]) -> Plan:
    """``gold.plan_messages`` offline, as Ask plans a conversation (US-175): a follow-up is applied to the previous
    answer's spec, else written out with it and planned; a message that stands alone is planned as asked."""
    p = plan(messages[0])
    history: list[Turn] = []
    for prev, text in pairwise(messages):
        if p.status != "ok" or p.spec is None:
            break
        history.append(Turn(prev, p.spec))
        base = base_turn(history)
        if base is None or not is_follow_up(text, SNAP.measures):
            p = plan(text)
            continue
        assert base.spec is not None
        applied = apply_follow_up(text, base, SNAP, measures=SNAP.measures, dims=SNAP.dimensions,
                                  values=_values(text, base.spec), linked=link(text, INDEX, known=KNOWN).links)
        p = Plan(status="ok", spec=applied[0]) if applied else plan(words_rewrite(text, base, SNAP))
    return p


def score(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, question: str, expected: dict[str, Any],
          tags: list[str] | None = None, turns: list[str] | None = None) -> dict[str, Any]:
    """What the live suite records for one example, minus retrieval: plan, compare, run both when they differ."""
    p = plan_turns([*(turns or []), question])
    actual = p.spec if p.status == "ok" else None
    shape = scoring.answer_shape(expected)
    wrong_shape = scoring.shape_diff(shape, actual)  # US-250, as ``gold.evaluate_one``
    diff = spec_diff(expected, actual) + wrong_shape
    out: dict[str, Any] = {"question": question, "tags": tags or [], "spec_match": not diff, "diff": diff,
                           "values_linked": scoring.values_linked(expected, actual), "retrieval": None,
                           "shape": shape, "status": p.status,
                           "confident_wrong": p.status == "ok" and p.confidence >= confidence.threshold(None)}
    if actual is None:
        return {**out, "passed": False, "error_class": scoring.classify(diff, status=p.status)}
    if not diff:
        return {**out, "passed": True, "error_class": None, "confident_wrong": False}
    want, got = run(db, snap, expected), run(db, snap, actual)
    ok = not wrong_shape and scoring.informative(want) and scoring.same_result(
        want, got, ordered=scoring.ordered_spec(expected))
    return {**out, "passed": ok, "values_linked": scoring.values_linked(expected, actual, passed=ok),
            "confident_wrong": out["confident_wrong"] and not ok,
            "error_class": None if ok else scoring.classify(diff, expected=expected, actual=actual)}


def _oracle_value(spec: dict[str, Any]) -> tuple[bool, Any]:
    """The oracle's answer to a spec it can compute: a whole-measure figure, or a count by one plain field."""
    measure = spec["measure"]["ref"].split(":", 1)[1]
    if spec.get("filters") or spec.get("time") or spec.get("extra_measures") or spec.get("exists_in") or spec.get("compare") or spec.get("cohort"):
        return False, None
    breakdowns = spec.get("breakdowns") or []
    if not breakdowns:
        if measure in oracle.CATALOGUE:
            return True, oracle.CATALOGUE[measure]
        if measure in oracle.RULES or measure in oracle.REQUEST_RULES:
            return True, oracle.count(measure)
        return False, None
    plain = {"region": "region", "portfolio": "portfolio", "priority": "priority", "programme": "program",
             "cio": "cio", "delivery_manager": "delivery_manager", "lifecycle_phase": "lifecycle_phase"}
    dim = breakdowns[0]["ref"].split(":", 1)[1]
    if len(breakdowns) == 1 and dim in plain and measure in oracle.RULES:
        return True, oracle.by(measure, plain[dim])
    return False, None


def _close(got: Any, want: Any) -> bool:
    if want is None or got is None:
        return got is want or (got in (None, 0) and want in (None, 0))
    return abs(float(got) - float(want)) <= 1e-6 * max(1.0, abs(float(want)))


def test_expected_answers_agree_with_the_oracle(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    """Each draft gold spec the oracle can compute gives the oracle's numbers: the gold set's answers are right."""
    checked, wrong = 0, []
    for ex in pack_draft():
        known, want = _oracle_value(ex["spec"])
        if not known:
            continue
        rows = run(db, snapshot, ex["spec"])
        at = len(ex["spec"].get("breakdowns") or []) + bool((ex["spec"].get("time") or {}).get("grain"))  # the measure
        if isinstance(want, dict):
            got = {r[0]: r[at] for r in rows}
            ok = {k: v for k, v in want.items() if v} == {k: v for k, v in got.items() if v}
        else:
            ok = _close(rows[0][at] if rows else None, want)
        checked += 1
        if not ok:
            wrong.append((ex["question"], rows[:5], want))
    assert wrong == []
    assert checked >= 25  # most of the draft set; the rest are bands, time grains and two-way breakdowns


# Conversation fixtures (earlier messages, follow-up, expected spec): the shape gold set 2.0 takes multi-turn in.
MULTI_TURN: list[tuple[list[str], str, dict[str, Any]]] = [
    (["Open projects by region"], "top 3", {"measure": {"ref": M + "open_projects"}, "breakdowns": [
        {"ref": "dimension:region"}], "sort": [{"by": "measure", "dir": "desc"}], "limit": 3}),
    (["Overdue go-lives by portfolio"], "only KSA", {"measure": {"ref": M + "overdue_go_lives"}, "breakdowns": [
        {"ref": "dimension:portfolio"}], "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}),
    (["How many projects are on hold?", "by region"], "only Critical", {
        "measure": {"ref": M + "on_hold"}, "breakdowns": [{"ref": "dimension:region"}],
        "filters": [{"ref": "dimension:priority", "op": "=", "values": ["Critical"]}]}),
    # US-175: follow-ups that lean on the previous answer.
    (["Open projects in KSA"], "what about Jordan?", {
        "measure": {"ref": M + "open_projects"}, "filters": [{"ref": "dimension:region", "op": "=", "values": [
            "Jordan"]}]}),
    (["Open projects by region"], "same for forecast cost", {
        "measure": {"ref": M + "forecast_cost_total"}, "breakdowns": [{"ref": "dimension:region"}]}),
    (["Overdue go-lives in KSA"], "and by portfolio", {
        "measure": {"ref": M + "overdue_go_lives"}, "breakdowns": [{"ref": "dimension:portfolio"}],
        "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}),
    (["Open projects by region", "excluding KSA"], "and the forecast cost?", {
        "measure": {"ref": M + "forecast_cost_total"}, "breakdowns": [{"ref": "dimension:region"}],
        "filters": [{"ref": "dimension:region", "op": "!=", "values": ["KSA"]}]}),
    (["Open projects by region"], "what about the critical ones", {
        "measure": {"ref": M + "open_projects"}, "breakdowns": [{"ref": "dimension:region"}],
        "filters": [{"ref": "dimension:priority", "op": "=", "values": ["Critical"]}]}),
    (["Open projects by portfolio"], "How many projects are on hold by region?", {
        "measure": {"ref": M + "on_hold"}, "breakdowns": [{"ref": "dimension:region"}]}),
]


def test_execution_accuracy_on_the_draft_set(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    """The rules planner on the draft set and the held-out paraphrases, scored as the live suite scores it."""
    cases = [(e["question"], e["spec"], e.get("tags") or [], e.get("turns")) for e in pack_draft()
             if not e.get("held_out")]
    cases += [(q, s, ["paraphrase"], None) for q, s in PARAPHRASES]
    cases += [(q, s, ["multi_turn"], t) for t, q, s in MULTI_TURN]
    results = [score(db, snapshot, q, s, t, turns) for q, s, t, turns in cases]
    s = scoring.summarise(results)
    print(f"\nexecution {s['passed']}/{s['total']} · spec match {s['spec_matched']}/{s['total']} · "
          f"value linking {s['value_linking']} · errors {s['by_error']}")
    assert [r["question"] for r in results if not r["passed"]] == []
    assert s["pass_rate"] == 1.0 and s["by_error"] == {}


def test_a_different_spec_with_the_same_rows_passes(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    """Open projects are projects with status Active: spec mismatch, execution match."""
    expected = {"measure": {"ref": M + "total_projects"},
                "filters": [{"ref": "dimension:status", "op": "=", "values": ["Active"]}],
                "breakdowns": [{"ref": "dimension:region"}]}
    r = score(db, snapshot, "Open projects by region", expected)
    assert not r["spec_match"] and r["passed"] and r["error_class"] is None and r["values_linked"] is None


def test_look_alike_fails_on_rows_and_is_classed_as_measure(db: duckdb.DuckDBPyConnection,
                                                            snapshot: ModelSnapshot) -> None:
    """In delivery is not open: the rows differ, so execution doesn't rescue the wrong measure."""
    r = score(db, snapshot, "Open projects by region",
              {"measure": {"ref": M + "in_delivery"}, "breakdowns": [{"ref": "dimension:region"}]})
    assert not r["passed"] and r["error_class"] == "measure"
    # Open projects in KSA and in Jordan are the same figure here: one number never passes on execution.
    jordan = {"measure": {"ref": M + "open_projects"}, "filters": [
        {"ref": "dimension:region", "op": "=", "values": ["Jordan"]}]}
    ksa = {**jordan, "filters": [{**jordan["filters"][0], "values": ["KSA"]}]}
    assert run(db, snapshot, jordan) == run(db, snapshot, ksa)
    linked = score(db, snapshot, "Open projects in KSA", jordan)
    assert not linked["passed"] and linked["error_class"] == "linking" and linked["values_linked"] is False


def test_zero_rows_do_not_count_as_a_match(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    """Two wrong filters that both find nothing must not pass on execution."""
    expected = {"measure": {"ref": M + "open_projects"},
                "filters": [{"ref": "dimension:region", "op": "=", "values": ["Atlantis"]}]}
    assert run(db, snapshot, expected) in ([], [(0,)])
    r = score(db, snapshot, "Open projects in KSA", expected)
    assert not r["passed"]


# --------------------------------------------------------------------------- US-250: lists and the shape baseline


def test_each_list_has_as_many_rows_as_its_count(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    """The EXEC-186 lists run on the synthetic extract: as many records counted as the count twin's figure, and more
    than one, so execution accuracy can grade them. Today's compiler counts the condition inside the group, so a
    record breakdown also lists the records outside it with 0 (35 closed projects beside the 65 open ones): the
    list template (US-254) must drop them."""
    examples = pack_draft()
    lists = [e for e in examples if "list" in e["tags"] and "exec186" in e["tags"]]
    twins = {json.dumps(e["spec"], sort_keys=True): e for e in examples if "count" in e["tags"]}
    for e in lists:
        rows = run(db, snapshot, e["spec"])
        count = twins[json.dumps({k: v for k, v in e["spec"].items() if k != "breakdowns"}, sort_keys=True)]
        figure = run(db, snapshot, count["spec"])[0][0]
        counted = [r for r in rows if r[-1]]
        assert len(counted) == figure >= 1, (e["question"], len(counted), figure)
        assert scoring.informative(rows) or figure == 1, e["question"]  # one record: the spec match decides
        assert {r[-1] for r in counted} == {1}, e["question"]


def test_shape_baseline_offline(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    """US-250 baseline: today's rules planner on every draft example (originals, held-out paraphrases, noise and the
    EXEC-186 lists), graded by rows and by answer shape, reported as the shape by tag grid. The floors are this
    baseline (EXEC-189): a later change may raise them, not lower them. No list question is answered as a list yet."""
    results = [score(db, snapshot, e["question"], e["spec"], e.get("tags") or [], e.get("turns"))
               for e in pack_draft()]
    s = scoring.summarise(results)
    grid = s["shape_grid"]
    print(f"\nshape baseline {s['passed']}/{s['total']} · errors {s['by_error']}\n"
          + scoring.grid_text(grid, tags=["exec186", "list", "count", "paraphrase", "noise"]))
    assert grid["total"]["total"] == s["total"] == len(results)
    assert sum(r["total"] for r in grid["rows"].values()) == s["total"]
    assert sum(r["right"] for r in grid["rows"].values()) == s["passed"]
    rows = grid["rows"]
    lists, counts = grid["cells"]["list_of_records"]["list"], grid["cells"]["number"]["count"]
    assert lists["total"] >= 40 and counts["total"] == lists["total"]
    assert lists["right"] >= BASELINE["list"] and counts["right"] >= BASELINE["count"]
    assert rows["number"]["right"] >= BASELINE["number"] and rows["breakdown"]["right"] >= BASELINE["breakdown"]
    assert s["passed"] >= BASELINE["all"]


# EXEC-189 (2026-11-12), rules planner, pack 2.2.1 / gold 0.10.0: 447 of 622 right, 120 confident-wrong; every
# list answered with its count (0/44, 44 confident-wrong), the count twins 40/44.
BASELINE = {"list": 0, "count": 40, "number": 231, "breakdown": 183, "all": 447}
