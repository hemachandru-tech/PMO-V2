"""Narrator v2 and Ask next (US-186; FEAT-065), no database server: the checks on model sentences, the template
fallback per fact, data notes kept apart, and the follow-ups built from the spec."""

from __future__ import annotations

from typing import Any

from test_planner_gold_unit import SNAP

from tela_api.ask import insights, narrator, nextsteps
from tela_api.ask.narrator import Cited, NarratorV2Out

N = {"name": "m", "kind": "measure", "label": "Open projects", "format": "integer"}
REGION = {"name": "region", "kind": "dimension", "label": "Region", "ref": "dimension:region"}
ROWS = [["KSA", 40], ["GCC - UAE", 8], ["Egypt", 3], ["Jordan", 2], [None, 5]]


def story_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = {"columns": [REGION, N], "rows": ROWS, "row_count": len(ROWS), "truncated": False}
    f = narrator.facts(result)
    found = insights.detect(result, additive=True, agg="count")
    return f, found


def out(headline: str, *insights_: tuple[str, list[str]], headline_facts: list[str] | None = None) -> NarratorV2Out:
    return NarratorV2Out(headline=Cited(text=headline, facts=headline_facts or ["f0"]),
                         insights=[Cited(text=t, facts=ids) for t, ids in insights_])


def test_template_story_cites_every_fact_and_keeps_data_notes_apart() -> None:
    f, found = story_inputs()
    types = {x["id"]: x["type"] for x in found}
    assert "data_note" in types.values() and "concentration" in types.values()
    told = narrator.compose(f, found)
    assert told["source"] == "template"
    assert told["headline"] == {"text": narrator.template(f)["headline"], "facts": ["f0"], "source": "template"}
    told_ids = [i for c in told["insights"] for i in c["facts"]]
    assert told_ids == [x["id"] for x in found if x["type"] != "data_note"][:3]
    assert [n["fact"] for n in told["notes"]] == [x["id"] for x in found if x["type"] == "data_note"]
    assert all(c["text"] == next(x["text"] for x in found if x["id"] == c["facts"][0]) for c in told["insights"])


def test_model_sentences_are_kept_with_markers_stripped_and_ids_normalised() -> None:
    f, found = story_inputs()
    conc = next(x for x in found if x["type"] == "concentration")
    share = conc["numbers"]["top_share_pct"]
    o = out(f"KSA leads open projects with 40 of {f['total']}. [fact:f0]",
            (f"The top 3 regions hold {share}% of open projects [fact:{conc['id'][1:]}]", []))
    told = narrator.compose(f, found, o)
    assert told["headline"] == {"text": f"KSA leads open projects with 40 of {f['total']}.", "facts": ["f0"],
                                "source": "llm"}
    first = told["insights"][0]
    assert first == {"text": f"The top 3 regions hold {share}% of open projects", "facts": [conc["id"]],
                     "source": "llm"}
    assert told["source"] == "llm"
    # a fact the model leaves out keeps its template sentence
    only_headline = narrator.compose(f, found, out(f"KSA leads open projects with 40 of {f['total']}."))
    assert only_headline["source"] == "mixed" and only_headline["insights"][0]["source"] == "template"


def test_failed_checks_fall_back_to_the_template_sentence_of_each_fact() -> None:
    f, found = story_inputs()
    conc = next(x for x in found if x["type"] == "concentration")
    template = narrator.compose(f, found)
    bad = out("KSA has 41 open projects.",  # a number no fact has
              ("The top regions hold 99% of open projects.", [conc["id"]]),  # wrong number
              ("KSA is far ahead.", ["f9"]),  # unknown fact
              ("KSA is far ahead.", []))  # cites nothing
    assert narrator.compose(f, found, bad) == template
    # an insight may not cite the headline facts, and the wording check applies (no "spend" on forecast data)
    assert narrator.compose(f, found, out(narrator.template(f)["headline"],
                                          ("40 open projects are in KSA.", ["f0"]))) ["insights"] == \
        template["insights"]
    spend = out(f"Spend is highest in KSA (40 of {f['total']}).")
    assert narrator.compose(f, found, spend)["headline"]["source"] == "template"


def test_at_most_three_insights_and_none_for_an_empty_result() -> None:
    f, found = story_inputs()
    many = [*found, *({**found[0], "id": f"f{9 + i}"} for i in range(3))]
    assert len(narrator.compose(f, many)["insights"]) <= 3
    empty = narrator.facts({"columns": [REGION, N], "rows": []})
    told = narrator.compose(empty, [])
    assert told["headline"]["text"] == narrator.EMPTY and told["insights"] == [] and told["notes"] == []


def test_prompt_carries_fact_ids_and_numbers_but_no_rows() -> None:
    f, found = story_inputs()
    import json

    sent = json.loads(narrator.v2_prompt(f, found))
    assert sent["facts"][0]["id"] == "f0"
    assert all(set(x) == {"id", "type", "text", "numbers", "members"} for x in sent["facts"])
    assert "rows" not in json.dumps(sent["facts"][1:])


# --------------------------------------------------------------------------- Ask next


SPEC = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [{"ref": "dimension:region"}], "filters": [],
        "time": {"ref": "dimension:planned_deployment_date", "range": {"relative": "this_quarter"}}}


def test_follow_ups_driver_compare_and_drill() -> None:
    _, found = story_inputs()
    driver = {"type": "driver", "dimension": {"ref": "dimension:portfolio", "label": "Portfolio"}}
    got = nextsteps.follow_ups(SPEC, SNAP, found, driver=driver, can_compare=True)
    assert [g["kind"] for g in got] == ["driver", "compare", "drill"]
    assert got[0]["ops"] == [{"op": "add_breakdown", "ref": "dimension:portfolio"}]
    assert got[1]["ops"] == [{"op": "compare_previous"}]
    assert got[2] == {"kind": "drill", "label": "Drill into KSA",
                      "ops": [{"op": "add_filter", "ref": "dimension:region", "values": ["KSA"]}]}


def test_follow_ups_skip_what_the_answer_already_has() -> None:
    _, found = story_inputs()
    driver = {"type": "driver", "dimension": {"ref": "dimension:region", "label": "Region"}}  # already broken down
    compared = {**SPEC, "compare": {"kind": "previous_period"}}
    assert nextsteps.follow_ups(compared, SNAP, found, driver=driver, can_compare=True) == [
        g for g in nextsteps.follow_ups(SPEC, SNAP, found, can_compare=False) if g["kind"] == "drill"]
    no_range = {**SPEC, "time": None, "breakdowns": []}
    assert nextsteps.follow_ups(no_range, SNAP, found, can_compare=True) == []


def test_driver_candidates_follow_the_model_order_and_skip_used_dimensions() -> None:
    spec = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [{"ref": "dimension:region"}],
            "filters": [{"ref": "dimension:portfolio", "op": "=", "values": ["X"]}]}
    keys = [d.key for d in nextsteps.driver_candidates(spec, SNAP)]
    assert keys == ["programme", "cio", "delivery_manager"]
    assert all(not d.is_temporal for d in nextsteps.driver_candidates({**spec, "breakdowns": []}, SNAP))


def test_strongest_driver_is_the_one_whose_lead_explains_most() -> None:
    a = insights.drivers({"KSA": 10, "UAE": 5}, {"KSA": 4, "UAE": 5}, "now", "before", "Open projects", "integer")[0]
    b = insights.drivers({"P1": 8, "P2": 7}, {"P1": 5, "P2": 4}, "now", "before", "Open projects", "integer")[0]
    dims = nextsteps.driver_candidates({"measure": {"ref": "measure:open_projects"}, "breakdowns": []}, SNAP)
    best = nextsteps.strongest([(dims[0], a), (dims[1], b)])
    assert best is not None and best[0] is dims[0]
    assert nextsteps.strongest([]) is None


# --------------------------------------------------------------------------- live-check fixes (EXEC-076)


MONTH = {"name": "month", "kind": "period", "label": "Month", "ref": "dimension:planned_deployment_date"}
COST = {"name": "m", "kind": "measure", "label": "Forecast cost", "format": "currency"}


def test_period_headline_skips_a_trailing_empty_period_and_names_not_set() -> None:
    rows = [[None, 459681], ["2026-10-01", 69620], ["2026-11-01", 33037], ["2026-12-01", None]]
    f = narrator.facts({"columns": [MONTH, COST], "rows": rows})
    told = narrator.template(f)
    assert told["headline"] == "Forecast cost in 2026-11-01: $33,037."
    assert told["sentences"][0]["text"] == "4 periods shown. Highest: Not set ($459,681)."
    assert narrator.grounded(told["headline"], f)


def test_negative_currency_and_no_range_below_zero_for_a_cost() -> None:
    assert narrator.fmt(-225320, "currency") == "-$225,320"
    assert narrator.fmt(-0.5, "currency") == "-$0.50" and narrator.fmt(0, "currency") == "$0"
    series = [(f"2026-{i:02d}-01", v) for i, v in enumerate([0, 5, 2, 380, 4, 0, 3, 6], 1)]
    [brk] = [x for x in insights.trend(series, "Forecast cost", "currency") if x["type"] == "break"]
    assert brk["numbers"]["lower"] == 0 and "$-" not in brk["text"] and "-$" not in brk["text"]


def test_one_period_with_a_comparison_is_a_change_like_a_single_figure() -> None:
    YEAR = {"name": "year", "kind": "period", "label": "Year", "ref": "dimension:planned_deployment_date"}
    result = {"columns": [YEAR, N], "rows": [["2026-01-01", 42]], "row_count": 1, "truncated": False}
    cmp = {"current": 42, "previous": 14, "previous_range": "5 Mar 2025 to 31 Dec 2025"}
    [f] = insights.detect(result, additive=True, comparison=cmp)
    assert f["type"] == "change" and f["numbers"]["delta"] == 28
