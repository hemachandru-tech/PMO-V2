"""AI-Powered Dashboard Generation Engine (PMO V2).

Transforms natural language prompts into complete multi-widget dashboards with intelligent chart selection,
adaptive grid layouts, and semantic grounding.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from pydantic import BaseModel, Field

from tela_query_spec.model import ModelSnapshot


class LayoutSlot(BaseModel):
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=12)


class GeneratedWidget(BaseModel):
    id: str
    title: str
    visual_type: Literal["kpi_card", "ranked_bar", "trend_line", "donut", "stacked_bar", "table"]
    measure_key: str | None = None
    dimension_keys: list[str] = Field(default_factory=list)
    layout: LayoutSlot
    description: str
    sample_sql: str | None = None


class AutoDashboardPlan(BaseModel):
    title: str
    theme: str = "executive"
    layout_type: str = "grid"
    executive_summary: str
    recommended_filters: list[str] = Field(default_factory=list)
    widgets: list[GeneratedWidget] = Field(default_factory=list)


def recommend_chart_type(
    is_time_series: bool,
    cardinality: int,
    num_measures: int,
    is_part_of_whole: bool = False,
) -> Literal["kpi_card", "ranked_bar", "trend_line", "donut", "stacked_bar", "table"]:
    """Select the optimal visual representation based on data shape and business semantics."""
    if num_measures == 1 and cardinality <= 1 and not is_time_series:
        return "kpi_card"
    if is_time_series:
        return "trend_line"
    if is_part_of_whole and 2 <= cardinality <= 6:
        return "donut"
    if cardinality > 12 or num_measures > 2:
        return "table"
    return "ranked_bar"


def plan_dashboard_from_intent(
    prompt: str,
    snapshot: ModelSnapshot,
    max_widgets: int = 6,
) -> AutoDashboardPlan:
    """Generate a structured, governed dashboard plan matching user intent and semantic entities."""
    words = set(re.findall(r"[a-z0-9_]+", prompt.lower()))
    
    # 1. Identify relevant entities & measures from snapshot
    measures = snapshot.measures
    matched_measures = [
        m for m in measures 
        if any(w in m.key.lower() or w in m.label.lower() for w in words)
    ]
    if not matched_measures and measures:
        matched_measures = measures[:4]

    # 2. Identify dimensions for breakdowns & shared filters
    dimensions = snapshot.dimensions
    time_dims = [d for d in dimensions if d.is_temporal]
    cat_dims = [d for d in dimensions if not d.is_temporal]

    # 3. Build Widgets with Adaptive Layout Grid (12-column grid)
    widgets: list[GeneratedWidget] = []
    
    # Tier 1: Headline KPI Cards (Top Row)
    kpi_count = min(len(matched_measures), 3)
    kpi_width = 12 // max(1, kpi_count)
    for i in range(kpi_count):
        m = matched_measures[i]
        widgets.append(GeneratedWidget(
            id=f"w_kpi_{i+1}",
            title=m.label,
            visual_type="kpi_card",
            measure_key=m.key,
            dimension_keys=[],
            layout=LayoutSlot(x=i * kpi_width, y=0, w=kpi_width, h=3),
            description=f"Headline KPI tracking {m.label}.",
        ))

    # Tier 2: Primary Visualizations (Middle Row)
    row_y = 3
    if time_dims and matched_measures:
        # Time Trend
        widgets.append(GeneratedWidget(
            id="w_trend_1",
            title=f"{matched_measures[0].label} Trend",
            visual_type="trend_line",
            measure_key=matched_measures[0].key,
            dimension_keys=[time_dims[0].key],
            layout=LayoutSlot(x=0, y=row_y, w=8, h=5),
            description=f"Historical trend analysis over {time_dims[0].label}.",
        ))
        
        # Category Breakdown
        cat_dim = cat_dims[0] if cat_dims else None
        if cat_dim:
            widgets.append(GeneratedWidget(
                id="w_breakdown_1",
                title=f"Distribution by {cat_dim.label}",
                visual_type="donut" if len(cat_dims) > 1 else "ranked_bar",
                measure_key=matched_measures[0].key,
                dimension_keys=[cat_dim.key],
                layout=LayoutSlot(x=8, y=row_y, w=4, h=5),
                description=f"Composition breakdown by {cat_dim.label}.",
            ))
        row_y += 5

    # Tier 3: Detailed Breakdown Table (Bottom Row)
    detail_dim = cat_dims[1].key if len(cat_dims) > 1 else (cat_dims[0].key if cat_dims else None)
    if detail_dim and matched_measures:
        widgets.append(GeneratedWidget(
            id="w_table_detail",
            title="Detailed Performance Ledger",
            visual_type="table",
            measure_key=matched_measures[0].key,
            dimension_keys=[detail_dim],
            layout=LayoutSlot(x=0, y=row_y, w=12, h=6),
            description=f"Granular audit ledger grouped by {detail_dim}.",
        ))

    # Extract clean title from prompt
    clean_title = prompt.strip().capitalize()
    if len(clean_title) > 60:
        clean_title = clean_title[:57] + "..."
    if not clean_title or clean_title.lower().startswith("create"):
        clean_title = "Executive PMO Overview"

    return AutoDashboardPlan(
        title=clean_title,
        executive_summary=(
            f"Automated intelligence dashboard generated for intent: '{prompt}'. "
            f"Configured with {len(widgets)} interactive widgets covering key KPIs and trends."
        ),
        recommended_filters=[d.key for d in cat_dims[:3]],
        widgets=widgets[:max_widgets],
    )
