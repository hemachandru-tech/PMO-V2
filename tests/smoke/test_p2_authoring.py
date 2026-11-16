"""P2 authoring and publishing on the running Compose stack (core + dev profiles).

Nomsa (Creator and CIO) plans and builds a report with chat, refines and pins an answer, composes a
dashboard and requests publishing; the Admin runs the six checks and publishes to the CIO role; Nomsa reads
the live version in her own scope; the version is restored and withdrawn; the audit log shows it all.
Runs after the P1 smoke (it reuses its world fixture) and cleans up the dashboard it creates.
"""

from __future__ import annotations

import pytest
from test_p1_read_path import Api, apis, world  # noqa: F401 - shared module fixtures

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")


def test_plan_build_publish_and_read(apis: dict[str, Api], world: dict[str, str]) -> None:
    nomsa, admin = apis["nomsa"], apis["admin"]
    plan = nomsa.call("POST", "/reports/plan", json={
        "request": "Delivery report for my region with overdue go-lives by region and team workload"})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["sections"] and any("workload" in g["reason"] for g in body["gaps"])
    built = nomsa.call("POST", f"/reports/plans/{body['plan_id']}/build")
    assert built.status_code == 201, built.text
    rid = built.json()["report_id"]
    assert all(r["status"] in ("ok", "empty") for r in built.json()["results"])

    # Refine an answer and pin it onto the built report.
    # The stack has several PMO sources (P3): say which one, as the Ask screen's source buttons do.
    turn = nomsa.call("POST", "/ask/turns", json={"question": "Open projects by region",
                                                  "connection_id": world["cid"]}).json()
    nomsa.call("GET", f"/ask/turns/{turn['turn_id']}/stream")
    refined = nomsa.call("POST", f"/ask/turns/{turn['turn_id']}/refine", json={"chip_ops": [{"op": "set_limit",
                                                                                              "value": 3}]}).json()
    stream = nomsa.call("GET", f"/ask/turns/{refined['turn_id']}/stream")
    assert "event: complete" in stream.text
    pinned = nomsa.call("POST", "/widgets", json={"from_turn_id": refined["turn_id"], "title": "Top 3 regions",
                                                  "visual": "ranked_bar", "target": {"report_id": rid}})
    assert pinned.status_code in (201, 409), pinned.text

    d = nomsa.call("POST", "/dashboards", json={"title": "Smoke — delivery", "report_ids": [rid],
                                                "shared_filters": [{"ref": "dimension:region"}]}).json()
    try:
        assert nomsa.call("POST", f"/dashboards/{d['id']}/publish", json={"target_roles": ["cio"]}).status_code == 403
        assert nomsa.call("POST", f"/dashboards/{d['id']}/request-publish",
                          json={"target_roles": ["cio"]}).status_code == 202
        checks = admin.call("POST", f"/dashboards/{d['id']}/prechecks", json={"target_roles": ["cio"]}).json()
        assert checks["passed"], checks
        v1 = admin.call("POST", f"/dashboards/{d['id']}/publish", json={"target_roles": ["cio"], "note": "smoke"})
        assert v1.status_code == 201, v1.text
        view = nomsa.call("GET", f"/published/{d['id']}").json()
        assert view["kind"] == "user" and view["version_id"] == v1.json()["dashboard_version_id"]
        widget = next(w for t in view["tabs"] for w in t["widgets"] if not w["hidden"])
        run = nomsa.call("POST", f"/widgets/{widget['id']}/run",
                         json={"context": {"dashboard_version_id": view["version_id"]}})
        assert run.status_code == 200, run.text
        assert apis["viewer"].call("GET", f"/published/{d['id']}").status_code == 404
        v2 = admin.call("POST", f"/dashboards/{d['id']}/publish", json={"target_roles": ["cio"]})
        assert v2.status_code == 201
        v3 = admin.call("POST", f"/dashboards/{d['id']}/versions/1/restore", json={})
        assert v3.status_code == 201 and v3.json()["version"] == 3
        audit = admin.call("GET", f"/admin/audit?object_id={d['id']}").json()["items"]
        assert {"dashboard.published", "dashboard.restored", "dashboard.publish_requested"} <= {
            i["action"] for i in audit}
    finally:
        nomsa.call("DELETE", f"/dashboards/{d['id']}")
    assert nomsa.call("GET", f"/published/{d['id']}").status_code == 404
