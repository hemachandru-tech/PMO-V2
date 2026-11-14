"""Dashboard-building knowledge without a database (US-159, FEAT-059 / FEAT-016).

The PMO pack's dashboard rules, report blueprints and chart patterns v0.2 are the real draft files; the model is the
pack bound to the synthetic TPG fixture (``test_planner_gold_unit.SNAP``). Covers the pack lint, the knowledge
chunks, which blueprint a sentence names, the report planner (AG-05) building catalogue layouts, the wording check
and the advisor (AG-07) suggesting the catalogue's visuals.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_advisor_unit import ctx, run
from test_planner_gold_unit import SNAP

from tela_api.ask.advisor import AdviceItem, AdviceOut, advise, candidates, from_llm
from tela_api.ask.orchestrator import Source
from tela_api.content import wording
from tela_api.content.planner import Planner
from tela_api.identity.scope import EntityScope, ResolvedScope
from tela_api.knowledge.guidance import (
    available,
    blueprint_chunks,
    lint,
    load_blueprints,
    load_rules,
    match_blueprints,
    rule_chunks,
    rules_for_model,
)
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, load_manifest

MANIFEST, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
RULES = load_rules("pmo_tpg")
BLUEPRINTS = load_blueprints("pmo_tpg")


def planner(measures: list[str] | None = None) -> Planner:
    scope = ResolvedScope("c", {e.key: EntityScope(all_rows=True) for e in SNAP.entities}, frozenset(), frozenset())
    src = Source({"id": "c", "name": "TPG", "engine": "postgres"}, SNAP, scope)
    if measures is not None:
        src.measures = [m for m in src.measures if m.key in measures]
    return Planner(src, MANIFEST, BLUEPRINTS)


def widgets(plan: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return next(s for s in plan["sections"] if s["key"] == key)["widgets"]


# --------------------------------------------------------------------------- pack files


def test_pack_guidance_files_name_only_what_the_pack_has() -> None:
    """CI pack lint: every measure, dimension and table column a blueprint or rule names is in the manifest."""
    assert RULES is not None and len(RULES.rules) == 10 and RULES.status == "draft"
    assert lint(BLUEPRINTS, RULES, MANIFEST) == []
    ids = {b.id for b in BLUEPRINTS}
    assert len(ids) == len(BLUEPRINTS) >= 30
    # Every report the pack says the data can't support is a blueprint that names the same gaps.
    for key, rep in MANIFEST.unavailable_reports.items():
        bp = next(b for b in BLUEPRINTS if b.id == key)
        assert bp.unavailable == rep["gaps"]
    # On the synthetic TPG model every available blueprint is complete: nothing is left out.
    assert all(available(b, SNAP)[1] == [] for b in BLUEPRINTS if not b.unavailable)
    assert load_rules("no_such_pack") is None and load_blueprints("no_such_pack") == []


def test_rules_and_blueprints_become_knowledge_chunks() -> None:
    assert RULES is not None
    rules = rule_chunks(rules_for_model(RULES.rules, SNAP))
    assert len(rules) == 10 and all(c["metadata"]["kind"] == "dashboard_rule" for c in rules)
    spend = next(c for c in rules if c["metadata"]["rule"] == "no_actual_spend")
    assert "Forecast cost (EAC)" in spend["content"] and spend["metadata"]["measure"] == "forecast_cost_total"
    chunks = {c["metadata"]["blueprint"]: c for c in blueprint_chunks(BLUEPRINTS, SNAP)}
    delayed = chunks["delayed_projects"]
    assert delayed["metadata"]["kind"] == "report_blueprint"
    assert "Population: Projects in delivery (Active)" in delayed["content"]
    assert "Visual: Days past planned go-live — Overdue go-lives by Days past planned go-live as columns" in \
        delayed["content"]
    assert {"overdue_go_lives", "rag_contradictions"} <= set(delayed["metadata"]["measures"])
    assert chunks["milestone_slippage"]["metadata"]["unavailable"] is True
    # A model without a blueprint's figures leaves it out; without one visual it says what is missing.
    snap = SNAP.model_copy(update={"measures": [m for m in SNAP.measures if m.key != "overdue_over_90"]})
    cut = next(c for c in blueprint_chunks(BLUEPRINTS, snap) if c["metadata"]["blueprint"] == "delayed_projects")
    assert "Not in this model: overdue over 90" in cut["content"]


@pytest.mark.parametrize(("sentence", "expected"), [
    ("Build me a delayed-projects report", ["delayed_projects"]),
    ("Which projects are late?", ["delayed_projects"]),
    ("CAPEX budget vs forecast cost", ["capex_budget"]),
    ("Delayed projects and CAPEX report", ["delayed_projects", "capex_budget"]),
    ("On-hold aging", ["on_hold_aging"]),
    ("Requests on hold aging", ["request_on_hold_aging"]),
    ("Baselining compliance by CIO", ["baselining"]),
    ("A heatmap of region by status", ["delivery_heatmap"]),
    ("CIO and portfolio view", ["cio_portfolio"]),
    ("Overdue go-lives by region", ["delayed_projects"]),
    ("Milestone slippage report", ["milestone_slippage"]),
    ("Open projects by region", []),
])
def test_a_sentence_names_catalogue_screens(sentence: str, expected: list[str]) -> None:
    assert [b.id for b in match_blueprints(BLUEPRINTS, sentence)] == expected


# --------------------------------------------------------------------------- the report planner (AG-05)


def test_planner_builds_the_delayed_projects_report_from_a_sentence() -> None:
    plan = planner().plan("Build me a delayed-projects report")
    assert [s["key"] for s in plan["sections"]] == ["headline", "bp_delayed_projects"]
    assert [h["label"] for h in plan["headline_figures"]] == [
        "Overdue go-lives", "Overdue more than 90 days", "Most overdue (days)", "RAG contradictions"]
    section = plan["sections"][1]
    assert section["blueprint"]["question"].startswith("Which projects are past their planned go-live")
    assert "due on the reporting date is not overdue" in section["footnote"]
    by_title = {w["title"]: w for w in section["widgets"]}
    assert by_title["Days past planned go-live"]["visual"] == "columns"
    assert by_title["Delayed by portfolio"]["visual"] == "ranked_bar"
    table = by_title["Delayed projects"]
    assert table["visual"] == "table" and table["spec"]["visual"]["detail"]["sort"] == {
        "field": "planned_deployment_date", "dir": "asc"}
    assert all(w["certified"] for s in plan["sections"] for w in s["widgets"])
    assert plan["gaps"] == [] and plan["notes"] == []
    assert {p["widget_ref"] for p in plan["layout_preview"]} == {w["ref"] for s in plan["sections"]
                                                                 for w in s["widgets"]}


def test_planner_builds_the_capex_report_with_its_filter_callout_and_wording() -> None:
    plan = planner().plan("Build a CAPEX spend report")
    assert plan["title"] == "CAPEX forecast cost report"
    assert plan["notes"] and "no actual spend" in plan["notes"][0]
    section = next(s for s in plan["sections"] if s["key"] == "bp_capex_budget")
    assert section["callouts"][0]["title"] == "Forecasts are estimates"
    capex = {"ref": "dimension:budget_type", "op": "=", "values": ["CAPEX"]}
    assert all(capex in w["spec"]["filters"] for s in plan["sections"] for w in s["widgets"])
    assert len(plan["headline_figures"]) == 5
    paired = next(w for w in section["widgets"] if w["visual"] == "paired_bars")
    assert paired["spec"]["extra_measures"] == [{"ref": "measure:forecast_cost_total"}]
    # The callout is reserved at the top of the section's layout.
    assert min(p["y"] for p in plan["layout_preview"] if p["section"] == "bp_capex_budget") == 2


def test_two_screens_in_one_sentence_and_the_gaps() -> None:
    plan = planner().plan("Delayed projects and CAPEX report, plus milestone slippage")
    assert [s["key"] for s in plan["sections"]] == ["headline", "bp_delayed_projects", "bp_capex_budget"]
    # The second screen's figures open its own section.
    assert [w["visual"] for w in widgets(plan, "bp_capex_budget")][:5] == ["figure"] * 5
    assert plan["gaps"] == [{"request_part": "Milestone slippage", "reason": "No milestone records in this model"}]


def test_planner_leaves_out_what_the_creator_cannot_see() -> None:
    plan = planner(measures=["overdue_go_lives", "rag_contradictions"]).plan("Delayed projects report")
    assert [h["label"] for h in plan["headline_figures"]] == ["Overdue go-lives", "RAG contradictions"]
    left = {g["request_part"] for g in plan["gaps"]}
    assert {"Overdue more than 90 days", "Most overdue (days)"} <= left
    assert all(g["reason"] == "Not in this model (part of the Delayed projects layout)" for g in plan["gaps"])


def test_without_a_screen_the_themes_still_plan() -> None:
    plan = planner().plan("Governance report")
    # Pack 2.0 (US-155): the starter reports are the blueprints; their section names the theme.
    keys = [s["key"] for s in plan["sections"]]
    assert "baselining" in keys and all(s["widgets"] for s in plan["sections"])


# --------------------------------------------------------------------------- wording


def test_wording_check_finds_and_fixes_spend_claims_and_mixed_status_words() -> None:
    found = wording.issues("Actual spend is overspent and the reported health is Amber.")
    assert {i.found for i in found} >= {"actual spend", "overspent", "reported health"}
    assert wording.fix("Spend by portfolio") == "Forecast cost by portfolio"
    assert wording.fix("Budget spent per region") == "Budget forecast per region"
    assert wording.fix("Actual spend and burn rate") == "Forecast cost and forecast cost"
    assert not wording.ok("Not set is counted as Green.")
    assert wording.ok("Forecast cost (EAC) is above the approved budget.")
    assert wording.ok("Calculated health and reported status disagree.")
    # A source with a real "Actual spend" measure may use its own word.
    assert wording.ok("Actual spend by region", allowed={"actual spend (usd)"})


def test_advisor_drops_model_text_that_claims_spend() -> None:
    c = ctx("forecast_cost_total", "portfolio", visual="ranked_bar")
    cands = candidates(c)
    out = AdviceOut(suggestions=[
        AdviceItem(candidate="c0", reason="Shows how much was spent per portfolio."),
        AdviceItem(ops=[{"op": "set_limit", "value": 10}], title="Top spenders", reason="Rank the spend."),
    ])
    picked = from_llm(out, c, cands, [])
    assert len(picked) == 1 and picked[0].reason == cands[0].reason  # the pattern's own reason stands


# --------------------------------------------------------------------------- chart patterns v0.2 (AG-07)


async def test_advisor_suggests_a_heat_grid_for_region_by_status() -> None:
    s = (await advise(ctx("open_projects", "region", "reported_status", visual="stacked_status"), run))["suggestions"]
    heat = next(x for x in s if x["pattern"] == "region_by_status_heat_grid")
    assert heat["visual"] == "heat_grid" and heat["ops"] == [{"op": "set_visual", "value": "heat_grid"}]
    already = await advise(ctx("open_projects", "region", "reported_status", visual="heat_grid"), run)
    assert all(x["pattern"] != "region_by_status_heat_grid" for x in already["suggestions"])


async def test_advisor_keeps_bands_in_order_and_offers_a_compliance_meter() -> None:
    bands = (await advise(ctx("on_hold", "on_hold_aging", visual="ranked_bar"), run))["suggestions"]
    assert any(x["visual"] == "columns" and x["kind"] == "visual" for x in bands)
    meter = (await advise(ctx("baselined", visual="figure"), run))["suggestions"]
    m = next(x for x in meter if x["pattern"] == "compliance_meter")
    assert m["visual"] == "meter" and m["ops"][0] == {"op": "add_measure", "ref": "measure:baselining_in_scope"}
