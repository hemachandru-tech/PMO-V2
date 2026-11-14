"""Report plan gold set (US-243, for the US-246 final gate): requests with the plan a Creator should get.

``packs/pmo/fixtures/report_plan_gold.yaml`` holds 20 report requests on the synthetic TPG fixture with what a right
plan contains (measure groups, breakdowns, catalogue sections, page filters, gaps, title words to avoid). ``score``
lists what a plan misses; a plan with no misses is right. The gate plans each request with LLM review off and on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tela_api.packs.manifest import DEFAULT_PACKS_DIR
from tela_query_spec.spec import ref_key

GOLD = DEFAULT_PACKS_DIR / "pmo" / "fixtures" / "report_plan_gold.yaml"


def load(path: Path = GOLD) -> list[dict[str, Any]]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8"))["cases"])


def score(plan: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    """What ``plan`` misses against ``expect`` (empty when the plan is right)."""
    widgets = [w for s in plan["sections"] for w in s["widgets"]]
    measures = {ref_key(r) for w in widgets for r in
                [w["spec"]["measure"]["ref"], *(m["ref"] for m in w["spec"].get("extra_measures", []))]}
    breakdowns = {ref_key(b["ref"]) for w in widgets for b in w["spec"].get("breakdowns", [])}
    sections = {s["key"] for s in plan["sections"]}
    filters = {ref_key(f["ref"]) for f in plan["page_filters"]}
    gaps = " ".join(f"{g['request_part']} {g['reason']}" for g in plan["gaps"]).lower()
    misses = [f"no measure of {'/'.join(group)}" for group in expect.get("measures", [])
              if not measures & set(group)]
    misses += [f"no breakdown by {k}" for k in expect.get("breakdowns", []) if k not in breakdowns]
    misses += [f"no section {k}" for k in expect.get("sections", []) if k not in sections]
    misses += [f"no page filter {k}" for k in expect.get("page_filters", []) if k not in filters]
    misses += [f"no gap for {w}" for w in expect.get("gaps", []) if w.lower() not in gaps]
    misses += [f"title says {w}" for w in expect.get("title_without", []) if w.lower() in plan["title"].lower()]
    return misses
