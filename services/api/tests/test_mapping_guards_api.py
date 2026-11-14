"""Mapping guards live (US-199; FEAT-067): the EXEC-115 mapping on a TPG source whose record state is
``record_status``, through preview, install, the measure check and model approval."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import AS_OF, create_connection, h, load_packs_open_by_status, setup_world

pytestmark = [needs_db, pytest.mark.integration]


def _mysql_like_schema() -> None:
    """Schema ``ms``: the TPG tables as they are, with the project record state named ``record_status``."""
    from conftest import RO_USER, SOURCE_DB, _dsn_for

    with psycopg.connect(_dsn_for(SOURCE_DB), autocommit=True) as src:
        src.execute("DROP SCHEMA IF EXISTS ms CASCADE")
        src.execute("CREATE SCHEMA ms")
        tables = [r[0] for r in src.execute("SELECT table_name FROM information_schema.tables WHERE "
                                            "table_schema = 'public' AND table_type = 'BASE TABLE'").fetchall()]
        for t in tables:
            src.execute(f'CREATE TABLE ms."{t}" AS SELECT * FROM public."{t}"')
        src.execute("ALTER TABLE ms.projects RENAME COLUMN status TO record_status")
        src.execute(f"GRANT USAGE ON SCHEMA ms TO {RO_USER}")
        src.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA ms TO {RO_USER}")


async def test_exec115_mapping_blocked_and_record_status_passes(
    stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
    tpg_source: dict[str, Any],
) -> None:
    _mysql_like_schema()
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    cid = await create_connection(stack, admin, tpg_source, name="TPG MySQL names", schemas=["ms"])
    assert (await stack.post(f"/api/v1/connections/{cid}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))).status_code == 202
    load_packs_open_by_status(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    m = next(x for x in (await stack.get(f"/api/v1/starter-packs/match?connection_id={cid}",
                                         headers=h(admin))).json()["matches"] if x["pack_key"] == "pmo_tpg")
    url = f"/api/v1/starter-packs/{m['pack_id']}"

    # EXEC-115: Status mapped by hand to current_state, which is Current state's own column.
    wrong = {"fields": {"projects.status": "current_state"}}
    pre = await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid, "mapping": wrong})
    assert pre.status_code == 200, pre.text
    checks = pre.json()["checks"]
    assert [i["ref"] for i in checks["blocking"]] == ["field:projects.current_state"]
    assert "mapped to Status" in checks["blocking"][0]["message"]
    status_warning = next(x for x in checks["warnings"] if x["ref"] == "field:projects.status")
    assert status_warning["missing"] == ["Inactive"] and "current_state has" in status_warning["issue"]
    proj = next(e for e in pre.json()["mapping_step"]["entities"] if e["key"] == "projects")
    status = next(f for f in proj["fields"] if f["key"] == "status")
    assert {v["value"] for v in status["source_values"]} >= {"Active", "On Hold"}  # the column's values, beside
    assert status["values"] == ["Active", "Inactive"]                              # the pack's
    refused = await stack.post(f"{url}/install", headers=h(admin),
                               json={"connection_id": cid, "reporting_date": AS_OF, "mapping": wrong})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "validation_error"
    assert refused.json()["error"]["details"]["issues"] == checks["blocking"]
    check = await stack.post(f"{url}/check", headers=h(admin), json={"connection_id": cid, "mapping": wrong})
    assert check.status_code == 400

    # record_status: no conflict, no value warning on Status; measures are tried on the data.
    right = {"fields": {"projects.status": "record_status"}}
    ok = (await stack.post(f"{url}/preview", headers=h(admin), json={"connection_id": cid, "mapping": right})).json()
    assert ok["matched"] and ok["checks"]["blocking"] == [] and ok["checks"]["values_read"]
    assert "field:projects.status" not in {x["ref"] for x in ok["checks"]["warnings"]}
    trial = await stack.post(f"{url}/check", headers=h(admin), json={"connection_id": cid, "mapping": right})
    assert trial.status_code == 200, trial.text
    figures = {x["key"]: x["value"] for x in trial.json()["measures"]}
    with psycopg.connect(_dsn_for_source(), autocommit=True) as src:
        active = src.execute("SELECT count(*) FROM ms.projects WHERE record_status = 'Active'").fetchone()
    assert active is not None and int(figures["open_projects"]) == active[0]
    assert all(set(x) >= {"kind", "ref", "issue"} for x in trial.json()["warnings"])
    audit = owner_conn.execute("SELECT count(*) FROM ops.audit_events WHERE action = 'starter_pack.checked'").fetchone()
    assert audit is not None and audit[0] == 1

    # Install the good mapping, then break the draft as EXEC-115's model v2: both fields on current_state.
    install = await stack.post(f"{url}/install", headers=h(admin),
                               json={"connection_id": cid, "reporting_date": AS_OF, "mapping": right})
    assert install.status_code == 202, install.text
    model_id = install.json()["model_id"]
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    for q in draft["open_questions"]:
        await stack.post(f"/api/v1/models/{model_id}/questions/{q['id']}/answer", headers=h(admin),
                         json={"answer": q["options"][0] if q["options"] else "Yes"})
    draft = (await stack.get(f"/api/v1/models/{model_id}/draft", headers=h(admin))).json()
    entities = [{**e, "fields": [{**f, "source_column": "current_state"} if (e["key"], f["key"]) == ("projects", "status")
                                 else f for f in e["fields"]]} for e in draft["entities"]]
    broken = await stack.patch(f"/api/v1/models/{model_id}/draft", headers=h(admin, **{"If-Match": draft["draft_etag"]}),
                               json={"entities": entities})
    assert broken.status_code == 200, broken.text

    pre_checks = (await stack.get(f"/api/v1/models/{model_id}/draft/checks", headers=h(admin))).json()
    assert [i["ref"] for i in pre_checks["blocking"]] == ["field:projects.status"]
    warned = {x["ref"] for x in pre_checks["warnings"]}
    assert "measure:inactive_projects" in warned  # filters on Inactive, which current_state does not hold

    etag = broken.json()["draft_etag"]
    no = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin), json={"draft_etag": etag})
    assert no.status_code == 422, no.text
    err = no.json()["error"]
    assert err["code"] == "model_invalid" and err["details"]["can_override"] is True
    assert "Status and Current state both read current_state" in err["details"]["issues"][0]["issue"]

    yes = await stack.post(f"/api/v1/models/{model_id}/approve", headers=h(admin),
                           json={"draft_etag": etag, "override_note": "Checked with the PMO lead"})
    assert yes.status_code == 201, yes.text
    assert yes.json()["checks"]["blocking"]
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'model.approval_override'").fetchone()
    assert row is not None and row[0]["note"] == "Checked with the PMO lead"


def _dsn_for_source() -> str:
    from conftest import SOURCE_DB, _dsn_for

    return _dsn_for(SOURCE_DB)
