"""Unit tests for PMO V2 Intelligence and Dashboard Generation Engines."""

import pytest
from tela_api.content.auto_dashboard import (
    AutoDashboardPlan,
    plan_dashboard_from_intent,
    recommend_chart_type,
)
from tela_api.ask.conversational_analytics import (
    compute_root_cause,
    detect_follow_up_intent,
    generate_intelligent_suggestions,
)
from tela_api.query.transformation_engine import (
    calculate_variance_metrics,
    compute_time_intelligence,
    pivot_tabular_data,
)
from tela_api.ask.executive_storyteller import (
    detect_operational_anomalies,
    generate_executive_briefing,
)
from tela_api.content.state_memory import (
    DashboardSessionState,
    apply_cross_filter,
    clear_cross_filters,
)
from tela_query_spec.expr import AggExpr
from tela_query_spec.model import Dimension, Measure, ModelEntity, ModelField, ModelSnapshot


@pytest.fixture
def sample_snapshot() -> ModelSnapshot:
    field_id = ModelField(key="id", source_column="id", label="ID", data_type="integer")
    field_name = ModelField(key="name", source_column="name", label="Project Name", data_type="text")
    field_cost = ModelField(key="cost", source_column="cost", label="Budget Cost", data_type="decimal")
    field_status = ModelField(key="status", source_column="status", label="Health Status", data_type="text")
    field_date = ModelField(key="created_at", source_column="created_at", label="Date Created", data_type="date")

    entity = ModelEntity(
        key="projects",
        label="Projects",
        source_object="shop_data.projects",
        primary_key=["id"],
        fields=[field_id, field_name, field_cost, field_status, field_date],
    )

    measure_total = Measure(
        key="total_projects",
        label="Total Projects",
        entity="projects",
        expression=AggExpr(agg="count", field="id"),
        plain_rule="Count of projects",
    )
    measure_budget = Measure(
        key="budget_cost",
        label="Total Budget Cost",
        entity="projects",
        expression=AggExpr(agg="sum", field="cost"),
        plain_rule="Sum of budget cost",
    )

    dim_status = Dimension(
        key="health_status",
        label="Health Status",
        entity="projects",
        field="status",
    )
    dim_date = Dimension(
        key="month_period",
        label="Monthly Period",
        entity="projects",
        field="created_at",
        is_temporal=True,
    )

    return ModelSnapshot(
        model_version_id="mv-001",
        connection_id="conn-001",
        engine="postgres",
        entities=[entity],
        measures=[measure_total, measure_budget],
        dimensions=[dim_status, dim_date],
    )


# 1. AI Dashboard Generation Tests
def test_auto_dashboard_generation(sample_snapshot: ModelSnapshot):
    plan = plan_dashboard_from_intent(
        "Create an executive portfolio health dashboard with budget and status",
        sample_snapshot,
    )
    assert isinstance(plan, AutoDashboardPlan)
    assert len(plan.widgets) >= 3
    # First widget is a headline KPI card
    assert plan.widgets[0].visual_type == "kpi_card"
    # Trend line and breakdown are created
    types = [w.visual_type for w in plan.widgets]
    assert "trend_line" in types
    assert plan.recommended_filters == ["health_status"]


def test_chart_recommendation():
    assert recommend_chart_type(is_time_series=False, cardinality=1, num_measures=1) == "kpi_card"
    assert recommend_chart_type(is_time_series=True, cardinality=12, num_measures=1) == "trend_line"
    assert recommend_chart_type(is_time_series=False, cardinality=4, num_measures=1, is_part_of_whole=True) == "donut"
    assert recommend_chart_type(is_time_series=False, cardinality=25, num_measures=3) == "table"


# 2. Conversational Analytics & Root Cause Tests
def test_conversational_followup_intent():
    # Filter follow-up
    res = detect_follow_up_intent("Now filter by region = 'EMEA'", previous_query="Show total projects")
    assert res.is_follow_up is True
    assert res.operation == "filter"
    assert res.added_filters.get("region") == "EMEA"

    # Breakdown follow-up
    res2 = detect_follow_up_intent("Break down by cio", previous_query="Show open projects")
    assert res2.is_follow_up is True
    assert res2.operation == "drill_down"
    assert res2.added_breakdowns == ["cio"]

    # Root cause follow-up
    res3 = detect_follow_up_intent("Why did project count drop?", previous_query="Monthly active projects")
    assert res3.is_follow_up is True
    assert res3.operation == "root_cause"


def test_root_cause_analysis():
    baseline = [("EMEA", 100.0), ("APAC", 50.0), ("Americas", 80.0)]
    current = [("EMEA", 60.0), ("APAC", 55.0), ("Americas", 75.0)]
    rca = compute_root_cause("Active Projects", baseline, current)
    assert rca.total_previous == 230.0
    assert rca.total_current == 190.0
    assert rca.net_variance == -40.0
    assert rca.primary_driver == "EMEA"  # EMEA dropped by 40


def test_intelligent_suggestions():
    suggs = generate_intelligent_suggestions("Show project delivery performance", ["id", "status"])
    assert len(suggs) == 3
    assert any("overdue" in s.lower() for s in suggs)


# 3. Data Transformation & Time Intelligence Tests
def test_time_intelligence_pop():
    series = [("2026-08", 100.0), ("2026-09", 150.0), ("2026-10", 120.0)]
    pts = compute_time_intelligence(series)
    assert len(pts) == 3
    assert pts[0].change_pct is None
    assert pts[1].change_abs == 50.0
    assert pts[1].change_pct == 50.0
    assert pts[2].change_abs == -30.0
    assert pts[2].change_pct == -20.0


def test_tabular_pivot():
    rows = [
        ["EMEA", "Green", 10],
        ["EMEA", "Red", 3],
        ["APAC", "Green", 8],
        ["APAC", "Red", 1],
    ]
    piv = pivot_tabular_data(rows, row_idx=0, col_idx=1, val_idx=2, agg_func="sum")
    assert piv.column_headers == ["Category", "Green", "Red"]
    assert len(piv.matrix) == 2  # EMEA, APAC


def test_variance_metrics():
    vm = calculate_variance_metrics(actual=120.0, target=100.0)
    assert vm["variance_abs"] == 20.0
    assert vm["variance_pct"] == 20.0
    assert vm["completion_ratio"] == 1.2


# 4. Executive Storyteller Tests
def test_executive_storytelling_and_anomalies():
    brief = generate_executive_briefing(
        subject="Active Portfolios",
        headline_value=65,
        findings=["80% of projects sit in EMEA and APAC", "Schedule variance increased by 4 days"],
        recommendations=["Accelerate Stage 5 approvals for 3 flagged initiatives"],
        source_table="projects",
    )
    assert "Active Portfolios: 65" == brief.headline
    assert len(brief.key_findings) == 2
    assert brief.confidence_score >= 0.95

    # Outlier / Anomaly detection
    items = [("App A", 10.0), ("App B", 12.0), ("App C", 11.0), ("App D", 95.0), ("App E", 10.5)]
    anomalies = detect_operational_anomalies(items, sensitivity=1.5)
    assert len(anomalies) >= 1
    assert anomalies[0].member == "App D"


# 5. State Management & Filter Propagation Tests
def test_cross_filter_state():
    state = clear_cross_filters(session_id="s1", dashboard_id="d1")
    assert len(state.active_filters) == 0

    state2 = apply_cross_filter(state, dimension_key="region", selected_value="MENA")
    assert "region" in state2.active_filters
    assert state2.active_filters["region"].selected_values == ["MENA"]

    # Toggle off
    state3 = apply_cross_filter(state2, dimension_key="region", selected_value="MENA")
    assert "region" not in state3.active_filters
