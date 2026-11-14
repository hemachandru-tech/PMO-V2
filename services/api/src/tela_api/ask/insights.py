"""Insight engine (US-185, ENH-014).

Runs after execution on the (cached) result and returns typed facts: what is concentrated, what stands out, what
changed and what drove the change, the trend and any break in it, how values are spread, how far a figure is from
its pack threshold and which rows are "Not set". Every number is computed here; a narrator (AG-08 v2, US-186) only
phrases the facts, and each fact's template sentence is grounded in its own ``numbers``.

Only detectors whose data shape fits run. A fact names aggregated members (a region, a month) and aggregates, never
record-level values, so nothing row-level leaves the API.

A fact is ``{"id", "type", "priority", "numbers", "members", "text"}``; ``priority`` is size of effect x how unusual
(0 to 1). The top four are kept, and a fact that restates a kept one (same subject) is dropped.
"""

from __future__ import annotations

import itertools
import math
import statistics
from typing import Any

from tela_api.ask.narrator import fmt, numbers_in

NOT_SET = "Not set"
KEEP = 4
MAD_Z = 3.0  # robust z-score past which an item is an outlier
XMR = 2.66  # XmR natural process limits: mean +/- 2.66 x average moving range
RUN = 8  # points in a row on one side of the mean that signal a shift


def _name(v: Any) -> str:
    return NOT_SET if v is None or v == "" else str(v)


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _r(v: float, nd: int = 1) -> float:
    return round(v, nd)


def _pct(part: float, whole: float) -> float:
    return _r(part / whole * 100) if whole else 0.0


def _fact(type_: str, priority: float, text: str, numbers: dict[str, Any], members: list[str] | None = None,
          about: list[tuple[str, ...]] | None = None) -> dict[str, Any]:
    return {"type": type_, "priority": round(max(0.0, min(1.0, priority)), 3), "text": text, "numbers": numbers,
            "members": members or [], "_about": about or [(type_,)]}


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile (the method of PostgreSQL ``percentile_cont``)."""
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


# --------------------------------------------------------------------------- detectors


def concentration(items: list[tuple[str, float]], measure: str, kind: str | None) -> list[dict[str, Any]]:
    """Breakdown with 4+ items of an additive, non-negative measure: top-N share and the Pareto 80 % point."""
    if len(items) < 4 or any(v < 0 for _, v in items):
        return []
    total = sum(v for _, v in items)
    if total <= 0:
        return []
    ranked = sorted(items, key=lambda x: -x[1])
    n = len(ranked)
    some = [m for m, v in ranked if v > 0]
    if len(some) <= 3:  # a few items hold everything: name them
        joined = ", ".join(some[:-1]) + f" and {some[-1]}" if len(some) > 1 else some[0]
        text = f"Only {len(some)} of {n} have any {measure.lower()}: {joined}."
        return [_fact("concentration", 0.5 + 0.5 * (1 - len(some) / n), text,
                      {"items": n, "nonzero_items": len(some), "total": total}, some,
                      [("concentration",), ("lead", ranked[0][0])])]
    cum, k = 0.0, n
    for i, (_, v) in enumerate(ranked, 1):
        cum += v
        if cum >= 0.8 * total:
            k = i
            break
    top = min(3, n - 1)
    top_share = _pct(sum(v for _, v in ranked[:top]), total)
    if k / n > 0.5 and top_share / 100 - top / n < 0.15:
        return []  # spread fairly evenly: nothing to point out
    names = [m for m, _ in ranked[:top]]
    lead_share = _pct(ranked[0][1], total)
    text = (f"The top {top} of {n} account for {top_share}% of {measure.lower()} ({fmt(total, kind)}); "
            f"{k} of {n} make up 80%.")
    priority = (top_share / 100 - top / n) * (1 - k / n) * 2
    return [_fact("concentration", priority, text,
                  {"items": n, "top_n": top, "top_share_pct": top_share, "lead_share_pct": lead_share,
                   "pareto_items": k, "pareto_share_pct": _pct(k, n), "total": total},
                  names, [("concentration",), ("lead", ranked[0][0])])]


def outliers(items: list[tuple[str, float]], measure: str, kind: str | None) -> list[dict[str, Any]]:
    """6+ items: robust z-score on the median and MAD; |z| > 3 is an outlier (at most two reported)."""
    if len(items) < 6:
        return []
    values = [v for _, v in items]
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    scale = mad / 0.6745 if mad else 1.2533 * statistics.fmean(abs(v - med) for v in values)
    if not scale:
        return []
    top_value = max(values)
    span = max(values) - min(values) or 1.0
    found = sorted(((m, v, (v - med) / scale) for m, v in items if abs(v - med) / scale > MAD_Z),
                   key=lambda x: -abs(x[2]))[:2]
    out = []
    for m, v, z in found:
        side = "above" if z > 0 else "below"
        text = f"{m} stands out at {fmt(v, kind)}, well {side} the typical {fmt(med, kind)}."
        about: list[tuple[str, ...]] = [("member", m), *([("lead", m)] if v == top_value else [])]
        out.append(_fact("outlier", min(1.0, abs(z) / 8) * (0.5 + 0.5 * abs(v - med) / span), text,
                         {"value": v, "median": med, "z": _r(z), "direction": side}, [m], about))
    return out


def change(current: float, previous: float, label: str, previous_label: str, measure: str,
           kind: str | None) -> list[dict[str, Any]]:
    """Change vs the previous period: delta and % with both periods named."""
    delta = current - previous
    pct = _pct(delta, abs(previous)) if previous else None
    sign = "+" if delta > 0 else ""
    if delta == 0:
        text = f"{measure} in {label} is unchanged from {previous_label} ({fmt(current, kind)})."
    else:
        word = "up" if delta > 0 else "down"
        text = (f"{measure} in {label} is {word} {fmt(abs(delta), kind)}"
                + (f" ({sign}{pct}%)" if pct is not None else "")
                + f" on {previous_label} ({fmt(current, kind)} vs {fmt(previous, kind)}).")
    priority = min(1.0, abs(pct) / 50) if pct is not None else (0.5 if delta else 0.1)
    return [_fact("change", priority, text, {"current": current, "previous": previous, "delta": delta,
                                             "delta_pct": pct, "period": label, "previous_period": previous_label})]


def trend(series: list[tuple[str, float]], measure: str, kind: str | None) -> list[dict[str, Any]]:
    """6+ periods: least-squares slope sign; XmR limits for a break (a point past the limits, or 8 in a row on one
    side of the earlier mean, which replaces a weak trend)."""
    n = len(series)
    if n < 6:
        return []
    ys = [v for _, v in series]
    xbar, ybar = (n - 1) / 2, statistics.fmean(ys)
    sxx = sum((i - xbar) ** 2 for i in range(n))
    slope = sum((i - xbar) * (y - ybar) for i, y in enumerate(ys)) / sxx
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    r2 = (slope ** 2 * sxx / ss_tot) if ss_tot else 0.0
    out: list[dict[str, Any]] = []
    rel = abs(slope * (n - 1) / ybar) if ybar else 0.0
    if slope and r2 >= 0.3:
        word = "rising" if slope > 0 else "falling"
        text = (f"{measure} has been {word} over {n} periods, from {fmt(ys[0], kind)} in {series[0][0]} "
                f"to {fmt(ys[-1], kind)} in {series[-1][0]}.")
        out.append(_fact("trend", min(1.0, rel) * r2, text,
                         {"periods": n, "slope_per_period": _r(slope, 2), "r2": _r(r2, 2), "first": ys[0],
                          "last": ys[-1], "direction": word}, [series[0][0], series[-1][0]]))
    # XmR limits around the centre line: the mean, or the fitted line when the series trends (else a steady rise
    # would put its own ends past the limits)
    trending = bool(out)
    centre = [ybar + slope * (i - xbar) if trending else ybar for i in range(n)]
    resid = [y - c for y, c in zip(ys, centre, strict=True)]
    mr = [abs(b - a) for a, b in itertools.pairwise(resid)]
    mrbar = statistics.fmean(mr)
    if not mrbar:
        return out
    width = XMR * mrbar
    beyond = [(p, v, c) for (p, v), c in zip(series, centre, strict=True) if abs(v - c) > width]
    if beyond:
        p, v, c = beyond[-1]
        side = "above" if v > c else "below"
        # a series that is never negative (a cost, a count) has no range below zero
        lo, hi = _r(c - width if min(ys) < 0 else max(0.0, c - width), 2), _r(c + width, 2)
        usual = "the trend's usual range" if trending else "the usual range"
        text = f"{p} ({fmt(v, kind)}) is {side} {usual} of {fmt(lo, kind)} to {fmt(hi, kind)}."
        out.append(_fact("break", min(1.0, 0.6 + (abs(v - c) - width) / width), text,
                         {"value": v, "centre": _r(c, 2), "upper": hi, "lower": lo, "direction": side,
                          "against": "trend" if trending else "mean"}, [p], [("break",), ("period", p)]))
    elif n >= RUN + 4:
        # a level shift: the last RUN periods all on one side of the mean of the periods before them
        base = statistics.fmean(ys[:-RUN])
        tail = [y > base for y in ys[-RUN:]]
        if all(y != base for y in ys[-RUN:]) and (all(tail) or not any(tail)):
            side = "above" if tail[0] else "below"
            start = series[-RUN][0]
            text = (f"The last {RUN} periods, since {start}, are all {side} the earlier average of "
                    f"{fmt(_r(base, 2), kind)}.")
            if trending and r2 < 0.8:  # a step, not a steady trend
                out = []
            out.append(_fact("break", 0.6, text, {"run": RUN, "mean": _r(base, 2), "direction": side},
                             [start], [("break",)]))
    return out


def drivers(now: dict[str, float], before: dict[str, float], label: str, previous_label: str, measure: str,
            kind: str | None) -> list[dict[str, Any]]:
    """Change plus a second dimension: each member's contribution to the total delta (top three by size)."""
    members = set(now) | set(before)
    deltas = {m: now.get(m, 0) - before.get(m, 0) for m in members}
    total = sum(deltas.values())
    if not total or len(members) < 2:
        return []
    ranked = sorted(((m, d) for m, d in deltas.items() if d), key=lambda x: -abs(x[1]))[:3]
    lead, lead_delta = ranked[0]
    share = _pct(lead_delta, total)
    sign = "+" if lead_delta > 0 else "-"
    text = (f"{lead} drove most of the change in {label} vs {previous_label}: {sign}{fmt(abs(lead_delta), kind)} "
            f"of the {'+' if total > 0 else '-'}{fmt(abs(total), kind)} total ({share}%).")
    prev_total = sum(before.values())
    effect = min(1.0, abs(total / prev_total)) if prev_total else 0.5
    priority = 0.4 + 0.6 * effect * min(1.0, abs(lead_delta / total))
    return [_fact("driver", priority, text,
                  {"total_delta": total, "contributions": [{"member": m, "delta": d, "share_pct": _pct(d, total)}
                                                            for m, d in ranked],
                   "period": label, "previous_period": previous_label},
                  [m for m, _ in ranked], [("driver",)])]


def distribution(items: list[tuple[str, float]], measure: str, kind: str | None) -> list[dict[str, Any]]:
    """Percentile or record-level measure over 8+ items: P50, P85, spread (P15 to P85) and skew (Bowley)."""
    if len(items) < 8:
        return []
    values = [v for _, v in items]
    q1, p50, q3 = quantile(values, 0.25), quantile(values, 0.5), quantile(values, 0.75)
    p15, p85 = quantile(values, 0.15), quantile(values, 0.85)
    skew = (q3 + q1 - 2 * p50) / (q3 - q1) if q3 != q1 else 0.0
    shape = "skewed high" if skew > 0.2 else "skewed low" if skew < -0.2 else "fairly even"
    text = (f"Typical {measure.lower()} is {fmt(_r(p50, 2), kind)}; 85% are at or below {fmt(_r(p85, 2), kind)} "
            f"(spread {fmt(_r(p15, 2), kind)} to {fmt(_r(p85, 2), kind)}, {shape}).")
    spread = (p85 - p15) / abs(p50) if p50 else 1.0
    return [_fact("distribution", 0.2 + 0.4 * min(1.0, abs(skew)) * min(1.0, spread), text,
                  {"items": len(values), "p50": _r(p50, 2), "p85": _r(p85, 2), "p15": _r(p15, 2),
                   "skew": _r(skew, 2), "shape": shape})]


def gap(value: float, tone: dict[str, Any], measure: str, kind: str | None) -> list[dict[str, Any]]:
    """A single figure against its pack threshold (``tones`` in report_blueprints.yaml)."""
    worse = tone.get("higher_is_worse", True)
    warning, critical = tone.get("warning"), tone.get("critical")

    def past(t: float | None) -> bool:
        return t is not None and (value >= t if worse else value <= t)

    band = "critical" if past(critical) else "warning" if past(warning) else "within target"
    target = warning if warning is not None else critical
    if target is None:
        return []
    delta = value - target
    if band == "within target":
        text = f"{measure} ({fmt(value, kind)}) is within target; the warning level is {fmt(target, kind)}."
    else:
        text = (f"{measure} ({fmt(value, kind)}) is at {band} level: "
                f"the warning level is {fmt(target, kind)}"
                + (f" and critical is {fmt(critical, kind)}." if critical is not None and critical != target else "."))
    priority = {"critical": 0.9, "warning": 0.6}.get(band, 0.15)
    return [_fact("gap", priority, text, {"value": value, "warning": warning, "critical": critical,
                                          "gap": delta, "band": band, "higher_is_worse": worse})]


def members_past(items: list[tuple[str, float]], tone: dict[str, Any], measure: str, kind: str | None,
                 dimension: str = "items") -> list[dict[str, Any]]:
    """A breakdown of a ratio or share against the pack threshold: how many members are at warning or critical
    level."""
    worse = tone.get("higher_is_worse", True)
    critical, warning = tone.get("critical"), tone.get("warning")

    def hit(v: float, t: float) -> bool:
        return v >= t if worse else v <= t

    crit = [m for m, v in items if critical is not None and hit(v, critical)]
    warn = [m for m, v in items if warning is not None and hit(v, warning) and m not in crit]
    if not crit and not warn:
        return []
    parts = [f"{len(crit)} at the critical level of {fmt(critical, kind)}"] if crit else []
    parts += [f"{len(warn)} at the warning level of {fmt(warning, kind)}"] if warn else []
    text = f"Of {len(items)} {dimension.lower()} values, {' and '.join(parts)} for {measure.lower()}."
    return [_fact("gap", 0.85 if crit else 0.55, text,
                  {"items": len(items), "critical_items": len(crit), "warning_items": len(warn),
                   "warning": warning, "critical": critical}, (crit + warn)[:5])]


def data_note(items: list[tuple[str, float | None]], measure: str, kind: str | None, additive: bool,
              dimension: str, truncated: bool, row_count: int) -> list[dict[str, Any]]:
    """Rows with no value for the breakdown ("Not set") — their figure and share — and a truncated result."""
    out = []
    unset = [v for m, v in items if m == NOT_SET]
    if unset:
        v = unset[0]
        total = sum(x for _, x in items if x is not None and _num(x)) if additive else None
        if additive and v is not None and _num(v) and total:
            share = _pct(v, total)
            text = f"{fmt(v, kind)} of {fmt(total, kind)} ({share}%) has no {dimension.lower()} (Not set)."
            out.append(_fact("data_note", 0.25 + min(0.6, share / 100 * 2), text,
                             {"value": v, "total": total, "share_pct": share}, [NOT_SET], [("note", "not_set")]))
        else:
            text = f"Some records have no {dimension.lower()}; they are shown as Not set."
            out.append(_fact("data_note", 0.25, text, {}, [NOT_SET], [("note", "not_set")]))
    if truncated:
        out.append(_fact("data_note", 0.5, f"Only the first {row_count:,} rows were read; insights cover those rows.",
                         {"rows": row_count}, [], [("note", "truncated")]))
    return out


# --------------------------------------------------------------------------- the engine


def rank(found: list[dict[str, Any]], keep: int = KEEP) -> list[dict[str, Any]]:
    """Highest priority first; drop a fact whose subject a kept fact already covers; keep the top ``keep``."""
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for f in sorted(found, key=lambda x: -x["priority"]):
        about = set(f.pop("_about"))
        if about & seen:
            continue
        seen |= about
        kept.append(f)
        if len(kept) == keep:
            break
    for i, f in enumerate(kept, 1):
        f["id"] = f"f{i}"
    return [{"id": f.pop("id"), **f} for f in kept]


def detect(result: dict[str, Any], *, additive: bool, agg: str | None = None, tone: dict[str, Any] | None = None,
           comparison: dict[str, Any] | None = None, previous: dict[str, Any] | None = None,
           keep: int = KEEP) -> list[dict[str, Any]]:
    """The insights for one executed result. ``previous`` is the previous-period result of the same spec (FEAT-020
    compare), ``comparison`` its summary; ``tone`` the measure's pack threshold; ``agg`` the measure's aggregate."""
    cols, rows = result.get("columns") or [], result.get("rows") or []
    # US-177: skip window output columns (running_total, moving_avg, rank, etc.) from the primary measure
    mis = [i for i, c in enumerate(cols) if c["kind"] == "measure"
           and not (c.get("ref") or "").startswith("window:")]
    if not rows or not mis or result.get("hidden"):
        return []
    mi = mis[0]
    measure, kind = cols[mi]["label"], cols[mi].get("format")
    periods = [i for i, c in enumerate(cols) if c["kind"] == "period"]
    cats = [i for i, c in enumerate(cols) if c["kind"] == "dimension"]
    found: list[dict[str, Any]] = []

    if not cats and (not periods or len(rows) == 1):  # one figure, or one period of it ("this year" by year)
        value = rows[0][mi]
        if _num(value) and tone:
            found += gap(value, tone, measure, kind)
        if comparison and _num(comparison.get("current")) and _num(comparison.get("previous")):
            found += change(comparison["current"], comparison["previous"],
                            str(comparison.get("current_range") or "this period"),
                            str(comparison.get("previous_range") or "the previous period"), measure, kind)
        return rank(found, keep)

    if len(periods) == 1 and not cats:
        pi = periods[0]
        series = sorted(((str(r[pi]), r[mi]) for r in rows if r[pi] is not None and _num(r[mi])), key=lambda x: x[0])
        if len(series) >= 2:
            (p0, v0), (p1, v1) = series[-2], series[-1]
            found += change(v1, v0, p1, p0, measure, kind)
        found += trend(series, measure, kind)
        found += data_note([(_name(r[pi]), r[mi]) for r in rows], measure, kind, additive, cols[pi]["label"],
                           bool(result.get("truncated")), len(rows))
        return rank(found, keep)

    if len(periods) == 1 and len(cats) == 1 and additive:
        pi, ci = periods[0], cats[0]
        by: dict[str, dict[str, float]] = {}
        for r in rows:
            if r[pi] is not None and _num(r[mi]):
                by.setdefault(str(r[pi]), {})
                by[str(r[pi])][_name(r[ci])] = by[str(r[pi])].get(_name(r[ci]), 0) + r[mi]
        order = sorted(by)
        if len(order) >= 2:
            p0, p1 = order[-2], order[-1]
            t0, t1 = sum(by[p0].values()), sum(by[p1].values())
            found += change(t1, t0, p1, p0, measure, kind)
            found += drivers(by[p1], by[p0], p1, p0, measure, kind)
        if len(order) >= 6:
            found += trend([(p, sum(by[p].values())) for p in order], measure, kind)
        found += data_note([(_name(r[ci]), r[mi]) for r in rows], measure, kind, additive, cols[ci]["label"],
                           bool(result.get("truncated")), len(rows))
        return rank(found, keep)

    if len(cats) == 1 and not periods:
        ci = cats[0]
        items = [(_name(r[ci]), r[mi]) for r in rows if _num(r[mi])]
        known = [(m, v) for m, v in items if m != NOT_SET]
        if additive:
            found += concentration(known, measure, kind)
        found += outliers(known, measure, kind)
        if agg in ("avg", "median", "percentile", "min", "max"):
            found += distribution(known, measure, kind)
        if tone and additive:  # the threshold is for the whole figure
            found += gap(sum(v for _, v in items), tone, measure, kind)
        elif tone:
            found += members_past(known, tone, measure, kind, cols[ci]["label"])
        if comparison is not None and previous is not None and additive:
            before = {_name(r[ci]): r[mi] for r in previous.get("rows") or [] if _num(r[mi])}
            now = dict(items)
            label = str(comparison.get("current_range") or "this period")
            prev_label = str(comparison.get("previous_range") or "the previous period")
            found += change(sum(now.values()), sum(before.values()), label, prev_label, measure, kind)
            found += drivers(now, before, label, prev_label, measure, kind)
        found += data_note([(_name(r[ci]), r[mi]) for r in rows], measure, kind, additive, cols[ci]["label"],
                           bool(result.get("truncated")), len(rows))
        return rank(found, keep)

    found += data_note([], measure, kind, additive, "", bool(result.get("truncated")), len(rows))
    return rank(found, keep)


def numbers_of(fact: dict[str, Any], kind: str | None = None) -> set[str]:
    """Every number a fact's sentence may contain (for the grounding check of US-186): its numbers in raw and
    formatted form, and the digits in its member and period names."""
    out: set[str] = set()

    def add(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, list):
            for x in v:
                add(x)
        elif _num(v):
            out.update({str(v), *numbers_in(fmt(v, kind)), *numbers_in(fmt(abs(v), kind)), *numbers_in(str(v))})
        elif isinstance(v, str):
            out.update(numbers_in(v))

    add(fact["numbers"])
    add(fact["members"])
    for n in (80, 85, 15):  # the method's own constants: Pareto 80 %, P85, P15
        out.add(str(n))
    return out
