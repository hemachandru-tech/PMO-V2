"""PMO calculated health (RULE-033 weights and bands, RULE-034 coverage, RULE-035 overrides).

A record-level calculator of the PMO pack (``pmo_health_v1``). It takes one
open project's fields and the reporting date and returns the component scores,
the coverage, the score and the class with any override reason. The score is
rounded to one decimal for display; the class uses the unrounded value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

WEIGHTS = {"schedule": 0.35, "cost": 0.25, "progress": 0.25, "issues": 0.15}
MIN_COVERAGE = 0.60


@dataclass
class Health:
    components: dict[str, float] = field(default_factory=dict)
    coverage: float = 0.0
    score: float | None = None  # unrounded
    status: str = "Insufficient data"
    override_applied: bool = False
    reason: str | None = None

    @property
    def display_score(self) -> float | None:
        return None if self.score is None else round(self.score, 1)


def _d(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def schedule_score(planned: date | None, baseline: date | None, actual: date | None, as_of: date) -> float | None:
    if planned is None and baseline is None:
        return None
    reference = baseline or planned
    assert reference is not None
    if actual is not None:
        slip = (actual - reference).days
    elif planned is not None and planned < as_of:
        slip = (as_of - min(planned, reference)).days
    else:
        slip = ((planned or reference) - reference).days
    if slip <= 0:
        return 100.0
    if slip <= 15:
        return 75.0
    if slip <= 30:
        return 50.0
    if slip <= 60:
        return 25.0
    return 0.0


def cost_score(budget: float | None, cost: float | None) -> float | None:
    if budget is None or cost is None or budget <= 0 or cost < 0:
        return None
    variance = (cost - budget) / budget * 100
    if variance <= 0:
        return 100.0
    if variance <= 10:
        return 75.0
    if variance <= 25:
        return 50.0
    if variance <= 50:
        return 25.0
    return 0.0


def progress_score(percent: float | None, start: date | None, planned: date | None, as_of: date) -> float | None:
    if percent is None or not 0 <= percent <= 100 or start is None or planned is None:
        return None
    total = (planned - start).days
    if total <= 0:
        return None
    expected = min(max((as_of - start).days / total * 100, 0.0), 100.0)
    gap = percent - expected
    if gap >= -5:
        return 100.0
    if gap >= -15:
        return 75.0
    if gap >= -25:
        return 50.0
    if gap >= -50:
        return 25.0
    return 0.0


def issues_score(kpi: str | None) -> float | None:
    return {"green": 100.0, "yellow": 50.0, "amber": 50.0, "red": 0.0}.get((kpi or "").strip().lower())


def calculate(project: dict[str, Any], as_of: date) -> Health:
    planned = _d(project.get("planned_deployment_date"))
    components = {
        "schedule": schedule_score(planned, _d(project.get("first_baselined_deployment_date")),
                                   _d(project.get("actual_deployment_date")), as_of),
        "cost": cost_score(project.get("approved_budget_value_usd"), project.get("cost_total_usd")),
        "progress": progress_score(project.get("percent_complete"),
                                   _d(project.get("actual_project_start_date")) or _d(project.get("created_on")),
                                   planned, as_of),
        "issues": issues_score(project.get("issue_kpi")),
    }
    available = {k: v for k, v in components.items() if v is not None}
    coverage = round(sum(WEIGHTS[k] for k in available), 10)
    health = Health(components=available, coverage=coverage)
    if coverage < MIN_COVERAGE:
        return health
    score = sum(available[k] * WEIGHTS[k] for k in available) / coverage
    health.score = score
    status = "Green" if score >= 75 else "Amber" if score >= 50 else "Red"
    overrides = [
        ("schedule", 0.0, "Red", "Schedule score is 0 (fatal delay)"),
        ("issues", 0.0, "Red", "Issues score is 0"),
        ("cost", 0.0, "Red", "Cost score is 0 (severe overrun)"),
    ]
    for comp, value, forced, reason in overrides:
        if available.get(comp) == value:
            health.status, health.override_applied, health.reason = forced, True, reason
            return health
    if available.get("cost") == 25.0 and status == "Green":
        health.status, health.override_applied, health.reason = "Amber", True, "Cost score is 25 (capped at Amber)"
        return health
    health.status = status
    return health
