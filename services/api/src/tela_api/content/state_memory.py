"""Interactive Dashboard State Management & Context Memory Engine (PMO V2).

Maintains cross-filtering state, propagates dynamic filters across widgets,
and manages conversational Pin-to-Dashboard workflows.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class FilterState(BaseModel):
    dimension_key: str
    selected_values: list[str]
    operator: str = "in"  # "in", "equals", "range"


class DashboardSessionState(BaseModel):
    session_id: str
    dashboard_id: str
    active_filters: dict[str, FilterState] = Field(default_factory=dict)
    active_drill_path: list[str] = Field(default_factory=list)
    last_updated_at: str


class PinTurnRequest(BaseModel):
    turn_id: str
    dashboard_id: str
    widget_title: str
    visual_type: str = "table"
    sql: str
    explanation: str | None = None


def apply_cross_filter(
    current_state: DashboardSessionState,
    dimension_key: str,
    selected_value: str,
) -> DashboardSessionState:
    """Propagate a clicked chart element as an active cross-filter across all widgets."""
    state = current_state.model_copy(deep=True)
    if dimension_key in state.active_filters:
        vals = state.active_filters[dimension_key].selected_values
        if selected_value in vals:
            vals.remove(selected_value)
            if not vals:
                del state.active_filters[dimension_key]
        else:
            vals.append(selected_value)
    else:
        state.active_filters[dimension_key] = FilterState(
            dimension_key=dimension_key,
            selected_values=[selected_value],
            operator="in"
        )
    state.last_updated_at = datetime.datetime.now(datetime.UTC).isoformat()
    return state


def clear_cross_filters(session_id: str, dashboard_id: str) -> DashboardSessionState:
    """Clear all active cross-filters returning to default dashboard state."""
    return DashboardSessionState(
        session_id=session_id,
        dashboard_id=dashboard_id,
        active_filters={},
        active_drill_path=[],
        last_updated_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
