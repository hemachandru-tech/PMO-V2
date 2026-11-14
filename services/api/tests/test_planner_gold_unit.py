"""Rules-planner accuracy on the PMO gold set and on paraphrases of it (US-057 follow-up; no database).

Plans each question the way ``Orchestrator.plan_for`` does — rules, then again with the scoped values of the
dimensions ``value_dims`` picks — against the PMO pack bound to the synthetic TPG fixture, and compares with
``spec_diff`` as the evaluation suite does. The live suite runs through the worker; this one guards the rules.
"""

from __future__ import annotations

from typing import Any

import pytest
import tpg_synthetic
from pmo_oracle import open_by_status

from tela_api.ask.linking import rank_measures
from tela_api.ask.orchestrator import value_dims
from tela_api.ask.planner import Plan, known_words, plan_question
from tela_api.knowledge.gold import pack_draft, spec_diff
from tela_api.knowledge.values import indexable, link, load_pack_synonyms, pack_synonym_map, rows_for
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, bind, load_manifest, match
from tela_query_spec.model import ModelSnapshot

# The activity tables (US-155) are bound too, so their measures compete with the projects' ones.
ROWS = {name: rows for name, _, rows in tpg_synthetic.tables()}
PROJECTS, REQUESTS = ROWS["projects"], ROWS["project_requests"]
COLUMNS = {name: cols for name, cols, _ in tpg_synthetic.tables()}


def _snapshot() -> ModelSnapshot:
    manifest, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    catalog = [{"qualified_name": f"public.{name}", "columns": [{"name": k, "data_type": "text"} for k in cols]}
               for name, cols in COLUMNS.items()]
    binding = bind(manifest, match(manifest, catalog), reporting_date=tpg_synthetic.AS_OF.isoformat())
    return ModelSnapshot.model_validate({"model_version_id": "v", "connection_id": "c", **binding.definition})


def record_rule(snap: ModelSnapshot) -> ModelSnapshot:
    """The model with open = Status Active, as pack 2.1.0 had RULE-026 (``pmo_oracle.open_by_status``)."""
    data = snap.model_dump()
    data["measures"] = open_by_status(data["measures"])
    return ModelSnapshot.model_validate(data)


SNAP = _snapshot()
RECORD_SNAP = record_rule(SNAP)
# The value index (US-173) as a refresh builds it from the extract, with every pack synonym approved.
INDEX = rows_for(SNAP, {d.key: sorted({str(r[d.field]) for r in ROWS[d.entity] if r.get(d.field) is not None})
                        for d in indexable(SNAP)},
                 pack_synonym_map(load_pack_synonyms("pmo_tpg"), SNAP))
KNOWN = known_words(SNAP)


def plan(question: str, memory: dict[str, str] | None = None, meaning: dict[str, float] | None = None,
         index: list[Any] | None = None, snap: ModelSnapshot | None = None) -> Plan:
    """``meaning``: the measures retrieval found (US-192); offline none, so linking ranks by words and qualifiers.
    ``index``: another value index than the fixture's (US-220 adds a source's free-text values). ``snap``: another
    model of the same fixture (``RECORD_SNAP``)."""
    model = snap or SNAP
    kw: dict[str, Any] = {"visible_measures": model.measures, "visible_dims": model.dimensions, "memory": memory,
                          "linked_measures": rank_measures(question, model, model.measures, meaning)}
    out = plan_question(question, model, values={}, **kw)
    linking = link(question, INDEX if index is None else index, known=KNOWN)
    kw |= {"linked": linking.links, "choices": linking.choices}
    if out.status == "ok" and out.spec is not None:
        measure = model.measure(out.spec["measure"]["ref"].split(":", 1)[1])
        assert measure is not None
        # As ``QueryService.filter_values`` lists them: display labels (Yellow is listed as Amber).
        values = {d.key: sorted({d.value_labels.get(str(r[d.field]), str(r[d.field])) for r in ROWS[d.entity]
                                 if r.get(d.field) is not None})
                  for d in value_dims(question, model, model.dimensions, measure)}
        out = plan_question(question, model, values=values, **kw)
    return out


def failures(cases: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, list[dict[str, Any]]]]:
    out = []
    for question, spec in cases:
        p = plan(question)
        if diff := spec_diff(spec, p.spec if p.status == "ok" else None):
            out.append((question, diff))
    return out


def test_rules_planner_passes_the_draft_gold_set() -> None:
    """Baseline was 18 / 30 (EXEC-022); look-alikes, synonyms, finance, top N and value labels now plan right."""
    cases = [(e["question"], e["spec"]) for e in pack_draft() if not e.get("turns") and not e.get("held_out")]
    assert len(cases) >= 30
    assert failures(cases) == []


M = "measure:"
PARAPHRASES: list[tuple[str, dict[str, Any]]] = [
    ("Number of projects in delivery by region",
     {"measure": {"ref": M + "in_delivery"}, "breakdowns": [{"ref": "dimension:region"}]}),
    ("How many overdue projects in KSA?", {"measure": {"ref": M + "overdue_go_lives"},
                                          "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}),
    ("Count of projects over budget by portfolio",
     {"measure": {"ref": M + "over_budget"}, "breakdowns": [{"ref": "dimension:portfolio"}]}),
    ("Total approved budget by region",
     {"measure": {"ref": M + "approved_budget_total"}, "breakdowns": [{"ref": "dimension:region"}]}),
    ("Forecast variance percentage by portfolio",
     {"measure": {"ref": M + "budget_variance_pct"}, "breakdowns": [{"ref": "dimension:portfolio"}]}),
    ("How many closed projects are there?", {"measure": {"ref": M + "inactive_projects"}}),
    ("Projects not yet started by priority",
     {"measure": {"ref": M + "project_created"}, "breakdowns": [{"ref": "dimension:priority"}]}),
    ("Go-lives in 61-90 days", {"measure": {"ref": M + "go_lives_61_90"}}),
    ("Stale projects by delivery manager",
     {"measure": {"ref": M + "stale_projects"}, "breakdowns": [{"ref": "dimension:delivery_manager"}]}),
    ("How many projects are reported Red?", {"measure": {"ref": M + "total_projects"}, "filters": [
        {"ref": "dimension:reported_status", "op": "=", "values": ["Red"]}]}),
    ("Open projects with Amber schedule KPI", {"measure": {"ref": M + "open_projects"}, "filters": [
        {"ref": "dimension:schedule_status", "op": "=", "values": ["Yellow"]}]}),
    ("Top 3 portfolios by open projects", {"measure": {"ref": M + "open_projects"}, "breakdowns": [
        {"ref": "dimension:portfolio"}], "sort": [{"by": "measure", "dir": "desc"}], "limit": 3}),
    ("Open demand requests by request priority",
     {"measure": {"ref": M + "open_demand"}, "breakdowns": [{"ref": "dimension:request_priority"}]}),
    ("Overdue SOW gates", {"measure": {"ref": M + "overdue_sow"}}),
    ("How many requests by request state?",
     {"measure": {"ref": M + "total_requests"}, "breakdowns": [{"ref": "dimension:request_state"}]}),
    ("Schedule risk by programme",
     {"measure": {"ref": M + "schedule_risk"}, "breakdowns": [{"ref": "dimension:programme"}]}),
    ("Open projects on Network One", {"measure": {"ref": M + "open_projects"}, "filters": [
        {"ref": "dimension:platform", "op": "=", "values": ["Network One"]}]}),
    ("Projects by lifecycle phase",
     {"measure": {"ref": M + "total_projects"}, "breakdowns": [{"ref": "dimension:lifecycle_phase"}]}),
    ("Projects in the Deployment phase", {"measure": {"ref": M + "total_projects"}, "filters": [
        {"ref": "dimension:lifecycle_phase", "op": "=", "values": ["Deployment"]}]}),
    ("Forecast cost for KSA", {"measure": {"ref": M + "forecast_cost_total"}, "filters": [
        {"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}),
    ("How many projects are in delivery in Jordan?", {"measure": {"ref": M + "in_delivery"}, "filters": [
        {"ref": "dimension:region", "op": "=", "values": ["Jordan"]}]}),
    ("Open projects by planned deployment quarter", {"measure": {"ref": M + "open_projects"}, "time": {
        "ref": "dimension:planned_deployment_date", "grain": "quarter"}}),
    ("Critical projects on hold", {"measure": {"ref": M + "on_hold"}, "filters": [
        {"ref": "dimension:priority", "op": "=", "values": ["Critical"]}]}),
]


def test_paraphrases_of_the_gold_set() -> None:
    """Written as a held-out check before the last tuning pass: old planner 17 / 24, new 23 / 24 (+ 'late' below)."""
    assert failures(PARAPHRASES) == []


@pytest.mark.xfail(reason="vocabulary gap: the pack has no 'late projects' synonym (for STK-008)", strict=True)
def test_late_is_overdue() -> None:
    assert failures([("Which projects are late?", {"measure": {"ref": M + "overdue_go_lives"}})]) == []


def test_generic_measure_still_answers_plain_questions() -> None:
    assert plan("How many projects are there?").spec == {"measure": {"ref": M + "total_projects"},
                                                           "breakdowns": [], "filters": []}
    p = plan("How many projects are reported Amber?")
    assert p.spec is not None and p.spec["filters"][0]["values"] == ["Yellow"]  # stored value, label in the chip
    assert any(c["label"] == "Reported status: Amber" for c in p.chips)


def test_measure_words_are_not_filters_or_ranges() -> None:
    assert "time" not in (plan("Go-lives in the next 30 days").spec or {})
    assert (plan("How many active projects do we have?").spec or {}).get("filters") == []
    assert (plan("Trend of projects by planned deployment month").spec or {}).get("filters") == []
    # A range the measure's name doesn't contain is still read.
    assert (plan("Open projects last quarter").spec or {}).get("time", {}).get("range") == {"relative": "last_quarter"}


def test_two_equally_good_measures_still_clarify() -> None:
    # Since US-156 the pack's total of overdue gates answers this; without it DOU, DARB and SOW score the same.
    assert (plan("How many overdue gates?").spec or {}).get("measure") == {"ref": M + "overdue_gates_total"}
    measures = [m for m in SNAP.measures if m.key != "overdue_gates_total"]
    p = plan_question("How many overdue gates?", SNAP, values={}, visible_measures=measures,
                      visible_dims=SNAP.dimensions)
    assert p.status == "clarify" and p.question is not None
    assert {o["measure"] for o in p.question["options"]} >= {M + "overdue_dou", M + "overdue_darb", M + "overdue_sow"}
