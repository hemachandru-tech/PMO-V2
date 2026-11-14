"""The AI pack author (ENH-025, US-237; AG-04 pack mode), on the TPG fixture.

An Admin creates an *AI draft* pack on the TPG Postgres source with PMO as the template and drafts it stage by
stage. With no model, every stage proposes by rules (entities, links, measures, dimensions, synonyms, glossary), each
proposal valid and counted at the source, and nothing reaches the draft until accepted. With a (fake) model, every
stage yields valid items, an invented filter value is left out with the reason, a term naming a missing measure is
left out, an answer that stays invalid keeps the rules draft, and with DEC-27 off no value of the source is sent;
with DEC-27 on (send samples, local provider) values are sent and kept in the run log.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, load_packs, setup_world

pytestmark = [needs_db, pytest.mark.integration]
# Values only the TPG source holds (portfolio, platform, lifecycle phase, hold reason, a CIO): never in a prompt
# while DEC-27 is off.
SOURCE_ONLY = ("Acquiring", "Network Flex", "Execution", "Awaiting funding", "Nomsa Osei", "Inactive")


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    return w


async def _stream(api: httpx.AsyncClient, token: str, pack_id: str, **body: Any) -> list[tuple[str, Any]]:
    resp = await api.post(f"/api/v1/packs/{pack_id}/author", headers=h(token), json=body)
    assert resp.status_code == 200, resp.text
    out = []
    for block in resp.text.split("\n\n"):
        m = re.match(r"event: (\w+)\ndata: (.*)", block, re.S)
        if m:
            out.append((m.group(1), json.loads(m.group(2))))
    assert not [d for e, d in out if e == "error"], out
    return out


def _of(events: list[tuple[str, Any]], name: str) -> list[dict[str, Any]]:
    return [d for e, d in events if e == name]


async def _pack(api: httpx.AsyncClient, token: str, pack_id: str) -> tuple[dict[str, Any], str]:
    resp = await api.get(f"/api/v1/packs/{pack_id}", headers=h(token))
    assert resp.status_code == 200, resp.text
    return resp.json(), resp.headers["ETag"]


async def _accept(api: httpx.AsyncClient, token: str, pack_id: str, cid: str, kind: str,
                  proposals: list[dict[str, Any]], etag: str | None = None) -> httpx.Response:
    headers = {**h(token), **({"If-Match": etag} if etag else {})}
    return await api.post(f"/api/v1/packs/{pack_id}/author/accept", headers=headers, json={
        "connection_id": cid, "kind": kind, "items": [p["item"] for p in proposals],
        "sources": [p["source"] for p in proposals]})


async def _create(api: httpx.AsyncClient, admin: str, cid: str, key: str = "acme_ai") -> str:
    resp = await api.post("/api/v1/packs", headers=h(admin), json={
        "method": "ai", "pack_key": key, "title": "Acme AI pack", "options": {"connection_id": cid}})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"]["connection_id"] == cid and body["source"]["template_pack_id"]
    return str(body["id"])


async def _entities_and_links(api: httpx.AsyncClient, admin: str, pack_id: str, cid: str) -> None:
    ents = _of(await _stream(api, admin, pack_id, connection_id=cid, stage="entities"), "proposal")
    _, etag = await _pack(api, admin, pack_id)
    assert (await _accept(api, admin, pack_id, cid, "entity", ents, etag)).status_code == 200
    links = _of(await _stream(api, admin, pack_id, connection_id=cid, stage="links"), "proposal")
    _, etag = await _pack(api, admin, pack_id)
    assert (await _accept(api, admin, pack_id, cid, "link", links, etag)).status_code == 200


async def test_rules_only_every_stage_valid_and_nothing_written_until_accepted(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, world: World,
) -> None:
    admin, cid = world.tokens["admin"], world.connection_id
    stack.app.state.gateway = FakeGateway().client()  # type: ignore[attr-defined]  # no provider: rules only
    assert (await stack.post("/api/v1/packs", headers=h(admin), json={
        "method": "ai", "pack_key": "acme_x", "title": "X", "options": {}})).status_code == 400
    pack_id = await _create(stack, admin, cid)
    over = (await stack.get(f"/api/v1/packs/{pack_id}/author?connection_id={cid}", headers=h(admin))).json()
    assert over["ai"] is False and over["samples"] is False
    assert any(t["pack_key"] == "pmo_tpg" for t in over["templates"]) and len(over["tables"]) == 10
    assert not any(t["in_draft"] for t in over["tables"])
    before, etag = await _pack(stack, admin, pack_id)

    # 1. Entities: one per table, by rules, counted at the source; nothing written.
    events = await _stream(stack, admin, pack_id, connection_id=cid, stage="entities")
    assert _of(events, "stage")[0]["ai"] is False
    ents = _of(events, "proposal")
    assert len(ents) == 10 and not _of(events, "dropped") and {p["source"] for p in ents} == {"rules"}
    projects = next(p for p in ents if p["key"] == "projects")
    assert projects["checks"]["count"] == 100 and projects["checks"]["count_of"] == "rows"
    assert projects["item"]["object_candidates"] == ["projects"] and projects["item"]["primary_key"] == ["project_id"]
    assert next(f for f in projects["item"]["fields"] if f["key"] == "cio")["sensitivity"] == "personal"
    after, same = await _pack(stack, admin, pack_id)
    assert same == etag and after["counts"]["entities"] == 0

    # Accept needs the revision; then the draft holds them and the widest table is the primary entity.
    assert (await _accept(stack, admin, pack_id, cid, "entity", ents)).status_code == 412
    out = await _accept(stack, admin, pack_id, cid, "entity", ents, etag)
    assert out.status_code == 200, out.text
    assert out.json()["accepted"] == 10 and out.json()["manifest"]["signature"]["primary_entity"] == "projects"
    again = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="entities"), "note")
    assert again[0]["message"].startswith("Every table")

    # 2. Links: the foreign key, then the project_id names; each key checked unique by two counts.
    links = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="links"), "proposal")
    pairs = {(p["item"]["from_entity"], p["item"]["to_entity"]) for p in links}
    assert ("projects", "project_requests") in pairs and ("risks", "projects") in pairs and len(links) >= 8
    assert all("is unique (two counts)" in p["reason"] for p in links)
    _, etag = await _pack(stack, admin, pack_id)
    assert (await _accept(stack, admin, pack_id, cid, "link", links, etag)).status_code == 200

    # 3. Measures of projects: counts by the profile's status values, money, dates; each counted.
    events = await _stream(stack, admin, pack_id, connection_id=cid, stage="measures", entities=["projects"])
    measures = {p["key"]: p for p in _of(events, "proposal")}
    assert len(measures) <= 20 and not _of(events, "dropped")
    assert measures["projects_count"]["checks"]["count"] == 100
    assert measures["projects_active"]["checks"]["count"] == 65  # 65 open (status Active)
    assert measures["projects_active"]["item"]["expression"]["where"] == {"field": "status", "op": "=",
                                                                          "value": "Active"}
    assert any(k.endswith("_overdue") for k in measures) and any(k.endswith("_total") for k in measures)
    _, etag = await _pack(stack, admin, pack_id)
    out = await _accept(stack, admin, pack_id, cid, "measure", list(measures.values()), etag)
    assert out.status_code == 200, out.text

    # An invented filter value is refused on check and on accept, however it is sent.
    bad = {"key": "paused_projects", "label": "Paused projects", "entity": "projects", "plain_rule": "Paused ones.",
           "expression": {"agg": "count", "where": {"field": "status", "op": "=", "value": "Paused"}}}
    checked = (await stack.post(f"/api/v1/packs/{pack_id}/author/check", headers=h(admin), json={
        "connection_id": cid, "kind": "measure", "items": [bad]})).json()
    assert checked["valid"] is False
    assert checked["items"][0]["errors"][0]["message"].startswith("'Paused' is not a value of Status: it has Active")
    _, etag = await _pack(stack, admin, pack_id)
    refused = await _accept(stack, admin, pack_id, cid, "measure", [{"item": bad, "source": "ai"}], etag)
    assert refused.status_code == 400 and refused.json()["error"]["details"]["items"][0]["index"] == 0

    # 4. Dimensions, 5. synonyms, 6. glossary: by rules, valid, accepted.
    dims = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="dimensions", entities=["projects"]),
               "proposal")
    keys = {p["key"] for p in dims}
    assert {"region", "status"} <= keys and any(p["item"].get("answers") == ["how_late"] for p in dims)
    assert next(p for p in dims if p["key"] == "region")["checks"] == {
        "errors": [], "warnings": [], "count": 6, "count_of": "values"}
    assert "cio" not in keys  # a personal field is no breakdown
    _, etag = await _pack(stack, admin, pack_id)
    assert (await _accept(stack, admin, pack_id, cid, "dimension", dims, etag)).status_code == 200
    syn = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="synonyms"), "proposal")
    assert next(p for p in syn if p["key"] == "measure:projects_count")["item"]["synonyms"] == [
        "how many projects", "count of projects"]
    _, etag = await _pack(stack, admin, pack_id)
    assert (await _accept(stack, admin, pack_id, cid, "synonyms", syn, etag)).status_code == 200
    gl = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="knowledge", kind="glossary"), "proposal")
    assert gl and all(p["item"]["reading"] in measures for p in gl)
    assert (await _accept(stack, admin, pack_id, cid, "glossary", gl[:3])).status_code == 200
    note = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="knowledge", kind="dashboard_rules"),
               "note")
    assert note[0]["message"].startswith("Rules can't draft this kind")

    pack, _ = await _pack(stack, admin, pack_id)
    assert pack["checks"]["errors"] == [] and pack["counts"]["measures"] == len(measures)
    assert next(m for m in pack["manifest"]["measures"] if m["key"] == "projects_count")["synonyms"]
    doc = (await stack.get(f"/api/v1/packs/{pack_id}/knowledge/glossary", headers=h(admin))).json()
    assert len(doc["document"]["terms"]) == 3 and doc["checks"]["errors"] == []
    actions = [r[0] for r in owner_conn.execute(
        "SELECT action FROM ops.audit_events WHERE action LIKE 'pack.ai_%' ORDER BY occurred_at").fetchall()]
    assert actions.count("pack.ai_drafted") == 8 and actions.count("pack.ai_accepted") == 6
    # Only packs.author may draft, and a system pack can't be drafted into.
    creator = world.tokens["creator"]
    assert (await stack.post(f"/api/v1/packs/{pack_id}/author", headers=h(creator), json={
        "connection_id": cid, "stage": "entities"})).status_code == 403
    pmo = next(t for t in over["templates"] if t["pack_key"] == "pmo_tpg")["id"]
    assert (await stack.post(f"/api/v1/packs/{pmo}/author", headers=h(admin), json={
        "connection_id": cid, "stage": "entities"})).status_code == 409
    assert before["counts"]["entities"] == 0


def _drafted(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The rules draft at the end of a pack author prompt."""
    text = messages[1]["content"].split("Tela's rules draft:\n", 1)[1]
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


async def test_ai_every_stage_valid_invented_values_rejected_and_no_values_sent(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, world: World,
) -> None:
    admin, cid = world.tokens["admin"], world.connection_id
    fail_dimensions = {"on": False}

    def reply(task: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        assert task == "pack_author"
        user = messages[1]["content"]
        if user.startswith("Stage 1"):
            return {"tables": [{"table": "projects", "label": "Project", "grain": "One row per project",
                                "fields": [{"column": "cio", "label": "CIO", "sensitivity": "public"},
                                           {"column": "approved_budget_value_usd", "label": "Approved budget",
                                            "description": "Approved budget in USD.", "semantic_type": "currency",
                                            "sensitivity": "confidential"},
                                           {"column": "no_such_column", "label": "Ghost"}]},
                               {"table": "no_such_table", "label": "Ghost"}]}
        if user.startswith("Stage 3"):
            kept = [{k: v for k, v in m.items() if k != "entity"} for m in _drafted(messages)][:3]
            return {"measures": [*kept,
                                 {"key": "paused_projects", "label": "Paused projects", "plain_rule": "Paused.",
                                  "expression": {"agg": "count", "where": {"field": "status", "op": "=",
                                                                           "value": "Paused"}}},
                                 {"key": "active_lower", "label": "Active (any case)", "plain_rule": "Active.",
                                  "expression": {"agg": "count", "where": {"field": "status", "op": "=",
                                                                           "value": "active"}}},
                                 {"key": "ghost_total", "label": "Ghost", "plain_rule": "Sum of a ghost.",
                                  "expression": {"agg": "sum", "field": "ghost"}}]}
        if user.startswith("Stage 4"):
            if fail_dimensions["on"]:
                return {"__invalid__": "{\"dimensions\": 3}"}
            return {"dimensions": [{k: v for k, v in d.items() if k != "entity"} for d in _drafted(messages)][:2]}
        if user.startswith("Stage 5"):
            return {"items": [{"target": "measure:projects_count", "synonyms": ["project total", "Project total"]},
                              {"target": "measure:nope", "synonyms": ["x"]}]}
        if user.startswith("Stage 6"):
            return {"items": [{"term": "EAC", "means": "estimate at completion", "reading": "projects_count"},
                              {"term": "PCR", "means": "project change request", "reading": "no_such_measure"}],
                    "reasons": ["A PMO abbreviation.", "Another."]}
        raise AssertionError(user[:80])

    fake = FakeGateway({"AG-04": reply}, default_provider="ollama")
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    pack_id = await _create(stack, admin, cid, "acme_ai_llm")

    events = await _stream(stack, admin, pack_id, connection_id=cid, stage="entities", tables=["public.projects"])
    assert _of(events, "stage")[0]["ai"] is True and _of(events, "stage")[0]["samples"] is False
    [proj] = _of(events, "proposal")
    assert proj["source"] == "ai" and proj["item"]["label"] == "Project"
    fields = {f["key"]: f for f in proj["item"]["fields"]}
    assert fields["cio"]["sensitivity"] == "personal"  # never lowered by the model
    assert fields["approved_budget_value_usd"]["sensitivity"] == "confidential"
    assert fields["approved_budget_value_usd"]["description"] == "Approved budget in USD."
    assert "no_such_column" not in json.dumps(proj["item"])
    _, etag = await _pack(stack, admin, pack_id)
    assert (await _accept(stack, admin, pack_id, cid, "entity", [proj], etag)).status_code == 200

    events = await _stream(stack, admin, pack_id, connection_id=cid, stage="measures")
    kept = {p["key"]: p for p in _of(events, "proposal")}
    dropped = {p["key"]: p for p in _of(events, "dropped")}
    assert len(kept) == 4 and {p["source"] for p in kept.values()} == {"ai"}
    assert kept["projects_active"]["item"]["expression"]["where"]["value"] == "Active"  # token filled by Tela
    assert kept["active_lower"]["item"]["expression"]["where"]["value"] == "Active"
    assert kept["active_lower"]["checks"]["warnings"][0]["message"] == "'active' is written 'Active' in the data."
    assert dropped["paused_projects"]["checks"]["errors"][0]["message"].startswith("'Paused' is not a value of")
    assert "ghost" in dropped["ghost_total"]["checks"]["errors"][0]["message"]
    m_prompt = next(c for c in fake.calls if c["messages"][1]["content"].startswith("Stage 3"))
    assert "[[status:1]]" in m_prompt["messages"][1]["content"]
    _, etag = await _pack(stack, admin, pack_id)
    assert (await _accept(stack, admin, pack_id, cid, "measure", list(kept.values()), etag)).status_code == 200

    dims = _of(await _stream(stack, admin, pack_id, connection_id=cid, stage="dimensions"), "proposal")
    assert len(dims) == 2 and {p["source"] for p in dims} == {"ai"}
    _, etag = await _pack(stack, admin, pack_id)
    assert (await _accept(stack, admin, pack_id, cid, "dimension", dims, etag)).status_code == 200
    syn = await _stream(stack, admin, pack_id, connection_id=cid, stage="synonyms")
    assert [p["item"]["synonyms"] for p in _of(syn, "proposal")] == [["project total", "Project total"]]
    assert _of(syn, "dropped")[0]["checks"]["errors"][0]["path"] == "target"
    gl = await _stream(stack, admin, pack_id, connection_id=cid, stage="knowledge", kind="glossary")
    assert [p["key"] for p in _of(gl, "proposal")] == ["EAC"] and _of(gl, "proposal")[0]["reason"]
    assert _of(gl, "dropped")[0]["checks"]["errors"][0]["message"] == (
        "There is no measure 'no_such_measure' in this pack.")

    # DEC-27 off: only names and types went out.
    # DEC-27 off: the stages that read the source's values (entities to dimensions) sent names and types only;
    # synonyms and knowledge are about the pack's own names, which the Admin accepted (as AG-03 sends them).
    sent = "\n".join(m["content"] for c in fake.calls for m in c["messages"]
                     if c["messages"][1]["content"].startswith(("Stage 1", "Stage 3", "Stage 4")))
    assert len(sent) > 5000
    assert not [v for v in SOURCE_ONLY if v in sent]

    # A reply that stays invalid keeps the rules draft.
    fail_dimensions["on"] = True
    _, etag = await _pack(stack, admin, pack_id)
    events = await _stream(stack, admin, pack_id, connection_id=cid, stage="dimensions")
    assert "the rules draft stands" in _of(events, "note")[0]["message"]
    assert {p["source"] for p in _of(events, "proposal")} == {"rules"}

    # DEC-27 on (send samples, local provider): values go out, and the run log keeps them.
    owner_conn.execute("INSERT INTO ops.llm_settings (tenant_id, provider, allow_samples) VALUES (%s, 'ollama', true)",
                       (world.tenant_id,))
    fake.calls.clear()
    events = await _stream(stack, admin, pack_id, connection_id=cid, stage="measures")
    assert _of(events, "stage")[0]["samples"] is True
    prompt = next(c for c in fake.calls if c["messages"][1]["content"].startswith("Stage 3"))["messages"][1]["content"]
    assert "Acquiring" in prompt and "[[status:" not in prompt
    logged = owner_conn.execute("SELECT s.detail FROM ops.agent_steps s JOIN ops.agent_runs r ON r.id = s.run_id "
                                "WHERE s.kind = 'retrieve' AND r.title = 'Pack author: measures' ").fetchall()
    assert logged and "Acquiring" in logged[-1][0]["values_sent"]["projects.portfolio"]
