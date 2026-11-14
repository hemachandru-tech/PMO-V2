"""Ask a question (US-046 to US-051). Acceptance: TEST-020, TEST-021, TEST-022, TEST-065, TEST-095."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def parse_sse(text: str) -> list[tuple[int | None, str, dict[str, Any]]]:
    events = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith(":") and not ln.startswith("retry")]
        if not lines:
            continue
        fields = dict(ln.split(": ", 1) for ln in lines)
        events.append((int(fields["id"]) if "id" in fields else None, fields["event"], json.loads(fields["data"])))
    return events


async def ask(api: httpx.AsyncClient, token: str, question: str, **body: Any) -> tuple[str, list[Any]]:
    resp = await api.post("/api/v1/ask/turns", headers=h(token), json={"question": question, **body})
    assert resp.status_code == 202, resp.text
    turn = resp.json()
    stream = await api.get(turn["stream_url"], headers=h(token))
    assert stream.status_code == 200 and stream.headers["content-type"].startswith("text/event-stream")
    return turn["turn_id"], parse_sse(stream.text)


def by_event(events: list[Any]) -> dict[str, dict[str, Any]]:
    return {e[1]: e[2] for e in events}


async def test_certified_answer_with_grounded_headline(stack: httpx.AsyncClient, world: World,
                                                       owner_conn: psycopg.Connection) -> None:
    turn_id, events = await ask(stack, world.tokens["creator"], "Which programmes have the most overdue go-lives?")
    names = [e[1] for e in events]
    assert names == ["intent", "retrieval", "plan", "compile", "validate", "execute", "viz", "narrative", "complete"]
    ev = by_event(events)
    assert ev["plan"]["spec"]["measure"]["ref"] == "measure:overdue_go_lives"
    assert ev["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:programme"}]
    done = ev["complete"]
    assert done["certified"] and done["badge"] == "certified" and done["scope_text"] and done["as_of"]
    # Creator scope: Gulf regions only (RULE-004).
    expected = sum(1 for p in oracle.PROJECTS if p["region"] in {"KSA", "GCC - UAE"}
                   and oracle.RULES["overdue_go_lives"](p))
    assert sum(r[1] for r in done["rows"]) == expected
    # TEST-095 / RULE-022: every number in the headline is in the result.
    headline = ev["narrative"]["headline"]
    import re

    numbers = {n for r in done["rows"] for v in r for n in re.findall(r"\d+", str(v))}
    numbers |= {str(expected), str(len(done["rows"]))}

    assert all(n.replace(",", "") in numbers for n in re.findall(r"\d[\d,]*", headline)), headline
    assert ev["viz"]["visual"] == "ranked_bar"
    # US-185: insights name aggregated members only, each sentence cites its fact and stays grounded.
    from tela_api.ask import insights
    from tela_api.ask.narrator import numbers_in

    found = ev["narrative"]["insights"]
    assert len(found) <= 4 and [f["id"] for f in found] == [f"f{i}" for i in range(1, len(found) + 1)]
    labels = {str(r[0]) for r in done["rows"]} | {"Not set"}
    assert all(set(f["members"]) <= labels for f in found)
    assert all(numbers_in(f["text"]) <= insights.numbers_of(f, "integer") for f in found)
    # US-186: the card lists the insights apart from the sentences; with no model each is its fact's template sentence.
    story = ev["narrative"]
    assert story["headline_facts"] == ["f0"] and story["source"] == "template"
    assert all("fact" not in s for s in story["sentences"])
    told = [x for x in found if x["type"] != "data_note"][:3]
    assert story["cited"] == [{"text": x["text"], "facts": [x["id"]], "source": "template"} for x in told]
    assert story["notes"] == [{"text": x["text"], "fact": x["id"]} for x in found if x["type"] == "data_note"]
    assert {n["kind"] for n in story["next"]} <= {"driver", "compare", "drill"} and len(story["next"]) <= 3
    for n in story["next"]:  # every follow-up is a refinement the API accepts
        resp = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(world.tokens["creator"]),
                                json={"chip_ops": n["ops"]})
        assert resp.status_code == 202, (n, resp.text)
    # Rows are never stored on the turn; re-reading re-runs the spec.
    stored = owner_conn.execute("SELECT spec IS NOT NULL, narrative->>'headline', status FROM chat.turns "
                                "WHERE id = %s", (turn_id,)).fetchone()
    assert stored == (True, headline, "complete")
    stored_events = owner_conn.execute("SELECT data FROM chat.turn_events WHERE turn_id = %s", (turn_id,)).fetchall()
    assert all(not isinstance(e[0].get("rows"), list) for e in stored_events)  # rows are streamed, never stored
    again = (await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(world.tokens["creator"]))).json()
    assert again["result"]["rows"] == done["rows"]
    assert (await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(world.tokens["nomsa"]))).status_code == 404


async def test_named_gap_runs_no_query(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    before = owner_conn.execute("SELECT count(*) FROM ops.query_executions WHERE surface = 'ask'").fetchone()[0]
    _, events = await ask(stack, world.tokens["admin"], "Show team utilisation by delivery manager")
    err = by_event(events)["error"]
    assert err["code"] == "unsupported_question" and "utilisation or timesheet" in err["message"]
    assert err["message"].startswith("I can't answer that from the connected data")
    after = owner_conn.execute("SELECT count(*) FROM ops.query_executions WHERE surface = 'ask'").fetchone()[0]
    assert after == before


async def test_money_ambiguity_asks_then_remembers(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["admin"]
    turn_id, events = await ask(stack, token, "total cost by region")
    assert [e[1] for e in events][-1] == "clarify"
    q = by_event(events)["clarify"]
    labels = [o["label"] for o in q["options"]]
    assert "Forecast cost (EAC, USD)" in labels and any("budget" in lbl.lower() for lbl in labels)
    forecast = next(o for o in q["options"] if o["label"] == "Forecast cost (EAC, USD)")
    resumed = await stack.post(f"/api/v1/ask/turns/{turn_id}/clarification", headers=h(token),
                               json={"option_id": forecast["id"]})
    assert resumed.status_code == 202
    stream = await stack.get(f"/api/v1/ask/turns/{turn_id}/stream", headers=h(token, **{"Last-Event-ID": str(
        max(e[0] for e in events))}))
    later = parse_sse(stream.text)
    assert later[0][1] == "intent" and later[-1][1] == "complete"
    assert by_event(later)["plan"]["spec"]["measure"]["ref"] == "measure:forecast_cost_total"
    # Reopened later, the turn replays past its answered clarify to the answer, and the clarify can't be re-answered.
    replay = parse_sse((await stack.get(f"/api/v1/ask/turns/{turn_id}/stream", headers=h(token))).text)
    names = [e[1] for e in replay]
    assert "clarify" in names and names[-1] == "complete"
    again = await stack.post(f"/api/v1/ask/turns/{turn_id}/clarification", headers=h(token),
                             json={"option_id": forecast["id"]})
    assert again.status_code == 409
    # The same ambiguity later in the conversation reuses the choice and shows it as a chip.
    conversation = (await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(token))).json()["conversation_id"]
    _, events2 = await ask(stack, token, "total cost by portfolio", conversation_id=conversation)
    ev2 = by_event(events2)
    assert "clarify" not in ev2 and ev2["complete"]["interpreted"] == ["Interpreted as: Forecast cost (EAC, USD)"]


async def test_write_requests_are_refused_and_audited(stack: httpx.AsyncClient, world: World,
                                                      owner_conn: psycopg.Connection) -> None:
    _, events = await ask(stack, world.tokens["admin"], "Update project PRJ-001 status to Closed")
    err = by_event(events)["error"]
    assert err == {**err, "code": "write_refused", "message": "Tela is read-only, so it can't change source data."}
    assert owner_conn.execute("SELECT outcome FROM ops.audit_events WHERE action = 'ask.write_refused'"
                              ).fetchone() == ("deny",)


async def test_definition_question(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["nomsa"], "What does overdue go-lives mean?")
    done = by_event(events)["complete"]
    assert done["definition"]["ref"] == "measure:overdue_go_lives"
    assert "planned deployment date" in done["definition"]["rule_text"]


async def test_scope_and_filters_by_value(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["nomsa"], "open projects in KSA")
    ev = by_event(events)
    expected = sum(1 for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei" and p["region"] == "KSA"
                   and p["status"] == "Active")
    if expected:
        assert ev["plan"]["spec"]["filters"] == [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
        assert ev["complete"]["rows"] == [[expected]]
    _, events = await ask(stack, world.tokens["viewer"], "open projects")
    assert by_event(events)["error"]["code"] == "no_source"


async def test_dashboard_filters_apply_where_they_reach(stack: httpx.AsyncClient, world: World) -> None:
    """US-189: a question from a dashboard's Ask panel carries the page filters; those the measure can't reach are
    left out and named, not a failed query."""
    page = [{"ref": "dimension:region", "op": "=", "values": ["KSA"]},
            {"ref": "dimension:no_such_dimension", "op": "=", "values": ["x"]}]
    _, events = await ask(stack, world.tokens["nomsa"], "open projects",
                          context={"dashboard_id": "d1", "page_filters": page})
    done = by_event(events)["complete"]
    expected = sum(1 for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei" and p["region"] == "KSA"
                   and p["status"] == "Active")
    assert done["spec"]["filters"] == [page[0]]
    if expected:
        assert done["rows"] == [[expected]]
    assert "Dashboard filters: Region" in done["interpreted"]
    assert any(i.startswith("Dashboard filters not applied") and "no_such_dimension" in i for i in done["interpreted"])


async def test_llm_planner_used_only_when_rules_fail(stack: httpx.AsyncClient, world: World) -> None:
    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], "where are we slipping")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["spec"]["measure"]["ref"] == "measure:schedule_risk"
    # No measure is named, so AG-03 first picks candidates from the catalogue (US-181), then plans from them three
    # times at a moderate temperature and keeps the majority (US-182).
    assert [c["agent"] for c in fake.calls] == ["AG-03"] * 4
    assert [c.get("temperature") for c in fake.calls] == [None, 0.6, 0.6, 0.6]
    assert "choosing candidates" in fake.calls[0]["messages"][0]["content"]
    # The planner only ever saw metadata: no row values reached the model (RULE-019).
    prompt = json.dumps([c["messages"] for c in fake.calls])
    assert "Nomsa Osei" not in prompt and "PRJ-0" not in prompt
    _, events = await ask(stack, world.tokens["admin"], "open projects by region")
    assert by_event(events)["plan"]["planner"] == "rules" and len(fake.calls) == 4
    # Narrative stays template-based while aggregates may not leave (default off, TEST-058).
    assert by_event(events)["narrative"]["source"] == "template"


async def test_narrator_v2_keeps_checked_sentences_and_falls_back_per_fact(stack: httpx.AsyncClient,
                                                                            world: World) -> None:
    """US-186: AG-08 v2 sees fact ids and numbers only; a headline with a number no fact has falls back to the
    template, a checked insight sentence is kept with its marker stripped."""
    admin = world.tokens["admin"]
    fake = FakeGateway({"AG-08": {"headline": {"text": "999 overdue go-lives in all.", "facts": ["f0"]},
                                  "insights": [{"text": "One programme stands out. [fact:1]", "facts": []}]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    put = await stack.put("/api/v1/admin/llm-settings", headers=h(admin),
                          json={"provider": "ollama", "models": {"planner": "llama3"}, "allow_aggregates": True})
    assert put.status_code == 200, put.text
    try:
        _, events = await ask(stack, admin, "Which programmes have the most overdue go-lives?")
    finally:
        await stack.put("/api/v1/admin/llm-settings", headers=h(admin),
                        json={"provider": "ollama", "models": {"planner": "llama3"}, "allow_aggregates": False})
    story = by_event(events)["narrative"]
    assert "AG-08" in [c["agent"] for c in fake.calls]
    prompt = json.dumps(next(c for c in fake.calls if c["agent"] == "AG-08")["messages"])
    assert '\\"f0\\"' in prompt and "PRJ-0" not in prompt and "Nomsa Osei" not in prompt
    assert story["headline_facts"] == ["f0"] and "999" not in story["headline"]
    told = [x for x in story["insights"] if x["type"] != "data_note"]
    if told and told[0]["id"] == "f1":
        assert story["cited"][0] == {"text": "One programme stands out.", "facts": ["f1"], "source": "llm"}
        assert story["source"] == "mixed"
    else:
        assert story["source"] == "template"


async def test_chat_unavailable_message_when_llm_down(stack: httpx.AsyncClient, world: World) -> None:
    stack.app.state.gateway = FakeGateway(fail="planner_unavailable").client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], "where are we slipping")
    assert by_event(events)["error"]["message"] == "Chat is temporarily unavailable. Certified figures still work."
    _, events = await ask(stack, world.tokens["admin"], "overdue go-lives")
    assert by_event(events)["complete"]["rows"] == [[oracle.count("overdue_go_lives")]]


async def test_validation_and_limits(stack: httpx.AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    token = world.tokens["admin"]
    assert (await stack.post("/api/v1/ask/turns", headers=h(token), json={"question": ""})).status_code == 400
    assert (await stack.post("/api/v1/ask/turns", headers=h(token), json={"question": "x" * 2001})).status_code == 400
    other = await stack.post("/api/v1/ask/turns", headers=h(token), json={
        "question": "open projects", "conversation_id": "01900000-0000-7000-8000-000000000000"})
    assert other.status_code == 404
    from tela_api.ask import router as ask_router

    monkeypatch.setattr(ask_router, "PER_USER", 0)  # as if two answers were already in flight
    busy = await stack.post("/api/v1/ask/turns", headers=h(token), json={"question": "open projects"})
    assert busy.status_code == 429 and busy.json()["error"]["code"] == "rate_limited"


def test_narrative_names_ties_and_stays_grounded() -> None:
    from tela_api.ask import narrator

    result = {"columns": [{"name": "region", "kind": "dimension", "label": "Region"},
                          {"name": "n", "kind": "measure", "label": "Overdue go-lives", "format": "integer"}],
              "rows": [["KSA", 1], ["East Africa", 1], ["GCC - UAE", 1], ["Jordan", 0]]}
    f = narrator.facts(result)
    story = narrator.template(f)
    assert story["headline"] == "KSA, East Africa and GCC - UAE are tied for the most overdue go-lives (1 each of 3)."
    assert narrator.grounded(story["headline"], f)
    assert not narrator.grounded("KSA has 7 overdue go-lives.", f)
    empty = narrator.template(narrator.facts({**result, "rows": [["KSA", 0]]}))
    assert empty["headline"] == "Overdue go-lives: 0." or empty["headline"].startswith("No overdue")


async def test_generic_label_does_not_answer_unknown_questions(stack: httpx.AsyncClient, world: World) -> None:
    _, events = await ask(stack, world.tokens["admin"], "where are projects slipping the most?")
    assert by_event(events)["error"]["code"] == "unsupported_question"  # no confident wrong answer
    _, events = await ask(stack, world.tokens["admin"], "how many projects in KSA")
    done = by_event(events)["complete"]
    assert done["rows"] == [[sum(1 for p in oracle.PROJECTS if p["region"] == "KSA")]]


async def test_field_requests_open_the_builder_with_a_draft(stack: httpx.AsyncClient, world: World,
                                                            owner_conn: psycopg.Connection) -> None:
    """US-075: "group platforms into …" in chat drafts the field; nothing is saved and no query runs."""
    before = owner_conn.execute("SELECT count(*) FROM content.personal_fields").fetchone()
    _, events = await ask(stack, world.tokens["creator"], "Group platforms into Core (Network One) and Growth")
    assert [e[1] for e in events] == ["intent", "narrative", "complete"]
    ev = by_event(events)
    assert ev["intent"]["intent"] == "build_field"
    action = ev["complete"]["action"]
    assert action["kind"] == "build_field" and action["draft"]["source_ref"] == "dimension:platform"
    assert action["draft"]["connection_id"] == world.connection_id
    assert action["draft"]["groups"][0] == {"label": "Core", "values": ["Network One"]}
    assert action["missing"] == ["Pick the values for Growth."]
    assert owner_conn.execute("SELECT count(*) FROM content.personal_fields").fetchone() == before

    _, events = await ask(stack, world.tokens["creator"], "band forecast cost into <1M, 1-5M, >5M")
    assert by_event(events)["complete"]["action"]["draft"]["labels"] == ["< 1M", "1M - 5M", "5M+"]
    _, events = await ask(stack, world.tokens["creator"], "group gizmos into A and B")
    assert by_event(events)["error"]["code"] == "unsupported_question"
    _, events = await ask(stack, world.tokens["viewer"], "group platforms into Core and Growth")
    assert by_event(events)["error"]["code"] == "build_not_available"


async def test_link_question_uses_a_semi_join_not_a_same_named_value(stack: httpx.AsyncClient, world: World) -> None:
    """"Open projects with an open high risk" counts open projects that have at least one risk counted by the
    certified High-exposure risks measure — not projects whose own priority is High (US-178 follow-up)."""
    _, events = await ask(stack, world.tokens["admin"], "open projects with an open high risk")
    ev = by_event(events)
    assert ev["plan"]["spec"]["exists_in"] == [{"entity": "risks", "measure": "measure:high_exposure_risks"}]
    assert ev["plan"]["spec"]["filters"] == []
    risky = {r["project_id"] for r in oracle.ACTIVITY["risks"] if r["state"] != "Closed" and r["exposure"] >= 15}
    expected = sum(1 for p in oracle.PROJECTS if p["status"] == "Active" and p["project_id"] in risky)
    assert ev["complete"]["rows"] == [[expected]] and expected > 0


async def test_future_question_says_today_and_offers_the_date_measure(stack: httpx.AsyncClient, world: World) -> None:
    """US-219: a question about the future is answered with today's figure and says so; the measure counting ahead
    of the reporting date is offered and applies as a refinement. A horizon ("next month") plans that measure."""
    from tela_api.ask.tense import NOTE

    token = world.tokens["creator"]
    turn_id, events = await ask(stack, token, "How many open projects will go live")
    ev = by_event(events)
    assert ev["complete"]["spec"]["measure"]["ref"] == "measure:open_projects"
    note = ev["narrative"]["notes"][0]
    assert note["text"] == NOTE and note["fact"] == "future"
    assert note["action"]["ops"] == [{"op": "set_measure", "ref": "measure:go_lives_0_30"}]
    resp = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(token),
                            json={"chip_ops": note["action"]["ops"]})
    assert resp.status_code == 202, resp.text
    assert resp.json()["changes"] == ["Measure: Go-lives in the next 30 days"]
    stream = await stack.get(resp.json()["stream_url"], headers=h(token))
    done = by_event(parse_sse(stream.text))["complete"]
    assert done["spec"]["measure"]["ref"] == "measure:go_lives_0_30" and done["certified"]

    _, events = await ask(stack, token, "go-lives next month")
    ev = by_event(events)
    assert ev["complete"]["spec"]["measure"]["ref"] == "measure:go_lives_0_30"
    assert "time" not in ev["complete"]["spec"]
    assert "Read “next month” as Go-lives in the next 30 days" in ev["complete"]["interpreted"]
    assert all(n["fact"] != "future" for n in ev["narrative"]["notes"])
