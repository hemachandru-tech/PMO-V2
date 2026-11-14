"""ENH-022 Wave 3 (FEAT-072; no database): future and time words read honestly (US-219).

Tela counts what the data shows on the reporting date. A question in the future tense is answered with today's
figure and says so; a measure counting ahead of the reporting date that fits the question is offered, and a question
naming a short horizon ("go-lives next month") is planned on it.
"""

from __future__ import annotations

import pytest
from test_noise_set_unit import REAL_INDEX
from test_planner_gold_unit import SNAP, plan

from tela_api.ask import tense
from tela_api.ask.refine import apply_ops

EXEC139 = "How many number of project will be active in UAE region"
GO_LIVES = "measure:go_lives_0_30"


def note_of(question: str) -> dict | None:
    p = plan(question, index=REAL_INDEX)
    assert p.spec is not None, (question, p.status)
    return tense.note(question, p.spec, SNAP, SNAP.measures)


def test_forward_measures_are_found_by_their_rule() -> None:
    windows = {m.key: tense.ahead(m) for m in SNAP.measures if tense.ahead(m) is not None}
    assert windows["go_lives_0_30"] == (0, 30) and windows["go_lives_31_60"] == (30, 60)
    assert windows["go_lives_61_90"] == (60, 90)
    for key in ("open_projects", "overdue_go_lives", "throughput_go_lives_month", "stale_projects"):
        assert key not in windows, key  # today's or past records


def test_exec139_carries_the_note() -> None:
    p = plan(EXEC139, index=REAL_INDEX)
    assert p.spec is not None and p.spec["measure"]["ref"] == "measure:open_projects"
    assert note_of(EXEC139) == {"text": tense.NOTE, "fact": "future"}  # no go-live words: nothing to offer


@pytest.mark.parametrize("question", ["go-lives next month", "Which projects will go live next month",
                                      "upcoming go-lives", "Go-lives in the next 30 days"])
def test_a_horizon_plans_the_date_measure(question: str) -> None:
    p = plan(question, index=REAL_INDEX)
    assert p.spec is not None and p.spec["measure"]["ref"] == GO_LIVES, (question, p.spec)
    assert "time" not in p.spec  # "next month" is the measure's window, not a monthly grain
    assert note_of(question) is None  # the measure counts ahead: nothing to warn about


def test_the_horizon_is_said_in_interpreted() -> None:
    p = plan("go-lives next month", index=REAL_INDEX)
    assert "Read “next month” as Go-lives in the next 30 days" in p.interpreted
    assert not plan("Go-lives in the next 30 days", index=REAL_INDEX).interpreted


def test_a_future_question_on_another_measure_offers_the_date_measure() -> None:
    found = note_of("How many open projects will go live")
    assert found is not None and found["text"] == tense.NOTE
    assert found["action"] == {"label": "Show go-lives in the next 30 days",
                               "ops": [{"op": "set_measure", "ref": GO_LIVES}]}


@pytest.mark.parametrize("question", ["Open projects in UAE", "How many active projects in UAE region",
                                      "Forecast cost by region", "Overdue go-lives by programme"])
def test_present_tense_has_no_note(question: str) -> None:
    assert note_of(question) is None


def test_a_range_ahead_needs_no_note() -> None:
    spec = {"measure": {"ref": "measure:open_projects"}, "time": {"ref": "dimension:planned_deployment_date",
                                                                  "range": {"relative": "next_90_days"}}}
    assert tense.note("How many open projects will go live in the next 90 days", spec, SNAP, SNAP.measures) is None


def test_set_measure_swaps_the_measure_and_keeps_what_reaches_it() -> None:
    spec = {"measure": {"ref": "measure:open_projects"}, "extra_measures": [{"ref": "measure:on_hold"}],
            "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}],
            "breakdowns": [{"ref": "dimension:portfolio"}]}
    out, said = apply_ops(spec, [{"op": "set_measure", "ref": GO_LIVES}], SNAP)
    assert out["measure"] == {"ref": GO_LIVES} and "extra_measures" not in out
    assert out["filters"] == spec["filters"] and out["breakdowns"] == spec["breakdowns"]
    assert said == ["Measure: Go-lives in the next 30 days"]


def test_a_horizon_pick_is_not_a_near_tie() -> None:
    """Live, "go-lives next month" asked even at 1.0 rules confidence: linking ranks Go-lives per month within 0.1 of
    the next 30 days, and the near-tie cap (0.7) knew nothing of the horizon. It answers now, even at "ask freely"."""
    from test_planner_gold_unit import KNOWN

    from tela_api.ask import confidence
    from tela_api.knowledge.values import link

    q = "go-lives next month"
    p = plan(q, index=REAL_INDEX)
    assert p.signals == {"horizon": "next month"}
    # linking's ranking on the dev stack (EXEC-143 live check); offline embeddings don't rank by meaning
    live = [(0.935, "go_lives_0_30", "meaning"), (0.93, "throughput_go_lives_month", "named"),
            (0.62, "overdue_go_lives", "named"), (0.495, "go_lives_61_90", "meaning")]
    confidence.check_rules(p, q, SNAP, SNAP.measures, SNAP.dimensions, link(q, REAL_INDEX, known=KNOWN).links, live)
    assert p.signals["margin"] is None and p.confidence == 1.0, p.why
    assert confidence.decide(p, q, SNAP, SNAP.measures, "ask_freely").status == "ok"
