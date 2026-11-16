"""Predictive Analytics, Forecasting, Correlation & Conversion Engine for PMO V2.

Implements:
- Forward-looking time-series forecasting (Linear / Exponential trend projection)
- "What-If" scenario simulation (e.g. testing budget/schedule impact)
- Pearson cross-metric correlation matrix
- Multi-currency dynamic conversion (USD, EUR, GBP, SAR, AED)
- Multilingual intent normalization
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel

# --- Forecasting & What-If Models ---

class ForecastPoint(BaseModel):
    period_index: int
    projected_period: str
    predicted_value: float
    confidence_lower: float
    confidence_upper: float


class ForecastResult(BaseModel):
    metric_name: str
    historical_count: int
    forecast_periods: int
    slope: float
    intercept: float
    r_squared: float
    trend_direction: Literal["increasing", "decreasing", "stable"]
    projections: list[ForecastPoint]


class WhatIfScenarioRequest(BaseModel):
    baseline_budget: float
    baseline_duration_months: float
    budget_variance_pct: float = 0.0
    delay_months: float = 0.0
    contingency_rate_pct: float = 10.0


class WhatIfScenarioResult(BaseModel):
    original_budget: float
    projected_budget: float
    original_duration: float
    projected_duration: float
    cost_impact_abs: float
    cost_impact_pct: float
    recommended_contingency: float
    risk_level: Literal["Low", "Medium", "High", "Critical"]
    executive_assessment: str


# --- Correlation Models ---

class CorrelationPair(BaseModel):
    metric_x: str
    metric_y: str
    pearson_r: float
    strength: Literal["Strong Positive", "Moderate Positive", "Weak/None", "Moderate Negative",
                              "Strong Negative"]


# --- Currency Rates Registry ---

FX_RATES_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.28,
    "SAR": 0.2666,
    "AED": 0.2723,
    "INR": 0.0118,
    "JPY": 0.0066,
}


# --- Predictive Engine Logic ---

def forecast_time_series(
    historical_points: list[tuple[str, float]],
    future_steps: int = 3,
    metric_name: str = "Metric",
) -> ForecastResult:
    """Compute trendline slope, intercept, R-squared, and project future N steps."""
    n = len(historical_points)
    if n < 2:
        # Fallback for insufficient data
        projections = [
            ForecastPoint(
                period_index=i + 1,
                projected_period=f"Step +{i + 1}",
                predicted_value=historical_points[0][1] if n == 1 else 0.0,
                confidence_lower=0.0,
                confidence_upper=0.0,
            )
            for i in range(future_steps)
        ]
        return ForecastResult(
            metric_name=metric_name,
            historical_count=n,
            forecast_periods=future_steps,
            slope=0.0,
            intercept=historical_points[0][1] if n == 1 else 0.0,
            r_squared=0.0,
            trend_direction="stable",
            projections=projections,
        )

    # Simple Ordinary Least Squares (OLS)
    x_vals = list(range(n))
    y_vals = [p[1] for p in historical_points]

    mean_x = sum(x_vals) / n
    mean_y = sum(y_vals) / n

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals, strict=True))
    denominator = sum((x - mean_x) ** 2 for x in x_vals)

    slope = (numerator / denominator) if denominator != 0 else 0.0
    intercept = mean_y - slope * mean_x

    # Compute R-squared & standard error
    ss_tot = sum((y - mean_y) ** 2 for y in y_vals)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_vals, y_vals, strict=True))
    r_squared = (1 - (ss_res / ss_tot)) if ss_tot > 0 else 1.0
    r_squared = max(0.0, min(1.0, r_squared))

    std_err = math.sqrt(ss_res / (n - 2)) if n > 2 and ss_res > 0 else (mean_y * 0.05)

    direction: Literal["increasing", "decreasing", "stable"]
    if abs(slope) < 0.01:
        direction = "stable"
    elif slope > 0:
        direction = "increasing"
    else:
        direction = "decreasing"

    projections = []
    for step in range(1, future_steps + 1):
        future_x = n - 1 + step
        pred = slope * future_x + intercept
        margin = 1.96 * std_err * math.sqrt(1 + 1/n + ((future_x - mean_x)**2) / (denominator or 1))
        projections.append(
            ForecastPoint(
                period_index=future_x,
                projected_period=f"Forecast Period +{step}",
                predicted_value=round(pred, 2),
                confidence_lower=round(max(0.0, pred - margin), 2),
                confidence_upper=round(pred + margin, 2),
            )
        )

    return ForecastResult(
        metric_name=metric_name,
        historical_count=n,
        forecast_periods=future_steps,
        slope=round(slope, 4),
        intercept=round(intercept, 4),
        r_squared=round(r_squared, 4),
        trend_direction=direction,
        projections=projections,
    )


def simulate_what_if_scenario(req: WhatIfScenarioRequest) -> WhatIfScenarioResult:
    """Simulate budget, delay, and contingency variations for risk appraisal."""
    budget_delta = req.baseline_budget * (req.budget_variance_pct / 100.0)
    delay_cost = (req.baseline_budget / max(1.0, req.baseline_duration_months)) * (req.delay_months * 0.35)

    projected_budget = req.baseline_budget + budget_delta + delay_cost
    projected_duration = req.baseline_duration_months + req.delay_months

    cost_impact_abs = projected_budget - req.baseline_budget
    cost_impact_pct = (cost_impact_abs / req.baseline_budget * 100.0) if req.baseline_budget > 0 else 0.0

    contingency_needed = projected_budget * (req.contingency_rate_pct / 100.0)

    # Risk categorization
    if cost_impact_pct > 25.0 or req.delay_months >= 3.0:
        risk: Literal["Low", "Medium", "High", "Critical"] = "Critical"
        narrative = (f"Critical risk: Combined budget slip and {req.delay_months}m schedule delay pushes total "
                     f"exposure by {cost_impact_pct:.1f}%.")
    elif cost_impact_pct > 10.0 or req.delay_months >= 1.0:
        risk = "High"
        narrative = (f"High variance: Budget increases by {cost_impact_pct:.1f}%. "
                     "Immediate executive mitigation is advised.")
    elif cost_impact_pct > 0.0:
        risk = "Medium"
        narrative = "Moderate variance: Expected slippage is within standard buffer limits."
    else:
        risk = "Low"
        narrative = "Low risk: Performance is expected to track within baseline tolerances."

    return WhatIfScenarioResult(
        original_budget=round(req.baseline_budget, 2),
        projected_budget=round(projected_budget, 2),
        original_duration=round(req.baseline_duration_months, 1),
        projected_duration=round(projected_duration, 1),
        cost_impact_abs=round(cost_impact_abs, 2),
        cost_impact_pct=round(cost_impact_pct, 2),
        recommended_contingency=round(contingency_needed, 2),
        risk_level=risk,
        executive_assessment=narrative,
    )


def compute_correlation_matrix(
    columns: dict[str, list[float]],
) -> list[CorrelationPair]:
    """Calculate Pearson correlation coefficient between all pairs of numerical metrics."""
    keys = list(columns.keys())
    results: list[CorrelationPair] = []

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            k1, k2 = keys[i], keys[j]
            v1, v2 = columns[k1], columns[k2]
            n = min(len(v1), len(v2))
            if n < 3:
                continue

            sub1 = v1[:n]
            sub2 = v2[:n]
            m1 = sum(sub1) / n
            m2 = sum(sub2) / n

            num = sum((a - m1) * (b - m2) for a, b in zip(sub1, sub2, strict=True))
            den1 = sum((a - m1) ** 2 for a in sub1)
            den2 = sum((b - m2) ** 2 for b in sub2)

            den = math.sqrt(den1 * den2)
            r = (num / den) if den != 0 else 0.0
            r = max(-1.0, min(1.0, r))

            strength: Literal["Strong Positive", "Moderate Positive", "Weak/None", "Moderate Negative",
                              "Strong Negative"]
            if r >= 0.7:
                strength = "Strong Positive"
            elif r >= 0.3:
                strength = "Moderate Positive"
            elif r <= -0.7:
                strength = "Strong Negative"
            elif r <= -0.3:
                strength = "Moderate Negative"
            else:
                strength = "Weak/None"

            results.append(
                CorrelationPair(
                    metric_x=k1,
                    metric_y=k2,
                    pearson_r=round(r, 4),
                    strength=strength,
                )
            )

    return results


def convert_currency(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> dict[str, Any]:
    """Convert amount using exchange rate registry with deterministic provenance."""
    c_from = from_currency.upper().strip()
    c_to = to_currency.upper().strip()

    if c_from not in FX_RATES_TO_USD:
        raise ValueError(f"Unsupported source currency: '{from_currency}'. Supported: {list(FX_RATES_TO_USD.keys())}")
    if c_to not in FX_RATES_TO_USD:
        raise ValueError(f"Unsupported target currency: '{to_currency}'. Supported: {list(FX_RATES_TO_USD.keys())}")

    # Convert to USD anchor, then to target
    amount_usd = amount * FX_RATES_TO_USD[c_from]
    converted = amount_usd / FX_RATES_TO_USD[c_to]
    effective_rate = FX_RATES_TO_USD[c_from] / FX_RATES_TO_USD[c_to]

    return {
        "original_amount": round(amount, 2),
        "from_currency": c_from,
        "converted_amount": round(converted, 2),
        "to_currency": c_to,
        "exchange_rate": round(effective_rate, 6),
    }


def normalize_multilingual_prompt(prompt: str) -> str:
    """Normalize common multilingual phrasing into English PMO tokens."""
    p = prompt.strip()
    # Simple semantic mappings for common multilingual requests without external API dependency
    replacements = {
        "مشروع": "project",
        "المشاريع": "projects",
        "ميزانية": "budget",
        "الحالة": "status",
        "نشط": "active",
        "تكلفة": "cost",
        "projets": "projects",
        "projet": "project",
        "coût": "cost",
        "budget": "budget",
        "statut": "status",
        "actif": "active",
        "proyectos": "projects",
        "proyecto": "project",
        "costo": "cost",
        "presupuesto": "budget",
        "estado": "status",
        "activo": "active",
    }
    tokens = p.split()
    normalized_tokens = [replacements.get(t.lower(), t) for t in tokens]
    return " ".join(normalized_tokens)
