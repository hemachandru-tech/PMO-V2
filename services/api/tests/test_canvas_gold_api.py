"""Chat edit gold set on the TPG fixture (US-244, for the US-246 final gate).

The 20 instructions in ``packs/pmo/fixtures/canvas_edit_gold.yaml`` are previewed on the gold canvas: with review off
the rules get the ones in their own phrasings right and none confident-wrong; with a scripted AG-06 that replies the
expected ops by widget id, every case is right (each expected op can reach the preview through the merge, apply and
the chart guard). The live off/on scoring on a real model is the US-246 final gate's.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h
from test_ask_api import ask
from test_ask_review_api import review_on
from test_authoring_api import items, new_report, pin, world  # noqa: F401 - the TPG world

from tela_api.content import canvas_gold
from tela_api.core.settings import get_settings

pytestmark = [needs_db, pytest.mark.integration]

GOLD = canvas_gold.load()
CASES = {c["id"]: c for c in GOLD["cases"]}
RULES_RIGHT = {"ce01", "ce02", "ce03", "ce04", "ce05", "ce06", "ce07", "ce08", "ce09", "ce10", "ce18", "ce19",
               "ce20"}


async def gold_canvas(stack: httpx.AsyncClient, w: World) -> tuple[str, list[dict[str, Any]]]:
    token = w.tokens["creator"]
    report = await new_report(stack, token, "Gold canvas")
    for c in GOLD["canvas"]:
        turn, _ = await ask(stack, token, c["question"])
        assert (await pin(stack, token, turn, c["title"], c["visual"], report["id"])).status_code == 201, c
    got = (await stack.get(f"/api/v1/reports/{report['id']}", headers=h(token))).json()
    return report["id"], items(got)


async def run(stack: httpx.AsyncClient, w: World, rid: str, before: list[dict[str, Any]]) -> dict[str, list[str]]:
    out = {}
    for case in GOLD["cases"]:
        resp = await stack.post(f"/api/v1/reports/{rid}/chat-edit", headers=h(w.tokens["creator"]),
                                json={"instruction": case["instruction"]})
        assert resp.status_code == 200, (case["id"], resp.text)
        out[case["id"]] = canvas_gold.score(resp.json(), case["expect"], before)

    return out


def scripted(task: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """AG-06 that knows the gold: the expected ops by widget id, or a confirm when nothing should be added."""
    body = json.loads(messages[1]["content"])
    case = next(c for c in GOLD["cases"] if c["instruction"] == body["request"])
    ids = {x["title"]: x["id"] for x in body["context"]["widgets"]}
    dims = {d["label"]: d["key"] for d in body["context"]["dimensions"]}
    ops = []
    for want in case["expect"].get("ops", []):
        if want["op"] == "add_filter":
            ops.append({"op": "add_filter", "dimension": dims[want["widget"]]})
            continue
        op = {"op": want["op"], "widget": ids[want["widget"]]}
        op.update({"width": want.get("w"), "height": want.get("h"), "to": want.get("to"), "visual": want.get("visual"),
                   "limit": want.get("limit"), "next_to": ids.get(want.get("next_to", ""))})
        ops.append(op)
    if not ops:
        return {"verdict": "confirm", "reason": "nothing to add"}
    return {"verdict": "correct", "replacement": {"ops": ops}, "reason": "the gold reading"}


async def test_the_gold_set_has_twenty_scorable_cases() -> None:
    assert len(GOLD["cases"]) == 20 and len(CASES) == 20
    titles = {c["title"] for c in GOLD["canvas"]}
    for case in GOLD["cases"]:
        named = [o["widget"] for o in case["expect"].get("ops", []) if o["op"] != "add_filter"]
        named += [o["next_to"] for o in case["expect"].get("ops", []) if "next_to" in o]
        named += [r["widget"] for r in case["expect"].get("refused", [])] + case["expect"].get("clarify", [])
        assert set(named) <= titles, case["id"]


async def test_gold_rules_baseline_and_a_scripted_review(stack: httpx.AsyncClient, world: World,
                                                         owner_conn: psycopg.Connection,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", [])  # measured with AG-06 back in review
    rid, before = await gold_canvas(stack, world)
    review_on(owner_conn, world, False)
    off = await run(stack, world, rid, before)
    assert {k for k, v in off.items() if not v} == RULES_RIGHT, off
    assert not any(m.startswith("unexpected") for v in off.values() for m in v), off  # the rules never misread

    review_on(owner_conn, world)
    stack.app.state.gateway = FakeGateway({"AG-06": scripted}).client()  # type: ignore[attr-defined]
    on = await run(stack, world, rid, before)
    assert {k: v for k, v in on.items() if v} == {}
