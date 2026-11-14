"""Semantic measure linking (ENH-016, US-192; FEAT-066 / FEAT-062; no database).

The EXEC-102 cases where retrieval had the right measure but the rules planner and schema linking never read it:
sanctioned budget, live projects, pending requests, budget-code percentage, conversion share. ``hits`` stands in for
typed retrieval's ``certified_measure`` hits, in the order it returned them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from test_planner_gold_unit import SNAP, plan

from tela_api.ask import confidence, meaning
from tela_api.ask.linking import MAX_LINKED, link_schema, rank_measures
from tela_api.ask.planner import MEANING_CONFIDENCE, RETRIEVED_CONFIDENCE


def hits(*keys: str) -> list[Any]:
    """Retrieval hits: the measures' chunks in rank order, with an entity chunk among them as retrieval mixes them."""
    out: list[Any] = [SimpleNamespace(kind="certified_measure", metadata={"measure": k}) for k in keys]
    return [SimpleNamespace(kind="entity", metadata={"entity": "projects"}), *out]


def measure_of(question: str, *found: str) -> tuple[str | None, Any]:
    p = plan(question, meaning=meaning.from_hits(hits(*found)))
    return (p.spec["measure"]["ref"].split(":", 1)[1] if p.status == "ok" and p.spec else None), p


def test_retrieved_measures_score_by_reciprocal_rank() -> None:
    m = meaning.from_hits(hits("approved_budget_total", "request_budget_total", "approved_budget_total", "hidden"),
                          visible={"approved_budget_total", "request_budget_total"})
    assert list(m) == ["approved_budget_total", "request_budget_total"]  # first rank kept, hidden dropped
    assert m["approved_budget_total"] == pytest.approx(meaning.RR_TOP)
    assert m["request_budget_total"] == pytest.approx(meaning.RR_TOP * 3 / 4)
    assert meaning.from_hits([]) == {}


def test_qualifiers() -> None:
    assert meaning.qualifiers("Live projects split by urgency") == {"open": {"live"}}
    assert meaning.qualifiers("How many projects are still running?") == {"open": {"still running"}}
    assert "open" not in meaning.qualifiers("Go-lives in the next 30 days")  # "go-live" is not "live"
    assert meaning.qualifiers("Total sanctioned budget for live projects")["money"] == {"budget"}
    assert meaning.qualifiers("What percentage of projects have a budget code?")["percent"] == {"percentage"}
    assert "percent" not in meaning.qualifiers("Each region's share of the total open projects")
    by = {m.key: m for m in SNAP.measures}
    live = meaning.qualifiers("live projects")
    assert meaning.qualifier_shift(live, by["open_projects"]) > 0 > meaning.qualifier_shift(live, by["total_projects"])
    assert meaning.qualifier_shift(meaning.qualifiers("open projects"), by["open_projects"]) == 0  # it names it
    money = meaning.qualifiers("budget by region")
    assert meaning.qualifier_shift(money, by["approved_budget_total"]) > 0
    assert meaning.qualifier_shift(money, by["over_budget"]) == 0  # "budget" names it: not a money measure, not against
    assert meaning.qualifier_shift(money, by["open_projects"]) < 0
    # Only an open qualifier points at a measure; money and percent words prefer a kind.
    assert meaning.asks_open(live, by["open_projects"]) and not meaning.asks_open(live, by["total_projects"])
    assert not meaning.asks_open(money, by["approved_budget_total"])


@pytest.mark.parametrize(("question", "found", "want", "sure"), [
    # Retrieval had the right measure; the words named another one, or none. Retrieval alone is a reading to check
    # (below "balanced": Ask asks, offering it first); with the words naming half of it, or a qualifier, it answers.
    ("Total sanctioned budget for live projects", ["approved_budget_total", "request_budget_total"],
     "approved_budget_total", MEANING_CONFIDENCE),
    ("How much money has been sanctioned for the open projects?", ["approved_budget_total", "open_projects"],
     "approved_budget_total", RETRIEVED_CONFIDENCE),
    ("Pending requests by stage", ["open_demand", "total_requests"], "open_demand", MEANING_CONFIDENCE),
    ("What percentage of projects have a budget code?", ["budget_code_compliance_pct", "budget_code_set"],
     "budget_code_compliance_pct", MEANING_CONFIDENCE),
    ("What share of requests turn into projects, by quarter raised?", ["conversion_rate", "converted_requests"],
     "conversion_rate", RETRIEVED_CONFIDENCE),
    # An open qualifier alone, as offline (no retrieval): "live", "still going" are an open measure.
    ("Live projects split by urgency", [], "open_projects", MEANING_CONFIDENCE),
    ("Count the projects that are still going", [], "open_projects", MEANING_CONFIDENCE),
])
def test_exec102_cases_are_planned_by_rules(question: str, found: list[str], want: str, sure: float) -> None:
    got, p = measure_of(question, *found)
    assert got == want, (question, p.status, p.measures_considered)
    # Matched by meaning, not named: said so, less sure, and the word reading kept as an alternative.
    assert f"Matched by meaning: {SNAP.measure(want).label}" in p.interpreted  # type: ignore[union-attr]
    assert p.confidence == sure and p.alternatives and want not in p.alternatives
    assert (p.confidence >= confidence.threshold("balanced")) is (sure == MEANING_CONFIDENCE)
    links = link_schema(question, SNAP, SNAP.measures, SNAP.dimensions, meaning=meaning.from_hits(hits(*found)))
    assert links.measures[0].ref == f"measure:{want}" and links.measures[0].why in ("meaning", "qualifier")


@pytest.mark.parametrize(("question", "found", "want"), [
    # The total named outright stays: no qualifier, every word known.
    ("Projects by reported status", ["reported_red_pct", "reported_green_pct"], "total_projects"),
    # "How many" is a count, whatever retrieval found.
    ("How many projects are reported Amber?", ["reported_amber_pct"], "total_projects"),
    # A measure named outright is not outscored by a weaker retrieved one.
    ("Open projects by region", ["tcv_open_total", "staged_open_projects"], "open_projects"),
    ("Requests by stage", ["open_demand"], "total_requests"),
    # Word forms alone ("deploy" ~ "Deployed projects") are not meaning: nothing to plan from.
    ("How many projects are planned to deploy each month?", [], None),
    # Retrieval found it, but money was asked and it isn't money.
    ("Expected spend per portfolio", ["status_reporting_scope", "open_projects"], None),
])
def test_named_measures_and_word_forms_are_not_overridden(question: str, found: list[str], want: str | None) -> None:
    got, p = measure_of(question, *found)
    assert got == want, (question, p.status, p.interpreted)
    assert not any(i.startswith("Matched by meaning") for i in p.interpreted)


def test_a_tie_by_meaning_is_not_picked() -> None:
    """"Cost" alone: every money measure gains alike, so Tela asks which (RULE-023), as before."""
    got, p = measure_of("cost by region")
    assert got is None and p.status == "clarify"
    assert all(why != "money" for _, _, why in rank_measures("cost by region", SNAP, SNAP.measures))


def test_retrieved_measures_are_candidates() -> None:
    """Every measure retrieval found is a candidate AG-03 sees, after the named ones, with its meaning score."""
    q = "How many deployments are due within the next month?"
    found = ["project_starts_month", "throughput_go_lives_month", "overdue_risks", "overdue_issues", "go_lives_0_30"]
    links = link_schema(q, SNAP, SNAP.measures, SNAP.dimensions, meaning=meaning.from_hits(hits(*found)))
    refs = [c.ref for c in links.measures]
    assert "measure:go_lives_0_30" in refs and len(refs) <= MAX_LINKED
    cand = next(c for c in links.measures if c.ref == "measure:go_lives_0_30")
    assert cand.meaning > 0 and cand.public()["meaning"] == round(cand.meaning, 3)
    # Without retrieval the candidates are as before (US-181).
    plain = link_schema(q, SNAP, SNAP.measures, SNAP.dimensions)
    assert all("meaning" not in c.public() for c in plain.measures)
