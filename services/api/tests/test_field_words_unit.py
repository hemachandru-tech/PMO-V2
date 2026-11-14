"""Personal fields in words (US-075 chat entry, US-076 edit in words): rules only, no database."""

from __future__ import annotations

import pytest
from test_planner_gold_unit import SNAP

from tela_api.ask.planner import route_intent
from tela_api.content.field_words import band_diff, draft_from_words, edit_bands_in_words, is_field_request, numbers
from tela_api.content.fields import FieldIn, build
from tela_common.errors import TelaError


def test_numbers_read_units_and_ranges() -> None:
    assert numbers("1-5M") == [1_000_000, 5_000_000]
    assert numbers("$2.5m to 10m") == [2_500_000, 10_000_000]
    assert numbers("1,000,000 and 500k") == [1_000_000, 500_000]
    assert numbers("5 million") == [5_000_000]
    assert numbers("999-1500") == [999, 1500]


def test_grouping_from_chat_with_and_without_values() -> None:
    d = draft_from_words("group platforms into Core (Network One, Magnati) and Growth", SNAP)
    assert d.kind == "grouping" and d.source_ref == "dimension:platform"
    assert d.groups == [{"label": "Core", "values": ["Network One", "Magnati"]}, {"label": "Growth", "values": []}]
    assert d.missing == ["Pick the values for Growth."]
    assert "Everything else" in d.understood[0]
    bare = draft_from_words("Group platforms into Core and Growth", SNAP)
    assert [g["label"] for g in bare.groups] == ["Core", "Growth"]


def test_bands_from_chat_follow_the_edge_rule() -> None:
    d = draft_from_words("band forecast cost into <1M, 1-5M, >5M", SNAP)
    assert d.kind == "band" and d.source_ref == "field:projects.cost_total_usd"
    assert d.edges == [0, 1_000_000, 5_000_000] and d.open_ended_top
    assert d.labels == ["< 1M", "1M - 5M", "5M+"]
    assert "Exactly 1M goes into 1M - 5M." in d.understood
    # The draft is a valid builder request; exactly 1M lands in 1M - 5M (TEST-083).
    body = FieldIn(kind="band", name=d.name, connection_id="c", source_ref=d.source_ref, edges=d.edges,
                   labels=d.labels, open_ended_top=d.open_ended_top)
    _, rule = build(body, SNAP, frozenset(), "k")
    assert "exactly 1M goes into 1M - 5M" in rule
    at = draft_from_words("band approved budget at 1M and 5M", SNAP)
    assert at.edges == [0, 1_000_000, 5_000_000] and at.labels[0] == "< 1M"
    closed = draft_from_words("group percent complete into 0-50, 50-100", SNAP)
    assert closed.kind == "band" and closed.edges == [0, 50, 100] and not closed.open_ended_top


@pytest.mark.parametrize(("text", "message"), [
    ("group widgets into A and B", "couldn't find a field"),
    ("band platform into <1M, >1M", "number field"),
    ("group platforms into Core and core", "different"),
    ("what is this", "draft a grouping or bands"),
])
def test_drafts_explain_what_went_wrong(text: str, message: str) -> None:
    with pytest.raises(TelaError, match=message):
        draft_from_words(text, SNAP)


def test_hidden_fields_are_not_offered() -> None:
    with pytest.raises(TelaError):
        draft_from_words("band forecast cost into <1M, >1M", SNAP, frozenset({"projects.cost_total_usd",
                                                                             "project_requests.cost_total_usd"}))


def test_ask_routes_field_requests_but_not_questions() -> None:
    assert is_field_request("group platforms into Core and Growth")
    assert route_intent("Band forecast cost into <1M, 1-5M, >5M").kind == "field"
    assert route_intent("projects by platform").kind == "data"
    assert route_intent("how many projects are grouped by region").kind == "data"


BANDS = {"source": "field:projects.cost_total_usd", "closing_max": None, "bands": [
    {"label": "< 1M", "lower": 0, "upper": 1_000_000}, {"label": "1M - 5M", "lower": 1_000_000, "upper": 5_000_000},
    {"label": "5M+", "lower": 5_000_000, "upper": None}]}


def test_make_the_top_band_and_diff() -> None:
    e = edit_bands_in_words("make the top band 2M and above", BANDS)
    assert e.edges == [0, 1_000_000, 2_000_000] and e.open_top
    assert e.labels == ["< 1M", "1M - 2M", "2M+"]
    assert "Edges above it (5M) are removed" in e.changes[0]
    diff = band_diff(BANDS, e.edges, e.labels, e.open_top)
    assert [r["status"] for r in diff["after"]] == ["same", "new", "new"]
    assert [r["status"] for r in diff["before"]] == ["same", "removed", "removed"]


def test_more_edits_in_words() -> None:
    assert edit_bands_in_words("add an edge at 2M", BANDS).edges == [0, 1e6, 2e6, 5e6]
    assert edit_bands_in_words("remove the edge at 1M", BANDS).labels == ["< 5M", "5M+"]
    capped = edit_bands_in_words("cap at 10M", BANDS)
    assert capped.edges == [0, 1e6, 5e6, 10e6] and not capped.open_top and capped.labels[-1] == "5M - 10M"
    renamed = edit_bands_in_words("rename 1M - 5M to Medium; split at 3M", BANDS)
    assert renamed.labels == ["< 1M", "1M - 3M", "3M - 5M", "5M+"]  # a split band gets new names
    assert edit_bands_in_words("rename 5M+ to Large", BANDS).labels[-1] == "Large"
    assert edit_bands_in_words("start at -1M", BANDS).edges[0] == -1e6


def test_conflicting_edit_names_the_edges() -> None:
    moved = edit_bands_in_words("move 1M to 6M", BANDS)
    body = FieldIn(kind="band", name="x", connection_id="c", source_ref=BANDS["source"], edges=moved.edges,
                   labels=moved.labels, open_ended_top=moved.open_top)
    with pytest.raises(TelaError, match="5M must be above 6M"):
        build(body, SNAP, frozenset(), "k")
    for text, message in [("add an edge at 1M", "already an edge"), ("remove the edge at 2M", "no edge at 2M"),
                          ("rename Huge to Big", "no band called"), ("make it nicer", "didn't understand")]:
        with pytest.raises(TelaError, match=message):
            edit_bands_in_words(text, BANDS)
