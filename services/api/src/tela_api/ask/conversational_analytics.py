"""Conversational Data Intelligence & Multi-Turn Analytics Engine (PMO V2).

Provides multi-turn context retention, conversational drill-downs, root cause analysis (RCA),
and intelligent follow-up generation.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class FollowUpIntent(BaseModel):
    is_follow_up: bool
    operation: str  # "filter", "drill_down", "compare", "root_cause", "new_topic"
    added_filters: dict[str, str] = Field(default_factory=dict)
    added_breakdowns: list[str] = Field(default_factory=list)
    resolved_query: str


class RootCauseContribution(BaseModel):
    dimension_member: str
    previous_value: float
    current_value: float
    variance: float
    impact_pct: float
    direction: str  # "increased", "decreased", "neutral"


class RootCauseResult(BaseModel):
    metric_name: str
    total_previous: float
    total_current: float
    net_variance: float
    primary_driver: str
    contributions: list[RootCauseContribution]
    executive_narrative: str


def detect_follow_up_intent(
    current_prompt: str,
    previous_query: str | None = None,
    previous_filters: dict[str, str] | None = None,
) -> FollowUpIntent:
    """Analyze if the prompt continues the conversation or starts a new topic."""
    p_lower = current_prompt.lower().strip()
    prev_filters = dict(previous_filters or {})

    # Check for follow-up linguistic markers
    followup_markers = ["now ", "only ", "what about", "and for", "break down by", "filter by", "why did",
                        "show me just"]
    is_marker_present = any(p_lower.startswith(m) or f" {m}" in p_lower for m in followup_markers)

    if not previous_query or (not is_marker_present and len(p_lower.split()) > 7):
        return FollowUpIntent(
            is_follow_up=False,
            operation="new_topic",
            resolved_query=current_prompt
        )

    # 1. Detect filter operations
    filter_match = re.search(r"(?:filter by|only|for)\s+([a-zA-Z0-9_]+)\s*=\s*['\"]?([^'\"]+)['\"]?",
                             current_prompt, re.IGNORECASE)
    if filter_match:
        dim, val = filter_match.group(1).lower(), filter_match.group(2).strip()
        prev_filters[dim] = val
        return FollowUpIntent(
            is_follow_up=True,
            operation="filter",
            added_filters={dim: val},
            resolved_query=f"{previous_query} filtered by {dim} = '{val}'"
        )

    # 2. Detect breakdown / drill-down operations
    breakdown_match = re.search(r"(?:break down by|by|grouped by)\s+([a-z0-9_]+)", p_lower)
    if breakdown_match:
        dim = breakdown_match.group(1)
        return FollowUpIntent(
            is_follow_up=True,
            operation="drill_down",
            added_breakdowns=[dim],
            resolved_query=f"{previous_query} broken down by {dim}"
        )

    # 3. Detect root cause inquiry
    if any(q in p_lower for q in ["why did", "what caused", "why is", "root cause"]):
        return FollowUpIntent(
            is_follow_up=True,
            operation="root_cause",
            resolved_query=f"Root cause decomposition of {previous_query}"
        )

    # Default follow-up chaining
    return FollowUpIntent(
        is_follow_up=True,
        operation="refine",
        resolved_query=f"{previous_query} ({current_prompt})"
    )


def compute_root_cause(
    metric_name: str,
    baseline_data: list[tuple[str, float]],
    current_data: list[tuple[str, float]],
) -> RootCauseResult:
    """Analyze variance across breakdown dimensions to isolate the primary driver of a change."""
    base_map = dict(baseline_data)
    curr_map = dict(current_data)
    all_keys = set(base_map.keys()) | set(curr_map.keys())

    tot_base = sum(base_map.values())
    tot_curr = sum(curr_map.values())
    net_var = tot_curr - tot_base

    contributions: list[RootCauseContribution] = []
    for k in all_keys:
        v0 = base_map.get(k, 0.0)
        v1 = curr_map.get(k, 0.0)
        diff = v1 - v0
        impact = (diff / abs(net_var) * 100) if net_var != 0 else 0.0
        direction = "increased" if diff > 0 else ("decreased" if diff < 0 else "neutral")
        contributions.append(RootCauseContribution(
            dimension_member=k,
            previous_value=round(v0, 2),
            current_value=round(v1, 2),
            variance=round(diff, 2),
            impact_pct=round(impact, 1),
            direction=direction,
        ))

    # Sort by absolute variance impact
    contributions.sort(key=lambda c: -abs(c.variance))

    primary = contributions[0] if contributions else None
    primary_name = primary.dimension_member if primary else "None"

    narrative = (
        f"{metric_name} changed by {net_var:+,.1f} ({((tot_curr - tot_base) / tot_base * 100):+.1f}%). "
        f"The primary driver was '{primary_name}', contributing {primary.variance:+,.1f} "
        f"({abs(primary.impact_pct):.1f}% of total net variance)."
        if primary and tot_base > 0 else
        f"Net variance for {metric_name} is {net_var:+,.1f}."
    )

    return RootCauseResult(
        metric_name=metric_name,
        total_previous=round(tot_base, 2),
        total_current=round(tot_curr, 2),
        net_variance=round(net_var, 2),
        primary_driver=primary_name,
        contributions=contributions[:8],
        executive_narrative=narrative,
    )


def generate_intelligent_suggestions(
    query_text: str,
    returned_columns: list[str],
) -> list[str]:
    """Generate 3 contextual, high-value proactive analytics prompts."""
    suggestions: list[str] = []
    q_low = query_text.lower()

    if "project" in q_low:
        suggestions.append("Which projects are currently overdue by more than 30 days?")
        suggestions.append("Show project distribution by overall RAG health status.")
        suggestions.append("Break down projects by delivery manager and budget variance.")
    elif "cost" in q_low or "budget" in q_low or "revenue" in q_low:
        suggestions.append("Compare actual vs baselined costs for active projects.")
        suggestions.append("Which departments have the largest budget overrun?")
        suggestions.append("Show monthly expenditure trend over the last 6 months.")
    elif "delivery" in q_low or "metric" in q_low or "speed" in q_low:
        suggestions.append("What is the median speed across delivery tiers?")
        suggestions.append("Show failure and error rates broken down by model key.")
        suggestions.append("Compare weekly delivery volumes month-over-month.")
    else:
        suggestions.append("Show historical trend over time.")
        suggestions.append("Highlight outliers and anomalous entries in this data.")
        suggestions.append("Export this report as an executive presentation.")

    return suggestions[:3]
