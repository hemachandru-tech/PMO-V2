"""Multi-connection routing for Ask (US-116; AG-01; REQ-033, REQ-034): named source, conversation continuity,
best measure match, otherwise ask which source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from test_planner_gold_unit import SNAP

from tela_api.ask.routing import mentions, route
from tela_query_spec.model import ModelSnapshot


@dataclass
class Src:
    connection: dict[str, Any]
    snap: ModelSnapshot

    @property
    def measures(self) -> list[Any]:
        return self.snap.measures


def sources(*names_engines: tuple[str, str], snap: ModelSnapshot = SNAP) -> list[Src]:
    return [Src({"id": f"c{i}", "name": n, "engine": e}, snap) for i, (n, e) in enumerate(names_engines)]


PMO = sources(("Sample TPG (dev)", "postgres"), ("Sample TPG on MySQL (dev)", "mysql"),
              ("Finance workbook", "excel"), ("Sample TPG on Dataverse fake, Web API (dev)", "dataverse"))


def test_one_source_needs_no_routing() -> None:
    r = route("How many open projects?", PMO[:1], None)
    assert r.chosen is PMO[0] and r.reason == "only"


def test_identical_models_are_ambiguous_and_every_candidate_is_offered() -> None:
    r = route("How many open projects are there?", PMO, None)
    assert r.chosen is None and r.reason == "ambiguous" and r.candidates == PMO


def test_a_named_source_wins() -> None:
    assert route("How many open projects on MySQL?", PMO, None).chosen is PMO[1]
    assert route("Open projects in the finance workbook", PMO, None).chosen is PMO[2]
    assert route("Overdue go-lives from Dataverse by region", PMO, None).chosen is PMO[3]
    assert route("open projects in the spreadsheet", PMO, None).reason == "named"


def test_follow_ups_stay_on_the_conversation_source() -> None:
    r = route("And by region?  open projects by region", PMO, "c2")
    assert r.chosen is PMO[2] and r.reason == "conversation"


def test_generic_words_in_names_do_not_count_as_a_mention() -> None:
    assert not mentions("Show the sample of projects data", {"name": "Sample TPG (dev)", "engine": "postgres"})
    assert mentions("projects in postgres", {"name": "Sample TPG (dev)", "engine": "postgres"})


def test_only_sources_whose_measures_match_are_candidates() -> None:
    empty = ModelSnapshot.model_validate({**SNAP.model_dump(by_alias=True), "measures": [], "dimensions": []})
    mixed = [*PMO[:1], Src({"id": "x", "name": "HR system", "engine": "postgres"}, empty)]
    r = route("How many open projects are there?", mixed, None)
    assert r.chosen is PMO[0] and r.reason == "best_match"


def test_route_review_helpers() -> None:
    """US-242: AG-01's reading is refused for an unknown source or a term the question doesn't hold; the intent
    question offers the figures first and the chosen intent is read back on resume."""
    from tela_api.ask.routing import RouteOut, intent_choice, intent_question, route_errors

    q = "How many overdue go-lives are there?"
    assert route_errors(RouteOut(intent="definition", subject="overdue go-lives"), q, {"a"}) == []
    assert route_errors(RouteOut(intent="data", connection_id="b"), q, {"a"}) == [
        "a source that is not one of the candidates"]
    assert route_errors(RouteOut(intent="definition", subject="budget"), q, {"a"}) == [
        "a term that is not in the question"]
    clar = intent_question(["data", "definition", "build"], None, "")
    assert [o["label"] for o in clar["options"]] == ["Show the figures", "Explain how it is defined", "Plan a report"]
    assert intent_choice(clar) is None
    assert intent_choice({**clar, "subject": "x", "answer": "b"}) == ("definition", "x")
    assert intent_choice({"kind": "reading", "answer": "a", "options": [{"id": "a", "value": "reading:0"}]}) is None
