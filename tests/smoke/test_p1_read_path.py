"""P1 governed read path on the running Compose stack (core + dev profiles).

Real Keycloak sign-in → connection to the synthetic sample source through the connector runner (OpenBao
credentials, Ed25519 envelopes) → introspection → PMO pack install → approval → scope policies →
scoped queries per persona. Idempotent: re-running reuses the connection and the approved model.

    docker compose -f deploy/compose/compose.yaml --env-file deploy/compose/secrets/.env \\
        --profile core --profile dev up -d --build
    TELA_SMOKE=1 uv run pytest -q tests/smoke
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_compose_smoke import EDGE, SECRETS, sign_in

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packs" / "pmo" / "fixtures"))
CONNECTION = "Sample TPG (dev)"


def _pw(name: str) -> str:
    return (SECRETS / name).read_text().strip()


class Api:
    def __init__(self, token: str) -> None:
        self.client = httpx.Client(base_url=EDGE, timeout=90,
                                   headers={"Host": "tela.localhost:8088", "Authorization": f"Bearer {token}"})

    def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        return self.client.request(method, f"/api/v1{path}", **kw)


@pytest.fixture(scope="module")
def apis() -> dict[str, Api]:
    return {
        "admin": Api(sign_in("admin", _pw("dev_admin_password"))["access_token"]),
        "nomsa": Api(sign_in("nomsa", _pw("dev_creator_password"))["access_token"]),
        "viewer": Api(sign_in("viewer", _pw("dev_viewer_password"))["access_token"]),
    }


@pytest.fixture(scope="module")
def world(apis: dict[str, Api]) -> dict[str, str]:
    admin = apis["admin"]
    # Nomsa's CIO mapping carries her scope attribute.
    current = admin.call("GET", "/admin/group-mappings")
    mappings = [{"idp_group_id": m["idp_group_id"], "role": m["role"], "scope_attributes": m["scope_attributes"]}
                for m in current.json()["mappings"]]
    for m in mappings:
        if m["idp_group_id"] == "TELA-CIO" and m["role"] == "cio":
            m["scope_attributes"] = {"cio": ["Nomsa Osei"]}
    put = admin.call("PUT", "/admin/group-mappings", json={"mappings": mappings},
                     headers={"If-Match": current.json()["etag"]})
    assert put.status_code == 200, put.text

    items = admin.call("GET", "/connections").json()["items"]
    conn = next((c for c in items if c["name"] == CONNECTION), None)
    if conn is None:
        resp = admin.call("POST", "/connections", json={
            "name": CONNECTION, "engine": "postgres", "host": "sample-tpg", "port": 5432, "database": "tpg",
            "username": "tela_ro", "password": _pw("sample_tpg_ro_password"), "tls": {"mode": "disable"}})
        assert resp.status_code == 201, resp.text
        cid = resp.json()["connection_id"]
    else:
        cid = conn["id"]
    test = admin.call("POST", f"/connections/{cid}/test").json()
    assert test["status"] == "connected" and test["read_only"]["verdict"] == "safe", test
    intro = admin.call("POST", f"/connections/{cid}/introspect").json()
    assert intro["status"] == "connected", intro

    models = admin.call("GET", f"/models?connection_id={cid}").json()["items"]
    if not models or not models[0]["current_version_id"]:
        packs = admin.call("GET", "/starter-packs").json()["items"]
        pack = next(p for p in packs if p["pack_key"] == "pmo_tpg")
        install = admin.call("POST", f"/starter-packs/{pack['id']}/install",
                             json={"connection_id": cid, "reporting_date": "2026-09-30"})
        assert install.status_code == 202, install.text
        model_id = install.json()["model_id"]
        draft = admin.call("GET", f"/models/{model_id}/draft").json()
        approved = admin.call("POST", f"/models/{model_id}/approve", json={"draft_etag": draft["draft_etag"]})
        assert approved.status_code == 201, approved.text
        version_id = approved.json()["model_version_id"]
    else:
        version_id = models[0]["current_version_id"]

    # Starter dashboards arrive with pack 1.1.0: re-install the newest pack and approve when they are missing.
    starters = admin.call("GET", "/published").json()["items"]
    if not any(d["title"] == "PMO — Projects" for d in starters):
        packs = admin.call("GET", "/starter-packs").json()["items"]
        newest = max((p for p in packs if p["pack_key"] == "pmo_tpg"),
                     key=lambda p: tuple(int(x) for x in p["pack_version"].split(".")))
        install = admin.call("POST", f"/starter-packs/{newest['id']}/install",
                             json={"connection_id": cid, "reporting_date": "2026-09-30"})
        assert install.status_code == 202, install.text
        model_id = install.json()["model_id"]
        draft = admin.call("GET", f"/models/{model_id}/draft").json()
        approved = admin.call("POST", f"/models/{model_id}/approve", json={"draft_etag": draft["draft_etag"]})
        assert approved.status_code == 201, approved.text
        version_id = approved.json()["model_version_id"]

    existing = {(p["entity"], p["role"]) for p in
                admin.call("GET", f"/admin/scope-policies?connection_id={cid}").json()["items"]}
    for entity in ("projects", "project_requests"):
        for role, body in (("admin", {"all_rows": True, "description": "Admin: all rows"}),
                           ("cio", {"predicates": [{"field": "cio", "op": "=", "attr": "cio"}],
                                    "description": "Own CIO"})):
            if (entity, role) not in existing:
                resp = admin.call("POST", "/admin/scope-policies",
                                  json={"connection_id": cid, "entity": entity, "role": role, **body})
                assert resp.status_code == 201, resp.text
    return {"cid": cid, "version_id": version_id}


def _run(api: Api, world: dict[str, str], measure: str, **extra: Any) -> httpx.Response:
    spec = {"model_version_id": world["version_id"], "connection_id": world["cid"],
            "measure": {"ref": f"measure:{measure}"}, **extra}
    return api.call("POST", "/query/run", json={"spec": spec, "use_cache": False})


def test_reference_counts_through_the_real_runner(apis: dict[str, Api], world: dict[str, str]) -> None:
    for measure, expected in (("total_projects", 100), ("open_projects", 65), ("in_delivery", 13),
                              ("on_hold", 3), ("project_created", 49)):
        resp = _run(apis["admin"], world, measure)
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"] == [[expected]], measure


def test_cio_sees_only_her_projects(apis: dict[str, Api], world: dict[str, str]) -> None:
    import tpg_synthetic

    projects, _ = tpg_synthetic.generate()
    mine = sum(1 for p in projects if p["cio"] == "Nomsa Osei" and p["status"] == "Active")
    resp = _run(apis["nomsa"], world, "open_projects")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == [[mine]] and resp.json()["scope"]["locked"]
    regions = _run(apis["nomsa"], world, "total_projects", breakdowns=[{"ref": "dimension:cio"}]).json()["rows"]
    assert [r[0] for r in regions] == ["Nomsa Osei"]


def test_viewer_without_policy_gets_nothing(apis: dict[str, Api], world: dict[str, str]) -> None:
    resp = _run(apis["viewer"], world, "open_projects")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "scope_unresolved"


def test_home_and_starter_dashboard_for_a_cio(apis: dict[str, Api], world: dict[str, str]) -> None:
    home = apis["nomsa"].call("GET", "/home").json()
    titles = {t["title"] for t in home["shelves"]["starter"]}
    assert {"PMO — Projects", "PMO — Requests"} <= titles
    dash = next(t for t in home["shelves"]["starter"] if t["title"] == "PMO — Projects")
    view = apis["nomsa"].call("GET", f"/published/{dash['id']}").json()
    assert view["locked_scope_chip"] and len(view["tabs"]) == 6
    widget = view["tabs"][0]["widgets"][3]  # Open projects by region
    run = apis["nomsa"].call("POST", f"/widgets/{widget['id']}/run", json={"page_filters": []}).json()
    assert run["rows"] and run["viz"]["visual"] in ("ranked_bar", "table")
    region = run["rows"][0][0]
    drill = apis["nomsa"].call("POST", f"/widgets/{widget['id']}/drill", json={"mark": {"region": region}}).json()
    assert drill["total"] == run["rows"][0][1]
    cio_col = [c["name"] for c in drill["columns"]].index("cio")
    assert {r[cio_col] for r in drill["rows"]} == {"Nomsa Osei"}


def test_ask_through_the_stack(apis: dict[str, Api], world: dict[str, str]) -> None:
    turn = apis["nomsa"].call("POST", "/ask/turns", json={"question": "Which regions have the most overdue go-lives?",
                                                          "connection_id": world["cid"]})
    assert turn.status_code == 202, turn.text
    stream = apis["nomsa"].call("GET", f"/ask/turns/{turn.json()['turn_id']}/stream")
    assert "event: complete" in stream.text and "Nomsa" not in stream.text.split("event: complete")[0]
    assert "\"certified\": true" in stream.text
