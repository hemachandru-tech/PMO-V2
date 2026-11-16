"""Named conditions and the order of authority (ENH-028, US-252; FEAT-078 / FEAT-007), live on the TPG fixture.

Acceptance: indexing an approved model stores each certified count measure's condition, approved with the model
("Open projects" → open on Projects); approving a definition of the same term that selects other rows stores its
condition with a **conflict** — both rules and a row count each — listed for the Admin; until the Admin chooses, Ask
counts by the certified measure and says the two differ; choosing the condition makes Ask count by it, under the
person's row scope; choosing the measure drops it from Ask; retiring the definition retires its condition; a new
model version carries the conditions and the Admin's choice.
"""

from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h
from test_ask_api import ask, by_event
from test_knowledge_api import index_model
from test_knowledge_definitions_api import (
    OPEN_STATES,
    by_term,
    indexed_doc,
    listing,
    record_inactive,  # noqa: F401 - the fixture
    review,
    source_count,
    world,  # noqa: F401 - the fixture
)

pytestmark = [needs_db, pytest.mark.integration]
Q = "/api/v1/knowledge/conditions"
PLAIN = "How many open projects are there?"
GULF = ("KSA", "GCC - UAE")  # the creator's row scope in the PMO world


async def conditions(api: httpx.AsyncClient, token: str, **params: Any) -> dict[str, Any]:
    resp = await api.get(Q, headers=h(token), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def choose(api: httpx.AsyncClient, token: str, condition_id: str, use: str) -> httpx.Response:
    return await api.post(f"{Q}/{condition_id}/choose", headers=h(token), json={"use": use, "note": "PMO office"})


async def approved_open(api: httpx.AsyncClient, world_: World, keys: dict[str, Any]) -> dict[str, Any]:
    """The document indexed and approved, and its Open project definition approved (it differs from Open projects,
    which counts Status Active in this world)."""
    admin = world_.tokens["admin"]
    await indexed_doc(api, world_, keys, approve=True)
    open_ = by_term((await listing(api, admin))["items"])["Open project"]
    ok = await review(api, admin, open_["id"], decision="approve")
    assert ok.status_code == 200 and ok.json()["conflict"], ok.text
    return open_


async def test_measures_store_their_conditions_with_the_model(
        stack: httpx.AsyncClient, world: World, signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)
    assert (await stack.get(Q, headers=h(world.tokens["creator"]))).status_code == 403  # Admins only
    data = await conditions(stack, admin)
    items = by_term(data["items"])
    open_ = items["Open projects"]
    assert open_["source"] == "pack_measure" and open_["status"] == "approved" and open_["entity"] == "projects"
    assert open_["name"] == "open" and open_["measure"]["ref"] == "measure:open_projects"
    assert open_["rule_text"].startswith("Projects where Status") and open_["conflict"] is None
    assert open_["uses"] == "measure" and data["counts"]["conflicts"] == 0
    assert all(i["source"] == "pack_measure" and i["model_version"]["id"] == world.version_id for i in data["items"])


async def test_conflict_waits_for_the_admin_then_the_choice_applies(
        stack: httpx.AsyncClient, world: World, signing_keys: dict[str, Any],
        owner_conn: psycopg.Connection, record_inactive: list[str]) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    definition = await approved_open(stack, world, signing_keys)

    data = await conditions(stack, admin, conflicts=True)
    assert data["counts"]["conflicts"] == 1
    [held] = data["items"]
    # The term stored once: the definition's condition replaced the measure's own.
    assert held["term"] == "Open project" and held["source"] == "document" and held["status"] == "approved"
    assert held["definition"]["id"] == definition["id"] and held["prefer"] is None and held["uses"] == "measure"
    assert held["condition"] == {"field": "current_state", "op": "in", "values": list(OPEN_STATES)}
    defined = source_count("current_state = ANY(%s)", (list(OPEN_STATES),))
    certified = source_count("status = 'Active'")
    conflict = held["conflict"]
    assert conflict["condition"] == {"rule": "Projects where Current state is one of Project Created, Active, On Hold",
                                     "figure": defined}
    assert conflict["measure"]["label"] == "Open projects" and conflict["measure"]["figure"] == certified
    assert "Status" in conflict["measure"]["rule"]

    # Until the Admin chooses: the certified measure answers, and says the two differ.
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}
    assert ev["narrative"]["facts"]["value"] == certified
    assert ev["complete"]["badge"] == "definition_conflict"

    # Counting again keeps it; only an Admin chooses; only a conflict can be chosen.
    again = await stack.post(f"{Q}/{held['id']}/check", headers=h(admin))
    assert again.status_code == 200 and again.json()["conflict"]["measure"]["figure"] == certified, again.text
    assert (await choose(stack, creator, held["id"], "condition")).status_code == 403
    plain = next(i for i in (await conditions(stack, admin))["items"] if i["conflict"] is None)
    assert (await choose(stack, admin, plain["id"], "condition")).status_code == 409

    # The condition chosen: Ask counts by it, under each person's row scope.
    chose = await choose(stack, admin, held["id"], "condition")
    assert chose.status_code == 200, chose.text
    assert chose.json()["prefer"] == "condition" and chose.json()["decided_by"]["display_name"] == "Ada Admin"
    audit = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'knowledge.condition_chosen'"
                               ).fetchone()
    assert audit is not None and audit[0]["use"] == "condition" and audit[0]["note"] == "PMO office"
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["planner"] == "knowledge" and ev["narrative"]["facts"]["value"] == defined
    ev = by_event((await ask(stack, creator, PLAIN))[1])
    scoped = source_count("current_state = ANY(%s) AND region = ANY(%s)", (list(OPEN_STATES), list(GULF)))
    assert ev["plan"]["planner"] == "knowledge" and 0 < scoped < defined
    assert ev["narrative"]["facts"]["value"] == scoped

    # The measure chosen: Ask answers by it and no longer notes the conflict.
    assert (await choose(stack, admin, held["id"], "measure")).status_code == 200
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["spec"]["measure"] == {"ref": "measure:open_projects"}
    assert ev["complete"].get("badge") != "definition_conflict"

    # Retiring the definition retires its condition; the measure's own comes back.
    assert (await review(stack, admin, definition["id"], decision="retire")).status_code == 200
    items = by_term((await conditions(stack, admin))["items"])
    assert "Open project" not in items and items["Open projects"]["source"] == "pack_measure"


async def test_new_model_version_carries_conditions_and_the_choice(
        stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
        signing_keys: dict[str, Any], record_inactive: list[str]) -> None:
    admin = world.tokens["admin"]
    await approved_open(stack, world, signing_keys)
    [held] = (await conditions(stack, admin, conflicts=True))["items"]
    assert (await choose(stack, admin, held["id"], "condition")).status_code == 200

    old = world.version_id
    new = owner_conn.execute(
        "INSERT INTO model.model_versions (id, tenant_id, data_model_id, version_no, status, definition, approved_by, "
        "approved_at, created_by) SELECT gen_random_uuid(), tenant_id, data_model_id, version_no + 1, 'approved', "
        "definition, approved_by, now(), created_by FROM model.model_versions WHERE id = %s RETURNING id",
        (old,)).fetchone()
    assert new is not None
    owner_conn.execute("UPDATE model.data_models SET current_version_id = %s WHERE id = %s", (new[0], world.model_id))
    world.version_id = str(new[0])
    await index_model(stack, world, signing_keys)

    data = await conditions(stack, admin)
    assert {i["model_version"]["id"] for i in data["items"]} == {str(new[0])}
    carried = by_term(data["items"])["Open project"]
    assert carried["status"] == "approved" and carried["prefer"] == "condition" and carried["conflict"]
    assert carried["decided_by"]["display_name"] == "Ada Admin" and carried["uses"] == "condition"
    assert "Open projects" not in by_term(data["items"])  # one term, stored once: the definition's condition
    old_rows = owner_conn.execute("SELECT DISTINCT status FROM rag.named_conditions WHERE model_version_id = %s",
                                  (old,)).fetchall()
    assert [r[0] for r in old_rows] == ["retired"]
    ev = by_event((await ask(stack, admin, PLAIN))[1])
    assert ev["plan"]["planner"] == "knowledge"
