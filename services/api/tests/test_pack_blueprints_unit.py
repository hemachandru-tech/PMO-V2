"""Report blueprints and starter dashboards in a pack (ENH-025, US-234; FEAT-075): the checks, the draft edits and
"Save as blueprint" without a database.

Every PMO blueprint passes the checks; a blueprint naming a measure, dimension, field or dashboard the pack lacks, or
a starter report text that fails ``check_report_texts``, is refused with the path of the part; blueprints and their
dashboards' tabs stay in step; a report's widgets become blueprint parts by key, and uncertified, own-rule, custom
SQL, field-based and calculated widgets are named and left out.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from tela_api.packs import blueprints as bps
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, blueprint_reports
from tela_query_spec.model import ModelSnapshot

PMO = DEFAULT_PACKS_DIR / "pmo"


@pytest.fixture(scope="module")
def pmo() -> tuple[dict[str, Any], dict[str, Any]]:
    head = yaml.safe_load((PMO / "manifest.yaml").read_text())
    doc = yaml.safe_load((PMO / "report_blueprints.yaml").read_text())
    return head, doc


def _bp(doc: dict[str, Any], bid: str) -> dict[str, Any]:
    return copy.deepcopy(next(b for b in doc["blueprints"] if b["id"] == bid))


def test_every_pmo_blueprint_passes(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    assert bps.check_document(doc, head) == {}


def test_problems_name_the_part(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    bp = _bp(doc, "projects_overview")
    bp["kpis"][0] = "no_such_measure"
    bp["widgets"][1]["breakdowns"] = ["region", "no_such_dimension"]
    bp["table"]["columns"].append("no_such_field")
    bp["dashboard"] = "risks"
    problems = {(p["path"], p["message"]) for p in bps.check_blueprint(bp, head, doc)}
    assert ("kpis.0", "the pack has no measure no_such_measure") in problems
    assert ("widgets.1", "Reported status by region: the pack has no dimension no_such_dimension") in problems
    assert ("table.columns", "projects has no field no_such_field") in problems
    assert ("dashboard", "the pack has no dashboard risks") in problems
    # Schema errors carry their path; a blueprint without a footnote is refused.
    bad = _bp(doc, "projects_overview")
    bad["widgets"][0]["visual"] = "spiral"
    del bad["footnote"]
    paths = {p["path"] for p in bps.check_blueprint(bad, head, doc)}
    assert any(p.startswith("widgets.0") for p in paths) and paths
    # A figure tone outside the starter report's rules fails check_report_texts / the tone schema.
    bp = _bp(doc, "projects_overview")
    assert bps.check_blueprint(bp, head, {**doc, "tones": {"in_delivery": {"warning": "lots"}}})


def test_blueprints_and_dashboard_tabs_stay_in_step(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = copy.deepcopy(pmo)
    new = {**_bp(doc, "projects_overview"), "id": "my_overview", "dashboard": "requests"}
    head2, doc2 = bps.put_blueprint(head, doc, new)
    assert head2["dashboards"]["requests"]["reports"][-1] == "my_overview"
    assert "my_overview" not in head2["dashboards"]["projects"]["reports"]
    # Renaming keeps the tab where it was; moving to another dashboard moves the tab.
    head3, doc3 = bps.put_blueprint(head2, doc2, {**new, "id": "my_overview_2"}, replaces="my_overview")
    tabs = head3["dashboards"]["requests"]["reports"]
    assert tabs[-1] == "my_overview_2" and "my_overview" not in tabs
    with pytest.raises(ValueError, match="already has"):
        bps.put_blueprint(head3, doc3, {**new, "id": "projects_overview"}, replaces="my_overview_2")
    head4, doc4, problems = bps.put_dashboard(head3, doc3, "projects", {
        "title": "PMO — Projects", "reports": ["my_overview_2", "projects_overview"], "filters": ["region"]})
    assert problems == []
    assert head4["dashboards"]["projects"]["reports"] == ["my_overview_2", "projects_overview"]
    assert "my_overview_2" not in head4["dashboards"]["requests"]["reports"]
    assert next(b for b in doc4["blueprints"] if b["id"] == "my_overview_2")["dashboard"] == "projects"
    _, _, problems = bps.put_dashboard(head4, doc4, "projects", {"title": "x", "reports": ["nope", "nope"],
                                                                 "filters": ["nowhere"]})
    assert {p["path"] for p in problems} == {"reports.0", "reports.1", "filters.0", "reports"}
    # A dashboard still named by a blueprint can't go; a dropped blueprint leaves its tab.
    with pytest.raises(ValueError, match="move or delete"):
        bps.drop_dashboard(head4, doc4, "projects")
    head5, doc5 = bps.drop_blueprint(head4, doc4, "my_overview_2")
    assert "my_overview_2" not in head5["dashboards"]["projects"]["reports"]
    assert "my_overview_2" not in [b["id"] for b in doc5["blueprints"]]
    # A tenant dashboard's starter reports open with its title, PMO's with Projects / Requests.
    head6, doc6, _ = bps.put_dashboard(head5, doc5, "risks", {"title": "Risk register", "reports": [],
                                                              "filters": []})
    head6, doc6 = bps.put_blueprint(head6, doc6, {**new, "id": "risk_view", "dashboard": "risks"})
    reports, _ = blueprint_reports(doc6, head6)
    assert reports["risk_view"]["title"].startswith("Risk register — ")
    assert reports["projects_overview"]["title"] == "Projects — Projects overview"


SNAP = ModelSnapshot.model_validate({
    "model_version_id": "v1", "connection_id": "c1", "version_no": 1,
    "entities": [{"key": "projects", "label": "Projects", "source_object": "public.projects", "primary_key": ["id"],
                  "fields": [{"key": "id", "source_column": "id", "label": "Id", "data_type": "text"},
                             {"key": "name", "source_column": "name", "label": "Name", "data_type": "text"},
                             {"key": "region", "source_column": "region", "label": "Region", "data_type": "text"}]}],
    "measures": [
        {"key": "open_projects", "label": "Open projects", "entity": "projects", "expression": {"agg": "count"}, "plain_rule": "Count."},
        {"key": "in_delivery", "label": "In delivery", "entity": "projects", "expression": {"agg": "count"}, "plain_rule": "Count."},
        {"key": "draft_rule", "label": "Draft rule", "entity": "projects", "expression": {"agg": "count"},
         "plain_rule": "Count.", "status": "deprecated"},
    ],
    "dimensions": [{"key": "region", "label": "Region", "entity": "projects", "field": "region"}],
})


def _w(title: str, spec: dict[str, Any], visual: str, badge: str = "certified") -> dict[str, Any]:
    return {"kind": "widget", "title": title, "visual": visual, "badge": badge,
            "spec": {"model_version_id": "v1", "connection_id": "c1", "visual": {"type": visual}, **spec}}


def test_save_as_blueprint_keeps_certified_parts_by_key(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, _ = pmo
    m = {"measure": {"ref": "measure:open_projects"}}
    report = {"title": "Regional delivery", "sections": [
        {"title": "Headline", "items": [
            _w("Open projects", m, "figure"),
            _w("Gulf in delivery", {"measure": {"ref": "measure:in_delivery"},
                                    "filters": [{"ref": "dimension:region", "op": "=", "values": ["KSA"]}]}, "figure"),
            {"kind": "callout", "title": "Read me", "text": "Counts are open projects.", "level": "warning"},
        ]},
        {"title": "Details", "items": [
            _w("Open by region", {**m, "breakdowns": [{"ref": "dimension:region"}]}, "ranked_bar"),
            _w("Open projects list", {**m, "visual": {"type": "table", "detail": {
                "columns": ["name", "region"], "sort": {"field": "name", "dir": "asc"}}}}, "table"),
            _w("Custom", {"custom_sql_id": "x"}, "table", badge="custom_sql"),
            _w("Own rule", {"measure": {"ref": "measure:_adhoc", "adhoc": {"entity": "projects"}}}, "figure"),
            _w("Old rule", {"measure": {"ref": "measure:draft_rule"}}, "figure"),
            _w("By field", {**m, "breakdowns": [{"ref": "field:projects.name"}]}, "ranked_bar"),
            _w("Shares", {**m, "breakdowns": [{"ref": "dimension:region"}], "share_of_total": ["measure:x"]},
               "donut"),
            {"kind": "text_note", "text": "Ask the PMO"},
            {"kind": "footnote", "text": "Open = not closed."},
        ]},
    ]}
    raw, tones, left = bps.blueprint_from_report(report, {"v1": SNAP}, head, bid="regional_delivery",
                                                 dashboard="projects")
    assert raw["kpis"] == ["open_projects", {"title": "Gulf in delivery", "measure": "in_delivery",
                                              "filters": [{"ref": "region", "op": "=", "values": ["KSA"]}]}]
    assert raw["widgets"] == [{"title": "Open by region", "measure": "open_projects", "visual": "ranked_bar",
                               "breakdowns": ["region"]}]
    assert raw["table"] == {"title": "Open projects list", "measure": "open_projects", "columns": ["name", "region"],
                            "sort": {"field": "name", "dir": "asc"}}
    assert raw["callout"] == {"title": "Read me", "text": "Counts are open projects.", "level": "warning"}
    assert raw["footnote"] == "Open = not closed." and raw["dashboard"] == "projects" and tones == {}
    assert {x["title"]: x["reason"] for x in left} == {
        "Custom": "custom SQL", "Own rule": "its own rule, not a certified measure",
        "Old rule": "measure draft_rule is not certified", "By field": "reads a field, not a dimension",
        "Shares": "a calculation a blueprint can't hold (share of total)",
        "Ask the PMO": "a note has no place in a blueprint"}
    assert bps.check_blueprint(raw, head, {"version": "0.1.0", "blueprints": []}) == []
    # A measure the pack lacks is named, not invented.
    report["sections"][0]["items"] = [_w("Elsewhere", {"measure": {"ref": "measure:open_projects"},
                                                        "breakdowns": [{"ref": "dimension:region"}]}, "ranked_bar")]
    _, _, left = bps.blueprint_from_report(report, {"v1": SNAP}, {**head, "dimensions": []}, bid="x1",
                                           dashboard="projects")
    assert left[0]["reason"] == "not in this pack: dimension region"


def test_slug_is_a_free_blueprint_id() -> None:
    assert bps.slug("Delayed projects — by CIO", set()) == "delayed_projects_by_cio"
    assert bps.slug("2026 plan", {"report_2026_plan"}) == "report_2026_plan_2"
