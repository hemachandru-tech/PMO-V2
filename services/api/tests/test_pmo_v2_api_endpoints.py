"""Integration tests for PMO V2 Intelligence REST API endpoints."""

import pytest
from fastapi.testclient import TestClient
from tela_api.main import create_app
from tela_api.core.settings import Settings


@pytest.fixture
def test_client():
    settings = Settings(
        env="local",
        database_url="postgresql+asyncpg://mock:mock@localhost/mock",
        jwt_public_key="mock",
    )
    app = create_app(settings)
    return TestClient(app)


def test_v2_followup_endpoint(test_client):
    resp = test_client.post(
        "/api/v1/ask/followup-intent",
        json={
            "current_prompt": "Now filter by status = 'Active'",
            "previous_query": "List all initiatives",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_follow_up"] is True
    assert data["operation"] == "filter"
    assert data["added_filters"]["status"] == "Active"


def test_v2_suggestions_endpoint(test_client):
    resp = test_client.post(
        "/api/v1/ask/suggestions",
        json={
            "query_text": "Total budget across projects",
            "returned_columns": ["department", "status", "priority", "budget"],
        },
    )
    assert resp.status_code == 200
    suggestions = resp.json()
    assert len(suggestions) >= 3
    assert any("project" in s.lower() for s in suggestions)


def test_v2_root_cause_endpoint(test_client):
    resp = test_client.post(
        "/api/v1/ask/root-cause",
        json={
            "metric_name": "Operating Cost",
            "baseline_data": [
                {"label": "Eng", "value": 100},
                {"label": "Sales", "value": 50},
            ],
            "current_data": [
                {"label": "Eng", "value": 140},
                {"label": "Sales", "value": 45},
            ],
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["metric_name"] == "Operating Cost"
    assert res["net_variance"] == 35.0
    assert res["primary_driver"] == "Eng"


def test_v2_executive_brief_endpoint(test_client):
    resp = test_client.post(
        "/api/v1/ask/executive-brief",
        json={
            "subject": "Enterprise PMO Overview",
            "headline_value": "42 Active Projects",
            "findings": [
                "Overall project health is trending positive with 88% on-schedule.",
                "Digital Core program delayed by 2 weeks.",
            ],
            "recommendations": ["Reallocate engineering staff to Digital Core."],
        },
    )
    assert resp.status_code == 200
    brief = resp.json()
    assert "Enterprise PMO Overview" in brief["headline"]
    assert len(brief.get("key_findings", [])) >= 1
    assert brief.get("actionable_recommendation") is not None


def test_v2_anomaly_endpoint(test_client):
    series = [{"label": f"2026-W0{i}", "value": 100.0} for i in range(1, 9)]
    series.append({"label": "2026-W10", "value": 900.0})

    resp = test_client.post(
        "/api/v1/ask/anomalies",
        json={
            "items": series,
            "sensitivity": 2.0,
        },
    )
    assert resp.status_code == 200
    anomalies = resp.json()
    assert len(anomalies) >= 1
    assert anomalies[0]["member"] == "2026-W10"


def test_v2_time_series_endpoint(test_client):
    series = [
        {"period": "2026-01-01", "value": 1000},
        {"period": "2026-02-01", "value": 1250},
    ]
    resp = test_client.post(
        "/api/v1/transform/time-series",
        json={"series": series},
    )
    assert resp.status_code == 200
    transformed = resp.json()
    assert len(transformed) == 2
    assert transformed[1]["change_pct"] == 25.0


def test_v2_variance_endpoint(test_client):
    resp = test_client.post(
        "/api/v1/transform/variance",
        json={"actual": 120.0, "target": 100.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["variance_abs"] == 20.0
    assert data["variance_pct"] == 20.0


def test_v2_cross_filter_state_endpoint(test_client):
    # Apply filter
    resp = test_client.post(
        "/api/v1/dashboards/state/cross-filter",
        json={
            "session_id": "test-session-123",
            "dashboard_id": "dash-456",
            "dimension_key": "status",
            "selected_value": "Critical",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Critical" in data["active_filters"]["status"]

    # Reset filter
    del_resp = test_client.delete("/api/v1/dashboards/state/cross-filter/test-session-123")
    assert del_resp.status_code == 200
    assert del_resp.json()["active_filters"] == {}

