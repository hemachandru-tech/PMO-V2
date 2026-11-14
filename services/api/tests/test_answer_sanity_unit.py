"""Answer sanity checks before answering (ENH-017, US-205; FEAT-067 / FEAT-064), without a database server.

Acceptance: a value the question lists that the plan's filter leaves out is a back-check mismatch even without a value
index (the "On Hold" of EXEC-115), and the readings offered include the filter with every listed value, on the measure
and on its table's own count; filters that leave the measure unchanged but narrow its table drop the plan's confidence
below the threshold, while filters that narrow nothing, agree with the measure's rule, sit on a field the rule reads or
the person's scope restricts say nothing. The original gold questions and the paraphrases plan and decide as before.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_paraphrase_set_unit import EXAMPLES, ORIGINALS
from test_planner_gold_unit import INDEX, KNOWN, ROWS, plan
from test_planner_gold_unit import RECORD_SNAP as SNAP

from tela_api.ask import confidence, sanity
from tela_api.ask.linking import rank_measures
from tela_api.ask.orchestrator import value_dims
from tela_api.ask.planner import Plan, norm
from tela_api.identity.scope import EntityScope, ResolvedCondition, ResolvedScope
from tela_api.knowledge.values import link
from tela_common.errors import ErrorCode, TelaError

EXEC115 = "How many open projects (current state Project Created, Active or On Hold) are there?"
STATES = {"ref": "dimension:current_state", "op": "in", "values": ["Active", "Project Created"]}
PORTFOLIO = {"ref": "dimension:portfolio", "op": "=", "values": ["Digital"]}
STATUS = {"ref": "dimension:status", "op": "=", "values": ["Active"]}
REGION = {"ref": "dimension:region", "op": "=", "values": ["KSA"]}


def scoped_values(question: str, p: Plan) -> dict[str, list[str]]:
    """The scoped values ``Orchestrator._values`` lists for the plan's measure (display labels)."""
    if p.status != "ok" or p.spec is None:
        return {}
    m = SNAP.measure(p.spec["measure"]["ref"].split(":", 1)[1])
    assert m is not None
    return {d.key: sorted({d.value_labels.get(str(r[d.field]), str(r[d.field])) for r in ROWS[d.entity]
                           if r.get(d.field) is not None}) for d in value_dims(question, SNAP, SNAP.dimensions, m)}


def checked(question: str, *, index: bool = True, values: bool = True) -> tuple[Plan, Plan]:
    """The rules plan with its checks as ``Orchestrator._rules`` runs them, then Ask's decision at "balanced"."""
    p = plan(question, snap=SNAP)
    linked = link(question, INDEX, known=KNOWN).links if index else []
    confidence.check_rules(p, question, SNAP, SNAP.measures, SNAP.dimensions, linked,
                           rank_measures(question, SNAP, SNAP.measures), scoped_values(question, p) if values else None)
    return p, confidence.decide(p, question, SNAP, SNAP.measures, None)


def spec(*filters: dict[str, Any], measure: str = "open_projects") -> dict[str, Any]:
    return {"model_version_id": "v", "connection_id": "c", "measure": {"ref": f"measure:{measure}"},
            "breakdowns": [], "filters": list(filters)}


# --------------------------------------------------------------------------- named values left out


def test_exec115_without_a_value_index_asks_with_every_listed_value() -> None:
    """The live turn had no value index: the scoped values alone name "On Hold" as left out."""
    before, _ = checked(EXEC115, index=False, values=False)
    assert before.confidence == 1.0  # the EXEC-115 turn: certified at 1.0
    p, decided = checked(EXEC115, index=False)
    assert p.confidence < confidence.threshold(None) and "the question names On Hold" in p.why
    assert decided.status == "clarify"
    labels = [o["label"] for o in decided.question["options"]]
    assert labels[0] == "Open projects, Current state: Active, Project Created"
    assert "Open projects, Current state: Active, Project Created, On Hold" in labels
    assert "Projects, Current state: Active, Project Created, On Hold" in labels  # 431 on TPG MySQL


def test_exec115_with_a_value_index_is_still_caught() -> None:
    p, decided = checked(EXEC115)
    assert decided.status == "clarify" and p.why.count("the question names On Hold") == 1
    assert "Projects, Current state: Active, Project Created, On Hold" in [o["label"] for o in
                                                                          decided.question["options"]]


def test_listed_values_skip_the_filter_values_measure_words_and_negations() -> None:
    q = norm("Open projects in current state Active or Project Created")
    vals = {"current_state": ["Active", "On Hold", "Project Created"]}
    # every named value filtered: nothing left out
    assert confidence.listed_values(q, spec(STATES), SNAP, vals, set()) == {}
    # Project Created left out — unless its words name the plan's measure
    one = {**STATES, "values": ["Active"]}
    assert confidence.listed_values(q, spec(one), SNAP, vals, set()) == {"dimension:current_state": ["Project Created"]}
    assert confidence.listed_values(q, spec(one), SNAP, vals, {"project", "created"}) == {}
    # a negated filter lists what is excluded, not what is asked; so does a value the question excludes
    assert confidence.listed_values(q, spec({**one, "op": "not_in"}), SNAP, vals, set()) == {}
    assert confidence.listed_values(norm(f"{q}, not on hold"), spec(STATES), SNAP, vals, set()) == {}
    assert confidence.listed_values(norm(f"{q} or on hold"), spec(STATES), SNAP, vals, set()) == {
        "dimension:current_state": ["On Hold"]}
    # a field the plan doesn't filter on is the value index's job (US-193), not this check's
    assert confidence.listed_values(q, spec(PORTFOLIO), SNAP, vals, set()) == {}


def test_a_listed_reading_keeps_the_stored_values_and_skips_the_count_for_a_sum() -> None:
    [full, count] = confidence.with_listed(spec(STATES), {"dimension:current_state": ["On Hold"]}, SNAP,
                                           SNAP.measures)
    assert full["filters"] == [{**STATES, "values": ["Active", "Project Created", "On Hold"]}]
    assert count["measure"] == {"ref": "measure:total_projects"} and count["filters"] == full["filters"]
    assert len(confidence.with_listed(spec(STATES, measure="approved_budget_total"),
                                      {"dimension:current_state": ["On Hold"]}, SNAP, SNAP.measures)) == 1
    assert confidence.with_listed(spec(STATES, measure="total_projects"), {"dimension:current_state": ["On Hold"]},
                                  SNAP, SNAP.measures)[1:] == []


@pytest.mark.parametrize("which", ["originals", "paraphrases"])
def test_the_scoped_values_change_no_gold_decision(which: str) -> None:
    """US-190 offline harness: with the scoped values the back-check now reads, every original and paraphrase plans,
    scores and decides as without them (W5: no loss on originals)."""
    questions = [e["question"] for e in (ORIGINALS if which == "originals" else EXAMPLES)]
    changed = []
    for q in questions:
        without, d1 = checked(q, values=False)
        with_, d2 = checked(q)
        if (without.confidence, d1.status, without.spec) != (with_.confidence, d2.status, with_.spec):
            changed.append(q)
    assert not changed


# --------------------------------------------------------------------------- filters with no effect


NOTE = {"kind": "filters_no_effect", "text": "Your filters (Current state: Active, Project Created) don't change the "
                                             "total: open projects is 279 with or without them.",
        "numbers": {"total": 279, "filtered": 279}}


def test_no_effect_drops_below_balanced_and_offers_the_table_count() -> None:
    p = Plan("ok", spec=spec(STATES), confidence=1.0, alternatives=["on_hold"])
    confidence.no_effect(p, NOTE, SNAP, SNAP.measures)
    assert confidence.threshold("assume_first") < p.confidence < confidence.threshold(None)
    assert p.signals == {"filters_no_effect": True} and NOTE["text"] in p.why
    assert p.alternatives == ["total_projects", "on_hold"]
    decided = confidence.decide(p, EXEC115, SNAP, SNAP.measures, None)
    assert decided.status == "clarify"
    assert "Projects, Current state: Active, Project Created" in [o["label"] for o in decided.question["options"]]
    assert confidence.decide(p, EXEC115, SNAP, SNAP.measures, "assume_first").status == "ok"


class Figures:
    """Answers a total probe by its measure and whether it is filtered; records the probes."""

    def __init__(self, figures: dict[tuple[str, bool], float], fail: bool = False) -> None:
        self.figures, self.fail, self.seen = figures, fail, []

    async def __call__(self, s: dict[str, Any]) -> dict[str, Any]:
        self.seen.append(s)
        if self.fail:
            raise TelaError(ErrorCode.QUERY_TIMEOUT, "slow")
        v = self.figures[(s["measure"]["ref"].split(":")[1], bool(s.get("filters")))]
        return {"columns": [{"name": "m", "kind": "measure", "label": "x", "format": "integer"}], "rows": [[v]],
                "row_count": 1, "truncated": False}


def figures(open_f: float, open_all: float, count_f: float, count_all: float) -> Figures:
    return Figures({("open_projects", True): open_f, ("open_projects", False): open_all,
                    ("total_projects", True): count_f, ("total_projects", False): count_all})


async def probe(run: Figures, *filters: dict[str, Any], reads: set[str] | None = None,
                scoped: set[str] | None = None, count: str | None = "measure:total_projects") -> dict[str, Any] | None:
    return await sanity.filters_no_effect(spec(*filters), run, SNAP, label="Open projects", kind="integer",
                                          agg="count", reads=reads or {"projects.status"}, scoped=scoped or set(),
                                          count=count)


async def test_exec115_figures_are_a_filter_with_no_effect() -> None:
    """Status read from current state: Open projects 279 with or without the filter; Projects with it 352 of 1,000."""
    run = figures(279, 279, 352, 1000)
    note = await probe(run, STATES)
    assert note is not None and note["kind"] == "filters_no_effect" and "Current state: Active, Project Created" in \
        note["text"] and note["numbers"] == {"total": 279, "filtered": 279}
    assert len(run.seen) == 4 and all(not s.get("breakdowns") for s in run.seen)


async def test_filters_that_narrow_agree_or_narrow_nothing_say_nothing() -> None:
    assert await probe(figures(120, 279, 352, 1000), STATES) is None  # the filter narrows the measure
    assert await probe(figures(65, 65, 100, 100), PORTFOLIO) is None  # every portfolio named: narrows nothing
    assert await probe(figures(65, 65, 65, 100), STATES) is None  # the rule and the stated criteria agree


async def test_rule_fields_scope_fields_no_count_and_failing_probes_say_nothing() -> None:
    no_effect = figures(279, 279, 352, 1000)
    assert await probe(no_effect, STATUS) is None  # "open projects that are active": the rule's own field
    assert await probe(no_effect, REGION, scoped={"projects.region"}) is None  # all they see is KSA
    assert await probe(no_effect, STATES, count=None) is None
    assert no_effect.seen == []
    assert await probe(figures(279, 279, 352, 1000), STATES, STATUS) is not None  # one telling filter is enough
    assert await probe(Figures({}, fail=True), STATES) is None


def test_scoped_fields_are_read_from_the_conditions_and_the_parent_link() -> None:
    from tela_api.identity.scope import ParentScope

    scope = ResolvedScope("c", {
        "projects": EntityScope(alternatives=[(ResolvedCondition("region", "=", ("KSA",)),),
                                              (ResolvedCondition("portfolio", "=", ("Digital",)),)]),
        "status_reports": EntityScope(via=ParentScope("project_id", "projects", "project_id")),
        "change_requests": EntityScope(all_rows=True)}, frozenset(), frozenset())
    assert sanity.scoped_fields(scope) == {"projects.region", "projects.portfolio", "status_reports.project_id"}
    assert sanity.telling(spec(REGION, PORTFOLIO, STATES), SNAP, {"projects.status"},
                          sanity.scoped_fields(scope)) == [STATES]
