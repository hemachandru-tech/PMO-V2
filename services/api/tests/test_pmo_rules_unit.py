"""PMO pack rules that are not SQL measures, and pack detection by Dataverse names (US-036, US-038)."""

from __future__ import annotations

from datetime import date

import pytest

from tela_api.packs.manifest import DEFAULT_PACKS_DIR, bind, load_manifest, match
from tela_api.packs.pmo_health import calculate, cost_score, issues_score, progress_score, schedule_score

AS_OF = date(2026, 9, 30)


def test_health_worked_example_rule_033() -> None:
    h = calculate({"planned_deployment_date": "2026-12-01", "first_baselined_deployment_date": "2026-11-01",
                   "approved_budget_value_usd": 100_000, "cost_total_usd": 110_000,
                   "actual_project_start_date": "2026-01-01", "percent_complete": 50, "issue_kpi": "Yellow"}, AS_OF)
    assert h.components == {"schedule": 50.0, "cost": 75.0, "progress": 25.0, "issues": 50.0}
    assert h.coverage == 1.0 and h.display_score == 50.0 and h.status == "Amber" and not h.override_applied


def test_override_example_rule_035() -> None:
    h = calculate({"planned_deployment_date": "2026-12-01", "first_baselined_deployment_date": "2026-12-01",
                   "approved_budget_value_usd": 100_000, "cost_total_usd": 160_000,
                   "actual_project_start_date": "2026-01-01", "percent_complete": 90, "issue_kpi": "Green"}, AS_OF)
    assert h.score == 75.0 and h.status == "Red" and h.reason == "Cost score is 0 (severe overrun)"


@pytest.mark.parametrize(("available", "coverage", "scored"), [
    ({"schedule", "cost", "progress", "issues"}, 1.0, True),
    ({"schedule", "cost", "progress"}, 0.85, True),
    ({"schedule", "cost"}, 0.60, True),
    ({"schedule", "progress"}, 0.60, True),
    ({"schedule", "issues"}, 0.50, False),
    ({"cost", "issues"}, 0.40, False),
    ({"schedule"}, 0.35, False),
])
def test_coverage_table_rule_034(available: set[str], coverage: float, scored: bool) -> None:
    project = {"planned_deployment_date": "2026-12-01"} if "schedule" in available or "progress" in available else {}
    if "cost" in available:
        project.update(approved_budget_value_usd=100, cost_total_usd=90)
    if "progress" in available:
        project.update(actual_project_start_date="2026-01-01", percent_complete=99)
    if "issues" in available:
        project["issue_kpi"] = "Green"
    if "schedule" not in available and "progress" in available:
        pytest.skip("progress needs a planned date, which also yields a schedule score")
    h = calculate(project, AS_OF)
    assert h.coverage == pytest.approx(coverage)
    assert (h.score is not None) is scored
    if not scored:
        assert h.status == "Insufficient data"


def test_component_bands_and_boundaries() -> None:
    assert schedule_score(date(2026, 10, 1), None, None, AS_OF) == 100.0
    assert schedule_score(date(2026, 9, 29), None, None, AS_OF) == 75.0  # 1 day past due
    assert schedule_score(date(2026, 9, 29), date(2026, 7, 1), None, AS_OF) == 0.0
    assert cost_score(0, 10) is None and cost_score(100, -1) is None and cost_score(100, 125) == 50.0
    assert cost_score(100, 150) == 25.0 and cost_score(100, 151) == 0.0
    assert progress_score(101, date(2026, 1, 1), date(2026, 12, 1), AS_OF) is None
    assert progress_score(50, date(2026, 12, 1), date(2026, 12, 1), AS_OF) is None
    assert issues_score("Amber") == 50.0 and issues_score(None) is None
    capped = calculate({"planned_deployment_date": "2026-12-01", "first_baselined_deployment_date": "2026-12-01",
                        "approved_budget_value_usd": 100, "cost_total_usd": 140, "issue_kpi": "Green",
                        "actual_project_start_date": "2026-01-01", "percent_complete": 95}, AS_OF)
    assert capped.status == "Amber" and capped.reason == "Cost score is 25 (capped at Amber)"


def test_dataverse_logical_names_are_detected() -> None:
    manifest, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    catalog = [{"qualified_name": "dbo.tpg_project", "columns": [{"name": n, "type": "nvarchar"} for n in (
        "tpg_projectid", "tpgmea_projectnumber", "tpg_title", "status", "current_state", "tpgmea_region",
        "tpgmea_customcio", "tpgmea_planneddeploymentdate", "tpgmea_actualdeploymentdate",
        "tpgmea_approvedbudgetvaluesusd", "tpgmea_costtotal_usd", "modifiedon")]}]
    result = match(manifest, catalog)
    assert result.matched
    mapping = result.out()["field_mapping"]
    assert mapping["projects.cio"] == "tpgmea_customcio"
    assert mapping["projects.planned_deployment_date"] == "tpgmea_planneddeploymentdate"
    binding = bind(manifest, result)
    keys = {m["key"] for m in binding.definition["measures"]}
    assert {"open_projects", "overdue_go_lives", "over_budget"} <= keys and "schedule_risk" not in keys
    gaps = {i["key"]: i["gaps"] for i in binding.items if i["availability"] == "unavailable"}
    assert gaps["schedule_risk"] == ["Projects: Schedule KPI"]


def test_no_match_without_required_fields() -> None:
    manifest, _, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    assert not match(manifest, [{"qualified_name": "public.orders", "columns": [{"name": "id", "type": "int"}]}]).matched


def test_forecast_cost_is_never_called_spend() -> None:
    manifest, raw, _ = load_manifest(DEFAULT_PACKS_DIR / "pmo" / "manifest.yaml")
    text = str(raw).lower()
    assert "forecast cost (eac" in text
    for m in manifest.measures:
        if "cost_total_usd" in str(m["expression"]):
            assert "spend" not in m["label"].lower()
