"""Business definitions from knowledge (ENH-017, US-203; FEAT-067 / FEAT-014 / FEAT-062), live.

Acceptance: indexing an uploaded Markdown document proposes its definitions (term, meaning, a rule in the measure
expression format where the model has the field and values), linked to the passage; nothing applies until an Admin
approves (the review gate, after the document's own); a definition naming a field or value the model lacks is text
only, with the gap; an approved definition that disagrees with the certified measure of the same term is listed
with both figures (EXEC-115: Open projects by record status vs current state in Project Created, Active, On Hold);
approved definitions are always in AG-03's context for questions that use the term; a new model version carries
them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import SOURCE_DB, FakeGateway, _dsn_for
from pmo_testkit import World, h, open_by_status_draft, setup_world
from test_ask_api import ask, by_event
from test_knowledge_api import bag, index_model, run_worker, upload, with_knowledge

pytestmark = [needs_db, pytest.mark.integration]
Q = "/api/v1/knowledge/definitions"
OPEN_STATES = ("Project Created", "Active", "On Hold")
DOC = b"""# PMO definitions

## Open project
An open project is a project whose current state is Project Created, Active or On Hold.

## Gated project
A gated project is a project whose stage gate is G3 or G4.

## Terms
- Paused project: current state On Hold or Parked.
- Quantum widget: projects with current state On Hold.
- Go-live means the deployment of a project to production.
"""
MISS = "zorbly count of the quantum widgets"  # names nothing the rules or linking know: AG-03 plans it
INACTIVE = 3  # Active projects whose record is Inactive (EXEC-115 had 11)


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Any,
                tpg_source: dict[str, Any]) -> World:
    """The knowledge world with open = Status Active, as pack 2.1.0 had RULE-026 (DEC-24 moved it to the current
    state): the certified rule these definitions disagree with."""
    return with_knowledge(stack, await setup_world(stack, owner_conn, make_token, tpg_source,
                                                   before_approve=open_by_status_draft(owner_conn)))


@pytest.fixture
def record_inactive() -> Iterator[list[str]]:
    """Three projects in current state Active whose record status is Inactive, as on TPG MySQL; put back after."""
    with psycopg.connect(_dsn_for(SOURCE_DB), autocommit=True) as src:
        ids = [r[0] for r in src.execute(
            "SELECT project_id FROM projects WHERE current_state = 'Active' AND status = 'Active' ORDER BY "
            "project_id LIMIT %s", (INACTIVE,)).fetchall()]
        src.execute("UPDATE projects SET status = 'Inactive' WHERE project_id = ANY(%s)", (ids,))
        try:
            yield ids
        finally:
            src.execute("UPDATE projects SET status = 'Active' WHERE project_id = ANY(%s)", (ids,))


def source_count(where: str, params: tuple[Any, ...] = ()) -> int:
    with psycopg.connect(_dsn_for(SOURCE_DB)) as src:
        return int(src.execute(f"SELECT count(*) FROM projects WHERE {where}", params).fetchone()[0])  # type: ignore[index]


async def listing(api: httpx.AsyncClient, token: str, **params: Any) -> dict[str, Any]:
    resp = await api.get(Q, headers=h(token), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def by_term(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {d["term"]: d for d in items}


async def review(api: httpx.AsyncClient, token: str, definition_id: str, **body: Any) -> httpx.Response:
    return await api.post(f"{Q}/{definition_id}/review", headers=h(token), json=body)


async def indexed_doc(api: httpx.AsyncClient, world: World, keys: dict[str, Any], *, approve: bool,
                      kind: str = "glossary") -> dict[str, Any]:
    admin = world.tokens["admin"]
    await index_model(api, world, keys)
    doc = (await upload(api, admin, "pmo-definitions.md", DOC, source_kind=kind,
                        connection_id=world.connection_id)).json()
    out = await run_worker(api, world, keys, doc["id"])
    assert out["status"] == "indexed" and out["definitions"] == 4, out
    if approve:
        ok = await api.post(f"/api/v1/knowledge/documents/{doc['id']}/review", headers=h(admin),
                            json={"decision": "approve"})
        assert ok.status_code == 200, ok.text
    return doc


async def test_definitions_proposed_on_indexing_and_held_for_review(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection, signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    doc = await indexed_doc(stack, world, signing_keys, approve=False)
    # The uploader is told definitions were proposed.
    note = owner_conn.execute("SELECT body FROM ops.notifications WHERE kind = 'knowledge.indexed'").fetchone()
    assert note is not None and "defines 4 business terms" in note[0]

    assert (await stack.get(Q, headers=h(world.tokens["creator"]))).status_code == 403  # Admins only
    data = await listing(stack, admin)
    assert data["counts"] == {"pending": 4, "approved": 0, "rejected": 0, "conflicts": 0, "text_only": 1}
    items = by_term(data["items"])
    assert set(items) == {"Open project", "Gated project", "Paused project", "Quantum widget"}  # "Go-live" names nothing on the model
    open_ = items["Open project"]
    assert open_["status"] == "pending" and open_["expressible"] and open_["gaps"] == []
    assert open_["rule"] == {"entity": "projects", "field": "current_state", "op": "in", "values": list(OPEN_STATES)}
    assert open_["rule_text"] == "Projects where Current state is one of Project Created, Active, On Hold"
    assert open_["measure"] == {"ref": "measure:open_projects", "label": "Open projects"}
    assert open_["connection"]["id"] == world.connection_id and open_["model_version"]["id"] == world.version_id
    # Linked to its passage.
    assert open_["document"] == {"id": doc["id"], "title": "pmo definitions", "status": "pending_review"}
    assert open_["passage"]["heading"] == "Open project" and "current state is Project Created" in \
        open_["passage"]["text"]
    chunk = owner_conn.execute("SELECT content FROM rag.knowledge_chunks WHERE id = %s",
                               (open_["passage"]["chunk_id"],)).fetchone()
    assert chunk is not None and "An open project is" in chunk[0]
    # Text only: the model has no stage gate.
    gated = items["Gated project"]
    assert gated["rule"] is None and not gated["expressible"]
    assert gated["gaps"] == ["The model has no field “stage gate” on Projects."]

    # The review gate: not before the document is approved; nothing in AG-03's context meanwhile.
    early = await review(stack, admin, open_["id"], decision="approve")
    assert early.status_code == 409 and "document" in early.text
    assert (await review(stack, world.tokens["creator"], open_["id"], decision="approve")).status_code == 403
    rows = owner_conn.execute("SELECT count(*) FROM rag.knowledge_definitions WHERE status = 'approved'").fetchone()
    assert rows is not None and rows[0] == 0

    # Re-indexing keeps the proposals (one per term), no duplicates.
    await run_worker(stack, world, signing_keys, doc["id"])
    assert (await listing(stack, admin))["counts"]["pending"] == 4

    # Reject one; it can't be rejected twice.
    no = await review(stack, admin, items["Paused project"]["id"], decision="reject", note="Not ours")
    assert no.status_code == 200 and no.json()["status"] == "rejected" and no.json()["review_note"] == "Not ours"
    assert (await review(stack, admin, items["Paused project"]["id"], decision="reject")).status_code == 409


async def test_conflict_with_open_projects_listed_with_both_figures(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
        record_inactive: list[str]) -> None:
    """EXEC-115: Open projects counts record status Active; the document defines open by current state."""
    admin = world.tokens["admin"]
    await indexed_doc(stack, world, signing_keys, approve=True)
    items = by_term((await listing(stack, admin))["items"])
    certified = source_count("status = 'Active'")
    defined = source_count("current_state = ANY(%s)", (list(OPEN_STATES),))
    assert defined - certified == INACTIVE

    ok = await review(stack, admin, items["Open project"]["id"], decision="approve", note="PMO handbook wording")
    assert ok.status_code == 200, ok.text
    got = ok.json()
    assert got["status"] == "approved" and got["reviewed_by"]["display_name"] == "Ada Admin" and got["conflict"]
    checks = got["checks"]
    assert checks["figure"] == defined and checks["measure"] == {
        "ref": "measure:open_projects", "label": "Open projects", "rule": "Status is Active", "figure": certified}
    assert checks["same_rule"] is False and checks["conflict"] is True
    assert checks["values"] == {s: source_count("current_state = %s", (s,)) for s in OPEN_STATES}
    # Listed for the Admin, not resolved: the measure is unchanged.
    listed = await listing(stack, admin, conflicts="true")
    assert [d["term"] for d in listed["items"]] == ["Open project"] and listed["counts"]["conflicts"] == 1
    measure = owner_conn.execute(
        "SELECT definition FROM model.model_versions WHERE id = %s", (world.version_id,)).fetchone()
    assert measure is not None and '"current_state"' not in str(next(
        m for m in measure[0]["measures"] if m["key"] == "open_projects"))
    audit = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'knowledge.definition_approved'"
                               ).fetchone()
    assert audit is not None and audit[0]["conflict"] is True and audit[0]["measure"] == "measure:open_projects"

    # Counting again gives the same figures; a value no row has makes it text only.
    again = await stack.post(f"{Q}/{got['id']}/check", headers=h(admin))
    assert again.status_code == 200 and again.json()["checks"]["figure"] == defined
    paused = items["Paused project"]
    assert paused["expressible"] and paused["rule"]["values"] == ["On Hold", "Parked"]
    text_only = await review(stack, admin, paused["id"], decision="approve")
    assert text_only.status_code == 200, text_only.text
    assert not text_only.json()["expressible"] and text_only.json()["gaps"] == ["No rows have Current state “Parked”."]
    # An Admin's edited rule is checked on the model.
    gated = items["Gated project"]
    bad = await review(stack, admin, gated["id"], decision="approve",
                       rule={"field": "stage_gate", "values": ["G3"]})
    assert bad.status_code == 400 and "stage gate" in bad.text
    fixed = await review(stack, admin, gated["id"], decision="approve",
                         rule={"field": "lifecycle_phase", "values": ["UAT", "Deployment"]})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["expressible"] and fixed.json()["rule"]["field"] == "lifecycle_phase"
    retired = await review(stack, admin, fixed.json()["id"], decision="retire")
    assert retired.status_code == 200 and retired.json()["status"] == "retired"


async def test_approved_definition_always_in_ag03_context(stack: httpx.AsyncClient, world: World,
                                                          signing_keys: dict[str, Any]) -> None:
    """A term the model has no measure or word for: the rules planner leaves the question to AG-03, which reads the
    approved definition in its own section, ahead of the budgeted knowledge. (An uploaded document: the rewriter
    reads glossary terms itself, US-175.)"""
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    await indexed_doc(stack, world, signing_keys, approve=True, kind="uploaded_doc")
    widget = by_term((await listing(stack, admin))["items"])["Quantum widget"]
    assert widget["rule"] == {"entity": "projects", "field": "current_state", "op": "in", "values": ["On Hold"]}

    async def system_prompt(question: str) -> str:
        fake = FakeGateway({"AG-03": {"measure": "measure:on_hold", "breakdowns": ["dimension:region"]}},
                           embedder=bag)
        stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
        _, events = await ask(stack, creator, question)  # AG-03 plans it (the answer may then ask, US-193)
        assert by_event(events).get("complete") or by_event(events).get("clarify"), question
        return next(c["messages"][0]["content"] for c in fake.calls
                    if c["agent"] == "AG-03" and "Candidates follow" in c["messages"][0]["content"])

    assert "## Business definitions" not in await system_prompt(MISS)  # pending
    ok = await review(stack, admin, widget["id"], decision="approve")
    assert ok.status_code == 200 and ok.json()["expressible"], ok.text
    system = await system_prompt(MISS)
    knowledge = system[system.index("Approved knowledge follows."):]
    section = knowledge[knowledge.index("## Business definitions"):]
    section = section[:section.index("\n## ")]
    assert "Quantum widget means: projects with current state On Hold" in section
    assert "Rule: count Projects where Current state is On Hold." in section
    assert f'id="chunk:{widget["passage"]["chunk_id"]}"' in section  # cited by its passage
    # A question that doesn't use the term doesn't get it.
    assert "## Business definitions" not in await system_prompt("where are we slipping by region and CIO")


async def test_new_model_version_carries_definitions(stack: httpx.AsyncClient, world: World,
                                                     owner_conn: psycopg.Connection,
                                                     signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    await indexed_doc(stack, world, signing_keys, approve=True)
    items = by_term((await listing(stack, admin))["items"])
    assert (await review(stack, admin, items["Open project"]["id"], decision="approve")).status_code == 200
    # A second approved version of the model (same definition), indexed as the worker does on approval.
    old = world.version_id
    new = owner_conn.execute(
        "INSERT INTO model.model_versions (id, tenant_id, data_model_id, version_no, status, definition, approved_by, "
        "approved_at, created_by) SELECT gen_random_uuid(), tenant_id, data_model_id, version_no + 1, 'approved', "
        "definition, approved_by, now(), created_by FROM model.model_versions WHERE id = %s RETURNING id",
        (old,)).fetchone()
    assert new is not None
    owner_conn.execute("UPDATE model.data_models SET current_version_id = %s WHERE id = %s", (new[0], world.model_id))
    world.version_id = str(new[0])
    out = await index_model(stack, world, signing_keys)
    assert out["definitions_carried"] == 4
    data = await listing(stack, admin)
    carried = by_term(data["items"])
    assert {d["model_version"]["id"] for d in data["items"]} == {str(new[0])}
    assert carried["Open project"]["status"] == "approved" and carried["Open project"]["expressible"]
    assert carried["Open project"]["reviewed_by"]["display_name"] == "Ada Admin"
    assert carried["Gated project"]["status"] == "pending"
    old_rows = owner_conn.execute("SELECT DISTINCT status FROM rag.knowledge_definitions WHERE model_version_id = %s",
                                  (old,)).fetchall()
    assert [r[0] for r in old_rows] == ["retired"]
