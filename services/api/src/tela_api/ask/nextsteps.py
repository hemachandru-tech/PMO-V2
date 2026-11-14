"""Ask next (US-186, FEAT-065): three follow-ups built from the spec and the answer's facts, never by a model.

1. **Break down by** the strongest driver dimension — the dimension whose members explain most of the change (found
   by ``driver_candidates`` + ``insights.drivers`` in the orchestrator), when the answer is not already broken down
   by it.
2. **Compare with the previous period** — when the answer has a time range and no comparison yet.
3. **Drill into** the outlier (or the lead of a concentration) — a filter to that member of the breakdown.

Each follow-up is ``{"kind", "label", "ops"}``; ``ops`` are refinement chip ops (``refine.apply_ops``), so a click
re-runs the answer through the normal refine path.
"""

from __future__ import annotations

from typing import Any

from tela_api.ask.insights import NOT_SET
from tela_query_spec.model import Dimension, ModelSnapshot
from tela_query_spec.spec import ref_key

MAX_CANDIDATES = 3


def driver_candidates(spec: dict[str, Any], snap: ModelSnapshot) -> list[Dimension]:
    """Dimensions worth trying as the driver of a change: plain (not temporal) dimensions of the measure's entity
    that the answer is not broken down or filtered to one value by, in the model's order (packs list the main
    breakdowns — region, portfolio, programme — first)."""
    measure = snap.measure(ref_key(spec["measure"]["ref"]))
    if measure is None:
        return []
    used = {ref_key(b["ref"]) for b in spec.get("breakdowns", [])}
    pinned = {ref_key(f["ref"]) for f in spec.get("filters", []) if f.get("op") == "=" or (
        f.get("op") == "in" and len(f.get("values") or []) == 1)}
    dims = [d for d in snap.dimensions if d.entity == measure.entity and not d.is_temporal and d.key not in used
            and d.key not in pinned]
    return dims[:MAX_CANDIDATES]


def strongest(found: list[tuple[Dimension, dict[str, Any]]]) -> tuple[Dimension, dict[str, Any]] | None:
    """The driver fact whose lead member explains the largest share of the change."""
    if not found:
        return None
    return max(found, key=lambda x: abs(x[1]["numbers"]["contributions"][0]["share_pct"]) * x[1]["priority"])


def _stored(dim: Dimension, label: str) -> str:
    back: dict[str, str] = {shown: raw for raw, shown in dim.value_labels.items()}
    return back.get(label, label)


def follow_ups(spec: dict[str, Any], snap: ModelSnapshot, facts: list[dict[str, Any]], *,
               driver: dict[str, Any] | None = None, can_compare: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used = {ref_key(b["ref"]) for b in spec.get("breakdowns", [])}
    if driver is not None and (ref := (driver.get("dimension") or {}).get("ref")) and ref_key(ref) not in used:
        out.append({"kind": "driver", "label": f"Break down by {driver['dimension']['label'].lower()}",
                    "ops": [{"op": "add_breakdown", "ref": ref}]})
    if not spec.get("compare") and (spec.get("time") or {}).get("range"):
        if can_compare:
            out.append({"kind": "compare", "label": "Compare with the previous period",
                        "ops": [{"op": "compare_previous"}]})
        else:
            # Offer year-over-year when the previous period spec cannot be built but a range exists (US-178)
            out.append({"kind": "compare", "label": "Compare year over year",
                        "ops": [{"op": "compare_last_year"}]})
    # Offer "Show running total" on a trend without one (US-177)
    if (spec.get("time") or {}).get("grain") and "running_total" not in spec and not spec.get("compare"):
        out.append({"kind": "window", "label": "Show running total",
                    "ops": [{"op": "set_running_total"}]})
    first = next((snap.dimension(ref_key(b["ref"])) for b in spec.get("breakdowns", [])), None)
    if first is not None and not first.computed:
        target = next((f for f in facts if f["type"] == "outlier"), None) or next(
            (f for f in facts if f["type"] == "concentration" and f["members"]), None)
        member = target["members"][0] if target and target["members"] else None
        if member and member != NOT_SET:
            out.append({"kind": "drill", "label": f"Drill into {member}",
                        "ops": [{"op": "add_filter", "ref": f"dimension:{first.key}",
                                 "values": [_stored(first, member)]}]})
    return out[:3]
