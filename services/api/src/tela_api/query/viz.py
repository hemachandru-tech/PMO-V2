"""Visual choice for a result (AG-07 lite; RULE-008; US-050). Code decides from the result shape.

The chart spec carries what the renderer needs and must not work out itself (COMP-001): the meter's part, whole
and percent, and the tone of each status value (US-157), and the figures of the US-187 visuals (``figures``): waterfall
steps, Pareto cumulative shares, XmR limits, bullet bands, funnel drop-offs, Gantt dates and small-multiple panels.
Most also carry ``table``, the same figures as a small table for the Excel sheet.

US-188 (wave 2) adds the calendar's day levels, sankey nodes and links, UpSet set sizes and intersections, beeswarm
and ridgeline quartiles and densities, the Kaplan-Meier time-to-event curve, bump ranks, treemap levels and waffle
cells. The browser only places them (the beeswarm's dot layout is presentation, not a figure).
"""

from __future__ import annotations

import itertools
import math
import statistics
from datetime import date
from typing import Any

from tela_chart_spec import ChartSpec, ResultShape, choose_visual, status_tone, suitability, to_chart_spec
from tela_query_spec.expr import is_additive
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import FILLED_CARDS, NO_CHART

XMR = 2.66  # XmR natural process limits, as in ask/insights.py


def result_shape(result: dict[str, Any], snapshot: ModelSnapshot, target: bool = False) -> ResultShape:
    """``target``: the measure has a target (the widget's tone or the pack's threshold) — the bullet (US-187)."""
    cats, dates, statuses, ordered, measures, formats, flags = [], [], [], [], [], [], []
    distinct: dict[str, int] = {}
    companions: dict[str, str] = {}
    additive, non_negative = True, True
    names = {c["name"] for c in result["columns"]}
    for i, col in enumerate(result["columns"]):
        name, kind, ref = col["name"], col["kind"], col.get("ref") or ""
        if kind == "measure":
            measures.append(name)
            formats.append(col.get("format") or "integer")
            if col.get("of") in names:  # a derived figure or share labels its measure (US-176)
                companions[name] = col["of"]
                continue
            m = snapshot.measure(ref.split(":", 1)[1]) if ref.startswith("measure:") else None
            if m is not None and not is_additive(m.expression):
                additive = False
            if any(isinstance(r[i], int | float) and r[i] < 0 for r in result["rows"]):
                non_negative = False
            continue
        distinct[name] = len({r[i] for r in result["rows"]})
        if kind == "period" or col.get("data_type") in ("date", "datetime"):
            dates.append(name)
            continue
        cats.append(name)
        if _is_flag([r[i] for r in result["rows"]]) or col.get("data_type") == "boolean":
            flags.append(name)
        dim = snapshot.dimension(ref.split(":", 1)[1]) if ref.startswith("dimension:") else None
        if dim is not None and dim.is_status:
            statuses.append(name)
        if dim is not None and (dim.sort_order or dim.bands):
            ordered.append(name)
    rows = result["rows"]
    series = [i for i, c in enumerate(result["columns"]) if c["kind"] == "measure" and c["name"] not in companions]
    descending = _descending(rows, series)
    within = (len(rows) == 1 and len(measures) == 2 and len(result["columns"]) == 2
              and all(isinstance(v, int | float) for v in rows[0]) and 0 <= rows[0][0] <= rows[0][1])
    days = [_day(r[_idx(result, dates[0])]) for r in rows] if len(dates) == 1 else []
    daily = bool(days) and all(d is not None for d in days)
    months = 0
    if daily:
        first, last = min(days), max(days)  # type: ignore[type-var]
        months = (last.year - first.year) * 12 + last.month - first.month + 1  # type: ignore[union-attr]
    return ResultShape(row_count=len(rows), categories=cats, dates=dates, statuses=statuses,
                       ordered=ordered, measures=measures, measure_formats=formats, additive=additive,
                       non_negative=non_negative, distinct_counts=distinct, part_within_whole=within,
                       companions=companions, has_target=target, descending=descending, flags=flags,
                       daily=daily, months=months)


FLAG_WORDS = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False, "1": True, "0": False}


def _is_flag(values: list[Any]) -> bool:
    """Yes/no values only (booleans, or the words yes / no, true / false); blanks allowed (US-188)."""
    seen = [v for v in values if v is not None]
    return bool(seen) and all(isinstance(v, bool) or str(v).strip().lower() in FLAG_WORDS for v in seen)


def _flag(v: Any) -> bool:
    return v if isinstance(v, bool) else FLAG_WORDS.get(str(v).strip().lower(), False) if v is not None else False


def _day(v: Any) -> date | None:
    """A day (``YYYY-MM-DD``, or a date); a month or week period (``2026-03``, ``2026-W12``) is not one."""
    if isinstance(v, date):
        return v
    text = str(v) if v is not None else ""
    if len(text) < 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _descending(rows: list[list[Any]], series: list[int]) -> bool:
    """Stages that only fall: one row of measures left to right, or one measure down the rows (funnel, US-187)."""
    if len(rows) == 1 and len(series) >= 2:
        values = [rows[0][i] for i in series]
    elif len(series) == 1 and len(rows) >= 2:
        values = [r[series[0]] for r in rows]
    else:
        return False
    return (all(isinstance(v, int | float) for v in values) and values[0] > 0
            and all(b <= a for a, b in itertools.pairwise(values)))


def meter_figures(result: dict[str, Any], note: str | None = None) -> dict[str, Any]:
    """Part, whole and percent of a meter ("31 of 111 (27.9 %)"); the note defaults to "<part> out of <whole>"."""
    part, whole = result["rows"][0][0], result["rows"][0][1]
    a, b = result["columns"][0]["label"], result["columns"][1]["label"]
    return {"part": part, "whole": whole, "percent": round(part / whole * 100, 1) if whole else None,
            "note": note or f"{a} out of {b.lower()}"}


def value_tones(result: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """Tone of each value of the status columns (RAG colours with the word); neutral values are left out."""
    idx = [i for i, c in enumerate(result["columns"]) if c["name"] in names]
    tones = {str(r[i]): status_tone(r[i]) for r in result["rows"] for i in idx if r[i] is not None}
    return {k: v for k, v in tones.items() if v != "neutral"}


def visualise(result: dict[str, Any], snapshot: ModelSnapshot, requested: str | None = None,
              options: dict[str, Any] | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    """``options`` is the widget's stored visual (``spec.visual``): a meter's ``note``, a figure's or bullet's
    ``tone``. ``target`` is the pack's threshold for the measure (``tones``), used when the widget sets none."""
    if result.get("hidden"):
        return {"visual": None, "alternatives": [], "disabled": [], "chart_spec": None, "note": None}
    if result.get("detail"):  # a detail table lists records: nothing to chart (US-158)
        labels = {c["name"]: c["label"] for c in result["columns"]}
        return {"visual": "table", "alternatives": [], "disabled": [], "note": None,
                "chart_spec": _styled(_coloured(ChartSpec(type="table", labels=labels), result, options),
                                      options).model_dump()}
    tone = _tone((options or {}).get("tone")) or _tone(target)
    shape = result_shape(result, snapshot, target=tone is not None)
    visual, alternatives, note = choose_visual(shape, requested)
    labels = {c["name"]: c["label"] for c in result["columns"]}
    formats = {c["name"]: c.get("format") or "" for c in result["columns"] if c["kind"] == "measure"}
    disabled = [s for s in suitability(shape) if not s["suitable"]]
    spec = to_chart_spec(visual, shape, labels, formats)
    if visual == "meter":
        spec.meter = meter_figures(result, (options or {}).get("note"))
    if (status_cols := [c for c in (spec.y, spec.stack) if c and c in shape.statuses]):
        spec.tones = value_tones(result, status_cols)
    if (make := FIGURES.get(visual)) is not None:
        spec.figures = make(result, spec, tone) if visual == "bullet" else make(result, spec)
    if visual == "table":
        _coloured(spec, result, options)
    _styled(spec, options)
    return {"visual": visual, "alternatives": alternatives, "disabled": disabled,
            "chart_spec": spec.model_dump(), "note": note}


# --------------------------------------------------------------------------- US-187 figures (COMP-001)

Cols = list[dict[str, Any]]


def _coloured(spec: ChartSpec, result: dict[str, Any], options: dict[str, Any] | None) -> ChartSpec:
    """A table in colour (ENH-020): the tone of every status word in its text columns, for RAG badges."""
    if (options or {}).get("colour"):
        spec.colour = True
        spec.tones = value_tones(result, [c["name"] for c in result["columns"] if c["kind"] != "measure"])
    return spec


def _styled(spec: ChartSpec, options: dict[str, Any] | None) -> ChartSpec:
    """The widget's card colour (US-223) and chart colours (US-224), when they fit what is shown: a filled card only
    behind a single figure, chart colours only on a chart with series (an automatic visual may differ from the
    stored one)."""
    o = options or {}
    card = o.get("card")
    if card and card != "default" and (card not in FILLED_CARDS or spec.type == "figure"):
        spec.card = card
    if o.get("colours") and spec.type not in NO_CHART:
        spec.colours = o["colours"]
    return spec


def _tone(tone: dict[str, Any] | None) -> dict[str, Any] | None:
    return tone if tone and (tone.get("warning") is not None or tone.get("critical") is not None) else None


def _idx(result: dict[str, Any], name: str | None) -> int:
    return next((i for i, c in enumerate(result["columns"]) if c["name"] == name), -1)


def _label(v: Any) -> str:
    return "Not set" if v is None else str(v)


def _num(v: Any) -> float:
    return float(v) if isinstance(v, int | float) else 0.0


def _r(v: float, nd: int = 2) -> float | int:
    out = round(v, nd)
    return int(out) if float(out).is_integer() else out


def _col(name: str, label: str, fmt: str | None = None) -> dict[str, Any]:
    return {"name": name, "label": label, "kind": "measure" if fmt else "dimension", "format": fmt}


def _table(title: str, columns: Cols, rows: list[list[Any]]) -> dict[str, Any]:
    return {"title": title, "columns": columns, "rows": rows}


def _fmt(result: dict[str, Any], i: int) -> str:
    return result["columns"][i].get("format") or "integer"


def waterfall_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """One measure: the items build up to the total. Two measures (a bridge): from the first measure's total to
    the second's, each item's step being its change (second - first). ``low``/``high`` place each floating bar."""
    xi, ms = _idx(result, spec.x), [_idx(result, m) for m in spec.series]
    fmt = _fmt(result, ms[0])
    labels = [_label(r[xi]) for r in result["rows"]]
    steps: list[dict[str, Any]] = []
    if len(ms) == 2:
        a, b = ms
        start = sum(_num(r[a]) for r in result["rows"])
        steps.append({"label": f"{result['columns'][a]['label']} (total)", "kind": "start", "value": _r(start),
                      "low": 0, "high": _r(start)})
        running, deltas = start, [_num(r[b]) - _num(r[a]) for r in result["rows"]]
        mode, total_label = "bridge", f"{result['columns'][b]['label']} (total)"
    else:
        running, deltas = 0.0, [_num(r[ms[0]]) for r in result["rows"]]
        mode, total_label = "build", "Total"
    for label, d in zip(labels, deltas, strict=True):
        end = running + d
        steps.append({"label": label, "kind": "up" if d >= 0 else "down", "value": _r(d),
                      "low": _r(min(running, end)), "high": _r(max(running, end))})
        running = end
    steps.append({"label": total_label, "kind": "total", "value": _r(running), "low": 0, "high": _r(running)})
    table = _table("Waterfall steps", [_col("step", "Step"), _col("value", "Change", fmt),
                                       _col("running", "Running total", fmt)],
                   [[s["label"], s["value"], s["high"] if s["kind"] != "down" else s["low"]] for s in steps])
    return {"mode": mode, "steps": steps, "format": fmt, "table": table}


def pareto_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Items largest first with the cumulative share; ``vital_few`` items make up 80 % of the total."""
    xi, mi = _idx(result, spec.x), _idx(result, spec.series[0])
    items = sorted(((_label(r[xi]), _num(r[mi])) for r in result["rows"]), key=lambda t: -t[1])
    total = sum(v for _, v in items)
    cumulative, running = [], 0.0
    for _, v in items:
        running += v
        cumulative.append(_r(running / total * 100, 1) if total else 0)
    vital = next((i + 1 for i, c in enumerate(cumulative) if c >= 80), len(items))
    fmt = _fmt(result, mi)
    table = _table("Pareto", [_col("item", result["columns"][xi]["label"]), _col("value", result["columns"][mi]
                                                                                   ["label"], fmt),
                              _col("cumulative", "Cumulative share", "percent")],
                   [[k, _r(v), c] for (k, v), c in zip(items, cumulative, strict=True)])
    return {"labels": [k for k, _ in items], "values": [_r(v) for _, v in items], "cumulative_pct": cumulative,
            "vital_few": vital, "table": table}


def xmr_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Individuals chart: the mean and natural process limits (mean +/- 2.66 x average moving range); a measure
    that is never negative has no lower limit below zero. ``beyond`` names the periods past a limit."""
    xi, mi = _idx(result, spec.x), _idx(result, spec.series[0])
    points = [(_label(r[xi]), r[mi]) for r in result["rows"] if isinstance(r[mi], int | float)]
    ys = [float(v) for _, v in points]
    mean = statistics.fmean(ys) if ys else 0.0
    mr = [abs(b - a) for a, b in itertools.pairwise(ys)]
    mr_bar = statistics.fmean(mr) if mr else 0.0
    upper, lower = mean + XMR * mr_bar, mean - XMR * mr_bar
    if ys and min(ys) >= 0:
        lower = max(0.0, lower)
    beyond = [p for p, v in points if v > upper or v < lower]
    fmt = _fmt(result, mi)
    table = _table("Control limits", [_col("line", "Line"), _col("value", result["columns"][mi]["label"], fmt)],
                   [["Upper limit", _r(upper)], ["Mean", _r(mean)], ["Lower limit", _r(lower)],
                    ["Average moving range", _r(mr_bar)]])
    return {"mean": _r(mean), "upper": _r(upper), "lower": _r(lower), "mr_bar": _r(mr_bar), "beyond": beyond,
            "table": table}


def bullet_figures(result: dict[str, Any], spec: ChartSpec, tone: dict[str, Any] | None) -> dict[str, Any]:
    """Each item's value, its band (good / warning / critical) and the target band edges, and the axis maximum."""
    tone = tone or {}
    worse = tone.get("higher_is_worse", True)
    warning, critical = tone.get("warning"), tone.get("critical")
    xi, mi = _idx(result, spec.x), _idx(result, spec.series[0])

    def band(v: float) -> str:
        def past(t: float | None) -> bool:
            return t is not None and (v >= t if worse else v <= t)
        return "critical" if past(critical) else "warning" if past(warning) else "good"

    rows = [{"label": _label(r[xi]) if xi >= 0 else result["columns"][mi]["label"], "value": r[mi],
             "band": band(_num(r[mi]))} for r in result["rows"]]
    fmt = _fmt(result, mi)
    edges = [t for t in (warning, critical) if t is not None]
    top = max([_num(r["value"]) for r in rows] + edges + [0.0])
    axis_max = 100 if fmt == "percent" and top <= 100 else _r(top * 1.1 if top else 1)
    table = _table("Against target", [_col("item", "Item"), _col("value", result["columns"][mi]["label"], fmt),
                                      _col("band", "Band")], [[r["label"], r["value"], r["band"]] for r in rows])
    return {"rows": rows, "warning": warning, "critical": critical, "higher_is_worse": worse, "max": axis_max,
            "target": warning if warning is not None else critical, "table": table}


def funnel_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Stages in order: each stage's share of the first and its drop-off from the stage before."""
    if spec.x is not None and _idx(result, spec.x) >= 0:
        xi, mi = _idx(result, spec.x), _idx(result, spec.series[0])
        stages = [(_label(r[xi]), _num(r[mi])) for r in result["rows"]]
        fmt = _fmt(result, mi)
    else:
        ms = [_idx(result, m) for m in spec.series]
        stages = [(result["columns"][i]["label"], _num(result["rows"][0][i])) for i in ms]
        fmt = _fmt(result, ms[0])
    first = stages[0][1] if stages else 0.0
    out, prev = [], None
    for label, v in stages:
        out.append({"label": label, "value": _r(v), "pct_of_first": _r(v / first * 100, 1) if first else None,
                    "drop_pct": _r((prev - v) / prev * 100, 1) if prev else None})
        prev = v
    table = _table("Funnel", [_col("stage", "Stage"), _col("value", "Value", fmt),
                              _col("pct_of_first", "Of the first stage", "percent"),
                              _col("drop_pct", "Drop from the stage before", "percent")],
                   [[s["label"], s["value"], s["pct_of_first"], s["drop_pct"]] for s in out])
    return {"stages": out, "table": table}


def gantt_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """The start and end date columns (the earlier-named date is the start) and how many rows lack either, or end
    before they start (listed in the table, not drawn)."""
    dates = [c["name"] for c in result["columns"]
             if c["kind"] != "measure" and (c["kind"] == "period" or c.get("data_type") in ("date", "datetime"))]
    start, end = dates[0], dates[1]
    si, ei = _idx(result, start), _idx(result, end)
    unplanned = sum(1 for r in result["rows"] if r[si] is None or r[ei] is None or str(r[ei]) < str(r[si]))
    return {"start": start, "end": end, "unplanned": unplanned}


def panel_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Panels largest total first, and the shared axis maximum, so every panel reads on one scale."""
    yi, mi = _idx(result, spec.y), _idx(result, spec.series[0])
    totals: dict[str, float] = {}
    for r in result["rows"]:
        totals[_label(r[yi])] = totals.get(_label(r[yi]), 0.0) + _num(r[mi])
    values = [_num(r[mi]) for r in result["rows"]]
    return {"panels": sorted(totals, key=lambda k: -totals[k]), "max": _r(max([*values, 0.0])),
            "min": _r(min([*values, 0.0]))}


def dumbbell_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Each item's gap (second - first measure), widest first in ``order`` (row indexes)."""
    a, b = (_idx(result, m) for m in spec.series[:2])
    gaps = [_r(_num(r[b]) - _num(r[a])) for r in result["rows"]]
    order = sorted(range(len(gaps)), key=lambda i: -abs(gaps[i]))
    return {"gaps": gaps, "order": order}


# --------------------------------------------------------------------------- US-188 figures (COMP-001)

CALENDAR_LEVELS = 4  # quartile steps of the days with a value


def calendar_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Days from first to last, the level edges (quartiles of the days with a value, so the four colour steps hold
    about as many days each), the years the calendar is drawn for, and the monthly totals for Excel."""
    xi, mi = _idx(result, spec.x), _idx(result, spec.series[0])
    days = [(d, r[mi]) for r in result["rows"] if (d := _day(r[xi])) is not None]
    values = sorted(_num(v) for _, v in days if isinstance(v, int | float))
    edges = ([_r(e) for e in statistics.quantiles(values, n=CALENDAR_LEVELS, method="inclusive")]
             if len(values) >= 2 else [])
    first, last = min(d for d, _ in days), max(d for d, _ in days)
    months: dict[str, list[float]] = {}
    for d, v in days:
        months.setdefault(d.isoformat()[:7], []).append(_num(v))
    fmt = _fmt(result, mi)
    additive = spec.formats.get(spec.series[0]) not in ("percent",)
    table = _table("By month", [_col("month", "Month"), _col("value", ("Total " if additive else "Average ")
                                                            + result["columns"][mi]["label"].lower(), fmt),
                                _col("days", "Days with a value", "integer")],
                   [[k, _r(sum(v) if additive else statistics.fmean(v)), len(v)] for k, v in sorted(months.items())])
    return {"start": first.isoformat(), "end": last.isoformat(), "years": list(range(first.year, last.year + 1)),
            "edges": edges, "max": _r(values[-1]) if values else 0, "table": table}


def sankey_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Nodes (one per value of each breakdown, left to right in the result's column order) and links between
    neighbouring breakdowns, summed over the rows; rows of zero are left out."""
    dims = [i for i, c in enumerate(result["columns"]) if c["kind"] != "measure"]
    mi = _idx(result, spec.series[0])
    nodes: list[dict[str, Any]] = []
    index: dict[tuple[int, str], int] = {}
    links: dict[tuple[int, int], float] = {}
    for r in result["rows"]:
        v = _num(r[mi])
        if v <= 0:
            continue
        path = []
        for level, di in enumerate(dims):
            key = (level, _label(r[di]))
            if key not in index:
                index[key] = len(nodes)
                nodes.append({"label": key[1], "level": level, "value": 0.0})
            path.append(index[key])
        for a, b in itertools.pairwise(path):
            links[(a, b)] = links.get((a, b), 0.0) + v
        nodes[path[0]]["value"] += v
        for k in path[1:]:
            nodes[k]["value"] += v
    for node in nodes:
        node["value"] = _r(node["value"])
    out: list[dict[str, Any]] = [{"source": a, "target": b, "value": _r(v)} for (a, b), v in links.items()]
    fmt = _fmt(result, mi)
    table = _table("Flows", [_col("from", "From"), _col("to", "To"), _col("value", result["columns"][mi]["label"],
                                                                         fmt)],
                   [[nodes[lk["source"]]["label"], nodes[lk["target"]]["label"], lk["value"]] for lk in out])
    return {"levels": [result["columns"][i]["label"] for i in dims], "nodes": nodes, "links": out, "table": table}


UPSET_MAX = 20  # intersections drawn, largest first


def upset_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Each yes/no breakdown is a set: its size (rows where it is yes), and each combination's count, largest
    first. A combination with no set is kept ("none of these") so the counts add up to the total."""
    flags = [i for i, c in enumerate(result["columns"]) if c["kind"] != "measure"]
    mi = _idx(result, spec.series[0])
    sizes = [0.0] * len(flags)
    combos: dict[tuple[bool, ...], float] = {}
    for r in result["rows"]:
        v = _num(r[mi])
        members = tuple(_flag(r[i]) for i in flags)
        combos[members] = combos.get(members, 0.0) + v
        for k, m in enumerate(members):
            sizes[k] += v if m else 0.0
    names = [result["columns"][i]["label"] for i in flags]
    ranked = sorted(combos.items(), key=lambda kv: (-kv[1], -sum(kv[0])))
    intersections: list[dict[str, Any]] = [{"members": list(m), "value": _r(v), "degree": sum(m)}
                                           for m, v in ranked if v > 0]
    fmt = _fmt(result, mi)

    def words(m: list[bool]) -> str:
        return " + ".join(n for n, on in zip(names, m, strict=True) if on) or "None of these"

    table = _table("Combinations", [_col("combination", "Combination"), _col("value", result["columns"][mi]["label"],
                                                                            fmt)],
                   [[words(x["members"]), x["value"]] for x in intersections])
    return {"sets": [{"label": n, "size": _r(z)} for n, z in zip(names, sizes, strict=True)],
            "intersections": intersections[:UPSET_MAX], "more": max(0, len(intersections) - UPSET_MAX),
            "total": _r(sum(combos.values())), "table": table}


def _quartiles(values: list[float]) -> dict[str, Any]:
    """Five numbers and the whiskers (the furthest values within 1.5 x IQR of the box)."""
    vs = sorted(values)
    if len(vs) == 1:
        q1 = med = q3 = vs[0]
    else:
        q1, med, q3 = statistics.quantiles(vs, n=4, method="inclusive")
    iqr = q3 - q1
    lo = min((v for v in vs if v >= q1 - 1.5 * iqr), default=vs[0])
    hi = max((v for v in vs if v <= q3 + 1.5 * iqr), default=vs[-1])
    return {"n": len(vs), "min": _r(vs[0]), "q1": _r(q1), "median": _r(med), "q3": _r(q3), "max": _r(vs[-1]),
            "low": _r(lo), "high": _r(hi), "mean": _r(statistics.fmean(vs))}


def _groups(result: dict[str, Any], spec: ChartSpec) -> tuple[int, dict[str, list[float]]]:
    """Values of the measure per group (one group, the measure's label, without a group breakdown)."""
    mi, gi = _idx(result, spec.series[0]), _idx(result, spec.y)
    groups: dict[str, list[float]] = {}
    for r in result["rows"]:
        if isinstance(r[mi], int | float):
            groups.setdefault(_label(r[gi]) if gi >= 0 else result["columns"][mi]["label"], []).append(float(r[mi]))
    return mi, groups


def _spread_table(result: dict[str, Any], mi: int, stats: list[dict[str, Any]], title: str) -> dict[str, Any]:
    fmt = _fmt(result, mi)
    return _table(title, [_col("group", "Group"), _col("n", "Items", "integer"), _col("min", "Lowest", fmt),
                          _col("q1", "Lower quartile", fmt), _col("median", "Median", fmt),
                          _col("q3", "Upper quartile", fmt), _col("max", "Highest", fmt)],
                  [[g["label"], g["n"], g["min"], g["q1"], g["median"], g["q3"], g["max"]] for g in stats])


def beeswarm_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Groups (highest median first) with their quartiles, and the shared value range."""
    mi, groups = _groups(result, spec)
    stats = sorted(({"label": k, **_quartiles(v)} for k, v in groups.items()), key=lambda g: -g["median"])
    values = [v for vs in groups.values() for v in vs]
    return {"groups": stats, "min": _r(min(values, default=0.0)), "max": _r(max(values, default=0.0)),
            "table": _spread_table(result, mi, stats, "Spread")}


RIDGE_POINTS = 48  # density evaluated at this many points across the shared range


def _density(values: list[float], lo: float, hi: float) -> list[float]:
    """Gaussian kernel density (Silverman's bandwidth), scaled so its peak is 1."""
    n = len(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    q = statistics.quantiles(values, n=4, method="inclusive") if n > 1 else [0.0, 0.0, 0.0]
    iqr = q[2] - q[0]
    spread = min(sd, iqr / 1.34) if iqr > 0 else sd
    h = 0.9 * spread * n ** -0.2 if spread > 0 else max((hi - lo) / 20, 1e-9)
    xs = [lo + (hi - lo) * k / (RIDGE_POINTS - 1) for k in range(RIDGE_POINTS)]
    ys = [sum(math.exp(-0.5 * ((x - v) / h) ** 2) for v in values) for x in xs]
    peak = max(ys) or 1.0
    return [round(y / peak, 4) for y in ys]


def ridgeline_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Each group's density (peak 1) over one shared range, its quartiles and whiskers; highest median first."""
    mi, groups = _groups(result, spec)
    values = [v for vs in groups.values() for v in vs]
    lo, hi = min(values, default=0.0), max(values, default=0.0)
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    if min(values, default=0.0) >= 0:
        lo = max(0.0, lo)
    stats = sorted(({"label": k, **_quartiles(v), "density": _density(v, lo, hi)} for k, v in groups.items()),
                   key=lambda g: -g["median"])
    return {"groups": stats, "min": _r(lo), "max": _r(hi), "points": RIDGE_POINTS,
            "table": _spread_table(result, mi, stats, "Spread by group")}


def today() -> date:
    """The day open items are counted to (patched in tests)."""
    return date.today()


def survival_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Kaplan-Meier estimate of the share of items not yet ended, by days since their start. An item without an
    end date is still open: it counts to today, then leaves the risk set (censored). Items without a start, or that
    end before they start, are left out and counted. The median is the first day the share falls to 50 % or below."""
    dates = [c["name"] for c in result["columns"]
             if c["kind"] != "measure" and (c["kind"] == "period" or c.get("data_type") in ("date", "datetime"))]
    si, ei, gi = _idx(result, dates[0]), _idx(result, dates[1]), _idx(result, spec.y)
    as_of = today()
    subjects: dict[str, list[tuple[int, bool]]] = {}
    left_out = 0
    for r in result["rows"]:
        start, end = _day(r[si]), _day(r[ei])
        if start is None or (end is not None and end < start):
            left_out += 1
            continue
        stop, event = (end, True) if end is not None else (as_of, False)
        subjects.setdefault(_label(r[gi]) if gi >= 0 else "All", []).append((max(0, (stop - start).days), event))
    curves: list[dict[str, Any]] = []
    rows: list[list[Any]] = []
    for label, subs in sorted(subjects.items(), key=lambda kv: -len(kv[1])):
        at_risk, s, median = len(subs), 1.0, None
        points: list[dict[str, Any]] = [{"t": 0, "s": 1.0, "at_risk": at_risk, "events": 0, "censored": 0}]
        for t, group in itertools.groupby(sorted(subs), key=lambda x: x[0]):
            g = list(group)
            events = sum(1 for _, e in g if e)
            censored = len(g) - events
            if events:
                s *= 1 - events / at_risk
                if median is None and round(s, 9) <= 0.5:
                    median = t
            points.append({"t": t, "s": round(s, 4), "at_risk": at_risk, "events": events, "censored": censored})
            rows.append([label, t, at_risk, events, censored, _r(s * 100, 1)])
            at_risk -= len(g)
        curves.append({"label": label, "n": len(subs), "events": sum(1 for _, e in subs if e), "median": median,
                       "points": points})
    end_label = next(c["label"] for c in result["columns"] if c["name"] == dates[1])
    table = _table("Time to event", [_col("group", "Group"), _col("days", "Days since start", "integer"),
                                     _col("at_risk", "Not ended before", "integer"),
                                     _col("events", "Ended", "integer"), _col("censored", "Still open", "integer"),
                                     _col("share", "Share not yet ended", "percent")], rows)
    return {"start": dates[0], "end": dates[1], "event": end_label, "as_of": as_of.isoformat(), "curves": curves,
            "left_out": left_out, "max_t": max((p["t"] for c in curves for p in c["points"]), default=0),
            "table": table}


def bump_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Each item's rank (1 = highest value) in each period; ties share the better rank. Items ordered by their
    rank in the last period. An item with no row in a period has no rank there."""
    xi, yi, mi = _idx(result, spec.x), _idx(result, spec.y), _idx(result, spec.series[0])
    periods = sorted({_label(r[xi]) for r in result["rows"]})
    items = sorted({_label(r[yi]) for r in result["rows"]})
    value = {(_label(r[xi]), _label(r[yi])): _num(r[mi]) for r in result["rows"] if isinstance(r[mi], int | float)}
    ranks: dict[str, list[int | None]] = {i: [] for i in items}
    for p in periods:
        present = [(i, value[(p, i)]) for i in items if (p, i) in value]
        for i in items:
            v = value.get((p, i))
            ranks[i].append(None if v is None else 1 + sum(1 for _, w in present if w > v))
    last = {i: next((r for r in reversed(ranks[i]) if r is not None), len(items) + 1) for i in items}
    order = sorted(items, key=lambda i: (last[i], i))
    out = [{"label": i, "ranks": ranks[i], "values": [_r(value[(p, i)]) if (p, i) in value else None
                                                      for p in periods]} for i in order]
    table = _table("Rank by period", [_col("item", result["columns"][yi]["label"]),
                                      *[_col(p, p, "integer") for p in periods]],
                   [[o["label"], *o["ranks"]] for o in out])
    return {"periods": periods, "items": out, "table": table}


def treemap_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Tiles with their value and share of the total; with two breakdowns, the outer level holds the inner one.
    Largest first at each level; tiles of zero are left out."""
    xi, yi, mi = _idx(result, spec.x), _idx(result, spec.y), _idx(result, spec.series[0])
    total = sum(_num(r[mi]) for r in result["rows"])
    tree: dict[str, dict[str, float]] = {}
    for r in result["rows"]:
        tree.setdefault(_label(r[xi]), {})
        if yi >= 0:
            tree[_label(r[xi])][_label(r[yi])] = tree[_label(r[xi])].get(_label(r[yi]), 0.0) + _num(r[mi])
        else:
            tree[_label(r[xi])][""] = tree[_label(r[xi])].get("", 0.0) + _num(r[mi])

    def share(v: float) -> float | int | None:
        return _r(v / total * 100, 1) if total else None

    nodes: list[dict[str, Any]] = []
    for outer, inner in sorted(tree.items(), key=lambda kv: -sum(kv[1].values())):
        v = sum(inner.values())
        if v <= 0:
            continue
        kids = [{"label": k, "value": _r(w), "share": share(w)} for k, w in sorted(inner.items(), key=lambda kv: -kv[1])
                if w > 0 and yi >= 0]
        nodes.append({"label": outer, "value": _r(v), "share": share(v), "children": kids})
    fmt = _fmt(result, mi)
    head = [_col("outer", result["columns"][xi]["label"])]
    if yi >= 0:
        head.append(_col("inner", result["columns"][yi]["label"]))
    rows = ([[n["label"], k["label"], k["value"], k["share"]] for n in nodes for k in n["children"]] if yi >= 0
            else [[n["label"], n["value"], n["share"]] for n in nodes])
    table = _table("Tiles", [*head, _col("value", result["columns"][mi]["label"], fmt),
                             _col("share", "Share of total", "percent")], rows)
    return {"nodes": nodes, "total": _r(total), "table": table}


WAFFLE_CELLS = 100


def waffle_figures(result: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Cells out of 100 per part, by largest remainder, so they add to 100 exactly. A part and its whole (one row,
    two counts) is two parts: the part and the rest."""
    if spec.x is None or _idx(result, spec.x) < 0:
        part, whole = _num(result["rows"][0][0]), _num(result["rows"][0][1])
        a, b = result["columns"][0]["label"], result["columns"][1]["label"]
        parts = [(a, part), (f"Rest of {b.lower()}", whole - part)]
        fmt = _fmt(result, 0)
    else:
        xi, mi = _idx(result, spec.x), _idx(result, spec.series[0])
        parts = [(_label(r[xi]), _num(r[mi])) for r in result["rows"]]
        fmt = _fmt(result, mi)
    total = sum(v for _, v in parts)
    exact = [v / total * WAFFLE_CELLS if total else 0.0 for _, v in parts]
    cells = [math.floor(e) for e in exact]
    for k in sorted(range(len(parts)), key=lambda k: -(exact[k] - cells[k]))[:WAFFLE_CELLS - sum(cells) if total
                                                                              else 0]:
        cells[k] += 1
    out = [{"label": k, "value": _r(v), "pct": _r(v / total * 100, 1) if total else None, "cells": c}
           for (k, v), c in zip(parts, cells, strict=True)]
    table = _table("Out of 100", [_col("part", "Part"), _col("value", "Value", fmt),
                                  _col("pct", "Share", "percent"), _col("cells", "Cells of 100", "integer")],
                   [[o["label"], o["value"], o["pct"], o["cells"]] for o in out])
    return {"parts": out, "total": _r(total), "table": table}


FIGURES: dict[str, Any] = {"waterfall": waterfall_figures, "pareto": pareto_figures, "xmr": xmr_figures,
                           "bullet": bullet_figures, "funnel": funnel_figures, "gantt": gantt_figures,
                           "small_multiples": panel_figures, "dumbbell": dumbbell_figures,
                           "calendar": calendar_figures, "sankey": sankey_figures, "upset": upset_figures,
                           "beeswarm": beeswarm_figures, "ridgeline": ridgeline_figures,
                           "survival": survival_figures, "bump": bump_figures, "treemap": treemap_figures,
                           "waffle": waffle_figures}
