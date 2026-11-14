"""AG-06 review merge rules (ENH-026, US-244; DEC-28): rules ops stand, additions are marked, a different target asks,
unknown ids and ops are dropped; the AI's options become instructions the rules read exactly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tela_api.content.chat_edit import describe, parse
from tela_api.content.chat_review import CanvasEditOut, LayoutOpOut, as_out, ask, canonical, draft, merge


@dataclass
class Dim:
    key: str
    label: str
    is_temporal: bool = False
    synonyms: list[str] = field(default_factory=list)


DIMS = [Dim("region", "Region"), Dim("current_state", "Current state"), Dim("start_date", "Start date", True)]


def state() -> dict[str, Any]:
    widgets = {"w1": ("Open by region", "ranked_bar"), "w2": ("Open by state", "ranked_bar"),
               "w3": ("Open projects", "figure")}
    return {"sections": [{"id": "s1", "title": "Main"}, {"id": "s2", "title": "Detail"}],
            "items": [{"id": f"i{n}", "kind": "widget", "widget_id": f"w{n}", "section_id": "s1", "x": (n - 1) * 4,
                       "y": 0, "w": 4, "h": 4} for n in (1, 2, 3)],
            "widgets": {k: {"title": t, "visual": v, "spec": {}} for k, (t, v) in widgets.items()},
            "page_filters": []}


def reply(*ops: dict[str, Any], not_understood: list[str] | None = None) -> CanvasEditOut:
    return CanvasEditOut(ops=[LayoutOpOut(**o) for o in ops], not_understood=not_understood or [])


def rules(instruction: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    ops, refused = parse(instruction, state(), DIMS, seen)
    return ops, refused, seen


def test_parse_records_the_target_words_without_changing_its_ops() -> None:
    ops, refused, seen = rules("make open by region full width; move open projects next to open by state")
    assert ops == parse("make open by region full width; move open projects next to open by state", state(), DIMS)[0]
    assert seen == [{"phrase": "open by region", "next_to": None}, {"phrase": "open projects", "next_to": "i2"}]
    assert [as_out(o, state(), s) for o, s in zip(ops, seen, strict=True)] == [
        {"op": "resize", "widget": "i1", "width": 12, "height": 4}, {"op": "move", "widget": "i3", "next_to": "i2"}]
    assert draft(ops, refused, seen, state())["refused"] == []


def test_an_agreed_op_keeps_the_rules_version_and_a_missed_one_is_added() -> None:
    ops, refused, seen = rules("make open by region wider; spin the count")
    m = merge(ops, refused, seen, reply({"op": "resize", "widget": "i1", "width": 12},
                                        {"op": "move", "widget": "i3", "to": "top"}), state(), DIMS)
    assert m.question is None and [o["op"] for o in m.ops] == ["resize", "move"]
    assert m.ops[0]["to"] == {"w": 7, "h": 4}  # the rules' "wider", not the AI's width
    assert m.by_ai == {id(m.ops[1])} and m.refused == []  # the clause the rules missed is now read


def test_a_different_target_asks_with_both_widgets() -> None:
    ops, refused, seen = rules("remove open by region")
    m = merge(ops, refused, seen, reply({"op": "remove", "widget": "i2"}), state(), DIMS, "maybe the state one")
    assert m.question == {"question": "Which widget do you mean by “open by region”?",
                          "options": [{"item_id": "i1", "title": "Open by region"},
                                      {"item_id": "i2", "title": "Open by state"}], "why": ["maybe the state one"]}


def test_unknown_ids_ops_sections_and_dimensions_are_dropped_with_a_reason() -> None:
    m = merge([], [], [], reply({"op": "remove", "widget": "nope"}, {"op": "explode", "widget": "i1"},
                                {"op": "move", "widget": "i1", "to": "Appendix"},
                                {"op": "add_filter", "dimension": "colour"},
                                {"op": "set_limit", "widget": "i1", "limit": 0},
                                {"op": "add_filter", "dimension": "Region"}), state(), DIMS)
    assert [o["op"] for o in m.ops] == ["add_filter"] and m.ops[0]["ref"] == "dimension:region"
    reasons = [r["reason"] for r in m.refused]
    assert reasons[0].startswith("The AI named a widget that isn't on this report (“nope”)")
    assert reasons[1:] == ["“explode” isn't a layout change Tela can make.", "No section is called “Appendix”.",
                           "There is no “colour” to filter by.", "A top-N limit needs a number from 1 to 1000."]
    assert all(r["by"] == "ai" for r in m.refused)


def test_a_different_filter_leaves_the_rules_filter() -> None:
    ops, refused, seen = rules("add a region filter")
    m = merge(ops, refused, seen, reply({"op": "add_filter", "dimension": "current_state"}), state(), DIMS)
    assert [o["ref"] for o in m.ops] == ["dimension:region"] and m.notes


def test_not_understood_parts_are_listed_and_temporal_dimensions_are_not_filters() -> None:
    ops, refused, seen = rules("stretch the region one; add a start date filter")
    m = merge(ops, refused, seen, reply({"op": "resize", "widget": "i1", "width": 12},
                                        {"op": "add_filter", "dimension": "start_date"},
                                        not_understood=["add a start date filter"]), state(), DIMS)
    assert [o["op"] for o in m.ops] == ["resize"]
    assert [r["request"] for r in m.refused] == ["add filter start_date", "add a start date filter"]


def test_canonical_words_read_back_to_the_same_op() -> None:
    s = state()
    for o in ({"op": "remove", "widget": "i2"}, {"op": "set_limit", "widget": "i1", "limit": 5},
              {"op": "set_visual", "widget": "i1", "visual": "columns"}, {"op": "resize", "widget": "i2", "width": 6},
              {"op": "move", "widget": "i3", "to": "top"}, {"op": "move", "widget": "i3", "to": "detail"},
              {"op": "move", "widget": "i1", "next_to": "i3"}, {"op": "add_filter", "dimension": "region"}):
        words = canonical(LayoutOpOut(**o), s, DIMS)
        assert words is not None, o
        got, refused = parse(words, s, DIMS)
        assert not refused and len(got) == 1 and got[0]["op"] == o["op"], (words, got)
        assert got[0].get("item_id") == o.get("widget"), words
    assert canonical(LayoutOpOut(op="resize", widget="i1", width=9), s, DIMS) is None  # no words for 9 columns


def test_an_ask_becomes_a_widget_question_or_instructions() -> None:
    ops, _, seen = rules("remove open by region")
    q = ask(ops, seen, [reply({"op": "remove", "widget": "i1"}), reply({"op": "remove", "widget": "i3"})],
            state(), DIMS)
    assert q is not None and [o["item_id"] for o in q["options"]] == ["i1", "i3"]

    q = ask([], [], [reply({"op": "set_limit", "widget": "i1", "limit": 5}),
                     reply({"op": "set_limit", "widget": "i2", "limit": 5}),
                     reply({"op": "remove", "widget": "missing"})], state(), DIMS, "two bar charts")
    assert q is not None and q["why"] == ["two bar charts"]
    assert [(o["title"], o["instruction"]) for o in q["options"]] == [
        ("Show the top 5 in Open by region", "limit Open by region to top 5"),
        ("Show the top 5 in Open by state", "limit Open by state to top 5")]
    assert ask([], [], [reply({"op": "remove", "widget": "i1"}), reply({"op": "remove", "widget": "i1"})],
               state(), DIMS) is None  # one usable choice: the rules' reading stands


def test_describe_summarises_every_op() -> None:
    s = state()
    assert describe({"op": "set_limit", "item_id": "i1", "to": 5}, s)["summary"] == "Show the top 5 in Open by region"
    assert describe({"op": "set_visual", "item_id": "i1", "to": "ranked_bar"}, s)["summary"] == \
        "Show Open by region as ranked bar"
    assert describe({"op": "resize", "item_id": "i1", "to": {"w": 12, "h": 4}}, s)["summary"] == \
        "Resize Open by region to 12 x 4"
