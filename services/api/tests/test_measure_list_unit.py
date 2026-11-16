"""Several measures listed with one noun (EXEC-172): "Total internal, external, contingency, hardware and software
cost …" is one answer of five measures, not "Which measure do you mean?"; up to six measures in a spec; "open" on a sum
of every row is not in the plan unless a filter says the open states.
"""

from __future__ import annotations

import duckdb
import pytest
from pydantic import ValidationError
from test_multi_measure_unit import db, run, snapshot, spec  # noqa: F401 - pytest fixtures
from test_planner_gold_unit import SNAP, plan
from test_rules_confidence_unit import checked

from tela_api.ask import confidence
from tela_api.ask.orchestrator import _tied_measures
from tela_api.ask.planner import Plan, PlannerOut, measure_list
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import MAX_MEASURES

EXEC172 = "Total internal, external, contingency, hardware and software cost of CAPEX projects, as a bar."
FIVE = ["cost_internal_total", "cost_external_total", "cost_contingency_total", "cost_hw_total", "cost_sw_total"]


def refs(spec: dict) -> list[str]:  # type: ignore[type-arg]
    return [spec["measure"]["ref"], *(e["ref"] for e in spec.get("extra_measures", []))]


def test_the_exec172_list_is_five_measures_with_its_filter_and_bar() -> None:
    p = plan(EXEC172)
    assert p.status == "ok" and p.spec is not None
    assert refs(p.spec) == [f"measure:{k}" for k in FIVE]
    assert p.spec["filters"] == [{"ref": "dimension:budget_type", "op": "=", "values": ["CAPEX"]}]
    assert p.spec["visual"] == {"type": "ranked_bar"}
    p, decided = checked(EXEC172)
    assert decided.status == "ok" and p.confidence >= confidence.threshold(None), p.why


@pytest.mark.parametrize(("question", "keys"), [
    ("internal and external cost", ["cost_internal_total", "cost_external_total"]),
    ("Internal, external and contingency costs by region",
     ["cost_internal_total", "cost_external_total", "cost_contingency_total"]),
    ("show me hardware and software cost", ["cost_hw_total", "cost_sw_total"]),
])
def test_a_list_names_each_measure(question: str, keys: list[str]) -> None:
    listed = measure_list(question, SNAP.measures)
    assert listed is not None and [m.key for m in listed.measures] == keys


@pytest.mark.parametrize("question", [
    "open projects by region and status",  # "and" joins breakdowns, no shared measure noun
    "internal and internal cost",  # the same measure twice
    "cost by region and programme",
])
def test_not_a_list(question: str) -> None:
    assert measure_list(question, SNAP.measures) is None


def test_list_words_are_not_filters() -> None:
    """"external" names Forecast cost: external, not a value of another field."""
    p = plan("internal and external cost")
    assert p.status == "ok" and p.spec is not None and p.spec["filters"] == []


def test_open_on_a_sum_of_every_row_is_not_in_the_plan() -> None:
    p, decided = checked(EXEC172.replace("of CAPEX", "of open CAPEX"))
    assert p.confidence < confidence.threshold(None)
    assert p.why == ["“open” is not in the plan; Forecast cost: internal (USD) adds up every row"]
    assert decided.status == "clarify"


def test_six_measures_in_one_spec(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> None:
    assert MAX_MEASURES == 6
    six = [{"ref": f"measure:{k}"} for k in [*FIVE, "forecast_cost_total"]]
    assert len(spec({"measures": six}).all_measure_refs()) == 6
    with pytest.raises(ValidationError, match="at most 6"):
        spec({"measures": [*six, {"ref": "measure:approved_budget_total"}]})
    names, rows = run(db, snapshot, {"measures": six})
    assert len(names) == 6 and len(rows) == 1
    alone = [run(db, snapshot, {"measure": m})[1][0][0] for m in six]
    assert list(rows[0]) == alone  # each column is its measure's own figure
    assert len(PlannerOut(measure="measure:a", extra_measures=[f"measure:{k}" for k in FIVE]).extra_measures) == 5


def test_only_a_measure_tie_goes_to_ag03() -> None:
    """RULE-023's "By 'cost' do you mean …?" stays the person's; "Which measure do you mean?" is AG-03's first."""
    tie = plan("action items")
    assert _tied_measures(tie) == {"measure:open_actions", "measure:overdue_actions"}
    cost = plan("What is the cost by region?")
    assert cost.status == "clarify" and cost.question is not None and cost.question["key"].startswith("money:")
    assert _tied_measures(cost) == set()
    assert _tied_measures(Plan("ok")) == set()
