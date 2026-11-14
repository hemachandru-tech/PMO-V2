"""Schema linking and spec repair (US-181; FEAT-064), without a database.

Linking: every question of the draft gold set and its paraphrases gets the expected measure, extra measures,
breakdowns, filter and time dimensions and filter values among 5-10 candidates, with the rules planner's reading left
out (the LLM path runs when the rules found nothing). Repair: the typed errors AG-03 gets back for each refusal.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_planner_gold_unit import INDEX, KNOWN, PARAPHRASES, SNAP

from tela_api.ask import repair
from tela_api.ask.linking import (
    MAX_DIMENSIONS,
    MAX_MEASURES,
    MIN_CANDIDATES,
    LinkOut,
    candidates_block,
    catalogue_block,
    from_model,
    link_schema,
)
from tela_api.ask.planner import PlannerOut
from tela_api.knowledge.gold import pack_draft
from tela_api.knowledge.values import link
from tela_api.query.validator import SpecValidator
from tela_common.errors import TelaError
from tela_query_spec.spec import parse_spec

# The held-out `paraphrase` set (US-190) is measured by test_paraphrase_set_unit, never a guard to tune linking on.
# The noise set (US-220) is scored in test_noise_set_unit.py, not held to the linking bar here.
# The EXEC-186 lists and their count twins (US-250) are a held-out baseline: a list needs its entity's record field,
# which schema linking does not offer yet (the question frame, US-253/254, will).
CASES = [(e["question"], e["spec"]) for e in pack_draft()
         if not {"paraphrase", "noise", "exec186"} & set(e["tags"])] + PARAPHRASES


def links_for(question: str) -> Any:
    linking = link(question, INDEX, known=KNOWN)
    return link_schema(question, SNAP, SNAP.measures, SNAP.dimensions, linked=linking.links, choices=linking.choices)


def wanted(spec: dict[str, Any]) -> tuple[set[str], set[str]]:
    refs = {spec["measure"]["ref"], *(e["ref"] for e in spec.get("extra_measures", [])),
            *(b["ref"] for b in spec.get("breakdowns", [])), *(f["ref"] for f in spec.get("filters", []))}
    if spec.get("time"):
        refs.add(spec["time"]["ref"])
    values = {f"{f['ref']}={v}" for f in spec.get("filters", []) for v in f["values"]}
    return refs, values


def test_every_gold_question_finds_what_it_needs_among_few_candidates() -> None:
    assert len(CASES) >= 130
    lost = []
    for question, spec in CASES:
        found = links_for(question)
        refs, values = wanted(spec)
        assert MIN_CANDIDATES <= len(found.refs) <= MAX_MEASURES + MAX_DIMENSIONS, question
        missing = (refs - set(found.refs)) | (values - {c.ref for c in found.values})
        if missing:
            lost.append((question, sorted(missing)))
    assert lost == []


def test_look_alikes_are_ranked_with_the_right_measure_first() -> None:
    """The first baseline's 33 % look_alike tag: the expected measure leads, the look-alike stays a candidate."""
    looks = [(q, s) for q, s in CASES if "look_alike" in next((e["tags"] for e in pack_draft()
                                                                if e["question"] == q), [])]
    assert len(looks) >= 10
    first = [q for q, s in looks if links_for(q).measures[0].ref != s["measure"]["ref"]]
    assert first == []


def test_named_values_and_time_words_are_candidates() -> None:
    found = links_for("Open projects in Saudi by month last quarter")
    assert "dimension:region=KSA" in {c.ref for c in found.values}
    assert {"range:last_quarter", "grain:month"} <= {c.ref for c in found.time}
    assert "dimension:planned_deployment_date" in found.refs  # a time word brings the measure's date field
    assert found.dimensions[0].why in ("linked value", "date field", "named")
    assert not found.miss and found.by == "rules"


def test_a_miss_keeps_the_closest_by_word_forms_and_the_model_may_pick() -> None:
    found = links_for("zorbly glimps of the quantum widgets")
    assert found.miss and len(found.measures) >= 3 and len(found.refs) >= MIN_CANDIDATES
    picked = from_model(LinkOut(measures=["measure:on_hold", "measure:not_a_measure"],
                                dimensions=["dimension:hold_reason", "dimension:nope"]),
                        found, SNAP, SNAP.measures, SNAP.dimensions)
    assert picked.by == "model" and picked.measures[0].ref == "measure:on_hold"
    assert "measure:not_a_measure" not in picked.refs and "dimension:nope" not in picked.refs
    assert picked.dimensions[0].ref == "dimension:hold_reason"
    assert len(picked.measures) <= MAX_MEASURES and len(picked.dimensions) <= MAX_DIMENSIONS
    # A pick of measures the person can't see narrows nothing.
    hidden = from_model(LinkOut(measures=["measure:on_hold"]), found, SNAP,
                        [m for m in SNAP.measures if m.key != "on_hold"], SNAP.dimensions)
    assert hidden is found


def test_ag03_sees_the_candidates_not_the_catalogue() -> None:
    found = links_for("Forecast cost by region")
    block = candidates_block(found, SNAP)
    assert "measure:forecast_cost_total" in block and "dimension:region" in block and "(score " in block
    assert "measure:hours_logged" not in block and block.count("- measure:") <= MAX_MEASURES
    forecast = SNAP.measure("forecast_cost_total")
    assert forecast is not None and forecast.plain_rule in block
    assert "measure:hours_logged — Hours logged" in catalogue_block(SNAP.measures, SNAP.dimensions)
    assert found.public()["measures"][0] == {"kind": "measure", "ref": "measure:forecast_cost_total",
                                             "label": "Forecast cost (EAC, USD)", "score": pytest.approx(1.25, 0.1),
                                             "why": "named"}


def test_rules_reading_comes_first_among_equals() -> None:
    order = [c.ref for c in link_schema("overdue gates", SNAP, SNAP.measures, SNAP.dimensions).measures]
    assert order[2:] == ["measure:overdue_dou", "measure:overdue_darb", "measure:overdue_sow"]  # tied, model order
    ruled = link_schema("overdue gates", SNAP, SNAP.measures, SNAP.dimensions, considered=["overdue_sow"])
    assert [c.ref for c in ruled.measures][:3] == ["measure:overdue_gates_total", "measure:overdue_go_lives",
                                                  "measure:overdue_sow"]


# --------------------------------------------------------------------------- repair: typed errors


def test_precheck_names_unknown_and_unseen_references() -> None:
    visible = [m for m in SNAP.measures if m.key != "forecast_cost_total"]
    out = PlannerOut(measure="measure:open_projectz", breakdowns=["dimension:regions"],
                     filters=[{"ref": "dimension:region", "values": ["KSA"]}])
    errors = repair.precheck(out, SNAP, visible, SNAP.dimensions)
    assert [(e.kind, e.ref) for e in errors] == [("unknown_measure", "measure:open_projectz"),
                                                 ("unknown_dimension", "dimension:regions")]
    assert errors[0].typed.startswith("measure:open_projectz is not a certified measure you can use; closest: "
                                      "measure:open_projects")
    assert "closest: dimension:region" in errors[1].typed
    # A measure the person can't see is unknown to them, and no closest is offered from what they can't see.
    hidden = repair.precheck(PlannerOut(measure="forecast_cost_total"), SNAP, visible, SNAP.dimensions)
    assert hidden[0].kind == "unknown_measure" and "forecast_cost_total, " not in hidden[0].typed
    # Extras of another table, a derived figure of a measure not asked for, no measure at all.
    extras = repair.precheck(PlannerOut(measure="open_projects", extra_measures=["total_requests"],
                                        derived=[{"op": "ratio", "of": "measure:open_projects",
                                                  "to": "measure:on_hold"}]), SNAP, SNAP.measures, SNAP.dimensions)
    assert [e.kind for e in extras] == ["invalid_extra", "invalid_extra"]
    assert "measure:total_requests counts project_requests, not projects" in extras[0].typed
    assert "derived ratio uses measure:on_hold" in extras[1].typed
    assert repair.precheck(PlannerOut(), SNAP, SNAP.measures, SNAP.dimensions)[0].kind == "unknown_measure"
    assert repair.precheck(PlannerOut(unsupported=True), SNAP, SNAP.measures, SNAP.dimensions) == []
    assert repair.precheck(PlannerOut(measure="open_projects", breakdowns=["region"]), SNAP, SNAP.measures,
                           SNAP.dimensions) == []


def _refusal(snap: Any, spec: dict[str, Any], **kw: Any) -> TelaError:
    parsed, _ = parse_spec({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id, **spec})
    with pytest.raises(TelaError) as exc:
        SpecValidator(snap, **kw).validate(parsed)
    return exc.value


def test_validator_refusals_become_typed_errors() -> None:
    apart = SNAP.model_copy(update={"links": []})  # no approved links: a request field can't reach projects
    spec = {"measure": {"ref": "measure:forecast_cost_total"}, "breakdowns": [{"ref": "dimension:request_region"}]}
    err = repair.from_refusal(_refusal(apart, spec), spec, apart, apart.dimensions)
    assert err.kind == "not_reachable" and err.ref == "dimension:request_region"
    assert err.typed.startswith("dimension:request_region is not reachable from measure:forecast_cost_total; "
                                "reachable: dimension:region")
    assert err.logged == "dimension:request_region is not reachable from measure:forecast_cost_total"

    spec = {"measure": {"ref": "measure:open_projects"},
            "filters": [{"ref": "dimension:region", "op": "=", "values": ["Saudi Arabia"]}]}
    exc = _refusal(SNAP, spec, enum_values=lambda f: ["KSA", "Jordan"] if f.qualified == "projects.region" else None)
    err = repair.from_refusal(exc, spec, SNAP, SNAP.dimensions, {"Saudi Arabia": "KSA"})
    assert err.kind == "value_not_found"
    assert err.typed == "value 'Saudi Arabia' not found in dimension:region; closest: 'KSA'"
    assert err.logged == "value not found in dimension:region"  # filter values stay out of the log
    assert err.message == "No Region called Saudi Arabia in your data."

    spec = {"measure": {"ref": "measure:open_projects"}, "share_of_total": ["measure:open_projects"]}
    err = repair.from_refusal(_refusal(SNAP, spec), spec, SNAP, SNAP.dimensions)
    assert err.kind == "invalid" and err.typed == ("measure:open_projects: A share of the total needs a breakdown to "
                                                   "share out.")
    compiled = repair.from_refusal(TelaError("spec_invalid", "Unavailable on this source."), spec, SNAP,
                                   SNAP.dimensions, compiling=True)
    assert compiled.kind == "compile" and "can't be run on this source" in compiled.typed


def test_closest_value_and_the_repair_request() -> None:
    assert repair.closest_value("ksaa", ["KSA", "Jordan", "GCC - UAE"]) == "KSA"
    assert repair.closest_value("Mars", ["KSA", "Jordan"]) is None
    assert repair.closest_refs("measure:open_projectz", {"measure:open_projects": "Open projects",
                                                         "measure:on_hold": "Projects on hold"})[0] == \
        "measure:open_projects"
    out = PlannerOut(measure="measure:open_projects", breakdowns=["dimension:regions"])
    errs = repair.precheck(out, SNAP, SNAP.measures, SNAP.dimensions)
    block = repair.repair_block(out, errs)
    assert block.startswith("Your previous plan was refused.")
    assert '"breakdowns": ["dimension:regions"]' in block and '"unsupported"' not in block
    assert "- dimension:regions is not a dimension you can use; closest: dimension:region" in block
    assert repair.MAX_REPAIRS == 2 and set(repair.KINDS) >= {e.kind for e in errs}
