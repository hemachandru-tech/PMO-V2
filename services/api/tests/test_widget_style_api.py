"""ENH-023: widget filters (US-221), card colour (US-223) and chart colours (US-224) on a report widget."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pmo_oracle as oracle
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_authoring_api import items, new_report

pytestmark = [needs_db, pytest.mark.integration]

KSA = [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def _figure(stack: httpx.AsyncClient, world: World) -> tuple[str, str, str]:
    token = world.tokens["creator"]
    report = await new_report(stack, token, "Styled")
    added = await stack.post(f"/api/v1/reports/{report['id']}/items", headers=h(token), json={
        "kind": "measure", "measure": "measure:open_projects", "connection_id": world.connection_id})
    assert added.status_code == 201, added.text
    item = next(i for i in items(added.json()) if i["id"] == added.json()["item_id"])
    return report["id"], item["id"], item["widget_id"]


def _visual(report: dict[str, Any], item_id: str) -> dict[str, Any]:
    return dict(next(i for i in items(report) if i["id"] == item_id)["spec"]["visual"])


async def test_widget_filters_fixed_and_for_readers(stack: httpx.AsyncClient, world: World) -> None:
    """US-221: the Creator offers fields readers can filter the widget by; a reader's pick narrows only that widget
    (also with page filters off) and its drill; fields not offered, unknown or out of reach are refused; the
    Creator's fixed filters come back with their labels."""
    token = world.tokens["creator"]
    report_id, item_id, widget_id = await _figure(stack, world)
    url = f"/api/v1/reports/{report_id}/items/{item_id}"
    run_url = f"/api/v1/widgets/{widget_id}/run"
    plain = (await stack.post(run_url, headers=h(token), json={})).json()
    assert plain["controls"] == [] and plain["fixed_filters"] == []
    # Not offered yet: refused.
    early = await stack.post(run_url, headers=h(token), json={"widget_filters": KSA})
    assert early.status_code == 400 and "no filter for that field" in early.json()["error"]["message"]

    offered = await stack.patch(url, headers=h(token), json={"controls": ["dimension:region"],
                                                            "follow_page_filters": False})
    assert offered.status_code == 200, offered.text
    assert _visual(offered.json(), item_id)["controls"] == ["dimension:region"]
    run = (await stack.post(run_url, headers=h(token), json={})).json()
    assert run["controls"] == [{"ref": "dimension:region", "label": "Region"}]
    picked = (await stack.post(run_url, headers=h(token), json={"widget_filters": KSA})).json()
    assert picked["rows"] == [[oracle.count("open_projects", regions={"KSA"})]]
    assert picked["kpi"]["value"] == oracle.count("open_projects", regions={"KSA"})
    drill = await stack.post(f"/api/v1/widgets/{widget_id}/drill", headers=h(token), json={"widget_filters": KSA})
    assert drill.status_code == 200, drill.text
    assert drill.json()["total"] == oracle.count("open_projects", regions={"KSA"})
    other = await stack.post(run_url, headers=h(token), json={"widget_filters": [
        {"ref": "dimension:priority", "op": "=", "values": ["High"]}]})
    assert other.status_code == 400
    ranged = await stack.post(run_url, headers=h(token), json={"widget_filters": [
        {"ref": "dimension:region", "op": "!=", "values": ["KSA"]}]})
    assert ranged.status_code == 400

    unknown = await stack.patch(url, headers=h(token), json={"controls": ["dimension:no_such_thing"]})
    assert unknown.status_code == 400 and "no field" in unknown.json()["error"]["message"]
    too_many = await stack.patch(url, headers=h(token), json={"controls": [
        "dimension:region", "dimension:priority", "dimension:request_region", "field:projects.cio"]})
    assert too_many.status_code == 400

    fixed = await stack.patch(url, headers=h(token), json={"filters": [
        {"ref": "dimension:priority", "op": "in", "values": ["High", "Critical"]}]})
    assert fixed.status_code == 200, fixed.text
    run = (await stack.post(run_url, headers=h(token), json={})).json()
    assert run["fixed_filters"] == [{"ref": "dimension:priority", "label": "Priority", "op": "in",
                                     "values": ["High", "Critical"]}]
    assert run["controls"] == [{"ref": "dimension:region", "label": "Region"}]  # kept by the filter edit
    cleared = await stack.patch(url, headers=h(token), json={"controls": None})
    assert "controls" not in {k for k, v in _visual(cleared.json(), item_id).items() if v}
    undone = await stack.post(f"/api/v1/reports/{report_id}/undo", headers=h(token))
    assert _visual(undone.json(), item_id)["controls"] == ["dimension:region"]


async def test_card_and_chart_colours(stack: httpx.AsyncClient, world: World) -> None:
    """US-223 / US-224: a figure's card takes a swatch (brand and dark fill only a figure's card); chart colours are
    refused on a figure; both are kept across a new chart type when it can show them and reach the run."""
    token = world.tokens["creator"]
    report_id, item_id, widget_id = await _figure(stack, world)
    url = f"/api/v1/reports/{report_id}/items/{item_id}"
    run_url = f"/api/v1/widgets/{widget_id}/run"
    brand = await stack.patch(url, headers=h(token), json={"card": "brand"})
    assert brand.status_code == 200, brand.text
    assert _visual(brand.json(), item_id)["card"] == "brand"
    run = (await stack.post(run_url, headers=h(token), json={})).json()
    assert run["viz"]["chart_spec"]["card"] == "brand"
    bad = await stack.patch(url, headers=h(token), json={"card": "pink"})
    assert bad.status_code == 400
    on_figure = await stack.patch(url, headers=h(token), json={"colours": {"scheme": "warm"}})
    assert on_figure.status_code == 400 and "no chart colours" in on_figure.json()["error"]["message"]

    # A tinted card survives a new chart type; brand does not fit a chart, so the move to bars drops it.
    as_bars = await stack.patch(url, headers=h(token), json={
        "visual": "ranked_bar", "ops": [{"op": "add_breakdown", "ref": "dimension:region"}]})
    assert as_bars.status_code == 200, as_bars.text
    assert "card" not in _visual(as_bars.json(), item_id)
    styled = await stack.patch(url, headers=h(token), json={
        "card": "accent", "colours": {"scheme": "cool", "overrides": {"KSA": "status-danger"}}})
    assert styled.status_code == 200, styled.text
    v = _visual(styled.json(), item_id)
    assert v["card"] == "accent" and v["colours"] == {"scheme": "cool", "overrides": {"KSA": "status-danger"}}
    spec = (await stack.post(run_url, headers=h(token), json={})).json()["viz"]["chart_spec"]
    assert spec["card"] == "accent" and spec["colours"]["overrides"] == {"KSA": "status-danger"}
    filled = await stack.patch(url, headers=h(token), json={"card": "dark"})
    assert filled.status_code == 400 and "single figure" in filled.json()["error"]["message"]
    swatch = await stack.patch(url, headers=h(token), json={"colours": {"scheme": "cool", "overrides": {"KSA": "#ff0000"}}})
    assert swatch.status_code == 400
    as_donut = await stack.patch(url, headers=h(token), json={"visual": "donut"})
    assert as_donut.status_code == 200, as_donut.text
    assert _visual(as_donut.json(), item_id)["colours"]["scheme"] == "cool"  # kept across chart types
    reset = await stack.patch(url, headers=h(token), json={"card": "default", "colours": None})
    v = _visual(reset.json(), item_id)
    assert not v.get("card") and not v.get("colours")
