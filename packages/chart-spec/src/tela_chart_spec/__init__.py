"""Chart spec and visual suitability (RULE-008; US-050). Shared with the web app via generated types."""

from tela_chart_spec.suitability import (
    REASONS,
    VISUALS,
    ChartSpec,
    ResultShape,
    choose_visual,
    grid_axes,
    status_tone,
    suitability,
    to_chart_spec,
)

__all__ = ["REASONS", "VISUALS", "ChartSpec", "ResultShape", "choose_visual", "grid_axes", "status_tone", "suitability",
           "to_chart_spec"]
