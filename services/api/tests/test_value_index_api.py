"""Dimension value index and value linking in Ask (US-173; FEAT-062 / FEAT-014).

Acceptance: the index is refreshed on model approval and nightly (the worker's part played by calling the internal
endpoints with its service token); "Saudi", "UAE" or "Lina" filter on the stored spelling before planning; pack and
clarification synonyms are used only once an Admin approves them; a person is never linked to a value outside their
scope. Linking accuracy on the 50 alias questions is measured offline (``test_value_linking_unit``) and in the eval
run (gold tag ``alias``).
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h
from test_ask_api import ask, by_event, parse_sse
from test_exports_api import worker
from test_knowledge_api import bag, world  # noqa: F401 - the knowledge world (FakeGateway with embeddings)

pytestmark = [needs_db, pytest.mark.integration]
BASE = "/api/v1/internal/knowledge"


async def refresh(api: httpx.AsyncClient, world: World, keys: dict[str, Any]) -> dict[str, Any]:
    resp = await api.post(f"{BASE}/model-versions/{world.version_id}/values", headers=worker(keys),
                          json={"tenant_id": world.tenant_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def synonyms(api: httpx.AsyncClient, world: World, **params: str) -> dict[str, Any]:
    resp = await api.get("/api/v1/knowledge/value-synonyms", headers=h(world.tokens["admin"]),
                         params={"connection_id": world.connection_id, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def approve(api: httpx.AsyncClient, world: World, ids: list[str]) -> None:
    resp = await api.post("/api/v1/knowledge/value-synonyms/review", headers=h(world.tokens["admin"]),
                          json={"ids": ids, "decision": "approve"})
    assert resp.status_code == 200 and resp.json()["updated"] == len(ids), resp.text


async def approve_pack(api: httpx.AsyncClient, world: World) -> None:
    pending = (await synonyms(api, world, status="pending_review"))["items"]
    await approve(api, world, [s["id"] for s in pending if s["source"] == "pack"])


async def planned(api: httpx.AsyncClient, token: str, question: str, **body: Any) -> dict[str, Any]:
    _, events = await ask(api, token, question, **body)
    ev = by_event(events)
    assert "plan" in ev, events
    return ev


def filters(ev: dict[str, Any]) -> list[dict[str, Any]]:
    return ev["plan"]["spec"]["filters"]


async def assume_first(api: httpx.AsyncClient, token: str) -> None:
    resp = await api.put("/api/v1/me/preferences", headers=h(token), json={"ask_verbosity": "assume_first"})
    assert resp.status_code == 200, resp.text


async def test_index_refreshed_on_approval_and_pack_synonyms_wait_for_review(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        signing_keys: dict[str, Any]) -> None:
    out = await refresh(stack, world, signing_keys)
    assert out["status"] == "refreshed" and out["failed"] == [] and out["dimensions"] >= 10
    assert out["values"] > 40 and out["pack_synonyms_added"] >= 50 and out["embedded"] > 0
    rows = dict(owner_conn.execute("SELECT stored_value, normalised FROM rag.dimension_values WHERE dimension = "
                                   "'region'").fetchall())
    assert rows["GCC - UAE"] == "gcc uae" and "KSA" in rows
    # Person names are indexed for linking but never sent to the embedding service; regions are embedded.
    embedded = dict(owner_conn.execute("SELECT dimension, bool_or(embedding IS NOT NULL) FROM rag.dimension_values "
                                       "WHERE dimension IN ('region', 'project_manager', 'cio') GROUP BY dimension"
                                       ).fetchall())
    assert embedded == {"region": True, "project_manager": False, "cio": False}
    # Computed dimensions and dates are not indexed; Yellow is stored, its label Amber links as a synonym.
    dims = {r[0] for r in owner_conn.execute("SELECT DISTINCT dimension FROM rag.dimension_values").fetchall()}
    assert not dims & {"calculated_health", "lateness_bucket", "planned_deployment_date"}
    assert owner_conn.execute("SELECT synonyms FROM rag.dimension_values WHERE dimension = 'reported_status' AND "
                              "stored_value = 'Yellow'").fetchone()[0] == ["amber"]
    # The reads went through the one query path, recorded as the value index's.
    assert owner_conn.execute("SELECT count(*) FROM ops.query_executions WHERE surface = 'value_index'"
                              ).fetchone()[0] >= out["dimensions"]
    assert owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action = 'knowledge.values_indexed'"
                              ).fetchone()[0] == 1

    admin = world.tokens["admin"]
    # Pending pack synonyms are not used: "Saudi Arabia" is not a filter yet.
    ev = await planned(stack, admin, "Open projects in Saudi Arabia")
    assert filters(ev) == []
    listed = await synonyms(stack, world)
    assert listed["counts"] == {"pending_review": out["pack_synonyms_added"]}
    assert listed["index"]["values"] == out["values"]
    ksa = [s for s in listed["items"] if s["stored_value"] == "KSA" and s["dimension"] == "region"]
    assert {s["synonym"] for s in ksa} == {"Saudi", "Saudi Arabia", "Kingdom of Saudi Arabia"}
    assert all(s["in_data"] and s["dimension_label"] == "Region" for s in ksa)
    await approve(stack, world, [s["id"] for s in ksa])
    ev = await planned(stack, admin, "Open projects in Saudi Arabia")
    assert filters(ev) == [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
    assert ev["complete"]["interpreted"] == ["Interpreted “saudi arabia” as Region: KSA"]
    assert ev["complete"]["filters_text"] == "Region: KSA"
    # Approval is audited; a later refresh keeps the review and adds nothing new.
    assert owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action = "
                              "'knowledge.value_synonyms_reviewed'").fetchone()[0] == 1
    again = await refresh(stack, world, signing_keys)
    assert again["pack_synonyms_added"] == 0 and again["embedded"] == 0  # unchanged values keep their vectors
    assert (await synonyms(stack, world))["counts"]["approved"] == 3
    # Spelling slips and parts of a name need no synonym. A slip is a guess: at "balanced" Ask asks (US-217), with the
    # slip read first; "assume first" answers it.
    _, events = await ask(stack, admin, "Open projects in Jordon")
    q = by_event(events)["clarify"]
    assert q["why"] == ["“jordon” was matched to Jordan by spelling"]
    assert q["options"][0]["label"] == "Open projects, Region: Jordan"
    await assume_first(stack, admin)
    ev = await planned(stack, admin, "Open projects where Omar is the project manager in Jordon")
    assert sorted(filters(ev), key=lambda f: f["ref"]) == [
        {"ref": "dimension:project_manager", "op": "=", "values": ["Omar Saleh"]},
        {"ref": "dimension:region", "op": "=", "values": ["Jordan"]}]


async def test_linking_respects_the_persons_scope(stack: httpx.AsyncClient, world: World,
                                                  signing_keys: dict[str, Any]) -> None:
    await refresh(stack, world, signing_keys)
    await approve_pack(stack, world)
    creator = world.tokens["creator"]  # scope: KSA and GCC - UAE only
    ev = await planned(stack, creator, "Open projects in Dubai")
    assert filters(ev) == [{"ref": "dimension:region", "op": "=", "values": ["GCC - UAE"]}]
    # Jordan is outside the Creator's scope: "Amman" is not linked to it (nor is it shown as understood).
    ev = await planned(stack, creator, "Open projects in Amman")
    assert filters(ev) == [] and ev["complete"]["interpreted"] == []
    # A CIO sees only their own name; another CIO's is never linked for them, their own is.
    nomsa = world.tokens["nomsa"]
    assert filters(await planned(stack, nomsa, "How many open projects does Karim have?")) == []
    assert filters(await planned(stack, nomsa, "Open projects for Nomsa")) == [
        {"ref": "dimension:cio", "op": "=", "values": ["Nomsa Osei"]}]
    # The Admin's check over the whole index finds both.
    resp = await stack.post("/api/v1/knowledge/values/link", headers=h(world.tokens["admin"]),
                            json={"connection_id": world.connection_id, "text": "Karim in Amman"})
    assert resp.status_code == 200, resp.text
    assert {(x["dimension"], x["stored"], x["how"]) for x in resp.json()["links"]} >= {
        ("cio", "Karim Haddad", "partial"), ("region", "Jordan", "synonym")}
    denied = await stack.post("/api/v1/knowledge/values/link", headers=h(creator),
                              json={"connection_id": world.connection_id, "text": "Karim"})
    assert denied.status_code == 403


async def test_value_clarification_is_remembered_and_proposed_as_a_synonym(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        signing_keys: dict[str, Any]) -> None:
    await refresh(stack, world, signing_keys)
    admin = world.tokens["admin"]
    turn_id, events = await ask(stack, admin, "Open projects on Network")
    assert events[-1][1] == "clarify"
    q = by_event(events)["clarify"]
    assert q["kind"] == "value" and q["question"] == "By “network” do you mean Platform Network Flex or Network One?"
    flex = next(o for o in q["options"] if o["label"] == "Network Flex")
    resumed = await stack.post(f"/api/v1/ask/turns/{turn_id}/clarification", headers=h(admin),
                               json={"option_id": flex["id"]})
    assert resumed.status_code == 202 and resumed.json()["chosen"] == "Network Flex"
    stream = await stack.get(f"/api/v1/ask/turns/{turn_id}/stream",
                             headers=h(admin, **{"Last-Event-ID": str(max(e[0] for e in events))}))
    later = by_event(parse_sse(stream.text))
    assert filters(later) == [{"ref": "dimension:platform", "op": "=", "values": ["Network Flex"]}]
    # The choice waits for an Admin as a synonym ...
    proposed = owner_conn.execute("SELECT synonym, stored_value, source, status, proposed_by IS NOT NULL FROM "
                                  "rag.value_synonyms WHERE source = 'clarification'").fetchall()
    assert proposed == [("network", "Network Flex", "clarification", "pending_review", True)]
    # ... and the conversation remembers it meanwhile.
    conversation = (await stack.get(f"/api/v1/ask/turns/{turn_id}", headers=h(admin))).json()["conversation_id"]
    ev = await planned(stack, admin, "Open projects on network by region", conversation_id=conversation)
    assert filters(ev) == [{"ref": "dimension:platform", "op": "=", "values": ["Network Flex"]}]
    # Once approved, everyone's "network" means Network Flex.
    pid = next(s["id"] for s in (await synonyms(stack, world))["items"] if s["source"] == "clarification")
    await approve(stack, world, [pid])
    ev = await planned(stack, admin, "Open projects on network")
    assert filters(ev) == [{"ref": "dimension:platform", "op": "=", "values": ["Network Flex"]}]


async def test_admin_synonyms_and_review_rules(stack: httpx.AsyncClient, world: World,
                                              signing_keys: dict[str, Any]) -> None:
    await refresh(stack, world, signing_keys)
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    body = {"connection_id": world.connection_id, "dimension": "portfolio", "stored_value": "Infrastructure",
            "synonym": "Infra"}
    resp = await stack.post("/api/v1/knowledge/value-synonyms", headers=h(admin), json=body)
    assert resp.status_code == 201 and resp.json()["status"] == "approved", resp.text
    ev = await planned(stack, admin, "Open projects in infra")
    assert filters(ev) == [{"ref": "dimension:portfolio", "op": "=", "values": ["Infrastructure"]}]
    bad = await stack.post("/api/v1/knowledge/value-synonyms", headers=h(admin),
                           json={**body, "stored_value": "Moon base"})
    assert bad.status_code == 400 and "not in the value index" in bad.json()["error"]["message"]
    same = await stack.post("/api/v1/knowledge/value-synonyms", headers=h(admin),
                            json={**body, "synonym": "infrastructure"})
    assert same.status_code == 400
    assert (await stack.post("/api/v1/knowledge/value-synonyms", headers=h(creator), json=body)).status_code == 403
    assert (await stack.get("/api/v1/knowledge/value-synonyms", headers=h(creator),
                            params={"connection_id": world.connection_id})).status_code == 403
    # A rejected synonym stops linking.
    sid = next(s["id"] for s in (await synonyms(stack, world, status="approved"))["items"] if s["synonym"] == "Infra")
    resp = await stack.post("/api/v1/knowledge/value-synonyms/review", headers=h(admin),
                            json={"ids": [sid], "decision": "reject", "note": "Too vague"})
    assert resp.status_code == 200
    assert filters(await planned(stack, admin, "Open projects in infra")) == []


async def test_meaning_links_unknown_words_and_nightly_refresh(stack: httpx.AsyncClient, world: World,
                                                               owner_conn: psycopg.Connection,
                                                               signing_keys: dict[str, Any]) -> None:
    levant = [0.0] * 1024
    levant[7] = 1.0

    def embedder(text: str) -> list[float]:
        # "the Levant" and "Region: Jordan" mean the same; everything else is a bag of words.
        if "levant" in text.lower() or text == "Region: Jordan":
            return levant
        return bag(text)

    world.extra["gateway"].embedder = embedder
    out = await refresh(stack, world, signing_keys)
    assert out["embedded"] > 0
    await assume_first(stack, world.tokens["admin"])  # a meaning link asks at "balanced" (US-217)
    ev = await planned(stack, world.tokens["admin"], "Open projects in the Levant")
    assert filters(ev) == [{"ref": "dimension:region", "op": "=", "values": ["Jordan"]}]
    assert ev["complete"]["interpreted"] == ["Interpreted “levant” as Region: Jordan"]
    # Below the similarity floor nothing links.
    half = [0.0] * 1024
    half[7], half[8] = 1.0, 1.0
    world.extra["gateway"].embedder = lambda t: [x / math.sqrt(2) for x in half] if "levant" in t.lower() \
        else embedder(t)
    assert filters(await planned(stack, world.tokens["admin"], "Open projects in the Levant")) == []

    # Nightly: every current version is refreshed; a value that left the data leaves the index.
    owner_conn.execute("UPDATE rag.dimension_values SET stored_value = 'Atlantis', normalised = 'atlantis' WHERE "
                       "dimension = 'region' AND stored_value = 'DPO'")
    resp = await stack.post(f"{BASE}/values/nightly", headers=worker(signing_keys))
    assert resp.status_code == 200 and resp.json() == {"tenants": 1, "refreshed": 1}
    regions = {r[0] for r in owner_conn.execute("SELECT stored_value FROM rag.dimension_values WHERE dimension = "
                                                "'region'").fetchall()}
    assert "Atlantis" not in regions and "DPO" in regions


async def test_pending_pack_synonyms_are_shown_and_approved_together(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        signing_keys: dict[str, Any]) -> None:
    """US-215: the Admin overview says how many pack synonyms wait for review, and one call approves them all."""
    out = await refresh(stack, world, signing_keys)
    assert out["free_text"] == []  # the fixture's fields are categories (US-214)
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    overview = (await stack.get("/api/v1/admin/overview", headers=h(admin))).json()["value_synonyms"]
    assert overview["pack_pending"] == overview["pending"] == out["pack_synonyms_added"] >= 50
    assert overview["connections"][0]["id"] == world.connection_id
    assert filters(await planned(stack, admin, "Open projects in Saudi")) == []
    body = {"connection_id": world.connection_id}
    assert (await stack.post("/api/v1/knowledge/value-synonyms/approve-pack", headers=h(creator),
                             json=body)).status_code == 403
    resp = await stack.post("/api/v1/knowledge/value-synonyms/approve-pack", headers=h(admin), json=body)
    assert resp.status_code == 200 and resp.json()["updated"] == out["pack_synonyms_added"], resp.text
    assert (await synonyms(stack, world))["counts"] == {"approved": out["pack_synonyms_added"]}
    assert filters(await planned(stack, admin, "Open projects in Saudi")) == [
        {"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = "
                             "'knowledge.value_synonyms_reviewed'").fetchone()
    assert row[0]["source"] == "pack" and row[0]["count"] == out["pack_synonyms_added"]
    assert (await stack.get("/api/v1/admin/overview", headers=h(admin))).json()["value_synonyms"]["pending"] == 0
    again = await stack.post("/api/v1/knowledge/value-synonyms/approve-pack", headers=h(admin), json=body)
    assert again.json()["updated"] == 0


async def test_a_filter_that_empties_the_answer_is_left_out_and_asked(
        stack: httpx.AsyncClient, world: World, signing_keys: dict[str, Any]) -> None:
    """US-218: "Lina" links Lina Farouk by part of her name; no open project in KSA has her as delivery manager, so
    the count is asked without that filter (11), the filtered reading kept on offer; "assume first" answers 11, not
    certified. A true zero on exact filters still answers 0."""
    await refresh(stack, world, signing_keys)
    admin = world.tokens["admin"]
    question = "Open projects in KSA for Lina"
    _, events = await ask(stack, admin, question)
    assert events[-1][1] == "clarify", events
    q = by_event(events)["clarify"]
    assert q["kind"] == "reading" and q["why"][0] == ("Nothing matches with Delivery manager: Lina Farouk; without "
                                                      "it open projects is 11.")
    labels = [o["label"] for o in q["options"]]
    assert labels[0] == "Open projects, Region: KSA" and "Open projects, Region: KSA, Delivery manager: Lina Farouk" \
        in labels and any("Project manager: Lina Farouk" in label for label in labels), labels

    await assume_first(stack, admin)
    ev = await planned(stack, admin, question)
    assert filters(ev) == [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]
    assert ev["narrative"]["facts"]["value"] == 11
    assert ev["complete"]["certified"] is False and ev["complete"]["badge"] == "filter_left_out"
    assert "Left out Delivery manager: Lina Farouk: with it nothing matched" in ev["complete"]["interpreted"]
    assert ev["complete"]["confidence"]["signals"]["zero_result"]["numbers"] == {"filtered": 0, "without": 11}

    zero = await planned(stack, admin, "Red open projects in KSA")  # every filter found exactly: a true zero
    assert zero["complete"]["certified"] is True and zero["narrative"]["facts"]["value"] == 0
    assert "zero_result" not in zero["complete"]["confidence"]["signals"]
