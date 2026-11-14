"""Agents screen API: catalogue, editable prompts and skillsets (ENH-030, US-264 to US-266)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event, parse_sse

from tela_api.agents import catalog, prompts

pytestmark = [needs_db, pytest.mark.integration]
BASE = "/api/v1/admin/agents"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> AsyncIterator[World]:
    prompts.invalidate()
    yield await setup_world(stack, owner_conn, make_token, tpg_source)
    prompts.invalidate()


def system_of(call: dict[str, Any]) -> str:
    text: str = call["messages"][0]["content"]
    return text


async def test_admins_see_every_agent_with_its_prompts_and_details(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    resp = await stack.get(BASE, headers=h(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["code"] for a in body["items"]] == [a.code for a in catalog.AGENTS]
    ag03 = next(a for a in body["items"] if a["code"] == "AG-03")
    assert ag03["name"] == "Query planner" and ag03["prompts"] == 4 and ag03["edited"] == 0
    assert ag03["stats"]["llm_calls"] == 0 and body["stats_days"] == 7
    assert [p["key"] for p in body["shared"]] == ["review.frame"]
    assert next(a for a in body["items"] if a["code"] == "AG-05")["review_off"] is True

    d = (await stack.get(f"{BASE}/ag-03", headers=h(admin))).json()
    assert d["inputs"] and d["guard"] and {"label": "Gold examples", "href": "/admin/knowledge/gold"} in d["knowledge"]
    plan = next(p for p in d["prompts"] if p["key"] == "ask.plan")
    assert plan["text"] == plan["default"] == catalog.default("ask.plan").strip() and plan["version"] == 0
    author = next(p for p in (await stack.get(f"{BASE}/AG-04", headers=h(admin))).json()["prompts"]
                  if p["key"] == "packs.author")
    assert author["editable"] is False and "read-only" in author["note"]

    assert (await stack.get(BASE, headers=h(world.tokens["creator"]))).status_code == 403
    assert (await stack.get(f"{BASE}/AG-11", headers=h(admin))).status_code == 404


async def test_an_edited_prompt_and_skills_reach_the_model_and_reset_goes_back(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    url = f"{BASE}/AG-03/prompts/ask.plan"
    resp = await stack.put(url, headers=h(admin), json={"text": "You plan PMO questions. Be exact.",
                                                        "note": "Shorter"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["edited"] is True and resp.json()["version"] == 1 and resp.json()["change_note"] == "Shorter"
    # Saving the same text again makes no new version.
    assert (await stack.put(url, headers=h(admin), json={"text": "You plan PMO questions. Be exact."})
            ).json()["version"] == 1
    skill = await stack.post(f"{BASE}/AG-03/skills", headers=h(admin),
                             json={"name": "Slipping", "body": "'Slipping' means schedule risk."})
    assert skill.status_code == 201, skill.text
    off = await stack.post(f"{BASE}/AG-03/skills", headers=h(admin),
                           json={"name": "Unused", "body": "Never sent.", "enabled": False})
    assert off.status_code == 201

    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, events = await ask(stack, admin, "where are we slipping")
    assert by_event(events)["plan"]["planner"] == "llm"
    plans = [system_of(c) for c in fake.calls if c["agent"] == "AG-03"]
    assert not any(catalog.default("ask.plan") in s for s in plans)
    sent = next(s for s in plans if "You plan PMO questions. Be exact." in s)  # linking has its own prompt
    assert all("## Slipping" in s for s in plans)  # every AG-03 call gets its skills
    assert "## Slipping\n'Slipping' means schedule risk." in sent and "Never sent." not in sent
    assert sent.index(prompts.SKILLS_HEAD) < sent.index("Answer with JSON only")
    # The run log names what was used.
    run = (await stack.get("/api/v1/admin/agent-runs", headers=h(admin), params={"turn_id": turn_id})).json()
    d = (await stack.get(f"/api/v1/admin/agent-runs/{run['items'][0]['id']}", headers=h(admin))).json()
    tuned = [s["detail"].get("tuned") for s in d["steps"] if s["kind"] == "llm_call" and s["agent"] == "AG-03"]
    assert {"prompts": ["ask.plan"], "skills": ["Slipping"]} in tuned
    assert all(t and t["skills"] == ["Slipping"] for t in tuned)
    assert (await stack.get(f"{BASE}/AG-03", headers=h(admin))).json()["stats"]["llm_calls"] >= 1

    reset = await stack.delete(url, headers=h(admin))
    assert reset.json()["edited"] is False and reset.json()["version"] == 2
    assert reset.json()["text"] == catalog.default("ask.plan").strip()
    versions = (await stack.get(f"{url}/versions", headers=h(admin))).json()["items"]
    assert [(v["version"], v["is_default"]) for v in versions] == [(2, True), (1, False)]
    assert versions[1]["text"] == "You plan PMO questions. Be exact." and versions[1]["by"] == "Ada Admin"

    fake.calls.clear()
    await ask(stack, admin, "where are we slipping")
    assert any(catalog.default("ask.plan") in system_of(c) for c in fake.calls if c["agent"] == "AG-03")

    rows = owner_conn.execute("SELECT action, object_id FROM ops.audit_events WHERE action LIKE 'agent_%' "
                              "ORDER BY occurred_at").fetchall()
    assert [r[0] for r in rows].count("agent_prompt.changed") == 2
    assert [r[0] for r in rows].count("agent_skill.changed") == 2


async def test_prompt_rules(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    template = await stack.put(f"{BASE}/AG-04/prompts/packs.author", headers=h(admin), json={"text": "x"})
    assert template.status_code == 400
    wrong_agent = await stack.put(f"{BASE}/AG-08/prompts/ask.plan", headers=h(admin), json={"text": "x"})
    assert wrong_agent.status_code == 404
    blank = await stack.put(f"{BASE}/AG-03/prompts/ask.plan", headers=h(admin), json={"text": "   "})
    assert blank.status_code == 400
    # Saving the default's own text keeps it as "default".
    same = await stack.put(f"{BASE}/AG-03/prompts/ask.plan", headers=h(admin),
                           json={"text": catalog.default("ask.plan")})
    assert same.json()["edited"] is False and same.json()["version"] == 0
    shared = await stack.put(f"{BASE}/shared/prompts/review.frame", headers=h(admin), json={"text": "Review it."})
    assert shared.status_code == 200 and shared.json()["edited"] is True
    assert (await stack.put(f"{BASE}/AG-03/prompts/ask.plan", headers=h(world.tokens["creator"]),
                            json={"text": "x"})).status_code == 403


async def test_skillset_add_edit_turn_off_and_delete(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    made = (await stack.post(f"{BASE}/AG-08/skills", headers=h(admin),
                             json={"name": "Tone", "body": "Write for a PMO director."})).json()
    dup = await stack.post(f"{BASE}/AG-08/skills", headers=h(admin), json={"name": "tone", "body": "Again"})
    assert dup.status_code == 409
    edited = await stack.put(f"{BASE}/AG-08/skills/{made['id']}", headers=h(admin),
                             json={"name": "Tone", "body": "Short sentences.", "enabled": False})
    assert edited.status_code == 200 and edited.json()["enabled"] is False and edited.json()["updated_by"]
    listed = (await stack.get(BASE, headers=h(admin))).json()["items"]
    ag08 = next(a for a in listed if a["code"] == "AG-08")
    assert ag08["skills"] == 1 and ag08["skills_on"] == 0
    # The skills that are on stay under the limit.
    big = "x" * prompts.SKILL_MAX
    assert (await stack.post(f"{BASE}/AG-08/skills", headers=h(admin), json={"name": "A", "body": big})
            ).status_code == 201
    over = await stack.post(f"{BASE}/AG-08/skills", headers=h(admin), json={"name": "B", "body": big})
    assert over.status_code == 400 and "12,000" in over.json()["error"]["message"]
    assert (await stack.post(f"{BASE}/AG-08/skills", headers=h(admin),
                             json={"name": "B", "body": big, "enabled": False})).status_code == 201
    # A skill is found under its own agent only.
    assert (await stack.delete(f"{BASE}/AG-03/skills/{made['id']}", headers=h(admin))).status_code == 404
    assert (await stack.delete(f"{BASE}/AG-08/skills/{made['id']}", headers=h(admin))).status_code == 204
    names = [s["name"] for s in (await stack.get(f"{BASE}/AG-08", headers=h(admin))).json()["skills"]]
    assert names == ["A", "B"]


# --------------------------------------------------------------------------- Try it (US-267)


def planner_by_prompt(task: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    """AG-03 answers schedule risk only when the draft's words are in its prompt, open projects otherwise."""
    system = messages[0]["content"]
    if "measures that could answer" in system or "choosing candidates" in system:  # linking: offer both
        return {"measures": ["measure:schedule_risk", "measure:open_projects"], "dimensions": ["dimension:region"]}
    if "Slipping means schedule risk" in system:
        return {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}
    return {"measure": "measure:open_projects", "breakdowns": ["dimension:region"]}


async def try_it(api: httpx.AsyncClient, token: str, code: str, **body: Any) -> dict[str, Any]:
    resp = await api.post(f"{BASE}/{code}/try", headers={**h(token), "Accept": "text/event-stream"}, json=body)
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    assert events[0][1] == "start" and events[-1][1] == "complete"
    sides = {e[2]["side"]: e[2] for e in events if e[1] == "side"}
    return {"start": events[0][2], **sides}


async def test_try_it_runs_the_question_with_saved_and_draft_side_by_side_and_saves_nothing(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    fake = FakeGateway({"AG-03": planner_by_prompt})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    draft = catalog.default("ask.plan") + " Slipping means schedule risk."
    out = await try_it(stack, admin, "AG-03", question="where are we slipping",
                       prompt={"key": "ask.plan", "text": draft})
    before, after = out["before"], out["after"]
    assert out["start"]["kind"] == "ask" and out["start"]["connection"]["id"]
    assert before["spec"]["measure"]["ref"] == "measure:open_projects"
    assert after["spec"]["measure"]["ref"] == "measure:schedule_risk" and after["reading"]
    assert any(c["agent"] == "AG-03" and c["tuned"]["prompts"] == ["ask.plan"] for c in after["calls"])
    assert not any(c["tuned"]["prompts"] for c in before["calls"]) and before["calls"][0]["reply"]
    # Nothing saved, nothing in the run log.
    assert (await stack.get(f"{BASE}/AG-03", headers=h(admin))).json()["prompts"][0]["version"] == 0
    assert owner_conn.execute("SELECT count(*) FROM ops.agent_runs").fetchone()[0] == 0

    # A draft skill instead of a prompt: the saved skill it replaces is left out of "after".
    saved = (await stack.post(f"{BASE}/AG-03/skills", headers=h(admin),
                              json={"name": "Old note", "body": "Prefer counts."})).json()
    out = await try_it(stack, admin, "AG-03", question="where are we slipping",
                       skill={"id": saved["id"], "name": "Old note", "body": "Slipping means schedule risk."})
    assert out["after"]["spec"]["measure"]["ref"] == "measure:schedule_risk"
    assert out["before"]["spec"]["measure"]["ref"] == "measure:open_projects"
    assert all(c["tuned"]["skills"] == ["Old note"] for c in out["before"]["calls"] + out["after"]["calls"])


async def test_try_it_drafts_sql_for_ag10_and_refuses_what_it_cannot_try(stack: httpx.AsyncClient,
                                                                         world: World) -> None:
    admin = world.tokens["admin"]
    calls: list[str] = []

    def sql(task: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        calls.append(messages[0]["content"])
        return {"sql": "SELECT region, count(*) AS n FROM projects WHERE :page_filters GROUP BY region",
                "explanation": "Projects per region."}

    fake = FakeGateway({"AG-10": sql})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    out = await try_it(stack, admin, "AG-10", question="projects by region",
                       prompt={"key": "sql.role", "text": "You draft careful SQL for PMO people."})
    assert out["start"]["kind"] == "sql"
    assert {out["before"]["status"], out["after"]["status"]} <= {"ok", "unsupported"}
    assert any("You draft careful SQL for PMO people." in c for c in calls)
    assert any(catalog.default("sql.role") in c for c in calls)

    assert (await stack.post(f"{BASE}/AG-08/try", headers=h(admin),
                             json={"question": "x", "prompt": {"key": "ask.narrate", "text": "y"}})).status_code == 400
    assert (await stack.post(f"{BASE}/AG-03/try", headers=h(admin), json={"question": "x"})).status_code == 400
    assert (await stack.post(f"{BASE}/AG-03/try", headers=h(admin),
                             json={"question": "x", "prompt": {"key": "ask.narrate", "text": "y"}})).status_code == 404
    assert (await stack.post(f"{BASE}/AG-03/try", headers=h(world.tokens["creator"]),
                             json={"question": "x", "prompt": {"key": "ask.plan", "text": "y"}})).status_code == 403
