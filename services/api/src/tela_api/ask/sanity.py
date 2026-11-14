"""Result sanity checks (US-183, FEAT-064).

Run in code after execution, on the answer's own result plus at most a few small totals through the same scoped
query path. Each check returns a short data note for the card (``{"text", "fact", "kind", "numbers", "action"?}``,
ids ``s1``…); nothing is rerun in the answer's place — a note may only *offer* a refinement (``action.ops``, the
refine chip ops) that the person applies.

* **empty** — no rows: each filter (and the time range) is dropped in turn; the first whose removal brings data back is
  named and offered for removal; when only all of them together empty the result, removing all filters is offered.
* **single_row** — a breakdown was asked and one row came back; a filter on the same dimension is offered for removal.
* **filters_no_effect / filtered_above_total** — for an additive measure with filters, the filtered total against the
  same measure without filters: the filters did not narrow anything, or (counts) the filtered total is higher.
  Before answering (US-205), ``filters_no_effect`` probes the same two totals for the plan: filters that change nothing
  lower its confidence, so Ask re-plans or asks instead of answering a misread question as certified.
* **zero_result** — before answering (US-218), a plan whose filters leave nothing is probed without the filter it is
  least sure of; data without it means that filter was likely a misread word, so Ask re-plans without it and asks.
* **fan_out / excluded** — for an additive breakdown, the groups' sum against the total of the same spec without the
  breakdown (each root record counted once): a larger sum means records count under several groups; a smaller one
  (no top N) means records fall outside every group shown — the "excluded rows beyond Not set" of US-185's data note.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from tela_api.ask.insights import _name, _num, _pct
from tela_api.ask.narrator import fmt
from tela_api.identity.scope import ResolvedScope
from tela_common.errors import TelaError
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import ref_key

Run = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
COUNTS = ("count", "count_distinct")
MAX_PROBES = 4  # filters (and the time range) tried one at a time on an empty result
KEEP = ("spec_version", "model_version_id", "connection_id", "measure", "personal_fields", "personal_measures",
        "follow_page_filters")


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, 1e-6 * max(abs(a), abs(b)))


def total_spec(spec: dict[str, Any], *, filters: bool = True) -> dict[str, Any]:
    """The same measure as one figure: no breakdowns, period grain, extra measures, sort, limit or visual."""
    out = {k: v for k, v in spec.items() if k in KEEP}
    time = spec.get("time") or {}
    if time.get("range"):
        out["time"] = {k: v for k, v in time.items() if k != "grain"}
    if filters and spec.get("filters"):
        out["filters"] = spec["filters"]
    return out


def filter_label(f: dict[str, Any], snap: ModelSnapshot | None) -> str:
    ref = f.get("ref", "")
    dim = snap.dimension(ref_key(ref)) if snap is not None and ref.startswith("dimension:") else None
    name = dim.label if dim else ref.split(":", 1)[-1].split(".")[-1].replace("_", " ").capitalize()
    labels = [dim.value_labels.get(str(v), str(v)) if dim else str(v) for v in f.get("values") or []]
    neg = "not " if f.get("op") in ("!=", "not_in") else ""
    return f"{name}: {neg}{', '.join(labels[:3])}{'…' if len(labels) > 3 else ''}" if labels else name


def _dim_label(ref: str, snap: ModelSnapshot | None) -> str:
    dim = snap.dimension(ref_key(ref)) if snap is not None and ref.startswith("dimension:") else None
    return dim.label if dim else "filter"


def _note(kind: str, text: str, numbers: dict[str, Any] | None = None,
          action: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "text": text, "numbers": numbers or {}}
    if action is not None:
        out["action"] = action
    return out


def _measure(result: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    cols = result.get("columns") or []
    mi = next((i for i, c in enumerate(cols) if c["kind"] == "measure"), None)
    return mi, (cols[mi] if mi is not None else None)


def _sum(result: dict[str, Any]) -> float | None:
    mi, _ = _measure(result)
    if mi is None:
        return None
    values = [r[mi] for r in result.get("rows") or [] if _num(r[mi])]
    return sum(values) if values else None


def _value(result: dict[str, Any]) -> float | None:
    mi, _ = _measure(result)
    rows = result.get("rows") or []
    if mi is None or len(rows) != 1 or not _num(rows[0][mi]):
        return 0 if mi is not None and not rows else None
    return float(rows[0][mi])


# --------------------------------------------------------------------------- pure checks


def single_row(spec: dict[str, Any], result: dict[str, Any], snap: ModelSnapshot | None) -> list[dict[str, Any]]:
    """A breakdown was asked and only one group came back."""
    rows, cols = result.get("rows") or [], result.get("columns") or []
    asked = bool(spec.get("breakdowns")) or bool((spec.get("time") or {}).get("grain"))
    di = next((i for i, c in enumerate(cols) if c["kind"] in ("dimension", "period")), None)
    if not asked or len(rows) != 1 or di is None:
        return []
    col = cols[di]
    member = _name(rows[0][di])
    by = (spec.get("breakdowns") or [{}])[0].get("ref")
    same = next((f for f in spec.get("filters") or [] if by and f.get("ref") == by), None)
    if same is not None:
        return [_note("single_row", f"Your {col['label'].lower()} filter leaves one {col['label'].lower()} "
                                    f"({member}), so the breakdown has nothing to compare.", {"rows": 1},
                      {"label": f"Remove the {col['label'].lower()} filter",
                       "ops": [{"op": "remove_filter", "ref": by}]})]
    return [_note("single_row", f"Only one {col['label'].lower()} has data in your scope and filters ({member}), "
                                "so the breakdown has nothing to compare.", {"rows": 1})]


def against_unfiltered(spec: dict[str, Any], filtered: float | None, unfiltered: float | None, label: str,
                       kind: str | None, agg: str | None, snap: ModelSnapshot | None) -> list[dict[str, Any]]:
    """The filtered total against the same measure without the filters."""
    filters = spec.get("filters") or []
    if not filters or filtered is None or unfiltered is None or not _num(filtered) or not _num(unfiltered):
        return []
    named = "; ".join(filter_label(f, snap) for f in filters[:3])
    if unfiltered and _close(filtered, unfiltered):
        return [_note("filters_no_effect", f"Your filters ({named}) don't change the total: {label.lower()} is "
                                           f"{fmt(unfiltered, kind)} with or without them.",
                      {"total": unfiltered, "filtered": filtered})]
    if agg in COUNTS and filtered > unfiltered and not _close(filtered, unfiltered):
        return [_note("filtered_above_total", f"With your filters {label.lower()} is {fmt(filtered, kind)}, more "
                                              f"than the {fmt(unfiltered, kind)} without them; check how the "
                                              "filter is defined.", {"total": unfiltered, "filtered": filtered})]
    return []


def against_total(spec: dict[str, Any], result: dict[str, Any], total: float | None) -> list[dict[str, Any]]:
    """An additive breakdown's groups against the total with each root record counted once."""
    groups = _sum(result)
    _, mcol = _measure(result)
    cols = result.get("columns") or []
    di = next((i for i, c in enumerate(cols) if c["kind"] in ("dimension", "period")), None)
    if groups is None or total is None or not _num(total) or mcol is None or di is None:
        return []
    kind, dim = mcol.get("format"), cols[di]["label"].lower()
    if groups > total and not _close(groups, total):
        return [_note("fan_out", f"Some records count under more than one {dim}: the {dim} figures add up to "
                                 f"{fmt(groups, kind)}, while the total is {fmt(total, kind)}.",
                      {"groups_total": groups, "total": total})]
    if groups < total and not _close(groups, total) and not spec.get("limit") and total > 0:
        missing = total - groups
        share = _pct(missing, total)
        return [_note("excluded", f"{fmt(missing, kind)} of {fmt(total, kind)} ({share}%) isn't under any {dim} "
                                  "shown, so the breakdown adds up to less than the total.",
                      {"excluded": missing, "total": total, "share_pct": share})]
    return []


# --------------------------------------------------------------------------- the checks with their probes


async def _probe(run: Run, spec: dict[str, Any]) -> dict[str, Any] | None:
    try:
        res = await run(spec)
    except TelaError:
        return None
    return None if res.get("hidden") or res.get("truncated") else res


async def empty(spec: dict[str, Any], run: Run, snap: ModelSnapshot | None) -> list[dict[str, Any]]:
    """No rows: name the filter (or the time range) whose removal brings data back and offer to remove it."""
    filters = spec.get("filters") or []
    has_range = bool((spec.get("time") or {}).get("range"))
    tries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for f in filters:
        rest = [g for g in filters if g is not f]
        name = filter_label(f, snap)
        tries.append((f"the {name} filter", {**spec, "filters": rest},
                      {"label": f"Remove the {_dim_label(f['ref'], snap).lower()} filter",
                       "ops": [{"op": "remove_filter", "ref": f["ref"]}]}))
    if has_range:
        tries.append(("the date range", {k: v for k, v in spec.items() if k != "time"},
                      {"label": "Use all dates", "ops": [{"op": "clear_time"}]}))
    for what, probe, action in tries[:MAX_PROBES]:
        res = await _probe(run, total_spec(probe))
        if res is not None and res.get("rows") and (_value(res) or 0) != 0:
            return [_note("empty", f"Nothing matches with {what}; without it there is data.", {}, action)]
    if len(tries) > 1:
        bare = total_spec({k: v for k, v in spec.items() if k != "time"}, filters=False)
        res = await _probe(run, bare)
        if res is not None and res.get("rows") and (_value(res) or 0) != 0:
            ops: list[dict[str, Any]] = [{"op": "remove_filter"}] if filters else []
            if has_range:
                ops.append({"op": "clear_time"})
            return [_note("empty", "No single filter empties the result, but together they remove everything.", {},
                          {"label": "Remove all filters" if not has_range else "Remove filters and dates",
                           "ops": ops})]
    if not tries:
        return [_note("empty", "There is no data for this in your scope.", {})]
    return []


def scoped_fields(scope: ResolvedScope) -> set[str]:
    """The ``entity.field`` pairs the person's scope restricts: a filter on one may well change nothing."""
    out = {f"{e}.{c.field}" for e, es in scope.entities.items() for alt in es.alternatives for c in alt}
    return out | {f"{e}.{es.via.field}" for e, es in scope.entities.items() if es.via is not None}


def telling(spec: dict[str, Any], snap: ModelSnapshot, reads: set[str], scoped: set[str]) -> list[dict[str, Any]]:
    """The filters that should narrow the total: not on a field the measure's own rule reads ("open projects that are
    active") or one the person's scope already restricts ("in KSA" for someone who sees only KSA)."""
    out = []
    for f in spec.get("filters") or []:
        d = snap.dimension(ref_key(f.get("ref", ""))) if f.get("ref", "").startswith("dimension:") else None
        if d is not None and f"{d.entity}.{d.field}" not in reads | scoped:
            out.append(f)
    return out


async def filters_no_effect(spec: dict[str, Any], run: Run, snap: ModelSnapshot, *, label: str, kind: str | None,
                            agg: str | None, reads: set[str], scoped: set[str],
                            count: str | None) -> dict[str, Any] | None:
    """The plan's filters, before it is answered (US-205): the ``filters_no_effect`` note when the telling ones leave
    the measure unchanged yet narrow its table (``count``: the table's own count measure) to another figure — the
    measure's rule already implies them, so the question's criteria were likely read against the wrong field or
    measure. Filters that narrow nothing (every portfolio named) or agree with the rule say nothing, as does a failing,
    hidden or truncated probe."""
    if count is None or not telling(spec, snap, reads, scoped):
        return None
    note: dict[str, Any] | None = None
    figures: list[float | None] = []
    for probe in (total_spec(spec), total_spec(spec, filters=False), total_spec({**spec, "measure": {"ref": count}}),
                  total_spec({**spec, "measure": {"ref": count}}, filters=False)):
        res = await _probe(run, probe)
        if res is None or (value := _value(res)) is None:
            return None
        figures.append(value)
        if len(figures) == 2:
            notes = against_unfiltered(spec, figures[0], figures[1], label, kind, agg, snap)
            if (note := next((n for n in notes if n["kind"] == "filters_no_effect"), None)) is None:
                return None
    measure, _, counted, table = figures
    assert measure is not None and counted is not None and table is not None
    if _close(counted, measure) or _close(counted, table):
        return None
    return note


async def zero_result(spec: dict[str, Any], run: Run, snap: ModelSnapshot, weak: dict[str, float], *, label: str,
                      kind: str | None) -> dict[str, Any] | None:
    """Before answering (US-218): when the plan's filters leave nothing, the total without the filter it is least sure
    of (``weak``: filter ref → link score; a value found exactly is never weak). Data without it gives the
    ``zero_result`` note naming that filter (``ref``); a true zero, a failing or hidden probe, or no weak filter say
    nothing. The first probe is the plan's own total, which the cache serves to the answer: one extra query at most."""
    filters = spec.get("filters") or []
    weakest = min((f for f in filters if f.get("ref") in weak), key=lambda f: weak[f["ref"]], default=None)
    if weakest is None:
        return None
    res = await _probe(run, total_spec(spec))
    if res is None or (_value(res) or 0) != 0:
        return None
    rest = [f for f in filters if f is not weakest]
    res = await _probe(run, total_spec({**spec, "filters": rest}))
    if res is None or not (without := _value(res)):
        return None
    name = filter_label(weakest, snap)
    return {**_note("zero_result", f"Nothing matches with {name}; without it {label.lower()} is {fmt(without, kind)}.",
                    {"filtered": 0, "without": without}), "ref": weakest["ref"], "filter": name}


async def check(spec: dict[str, Any], result: dict[str, Any], run: Run, *, snap: ModelSnapshot | None,
                additive: bool, agg: str | None) -> list[dict[str, Any]]:
    """All sanity notes for one answer, ids ``s1``…; a failing probe skips its check."""
    if result.get("hidden") or result.get("truncated"):
        return []
    notes: list[dict[str, Any]] = []
    rows = result.get("rows") or []
    _, mcol = _measure(result)
    if not rows:
        notes += await empty(spec, run, snap)
    else:
        notes += single_row(spec, result, snap)
        if additive and mcol is not None:
            dims = any(c["kind"] in ("dimension", "period") for c in result.get("columns") or [])
            filtered: float | None = _value(result) if not dims else None
            if dims:
                res = await _probe(run, total_spec(spec))
                filtered = _value(res) if res is not None else None
                notes += against_total(spec, result, filtered)
            if spec.get("filters"):
                res = await _probe(run, total_spec(spec, filters=False))
                notes += against_unfiltered(spec, filtered, _value(res) if res is not None else None,
                                            mcol["label"], mcol.get("format"), agg, snap)
    for i, n in enumerate(notes, 1):
        n["fact"] = f"s{i}"
    return notes
