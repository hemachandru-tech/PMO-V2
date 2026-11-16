"""Advanced Data Transformation & Time Intelligence Engine (PMO V2).

Provides dynamic pivoting, Period-over-Period (PoP) variance calculations,
metric normalization, and dynamic grouping across tabular query results.
"""

from __future__ import annotations

import math
from typing import Any
from pydantic import BaseModel, Field


class TimeSeriesPoint(BaseModel):
    period: str
    value: float
    previous_value: float | None = None
    change_abs: float | None = None
    change_pct: float | None = None


class PivotedTable(BaseModel):
    row_headers: list[str]
    column_headers: list[str]
    matrix: list[list[Any]]


def compute_time_intelligence(
    series: list[tuple[str, float]],
) -> list[TimeSeriesPoint]:
    """Compute Period-over-Period (PoP), Month-over-Month (MoM) growth rates and variances."""
    out: list[TimeSeriesPoint] = []
    prev_val: float | None = None
    
    for period, val in series:
        if prev_val is None:
            out.append(TimeSeriesPoint(
                period=period,
                value=round(val, 2),
                previous_value=None,
                change_abs=None,
                change_pct=None,
            ))
        else:
            diff = val - prev_val
            pct = (diff / prev_val * 100) if prev_val != 0 else 0.0
            out.append(TimeSeriesPoint(
                period=period,
                value=round(val, 2),
                previous_value=round(prev_val, 2),
                change_abs=round(diff, 2),
                change_pct=round(pct, 1),
            ))
        prev_val = val
        
    return out


def pivot_tabular_data(
    rows: list[list[Any]],
    row_idx: int,
    col_idx: int,
    val_idx: int,
    agg_func: str = "sum",
) -> PivotedTable:
    """Pivot tabular results into a cross-tabulated matrix (e.g. Region x Status)."""
    unique_rows: list[str] = []
    unique_cols: list[str] = []
    cell_values: dict[tuple[str, str], list[float]] = {}
    
    for r in rows:
        row_key = str(r[row_idx] if r[row_idx] is not None else "None")
        col_key = str(r[col_idx] if r[col_idx] is not None else "None")
        raw_val = r[val_idx]
        val = float(raw_val) if isinstance(raw_val, int | float) else 0.0
        
        if row_key not in unique_rows:
            unique_rows.append(row_key)
        if col_key not in unique_cols:
            unique_cols.append(col_key)
            
        cell_values.setdefault((row_key, col_key), []).append(val)
        
    matrix: list[list[Any]] = []
    for r_k in unique_rows:
        row_cells: list[Any] = [r_k]
        for c_k in unique_cols:
            vals = cell_values.get((r_k, c_k), [])
            if not vals:
                row_cells.append(0.0)
            elif agg_func == "avg":
                row_cells.append(round(sum(vals) / len(vals), 2))
            elif agg_func == "count":
                row_cells.append(len(vals))
            else:  # sum
                row_cells.append(round(sum(vals), 2))
        matrix.append(row_cells)
        
    return PivotedTable(
        row_headers=["Category"],
        column_headers=["Category", *unique_cols],
        matrix=matrix,
    )


def calculate_variance_metrics(
    actual: float,
    target: float,
) -> dict[str, float]:
    """Compute absolute variance, percentage variance, and completion ratios."""
    var_abs = actual - target
    var_pct = (var_abs / target * 100) if target != 0 else 0.0
    completion_ratio = (actual / target) if target != 0 else 0.0
    
    return {
        "actual": round(actual, 2),
        "target": round(target, 2),
        "variance_abs": round(var_abs, 2),
        "variance_pct": round(var_pct, 1),
        "completion_ratio": round(completion_ratio, 3),
    }
