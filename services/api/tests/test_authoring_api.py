"""Authoring and publishing (P2 core slice: US-060 to US-073, US-088 to US-094, US-105, US-107).

Acceptance: TEST-023 (refine), TEST-024 (pin), TEST-025/TEST-058 (ask about a widget), TEST-026/TEST-027
(plan and build), TEST-028 (canvas layout), TEST-029 (chat edit), TEST-030 (chart guard), TEST-031/TEST-068
(drafts, Undo all), TEST-045 to TEST-049 (compose, checks, publish, versions, share), TEST-057 (audit viewer).
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


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def stream(api: httpx.AsyncClient, token: str, body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resp = await api.get(body["stream_url"], headers=h(token))
    return by_event(parse_sse(resp.text))


async def new_report(api: httpx.AsyncClient, token: str, title: str = "My report") -> dict[str, Any]:
    resp = await api.post("/api/v1/reports", headers=h(token), json={"title": title})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def pin(api: httpx.AsyncClient, token: str, turn_id: str, title: str, visual: str,
              report_id: str | None = None, **extra: Any) -> httpx.Response:
    body: dict[str, Any] = {"from_turn_id": turn_id, "title": title, "visual": visual, **extra}
    if report_id:
        body["target"] = {"report_id": report_id}
    return await api.post("/api/v1/widgets", headers=h(token), json=body)


def items(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in report["sections"] for i in s["items"]]


# --------------------------------------------------------------------------- Ask part 2


async def test_refine_undo_and_compare(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    turn_id, events = await ask(stack, token, "Open projects by region")
    first = by_event(events)["complete"]
    assert first["spec"]["breakdowns"] == [{"ref": "dimension:region"}] and first["can_pin"] is True

    resp = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(token),
                            json={"instruction": "only KSA, as columns"})
    assert resp.status_code == 202, resp.text
    refined = resp.json()
    assert set(refined["changes"]) >= {"Region: KSA", "Shown as columns"}
    done = (await stream(stack, token, refined))["complete"]
    assert {"ref": "dimension:region", "op": "in", "values": ["KSA"]} in done["spec"]["filters"]
    assert done["spec"]["breakdowns"] == [{"ref": "dimension:region"}]  # carried forward (S30)
    assert done["refined_from"] == turn_id and "Region: KSA" in done["filters_text"]

    chips = await stack.post(f"/api/v1/ask/turns/{refined['turn_id']}/refine", headers=h(token),
                             json={"chip_ops": [{"op": "set_limit", "value": 1}]})
    third = (await stream(stack, token, chips.json()))["complete"]
    assert third["spec"]["limit"] == 1 and len(third["spec"]["filters"]) == 1

    # Undo is repeatable: back to the KSA answer, then to the first answer.
    u1 = await stack.post(f"/api/v1/ask/turns/{chips.json()['turn_id']}/undo", headers=h(token))
    back1 = (await stream(stack, token, u1.json()))["complete"]
    assert "limit" not in back1["spec"] and back1["spec"]["filters"]
    u2 = await stack.post(f"/api/v1/ask/turns/{u1.json()['turn_id']}/undo", headers=h(token))
    back2 = (await stream(stack, token, u2.json()))["complete"]
    assert back2["spec"]["filters"] == first["spec"]["filters"]
    nothing = await stack.post(f"/api/v1/ask/turns/{u2.json()['turn_id']}/undo", headers=h(token))
    assert nothing.status_code == 409

    # Rule-changing requests are explained and redirected; nothing runs.
    rule = await stack.post(f"/api/v1/ask/turns/{turn_id}/refine", headers=h(token),
                            json={"instruction": "count cancelled projects too"})
    assert rule.status_code == 202 and rule.json()["turn_id"] is None
    assert "personal field" in rule.json()["message"]

    # Compare with the previous period needs a range; with one, the narrative states the change.
    t2, _ = await ask(stack, token, "Total projects")
    no_range = await stack.post(f"/api/v1/ask/turns/{t2}/refine", headers=h(token),
                                json={"chip_ops": [{"op": "compare_previous"}]})
    assert no_range.status_code == 400 and "time range" in no_range.json()["error"]["message"]
    ranged = await stack.post(f"/api/v1/ask/turns/{t2}/refine", headers=h(token),
                              json={"instruction": "last 12 months, compare with the previous period"})
    assert ranged.status_code == 202, ranged.text
    cmp_done = (await stream(stack, token, ranged.json()))
    assert "complete" in cmp_done, cmp_done.get("error")
    assert cmp_done["complete"]["comparison"] is not None
    # US-186: the comparison is told once — as the cited change insight, or as a sentence when no change fact fits.
    story = cmp_done["narrative"]
    change = [x for x in story["insights"] if x["type"] == "change"]
    if change:
        assert any(change[0]["id"] in c["facts"] for c in story["cited"])
        assert not any(s["text"].startswith("Previous period (") for s in story["sentences"])
    else:
        assert story["sentences"][0]["text"].startswith("Previous period (")


async def test_pin_guard_dedupe_and_viewer(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    turn_id, _ = await ask(stack, token, "Open projects by region")
    report = await new_report(stack, token, "Delivery")
    # TEST-030: a trend over regions misleads → refused with the reason and one alternative.
    bad = await pin(stack, token, turn_id, "Open by region", "trend", report["id"])
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "visual_not_suitable"
    assert bad.json()["error"]["details"]["alternative"] == "ranked_bar"
    assert "Ranked bars by region" == bad.json()["error"]["details"]["alternative_label"]
    nope = await pin(stack, token, turn_id, "Open by region", "map", report["id"])
    assert nope.status_code == 422 and "Not available yet" in nope.json()["error"]["message"]

    suitable = await stack.post("/api/v1/visuals/suitable", headers=h(token),
                                json={"spec": by_event((await ask(stack, token, "Open projects by region"))[1])
                                      ["complete"]["spec"]})
    assert "ranked_bar" in suitable.json()["suitable"]
    assert any(d["visual"] == "trend" and d["reason"] for d in suitable.json()["disabled"])

    ok = await stack.post(
        "/api/v1/widgets", headers=h(token, **{"Idempotency-Key": "pin-1"}),
        json={"from_turn_id": turn_id, "title": "Open by region", "visual": "ranked_bar",
              "target": {"report_id": report["id"]}})
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["badges"] == ["certified"] and body["placement"]["w"] == 6 and body["placement"]["h"] == 4
    assert "rows" not in body and body["spec"]["visual"] == {"type": "ranked_bar"}
    replayed = await stack.post("/api/v1/widgets", headers=h(token, **{"Idempotency-Key": "pin-1"}),
                                json={"from_turn_id": turn_id, "title": "Open by region", "visual": "ranked_bar",
                                      "target": {"report_id": report["id"]}})
    assert replayed.json()["id"] == body["id"]
    # MSG-023: the same definition onto the same report.
    again = await pin(stack, token, turn_id, "Open by region", "ranked_bar", report["id"])
    assert again.status_code == 409 and again.json()["error"]["message"] == "This widget is already on this report."
    figure_turn, _ = await ask(stack, token, "How many open projects?")
    fig = (await pin(stack, token, figure_turn, "Open projects", "figure", report["id"])).json()
    assert (fig["placement"]["w"], fig["placement"]["h"]) == (3, 2) and fig["placement"]["x"] == 6
    lib = (await stack.get("/api/v1/widgets", headers=h(token))).json()["items"]
    assert {w["title"] for w in lib} == {"Open by region", "Open projects"}

    viewer_turn, _ = await ask(stack, world.tokens["nomsa"], "Open projects by region")
    denied = await pin(stack, world.tokens["nomsa"], viewer_turn, "Mine", "ranked_bar")
    assert denied.status_code == 403


async def test_ask_about_a_widget(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["nomsa"]
    d = next(x for x in (await stack.get("/api/v1/published", headers=h(token))).json()["items"]
             if x["title"] == "PMO — Projects")
    view = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(token))).json()
    w = next(x for t in view["tabs"] for x in t["widgets"] if x["title"] == "Open projects by region")
    page = [{"ref": "dimension:priority", "op": "=", "values": ["Critical"]}]
    _, events = await ask(stack, token, "top 2 only", context={"widget_id": w["id"], "page_filters": page})
    done = by_event(events)["complete"]
    assert done["widget"]["title"] == "Open projects by region" and done["spec"]["limit"] == 2
    assert {"ref": "dimension:priority", "op": "=", "values": ["Critical"]} in done["spec"]["filters"]
    assert done["scope_text"] and done["as_of"] and "Priority: Critical" in done["filters_text"]
    assert done["can_pin"] is False  # Viewers ask but cannot pin (TEST-058)
    _, why = await ask(stack, token, "Why is this so high?", context={"widget_id": w["id"]})
    ev = by_event(why)
    texts = [s["text"] for s in ev["narrative"]["sentences"]]
    assert any("not why" in t for t in texts) and any(t.startswith("Open projects:") for t in texts)
    _, rule = await ask(stack, token, "Should count on hold projects too", context={"widget_id": w["id"]})
    assert by_event(rule)["error"]["code"] == "rule_change"


# --------------------------------------------------------------------------- reports and the canvas


async def test_layout_undo_and_undo_all(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    report = await new_report(stack, token)
    t1, _ = await ask(stack, token, "Open projects by region")
    t2, _ = await ask(stack, token, "How many open projects?")
    await pin(stack, token, t1, "By region", "ranked_bar", report["id"])
    await pin(stack, token, t2, "Open", "figure", report["id"])
    note = await stack.post(f"/api/v1/reports/{report['id']}/items", headers=h(token),
                            json={"kind": "text_note", "text": "<script>x</script>Read **me**"})
    assert note.status_code == 201
    current = (await stack.get(f"/api/v1/reports/{report['id']}", headers=h(token)))
    etag, rep = current.headers["etag"], current.json()
    assert [i["text"] for i in items(rep) if i["kind"] == "text_note"] == ["xRead **me**"]
    by_region = next(i for i in items(rep) if i.get("title") == "By region")
    sec = rep["sections"][0]["id"]

    # Overlap and out-of-grid layouts are rejected; stale revisions get 412 (TEST-028).
    overlap = [{"item_id": i["id"], "section_id": sec, "x": 0, "y": 0, "w": 6, "h": 4} for i in items(rep)]
    bad = await stack.put(f"/api/v1/reports/{report['id']}/layout", headers=h(token, **{"If-Match": etag}),
                          json={"items": overlap})
    assert bad.status_code == 400 and "overlaps" in str(bad.json()["error"]["details"])
    wide = await stack.put(f"/api/v1/reports/{report['id']}/layout", headers=h(token, **{"If-Match": etag}),
                           json={"items": [{"item_id": by_region["id"], "section_id": sec, "x": 4, "y": 10, "w": 9,
                                            "h": 4}]})
    assert wide.status_code == 400
    moved = await stack.put(f"/api/v1/reports/{report['id']}/layout", headers=h(token, **{"If-Match": etag}),
                            json={"items": [{"item_id": by_region["id"], "section_id": sec, "x": 0, "y": 20, "w": 12,
                                             "h": 4}]})
    assert moved.status_code == 200, moved.text
    after = next(i for i in items(moved.json()) if i["id"] == by_region["id"])
    assert after["w"] == 12 and after["y"] < 20  # compacted upwards
    stale = await stack.put(f"/api/v1/reports/{report['id']}/layout", headers=h(token, **{"If-Match": etag}),
                            json={"items": []})
    assert stale.status_code == 412

    # Chart type from the toolbar goes through the guard.
    guard = await stack.patch(f"/api/v1/reports/{report['id']}/items/{by_region['id']}", headers=h(token),
                              json={"visual": "trend"})
    assert guard.status_code == 422 and guard.json()["error"]["code"] == "visual_not_suitable"
    renamed = await stack.patch(f"/api/v1/reports/{report['id']}/items/{by_region['id']}", headers=h(token),
                                json={"title": "Open by region"})
    assert any(i.get("title") == "Open by region" for i in items(renamed.json()))
    undone = (await stack.post(f"/api/v1/reports/{report['id']}/undo", headers=h(token))).json()
    assert undone["undone"] == "Renamed a widget" and any(i.get("title") == "By region" for i in items(undone))
    # Never published: Undo all empties the report (TEST-068).
    emptied = (await stack.post(f"/api/v1/reports/{report['id']}/undo-all", headers=h(token))).json()
    assert items(emptied) == [] and len(emptied["sections"]) == 1
    restored = (await stack.post(f"/api/v1/reports/{report['id']}/undo", headers=h(token))).json()
    assert len(items(restored)) == 3
    other = await stack.get(f"/api/v1/reports/{report['id']}", headers=h(world.tokens["admin"]))
    assert other.status_code == 404


async def test_add_certified_measure_from_the_canvas_library(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    report = await new_report(stack, token)
    url = f"/api/v1/reports/{report['id']}/items"
    body = {"kind": "measure", "measure": "measure:open_projects", "connection_id": world.connection_id, "x": 6,
            "y": 0}
    added = await stack.post(url, headers=h(token), json=body)
    assert added.status_code == 201, added.text
    item = next(i for i in items(added.json()) if i["id"] == added.json()["item_id"])
    assert (item["visual"], item["badges"], item["x"]) == ("figure", ["certified"], 6)
    assert (await stack.post(url, headers=h(token), json=body)).status_code == 409  # already on this report
    library = (await stack.get("/api/v1/widgets", headers=h(token))).json()["items"]
    assert any(w["id"] == item["widget_id"] for w in library)
    unknown = await stack.post(url, headers=h(token), json={**body, "measure": "measure:no_such_measure"})
    assert unknown.status_code == 404
    missing = await stack.post(url, headers=h(token), json={"kind": "measure", "measure": "measure:open_projects"})
    assert missing.status_code == 400


async def test_a_widget_on_two_reports_changes_only_where_it_is_edited(stack: httpx.AsyncClient, world: World) -> None:
    """BUG-020: the same widget placed on two reports; a chart, title or tone change made on one report goes to a copy
    for that report, so the other keeps its widget. Undo puts the shared widget back; a widget used once is edited in
    place."""
    token = world.tokens["creator"]
    body = {"kind": "measure", "measure": "measure:open_projects", "connection_id": world.connection_id}
    reports, placed = [], []
    for title in ("Report A", "Report B"):
        report = await new_report(stack, token, title)
        added = await stack.post(f"/api/v1/reports/{report['id']}/items", headers=h(token), json=body)
        assert added.status_code == 201, added.text
        reports.append(report)
        placed.append(next(i for i in items(added.json()) if i["id"] == added.json()["item_id"]))
    shared = placed[0]["widget_id"]
    assert placed[1]["widget_id"] == shared  # the library's widget, placed twice

    url = f"/api/v1/reports/{reports[0]['id']}/items/{placed[0]['id']}"
    edited = await stack.patch(url, headers=h(token), json={"title": "Open projects (A)", "tone": {"warning": 1}})
    assert edited.status_code == 200, edited.text
    a = next(i for i in items(edited.json()) if i["id"] == placed[0]["id"])
    assert a["widget_id"] != shared and a["title"] == "Open projects (A)" and a["spec"]["visual"]["tone"]
    b_report = (await stack.get(f"/api/v1/reports/{reports[1]['id']}", headers=h(token))).json()
    b = next(i for i in items(b_report) if i["id"] == placed[1]["id"])
    assert b["widget_id"] == shared and b["title"] == placed[1]["title"] and "tone" not in b["spec"]["visual"]

    # The copy is used once now: further edits change it in place.
    again = await stack.patch(url, headers=h(token), json={"title": "Open projects (A2)"})
    assert next(i for i in items(again.json()) if i["id"] == placed[0]["id"])["widget_id"] == a["widget_id"]

    for _ in range(2):
        undone = await stack.post(f"/api/v1/reports/{reports[0]['id']}/undo", headers=h(token))
        assert undone.status_code == 200, undone.text
    back = next(i for i in items(undone.json()) if i["id"] == placed[0]["id"])
    assert back["widget_id"] == shared and back["title"] == placed[0]["title"]


async def test_figure_tone_threshold_and_meter_note(stack: httpx.AsyncClient, world: World) -> None:
    """US-157: a figure's tone threshold is kept in ``spec.visual`` and the run returns the tone in words; a note or
    a tone on the wrong visual, or thresholds in the wrong order, are refused; other edits keep the threshold."""
    token = world.tokens["creator"]
    report = await new_report(stack, token, "Tone")
    added = await stack.post(f"/api/v1/reports/{report['id']}/items", headers=h(token), json={
        "kind": "measure", "measure": "measure:open_projects", "connection_id": world.connection_id})
    assert added.status_code == 201, added.text
    item = next(i for i in items(added.json()) if i["id"] == added.json()["item_id"])
    url = f"/api/v1/reports/{report['id']}/items/{item['id']}"
    value = (await stack.post(f"/api/v1/widgets/{item['widget_id']}/run", headers=h(token), json={})
             ).json()["kpi"]["value"]
    toned = await stack.patch(url, headers=h(token), json={"tone": {"warning": value, "critical": value + 10}})
    assert toned.status_code == 200, toned.text
    spec = next(i for i in items(toned.json()) if i["id"] == item["id"])["spec"]
    assert spec["visual"] == {"type": "figure", "tone": {"warning": value, "critical": value + 10,
                                                          "higher_is_worse": True}}
    run = (await stack.post(f"/api/v1/widgets/{item['widget_id']}/run", headers=h(token), json={})).json()
    assert run["kpi"]["tone"] == {"tone": "warning", "label": "Warning", "rule": f"Warning at or above {value}"}
    assert run["kpi"]["definition"] == "Projects not yet closed: Project Created, Active and On Hold."
    # Renaming leaves the threshold; the wrong order, or a meter note on a figure, is refused.
    await stack.patch(url, headers=h(token), json={"title": "Open projects (toned)"})
    run = (await stack.post(f"/api/v1/widgets/{item['widget_id']}/run", headers=h(token), json={})).json()
    assert run["kpi"]["tone"]["tone"] == "warning"
    wrong = await stack.patch(url, headers=h(token), json={"tone": {"warning": 10, "critical": 5}})
    assert wrong.status_code == 400 and "past the warning" in wrong.json()["error"]["message"]
    note = await stack.patch(url, headers=h(token), json={"note": "Projects with a code"})
    assert note.status_code == 400 and note.json()["error"]["message"] == "Only a meter has a note."
    cleared = await stack.patch(url, headers=h(token), json={"tone": None})
    assert next(i for i in items(cleared.json()) if i["id"] == item["id"])["spec"]["visual"] == {"type": "figure"}
    run = (await stack.post(f"/api/v1/widgets/{item['widget_id']}/run", headers=h(token), json={})).json()
    assert run["kpi"]["tone"] is None


async def test_text_blocks_reporting_date_and_detail_table(stack: httpx.AsyncClient, world: World) -> None:
    """US-158: callouts and footnotes are report items (added, edited, undone, published and shown to readers); the
    report and the dashboard say "Data as of" the reporting date; a detail table lists the population's records with
    its columns and sort, as many as the figure counts."""
    token, admin = world.tokens["creator"], world.tokens["admin"]
    report = await new_report(stack, token, "Delivery detail")
    assert report["reporting_date"] is None  # no widget yet, so no model to take the date from
    base = f"/api/v1/reports/{report['id']}/items"
    callout = await stack.post(base, headers=h(token), json={
        "kind": "callout", "title": "Read me", "text": "Counts **open** projects.", "level": "warning"})
    assert callout.status_code == 201, callout.text
    c = next(i for i in items(callout.json()) if i["id"] == callout.json()["item_id"])
    assert (c["kind"], c["title"], c["text"], c["level"], c["w"]) == ("callout", "Read me", "Counts **open** projects.",
                                                                     "warning", 12)
    figure = await stack.post(base, headers=h(token), json={
        "kind": "measure", "measure": "measure:overdue_go_lives", "connection_id": world.connection_id})
    assert figure.status_code == 201, figure.text
    assert figure.json()["reporting_date"] == oracle.AS_OF.isoformat()
    footnote = await stack.post(base, headers=h(token), json={"kind": "footnote", "text": "Population: in delivery."})
    f = next(i for i in items(footnote.json()) if i["id"] == footnote.json()["item_id"])
    assert f["kind"] == "footnote" and f["w"] == 12 and f["y"] >= max(i["y"] + i["h"] for i in items(footnote.json())
                                                                       if i["id"] != f["id"])
    # Edits: a callout's title, level and text; a footnote has no title; empty text is refused; undo restores.
    url = f"{base}/{c['id']}"
    edited = await stack.patch(url, headers=h(token), json={"title": "Scope", "level": "info", "text": "Edited."})
    assert edited.status_code == 200, edited.text
    c2 = next(i for i in items(edited.json()) if i["id"] == c["id"])
    assert (c2["title"], c2["level"], c2["text"]) == ("Scope", "info", "Edited.")
    untitled = await stack.patch(url, headers=h(token), json={"title": ""})
    assert next(i for i in items(untitled.json()) if i["id"] == c["id"])["title"] is None
    edited = await stack.patch(url, headers=h(token), json={"title": "Scope"})
    bad = await stack.patch(f"{base}/{f['id']}", headers=h(token), json={"level": "warning"})
    assert bad.status_code == 400 and "Only callouts" in bad.json()["error"]["message"]
    empty = await stack.patch(url, headers=h(token), json={"text": "  <b></b> "})
    assert empty.status_code == 400
    undone = await stack.post(f"/api/v1/reports/{report['id']}/undo", headers=h(token))
    back = next(i for i in items(undone.json()) if i["id"] == c["id"])
    assert (back["title"], back["level"]) == (None, "info")  # the last change (title "Scope") is undone

    # A detail table: overdue projects with their columns, latest go-live first; as many rows as the figure says.
    widget = next(i for i in items(undone.json()) if i["kind"] == "widget")
    wurl = f"{base}/{widget['id']}"
    detail = {"columns": ["project_number", "name", "planned_deployment_date"],
              "sort": {"field": "planned_deployment_date", "dir": "desc"}, "export_name": "Overdue projects"}
    on_figure = await stack.patch(wurl, headers=h(token), json={"detail": detail})
    assert on_figure.status_code == 400 and on_figure.json()["error"]["message"] == "Only a table lists records."
    unknown = await stack.patch(wurl, headers=h(token), json={"visual": "table", "detail": {"columns": ["nope"]}})
    assert unknown.status_code == 400 and unknown.json()["error"]["details"]["unknown"] == ["nope"]
    count = (await stack.post(f"/api/v1/widgets/{widget['widget_id']}/run", headers=h(token), json={})
             ).json()["kpi"]["value"]
    tabled = await stack.patch(wurl, headers=h(token), json={"visual": "table", "detail": detail})
    assert tabled.status_code == 200, tabled.text
    run = (await stack.post(f"/api/v1/widgets/{widget['widget_id']}/run", headers=h(token), json={})).json()
    names = [col["name"] for col in run["columns"]]
    assert names[0] == "project_id" and run["detail"]["columns"] == detail["columns"]
    assert run["row_count"] == count == len(run["rows"]) and count > 1 and run["viz"]["visual"] == "table"
    dates = [r[names.index("planned_deployment_date")] for r in run["rows"]]
    assert dates == sorted(dates, reverse=True) and all(d < oracle.AS_OF.isoformat() for d in dates)
    assert run["detail"]["export_name"] == "Overdue projects"

    # Published: readers get the callout, the footnote and the reporting date.
    dash = await stack.post("/api/v1/dashboards", headers=h(token), json={
        "title": "Detail", "report_ids": [report["id"]], "layout": "tabs"})
    assert dash.status_code == 201, dash.text
    published = await stack.post(f"/api/v1/dashboards/{dash.json()['id']}/publish", headers=h(admin),
                                 json={"target_roles": ["creator"]})
    assert published.status_code == 201, published.text
    view = (await stack.get(f"/api/v1/published/{dash.json()['id']}", headers=h(token))).json()
    assert view["reporting_date"] == view["tabs"][0]["reporting_date"] == oracle.AS_OF.isoformat()
    shown = {i["kind"]: i for s in view["tabs"][0]["sections"] for i in s["items"]}
    assert (shown["callout"]["title"], shown["callout"]["level"], shown["callout"]["text"]) == (None, "info", "Edited.")
    assert shown["footnote"]["text"] == "Population: in delivery." and "title" not in shown["footnote"]


async def test_plan_edit_and_build(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    resp = await stack.post("/api/v1/reports/plan", headers=h(token, Accept="text/event-stream"), json={
        "request": "Delivery health report for my region with overdue go-lives, budget exposure and team workload"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    events = by_event(parse_sse(resp.text))
    plan = events["plan"]
    assert events["complete"]["plan_id"] == plan["plan_id"]
    titles = {w["title"] for s in plan["sections"] for w in s["widgets"]}
    assert "Overdue go-lives" in titles and {h_["label"] for h_ in plan["headline_figures"]} >= {"Overdue go-lives"}
    assert any("workload" in g["reason"] for g in plan["gaps"])
    assert any(f["ref"] == "dimension:region" for f in plan["page_filters"])
    assert all(w["certified"] for s in plan["sections"] for w in s["widgets"])
    assert {p["widget_ref"] for p in plan["layout_preview"]} == {w["ref"] for s in plan["sections"]
                                                                 for w in s["widgets"]}
    unsupported = (await stack.post("/api/v1/reports/plan", headers=h(token),
                                    json={"request": "Milestone slippage report"})).json()
    assert unsupported["sections"] == [] and unsupported["gaps"]

    # "Overdue go-lives" and "budget" name two report catalogue screens (US-159): their layouts, not the themes.
    keys = [s["key"] for s in plan["sections"]]
    assert keys == ["headline", "named", "bp_delayed_projects", "bp_capex_budget"]  # "named": for my region
    assert plan["sections"][3]["callouts"] and "not overdue" in plan["sections"][2]["footnote"]

    edited = await stack.post(f"/api/v1/reports/plans/{plan['plan_id']}/edit", headers=h(token),
                              json={"instruction": "remove Delayed by portfolio; add on hold"})
    assert edited.status_code == 200, edited.text
    diff = edited.json()["diff"]
    assert "Delayed by portfolio" in diff["removed"] and diff["added"] == ["Projects on hold"]

    built = await stack.post(f"/api/v1/reports/plans/{plan['plan_id']}/build",
                             headers=h(token, **{"Idempotency-Key": "b1"}))
    assert built.status_code == 201, built.text
    results = built.json()["results"]
    assert results and all(r["status"] in ("ok", "empty") for r in results)
    again = await stack.post(f"/api/v1/reports/plans/{plan['plan_id']}/build",
                             headers=h(token, **{"Idempotency-Key": "b1"}))
    assert again.json()["report_id"] == built.json()["report_id"]
    report = (await stack.get(f"/api/v1/reports/{built.json()['report_id']}", headers=h(token))).json()
    widgets_ = [i for i in items(report) if i["kind"] == "widget"]
    assert report["sections"][0]["kind"] == "headline" and len(widgets_) == len(results)
    assert {f["ref"] for f in report["page_filters"]} >= {"dimension:region"}
    # Each blueprint's footnote closes its section; the CAPEX callout opens its own.
    by_title = {s["title"]: s for s in report["sections"]}
    for title in ("Delayed projects", "CAPEX budget vs forecast cost"):
        last = max(by_title[title]["items"], key=lambda i: i["y"])
        assert last["kind"] == "footnote" and last["w"] == 12, by_title[title]["items"]
        assert all(i["y"] + i["h"] <= last["y"] for i in by_title[title]["items"] if i is not last)
    capex = by_title["CAPEX budget vs forecast cost"]["items"]
    callout = next(i for i in capex if i["kind"] == "callout")
    assert (callout["y"], callout["title"]) == (0, "Forecasts are estimates")
    assert all(i["y"] >= 2 for i in capex if i["kind"] == "widget")


async def test_planner_builds_catalogue_reports_from_a_sentence(stack: httpx.AsyncClient, world: World) -> None:
    """US-159: "delayed projects" and "CAPEX" plan the catalogue layouts from certified measures; a detail table
    keeps its columns; the title doesn't claim spend; a screen the data can't support is a named gap."""
    token = world.tokens["creator"]
    plan = (await stack.post("/api/v1/reports/plan", headers=h(token), json={
        "request": "Build me a delayed projects and CAPEX spend report, with milestone slippage"})).json()
    assert plan["title"] == "Delayed projects and CAPEX forecast cost report, with milestone slippage"
    assert plan["notes"] and "no actual spend" in plan["notes"][0]
    assert {"request_part": "Milestone slippage", "reason": "No milestone records in this model"} in plan["gaps"]
    assert [h_["label"] for h_ in plan["headline_figures"]][:2] == ["Overdue go-lives", "Overdue more than 90 days"]
    delayed = next(s for s in plan["sections"] if s["key"] == "bp_delayed_projects")
    table = next(w for w in delayed["widgets"] if w["visual"] == "table")
    assert table["spec"]["visual"]["detail"]["export_name"] == "Delayed projects"
    built = await stack.post(f"/api/v1/reports/plans/{plan['plan_id']}/build", headers=h(token))
    assert built.status_code == 201, built.text
    results = {r["title"]: r for r in built.json()["results"]}
    assert all(r["status"] in ("ok", "empty") for r in results.values()), results
    run = await stack.post(f"/api/v1/widgets/{results['Delayed projects']['widget_id']}/run", headers=h(token),
                           json={})
    assert run.status_code == 200, run.text
    assert run.json()["detail"]["columns"][:2] == ["project_number", "name"]


async def test_chat_edit_preview_apply_undo(stack: httpx.AsyncClient, world: World) -> None:
    token = world.tokens["creator"]
    report = await new_report(stack, token)
    t1, _ = await ask(stack, token, "Open projects by region")
    t2, _ = await ask(stack, token, "Open projects by current state")
    t3, _ = await ask(stack, token, "How many open projects?")
    await pin(stack, token, t1, "Open by region", "ranked_bar", report["id"])
    await pin(stack, token, t2, "Open by state", "ranked_bar", report["id"])
    await pin(stack, token, t3, "Open projects", "figure", report["id"])
    url = f"/api/v1/reports/{report['id']}/chat-edit"
    ambiguous = (await stack.post(url, headers=h(token), json={"instruction": "remove open by"})).json()
    assert ambiguous["clarification"] and len(ambiguous["clarification"]["options"]) == 2
    preview = (await stack.post(url, headers=h(token), json={
        "instruction": "make open by region full width and show open by state as trend, then move open projects to "
                       "the top"})).json()
    ops = [c["op"] for c in preview["changes"]]
    assert ops == ["resize", "move", "refused"] and preview["preview_id"]
    refused = preview["changes"][-1]
    assert refused["reason"].startswith("Trend needs a date") and refused["alternative"]
    unchanged = (await stack.get(f"/api/v1/reports/{report['id']}", headers=h(token))).json()
    assert all(i["w"] != 12 for i in items(unchanged))  # nothing changes before Apply
    applied = await stack.post(f"{url}/{preview['preview_id']}/apply", headers=h(token))
    assert applied.status_code == 200, applied.text
    after = {i["title"]: i for i in items(applied.json())}
    assert after["Open by region"]["w"] == 12 and after["Open projects"]["y"] == 0
    assert applied.json()["undo_summary"].startswith("Chat edit:")
    gone = await stack.post(f"{url}/{preview['preview_id']}/apply", headers=h(token))
    assert gone.status_code == 404
    undone = (await stack.post(f"/api/v1/reports/{report['id']}/undo", headers=h(token))).json()
    assert {i["title"]: i["w"] for i in items(undone)}["Open by region"] == 6


# --------------------------------------------------------------------------- dashboards and publishing


async def built_report(api: httpx.AsyncClient, token: str) -> str:
    plan = (await api.post("/api/v1/reports/plan", headers=h(token),
                           json={"request": "Delivery report with overdue go-lives by region"})).json()
    built = await api.post(f"/api/v1/reports/plans/{plan['plan_id']}/build", headers=h(token))
    assert built.status_code == 201, built.text
    return str(built.json()["report_id"])


async def test_compose_check_publish_restore(stack: httpx.AsyncClient, world: World) -> None:
    creator, admin, nomsa = world.tokens["creator"], world.tokens["admin"], world.tokens["nomsa"]
    rid = await built_report(stack, creator)
    created = await stack.post("/api/v1/dashboards", headers=h(creator), json={
        "title": "Gulf delivery", "report_ids": [rid], "layout": "tabs",
        "shared_filters": [{"ref": "dimension:region"}, {"ref": "dimension:priority"}]})
    assert created.status_code == 201, created.text
    d = created.json()
    assert all(f["reaches_all"] for f in d["reach"]) and {f["label"] for f in d["reach"]} == {"Region", "Priority"}
    unknown = await stack.patch(f"/api/v1/dashboards/{d['id']}", headers=h(creator),
                                json={"shared_filters": [{"ref": "dimension:no_such_thing"}]})
    assert unknown.status_code == 400

    # Creators cannot publish; the checks say so and offer Request publish (DEC-05).
    denied = await stack.post(f"/api/v1/dashboards/{d['id']}/publish", headers=h(creator),
                              json={"target_roles": ["cio"]})
    assert denied.status_code == 403 and "Request publish" in denied.json()["error"]["message"]
    checks = (await stack.post(f"/api/v1/dashboards/{d['id']}/prechecks", headers=h(creator),
                               json={"target_roles": ["cio", "viewer"]})).json()
    by_check = {c["check"]: c for c in checks["checks"]}
    assert not checks["passed"] and len(checks["checks"]) == 6
    assert not by_check["publish_permission"]["passed"]
    assert by_check["shared_filters_reach_every_widget"]["passed"] and by_check["model_version_current"]["passed"]
    assert not by_check["returns_data_for_each_role"]["passed"]  # Viewer has no scope policy
    assert any("Depends on each person's cio" in n for n in checks["notes"])
    req = await stack.post(f"/api/v1/dashboards/{d['id']}/request-publish", headers=h(creator),
                           json={"target_roles": ["cio"], "note": "October review"})
    assert req.json()["notified"] >= 1
    assert (await stack.get("/api/v1/publish-requests", headers=h(creator))).status_code == 403
    queue = (await stack.get("/api/v1/publish-requests", headers=h(admin))).json()["items"]
    assert [(q["dashboard"]["id"], q["status"], [r["key"] for r in q["roles"]], q["note"]) for q in queue] == [
        (d["id"], "open", ["cio"], "October review")]
    mine = (await stack.get(f"/api/v1/dashboards/{d['id']}", headers=h(creator))).json()["publish_request"]
    assert mine["id"] == req.json()["request_id"] and mine["requested_by"]["name"]
    roles = {r["key"]: r for r in (await stack.get("/api/v1/roles", headers=h(creator))).json()["items"]}
    assert roles["admin"]["members"] >= 1

    # Admin publishes; failing checks block and leave nothing live.
    blocked = await stack.post(f"/api/v1/dashboards/{d['id']}/publish", headers=h(admin),
                               json={"target_roles": ["cio", "viewer"]})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "precheck_failed"
    failed = [c["check"] for c in blocked.json()["error"]["details"]["checks"] if not c["passed"]]
    assert failed == ["returns_data_for_each_role"]
    assert (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).status_code == 404
    v1 = await stack.post(f"/api/v1/dashboards/{d['id']}/publish", headers=h(admin, **{"Idempotency-Key": "p1"}),
                          json={"target_roles": ["cio"], "note": "October review"})
    assert v1.status_code == 201, v1.text
    assert v1.json()["version"] == 1 and v1.json()["notified"] == 1  # Nomsa; Karim has not signed in yet
    assert v1.json()["request_id"] == req.json()["request_id"]  # publishing closes the open request
    assert (await stack.get("/api/v1/publish-requests", headers=h(admin))).json()["items"] == []
    decided = (await stack.get("/api/v1/publish-requests?status=decided", headers=h(admin))).json()["items"]
    assert decided[0]["status"] == "published" and decided[0]["version"] == 1

    # Nomsa sees it, scoped to her own CIO; Vic (no CIO role) does not.
    view = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).json()
    assert view["kind"] == "user" and view["version"] == 1 and "Own CIO" in view["locked_scope_chip"]
    widget = next(w for t in view["tabs"] for w in t["widgets"] if w["title"] == "Overdue go-lives by region")
    run = (await stack.post(f"/api/v1/widgets/{widget['id']}/run", headers=h(nomsa),
                            json={"context": {"dashboard_version_id": view["version_id"]}})).json()
    assert sum(r[-1] for r in run["rows"]) == oracle.count("overdue_go_lives", cio="Nomsa Osei")
    assert (await stack.get(f"/api/v1/published/{d['id']}", headers=h(world.tokens["viewer"]))).status_code == 404
    home = (await stack.get("/api/v1/home", headers=h(nomsa))).json()
    assert d["id"] in {t["id"] for t in home["shelves"]["published_to_my_role"]}
    notes = (await stack.get("/api/v1/notifications?unread=true", headers=h(nomsa))).json()
    assert notes["unread_count"] >= 1 and notes["items"][0]["kind"] == "dashboard.published"

    # The draft changes; readers keep v1 until the next publish (TEST-031).
    report = (await stack.get(f"/api/v1/reports/{rid}", headers=h(creator))).json()
    victim = next(i for i in items(report) if i.get("title") == "Overdue go-lives by region")
    await stack.delete(f"/api/v1/reports/{rid}/items/{victim['id']}", headers=h(creator))
    still = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).json()
    assert any(w["title"] == "Overdue go-lives by region" for t in still["tabs"] for w in t["widgets"])
    assert (await stack.post(f"/api/v1/widgets/{widget['id']}/run", headers=h(nomsa), json={})).status_code == 200
    detail = (await stack.get(f"/api/v1/dashboards/{d['id']}", headers=h(creator))).json()
    assert detail["draft_differs_from_live"] is True and detail["live"]["version"] == 1
    v2 = await stack.post(f"/api/v1/dashboards/{d['id']}/publish", headers=h(admin), json={"target_roles": ["cio"]})
    assert v2.status_code == 201 and v2.json()["version"] == 2 and "-1 widgets" in v2.json()["change_summary"]
    # Restoring v1 creates v3 with v1's content (RULE-016, TEST-048).
    v3 = await stack.post(f"/api/v1/dashboards/{d['id']}/versions/1/restore", headers=h(admin), json={})
    assert v3.status_code == 201, v3.text
    assert v3.json()["version"] == 3
    history = (await stack.get(f"/api/v1/dashboards/{d['id']}/versions", headers=h(admin))).json()["items"]
    assert [(v["version"], v["state"]) for v in history] == [(3, "live"), (2, "superseded"), (1, "superseded")]
    assert history[0]["restored_from"] == 1 and history[0]["roles"] == ["CIO"]
    assert history[2]["requested_by"] and history[1]["requested_by"] is None
    restored = (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).json()
    assert any(w["title"] == "Overdue go-lives by region" for t in restored["tabs"] for w in t["widgets"])
    assert (await stack.post(f"/api/v1/dashboards/{d['id']}/unpublish", headers=h(admin))).status_code == 200
    assert (await stack.get(f"/api/v1/published/{d['id']}", headers=h(nomsa))).status_code == 404

    audit = (await stack.get("/api/v1/admin/audit?action=dashboard.", headers=h(admin))).json()
    actions = {i["action"] for i in audit["items"]}
    assert {"dashboard.published", "dashboard.restored", "dashboard.unpublished",
            "dashboard.publish_blocked"} <= actions
    assert (await stack.get("/api/v1/admin/audit", headers=h(creator))).status_code == 403
    csv = await stack.post("/api/v1/admin/audit/export?action=dashboard.published", headers=h(admin))
    assert csv.status_code == 200 and csv.text.startswith("at,actor,action")
    exported = (await stack.get("/api/v1/admin/audit?action=audit.exported", headers=h(admin))).json()
    assert exported["items"]


async def test_preview_as_role_and_person(stack: httpx.AsyncClient, world: World) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    rid = await built_report(stack, creator)
    d = (await stack.post("/api/v1/dashboards", headers=h(creator), json={"title": "P", "report_ids": [rid]})).json()
    as_cio = (await stack.post(f"/api/v1/dashboards/{d['id']}/preview", headers=h(admin),
                               json={"as": {"role": "cio"}})).json()
    assert all(not w["shown"] and "named person" in w["reason"] for w in as_cio["widgets"])
    await stack.get("/api/v1/me", headers=h(world.tokens["nomsa"]))
    users = (await stack.get("/api/v1/directory/users?q=Nomsa", headers=h(creator))).json()["items"]
    nomsa_id = users[0]["id"]
    as_nomsa = (await stack.post(f"/api/v1/dashboards/{d['id']}/preview", headers=h(admin),
                                 json={"as": {"user_id": nomsa_id}})).json()
    assert all(w["shown"] for w in as_nomsa["widgets"]) and as_nomsa["rows_in_scope"]["projects"] == \
        sum(1 for p in oracle.PROJECTS if p["cio"] == "Nomsa Osei")
    # A Creator sees shown/hidden and reasons, never counts, and only people they shared with (A1).
    mine = (await stack.post(f"/api/v1/dashboards/{d['id']}/preview", headers=h(creator),
                             json={"as": {"role": "creator"}})).json()
    assert mine["rows_in_scope"] is None and all("rows" not in w for w in mine["widgets"])
    forbidden = await stack.post(f"/api/v1/dashboards/{d['id']}/preview", headers=h(creator),
                                 json={"as": {"user_id": nomsa_id}})
    assert forbidden.status_code == 403


async def test_share_view_only_in_own_scope(stack: httpx.AsyncClient, world: World) -> None:
    creator, nomsa, viewer = world.tokens["creator"], world.tokens["nomsa"], world.tokens["viewer"]
    for t in (nomsa, viewer):
        await stack.get("/api/v1/me", headers=h(t))
    rid = await built_report(stack, creator)
    people = {u["name"]: u["id"] for u in (await stack.get("/api/v1/directory/users", headers=h(creator))).json()
              ["items"]}
    edit = await stack.post("/api/v1/shares", headers=h(creator), json={
        "object": {"kind": "report", "id": rid}, "with_user_ids": [people["Nomsa Osei"]], "permission": "edit"})
    assert edit.status_code == 400
    shared = await stack.post("/api/v1/shares", headers=h(creator), json={
        "object": {"kind": "report", "id": rid}, "with_user_ids": [people["Nomsa Osei"], people["Vic Viewer"]]})
    assert shared.status_code == 201, shared.text
    body = shared.json()
    assert body["shared_with"] == 2 and [w["name"] for w in body["warnings"]] == ["Vic Viewer"]
    home = (await stack.get("/api/v1/home", headers=h(nomsa))).json()
    assert rid in {t["id"] for t in home["shelves"]["shared_with_me"]}
    report = (await stack.get(f"/api/v1/reports/{rid}", headers=h(nomsa))).json()
    assert report["can_edit"] is False and all("spec" not in i for i in items(report))
    figure = next(i for i in items(report) if i.get("title") == "Overdue go-lives")
    run = (await stack.post(f"/api/v1/widgets/{figure['widget_id']}/run", headers=h(nomsa), json={})).json()
    assert run["rows"] == [[oracle.count("overdue_go_lives", cio="Nomsa Osei")]]  # her scope, not the Creator's
    cannot = await stack.patch(f"/api/v1/reports/{rid}", headers=h(nomsa), json={"title": "x"})
    assert cannot.status_code in (403, 404)
    share_id = next(s["id"] for s in body["items"] if s["user"]["name"] == "Nomsa Osei")
    assert (await stack.delete(f"/api/v1/shares/{share_id}", headers=h(creator))).status_code == 204
    assert (await stack.get(f"/api/v1/reports/{rid}", headers=h(nomsa))).status_code == 404
    assert (await stack.post(f"/api/v1/widgets/{figure['widget_id']}/run", headers=h(nomsa),
                             json={})).status_code == 404


async def test_decline_publish_request(stack: httpx.AsyncClient, world: World) -> None:
    creator, admin = world.tokens["creator"], world.tokens["admin"]
    await stack.get("/api/v1/me", headers=h(admin))
    rid = await built_report(stack, creator)
    d = (await stack.post("/api/v1/dashboards", headers=h(creator), json={"title": "D", "report_ids": [rid]})).json()
    first = (await stack.post(f"/api/v1/dashboards/{d['id']}/request-publish", headers=h(creator),
                              json={"target_roles": ["cio"]})).json()
    second = (await stack.post(f"/api/v1/dashboards/{d['id']}/request-publish", headers=h(creator),
                               json={"target_roles": ["cio"], "note": "Now with finance"})).json()
    queue = (await stack.get("/api/v1/publish-requests", headers=h(admin))).json()["items"]
    assert [q["id"] for q in queue] == [second["request_id"]]  # asking again replaces the open request
    url = f"/api/v1/publish-requests/{second['request_id']}/decline"
    assert (await stack.post(url, headers=h(admin), json={"note": " "})).status_code == 400
    assert (await stack.post(url, headers=h(creator), json={"note": "No"})).status_code == 403
    out = await stack.post(url, headers=h(admin), json={"note": "Remove the personal field first."})
    assert out.status_code == 200 and out.json()["status"] == "declined" and out.json()["decided_by"]
    assert (await stack.post(url, headers=h(admin), json={"note": "Again"})).status_code == 409
    assert (await stack.post(f"/api/v1/publish-requests/{first['request_id']}/decline", headers=h(admin),
                             json={"note": "x"})).status_code == 409  # withdrawn
    detail = (await stack.get(f"/api/v1/dashboards/{d['id']}", headers=h(creator))).json()
    assert detail["publish_request"]["status"] == "declined"
    assert detail["publish_request"]["decision_note"] == "Remove the personal field first."
    notes = (await stack.get("/api/v1/notifications", headers=h(creator))).json()["items"]
    assert notes[0]["kind"] == "publish.declined"


async def test_share_check_warns_before_sharing(stack: httpx.AsyncClient, world: World) -> None:
    creator = world.tokens["creator"]
    for t in (world.tokens["nomsa"], world.tokens["viewer"]):
        await stack.get("/api/v1/me", headers=h(t))
    rid = await built_report(stack, creator)
    people = {u["name"]: u["id"] for u in (await stack.get("/api/v1/directory/users", headers=h(creator))).json()
              ["items"]}
    out = await stack.post("/api/v1/shares/check", headers=h(creator), json={
        "object": {"kind": "report", "id": rid}, "with_user_ids": [people["Nomsa Osei"], people["Vic Viewer"]]})
    assert out.status_code == 200, out.text
    assert [(w["name"], bool(w["connection"])) for w in out.json()["warnings"]] == [("Vic Viewer", True)]
    assert (await stack.get(f"/api/v1/shares?object_type=report&object_id={rid}", headers=h(creator))).json()[
        "items"] == []  # nothing shared
