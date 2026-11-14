"""PMO Starter Pack 2.0 (US-155; FEAT-059 / FEAT-046), no database server.

The starter reports come from the report blueprints; a report shows the widgets the model can run and names the
others; the dashboards hold the catalogue's screens as tabs in the catalogue layout; the activity entities (PCRs,
status reports, RAID, timesheets, tasks) reach the projects' shared filters through their link.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import duckdb
import pmo_oracle as oracle
import pytest
import tpg_synthetic
from test_pmo_catalogue_unit import AS_OF, _catalog, _run

from tela_api.content.starters import catalogue_layout, shown
from tela_api.knowledge.guidance import lint, load_blueprints, load_rules
from tela_api.packs.manifest import (
    PackManifest,
    bind,
    discover,
    load_manifest,
    match,
    usable_widgets,
    widget_spec,
)
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import parse_spec

PROJECT_TABS = ["Overview", "CIO & Portfolio", "Portfolio status", "Lifecycle", "Strategic initiatives",
                "ExCo priorities", "Delivery heatmap", "Regional portfolio", "Portfolio health", "Baselining",
                "Budget code", "PM assignment", "PCR compliance", "On-hold aging", "Closure", "Status reporting",
                "Upcoming go-lives", "Delayed projects", "Stage completion", "Schedule variance", "Update compliance",
                "Critical tasks", "RAID summary", "Resource utilisation", "CAPEX budget vs forecast", "Flow metrics"]
REQUEST_TABS = ["Overview", "Request lifecycle", "CIO & regional demand", "Discovery schedule", "On-hold aging",
                "Budget & cost", "Demand effort", "Governance actions", "Budget code", "PM assignment",
                "Change requests", "Demand conversion"]


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    return load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]


@pytest.fixture(scope="module")
def definition(manifest: PackManifest) -> dict[str, Any]:
    return bind(manifest, match(manifest, _catalog()), reporting_date=AS_OF.isoformat()).definition


@pytest.fixture(scope="module")
def snapshot(definition: dict[str, Any]) -> ModelSnapshot:
    return ModelSnapshot.model_validate({"model_version_id": "v1", "connection_id": "c1", "engine": "excel",
                                         **definition})


@pytest.fixture(scope="module")
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("\n".join(line for line in tpg_synthetic.DDL.splitlines() if not line.startswith("DROP TABLE")))
    for table, cols, rows in tpg_synthetic.tables():
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in rows])
    return con


# --------------------------------------------------------------------------- manifest


def test_pack_two_takes_its_reports_from_the_blueprints(manifest: PackManifest) -> None:
    assert manifest.pack_version == "2.2.1"
    tabs = {key: [manifest.reports[r]["tab"] for r in d["reports"]] for key, d in manifest.dashboards.items()}
    assert tabs == {"projects": PROJECT_TABS, "requests": REQUEST_TABS}
    assert manifest.dashboards["projects"]["filters"] == ["cio", "region", "portfolio", "project_manager",
                                                          "priority", "platform"]
    assert "portfolio" not in " ".join(manifest.dashboards["requests"]["filters"])
    # The four screens the data cannot support stay unavailable with named gaps; 1.x reports are gone (D2).
    assert set(manifest.unavailable_reports) == {"portfolio_trends", "schedule_compliance", "mandatory_fields",
                                                 "milestone_slippage"}
    assert all(r["gaps"] for r in manifest.unavailable_reports.values())
    assert not {"projects_executive", "projects_governance", "requests_pipeline"} & set(manifest.reports)
    assert lint(load_blueprints("pmo_tpg"), load_rules("pmo_tpg"), manifest) == []


def test_every_report_follows_the_catalogue_layout(manifest: PackManifest) -> None:
    for key, rep in manifest.reports.items():
        visuals = [w["visual"] for w in rep["widgets"]]
        figures = visuals.count("figure")
        assert 2 <= figures <= 5, key
        assert visuals[:figures] == ["figure"] * figures, key  # the KPI strip comes first
        assert rep["footnote"].strip(), key
        tables = [w for w in rep["widgets"] if w["visual"] == "table" and w.get("detail")]
        assert all(t["detail"].get("export_name") for t in tables), key
        assert all(len(w.get("extra_measures", [])) <= 3 for w in rep["widgets"]), key
    # A blueprint's filter applies to every widget of its report; tones come from the blueprints' thresholds.
    exco = manifest.reports["exco_priorities"]["widgets"]
    assert all({"ref": "key_priority_project", "op": "=", "values": ["Yes"]} in w["filters"] for w in exco)
    delayed = manifest.reports["delayed_projects"]["widgets"][0]
    assert delayed["tone"] == {"warning": 1, "critical": 10}
    spec = widget_spec(manifest, "baselining", 2, model_version_id="v", connection_id="c")
    assert spec["visual"] == {"type": "figure", "tone": {"warning": 90, "critical": 75, "higher_is_worse": False}}
    meter = widget_spec(manifest, "update_compliance", 2, model_version_id="v", connection_id="c")
    assert meter["visual"] == {"type": "meter", "note": "not updated for 30 days or more"}


def test_wording_rules_hold_on_every_title(manifest: PackManifest) -> None:
    """Titles and labels (footnotes may say what a figure is not: "not money spent")."""
    from tela_api.content import wording

    labels = {m["key"]: m["label"] for m in manifest.measures}
    for rep in manifest.reports.values():
        texts = [rep["title"], rep["tab"], *(w["title"] for w in rep["widgets"]),
                 *(labels[w["measure"]] for w in rep["widgets"])]
        for t in texts:
            assert wording.ok(t), t


# --------------------------------------------------------------------------- availability per widget


def test_widgets_the_source_lacks_are_left_out_and_named(manifest: PackManifest) -> None:
    # A projects-and-requests extract without the actual deployment date and the activity tables (as real TPG).
    cat = [t for t in _catalog() if t["qualified_name"] in ("main.projects", "main.project_requests")]
    cat[1]["columns"] = [c for c in cat[1]["columns"] if c["name"] != "actual_deployment_date"]
    binding = bind(manifest, match(manifest, cat), reporting_date=AS_OF.isoformat())
    reports = {i["key"]: i for i in binding.items if i["kind"] == "report"}
    overview = reports["projects_overview"]
    assert overview["availability"] == "available" and "measure overdue_go_lives" in overview["gaps"]
    assert reports["raid_summary"]["availability"] == "unavailable"  # no widget left
    assert any("risks (whole table)" in i["gaps"] for i in binding.items if i["key"] == "open_risks")
    assert reports["schedule_variance"]["availability"] == "unavailable"
    keep, left = usable_widgets(manifest.reports["projects_overview"], binding.definition)
    assert "Overdue go-lives" in left and keep
    _, rep = shown(manifest.reports["projects_overview"], binding.definition)
    assert rep["footnote"].endswith("Not shown on this source (the data is missing): " + ", ".join(left) + ".")
    # ExCo priorities keeps its project figures without the risks table.
    keep, left = usable_widgets(manifest.reports["exco_priorities"], binding.definition)
    assert "High-exposure risks" in left and len(keep) >= 4


def test_full_extract_shows_every_widget(manifest: PackManifest, definition: dict[str, Any]) -> None:
    for key, rep in manifest.reports.items():
        assert usable_widgets(rep, definition)[1] == [], key


def test_catalogue_layout() -> None:
    assert catalogue_layout(["figure"] * 5 + ["donut", "ranked_bar", "ranked_bar", "table"]) == [
        (0, 0, 3, 2), (3, 0, 3, 2), (6, 0, 2, 2), (8, 0, 2, 2), (10, 0, 2, 2),
        (0, 2, 6, 4), (6, 2, 6, 4), (0, 6, 12, 4), (0, 10, 12, 5)]
    assert catalogue_layout(["figure"] * 4 + ["meter", "paired_bars"], 2) == [
        (0, 2, 3, 2), (3, 2, 3, 2), (6, 2, 3, 2), (9, 2, 3, 2), (0, 4, 6, 4), (6, 4, 6, 4)]


# --------------------------------------------------------------------------- activity entities on the engine


def test_activity_measures_break_down_by_project_dimensions(db: duckdb.DuckDBPyConnection,
                                                            snapshot: ModelSnapshot) -> None:
    cio = {p["project_id"]: p["cio"] for p in oracle.PROJECTS}
    region = {p["project_id"]: p["region"] for p in oracle.PROJECTS}
    want: dict[str, int] = {}
    for r in oracle.ACTIVITY["risks"]:
        if r["state"] != "Closed":
            want[cio[r["project_id"]]] = want.get(cio[r["project_id"]], 0) + 1
    assert dict(_run(db, snapshot, "open_risks", breakdowns=[{"ref": "dimension:cio"}])) == want
    # A projects shared filter reaches a PCR widget through the link.
    got = _run(db, snapshot, "total_pcrs", filters=[{"ref": "dimension:region", "op": "=", "values": ["KSA"]}])
    assert got[0][0] == sum(region[c["project_id"]] == "KSA" for c in oracle.ACTIVITY["change_requests"])
    # Status reporting by CIO counts each project once, however many reports it sent.
    rows = {k: (a, b) for k, a, b in _run(db, snapshot, "status_reporting_scope", breakdowns=[
        {"ref": "dimension:cio"}], extra_measures=[{"ref": "measure:reported_last_30d"}])}
    active = [p for p in oracle.PROJECTS if p["current_state"] == "Active"]
    assert sum(a for a, _ in rows.values()) == len(active)
    assert sum(b for _, b in rows.values()) == oracle.CATALOGUE["reported_last_30d"]


def test_activity_boundaries_on_the_reporting_date() -> None:
    srs = oracle.ACTIVITY["status_reports"]
    assert (AS_OF - srs[0]["report_date"]).days == 30 and (AS_OF - srs[1]["report_date"]).days == 31
    open_actions = [a for a in oracle.ACTIVITY["action_items"] if a["state"] != "Completed"]
    assert open_actions[0]["due_date"] == AS_OF - timedelta(days=1) and open_actions[1]["due_date"] == AS_OF
    # The day-before action is overdue, the one due on the reporting date is not (both counted in the oracle).
    assert oracle.CATALOGUE["overdue_actions"] == sum(a["due_date"] is not None and a["due_date"] < AS_OF
                                                      for a in open_actions)
    crit = [t for t in oracle.ACTIVITY["project_tasks"] if t["is_critical"] == "Yes" and t["progress"] < 100]
    assert crit[0]["finish_date"] < AS_OF and crit[1]["finish_date"] == AS_OF


def test_detail_tables_run_on_their_entity(snapshot: ModelSnapshot, manifest: PackManifest) -> None:
    from tela_api.query.compiler import RecordCompiler
    from tela_api.query.validator import SpecValidator

    for key in ("pcr_compliance", "raid_summary", "critical_tasks", "status_reporting_compliance",
                "resource_utilisation", "request_change_requests"):
        i = next(i for i, w in enumerate(manifest.reports[key]["widgets"]) if w.get("detail"))
        spec = widget_spec(manifest, key, i, model_version_id="v1", connection_id="c1")
        vs = SpecValidator(snapshot).validate(parse_spec(spec)[0])
        cols = manifest.reports[key]["widgets"][i]["detail"]["columns"]
        RecordCompiler("duckdb").compile_records(vs, marks={}, as_of=AS_OF, fields=cols, limit=50, offset=0)


def test_a_detail_table_drops_only_the_columns_the_source_lacks(manifest: PackManifest) -> None:
    cat = _catalog()
    projects = next(t for t in cat if t["qualified_name"] == "main.projects")
    projects["columns"] = [c for c in projects["columns"] if c["name"] not in ("project_number", "lifecycle_phase")]
    binding = bind(manifest, match(manifest, cat), reporting_date=AS_OF.isoformat())
    overview = manifest.reports["projects_overview"]
    keep, rep = shown(overview, binding.definition)
    table = rep["widgets"][-1]
    assert table["visual"] == "table" and len(keep) == len(overview["widgets"])  # nothing left out
    assert "project_number" not in table["detail"]["columns"] and "name" in table["detail"]["columns"]
    assert table["detail"]["sort"] == {"field": "planned_deployment_date", "dir": "asc"}
    lifecycle = shown(manifest.reports["lifecycle"], binding.definition)[1]["widgets"][-1]
    assert "sort" not in lifecycle["detail"]  # it sorted by the missing lifecycle phase
    item = next(i for i in binding.items if i["key"] == "projects_overview")
    assert item["availability"] == "available" and "field Projects: Project number" in item["gaps"]
