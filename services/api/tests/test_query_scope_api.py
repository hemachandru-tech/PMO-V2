"""Governed read path through the API: scope, hidden fields, limits, cache, as-of (US-018 - US-022, US-026, US-041).

Acceptance: TEST-003, TEST-004 (scope and hidden fields), TEST-059 / TEST-073 (scope-keyed cache),
TEST-074 - TEST-076 (scope cannot be bypassed), TEST-088 - TEST-090 (limits, as-of), TEST-061 (matrix).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, as_map, h, run, setup_world, value

pytestmark = [needs_db, pytest.mark.integration]
REGIONS = {"KSA", "GCC - UAE"}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


# --------------------------------------------------------------------------- scope isolation (TEST-003, TEST-061)


PERSONAS = {
    "admin": {},
    "creator": {"regions": REGIONS},
    "nomsa": {"cio": "Nomsa Osei"},
    "karim": {"cio": "Karim Haddad"},
}


@pytest.mark.parametrize("persona", sorted(PERSONAS))
async def test_each_persona_sees_exactly_its_scope(stack: httpx.AsyncClient, world: World, persona: str) -> None:
    token, scope = world.tokens[persona], PERSONAS[persona]
    for measure in ("open_projects", "overdue_go_lives", "in_delivery", "open_demand"):
        resp = await run(stack, token, world.spec(measure))
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"][0][0] == oracle.count(measure, **scope), (persona, measure)
        assert resp.json()["scope"]["locked"] is True and resp.json()["scope"]["summary"]

    by_region = as_map(await run(stack, token, world.spec("total_projects", breakdowns=[{"ref": "dimension:region"}])))
    if "regions" in scope:
        assert set(by_region) <= REGIONS
    if "cio" in scope:
        assert sum(by_region.values()) == oracle.count("total_projects", cio=scope["cio"])

    # Filter options are computed under scope too (RULE-005).
    values = await stack.post("/api/v1/query/filter-values", headers=h(token), json={
        "connection_id": world.connection_id, "model_version_id": world.version_id, "ref": "dimension:cio"})
    assert values.status_code == 200, values.text
    cios = set(values.json()["values"])
    if "cio" in scope:
        assert cios == {scope["cio"]}
    elif "regions" in scope:
        assert cios == {p["cio"] for p in oracle.PROJECTS if p["region"] in REGIONS}
    else:
        assert cios == set(oracle.CIOS if hasattr(oracle, "CIOS") else {p["cio"] for p in oracle.PROJECTS})


async def test_no_policy_means_no_data(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    resp = await run(stack, world.tokens["viewer"], world.spec("open_projects"))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "scope_unresolved"
    assert resp.json()["error"]["message"] == "Could not apply your data rules to this query, so it was not run."
    row = owner_conn.execute(
        "SELECT outcome FROM ops.audit_events WHERE action = 'query.scope_unresolved'").fetchone()
    assert row == ("deny",)
    # Nothing reached the source.
    assert owner_conn.execute("SELECT count(*) FROM ops.query_executions WHERE user_id IN "
                              "(SELECT id FROM iam.users WHERE display_name = 'Vic Viewer')").fetchone() == (0,)


async def test_client_supplied_scope_is_ignored(stack: httpx.AsyncClient, world: World) -> None:
    nomsa = world.tokens["nomsa"]
    plain = value(await run(stack, nomsa, world.spec("open_projects")))
    smuggled = world.spec("open_projects", scope={"all_rows": True}, scope_hash="0" * 64, sql="SELECT 1",
                          tenant_id="00000000-0000-0000-0000-000000000000")
    assert value(await run(stack, nomsa, smuggled)) == plain == oracle.count("open_projects", cio="Nomsa Osei")


async def test_filters_narrow_and_never_widen(stack: httpx.AsyncClient, world: World) -> None:
    nomsa = world.tokens["nomsa"]
    # A value outside her scope is not a known value for her: refused, nothing leaks.
    resp = await run(stack, nomsa, world.spec("open_projects", filters=[
        {"ref": "dimension:cio", "op": "=", "values": ["Karim Haddad"]}]))
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "spec_invalid"
    assert "admin_detail" not in resp.json()["error"]["details"]
    # A region filter inside her data narrows her count.
    region = next(p["region"] for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei")
    narrowed = value(await run(stack, nomsa, world.spec("total_projects", filters=[
        {"ref": "dimension:region", "op": "=", "values": [region]}])))
    assert narrowed == sum(1 for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei" and p["region"] == region)


async def test_hidden_and_masked_fields(stack: httpx.AsyncClient, world: World) -> None:
    admin, nomsa = world.tokens["admin"], world.tokens["nomsa"]
    put = await stack.put("/api/v1/admin/field-policies", headers=h(admin), json={
        "connection_id": world.connection_id,
        "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": ["cio"]},
                  {"field_ref": "field:projects.delivery_manager", "hidden_for": ["cio"], "effect": "mask"}]})
    assert put.status_code == 200, put.text

    hidden = await run(stack, nomsa, world.spec("approved_budget_total"))
    assert hidden.status_code == 200
    assert hidden.json()["hidden"] is True and hidden.json()["message"] == "Hidden for your role"
    assert hidden.json()["rows"] == []
    # Filtering on the hidden field is hidden too; the catalog no longer offers it.
    hidden_filter = await run(stack, nomsa, world.spec("open_projects", filters=[
        {"ref": "field:projects.approved_budget_value_usd", "op": ">", "values": [1]}]))
    assert hidden_filter.json()["hidden"] is True
    catalog = (await stack.get(f"/api/v1/query/catalog?connection_id={world.connection_id}", headers=h(nomsa))).json()
    assert "measure:approved_budget_total" not in {m["ref"] for m in catalog["measures"]}
    assert "measure:open_projects" in {m["ref"] for m in catalog["measures"]}
    # Fields and links for the field builder (US-078): hidden fields are left out here too.
    fields = {f["ref"] for e in catalog["entities"] for f in e["fields"]}
    assert "field:projects.approved_budget_value_usd" not in fields and "field:projects.delivery_manager" in fields
    assert catalog["links"] and all(len(pair) == 2 for pair in catalog["links"])
    projects = next(e for e in catalog["entities"] if e["key"] == "projects")
    assert projects["table"] and all(f["column"] for f in projects["fields"])

    masked = await run(stack, nomsa, world.spec("total_projects", breakdowns=[{"ref": "dimension:delivery_manager"}]))
    assert {r[0] for r in masked.json()["rows"]} == {"••••"}
    # Admin is not affected.
    assert value(await run(stack, admin, world.spec("approved_budget_total"))) is not None
    plain = await run(stack, admin, world.spec("total_projects", breakdowns=[{"ref": "dimension:delivery_manager"}]))
    assert "••••" not in {r[0] for r in plain.json()["rows"]}


# --------------------------------------------------------------------------- validation (US-018)


async def test_validation_rejections(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    admin, nomsa = world.tokens["admin"], world.tokens["nomsa"]
    unknown = await run(stack, nomsa, world.spec("not_a_measure"))
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "spec_invalid"
    assert "admin_detail" not in unknown.json()["error"]["details"]
    unknown_admin = await run(stack, admin, world.spec("not_a_measure"))
    assert unknown_admin.json()["error"]["details"]["admin_detail"]["ref"] == "measure:not_a_measure"

    other = dict(world.spec("open_projects"), connection_id="01900000-0000-7000-8000-000000000000")
    assert (await run(stack, admin, other)).status_code == 404  # unknown / other-tenant connection

    bad_value = await run(stack, admin, world.spec("open_projects", filters=[
        {"ref": "field:projects.percent_complete", "op": ">", "values": ["fifty"]}]))
    assert bad_value.status_code == 422
    bad_ref = await run(stack, admin, world.spec("open_projects", breakdowns=[{"ref": "dimension:region; DROP"}]))
    assert bad_ref.status_code == 422
    too_many = await run(stack, admin, world.spec("open_projects", limit=10_001))
    assert too_many.status_code == 422


async def test_one_database_per_widget(stack: httpx.AsyncClient, world: World, tpg_source: dict[str, Any]) -> None:
    from pmo_testkit import create_connection

    admin = world.tokens["admin"]
    other = await create_connection(stack, admin, tpg_source, name="Second source")
    await stack.post(f"/api/v1/connections/{other}/test", headers=h(admin))
    resp = await run(stack, admin, dict(world.spec("open_projects"), connection_id=other))
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "one_database_per_widget"


# --------------------------------------------------------------------------- limits, as-of, executions (US-021)


async def test_row_cap_truncation_and_boundary(stack: httpx.AsyncClient, world: World,
                                                owner_conn: psycopg.Connection) -> None:
    admin = world.tokens["admin"]
    conn = (await stack.get(f"/api/v1/connections/{world.connection_id}", headers=h(admin))).json()
    resp = await stack.patch(f"/api/v1/connections/{world.connection_id}", headers=h(admin, **{"If-Match": conn["etag"]}),
                             json={"row_cap": 6})
    assert resp.status_code == 200, resp.text
    # Six regions with a cap of 6: exactly the cap is not truncated.
    exact = (await run(stack, admin, world.spec("total_projects", breakdowns=[{"ref": "dimension:region"}]))).json()
    assert exact["row_count"] == 6 and exact["truncated"] is False
    many = (await run(stack, admin, world.spec("total_projects", breakdowns=[
        {"ref": "field:projects.project_number"}]))).json()
    assert many["row_count"] == 6 and many["truncated"] is True
    assert many["notes"] == ["Showing first 6 rows (truncated)"]
    top = (await run(stack, admin, world.spec("total_projects", breakdowns=[{"ref": "field:projects.project_number"}],
                                              limit=3))).json()
    assert top["row_count"] == 3 and top["truncated"] is False
    over = await run(stack, admin, world.spec("total_projects", limit=7))
    assert over.status_code == 422

    rows = owner_conn.execute(
        "SELECT surface, scope_hash, statement_hash, statement_text, row_count, truncated, outcome "
        "FROM ops.query_executions WHERE truncated").fetchall()
    assert rows and all(r[0] == "preview" and len(r[1]) == 64 and len(r[2]) == 64 and r[3] is None for r in rows)


async def test_result_cache_is_scope_keyed_and_invalidated(stack: httpx.AsyncClient, world: World) -> None:
    admin, nomsa, karim = world.tokens["admin"], world.tokens["nomsa"], world.tokens["karim"]
    spec = world.spec("open_projects")
    first = (await run(stack, nomsa, spec)).json()
    second = (await run(stack, nomsa, spec)).json()
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert second["as_of"] == first["as_of"]  # cached mode shows the fill time (RULE-018)
    other = (await run(stack, karim, spec)).json()
    assert other["cache_hit"] is False and other["rows"] != first["rows"]

    # Policy change invalidates.
    policies = (await stack.get(f"/api/v1/admin/scope-policies?connection_id={world.connection_id}",
                                headers=h(admin))).json()["items"]
    cio_policy = next(p for p in policies if p["role"] == "cio" and p["entity"] == "projects")
    await stack.patch(f"/api/v1/admin/scope-policies/{cio_policy['id']}", headers=h(admin),
                      json={"description": "Own CIO (edited)"})
    assert (await run(stack, nomsa, spec)).json()["cache_hit"] is False
    assert (await run(stack, nomsa, spec)).json()["cache_hit"] is True
    # Refresh now invalidates.
    refreshed = await stack.post(f"/api/v1/connections/{world.connection_id}/refresh", headers=h(admin))
    assert refreshed.status_code == 200 and refreshed.json()["invalidated"] >= 1
    assert (await run(stack, nomsa, spec)).json()["cache_hit"] is False
    # Opting out of the cache always reads the source.
    assert (await run(stack, nomsa, spec, use_cache=False)).json()["cache_hit"] is False


async def test_freshness_modes(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    conn = (await stack.get(f"/api/v1/connections/{world.connection_id}", headers=h(admin))).json()
    assert conn["freshness"]["mode"] == "live"
    resp = await stack.patch(f"/api/v1/connections/{world.connection_id}", headers=h(admin, **{"If-Match": conn["etag"]}),
                             json={"freshness": {"mode": "source_asof", "declared_at": "2026-09-30T06:00:00+00:00"}})
    assert resp.status_code == 200, resp.text
    result = (await run(stack, admin, world.spec("open_projects"), use_cache=False)).json()
    assert result["as_of"].startswith("2026-09-30T06:00:00") and result["freshness"] == "source_asof"

    conn = (await stack.get(f"/api/v1/connections/{world.connection_id}", headers=h(admin))).json()
    resp = await stack.patch(f"/api/v1/connections/{world.connection_id}", headers=h(admin, **{"If-Match": conn["etag"]}),
                             json={"freshness": {"mode": "source_asof", "entity": "projects", "field": "modified_on"}})
    assert resp.status_code == 200
    newest = max(p["modified_on"] for p in oracle.PROJECTS)
    result = (await run(stack, admin, world.spec("open_projects"), use_cache=False)).json()
    assert result["as_of"].startswith(newest.isoformat()[:19])

    conn = (await stack.get(f"/api/v1/connections/{world.connection_id}", headers=h(admin))).json()
    bad = await stack.patch(f"/api/v1/connections/{world.connection_id}", headers=h(admin, **{"If-Match": conn["etag"]}),
                            json={"freshness": {"mode": "cached", "cache_ttl_minutes": 2000}})
    assert bad.status_code == 400


async def test_disabled_connection_refuses_queries(stack: httpx.AsyncClient, world: World) -> None:
    admin = world.tokens["admin"]
    assert (await stack.post(f"/api/v1/connections/{world.connection_id}/disable", headers=h(admin))).status_code == 200
    resp = await run(stack, admin, world.spec("open_projects"))
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "source_unreachable"
    enabled = await stack.post(f"/api/v1/connections/{world.connection_id}/enable", headers=h(admin))
    assert enabled.json()["status"] == "connected"
    assert (await run(stack, admin, world.spec("open_projects"))).status_code == 200


async def test_scope_preview_counts_rows(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection) -> None:
    await run(stack, world.tokens["nomsa"], world.spec("open_projects"))  # creates the user
    nomsa = {p["project_id"] for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei"}
    user_id = owner_conn.execute("SELECT id FROM iam.users WHERE display_name = 'Nomsa Osei'").fetchone()[0]
    resp = await stack.post(f"/api/v1/admin/scope-policies/preview?connection_id={world.connection_id}"
                            f"&user_id={user_id}", headers=h(world.tokens["admin"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows_in_scope"] == {
        "project_requests": sum(1 for r in oracle.REQUESTS if r["cio"] == "Nomsa Osei"),
        "projects": oracle.count("total_projects", cio="Nomsa Osei"),
        # The activity tables follow their project's scope (US-155).
        **{t: sum(1 for r in rows if r["project_id"] in nomsa) for t, rows in oracle.ACTIVITY.items()}}


async def test_scope_policy_fields_checked_against_model(stack: httpx.AsyncClient, world: World) -> None:
    resp = await stack.post("/api/v1/admin/scope-policies", headers=h(world.tokens["admin"]), json={
        "connection_id": world.connection_id, "entity": "projects", "role": "executive", "description": "bad",
        "predicates": [{"field": "no_such_field", "op": "=", "values": ["x"]}]})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "field_not_in_model"


async def test_bulk_scope_policies_checked_against_model(stack: httpx.AsyncClient, world: World) -> None:
    """US-172: every entity and the condition's field on each must be in the approved model; one error names all."""
    admin = h(world.tokens["admin"])
    before = len((await stack.get(f"/api/v1/admin/scope-policies?connection_id={world.connection_id}",
                                  headers=admin)).json()["items"])
    resp = await stack.post("/api/v1/admin/scope-policies/bulk", headers=admin, json={
        "connection_id": world.connection_id, "entities": ["projects", "project_requests", "no_such_entity"],
        "roles": ["executive"], "description": "Region: KSA",
        "predicates": [{"field": "region", "op": "in", "values": ["KSA"]}]})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "field_not_in_model"
    details = resp.json()["error"]["details"]
    assert details["entities"] == ["no_such_entity"]
    after = len((await stack.get(f"/api/v1/admin/scope-policies?connection_id={world.connection_id}",
                                 headers=admin)).json()["items"])
    assert after == before


async def test_injection_strings_are_only_ever_values(stack: httpx.AsyncClient, world: World) -> None:
    """Hostile filter values travel as bound parameters: refused as unknown values, never executed."""
    from security_corpus import injection_values

    admin = world.tokens["admin"]
    for payload in injection_values()[:40]:
        resp = await run(stack, admin, world.spec("open_projects", filters=[
            {"ref": "field:projects.name", "op": "=", "values": [payload]}]))
        assert resp.status_code == 200, (payload, resp.text)
        assert resp.json()["rows"] == [[0]]
    # The source is intact.
    assert value(await run(stack, admin, world.spec("total_projects"), use_cache=False)) == 100


async def test_policy_form_lists_the_approved_models_entities(stack: httpx.AsyncClient, world: World) -> None:
    """The New scope policy form picks entities and fields from the approved model, not the admin's own scope."""
    url = f"/api/v1/admin/scope-policies/entities?connection_id={world.connection_id}"
    resp = await stack.get(url, headers=h(world.tokens["admin"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_version_id"] == world.version_id
    projects = next(e for e in body["entities"] if e["key"] == "projects")
    assert projects["table"].endswith("projects")
    assert {"region", "cio"} <= {f["key"] for f in projects["fields"]}
    assert (await stack.get(url, headers=h(world.tokens["nomsa"]))).status_code == 403
