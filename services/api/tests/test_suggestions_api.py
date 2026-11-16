"""Chart enhancement suggestions through the API (US-104; FEAT-016; REQ-040; RULE-008).

Acceptance: TEST-018 (given a widget whose visual breaks a suitability rule, Suggestions proposes a suitable
visual with a reason, and applying it can be undone). Also: chart patterns are indexed with the model and follow
it; Viewers get no suggestions; Apply on an answer is a refinement with Undo; a suggestion that no longer fits is
refused; AG-07's answer is held to typed ops, and the deterministic suggestions stand when it is down.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event
from test_authoring_api import items, new_report, pin, stream
from test_knowledge_api import bag, index_model

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    return w


async def test_chart_patterns_are_indexed_with_the_model(stack: httpx.AsyncClient, world: World,
                                                        owner_conn: psycopg.Connection,
                                                        signing_keys: dict[str, Any]) -> None:
    out = await index_model(stack, world, signing_keys)
    assert out["chart_patterns"] >= 10
    docs = (await stack.get("/api/v1/knowledge/documents", headers=h(world.tokens["admin"]),
                            params={"kind": "chart_pattern"})).json()["items"]
    assert len(docs) == 1 and docs[0]["review_status"] == "approved" and docs[0]["title"].startswith("Chart patterns")
    for call in (stack.delete(f"/api/v1/knowledge/documents/{docs[0]['id']}", headers=h(world.tokens["admin"])),
                 stack.post(f"/api/v1/knowledge/documents/{docs[0]['id']}/review", headers=h(world.tokens["admin"]),
                            json={"decision": "retire"})):
        assert (await call).status_code == 409  # they follow the model
    found = (await stack.post("/api/v1/knowledge/search", headers=h(world.tokens["admin"]), json={
        "query": "forecast cost approved budget chart", "connection_id": world.connection_id,
        "kinds": ["chart_pattern"]})).json()
    assert found["hits"] and all(x["kind"] == "chart_pattern" for x in found["hits"])
    # Ask's own retrieval does not pick chart patterns up.
    _turn_id, events = await ask(stack, world.tokens["creator"], "Forecast cost by portfolio")
    refs = set(by_event(events)["retrieval"].get("knowledge_refs") or [])
    patterns = {f"chunk:{r[0]}" for r in owner_conn.execute(
        "SELECT c.id FROM rag.knowledge_chunks c JOIN rag.knowledge_documents d ON d.id = c.document_id "
        "WHERE d.source_kind = 'chart_pattern'").fetchall()}
    assert refs and patterns and not refs & patterns


async def test_suggestions_on_an_answer_apply_as_a_refinement(stack: httpx.AsyncClient, world: World,
                                                             signing_keys: dict[str, Any]) -> None:
    await index_model(stack, world, signing_keys)
    creator = world.tokens["creator"]
    turn_id, _ = await ask(stack, creator, "Forecast cost by portfolio")
    resp = await stack.get(f"/api/v1/ask/turns/{turn_id}/suggestions", headers=h(creator))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "rules" and 1 <= len(body["suggestions"]) <= 3  # no provider set: deterministic
    fv = next(s for s in body["suggestions"] if s["pattern"] == "forecast_vs_budget")
    assert fv["visual"] == "paired_bars" and fv["reason"] and fv["cites"]  # cites the retrieved pattern
    assert any(p["pattern"] == "forecast_vs_budget" for p in body["patterns"])
    viewer_turn, _ = await ask(stack, world.tokens["viewer"], "How many projects are there?")
    denied = await stack.get(f"/api/v1/ask/turns/{viewer_turn}/suggestions", headers=h(world.tokens["viewer"]))
    assert denied.status_code == 403
    # Apply = refine with the suggestion's ops; Undo goes back.
    applied = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(creator),
                               json={"chip_ops": fv["ops"]})
    assert applied.status_code == 202, applied.text
    done = (await stream(stack, creator, applied.json()))["complete"]
    assert done["spec"]["extra_measures"] == [{"ref": "measure:approved_budget_total"}]
    assert done["spec"]["visual"] == {"type": "paired_bars"}
    undo = await stack.post(f"/api/v1/ask/turns/{applied.json()['turn_id']}/undo", headers=h(creator))
    back = (await stream(stack, creator, undo.json()))["complete"]
    assert "extra_measures" not in back["spec"] or back["spec"]["extra_measures"] == []


async def test_widget_with_an_unsuitable_visual_test_018(stack: httpx.AsyncClient, world: World,
                                                        owner_conn: psycopg.Connection) -> None:
    creator = world.tokens["creator"]
    report = await new_report(stack, creator)
    t1, _ = await ask(stack, creator, "Open projects by region")
    assert (await pin(stack, creator, t1, "Open by region", "ranked_bar", report["id"])).status_code == 201
    item = items((await stack.get(f"/api/v1/reports/{report['id']}", headers=h(creator))).json())[0]
    # The data or the rules changed since it was made: the stored chart is now one RULE-008 refuses.
    owner_conn.execute("UPDATE content.widgets SET visual = 'trend', spec = spec || '{\"visual\": {\"type\": "
                       "\"trend\"}}' WHERE id = (SELECT widget_id FROM content.report_items WHERE id = %s)",
                       (item["id"],))
    url = f"/api/v1/reports/{report['id']}/items/{item['id']}/suggestions"
    got = (await stack.get(url, headers=h(creator))).json()
    first = got["suggestions"][0]
    assert first["kind"] == "visual" and first["visual"] == "ranked_bar"
    assert first["reason"].startswith("Trend needs a date breakdown")
    applied = await stack.post(f"{url}/apply", headers=h(creator), json={"ops": first["ops"],
                                                                         "title": first["title"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["undo_summary"].startswith("Suggestion: Show as ranked bars")
    after = items(applied.json())[0]
    assert after["visual"] == "ranked_bar"
    undone = (await stack.post(f"/api/v1/reports/{report['id']}/undo", headers=h(creator))).json()
    assert items(undone)[0]["visual"] == "trend"
    # Applying an unsuitable chart is refused; other people's reports are not found; Viewers can't.
    bad = await stack.post(f"{url}/apply", headers=h(creator), json={"ops": [{"op": "set_visual", "value": "donut"},
                                                                             {"op": "add_breakdown",
                                                                              "ref": "dimension:priority"}]})
    assert bad.status_code == 400
    assert (await stack.get(url, headers=h(world.tokens["admin"]))).status_code == 404
    assert (await stack.get(url, headers=h(world.tokens["viewer"]))).status_code == 403
    assert owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action = 'report.suggestion_applied'"
                              ).fetchone()[0] == 1


async def test_ag07_answer_is_validated_and_rules_stand_when_it_fails(stack: httpx.AsyncClient, world: World,
                                                                     owner_conn: psycopg.Connection) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    stack.app.state.gateway = FakeGateway({"AG-01": {"ok": True}}).client()  # type: ignore[attr-defined]
    ok = await stack.put("/api/v1/admin/llm-settings", headers=h(admin), json={
        "provider": "ollama", "models": {"planner": "llama3.2:3b", "advisor": "llama3.2:3b"}})
    assert ok.status_code == 200, ok.text
    reply = {"suggestions": [
        {"ops": [{"op": "add_breakdown", "ref": "dimension:priority"}], "title": "Split by priority",
         "reason": "Priority shows which regions carry the critical work."},
        {"ops": [{"op": "add_measure", "ref": "measure:nope"}], "title": "Bogus", "reason": "Not a measure."},
    ], "looks_good": False}
    fake = FakeGateway({"AG-07": reply}, embedder=bag)
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, _ = await ask(stack, creator, "Open projects by region")
    out = (await stack.get(f"/api/v1/ask/turns/{turn_id}/suggestions", headers=h(creator))).json()
    assert out["source"] == "llm"
    assert out["suggestions"][0]["title"] == "Split by priority" and out["suggestions"][0]["source"] == "llm"
    assert all(s["title"] != "Bogus" for s in out["suggestions"])
    call = next(c for c in fake.calls if c["agent"] == "AG-07")
    sent = call["messages"][-1]["content"]
    assert "KSA" not in sent and "Jordan" not in sent  # shape only, never values
    stack.app.state.gateway = FakeGateway(fail="planner_unavailable").client()  # type: ignore[attr-defined]
    down = (await stack.get(f"/api/v1/ask/turns/{turn_id}/suggestions", headers=h(creator))).json()
    assert down["source"] == "rules" and down["suggestions"]
