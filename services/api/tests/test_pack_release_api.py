"""Release a tenant pack with checks and four eyes (ENH-025, US-238; FEAT-075; DEC-26), on the TPG fixture.

A clone of PMO is checked on TPG Postgres (schema, references, knowledge, blueprints, compile, sanity, starter widgets,
gold plan-only) and a starter widget on a missing measure blocks it. Submitted with a reason for each warning, it is
locked; its author can't release it, a second Admin sends it back with a reason and, once resubmitted, releases it:
the manifest is stored with its starter reports expanded and the version is frozen. Installed on a second connection,
the next version opens from the released one, a changed rule shows in the diff, and its release reaches that
connection as an update notice while 1.0.0 keeps serving it. The database refuses a release by the author.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import AS_OF, World, create_connection, h, setup_world
from test_knowledge_api import bag
from test_pack_from_model_api import _approve

pytestmark = [needs_db, pytest.mark.integration]
URL = "/api/v1/packs"
KEY = "acme_pmo"
BROKEN = {"broken": {"title": "Broken", "tab": "Broken", "dashboard": "projects", "section": "Overview",
                     "widgets": [{"title": "Lost", "measure": "no_such_measure", "visual": "figure"}]}}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    w.tokens["reviewer"] = make_token("admin-2", groups=["TELA-Admins"], name="Bea Reviewer")
    return w


async def _get(api: httpx.AsyncClient, token: str, pid: str) -> tuple[dict[str, Any], str]:
    resp = await api.get(f"{URL}/{pid}", headers=h(token))
    assert resp.status_code == 200, resp.text
    return resp.json(), resp.headers["ETag"]


async def _submit(api: httpx.AsyncClient, token: str, pid: str, cid: str, **body: Any) -> httpx.Response:
    _, etag = await _get(api, token, pid)
    return await api.post(f"{URL}/{pid}/submit", headers=h(token, **{"If-Match": etag}),
                          json={"connection_id": cid, **body})


async def _explained(api: httpx.AsyncClient, token: str, pid: str, cid: str) -> dict[str, str]:
    """A reason for every warning the checks give."""
    checks = (await api.post(f"{URL}/{pid}/release-checks", headers=h(token), json={"connection_id": cid})).json()
    return {w["id"]: f"Known on TPG: {w['message'][:60]}" for w in checks["checks"]["warnings"]}


async def test_release_with_checks_four_eyes_and_update_notice(stack: httpx.AsyncClient, world: World,
                                                               owner_conn: psycopg.Connection,
                                                               tpg_source: dict[str, Any]) -> None:
    admin, reviewer, cid = world.tokens["admin"], world.tokens["reviewer"], world.connection_id
    pmo = str(owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0])
    made = await stack.post(URL, headers=h(admin), json={"method": "clone", "pack_key": KEY, "title": "Acme PMO",
                                                         "version": "1.0.0", "from_pack_id": pmo})
    assert made.status_code == 201, made.text
    pid = made.json()["id"]
    assert made.json()["review"]["state"] == "draft"
    assert (await stack.get("/api/v1/me", headers=h(reviewer))).status_code == 200  # the second Admin signs in

    # The checks on TPG Postgres: every step runs and the clone of PMO passes (warnings only).
    resp = await stack.post(f"{URL}/{pid}/release-checks", headers=h(admin), json={"connection_id": cid})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    checks = out["checks"]
    assert checks["passed"], checks["errors"][:3]
    assert all(s["ran"] for s in checks["steps"])
    assert checks["facts"]["compiled"]["measures"] > 20 and checks["facts"]["connection"]["name"] == "TPG Postgres"
    assert checks["facts"]["gold_planned"] > 0
    assert out["diff"]["against"] is None and out["diff"]["summary"]["measure"]["added"] > 20
    assert all(w["id"] and w["check"] for w in checks["warnings"])

    # A broken widget blocks the release.
    _, etag = await _get(stack, admin, pid)
    patched = await stack.patch(f"{URL}/{pid}", headers=h(admin, **{"If-Match": etag}),
                                json={"sections": {"reports": BROKEN}})
    assert patched.status_code == 200, patched.text
    blocked = await _submit(stack, admin, pid, cid, reasons=await _explained(stack, admin, pid, cid))
    assert blocked.status_code == 400, blocked.text
    errors = blocked.json()["error"]["details"]["checks"]["errors"]
    assert [e["check"] for e in errors] == ["blueprints"] and "measure no_such_measure" in errors[0]["message"]
    _, etag = await _get(stack, admin, pid)
    fixed = await stack.patch(f"{URL}/{pid}", headers=h(admin, **{"If-Match": etag}), json={"sections": {"reports": {}}})
    assert fixed.status_code == 200, fixed.text

    # Each warning needs a reason.
    reasons = await _explained(stack, admin, pid, cid)
    if reasons:
        unexplained = await _submit(stack, admin, pid, cid)
        assert unexplained.status_code == 400
        assert set(unexplained.json()["error"]["details"]["missing_reasons"]) == set(reasons)
    sent = await _submit(stack, admin, pid, cid, reasons=reasons, note="First Acme release")
    assert sent.status_code == 200, sent.text
    assert sent.json()["review"]["state"] == "in_review" and not sent.json()["editable"]

    # Locked while in review; the author can't release or send back their own version.
    _, etag = await _get(stack, admin, pid)
    locked = await stack.patch(f"{URL}/{pid}", headers=h(admin, **{"If-Match": etag}),
                               json={"sections": {"title": "Renamed"}})
    assert locked.status_code == 409 and locked.json()["error"]["details"]["reason"] == "in_review"
    for path in ("release", "return"):
        own = await stack.post(f"{URL}/{pid}/{path}", headers=h(admin), json={"reason": "mine"})
        assert own.status_code == 403 and own.json()["error"]["details"]["reason"] == "four_eyes", own.text
    review = (await stack.get(f"{URL}/{pid}/review", headers=h(reviewer))).json()
    assert review["you"] == {"author": False, "can_submit": False, "can_withdraw": True, "can_release": True,
                             "can_return": True, "can_open_next": False}
    assert review["people"][review["state"]["submitted_by"]] == "Ada Admin"
    assert review["review"]["note"] == "First Acme release"
    told = owner_conn.execute("SELECT u.display_name FROM ops.notifications n JOIN iam.users u ON u.id = n.user_id "
                              "WHERE n.kind = 'pack.review_requested'").fetchall()
    assert ("Bea Reviewer",) in told and ("Ada Admin",) not in told

    # A reviewer sends it back with a reason; the author resubmits; the reviewer releases.
    back = await stack.post(f"{URL}/{pid}/return", headers=h(reviewer), json={"reason": "Explain the zero figures."})
    assert back.status_code == 200, back.text
    assert back.json()["review"] == {**back.json()["review"], "state": "returned", "reason": "Explain the zero figures."}
    assert back.json()["editable"]
    assert (await stack.post(f"{URL}/{pid}/release", headers=h(reviewer))).status_code == 409  # not in review
    assert (await _submit(stack, admin, pid, cid, reasons=reasons)).status_code == 200
    released = await stack.post(f"{URL}/{pid}/release", headers=h(reviewer))
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "released" and released.json()["review"]["state"] == "released"
    status, manifest, by = owner_conn.execute("SELECT status, manifest, released_by FROM model.starter_packs WHERE "
                                              "id = %s", (pid,)).fetchone()
    assert status == "released" and "reports_from" not in manifest and manifest["reports"]
    assert by is not None
    frozen = await stack.post(f"{URL}/{pid}/submit", headers=h(admin), json={"connection_id": cid})
    assert frozen.status_code == 409

    # Installed on a second connection to the same data.
    cid2 = await create_connection(stack, admin, tpg_source, name="TPG Replica")
    assert (await stack.post(f"/api/v1/connections/{cid2}/test", headers=h(admin))).status_code == 200
    assert (await stack.post(f"/api/v1/connections/{cid2}/introspect", headers=h(admin))).status_code == 202
    install = await stack.post(f"/api/v1/starter-packs/{pid}/install", headers=h(admin),
                               json={"connection_id": cid2, "reporting_date": AS_OF})
    assert install.status_code == 202, install.text
    await _approve(stack, admin, install.json()["model_id"])

    # Editing the released version opens the next draft version, once.
    nxt = await stack.post(f"{URL}/{pid}/next-version", headers=h(admin))
    assert nxt.status_code == 201, nxt.text
    pid2 = nxt.json()["id"]
    assert nxt.json()["version"] == "1.1.0" and nxt.json()["status"] == "draft"
    again = await stack.post(f"{URL}/{pid}/next-version", headers=h(admin))
    assert again.status_code == 409 and again.json()["error"]["details"]["pack_id"] == pid2
    detail2, etag2 = await _get(stack, admin, pid2)
    assert detail2["manifest"]["reports_from"] == "report_blueprints.yaml"

    # The database holds the four eyes too: the author can't release it, even in SQL.
    with pytest.raises(psycopg.errors.CheckViolation):
        owner_conn.execute("UPDATE model.starter_packs SET status = 'released', released_at = now(), submitted_at = "
                           "now(), submitted_by = created_by, released_by = created_by WHERE id = %s", (pid2,))

    # A changed rule shows in the diff.
    measures = detail2["manifest"]["measures"]
    target = next(m for m in measures if m["key"] == "open_projects")
    old_rule = target["plain_rule"]
    changed = {**target, "plain_rule": "Projects not closed or cancelled.", "label": "Open projects (new rule)"}
    patched = await stack.patch(f"{URL}/{pid2}", headers=h(admin, **{"If-Match": etag2}), json={"sections": {
        "measures": [changed if m["key"] == "open_projects" else m for m in measures]}})
    assert patched.status_code == 200, patched.text
    diff = (await stack.get(f"{URL}/{pid2}/diff", headers=h(admin))).json()
    assert diff["against"] == {"id": pid, "version": "1.0.0"}
    entry = next(c for c in diff["changes"] if c["kind"] == "measure" and c["key"] == "open_projects")
    assert entry["change"] == "changed" and entry["rule"] == {"before": old_rule,
                                                              "after": "Projects not closed or cancelled."}
    assert diff["summary"]["measure"] == {"added": 0, "changed": 1, "removed": 0}
    # The starter reports whose figure takes the measure's label change with it.
    assert set(diff["summary"]) == {"measure", "report"} and diff["summary"]["report"]["changed"] > 0

    # Released: the connection with 1.0.0 gets the update notice and keeps 1.0.0 until it updates.
    assert (await _submit(stack, admin, pid2, cid, reasons=await _explained(stack, admin, pid2, cid))
            ).status_code == 200
    out2 = await stack.post(f"{URL}/{pid2}/release", headers=h(reviewer))
    assert out2.status_code == 200, out2.text
    assert out2.json()["update_notice"]["connections"] == 1 and out2.json()["update_notice"]["notified"] >= 1
    notice = owner_conn.execute("SELECT title FROM ops.notifications WHERE kind = 'pack.update_available' AND title "
                                "LIKE 'Acme PMO 1.1.0%%'").fetchall()
    assert notice and all("TPG Replica" in t for (t,) in notice)
    installed = owner_conn.execute("SELECT installed_pack_id FROM model.data_models WHERE connection_id = %s",
                                   (cid2,)).fetchone()[0]
    assert str(installed) == pid
    updates = (await stack.get("/api/v1/admin/packs/updates", headers=h(admin))).json()["items"]
    offer = next(u for u in updates if u["connection"]["id"] == cid2)
    assert offer["available"] == "1.1.0" and offer["installed"] == "1.0.0"


async def test_who_may_review(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    creator = world.tokens["creator"]
    pmo = str(owner_conn.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0])
    for path, method in (("release-checks", "post"), ("release", "post"), ("return", "post"), ("review", "get"),
                         ("diff", "get")):
        resp = await getattr(stack, method)(f"{URL}/{pmo}/{path}", headers=h(creator),
                                            **({"json": {"connection_id": world.connection_id, "reason": "xyz"}}
                                               if method == "post" else {}))
        assert resp.status_code == 403, (path, resp.text)
    # A system pack is never reviewed, nor opened as a next version.
    admin = world.tokens["admin"]
    assert (await stack.post(f"{URL}/{pmo}/release", headers=h(admin))).status_code == 409
    assert (await stack.post(f"{URL}/{pmo}/next-version", headers=h(admin))).status_code == 409
