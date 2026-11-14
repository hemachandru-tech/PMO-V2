"""P2 personal fields, custom SQL and certification on the running Compose stack (core + dev profiles).

Nomsa (Creator and CIO) builds a grouping and runs custom SQL through the real connector runner (scope injected,
signed, re-verified by the runner); unsafe SQL is refused; a publish-bound request reaches the Admin queue and is
decided. Reuses the P1 smoke world and cleans up what it creates.
"""

from __future__ import annotations

import pytest
from test_p1_read_path import Api, apis, world  # noqa: F401 - shared module fixtures

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")
SQL = "SELECT cio, COUNT(*) AS n FROM projects WHERE :page_filters GROUP BY cio"


def test_fields_custom_sql_and_certification(apis: dict[str, Api], world: dict[str, str]) -> None:  # noqa: F811
    nomsa, admin = apis["nomsa"], apis["admin"]
    cid = world["cid"]
    check = nomsa.call("POST", "/custom-sql/check", json={"connection_id": cid, "sql": SQL})
    assert check.status_code == 200, check.text
    run = nomsa.call("POST", "/custom-sql/run", json={"connection_id": cid, "sql": SQL,
                                                      "check_token": check.json()["check_token"]})
    assert run.status_code == 200, run.text
    assert [r[0] for r in run.json()["rows"]] == ["Nomsa Osei"]  # her scope, injected and runner-verified
    bad = nomsa.call("POST", "/custom-sql/check", json={"connection_id": cid, "sql": "SELECT 1; DROP TABLE projects"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "sql_multiple_statements"

    name = "Smoke region split"
    for f in nomsa.call("GET", "/fields").json()["items"]:
        if f["name"] == name:
            nomsa.call("DELETE", f"/fields/{f['id']}")
    field = nomsa.call("POST", "/fields", json={
        "kind": "grouping", "name": name, "connection_id": cid, "source_ref": "dimension:region",
        "groups": [{"label": "Gulf", "values": ["KSA", "GCC - UAE"]}]})
    assert field.status_code == 201, field.text
    preview = nomsa.call("POST", "/fields/preview", json={"field_id": field.json()["id"]}).json()
    assert preview["buckets"] and preview["as_of"]

    report = nomsa.call("POST", "/reports", json={"title": "Smoke SQL"}).json()
    saved = nomsa.call("POST", "/widgets/custom-sql", json={
        "connection_id": cid, "sql": SQL, "check_token": check.json()["check_token"], "title": "Projects per CIO",
        "visual": "ranked_bar", "report_id": report["id"]})
    assert saved.status_code == 201, saved.text
    dash = nomsa.call("POST", "/dashboards", json={"title": "Smoke SQL", "report_ids": [report["id"]]}).json()
    try:
        sent = nomsa.call("POST", f"/dashboards/{dash['id']}/request-publish", json={"target_roles": ["cio"]}).json()
        assert sent["certification_requests"]
        queue = admin.call("GET", "/certification/queue").json()["items"]
        entry = next(i for i in queue if i["request_id"] == sent["certification_requests"][0])
        assert entry["kind"] == "custom_sql" and entry["blocks_publish"]
        detail = admin.call("GET", f"/certification/requests/{entry['request_id']}").json()
        assert detail["semantic_checks"] and detail["generated"].startswith("SELECT")
        decided = admin.call("POST", f"/certification/requests/{entry['request_id']}/decision",
                             json={"decision": "certify"})
        assert decided.status_code == 201, decided.text
        checks = admin.call("POST", f"/dashboards/{dash['id']}/prechecks", json={"target_roles": ["cio"]}).json()
        assert next(c for c in checks["checks"] if c["check"] == "all_widgets_certified")["passed"]
    finally:
        nomsa.call("DELETE", f"/dashboards/{dash['id']}")
