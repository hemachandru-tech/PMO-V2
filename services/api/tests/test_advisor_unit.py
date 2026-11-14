"""AG-07 chart suggestions without a database (US-104, FEAT-016; RULE-008; TEST-018 at unit level).

Results are made from the spec's shape only (fake counts), the way the advisor sees them: column kinds, row and
distinct counts. The PMO pack's chart patterns are the real draft file.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import SNAP

from tela_api.ask.advisor import LOOKS_GOOD, AdviceItem, AdviceOut, Context, advise, candidates, from_llm
from tela_api.knowledge.patterns import for_model, load_patterns, pattern_chunks
from tela_query_spec.spec import ref_key

PATTERNS = for_model(load_patterns("pmo_tpg"), SNAP)
COUNTS = {"region": 6, "portfolio": 4, "lifecycle_phase": 7, "reported_status": 3, "programme": 7,
          "delivery_manager": 20, "priority": 4, "request_state": 7}


def fake_result(spec: dict[str, Any]) -> dict[str, Any]:
    cols: list[dict[str, Any]] = []
    sizes: list[int] = []
    for b in spec.get("breakdowns", []):
        d = SNAP.dimension(ref_key(b["ref"]))
        assert d is not None
        cols.append({"name": d.key, "kind": "dimension", "label": d.label, "ref": b["ref"], "data_type": "text"})
        sizes.append(min(COUNTS.get(d.key, 5), spec.get("limit") or 1000))
    if spec.get("time", {}).get("grain"):
        cols.append({"name": "period", "kind": "period", "label": "Month", "ref": spec["time"]["ref"],
                     "data_type": "date"})
        sizes.append(12)
    for m in [spec["measure"], *spec.get("extra_measures", [])]:
        meas = SNAP.measure(ref_key(m["ref"]))
        assert meas is not None
        cols.append({"name": meas.key, "kind": "measure", "label": meas.label, "ref": m["ref"],
                     "data_type": "decimal", "format": meas.format})
    rows: list[list[Any]] = [[]]
    for i, n in enumerate(sizes):
        rows = [[*r, f"v{i}-{k}"] for r in rows for k in range(n)]
    n_meas = len(cols) - len(sizes)
    return {"columns": cols, "rows": [r + [k + 1] * n_meas for k, r in enumerate(rows)]}


async def run(spec: dict[str, Any]) -> dict[str, Any]:
    return fake_result(spec)


def ctx(measure: str, *breakdowns: str, visual: str, measures: list[str] | None = None,
        **extra: Any) -> Context:
    spec = {"model_version_id": "v", "connection_id": "c", "measure": {"ref": f"measure:{measure}"},
            "breakdowns": [{"ref": f"dimension:{b}"} for b in breakdowns], "filters": [], **extra}
    visible = [m for m in SNAP.measures if measures is None or m.key in measures]
    return Context(spec=spec, visual=visual, result=fake_result(spec), snap=SNAP, measures=visible,
                   dims=SNAP.dimensions, patterns=PATTERNS)


def test_patterns_file_fits_the_pack_model() -> None:
    raw = load_patterns("pmo_tpg")
    assert len(raw) >= 10 and len(PATTERNS) == len(raw)  # every pattern names real pack measures/dimensions
    chunks = pattern_chunks(PATTERNS, SNAP)
    assert all(c["metadata"]["kind"] == "chart_pattern" for c in chunks)
    fv = next(c for c in chunks if c["metadata"]["pattern"] == "forecast_vs_budget")
    assert "not actual spend" in fv["content"] and set(fv["metadata"]["measures"]) == {
        "forecast_cost_total", "approved_budget_total"}
    assert load_patterns("no_such_pack") == []


async def test_unsuitable_visual_is_fixed_first() -> None:
    """TEST-018: a donut with too many slices gets a suitable visual, with a reason, first."""
    c = ctx("open_projects", "delivery_manager", visual="donut")
    out = await advise(c, run)
    first = out["suggestions"][0]
    assert first["kind"] == "visual" and first["visual"] == "ranked_bar"
    assert first["reason"].startswith("20 slices is too many for a donut")
    assert first["ops"] == [{"op": "set_visual", "value": "ranked_bar"}]
    assert len(out["suggestions"]) <= 3 and out["message"] is None


async def test_pattern_adds_a_certified_measure_and_picks_a_suitable_visual() -> None:
    c = ctx("forecast_cost_total", "portfolio", visual="ranked_bar")
    s = (await advise(c, run))["suggestions"]
    fv = next(x for x in s if x["pattern"] == "forecast_vs_budget")
    assert fv["ops"] == [{"op": "add_measure", "ref": "measure:approved_budget_total"},
                         {"op": "set_visual", "value": "paired_bars"}]
    assert fv["visual"] == "paired_bars" and "Added Approved budget (USD)" in fv["changes"]


async def test_never_adds_a_measure_the_person_cannot_see() -> None:
    c = ctx("forecast_cost_total", "portfolio", visual="ranked_bar", measures=["forecast_cost_total"])
    s = (await advise(c, run))["suggestions"]
    assert all(x["pattern"] != "forecast_vs_budget" for x in s)


async def test_ordered_breakdown_and_many_bars() -> None:
    lc = (await advise(ctx("total_projects", "lifecycle_phase", visual="ranked_bar"), run))["suggestions"]
    assert lc[0]["visual"] == "columns" and lc[0]["kind"] == "visual"
    assert sum(1 for x in lc if x["ops"] == [{"op": "set_visual", "value": "columns"}]) == 1  # deduplicated
    dm = (await advise(ctx("total_projects", "delivery_manager", visual="ranked_bar"), run))["suggestions"]
    top = next(x for x in dm if x["kind"] == "top_n")
    assert top["ops"][0] == {"op": "set_limit", "value": 10} and top["reason"].startswith("20 bars")


async def test_time_range_offers_previous_period_and_status_mix_stacks() -> None:
    t = ctx("open_projects", visual="figure", time={"ref": "dimension:planned_deployment_date",
                                                    "range": {"relative": "this_quarter"}})
    assert any(x["kind"] == "comparison" for x in (await advise(t, run))["suggestions"])
    rag = (await advise(ctx("open_projects", "region", visual="ranked_bar"), run))["suggestions"]
    mix = next(x for x in rag if x["pattern"] == "rag_mix")
    assert mix["visual"] == "stacked_status"


async def test_looks_good_when_nothing_helps() -> None:
    out = await advise(ctx("total_projects", "region", visual="ranked_bar",
                           measures=["total_projects"]), run)
    assert [s["pattern"] for s in out["suggestions"]] == ["rag_mix"]
    plain = Context(**{**ctx("total_projects", visual="figure").__dict__, "patterns": []})
    assert (await advise(plain, run)) == {"suggestions": [], "message": LOOKS_GOOD, "source": "rules"}


def test_llm_output_is_held_to_typed_ops_and_given_shape() -> None:
    c = ctx("open_projects", "region", visual="ranked_bar")
    cands = candidates(c)
    out = AdviceOut(suggestions=[
        AdviceItem(candidate="c0", reason="See the RAG mix per region."),
        AdviceItem(ops=[{"op": "add_breakdown", "ref": "dimension:priority"}], title="By priority too",
                   reason="Priority shows which regions carry the critical work."),
    ])
    picked = from_llm(out, c, cands, [])
    assert [p.source for p in picked] == [cands[0].source, "llm"]
    assert picked[0].reason == "See the RAG mix per region."
    assert picked[1].ops == [{"op": "add_breakdown", "ref": "dimension:priority"}]
    # Unknown ops and invented figures are dropped; a cited chunk must be one that was supplied.
    bad = AdviceOut(suggestions=[
        AdviceItem(ops=[{"op": "drop_table"}], title="x", reason="y"),
        AdviceItem(ops=[{"op": "set_limit", "value": 3}], title="Top 3", reason="KSA has 42 projects."),
        AdviceItem(candidate="c0", reason="Already 42 at risk.", cites=["chunk:made-up"]),
    ])
    kept = from_llm(bad, c, cands, [])
    assert len(kept) == 1 and kept[0].reason == cands[0].reason and kept[0].cites == []


@pytest.mark.parametrize("visual", ["trend", "donut"])
async def test_llm_cannot_propose_an_unsuitable_chart(visual: str) -> None:
    from tela_api.ask.advisor import Candidate, check

    c = ctx("budget_variance", "region", visual="ranked_bar")
    c.result["rows"] = [[*r[:-1], -5] for r in c.result["rows"]]  # negative variance: no donut
    assert await check(c, Candidate("x", "y", [{"op": "set_visual", "value": visual}], visual, "llm"), run) is None


# --------------------------------------------------------------------------- US-187 visual catalogue wave 1


async def test_catalogue_patterns_suggest_suitable_visuals() -> None:
    bridge = (await advise(ctx("approved_budget_total", "portfolio", visual="ranked_bar",
                               measures=["approved_budget_total", "forecast_cost_total"]), run))["suggestions"]
    b = next(x for x in bridge if x["pattern"] == "budget_bridge")
    assert b["visual"] == "waterfall" and b["ops"][-1] == {"op": "set_visual", "value": "waterfall"}
    pareto = (await advise(ctx("overdue_go_lives", "region", visual="ranked_bar"), run))["suggestions"]
    assert next(x for x in pareto if x["pattern"] == "overdue_pareto")["visual"] == "pareto"
    funnel = (await advise(ctx("total_requests", visual="figure"), run))["suggestions"]
    f = next(x for x in funnel if x["pattern"] == "request_funnel")
    assert f["visual"] == "funnel" and [o.get("ref") for o in f["ops"][:2]] == [
        "measure:approved_for_delivery", "measure:converted_requests"]


async def test_bullet_needs_the_widgets_target() -> None:
    plain = (await advise(ctx("baselining_compliance_pct", "region", visual="ranked_bar"), run))["suggestions"]
    assert all(x["visual"] != "bullet" for x in plain)  # no target band: RULE-008 refuses the bullet
    spec_visual = {"type": "ranked_bar", "tone": {"warning": 90, "critical": 75, "higher_is_worse": False}}
    c = ctx("baselining_compliance_pct", "region", visual="ranked_bar")
    c.spec["visual"] = spec_visual
    s = (await advise(c, run))["suggestions"]
    assert next(x for x in s if x["pattern"] == "compliance_bullet")["visual"] == "bullet"
