"""Unit & Integration tests for PMO V2 Advanced Predictive, Brain Retrain, and Delta Rules Engines."""

import pytest
from fastapi.testclient import TestClient

from tela_api.brain.delta_rules import (
    delete_delta_rule,
    get_all_active_rules,
    parse_and_apply_delta_rule,
)
from tela_api.brain.semantic_brain import (
    auto_generate_model_snapshot,
    detect_field_semantic_type,
)
from tela_api.core.settings import Settings
from tela_api.main import create_app
from tela_api.query.predictive_engine import (
    WhatIfScenarioRequest,
    compute_correlation_matrix,
    convert_currency,
    forecast_time_series,
    normalize_multilingual_prompt,
    simulate_what_if_scenario,
)


@pytest.fixture
def test_client():
    settings = Settings(
        env="local",
        database_url="postgresql+asyncpg://mock:mock@localhost/mock",
        jwt_public_key="mock",
    )
    app = create_app(settings)
    return TestClient(app)


# --- 1. Predictive Engine Unit Tests ---

def test_forecast_time_series():
    historical = [
        ("2026-01", 100.0),
        ("2026-02", 120.0),
        ("2026-03", 140.0),
        ("2026-04", 160.0),
    ]
    res = forecast_time_series(historical, future_steps=3, metric_name="Monthly Cost")
    assert res.metric_name == "Monthly Cost"
    assert res.trend_direction == "increasing"
    assert len(res.projections) == 3
    assert res.projections[0].predicted_value == 180.0
    assert res.projections[1].predicted_value == 200.0
    assert res.projections[2].predicted_value == 220.0


def test_what_if_scenario():
    req = WhatIfScenarioRequest(
        baseline_budget=500000.0,
        baseline_duration_months=12.0,
        budget_variance_pct=15.0,
        delay_months=2.0,
        contingency_rate_pct=10.0,
    )
    res = simulate_what_if_scenario(req)
    assert res.original_budget == 500000.0
    assert res.projected_budget > res.original_budget
    assert res.cost_impact_pct > 15.0
    assert res.risk_level in ("Medium", "High", "Critical")


def test_correlation_matrix():
    # Perfect positive correlation between x and y
    columns = {
        "budget": [10.0, 20.0, 30.0, 40.0, 50.0],
        "spent": [12.0, 22.0, 31.0, 41.0, 51.0],
        "unrelated": [99.0, 12.0, 43.0, 1.0, 88.0],
    }
    pairs = compute_correlation_matrix(columns)
    assert len(pairs) >= 2
    budget_spent = next(p for p in pairs if (p.metric_x == "budget" and p.metric_y == "spent") or (p.metric_x == "spent" and p.metric_y == "budget"))
    assert budget_spent.pearson_r > 0.95
    assert budget_spent.strength == "Strong Positive"


def test_currency_conversion():
    res = convert_currency(100.0, "USD", "SAR")
    assert res["from_currency"] == "USD"
    assert res["to_currency"] == "SAR"
    assert res["converted_amount"] > 300.0  # ~375 SAR


def test_multilingual_normalization():
    arabic_query = "مشروع نشط ميزانية"
    norm = normalize_multilingual_prompt(arabic_query)
    assert "project" in norm
    assert "active" in norm
    assert "budget" in norm

    french_query = "projets statut actif"
    norm2 = normalize_multilingual_prompt(french_query)
    assert "projects" in norm2
    assert "status" in norm2
    assert "active" in norm2


# --- 2. Brain Model & Delta Rules Unit Tests ---

def test_semantic_type_detection():
    d_type, sem_type = detect_field_semantic_type("approved_budget_usd", "decimal(19,4)")
    assert d_type == "decimal"
    assert sem_type == "currency"

    d_type2, sem_type2 = detect_field_semantic_type("project_status", "varchar(50)")
    assert d_type2 == "text"
    assert sem_type2 == "status"


def test_auto_generate_model_snapshot_generic():
    # Arbitrary schema from any database
    schema = {
        "deliveries": [
            {"name": "id", "type": "int"},
            {"name": "speed", "type": "decimal"},
            {"name": "delivery_status", "type": "varchar"},
            {"name": "created_at", "type": "datetime"},
        ],
        "players": [
            {"name": "id", "type": "int"},
            {"name": "player_name", "type": "varchar"},
        ],
    }
    fks = [{"from_table": "deliveries", "to_table": "players", "from_col": "id", "to_col": "id"}]
    snapshot = auto_generate_model_snapshot(schema, foreign_keys=fks, connection_name="sports_db")

    assert len(snapshot.entities) == 2
    assert any(e.key == "deliveries" for e in snapshot.entities)
    assert any(e.key == "players" for e in snapshot.entities)
    assert len(snapshot.links) == 1
    assert any(m.key == "deliveries_count" for m in snapshot.measures)


def test_delta_rules_tokenless():
    # 1. Apply arithmetic formula
    delta = parse_and_apply_delta_rule("overrun = actual_cost - approved_budget", target_entity="projects")
    assert delta.rule_type == "formula"
    assert delta.rule_key == "overrun"
    assert delta.tokens_consumed == 0
    assert delta.latency_ms < 50.0  # Instantaneous

    # 2. Apply filter condition
    delta2 = parse_and_apply_delta_rule("Active Projects means status in ('Active', 'New')", target_entity="projects")
    assert delta2.rule_type == "filter_condition"

    # 3. List and delete
    rules = get_all_active_rules()
    assert len(rules) >= 2
    assert delete_delta_rule("overrun") is True


# --- 3. PMO V2 API Endpoints Integration Tests ---

def test_api_brain_retrain(test_client):
    resp = test_client.post(
        "/api/v1/brain/retrain",
        json={"connection_name": "tpg-mysql"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["connection_name"] == "tpg-mysql"
    assert data["tables_count"] > 0
    assert data["status"] == "synchronized"


def test_api_brain_delta_rule_endpoint(test_client):
    resp = test_client.post(
        "/api/v1/brain/rules/apply-text",
        json={
            "rule_text": "Critical Risks means risk_level = 'High' or cost_impact > 50000",
            "target_entity": "risks",
        },
    )
    assert resp.status_code == 200
    delta = resp.json()
    assert delta["tokens_consumed"] == 0
    assert delta["target_entity"] == "risks"

    # Verify list
    list_resp = test_client.get("/api/v1/brain/rules")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) > 0


def test_api_predictive_forecast(test_client):
    resp = test_client.post(
        "/api/v1/predict/forecast",
        json={
            "series": [("Q1", 100.0), ("Q2", 150.0), ("Q3", 200.0)],
            "future_steps": 2,
            "metric_name": "Quarterly Burn",
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["metric_name"] == "Quarterly Burn"
    assert len(res["projections"]) == 2
    assert res["projections"][0]["predicted_value"] == 250.0


def test_api_predictive_what_if(test_client):
    resp = test_client.post(
        "/api/v1/predict/what-if",
        json={
            "baseline_budget": 1000000.0,
            "baseline_duration_months": 6.0,
            "budget_variance_pct": 20.0,
            "delay_months": 3.0,
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["risk_level"] == "Critical"
    assert res["cost_impact_abs"] > 0.0


def test_api_predictive_currency(test_client):
    resp = test_client.post(
        "/api/v1/predict/currency-convert",
        json={"amount": 100.0, "from_currency": "USD", "to_currency": "EUR"},
    )
    assert resp.status_code == 200
    assert resp.json()["to_currency"] == "EUR"

