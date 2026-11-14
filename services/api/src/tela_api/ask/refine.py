"""Refine an answer in words or chips, and Undo (FEAT-020, FEAT-022; US-060, US-061, US-064).

A refinement changes the **spec**, never the rule: filters, top N, chart type, breakdowns, time grain and
range, extra measures and a previous-period comparison. Prior filters are carried forward explicitly
(the S30 entity-drop lesson): every op starts from a copy of the whole previous spec.

A request that would change how a certified measure counts ("count cancelled projects too", "treat on
hold as active") is not a refinement: it is explained and redirected to a personal field or a model
change (A1), and nothing runs.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from tela_api.ask.freehand import FreehandUnsupported, rule_chips, set_condition
from tela_api.ask.planner import (
    GRAINS,
    RANGES,
    VISUAL_WORDS,
    chart_for,
    match_dimension,
    match_measures,
    norm,
    requested_visual,
    words,
)
from tela_api.query.compiler import previous_window, resolve_range
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import Dimension, Measure, ModelSnapshot
from tela_query_spec.spec import MAX_MEASURES, TimeRange, ref_key, ref_kind

OPS = {"set_limit", "set_visual", "add_filter", "remove_filter", "add_breakdown", "remove_breakdown", "set_time",
       "set_range", "clear_time", "compare_previous", "compare_last_year", "add_measure", "remove_measure", "sort",
       "set_condition", "set_running_total", "set_moving_avg", "set_rank", "set_having", "set_measure",
       "remove_window"}
RULE_CHANGE = [
    re.compile(r"\b(count|counting|include|including)\b.*\b(too|also|as well)\b"),
    re.compile(r"\btreat\b.*\bas\b"),
    re.compile(r"\b(redefine|change (the )?(definition|rule|calculation|formula)|calculated? (it )?differently)\b"),
    re.compile(r"\b(should|shouldn ?t|should not|don ?t|do not|stop|start)\s+(count|counting|include|including)\b"),
    re.compile(r"\b(also|additionally)\s+count\b"),
]
EXPLAIN = re.compile(r"\b(why|explain|what does|what is driving|what drives|cause|caused|reason|how is this|how was "
                     r"this|what does this mean|meaning)\b")
FILTER_CUE = r"(only|just|in|for|to|exclude|excluding|except|without|not|filter(?:ed)?(?: to| by| on)?|where)"
REDIRECT = ("That would change how {measure} is counted, and a refinement can't change a certified rule. "
            "Make a personal field (grouping or band) for your own analysis, or ask your PMO admin to change the "
            "model definition.")


class ChipOp(BaseModel):
    op: str = Field(max_length=30)
    value: Any = None
    ref: str | None = Field(default=None, max_length=140)
    operator: str | None = Field(default=None, max_length=10)
    values: list[Any] = Field(default_factory=list, max_length=50)
    grain: str | None = None
    relative: str | None = None
    dir: str | None = None
    # set_condition (US-165): one condition of a free-hand rule, at ``path`` ("1.0"), becomes field/operator/value.
    path: str | None = Field(default=None, max_length=20, pattern=r"^\d{1,2}(\.\d{1,2}){0,4}$|^$")
    field: str | None = Field(default=None, max_length=130)


class RefineIn(BaseModel):
    instruction: str | None = Field(default=None, min_length=1, max_length=500)
    chip_ops: list[ChipOp] = Field(default_factory=list, max_length=10)


# --------------------------------------------------------------------------- applying ops


def apply_ops(spec: dict[str, Any], ops: list[dict[str, Any]], snap: ModelSnapshot) -> tuple[dict[str, Any], list[str]]:
    """Apply chip ops to a copy of the spec. Returns the new spec and a plain-language summary per op."""
    out = copy.deepcopy(spec)
    out.setdefault("filters", [])
    out.setdefault("breakdowns", [])
    said: list[str] = []
    for op in ops:
        kind = op.get("op")
        if kind not in OPS:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"'{kind}' is not a refinement Tela knows.")
        if kind == "set_limit":
            n = int(op.get("value") or 0)
            if not 1 <= n <= 1000:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Top N must be between 1 and 1,000.")
            out["limit"] = n
            direction = op.get("dir") or "desc"
            out["sort"] = [{"by": "measure", "dir": direction}]
            said.append(f"{'Bottom' if direction == 'asc' else 'Top'} {n}")
        elif kind == "set_visual":
            v = str(op.get("value"))
            # a figure's tone is also a bullet's target band (US-187): it stays when switching between the two
            tone = (out.get("visual") or {}).get("tone")
            out["visual"] = {"type": v, **({"tone": tone} if tone and v in ("figure", "bullet") else {})}
            said.append(f"Shown as {v.replace('_', ' ')}")
        elif kind == "sort":
            out["sort"] = [{"by": "measure", "dir": op.get("dir") or "desc"}]
            said.append("Sorted " + ("ascending" if op.get("dir") == "asc" else "descending"))
        elif kind == "add_filter":
            ref, values = op.get("ref"), [v for v in op.get("values") or [] if v is not None]
            dim = snap.dimension(ref_key(ref)) if ref and ref.startswith("dimension:") else None
            if dim is None or not values:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a dimension and at least one value to filter on.")
            operator = op.get("operator") or ("=" if len(values) == 1 else "in")
            if operator == "not_in" or operator == "!=":
                operator = "not_in" if len(values) > 1 else "!="
            out["filters"] = [f for f in out["filters"] if f["ref"] != ref] + [
                {"ref": ref, "op": operator, "values": values}]
            label = ", ".join(dim.value_labels.get(str(v), str(v)) for v in values)
            said.append(f"{dim.label}: {'not ' if operator in ('not_in', '!=') else ''}{label}")
        elif kind == "remove_filter":
            ref = op.get("ref")
            before = len(out["filters"])
            out["filters"] = [f for f in out["filters"] if ref is not None and f["ref"] != ref] if ref else []
            if before == len(out["filters"]) and ref:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "That filter is not on this answer.")
            dim = snap.dimension(ref_key(ref)) if ref else None
            said.append(f"Removed the {dim.label if dim else ''} filter".replace("  ", " ") if ref
                        else "Removed all filters")
        elif kind == "add_breakdown":
            ref = op.get("ref") or ""
            dim = snap.dimension(ref_key(ref)) if ref.startswith("dimension:") else None
            if dim is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a dimension to break down by.")
            if dim.is_temporal:
                out["time"] = {**(out.get("time") or {}), "ref": ref, "grain": (out.get("time") or {}).get("grain")
                               or "month"}
                said.append(f"{dim.label} by {out['time']['grain']}")
                continue
            if all(b["ref"] != ref for b in out["breakdowns"]):
                if len(out["breakdowns"]) >= 2:
                    raise TelaError(ErrorCode.VALIDATION_ERROR, "An answer can break down by two things at most.")
                out["breakdowns"].append({"ref": ref})
            said.append(f"By {dim.label.lower()}")
        elif kind == "remove_breakdown":
            ref = op.get("ref")
            out["breakdowns"] = [b for b in out["breakdowns"] if ref and b["ref"] != ref] if ref else []
            said.append("Removed the breakdown")
        elif kind == "set_time":
            grain = op.get("grain") or "month"
            if grain not in ("day", "week", "month", "quarter", "year"):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick day, week, month, quarter or year.")
            ref = op.get("ref") or (out.get("time") or {}).get("ref") or default_time(out, snap)
            if ref is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "This measure has no date to trend by.")
            out["time"] = {**(out.get("time") or {}), "ref": ref, "grain": grain}
            dim = snap.dimension(ref_key(ref))
            said.append(f"{dim.label if dim else 'Date'} by {grain}")
        elif kind == "set_range":
            relative = op.get("relative")
            try:
                TimeRange(relative=relative)
            except ValueError as exc:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "That time range is not one Tela knows.") from exc
            ref = (out.get("time") or {}).get("ref") or default_time(out, snap)
            if ref is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "This measure has no date to limit by.")
            out["time"] = {**(out.get("time") or {}), "ref": ref, "range": {"relative": relative}}
            said.append(str(relative).replace("_", " ").capitalize())
        elif kind == "clear_time":
            out.pop("time", None)
            out.pop("compare", None)
            said.append("All dates")
        elif kind == "set_condition":
            adhoc = (out.get("measure") or {}).get("adhoc")
            if not adhoc:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Only a free-hand rule has conditions to change.")
            values = [v for v in op.get("values") or [] if v is not None]
            change = {"field": op.get("field"), "op": op.get("operator"),
                      **({"values": values} if op.get("operator") in ("in", "not_in") else
                         {} if op.get("operator") in ("is_null", "not_null") else {"value": op.get("value")})}
            try:
                new = set_condition(adhoc, op.get("path") or "", change, snap)
            except FreehandUnsupported as exc:
                raise TelaError(ErrorCode.VALIDATION_ERROR, exc.reason) from exc
            out["measure"] = {"ref": out["measure"]["ref"], "adhoc": new}
            said.append(f"Rule changed: {new['plain_rule']}")
        elif kind == "compare_previous":
            if not (out.get("time") or {}).get("range"):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a time range first (for example this quarter) to "
                                "compare with the previous period.")
            out["compare"] = {"kind": "previous_period"}
            said.append("Compared with the previous period")
        elif kind == "compare_last_year":
            if not (out.get("time") or {}).get("range"):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a time range first (for example this quarter) to "
                                "compare with the same period last year.")
            out["compare"] = {"kind": "same_period_last_year"}
            said.append("Compared with the same period last year")
        elif kind == "set_measure":  # US-219: another certified measure for the same answer (a forward one)
            ref = op.get("ref") or ""
            m = snap.measure(ref_key(ref)) if ref.startswith("measure:") else None
            if m is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a certified measure.")
            out["measure"] = {"ref": ref}
            for k in ("extra_measures", "derived", "share_of_total"):
                out.pop(k, None)
            dims = {d.key for d in snap.dimensions if snap.join_path(m.entity, d.entity) is not None}
            # filters and breakdowns the new measure can't reach are dropped; personal fields stay as they were
            out["filters"] = [f for f in out["filters"] if ref_kind(f["ref"]) != "dimension"
                              or ref_key(f["ref"]) in dims]
            out["breakdowns"] = [b for b in out["breakdowns"] if ref_kind(b["ref"]) != "dimension"
                                 or ref_key(b["ref"]) in dims]
            said.append(f"Measure: {m.label}")
        elif kind in ("add_measure", "remove_measure"):
            ref = op.get("ref") or ""
            m = snap.measure(ref_key(ref)) if ref.startswith("measure:") else None
            if m is None:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a certified measure.")
            extras = [e for e in out.get("extra_measures", []) if e["ref"] != ref]
            if kind == "add_measure":
                if ref == out["measure"]["ref"]:
                    continue
                if len(extras) >= MAX_MEASURES - 1:
                    raise TelaError(ErrorCode.VALIDATION_ERROR, f"An answer can show {MAX_MEASURES} measures at most.")
                extras.append({"ref": ref})
                said.append(f"Added {m.label}")
            else:
                said.append(f"Removed {m.label}")
            out["extra_measures"] = extras
        # Window operations (US-177)
        elif kind == "set_running_total":
            out["running_total"] = {}
            said.append("Running total")
        elif kind == "set_moving_avg":
            window = int(op.get("value") or 3)
            if not 2 <= window <= 24:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Moving average window must be 2 to 24.")
            out["moving_avg"] = {"window": window}
            said.append(f"{window}-period moving average")
        elif kind == "set_rank":
            out["rank"] = {}
            said.append("Show rank")
        elif kind == "set_having":
            hv_op = op.get("operator") or ">"
            hv_val = op.get("value")
            if hv_op not in ("=", "!=", "<", "<=", ">", ">=") or not isinstance(hv_val, int | float):
                raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a valid comparison and number.")
            out["having"] = {"op": hv_op, "value": hv_val}
            said.append(f"Having {hv_op} {hv_val}")
        elif kind == "remove_window":
            for key in ("top_n_per_group", "running_total", "moving_avg", "rank", "bucket", "percentile", "having"):
                out.pop(key, None)
            said.append("Removed window operations")
    return _without_orphans(out), said


def _without_orphans(spec: dict[str, Any]) -> dict[str, Any]:
    """Derived figures and shares (US-176) whose measures an op removed or swapped are dropped with them."""
    refs = {spec["measure"]["ref"], *(e["ref"] for e in spec.get("extra_measures", []))}
    if "derived" in spec:
        spec["derived"] = [d for d in spec["derived"] if {d["of"], d["to"]} <= refs]
    if "share_of_total" in spec:
        spec["share_of_total"] = [r for r in spec["share_of_total"] if r in refs]
    return spec


def apply_field(spec: dict[str, Any], field: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Use a saved personal field in an answer: a grouping or band becomes the breakdown (replacing a breakdown on
    the same base), a cross-table measure becomes the measure. The definition travels inside the spec."""
    out = copy.deepcopy(spec)
    d = field["definition"]
    refs = [r for r in out.get("definition_refs", []) if r["id"] != str(field["id"])]
    refs.append({"kind": field["kind"], "id": str(field["id"])})
    out["definition_refs"] = refs
    if field["kind"] == "join_measure":
        out["personal_measures"] = [p for p in out.get("personal_measures", []) if p["key"] != d["key"]] + [d]
        out["measure"] = {"ref": f"measure:{d['key']}"}
        return _without_orphans(out), f"Measured as {field['label']} (personal)"
    out["personal_fields"] = [p for p in out.get("personal_fields", []) if p["key"] != d["key"]] + [d]
    ref = f"personal:{d['key']}"
    breakdowns = [b for b in out.get("breakdowns", []) if b["ref"] not in (d["source"], ref)]
    out["breakdowns"] = [{"ref": ref}, *breakdowns][:2]
    return out, f"Grouped by {field['label']} (personal)"


def default_time(spec: dict[str, Any], snap: ModelSnapshot) -> str | None:
    """The first date dimension the spec's measure can reach, used when a change needs a date and none was given."""
    m = snap.measure(ref_key(spec["measure"]["ref"]))
    if m is None:
        return None
    d = next((d for d in snap.dimensions if d.is_temporal and snap.join_path(m.entity, d.entity) is not None), None)
    return f"dimension:{d.key}" if d else None


# --------------------------------------------------------------------------- words → ops (rules)


def is_rule_change(text: str) -> bool:
    q = norm(text)
    return any(p.search(q) for p in RULE_CHANGE)


def parse_instruction(text: str, spec: dict[str, Any], snap: ModelSnapshot, *, measures: list[Measure],
                      dims: list[Dimension], values: dict[str, list[str]]) -> list[dict[str, Any]]:
    q = norm(text)
    qw = set(words(text))
    ops: list[dict[str, Any]] = []
    measure = snap.measure(ref_key(spec["measure"]["ref"]))
    reachable = [d for d in dims if measure is not None and snap.join_path(measure.entity, d.entity) is not None]

    if top := re.search(r"\b(top|bottom|first|highest|lowest|only)\s+(\d{1,4})\b", q):
        ops.append({"op": "set_limit", "value": int(top.group(2)),
                    "dir": "asc" if top.group(1) in ("bottom", "lowest") else "desc"})
    visual = next((v for w, v in VISUAL_WORDS.items()
                   if re.search(rf"\b(as|to|in|into|show|use|make it)\s+(a\s+|an\s+)?{w}\b", q)), None)
    if (visual := visual or requested_visual(q)) == "chart":  # any chart: one that suits the answer (BUG-017)
        visual = chart_for(spec, snap) if measure is not None else None
    if visual:
        ops.append({"op": "set_visual", "value": visual})
    grain = next((g for w, g in GRAINS.items() if re.search(rf"\b(by|per|each|over|monthly|weekly)\s*{w}\b", q)
                  or (re.search(rf"\b{w}ly\b", q) and w in ("month", "week", "quarter"))), None)
    if grain is None and re.search(r"\b(trend|over time)\b", q):
        grain = "month"
    if grain:
        ops.append({"op": "set_time", "grain": grain})
    rng = next((r for pattern, r in RANGES if re.search(pattern, q)), None)
    if rng:
        ops.append({"op": "set_range", "relative": rng})
    if re.search(r"\b(all time|all dates|any date|no date range)\b", q):
        ops.append({"op": "clear_time"})
    if re.search(r"\b(same period last year|year over year|vs last year|compared? with last year|yoy)\b", q):
        ops.append({"op": "compare_last_year"})
    elif re.search(r"\b(compare|compared|vs|versus|previous period|period over period|last period)\b", q):
        ops.append({"op": "compare_previous"})

    for m in re.finditer(r"\b(?:by|per|split by|break(?:\s+it)?\s+down by|grouped by)\s+([a-z0-9 ]+?)(?=$|,|\b(?:and|"
                         r"in|for|as|top|with|only|this|last|next|over)\b)", q):
        part = m.group(1).strip()
        if part in GRAINS or not part:
            continue
        dim = match_dimension(part, reachable)
        if dim is not None and not dim.is_temporal:
            ops.append({"op": "add_breakdown", "ref": f"dimension:{dim.key}"})
    if re.search(r"\b(remove|drop|no|without)\s+(the\s+)?breakdown\b|\bjust the total\b", q):
        ops.append({"op": "remove_breakdown"})

    negate = bool(re.search(r"\b(exclude|excluding|except|without|not|other than)\b", q))
    if re.search(r"\b(clear|remove|drop) (all )?(the )?filters\b", q):
        ops.append({"op": "remove_filter"})
    for d in reachable:
        if d.is_temporal:
            continue
        labels = dict(d.value_labels)
        found = []
        for v in values.get(d.key, []):
            shown = labels.get(v, v)
            value = re.escape(norm(str(shown)))
            # A value counts as a filter only with a cue ("only KSA", "in KSA", "exclude KSA", "KSA only").
            if len(str(shown)) >= 2 and (re.search(rf"\b{FILTER_CUE}\s+(the\s+)?{value}\b", q)
                                         or re.search(rf"\b{value}\s+only\b", q)
                                         or (re.search(rf"\b{FILTER_CUE}\s+[a-z0-9 ]*?\b{value}\b", q)
                                         and re.search(r"\b(only|just|exclude|excluding|except|without)\b", q))):
                found.append(v)
        if found:
            ops.append({"op": "add_filter", "ref": f"dimension:{d.key}", "values": found,
                        "operator": "not_in" if negate else "in"})
        elif re.search(rf"\b(remove|drop|clear|all)\s+(the\s+)?{re.escape(norm(d.label))}s?\b(\s+filter)?", q) and \
                any(f["ref"] == f"dimension:{d.key}" for f in spec.get("filters", [])):
            ops.append({"op": "remove_filter", "ref": f"dimension:{d.key}"})

    if re.search(r"\b(add|also show|include|with|and show|plus)\b", q):
        current = {spec["measure"]["ref"], *(e["ref"] for e in spec.get("extra_measures", []))}
        for _, m in match_measures(text, measures)[:2]:
            ref = f"measure:{m.key}"
            if ref not in current and (measure is None or m.entity == measure.entity):
                ops.append({"op": "add_measure", "ref": ref})
                break
    if not ops and qw & {"descending", "ascending", "sort", "sorted"}:
        ops.append({"op": "sort", "dir": "asc" if "ascending" in qw else "desc"})
    # Window operations (US-177)
    if re.search(r"\b(running total|cumulative|cumulative sum)\b", q):
        ops.append({"op": "set_running_total"})
    if mm := re.search(r"\b(\d{1,2})\s*(?:-|\s)?\s*(?:month|period|week|quarter|day)s?\s+moving average\b", q):
        ops.append({"op": "set_moving_avg", "value": int(mm.group(1))})
    if re.search(r"\b(show|add)\s+rank\b", q):
        ops.append({"op": "set_rank"})
    if re.search(r"\b(remove|drop|clear)\s+(all\s+)?(the\s+)?window\b", q):
        ops.append({"op": "remove_window"})
    return ops


def chip_ops_from(body: RefineIn) -> list[dict[str, Any]]:
    return [op.model_dump(exclude_none=True) for op in body.chip_ops]


# --------------------------------------------------------------------------- chips and comparison


def chips_for(spec: dict[str, Any], snap: ModelSnapshot) -> list[dict[str, Any]]:
    chips: list[dict[str, Any]] = []
    adhoc = spec["measure"].get("adhoc")
    if adhoc:  # a free-hand rule (US-165): its name, then one chip per condition the person can change
        chips += [{"kind": "measure", "label": adhoc["label"], "ref": spec["measure"]["ref"], "freehand": True},
                  *rule_chips(adhoc, snap)]
    else:
        m = snap.measure(ref_key(spec["measure"]["ref"]))
        chips.append({"kind": "measure", "label": m.label if m else spec["measure"]["ref"],
                      "ref": spec["measure"]["ref"]})
    for e in spec.get("extra_measures", []):
        em = snap.measure(ref_key(e["ref"]))
        chips.append({"kind": "measure", "label": em.label if em else e["ref"], "ref": e["ref"]})
    for b in spec.get("breakdowns", []):
        d = snap.dimension(ref_key(b["ref"])) if b["ref"].startswith("dimension:") else None
        chips.append({"kind": "breakdown", "label": d.label if d else b["ref"], "ref": b["ref"]})
    t = spec.get("time") or {}
    if t.get("grain"):
        d = snap.dimension(ref_key(t["ref"]))
        chips.append({"kind": "time", "label": f"{d.label if d else 'Date'} by {t['grain']}", "ref": t["ref"]})
    if (t.get("range") or {}).get("relative"):
        chips.append({"kind": "range", "label": t["range"]["relative"].replace("_", " "), "ref": t["ref"]})
    for f in spec.get("filters", []):
        d = snap.dimension(ref_key(f["ref"])) if f["ref"].startswith("dimension:") else None
        vals = ", ".join(d.value_labels.get(str(v), str(v)) if d else str(v) for v in f.get("values", []))
        neg = "not " if f["op"] in ("!=", "not_in") else ""
        chips.append({"kind": "filter", "label": f"{d.label if d else f['ref']}: {neg}{vals}", "ref": f["ref"]})
    if spec.get("limit"):
        direction = (spec.get("sort") or [{}])[0].get("dir", "desc")
        chips.append({"kind": "limit", "label": f"{'bottom' if direction == 'asc' else 'top'} {spec['limit']}"})
    if spec.get("compare"):
        cmp = spec["compare"]
        label = "vs same period last year" if cmp.get("kind") == "same_period_last_year" else "vs previous period"
        chips.append({"kind": "compare", "label": label})
    # Window operations (US-177)
    if spec.get("top_n_per_group"):
        tn = spec["top_n_per_group"]
        d = snap.dimension(ref_key(tn["by"])) if tn.get("by", "").startswith("dimension:") else None
        chips.append({"kind": "window", "label": f"Top {tn['n']} per {d.label.lower() if d else 'group'}"})
    if "running_total" in spec:
        chips.append({"kind": "window", "label": "Running total"})
    if spec.get("moving_avg"):
        chips.append({"kind": "window", "label": f"{spec['moving_avg']['window']}-period moving avg"})
    if "rank" in spec:
        chips.append({"kind": "window", "label": "Rank"})
    if spec.get("bucket"):
        chips.append({"kind": "window", "label": f"Bucket ({spec['bucket']['k']})"})
    if spec.get("percentile"):
        chips.append({"kind": "window", "label": spec["percentile"]["p"].upper()})
    if spec.get("having"):
        hv = spec["having"]
        chips.append({"kind": "window", "label": f"Having {hv['op']} {hv['value']}"})
    return chips


def compare_window(spec: dict[str, Any], as_of: date) -> tuple[dict[str, Any], str] | None:
    """The spec of the period the answer is compared with, and its name: the same period last year when the spec
    asks for it (US-178), else the equal-length period just before."""
    if (spec.get("compare") or {}).get("kind") == "same_period_last_year":
        return same_period_last_year_spec(spec, as_of)
    return previous_period_spec(spec, as_of)


def compare_title(spec: dict[str, Any]) -> str:
    return "Same period last year" if (spec.get("compare") or {}).get("kind") == "same_period_last_year" \
        else "Previous period"


def current_label(spec: dict[str, Any]) -> str:
    """The answer's own period in words ("this quarter"), for change and driver sentences."""
    rng = (spec.get("time") or {}).get("range") or {}
    if rng.get("relative"):
        return str(rng["relative"]).replace("_", " ")
    if rng.get("from") or rng.get("to"):
        return f"{rng.get('from') or '…'} to {rng.get('to') or '…'}"
    return "this period"


def previous_period_spec(spec: dict[str, Any], as_of: date) -> tuple[dict[str, Any], str] | None:
    """The same spec over the period of equal length just before the current range (with a grain, as many whole
    periods earlier, matching the compiled compare)."""
    t = spec.get("time") or {}
    if not t.get("range"):
        return None
    rng = TimeRange.model_validate(t["range"])
    start, end = resolve_range(rng, as_of)
    if start is None or end is None:
        return None
    prev_start, prev_end = previous_window(start, end, t.get("grain"))
    prev = copy.deepcopy(spec)
    prev.pop("compare", None)
    prev["time"] = {**t, "range": {"from": prev_start.isoformat(), "to": prev_end.isoformat()}}
    return prev, f"{prev_start:%-d %b %Y} to {prev_end:%-d %b %Y}"


def same_period_last_year_spec(spec: dict[str, Any], as_of: date) -> tuple[dict[str, Any], str] | None:
    """The same spec over the same calendar period one year earlier (US-178)."""
    t = spec.get("time") or {}
    if not t.get("range"):
        return None
    rng = TimeRange.model_validate(t["range"])
    start, end = resolve_range(rng, as_of)
    if start is None or end is None:
        return None
    try:
        prev_start = start.replace(year=start.year - 1)
    except ValueError:
        prev_start = start.replace(year=start.year - 1, day=28)
    try:
        prev_end = end.replace(year=end.year - 1)
    except ValueError:
        prev_end = end.replace(year=end.year - 1, day=28)
    prev = copy.deepcopy(spec)
    prev.pop("compare", None)
    prev["time"] = {**t, "range": {"from": prev_start.isoformat(), "to": prev_end.isoformat()}}
    return prev, f"{prev_start:%-d %b %Y} to {prev_end:%-d %b %Y}"
