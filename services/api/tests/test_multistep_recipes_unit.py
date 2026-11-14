"""Multi-step plans and analysis recipes (ENH-012, US-179; FEAT-063 / FEAT-014), without a database.

Acceptance: a plan of up to three specs where a later step uses an earlier step's single value or value list
(``{"from_step": n}``), each step a valid spec on its own; recipes are named, parameterised templates in the pack
that know their operations, chart and insight checks, indexed like the report blueprints, and their slots are
filled from the question or from AG-03's pick, only with options the person can see.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import SNAP

from tela_api.ask import recipes as analysis
from tela_api.ask import steps
from tela_api.ask.planner import Plan, PlannerOut, plan_question, spec_from_planner
from tela_api.ask.repair import precheck
from tela_api.ask.rewrite import describe
from tela_api.knowledge.gold import comparable
from tela_api.knowledge.guidance import lint_recipes, load_recipes, recipe_chunks, recipe_for_model
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, load_manifest

MEASURES = [m for m in SNAP.measures if m.status == "certified"]
DIMS = list(SNAP.dimensions)
RECIPES = load_recipes("pmo_tpg")
VISIBLE = [r for r in (analysis.visible(x, MEASURES, DIMS) for x in RECIPES) if r is not None]


def rules(question: str) -> Plan:
    return plan_question(question, SNAP, visible_measures=MEASURES, visible_dims=DIMS, values={})


def result(columns: list[tuple[str, str]], rows: list[list[Any]], **kw: Any) -> dict[str, Any]:
    return {"columns": [{"name": n, "kind": k, "label": n} for n, k in columns], "rows": rows,
            "row_count": len(rows), "truncated": False, **kw}


# --------------------------------------------------------------------------- multi-step plans


def test_inline_references_become_bindings_and_each_step_is_a_spec() -> None:
    plan = steps.lift({
        "measure": {"ref": "measure:total_projects"}, "breakdowns": [{"ref": "dimension:region"}],
        "filters": [{"ref": "dimension:region", "op": "in", "values": {"from_step": 1}}],
        "having": {"op": ">", "value": {"from_step": 2, "take": "mean"}},
        "steps": [{"measure": {"ref": "measure:overdue_go_lives"}, "breakdowns": [{"ref": "dimension:region"}]},
                  {"measure": {"ref": "measure:total_projects"}, "breakdowns": [{"ref": "dimension:region"}]}]})
    assert plan["uses"] == [{"step": 1, "at": "filter", "ref": "dimension:region", "op": "in", "take": "values"},
                            {"step": 2, "at": "having", "op": ">", "take": "mean"}]
    assert "having" not in plan and plan["filters"] == [] and len(plan["steps"]) == 2
    assert steps.skeleton(plan) == {"measure": {"ref": "measure:total_projects"},
                                    "breakdowns": [{"ref": "dimension:region"}], "filters": []}
    assert steps.wanted(plan) == {1: ["values"], 2: ["mean"]}


@pytest.mark.parametrize(("plan", "error"), [
    ({"measure": {"ref": "measure:a"}, "filters": [{"ref": "dimension:r", "op": "in", "values": {"from_step": 1}}]},
     "Step 1 can only use an earlier step"),
    ({"measure": {"ref": "measure:a"}, "having": {"op": ">", "value": {"from_step": 2}},
      "steps": [{"measure": {"ref": "measure:b"}}]}, r"Step 2 can only use an earlier step \(1 to 1\)"),
    ({"measure": {"ref": "measure:a"}, "filters": [{"ref": "dimension:r", "op": ">", "values": {"from_step": 1,
                                                                                                "take": "values"}}],
      "steps": [{"measure": {"ref": "measure:b"}}]}, "A value list goes with 'in' or 'not_in'"),
    ({"measure": {"ref": "measure:a"}, "having": {"op": ">", "value": {"from_step": 1, "take": "values"}},
      "steps": [{"measure": {"ref": "measure:b"}}]}, "compared with one value"),
    ({"measure": {"ref": "measure:a"}, "having": {"op": ">", "value": {"from_step": 2}},
      "steps": [{"measure": {"ref": "measure:b"}}, {"measure": {"ref": "measure:c"}}]}, "Step 1 is not used"),
    ({"measure": {"ref": "measure:a"}, "having": {"op": ">", "value": {"from_step": 1}},
      "steps": [{"measure": {"ref": "measure:b"}}] * 3}, "at most 3 steps"),
])
def test_references_that_cannot_run_are_refused(plan: dict[str, Any], error: str) -> None:
    with pytest.raises(steps.StepError, match=error):
        steps.lift(plan)


def test_steps_nothing_uses_leave_a_one_step_plan() -> None:
    plan = steps.lift({"measure": {"ref": "measure:a"}, "steps": [{"measure": {"ref": "measure:b"}}]})
    assert plan["measure"] == {"ref": "measure:a"} and not steps.is_multi(plan) and "uses" not in plan


def test_what_a_step_gives_comes_from_its_own_rows() -> None:
    by_region = result([("region", "dimension"), ("n", "measure")], [["KSA", 4], ["GCC - UAE", 2], [None, 1]])
    assert steps.take(by_region, "values") == ["KSA", "GCC - UAE"]
    assert steps.take(by_region, "mean") == pytest.approx(7 / 3, rel=1e-5)
    assert steps.take(by_region, "median") == 2
    assert steps.take(result([("n", "measure")], [[12.5]]), "value") == 12.5
    with pytest.raises(steps.StepError, match="must return one row; it returned 3"):
        steps.take(by_region, "value")
    with pytest.raises(steps.StepError, match="needs a breakdown"):
        steps.take(result([("n", "measure")], [[1]]), "values")
    with pytest.raises(steps.StepError, match="too many"):
        steps.take({**by_region, "truncated": True}, "values")
    with pytest.raises(steps.StepError, match="hidden for your role"):
        steps.take({"hidden": True, "columns": [], "rows": []}, "value")


def test_binding_puts_the_answers_in_place() -> None:
    final = {"measure": {"ref": "measure:a"}, "filters": [{"ref": "dimension:status", "op": "=", "values": ["x"]}],
             "uses": [{"step": 1, "at": "filter", "ref": "dimension:region", "op": "in", "take": "values"},
                      {"step": 2, "at": "having", "op": "<", "take": "value"}]}
    bound = steps.bind(final, {(1, "values"): ["KSA"], (2, "value"): 3.5})
    assert bound["filters"][1] == {"ref": "dimension:region", "op": "in", "values": ["KSA"]}
    assert bound["having"] == {"op": "<", "value": 3.5} and "uses" not in bound
    with pytest.raises(steps.StepError, match="Step 1 found nothing"):
        steps.bind(final, {(1, "values"): [], (2, "value"): 1})


def test_above_average_is_planned_in_two_steps() -> None:
    plan = rules("Portfolios with above-average overdue go-lives")
    assert plan.status == "ok" and plan.spec is not None
    # A count adds up: the average portfolio is the mean of the portfolios' figures.
    assert plan.spec["steps"] == [{"measure": {"ref": "measure:overdue_go_lives"},
                                   "breakdowns": [{"ref": "dimension:portfolio"}], "filters": []}]
    assert plan.spec["uses"] == [{"step": 1, "at": "having", "op": ">", "take": "mean"}]
    assert plan.spec["breakdowns"] == [{"ref": "dimension:portfolio"}]
    assert plan.interpreted[-1] == "Two steps: Overdue go-lives for the average Portfolio, then the Portfolio above it"
    assert describe(plan.spec, SNAP) == ("step 1: Overdue go-lives by Portfolio; then Overdue go-lives by Portfolio, "
                                         "above step 1's average across its rows")
    # A rate doesn't add up: the figure over every row is the average to beat.
    rate = rules("Regions with below average budget code compliance")
    assert rate.spec is not None and rate.spec["steps"][0]["breakdowns"] == []
    assert rate.spec["uses"] == [{"step": 1, "at": "having", "op": "<", "take": "value"}]
    # Nothing to compare against without a breakdown: one step.
    assert "steps" not in (rules("Are overdue go-lives above average?").spec or {})


def test_ag03_steps_become_a_plan_and_bad_references_are_typed() -> None:
    out = PlannerOut.model_validate({
        "measure": "total_projects", "breakdowns": ["region"],
        "filters": [{"ref": "region", "op": "in", "values": {"from_step": 1}}],
        "steps": [{"measure": "overdue_go_lives", "breakdowns": ["region"], "having": {"op": ">", "value": 0}}]})
    spec = spec_from_planner(out, SNAP)
    assert spec is not None and spec["filters"] == []
    assert spec["uses"] == [{"step": 1, "at": "filter", "ref": "dimension:region", "op": "in", "take": "values"}]
    assert spec["steps"][0]["having"] == {"op": ">", "value": 0}
    assert precheck(out, SNAP, MEASURES, DIMS) == []
    bad = out.model_copy(update={"filters": [{"ref": "region", "op": "in", "values": {"from_step": 2}}]})
    assert spec_from_planner(bad, SNAP) is None
    assert [e.typed for e in precheck(bad, SNAP, MEASURES, DIMS)] == [
        "steps: Step 2 can only use an earlier step (1 to 1)."]
    wrong = out.model_copy(update={"steps": [out.steps[0].model_copy(update={"measure": "measure:zzz"})]})
    assert precheck(wrong, SNAP, MEASURES, DIMS)[0].typed.startswith("step 1: measure:zzz is not a certified measure")


def test_votes_tell_multi_step_plans_apart() -> None:
    one = rules("Portfolios with above-average overdue go-lives").spec
    again = rules("overdue go-lives by portfolio above average").spec
    other = rules("Portfolios with below-average overdue go-lives").spec
    assert one is not None and again is not None and other is not None
    assert comparable(one) == comparable(again) != comparable(other)
    assert comparable(one) != comparable(steps.skeleton(one))


# --------------------------------------------------------------------------- recipes


def test_pack_recipes_name_only_what_the_pack_has() -> None:
    """CI pack lint, and the six recipes of the story."""
    manifest = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")[0]
    assert lint_recipes(RECIPES, manifest) == []
    assert [r.id for r in RECIPES] == ["variance_bridge", "pareto_80_20", "aging_buckets", "funnel_by_gate",
                                       "overdue_trend", "compliance_rate"]
    assert {r.chart for r in RECIPES} == {"waterfall", "pareto", "columns", "funnel", "trend", "bullet"}
    assert all(r.insights for r in RECIPES) and load_recipes("no_such_pack") == []


def test_recipes_become_knowledge_chunks_cut_to_the_model() -> None:
    chunks = {c["metadata"]["recipe"]: c for c in recipe_chunks(RECIPES, SNAP)}
    assert len(chunks) == 6
    pareto = chunks["pareto_80_20"]
    assert pareto["metadata"]["kind"] == "analysis_recipe" and pareto["metadata"]["term"] == "Pareto 80/20"
    assert "Answers: Which few portfolio account for most of forecast cost (eac, usd)?" in pareto["content"]
    assert "Chart: pareto" in pareto["content"] and "Insight checks: concentration, outlier" in pareto["content"]
    assert pareto["metadata"]["definition"]["spec"]["share_of_total"] == ["{measure}"]
    # A model without the default measure offers the next; without any measure of a slot, the recipe is left out.
    snap = SNAP.model_copy(update={"measures": [m for m in SNAP.measures if m.key != "forecast_cost_total"]})
    cut = recipe_for_model(RECIPES[1], snap)
    assert cut is not None and cut.slots["measure"].default == "approved_budget_total"
    none = SNAP.model_copy(update={"measures": [m for m in SNAP.measures
                                                if m.key not in ("total_requests", "open_demand")]})
    assert "funnel_by_gate" not in {c["metadata"]["recipe"] for c in recipe_chunks(RECIPES, none)}


@pytest.mark.parametrize(("question", "recipe", "measure", "slots", "visual"), [
    ("Pareto of forecast cost by portfolio", "pareto_80_20", "forecast_cost_total",
     {"dimension": "dimension:portfolio"}, "pareto"),
    ("80/20 of overdue go-lives by programme", "pareto_80_20", "overdue_go_lives",
     {"dimension": "dimension:programme"}, "pareto"),
    ("Variance bridge by region", "variance_bridge", "net_forecast_variance", {"dimension": "dimension:region"},
     "waterfall"),
    ("Aging of on-hold projects", "aging_buckets", "on_hold", {"dimension": "dimension:on_hold_aging"}, "columns"),
    ("Show the funnel by gate", "funnel_by_gate", "total_requests", {"dimension": "dimension:intake_gate"},
     "funnel"),
    ("Overdue trend by quarter", "overdue_trend", "overdue_go_lives", {"grain": "quarter"}, "trend"),
    ("Compliance rate of baselining by CIO", "compliance_rate", "baselining_compliance_pct",
     {"dimension": "dimension:cio"}, "bullet"),
])
def test_rules_fill_a_recipes_slots_from_the_question(question: str, recipe: str, measure: str,
                                                      slots: dict[str, str], visual: str) -> None:
    named = analysis.match(VISIBLE, question)
    assert named is not None and named.id == recipe
    plan = analysis.plan(named, question, rules(question), SNAP, MEASURES, DIMS)
    assert plan is not None and plan.spec is not None
    assert plan.spec["measure"] == {"ref": f"measure:{measure}"} and plan.spec["visual"] == {"type": visual}
    assert slots.items() <= plan.spec["recipe"]["slots"].items()
    assert plan.interpreted[-1].startswith(f"Analysis recipe: {named.title} (")


@pytest.mark.parametrize("question", ["Baselining compliance by CIO", "PM assignment compliance by lifecycle phase",
                                      "Pareto of PCRs by PCR reason", "Funnel of open demand by request region"])
def test_a_recipe_never_overrides_what_was_asked(question: str) -> None:
    """A measure or breakdown the recipe doesn't offer keeps the rules' plan (no recipe, or none applied)."""
    named = analysis.match(VISIBLE, question)
    assert named is None or analysis.plan(named, question, rules(question), SNAP, MEASURES, DIMS) is None


def test_recipe_operations_and_the_questions_filters() -> None:
    named = analysis.match(VISIBLE, "Pareto of forecast cost by portfolio")
    assert named is not None
    asked = Plan("ok", spec={"measure": {"ref": "measure:forecast_cost_total"}, "breakdowns": [],
                             "filters": [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}],
                             "time": {"ref": "dimension:planned_deployment_date", "range": {"relative": "this_year"}}})
    plan = analysis.plan(named, "Pareto of forecast cost by portfolio", asked, SNAP, MEASURES, DIMS)
    assert plan is not None and plan.spec is not None
    assert plan.spec["share_of_total"] == ["measure:forecast_cost_total"]
    assert plan.spec["sort"] == [{"by": "measure", "dir": "desc"}] and plan.spec["limit"] == 25
    assert plan.spec["filters"] == [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}]
    assert plan.spec["time"] == {"ref": "dimension:planned_deployment_date", "range": {"relative": "this_year"}}
    trend = analysis.match(VISIBLE, "overdue trend")
    assert trend is not None
    spec = analysis.fill(trend, SNAP, {"measure": None})
    assert spec["time"] == {"ref": "dimension:planned_deployment_date", "grain": "month"}
    assert spec["moving_avg"] == {"window": 3}


def test_slots_take_only_options_the_person_can_see() -> None:
    hidden = [m for m in MEASURES if m.key != "forecast_cost_total"]
    pareto = analysis.visible(RECIPES[1], hidden, DIMS)
    assert pareto is not None and "forecast_cost_total" not in pareto.slots["measure"].options
    spec = analysis.fill(pareto, SNAP, {"measure": "measure:forecast_cost_total", "dimension": "dimension:region"})
    assert spec["measure"] == {"ref": "measure:approved_budget_total"}  # the hidden pick falls back to the default
    assert spec["breakdowns"] == [{"ref": "dimension:region"}]
    # A dimension the measure can't reach is not used: the first option it can is.
    unlinked = SNAP.model_copy(update={"links": []})
    risks = analysis.fill(pareto, unlinked, {"measure": "measure:open_risks", "dimension": "dimension:portfolio"})
    assert risks["breakdowns"] == [{"ref": "dimension:risk_category"}]
    assert analysis.visible(RECIPES[3], [m for m in MEASURES if m.entity != "project_requests"], DIMS) is None


def test_ag03_picks_a_recipe_and_fills_its_slots() -> None:
    out = PlannerOut.model_validate({
        "measure": "measure:open_projects", "filters": [{"ref": "dimension:region", "values": ["KSA"]}],
        "recipe": {"id": "aging_buckets", "slots": {"measure": "measure:open_projects",
                                                    "dimension": "dimension:on_hold_aging"}}})
    spec = analysis.from_planner(out, VISIBLE, SNAP)
    assert spec is not None and spec["breakdowns"] == [{"ref": "dimension:on_hold_aging"}]
    assert spec["recipe"]["id"] == "aging_buckets" and spec["visual"] == {"type": "columns"}
    assert spec["filters"] == [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}]
    # An option not listed falls back; a recipe not listed is a plain plan.
    odd = out.model_copy(update={"recipe": {"id": "aging_buckets", "slots": {"dimension": "dimension:portfolio"}}})
    assert (analysis.from_planner(odd, VISIBLE, SNAP) or {})["breakdowns"] == [{"ref": "dimension:lateness_bucket"}]
    plain = out.model_copy(update={"recipe": {"id": "no_such_recipe"}})
    assert "recipe" not in (analysis.from_planner(plain, VISIBLE, SNAP) or {})
    block = analysis.recipes_block(VISIBLE)
    assert '<recipe id="pareto_80_20" title="Pareto 80/20" chart="pareto"' in block
    assert "dimension:portfolio|dimension:programme" in block and analysis.recipes_block([]) == ""


def test_the_recipes_insight_checks_lead() -> None:
    found = [{"id": "f1", "type": "outlier"}, {"id": "f2", "type": "data_note"}, {"id": "f3", "type": "concentration"}]
    led = analysis.lead_insights(found, {"insights": ["concentration", "outlier"]})
    assert [f["type"] for f in led] == ["concentration", "outlier", "data_note"]
    assert analysis.lead_insights(found, None) == found
