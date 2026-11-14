"""Personal fields, custom SQL and certification (P2 EPIC-07, EPIC-08: US-074 to US-086).

Acceptance: TEST-032 (grouping), TEST-033/TEST-083 (bands), TEST-034 (cross-table measure), TEST-035 to TEST-037
(custom SQL safety, scope and page filters), TEST-038 (request), TEST-039 (queue), TEST-040 (item detail),
TEST-041 (look-alikes), TEST-042 (semantic checks), TEST-043/TEST-044 (decisions and their effects).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event, parse_sse

pytestmark = [needs_db, pytest.mark.integration]
GULF = {"KSA", "GCC - UAE"}
SQL = "SELECT region, COUNT(*) AS n FROM projects WHERE :page_filters GROUP BY region"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def make_field(api: httpx.AsyncClient, token: str, world: World, **body: Any) -> httpx.Response:
    return await api.post("/api/v1/fields", headers=h(token), json={"connection_id": world.connection_id, **body})


GROUPING = {"kind": "grouping", "name": "Gulf split", "source_ref": "dimension:region",
            "groups": [{"label": "Saudi", "values": ["ksa"]}, {"label": "Emirates", "values": [" GCC - UAE "]}]}
JOIN = {"kind": "join_measure", "name": "Requests converted", "path": ["project_requests", "projects"],
        "agg": "count_distinct", "target": "project_requests.project_request_id"}


async def checked(api: httpx.AsyncClient, token: str, world: World, sql: str = SQL) -> dict[str, Any]:
    resp = await api.post("/api/v1/custom-sql/check", headers=h(token),
                          json={"connection_id": world.connection_id, "sql": sql})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


# --------------------------------------------------------------------------- personal fields (EPIC-07)


async def test_grouping_band_and_join_measure(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    dup = await make_field(stack, token, world, **{**GROUPING, "groups": [
        {"label": "A", "values": ["KSA"]}, {"label": "B", "values": ["ksa"]}]})
    assert dup.status_code == 400 and "ksa" in dup.json()["error"]["message"].lower()
    g = await make_field(stack, token, world, **GROUPING)
    assert g.status_code == 201, g.text
    field = g.json()
    assert field["badge"] == "personal" and "ignores case" in field["plain_rule"]
    assert field["spec_fragment"]["breakdown"] == {"ref": "personal:gulf_split"}
    prev = (await stack.post("/api/v1/fields/preview", headers=h(token), json={"field_id": field["id"]})).json()
    buckets = {b["label"]: b["count"] for b in prev["buckets"]}
    ksa = sum(1 for p in oracle.PROJECTS if p["region"] == "KSA")
    uae = sum(1 for p in oracle.PROJECTS if p["region"] == "GCC - UAE")
    assert buckets == {"Saudi": ksa, "Emirates": uae} and prev["as_of"]  # Creator scope: Gulf only (TEST-032)

    overlap = await make_field(stack, token, world, kind="band", name="Budget band",
                               source_ref="field:projects.approved_budget_value_usd", edges=[0, 5_000_000, 1_000_000])
    assert overlap.status_code == 400 and "5M" in overlap.json()["error"]["message"]
    band = await make_field(stack, token, world, kind="band", name="Budget band",
                            source_ref="field:projects.approved_budget_value_usd", edges=[0, 1_000_000, 5_000_000])
    assert band.status_code == 201, band.text
    assert "exactly 1M goes into 1M - 5M" in band.json()["plain_rule"]  # RULE-013 edge rule in words
    bprev = (await stack.post("/api/v1/fields/preview", headers=h(token), json={"field_id": band.json()["id"]})).json()
    gulf = [p for p in oracle.PROJECTS if p["region"] in GULF]
    expected = sum(1 for p in gulf if p["approved_budget_value_usd"] is not None
                   and 1_000_000 <= p["approved_budget_value_usd"] < 5_000_000)
    assert {b["label"]: b["count"] for b in bprev["buckets"]}.get("1M - 5M", 0) == expected
    assert sum(b["count"] for b in bprev["buckets"]) == len(gulf)

    j = await make_field(stack, token, world, **JOIN)
    assert j.status_code == 201, j.text
    assert j.json()["plain_rule"].startswith("Counts distinct") and "linked project" in j.json()["plain_rule"]
    jprev = (await stack.post("/api/v1/fields/preview", headers=h(token), json={"field_id": j.json()["id"]})).json()
    gulf_requests = {r["project_request_id"] for r in oracle.REQUESTS if r["region"] in GULF}
    linked = {p["project_request_id"] for p in gulf if p["project_request_id"] in gulf_requests}
    assert jprev["value"] == len(linked)  # fan-out safe: distinct requests (TEST-034)
    same = await make_field(stack, token, world, **{**JOIN, "name": "Other", "path": ["project_requests", "nowhere"]})
    assert same.status_code == 400

    # Use the grouping in an answer, then pin it: the widget is Personal.
    turn, _ = await ask(stack, token, "Open projects by region")
    resp = await stack.post(f"/api/v1/ask/turns/{turn}/refine", headers=h(token),
                            json={"chip_ops": [{"op": "use_field", "value": field["id"]}]})
    assert resp.status_code == 202, resp.text
    done = by_event(parse_sse((await stack.get(resp.json()["stream_url"], headers=h(token))).text))["complete"]
    assert done["spec"]["breakdowns"][0] == {"ref": "personal:gulf_split"}
    pinned = await stack.post("/api/v1/widgets", headers=h(token), json={
        "from_turn_id": resp.json()["turn_id"], "title": "Open by Gulf split", "visual": "ranked_bar"})
    assert pinned.status_code == 201, pinned.text
    assert pinned.json()["badges"] == ["personal"]
    in_use = await stack.delete(f"/api/v1/fields/{field['id']}", headers=h(token))
    assert in_use.status_code == 409


# --------------------------------------------------------------------------- custom SQL (EPIC-07)


async def test_custom_sql_check_run_and_safety(stack: httpx.AsyncClient, world: World) -> None:
    token, admin = world.tokens["creator"], world.tokens["admin"]
    ok = await checked(stack, token, world)
    assert [c["name"] for c in ok["checks"]][-2:] == ["scope_applied", "limits"] and ok["follows_page_filters"]
    run = await stack.post("/api/v1/custom-sql/run", headers=h(token), json={
        "connection_id": world.connection_id, "sql": SQL, "check_token": ok["check_token"]})
    assert run.status_code == 200, run.text
    rows = {r[0]: r[1] for r in run.json()["rows"]}
    assert set(rows) <= GULF and rows["KSA"] == sum(1 for p in oracle.PROJECTS if p["region"] == "KSA")  # scope
    filtered = await stack.post("/api/v1/custom-sql/run", headers=h(token), json={
        "connection_id": world.connection_id, "sql": SQL, "check_token": ok["check_token"],
        "page_filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]})
    assert [r[0] for r in filtered.json()["rows"]] == ["KSA"] and filtered.json()["filters_applied"]
    # The token binds the statement: a changed query needs a new check (TEST-035).
    tampered = await stack.post("/api/v1/custom-sql/run", headers=h(token), json={
        "connection_id": world.connection_id, "sql": SQL.replace("COUNT(*)", "COUNT(1)"),
        "check_token": ok["check_token"]})
    assert tampered.status_code == 412

    for sql, code in (
        ("SELECT 1; DROP TABLE projects", "sql_multiple_statements"),
        ("DELETE FROM projects", "sql_not_select"),
        ("SELECT pg_sleep(10)", "sql_forbidden_node"),
        ("SELECT usename FROM pg_catalog.pg_user", "sql_object_not_allowed"),
        ("SELECT region FROM projects -- sneaky", "sql_forbidden_node"),
        ("SELECT region FROM projects WHERE :page_filters AND :page_filters", "sql_placeholder_misplaced"),
        ("SELECT region FROM projects WHERE region = ':page_filters'", "sql_placeholder_misplaced"),
    ):
        bad = await stack.post("/api/v1/custom-sql/check", headers=h(token),
                               json={"connection_id": world.connection_id, "sql": sql})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == code, (sql, bad.text)
        assert bad.json()["error"]["details"]["checks"][-1]["ok"] is False
    # Hidden fields are refused (TEST-036).
    await stack.put("/api/v1/admin/field-policies", headers=h(admin), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["creator"]}]})
    hidden = await stack.post("/api/v1/custom-sql/check", headers=h(token), json={
        "connection_id": world.connection_id, "sql": "SELECT SUM(approved_budget_value_usd) FROM projects"})
    assert hidden.status_code == 422 and hidden.json()["error"]["code"] == "sql_field_hidden"
    events = (await stack.get("/api/v1/admin/audit?action=custom_sql.rejected&security=true", headers=h(admin))).json()
    assert len(events["items"]) >= 4

    # No placeholder: a warning, and the widget cannot follow page filters (MSG-017).
    plain = await checked(stack, token, world, "SELECT COUNT(*) AS n FROM projects")
    assert plain["warnings"] and not plain["follows_page_filters"]

    # Draft with chat never runs; the rules draft passes the checks.
    draft = (await stack.post("/api/v1/custom-sql/draft", headers=h(token), json={
        "connection_id": world.connection_id, "question": "open projects by region"})).json()
    assert draft["source"] == "rules" and ":page_filters" in draft["sql"]
    assert (await checked(stack, token, world, draft["sql"]))["ok"]

    report = (await stack.post("/api/v1/reports", headers=h(token), json={"title": "SQL"})).json()
    saved = await stack.post("/api/v1/widgets/custom-sql", headers=h(token), json={
        "connection_id": world.connection_id, "sql": SQL, "check_token": ok["check_token"], "title": "Projects by "
        "region (SQL)", "visual": "ranked_bar", "report_id": report["id"]})
    assert saved.status_code == 201, saved.text
    wid = saved.json()["widget_id"]
    widget_run = (await stack.post(f"/api/v1/widgets/{wid}/run", headers=h(token), json={
        "page_filters": [{"ref": "dimension:region", "op": "=", "values": ["GCC - UAE"]}]})).json()
    assert [r[0] for r in widget_run["rows"]] == ["GCC - UAE"] and widget_run["badges"] == ["custom_sql"]
    mongo = await stack.post("/api/v1/custom-sql/check", headers=h(world.tokens["viewer"]),
                             json={"connection_id": world.connection_id, "sql": SQL})
    assert mongo.status_code == 403  # Viewers have no customsql.use


# --------------------------------------------------------------------------- certification (EPIC-08)


async def personal_widget(stack: httpx.AsyncClient, world: World, field_id: str, report_id: str) -> str:
    token = world.tokens["creator"]
    turn, _ = await ask(stack, token, "Open projects by region")
    r = await stack.post(f"/api/v1/ask/turns/{turn}/refine", headers=h(token),
                         json={"chip_ops": [{"op": "use_field", "value": field_id}]})
    await stack.get(r.json()["stream_url"], headers=h(token))
    pinned = await stack.post("/api/v1/widgets", headers=h(token), json={
        "from_turn_id": r.json()["turn_id"], "title": "Open by Gulf split", "visual": "ranked_bar",
        "target": {"report_id": report_id}})
    assert pinned.status_code == 201, pinned.text
    return str(pinned.json()["id"])


async def test_request_queue_detail_and_certify(stack: httpx.AsyncClient, world: World) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    field = (await make_field(stack, creator, world, **GROUPING)).json()
    report = (await stack.post("/api/v1/reports", headers=h(creator), json={"title": "Gulf"})).json()
    widget_id = await personal_widget(stack, world, field["id"], report["id"])
    item = {"kind": "grouping", "id": field["id"]}
    early = await stack.post("/api/v1/certification/requests", headers=h(creator), json={"item": item})
    assert early.status_code == 409 and early.json()["error"]["message"] == "Share it or add it to something " \
        "you'll publish first."
    await stack.get("/api/v1/me", headers=h(world.tokens["nomsa"]))
    nomsa_id = (await stack.get("/api/v1/directory/users?q=Nomsa", headers=h(creator))).json()["items"][0]["id"]
    await stack.post("/api/v1/shares", headers=h(creator), json={"object": {"kind": "report", "id": report["id"]},
                                                                 "with_user_ids": [nomsa_id]})
    req = await stack.post("/api/v1/certification/requests", headers=h(creator),
                           json={"item": item, "note": "Used for the Gulf review"})
    assert req.status_code == 201, req.text
    again = await stack.post("/api/v1/certification/requests", headers=h(creator), json={"item": item})
    assert again.json()["request_id"] == req.json()["request_id"] and again.json()["reused"] is True

    q = (await stack.get("/api/v1/certification/queue", headers=h(admin))).json()["items"]
    entry = next(i for i in q if i["request_id"] == req.json()["request_id"])
    assert entry["name"] == "Gulf split" and entry["usage"]["widgets"] == 1 and entry["author"]["name"] == "Cleo Creator"
    assert (await stack.get("/api/v1/certification/queue", headers=h(creator))).status_code == 403

    d = (await stack.get(f"/api/v1/certification/requests/{req.json()['request_id']}", headers=h(admin))).json()
    assert d["rule"]["plain"].startswith("Groups Region") and "Not set" in d["rule"]["blank_handling"]
    assert {b["label"] for b in d["results"]["buckets"]} >= {"Saudi", "Emirates"}  # Admin scope: all data
    assert d["usage"]["widgets"][0]["id"] == widget_id and "CASE" in (d["generated"] or "").upper()
    assert any(lk["ref"] == "dimension:region" for lk in d["lookalikes"])  # TEST-041: same base field
    assert d["can_decide"]["allowed"] is True

    # Edits while pending flag the request; the decision must confirm the review.
    await stack.patch(f"/api/v1/fields/{field['id']}", headers=h(creator), json={
        "connection_id": world.connection_id, **GROUPING, "catch_all_label": "Other"})
    stale = await stack.post(f"/api/v1/certification/requests/{req.json()['request_id']}/decision",
                             headers=h(admin), json={"decision": "certify"})
    assert stale.status_code == 409 and stale.json()["error"]["details"]["changed_since_requested"]
    certified = await stack.post(f"/api/v1/certification/requests/{req.json()['request_id']}/decision",
                                 headers=h(admin, **{"Idempotency-Key": "c1"}),
                                 json={"decision": "certify", "reviewed_changes": True})
    assert certified.status_code == 201, certified.text
    assert certified.json()["widgets_changed"] == ["Open by Gulf split"]
    replay = await stack.post(f"/api/v1/certification/requests/{req.json()['request_id']}/decision",
                              headers=h(admin, **{"Idempotency-Key": "c1"}), json={"decision": "certify"})
    assert replay.json() == certified.json()
    twice = await stack.post(f"/api/v1/certification/requests/{req.json()['request_id']}/decision",
                             headers=h(admin), json={"decision": "reject", "note": "no"})
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "already_decided"
    lib = {w["id"]: w for w in (await stack.get("/api/v1/widgets", headers=h(creator))).json()["items"]}
    assert lib[widget_id]["badges"] == ["certified"]
    note = (await stack.get("/api/v1/notifications", headers=h(creator))).json()["items"]
    assert any(n["kind"] == "certification.certified" for n in note)


async def test_merge_reject_self_and_publish_bound(stack: httpx.AsyncClient, world: World,
                                                  owner_conn: psycopg.Connection) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    measure = (await make_field(stack, creator, world, **{**JOIN, "name": "Linked requests"})).json()
    # A widget using the personal measure, on a dashboard sent for publishing: publish-bound queue entry.
    turn, _ = await ask(stack, creator, "Total projects")
    r = await stack.post(f"/api/v1/ask/turns/{turn}/refine", headers=h(creator),
                         json={"chip_ops": [{"op": "use_field", "value": measure["id"]}]})
    await stack.get(r.json()["stream_url"], headers=h(creator))
    report = (await stack.post("/api/v1/reports", headers=h(creator), json={"title": "Demand"})).json()
    pinned = await stack.post("/api/v1/widgets", headers=h(creator), json={
        "from_turn_id": r.json()["turn_id"], "title": "Linked requests", "visual": "figure",
        "target": {"report_id": report["id"]}})
    assert pinned.status_code == 201 and pinned.json()["badges"] == ["personal"]
    ok = await checked(stack, creator, world, "SELECT COUNT(*) AS n FROM projects WHERE :page_filters")
    sql_widget = await stack.post("/api/v1/widgets/custom-sql", headers=h(creator), json={
        "connection_id": world.connection_id, "sql": "SELECT COUNT(*) AS n FROM projects WHERE :page_filters",
        "check_token": ok["check_token"], "title": "All projects (SQL)", "visual": "figure",
        "report_id": report["id"]})
    assert sql_widget.status_code == 201, sql_widget.text
    dash = (await stack.post("/api/v1/dashboards", headers=h(creator), json={
        "title": "Demand", "report_ids": [report["id"]]})).json()
    checks = (await stack.post(f"/api/v1/dashboards/{dash['id']}/prechecks", headers=h(admin),
                               json={"target_roles": ["cio"]})).json()
    cert = next(c for c in checks["checks"] if c["check"] == "all_widgets_certified")
    assert not cert["passed"] and {f["action"] for i in cert["items"] for f in i["fixes"]} >= {"request_certification"}
    sent = (await stack.post(f"/api/v1/dashboards/{dash['id']}/request-publish", headers=h(creator),
                             json={"target_roles": ["cio"]})).json()
    assert len(sent["certification_requests"]) == 2
    queue = {i["kind"]: i for i in (await stack.get("/api/v1/certification/queue", headers=h(admin))).json()["items"]}
    assert queue["join_measure"]["trigger"] == "publish_bound" and queue["join_measure"]["blocks_publish"]

    # Semantic checks: the SQL counts every project; Open projects only counts Active ones (TEST-042).
    sql_req = queue["custom_sql"]["request_id"]
    detail = (await stack.get(f"/api/v1/certification/requests/{sql_req}", headers=h(admin))).json()
    differs = [c for c in detail["semantic_checks"] if c["status"] == "differs"]
    assert any(c["measure"] == "measure:open_projects" and c["row_difference"] == 100 - oracle.count("open_projects")
               for c in differs), detail["semantic_checks"]
    rejected = await stack.post(f"/api/v1/certification/requests/{sql_req}/decision", headers=h(admin),
                                json={"decision": "reject"})
    assert rejected.status_code == 400
    rejected = await stack.post(f"/api/v1/certification/requests/{sql_req}/decision", headers=h(admin),
                                json={"decision": "reject", "note": "Use the certified Open projects measure."})
    assert rejected.status_code == 201 and rejected.json()["status"] == "rejected"
    with pytest.raises(psycopg.errors.RestrictViolation):
        owner_conn.execute("UPDATE gov.certification_decisions SET note = 'x'")  # insert-only record

    # Merge the personal measure into a certified one that answers differently: confirmation required.
    jm_req = queue["join_measure"]["request_id"]
    jd = (await stack.get(f"/api/v1/certification/requests/{jm_req}", headers=h(admin))).json()
    assert any(lk["ref"] == "measure:total_requests" for lk in jd["lookalikes"]), jd["lookalikes"]
    unconfirmed = await stack.post(f"/api/v1/certification/requests/{jm_req}/decision", headers=h(admin),
                                   json={"decision": "merge", "merge_into": "measure:total_requests"})
    assert unconfirmed.status_code == 409 and "Confirm" in unconfirmed.json()["error"]["message"]
    merged = await stack.post(f"/api/v1/certification/requests/{jm_req}/decision", headers=h(admin), json={
        "decision": "merge", "merge_into": "measure:total_requests", "confirm_different": True})
    assert merged.status_code == 201, merged.text
    assert merged.json()["widgets_changed"] == ["Linked requests"]
    lib = {w["title"]: w for w in (await stack.get("/api/v1/widgets", headers=h(creator))).json()["items"]}
    assert lib["Linked requests"]["spec"]["measure"] == {"ref": "measure:total_requests"}
    assert lib["Linked requests"]["badges"] == ["certified"]

    # An Admin cannot certify their own definition unless the tenant allows it.
    own = (await make_field(stack, admin, world, **{**GROUPING, "name": "Admin split"})).json()
    areport = (await stack.post("/api/v1/reports", headers=h(admin), json={"title": "A"})).json()
    turn, _ = await ask(stack, admin, "Open projects by region")
    r = await stack.post(f"/api/v1/ask/turns/{turn}/refine", headers=h(admin),
                         json={"chip_ops": [{"op": "use_field", "value": own["id"]}]})
    await stack.get(r.json()["stream_url"], headers=h(admin))
    await stack.post("/api/v1/widgets", headers=h(admin), json={"from_turn_id": r.json()["turn_id"], "title": "x",
                                                                "visual": "ranked_bar",
                                                                "target": {"report_id": areport["id"]}})
    await stack.post("/api/v1/dashboards", headers=h(admin), json={"title": "A", "report_ids": [areport["id"]]})
    mine = (await stack.post("/api/v1/certification/requests", headers=h(admin),
                             json={"item": {"kind": "grouping", "id": own["id"]}})).json()
    blocked = await stack.post(f"/api/v1/certification/requests/{mine['request_id']}/decision", headers=h(admin),
                               json={"decision": "certify"})
    assert blocked.status_code == 403
    await stack.put("/api/v1/admin/certification-policy", headers=h(admin), json={"allow_self_certification": True})
    allowed = await stack.post(f"/api/v1/certification/requests/{mine['request_id']}/decision", headers=h(admin),
                               json={"decision": "certify"})
    assert allowed.status_code == 201


async def test_lookalikes_use_embeddings_when_available(stack: httpx.AsyncClient, world: World) -> None:
    from conftest import FakeGateway

    creator, admin = world.tokens["creator"], world.tokens["admin"]
    gateway = FakeGateway(replies={"AG-09": {"text": "Counts each demand request that became a project."}})
    stack.app.state.gateway = gateway.client()  # type: ignore[attr-defined]
    measure = (await make_field(stack, creator, world, **JOIN)).json()
    report = (await stack.post("/api/v1/reports", headers=h(creator), json={"title": "D"})).json()
    turn, _ = await ask(stack, creator, "Total projects")
    r = await stack.post(f"/api/v1/ask/turns/{turn}/refine", headers=h(creator),
                         json={"chip_ops": [{"op": "use_field", "value": measure["id"]}]})
    await stack.get(r.json()["stream_url"], headers=h(creator))
    await stack.post("/api/v1/widgets", headers=h(creator), json={"from_turn_id": r.json()["turn_id"], "title": "L",
                                                                  "visual": "figure",
                                                                  "target": {"report_id": report["id"]}})
    await stack.post("/api/v1/dashboards", headers=h(creator), json={"title": "D", "report_ids": [report["id"]]})
    req = (await stack.post("/api/v1/certification/requests", headers=h(creator),
                            json={"item": {"kind": "join_measure", "id": measure["id"]}})).json()
    d = (await stack.get(f"/api/v1/certification/requests/{req['request_id']}", headers=h(admin))).json()
    assert d["lookalike_note"] is None and d["rule"]["rephrased"]
    similar = [lk for lk in d["lookalikes"] if any(x.startswith("similar meaning") for x in lk["reasons"])]
    assert "measure:total_requests" in {lk["ref"] for lk in similar}
    assert not any(lk["ref"].startswith("measure:open_projects") for lk in similar)
    assert any(c["agent"] == "embed" for c in gateway.calls)
    # Embeddings are cached: a second look does not call the model again for the same texts.
    before = sum(c["agent"] == "embed" for c in gateway.calls)
    async with __import__("tela_api.core.db", fromlist=["tenant_tx"]).tenant_tx(world.tenant_id) as conn:
        from sqlalchemy import text as sql_text
        await conn.execute(sql_text("DELETE FROM gov.lookalike_findings"))
    await stack.get(f"/api/v1/certification/requests/{req['request_id']}", headers=h(admin))
    assert sum(c["agent"] == "embed" for c in gateway.calls) == before
