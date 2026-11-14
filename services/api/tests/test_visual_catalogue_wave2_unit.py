"""Visual catalogue wave 2 without a database (US-188; RULE-008; COMP-001): densities, quartiles, survival, ranks,
flows, sets and cells are worked out here, never in the browser. Results are written by hand in the shape the query
service returns; the snapshot is the PMO pack bound to the synthetic fixture."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from test_planner_gold_unit import SNAP
from test_visual_catalogue_unit import period
from test_visuals_unit import dim, meas

from tela_api.ask.planner import VISUAL_WORDS
from tela_api.content.common import VISUAL_NAMES, default_size, visual_options
from tela_api.query import viz
from tela_api.query.viz import visualise
from tela_chart_spec import VISUALS
from tela_query_spec.spec import Visual


def text(name: str, label: str) -> dict[str, Any]:
    return {"name": name, "kind": "dimension", "label": label, "ref": "", "data_type": "text"}


def test_every_visual_is_named_and_stored() -> None:
    assert set(VISUAL_NAMES) == set(VISUALS)
    for v in ("calendar", "sankey", "upset", "beeswarm", "ridgeline", "survival", "bump", "treemap", "waffle"):
        Visual(type=v)  # type: ignore[arg-type]
    assert VISUAL_WORDS["alluvial"] == "sankey" and VISUAL_WORDS["raincloud"] == "ridgeline"
    assert VISUAL_WORDS["time to event"] == "survival" and VISUAL_WORDS["isotype"] == "waffle"


def test_calendar_days_levels_and_months() -> None:
    first = date(2026, 1, 5)
    rows = [[(first + timedelta(days=i)).isoformat(), i % 5] for i in range(0, 100, 2)]
    res = {"columns": [period("day", "Day"), meas("status_reports_total")], "rows": rows}
    viz_ = visualise(res, SNAP, "calendar")
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "calendar" and viz_["note"] is None
    assert (f["start"], f["end"], f["years"]) == ("2026-01-05", "2026-04-13", [2026])
    assert f["edges"] == [1, 2, 3] and f["max"] == 4  # quartiles of 0..4
    months = f["table"]["rows"]
    assert [m[0] for m in months] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert sum(m[1] for m in months) == sum(r[1] for r in rows) and sum(m[2] for m in months) == 50
    # Month periods are not days: no calendar.
    by_month = {"columns": res["columns"], "rows": [[f"2026-{m:02d}", m] for m in range(1, 7)]}
    assert visualise(by_month, SNAP, "calendar")["visual"] == "trend"


REQUESTS = {
    "columns": [text("kind", "Request kind"), dim("intake_gate"), text("outcome", "Outcome"), meas("total_requests")],
    "rows": [["New", "DOU", "Converted", 10], ["New", "DOU", "Rejected", 4], ["New", "DARB", "Converted", 6],
             ["Change", "DOU", "On hold", 3], ["Change", "DARB", "Converted", 2], ["Change", "DARB", "Rejected", 0]],
}


def test_sankey_nodes_and_links() -> None:
    viz_ = visualise(REQUESTS, SNAP)
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "sankey"  # three breakdowns: a flow, not a table
    labels = [(n["label"], n["level"], n["value"]) for n in f["nodes"]]
    assert labels == [("New", 0, 20), ("DOU", 1, 17), ("Converted", 2, 18), ("Rejected", 2, 4), ("DARB", 1, 8),
                      ("Change", 0, 5), ("On hold", 2, 3)]
    named = {(f["nodes"][lk["source"]]["label"], f["nodes"][lk["target"]]["label"]): lk["value"] for lk in f["links"]}
    assert named == {("New", "DOU"): 14, ("DOU", "Converted"): 10, ("DOU", "Rejected"): 4, ("New", "DARB"): 6,
                     ("DARB", "Converted"): 8, ("Change", "DOU"): 3, ("DOU", "On hold"): 3, ("Change", "DARB"): 2}
    assert f["levels"] == ["Request kind", "Intake gate", "Outcome"]
    assert len(f["table"]["rows"]) == len(f["links"]) and default_size("sankey") == (12, 5)


def test_sankey_keeps_a_value_on_two_levels_apart() -> None:
    res = {"columns": [text("a", "From"), text("b", "To"), meas("total_requests")],
           "rows": [["Open", "Open", 5], ["Open", "Closed", 3]]}
    f = visualise(res, SNAP, "sankey")["chart_spec"]["figures"]
    assert [(n["label"], n["level"]) for n in f["nodes"]] == [("Open", 0), ("Open", 1), ("Closed", 1)]


def test_upset_sets_and_intersections() -> None:
    cols = [text("no_code", "No budget code"), text("no_pm", "No PM"), text("no_baseline", "Not baselined"),
            meas("open_projects")]
    rows = [["Yes", "Yes", "No", 12], ["Yes", "No", "No", 20], ["No", "No", "No", 50], ["Yes", "Yes", "Yes", 3],
            ["No", "Yes", "Yes", 7], ["No", "No", "Yes", 0]]
    viz_ = visualise({"columns": cols, "rows": rows}, SNAP)
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "upset"
    assert f["sets"] == [{"label": "No budget code", "size": 35}, {"label": "No PM", "size": 22},
                         {"label": "Not baselined", "size": 10}]
    assert [(x["members"], x["value"]) for x in f["intersections"]] == [
        ([False, False, False], 50), ([True, False, False], 20), ([True, True, False], 12),
        ([False, True, True], 7), ([True, True, True], 3)]  # zero rows are left out
    assert f["total"] == 92 and f["table"]["rows"][0] == ["None of these", 50]
    assert f["table"]["rows"][2] == ["No budget code + No PM", 12]


PROJECTS = [f"P{i:02d}" for i in range(24)]


def _budgets() -> dict[str, Any]:
    groups = ["Digital", "Infra", "Data"]
    return {"columns": [text("project", "Project"), dim("portfolio"), meas("approved_budget_total")],
            "rows": [[p, groups[i % 3], (i % 3 + 1) * 100 + i] for i, p in enumerate(PROJECTS)]}


def test_beeswarm_quartiles_per_group() -> None:
    one = {"columns": _budgets()["columns"][::2], "rows": [[r[0], r[2]] for r in _budgets()["rows"]]}
    viz_ = visualise(one, SNAP, "beeswarm")
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "beeswarm" and viz_["chart_spec"]["x"] == "project"
    (g,) = f["groups"]
    values = sorted(r[1] for r in one["rows"])
    assert g["label"].startswith("Approved budget") and g["n"] == 24 and (g["min"], g["max"]) == (values[0], values[-1])
    assert (f["min"], f["max"]) == (values[0], values[-1])


def test_ridgeline_densities_and_boxes() -> None:
    viz_ = visualise(_budgets(), SNAP, "ridgeline")
    spec = viz_["chart_spec"]
    f = spec["figures"]
    assert viz_["visual"] == "ridgeline" and (spec["x"], spec["y"]) == ("project", "portfolio")
    assert [g["label"] for g in f["groups"]] == ["Data", "Infra", "Digital"]  # highest median first
    data = f["groups"][0]
    assert data["n"] == 8 and (data["min"], data["median"], data["max"]) == (302, 312.5, 323)
    assert all(len(g["density"]) == f["points"] and max(g["density"]) == 1 for g in f["groups"])
    assert f["min"] < 100 and f["max"] > 323 and f["min"] >= 0  # a padded shared range, not below zero
    assert f["table"]["rows"][0][:2] == ["Data", 8]
    assert "ridgeline" in visual_options(_budgets(), SNAP)["suitable"]


def test_time_to_event_kaplan_meier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(viz, "today", lambda: date(2026, 1, 31))
    start = "2026-01-01"
    ends = {"A": "2026-01-11", "B": "2026-01-11", "C": None, "D": "2026-01-21", "E": "2026-01-31", "F": None,
            "G": "2026-01-06", "H": "2026-01-26", "I": None, "J": "2026-01-16"}
    rows = [[k, start, v] for k, v in ends.items()] + [["K", None, "2026-01-02"], ["L", "2026-01-10", "2026-01-05"]]
    res = {"columns": [text("project", "Project"), period("start", "Start"), period("go_live", "Go-live")],
           "rows": rows}
    viz_ = visualise(res, SNAP, "survival")
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "survival" and f["left_out"] == 2 and f["event"] == "Go-live"
    (curve,) = f["curves"]
    assert (curve["n"], curve["events"]) == (10, 7)
    # Events at day 5 (1 of 10), 10 (2 of 9), 15 (1 of 7), 20 (1 of 6), 25 (1 of 5), 30 (1 of 4 + 3 still open).
    steps = [(p["t"], p["at_risk"], p["events"], p["censored"], p["s"]) for p in curve["points"]]
    assert steps == [(0, 10, 0, 0, 1.0), (5, 10, 1, 0, 0.9), (10, 9, 2, 0, 0.7), (15, 7, 1, 0, 0.6),
                     (20, 6, 1, 0, 0.5), (25, 5, 1, 0, 0.4), (30, 4, 1, 3, 0.3)]
    assert curve["median"] == 20 and f["max_t"] == 30


def test_bump_ranks_per_period() -> None:
    values = {"North": [5, 9, 9], "South": [8, 4, 2], "East": [3, 6, 10]}
    rows = [[f"2026-0{m + 1}", r, v[m]] for r, v in values.items() for m in range(3)]
    res = {"columns": [period("period", "Month"), dim("region"), meas("open_projects")], "rows": rows}
    viz_ = visualise(res, SNAP, "bump")
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "bump" and f["periods"] == ["2026-01", "2026-02", "2026-03"]
    assert [(i["label"], i["ranks"]) for i in f["items"]] == [("East", [3, 2, 1]), ("North", [2, 1, 2]),
                                                              ("South", [1, 3, 3])]
    assert f["table"]["rows"][0] == ["East", 3, 2, 1]


def test_bump_ties_share_the_better_rank() -> None:
    rows = [["2026-01", "North", 5], ["2026-01", "South", 5], ["2026-01", "East", 1]] * 1
    rows += [[f"2026-0{m}", r, 1] for m in (2, 3) for r in ("North", "South", "East")]
    res = {"columns": [period("period", "Month"), dim("region"), meas("open_projects")], "rows": rows}
    items = {i["label"]: i["ranks"] for i in visualise(res, SNAP, "bump")["chart_spec"]["figures"]["items"]}
    assert items == {"East": [3, 1, 1], "North": [1, 1, 1], "South": [1, 1, 1]}


def test_treemap_two_levels_with_shares() -> None:
    res = {"columns": [dim("portfolio"), dim("programme"), meas("tcv_open_total")],
           "rows": [["Digital", "Web", 300], ["Digital", "Apps", 100], ["Infra", "Network", 500], ["Infra", "DC", 0],
                    ["Data", "Lake", 100]]}
    viz_ = visualise(res, SNAP, "treemap")
    f = viz_["chart_spec"]["figures"]
    assert viz_["visual"] == "treemap" and viz_["chart_spec"]["x"] == "portfolio"
    assert [(n["label"], n["value"], n["share"]) for n in f["nodes"]] == [("Infra", 500, 50), ("Digital", 400, 40),
                                                                         ("Data", 100, 10)]
    assert f["nodes"][1]["children"] == [{"label": "Web", "value": 300, "share": 30},
                                         {"label": "Apps", "value": 100, "share": 10}]
    assert f["nodes"][0]["children"] == [{"label": "Network", "value": 500, "share": 50}]  # zero tiles left out
    assert f["total"] == 1000 and f["table"]["rows"][0] == ["Infra", "Network", 500, 50]


def test_waffle_cells_add_to_100() -> None:
    res = {"columns": [dim("region"), meas("open_projects")], "rows": [["North", 1], ["South", 1], ["East", 1]]}
    f = visualise(res, SNAP, "waffle")["chart_spec"]["figures"]
    assert [p["cells"] for p in f["parts"]] == [34, 33, 33] and sum(p["cells"] for p in f["parts"]) == 100
    meter = {"columns": [meas("budget_code_set"), meas("open_projects")], "rows": [[31, 111]]}
    viz_ = visualise(meter, SNAP, "waffle")
    parts = viz_["chart_spec"]["figures"]["parts"]
    assert viz_["visual"] == "waffle"
    assert [(p["value"], p["cells"]) for p in parts] == [(31, 28), (80, 72)]
    assert parts[1]["label"].startswith("Rest of") and default_size("waffle") == (4, 4)


def test_chat_edit_reads_the_new_visual_words() -> None:
    from test_authoring_unit import state

    from tela_api.content.chat_edit import parse

    for words, visual in (("show open by region as a treemap", "treemap"),
                          ("turn open by state into a waffle", "waffle"),
                          ("show open by region as a bump chart", "bump"),
                          ("show open by state as a sankey", "sankey")):
        ops = parse(words, state(), SNAP.dimensions)[0]
        assert [o["to"] for o in ops if o["op"] == "set_visual"] == [visual], words


def test_planner_reads_as_a_treemap() -> None:
    from tela_api.ask.planner import plan_question

    plan = plan_question("open projects by portfolio as a treemap", SNAP, visible_measures=SNAP.measures,
                         visible_dims=SNAP.dimensions, values={})
    assert plan.spec is not None and plan.spec["visual"] == {"type": "treemap"}
