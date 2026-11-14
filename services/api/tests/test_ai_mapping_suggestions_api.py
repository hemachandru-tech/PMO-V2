"""LLM mapping suggestions live (US-201; FEAT-067): on a TPG source whose go-live date has another name, AG-04 is
asked about what names and values leave open (Status is settled by its values, US-200), its suggestion is shown with
its reason and not applied, a suggestion US-199's guards would refuse is flagged, an invalid reply is ignored, and
with no model the step works as before."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import create_connection, h, load_packs_open_by_status, setup_world
from test_mapping_guards_api import _mysql_like_schema

pytestmark = [needs_db, pytest.mark.integration]


def _row(preview: dict[str, Any], key: str) -> dict[str, Any]:
    proj = next(e for e in preview["mapping_step"]["entities"] if e["key"] == "projects")
    return next(f for f in proj["fields"] if f["key"] == key)


async def test_ai_suggestions_shown_flagged_and_never_applied(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    from conftest import SOURCE_DB, _dsn_for

    _mysql_like_schema()
    with psycopg.connect(_dsn_for(SOURCE_DB), autocommit=True) as src:
        src.execute("ALTER TABLE ms.projects RENAME COLUMN planned_deployment_date TO go_live_target")
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    load_packs_open_by_status(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    cid = await create_connection(stack, admin, tpg_source, name="TPG MySQL names", schemas=["ms"])
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202
    pack_id = next(x for x in (await stack.get(f"/api/v1/starter-packs/match?connection_id={cid}",
                                               headers=h(admin))).json()["matches"] if x["pack_key"] == "pmo_tpg")[
        "pack_id"]
    url = f"/api/v1/starter-packs/{pack_id}"

    # Offline: no model, so the step works as before (US-199/US-200) and AI suggestions say so.
    pre = (await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid})).json()
    assert pre["checks"]["ai_available"] is False
    assert _row(pre, "status")["suggestion"]["column"] == "record_status"
    off = (await stack.post(f"{url}/ai-suggestions", headers=h(admin), json={"connection_id": cid})).json()
    assert off["available"] is False and off["suggestions"] == {} and "No AI model" in off["message"]

    projects = iter([
        {"suggestions": [{"field": "planned_deployment_date", "column": "go_live_target", "confidence": 0.85,
                          "reason": "A date column named for the go-live target."},
                         {"field": "status", "column": "record_status", "confidence": 1, "reason": "Not asked."}]},
        {"suggestions": [{"field": "planned_deployment_date", "column": "current_state", "confidence": 0.3,
                          "reason": "Guessing."}]},
        {"__invalid__": "{\"suggestions\": \"go_live_target\"}"},
        {"__invalid__": "{\"suggestions\": \"go_live_target\"}"},
    ])

    def reply(task: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        if messages[1]["content"].startswith("Pack table: Projects"):
            return next(projects)
        return {"suggestions": []}  # the fixture's other tables each lack one optional field: no column for it

    fake = FakeGateway({"AG-04": reply}, default_provider="ollama")
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    pre = (await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid})).json()
    assert pre["checks"]["ai_available"] is True and not fake.calls  # the preview never calls the model

    def project_calls() -> list[dict[str, Any]]:
        return [c for c in fake.calls if c["messages"][1]["content"].startswith("Pack table: Projects")]

    # One call per table with open fields; for projects, about the go-live date only: Status is settled by its
    # values (US-200).
    res = await stack.post(f"{url}/ai-suggestions", headers=h(admin), json={"connection_id": cid})
    assert res.status_code == 200, res.text
    body = res.json()
    assert {(c["agent"], c["task"]) for c in fake.calls} == {("AG-04", "map_suggest")}
    assert len(fake.calls) == len(body["tables"]) and len(project_calls()) == 1
    user = project_calls()[0]["messages"][1]["content"]
    assert "- planned_deployment_date — Planned deployment date, date" in user and "- status —" not in user
    assert "- go_live_target, date" in user and "- record_status, character varying(32); e.g. Active, Inactive" in user
    assert body["tables"][0] == {"entity": "projects", "asked": 1, "status": "ok", "suggested": 1}
    assert body["dropped"] == 1  # the answer about Status, which was not asked
    assert body["suggestions"] == {"projects.planned_deployment_date": {
        "column": "go_live_target", "confidence": 0.85, "reason": "A date column named for the go-live target.",
        "why": "unmapped", "source": "ai", "flags": []}}

    # Nothing applied: the preview still has the go-live date open and the saved mapping is untouched.
    pre = (await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid})).json()
    assert _row(pre, "planned_deployment_date")["column"] is None
    assert "projects.planned_deployment_date" not in pre["mapping"]["fields"]
    assert owner_conn.execute("SELECT count(*) FROM model.pack_field_mappings").fetchone() == (0,)

    # A suggestion US-199's guards would refuse is flagged: another field's column, of the wrong type.
    flagged = (await stack.post(f"{url}/ai-suggestions", headers=h(admin), json={"connection_id": cid})).json()
    s = flagged["suggestions"]["projects.planned_deployment_date"]
    assert s["column"] == "current_state" and s["flags"][0] == (
        "current_state is already Current state's column. One column can stand for one pack field only.")
    assert s["flags"][1].endswith("; Planned deployment date needs date.") and len(s["flags"]) == 2

    # A reply that stays invalid after one repair is ignored.
    bad = (await stack.post(f"{url}/ai-suggestions", headers=h(admin), json={"connection_id": cid})).json()
    assert bad["suggestions"] == {} and bad["tables"][0]["status"] == "failed"
    assert len(project_calls()) == 4  # one, one, then the invalid answer and its repair

    # The Admin confirms the good suggestion: it is an ordinary mapping and passes the guards.
    ok = (await stack.post(f"{url}/preview", headers=h(admin), json={
        "connection_id": cid, "mapping": {"fields": {"projects.planned_deployment_date": "go_live_target",
                                                     "projects.status": "record_status"}}})).json()
    assert _row(ok, "planned_deployment_date")["column"] == "go_live_target" and ok["checks"]["blocking"] == []
    audits = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'starter_pack.ai_suggested' "
                                "ORDER BY occurred_at").fetchall()
    assert [a[0]["suggested"] for a in audits] == [1, 1, 0] and audits[2][0]["failed"] == ["projects"]
    assert (await stack.post(f"{url}/ai-suggestions", headers=h(w.tokens["creator"]),
                             json={"connection_id": cid})).status_code == 403
