"""Scoring for the gold-set evaluation (US-184, gold set 2.0; FEAT-064).

Pure functions, no database, shared by the live suite (``knowledge/gold.py``) and the offline harness:

* **Execution accuracy** — a planned answer passes when it gives the same rows as the expected spec, not only when
  the specs match: two different specs can give the same right answer. Only a result of two or more rows with a
  non-zero number counts: one figure, no rows or all zeros match by chance too often (open projects in KSA and
  in Jordan are the same figure on the synthetic extract), so there the spec must match.
* **Error taxonomy** — each failure gets one class: linking, measure, filter, time, shape, over_clarification,
  result or error.
* **Retrieval quality** — reciprocal rank of the expected measure, nDCG@8 over the measure and dimensions the
  answer needs, recall per kind, and value-linking accuracy (filter values right) for questions with filters.
* **Per path and per set** (US-190) — each result's outcome (right, wrong, clarified, refused, error), the
  **confident-wrong** rate (a wrong answer given at or above the person's confidence threshold), whether a
  clarification offered the right measure, **required-item recall** (the expected measure and dimensions among the
  linking candidates and the retrieved knowledge, also for refused plans) and planning latency, grouped by planner
  path (rules, free-hand, LLM) and by set (the original questions, the held-out paraphrases, the noise set of
  US-220).
* **Answer shape** (US-250) — the kind of answer a spec gives (one number, a list of records, a breakdown, a ranking,
  a trend, a comparison, a share, a distribution …), read from the spec by ``answer_shape``. Gold compares it as well
  as the rows (one count is never a list), and a run reports right / confident-wrong / clarified / refused per shape
  and per shape by tag (``shape_grid``), so a whole kind of question can't go missing unnoticed.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

TAXONOMY = ("linking", "measure", "filter", "time", "shape", "over_clarification", "result", "error")
SHAPE_PATHS = ("answer_shape", "breakdowns", "extra_measures", "derived", "share_of_total", "compare", "sort", "limit",
               "visual")
TOP_K = 8
PATHS = ("rules", "freehand", "llm")
OUTCOMES = ("right", "wrong", "clarified", "refused", "error")
# US-250: every kind of answer a question can ask for (the question frame's ``shape``, ENH-028). The last two have no
# Query Spec yet (one record's details, what a term means); a gold example names them by hand.
SHAPES = ("number", "list_of_records", "breakdown", "ranking", "trend", "comparison", "share", "distribution",
          "record_detail", "definition")


# --------------------------------------------------------------------------- execution


def _cell(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int | float | Decimal):
        f = float(v)
        return None if math.isnan(f) else round(f, 6)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return str(v)


def _rows(rows: list[Any]) -> list[tuple[Any, ...]]:
    return [tuple(_cell(c) for c in (r.values() if isinstance(r, dict) else r)) for r in rows]


def informative(rows: list[Any]) -> bool:
    """True when matching rows say something: two or more rows and a non-zero number among them."""
    norm = _rows(rows)
    return len(norm) >= 2 and any(isinstance(c, float) and c != 0 for r in norm for c in r)


def same_result(expected: list[Any], actual: list[Any], *, ordered: bool = False) -> bool:
    """Same rows (cell by cell, numbers to 6 decimals); in order only when the spec sorts and limits."""
    e, a = _rows(expected), _rows(actual)
    if ordered:
        return e == a
    return Counter(map(repr, e)) == Counter(map(repr, a))


def _sql_cell(v: Any) -> Any:
    """A cell compared across a spec's result and a statement's: a midnight timestamp is its date (a SQL period
    truncation gives ``2026-01-01T00:00:00`` where the spec gives ``2026-01-01``)."""
    if isinstance(v, datetime) and (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0):
        return v.date().isoformat()
    return _cell(v)


def says_something(rows: list[Any]) -> bool:
    """A result that can be matched by its rows: at least one row with a non-zero number or a non-blank value."""
    return any(c not in (None, 0.0, "", False) for r in _rows(rows) for c in r)


def _listed(row: tuple[Any, ...]) -> bool:
    numbers = [c for c in row[1:] if isinstance(c, float)]
    return not numbers or any(c != 0 for c in numbers)


def direct_match(expected: list[Any], actual: list[Any], *, shape: str | None, ordered: bool = False) -> bool:
    """Whether a Direct SQL answer gives the expected spec's rows (US-259). The statement names and orders its columns
    its own way, so each row is compared as the set of its values; a list or a record's details as the same records:
    the values of the expected first column (the record's name, rows counted 0 left out) are one of the answer's
    columns. An expected result
    with nothing in it (no rows, all zeros or blanks) never matches: it proves nothing."""
    if not says_something(expected):
        return False
    exp = [tuple(_sql_cell(c) for c in (r.values() if isinstance(r, dict) else r)) for r in expected]
    got = [tuple(_sql_cell(c) for c in (r.values() if isinstance(r, dict) else r)) for r in actual]
    if shape in ("list_of_records", "record_detail"):
        # A spec lists records as a count by name, every record with its count: those counted 0 aren't listed.
        exp = [r for r in exp if r and _listed(r)]
        want = Counter(map(repr, (r[0] for r in exp)))
        return len(exp) == len(got) and any(
            Counter(map(repr, (r[i] for r in got))) == want for i in range(min(map(len, got), default=0)))
    rows_e = [sorted(map(repr, r)) for r in exp]
    rows_a = [sorted(map(repr, r)) for r in got]
    if ordered:
        return rows_e == rows_a
    return Counter(map(tuple, rows_e)) == Counter(map(tuple, rows_a))


def frame_match(expected: list[Any], actual: list[Any], *, shape: str | None, ordered: bool = False) -> bool:
    """Whether a Fields or Hybrid answer (an ad-hoc measure, US-254/255) gives the expected rows (US-259). Its spec
    can never equal a certified one, so one figure is compared by its value (non-zero) and a list as the same records
    (the expected 0-count records left out, as the list template does), as Direct SQL is; other shapes as always."""
    if shape in ("number", "list_of_records", "record_detail"):
        return direct_match(expected, actual, shape=shape, ordered=ordered)
    return informative(expected) and same_result(expected, actual, ordered=ordered)


def ordered_spec(spec: dict[str, Any]) -> bool:
    return bool(spec.get("limit")) and bool(spec.get("sort"))


# --------------------------------------------------------------------------- answer shape (US-250)


def is_record_ref(ref: str | None) -> bool:
    """A breakdown that gives one row per record: an entity's name, title, number or id field
    (``field:projects.name``, ``field:tpg_project.tpg_projectname``, ``field:pcrs.pcr_number``). Read from the ref
    alone, so a stored run is graded the same way without its model."""
    if not ref or not ref.startswith("field:") or "." not in ref:
        return False
    entity, field = ref.removeprefix("field:").split(".", 1)
    stems = {entity, entity.rstrip("s"), entity.split("_", 1)[-1], entity.split("_", 1)[-1].rstrip("s")}
    stems |= {entity.split("_", 1)[0]} if "_" in entity else set()  # a publisher prefix: tpg_risk.tpg_title
    own = {f"{s}{sep}{part}" for s in stems for sep in ("", "_") for part in ("name", "title", "number", "id")}
    return field in {"name", "title", "id"} | own


def answer_shape(spec: dict[str, Any] | None) -> str | None:
    """The kind of answer a spec gives (one of ``SHAPES``); None without a spec.

    Read in this order: a period comparison, a share of the total, a trend (time grain), a distribution (bands), a
    ranking (top N, rank, top N per group), one row per record (a name or id breakdown), a breakdown (or groups with
    a having condition), two or more figures side by side, else one number (a total, a ratio of two measures, a
    percentile)."""
    if not spec:
        return None
    if spec.get("compare"):
        return "comparison"
    if spec.get("share_of_total"):
        return "share"
    if (spec.get("time") or {}).get("grain"):
        return "trend"
    if spec.get("bucket"):
        return "distribution"
    if spec.get("limit") or spec.get("rank") is not None or spec.get("top_n_per_group"):
        return "ranking"
    refs = [b.get("ref") for b in spec.get("breakdowns") or []]
    if spec.get("percentile"):  # a statistic over the records: their breakdown is how it is computed
        refs = [r for r in refs if not is_record_ref(r)]
    elif any(is_record_ref(r) for r in refs):
        return "list_of_records"
    if refs or spec.get("having"):
        return "breakdown"
    if spec.get("extra_measures") and not spec.get("derived"):  # figures side by side; a ratio of two is one number
        return "comparison"
    return "number"


def shape_diff(expected_shape: str | None, actual: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The answer-shape difference of a planned spec, as a spec-diff entry (empty when the kinds agree)."""
    got = answer_shape(actual)
    if actual is None or expected_shape is None or got == expected_shape:
        return []
    return [{"path": "answer_shape", "expected": expected_shape, "actual": got}]


# --------------------------------------------------------------------------- Hybrid counterpart (US-255)

COUNTERPART_PATH = "hybrid_counterpart"


def counterpart_spec(spec: dict[str, Any], ref: str) -> tuple[dict[str, Any], str]:
    """The certified measure ``ref`` asked the way a Fields/Hybrid answer ``spec`` was (US-255), and how the two
    results must agree: a list of records (``rows``) has as many rows as the measure counts with the same
    filters and period; any other answer (``same``) gives the same rows with the measure in place of its ad-hoc
    twin."""
    if answer_shape(spec) == "list_of_records":
        out: dict[str, Any] = {"measure": {"ref": ref}, "breakdowns": [], "filters": list(spec.get("filters") or [])}
        if (time := spec.get("time")) and time.get("range"):
            out["time"] = {k: v for k, v in time.items() if k != "grain"}
        return out, "rows"
    out = {**spec, "measure": {"ref": ref}}
    if spec.get("share_of_total"):
        out["share_of_total"] = [ref]
    return out, "same"


def counterpart_problem(how: str, b_rows: list[Any], c_rows: list[Any], ref: str) -> dict[str, Any] | None:
    """The diff entry when a Fields/Hybrid answer and its certified counterpart disagree (a failing case), else
    None."""
    if how == "rows":
        want = _rows(c_rows)[0][-1] if c_rows else 0.0
        got = float(len(b_rows))
        if want is None or float(want) == got:
            return None
        return {"path": COUNTERPART_PATH, "expected": f"{ref}: {want:g}", "actual": f"{got:g} rows"}
    if same_result(c_rows, b_rows):
        return None
    return {"path": COUNTERPART_PATH, "expected": f"{ref}: {len(c_rows)} rows", "actual": "different rows"}


def _grid_cell(rs: list[dict[str, Any]]) -> dict[str, int]:
    out = Counter(outcome(r.get("status"), bool(r["passed"])) for r in rs)
    return {"total": len(rs), "right": out["right"], "confident_wrong": sum(bool(r.get("confident_wrong")) for r in rs),
            "clarified": out["clarified"], "refused": out["refused"], "wrong": out["wrong"], "error": out["error"]}


def shape_grid(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Right / confident-wrong / clarified / refused per answer shape (``rows``, totalling the run) and per shape by
    tag (``cells``; an example with two tags is in two cells of its row). Results carry ``shape`` (None: unknown)."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        rows.setdefault(r.get("shape") or "unknown", []).append(r)
    order = {s: i for i, s in enumerate(SHAPES)}
    out: dict[str, Any] = {"rows": {}, "cells": {}, "tags": sorted({t for r in results for t in r.get("tags") or []})}
    for shape, rs in sorted(rows.items(), key=lambda kv: order.get(kv[0], len(order))):
        out["rows"][shape] = _grid_cell(rs)
        tags: dict[str, list[dict[str, Any]]] = {}
        for r in rs:
            for t in r.get("tags") or ["untagged"]:
                tags.setdefault(t, []).append(r)
        out["cells"][shape] = {t: _grid_cell(x) for t, x in sorted(tags.items())}
    out["total"] = _grid_cell(results)
    return out


def grid_text(grid: dict[str, Any], tags: list[str] | None = None) -> str:
    """The shape grid as a plain table: one line per shape (its totals), then one line per tag it has. Each cell reads
    right/total, then cw (confident-wrong), cl (clarified) and rf (refused) when not zero."""
    def fmt(c: dict[str, int]) -> str:
        extra = " ".join(f"{k} {c[v]}" for k, v in (("cw", "confident_wrong"), ("cl", "clarified"), ("rf", "refused"))
                         if c[v])
        return f"{c['right']}/{c['total']}" + (f"  {extra}" if extra else "")
    lines = [f"{'shape / tag':<28} right/total  cw = confident-wrong, cl = clarified, rf = refused"]
    for shape, row in grid["rows"].items():
        lines.append(f"{shape:<28} {fmt(row)}")
        for tag, cell in grid["cells"][shape].items():
            if tags is None or tag in tags:
                lines.append(f"  {tag:<26} {fmt(cell)}")
    lines.append(f"{'all':<28} {fmt(grid['total'])}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- taxonomy


def filter_op(f: dict[str, Any]) -> str:
    """``in`` / ``not_in`` with one value is ``=`` / ``!=`` ("only KSA" refines to ``in [KSA]``)."""
    op = str(f.get("op") or "=")
    if len(f.get("values") or []) == 1:
        return {"in": "=", "not_in": "!="}.get(op, op)
    return op


def _filters_by_ref(spec: dict[str, Any] | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in (spec or {}).get("filters") or []:
        out.setdefault(f"{f['ref']}|{filter_op(f)}", set()).update(map(str, f.get("values") or []))
    return out


def classify(diff: list[dict[str, Any]], *, status: str = "ok", expected: dict[str, Any] | None = None,
             actual: dict[str, Any] | None = None) -> str | None:
    """The one error class of a failed example (None when nothing failed).

    ``status`` is the planner's: ``clarify`` is over-clarification (every gold question has one right reading),
    ``unsupported`` means the question was not linked to the model, ``error`` means planning or running broke.
    A filter on the right field with the wrong values is a linking failure (the words were not matched to the
    stored values); a missing or extra filter is a filter failure.
    """
    if status == "error":
        return "error"
    if status == "clarify":
        return "over_clarification"
    if status != "ok" or actual is None:
        return "linking"
    paths = {d["path"] for d in diff}
    if not paths:
        return None
    if "measure" in paths:
        return "measure"
    if "filters" in paths:
        e, a = _filters_by_ref(expected), _filters_by_ref(actual)
        return "linking" if e.keys() == a.keys() else "filter"
    if "time" in paths:
        return "time"
    if paths & set(SHAPE_PATHS):
        return "shape"
    if "result" in paths or COUNTERPART_PATH in paths:
        return "result"
    return "error"


def values_linked(expected: dict[str, Any], actual: dict[str, Any] | None, *, passed: bool = False) -> bool | None:
    """For a question with filters: did every expected filter come out with the same values? None without filters,
    and None for a right answer that needed no filter on those fields (another spec with the same rows)."""
    e = _filters_by_ref(expected)
    if not e:
        return None
    a = _filters_by_ref(actual)
    if passed and not e.keys() & a.keys():
        return None
    return all(a.get(k) == v for k, v in e.items())


# --------------------------------------------------------------------------- retrieval


def targets(expected: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Measure keys and dimension keys the answer needs (an ad-hoc measure has no chunk to find)."""
    refs = [expected["measure"].get("ref")] + [m.get("ref") for m in expected.get("extra_measures") or []]
    measures = {r.split(":", 1)[1] for r in refs if r and r != "measure:_adhoc"}
    dims = {b["ref"] for b in expected.get("breakdowns") or []} | {f["ref"] for f in expected.get("filters") or []}
    if (expected.get("time") or {}).get("ref"):
        dims.add(expected["time"]["ref"])
    return measures, {d.split(":", 1)[1] for d in dims}


def _covers(meta: dict[str, Any], measures: set[str], dims: set[str]) -> set[str]:
    out = {f"measure:{meta['measure']}"} if meta.get("measure") in measures else set()
    return out | {f"dimension:{d}" for d in meta.get("dimensions") or [] if d in dims}


def retrieval_scores(hits: list[dict[str, Any]], expected: dict[str, Any], k: int = TOP_K) -> dict[str, Any]:
    """Scores of one retrieval (hit metadata, best first) against what the expected spec needs.

    nDCG counts a hit only for the targets it adds, so eight chunks about one measure don't score as eight hits;
    the ideal is one chunk per target.
    """
    measures, dims = targets(expected)
    wanted = {f"measure:{m}" for m in measures} | {f"dimension:{d}" for d in dims}
    seen: set[str] = set()
    dcg, rank = 0.0, None
    for i, meta in enumerate(hits[:k]):
        new = _covers(meta, measures, dims) - seen
        if new:
            dcg += 1 / math.log2(i + 2)
            seen |= new
        if rank is None and meta.get("measure") in measures:
            rank = i + 1
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(wanted), k)))
    found_dims = {t for t in seen if t.startswith("dimension:")}
    return {
        "measure_rank": rank,
        "reciprocal_rank": round(1 / rank, 4) if rank else (0.0 if measures else None),
        "ndcg_at_8": round(dcg / ideal, 4) if ideal else None,
        "recall": {"measure": (1.0 if rank else 0.0) if measures else None,
                   "dimension": round(len(found_dims) / len(dims), 4) if dims else None},
    }


# --------------------------------------------------------------------------- per path and per set (US-190)


def example_set(tags: list[str] | None) -> str:
    """``paraphrase`` for the held-out rewordings (US-190), ``noise`` for the wording-noise set (US-220),
    ``original`` for every other example."""
    tags = tags or []
    return "paraphrase" if "paraphrase" in tags else "noise" if "noise" in tags else "original"


def outcome(status: str | None, passed: bool) -> str:
    """right, wrong (answered, not the expected rows), clarified, refused or error."""
    if passed:
        return "right"
    return {"ok": "wrong", "clarify": "clarified", "unsupported": "refused"}.get(status or "error", "error")


def offered_right(expected: dict[str, Any], question: dict[str, Any] | None) -> bool | None:
    """For a clarification: was the expected measure among its options (a measure option, or a reading's spec)?
    None when Ask didn't ask."""
    if not question:
        return None
    want = (expected.get("measure") or {}).get("ref")
    refs = {str(o.get("measure")) for o in question.get("options") or [] if o.get("measure")}
    refs |= {str(((s or {}).get("measure") or {}).get("ref")) for s in question.get("readings") or []}
    return want in refs


def required_recall(expected: dict[str, Any], linked: list[str], hits: list[dict[str, Any]],
                    k: int = TOP_K) -> dict[str, Any] | None:
    """Were the items the answer needs offered to the planner? The expected measures among the linking candidates
    (``linked``: ``measure:…`` / ``dimension:…`` refs) and the top ``k`` retrieved chunks, and the share of its
    dimensions in each. ``found`` is everything needed, from either. None when the answer needs nothing nameable."""
    measures, dims = targets(expected)
    if not measures and not dims:
        return None
    refs = set(linked)
    top = hits[:k]
    got_m = {str(h.get("measure")) for h in top if h.get("measure")}
    got_d = {str(d) for h in top for d in h.get("dimensions") or []}
    m_linked = {m for m in measures if f"measure:{m}" in refs}
    d_linked = {d for d in dims if f"dimension:{d}" in refs}
    m_found = m_linked | (measures & got_m)
    d_found = d_linked | (dims & got_d)
    return {
        "measure": {"linked": m_linked == measures, "retrieved": measures <= got_m} if measures else None,
        "dimensions": {"linked": round(len(d_linked) / len(dims), 4),
                       "retrieved": round(len(dims & got_d) / len(dims), 4)} if dims else None,
        "found": m_found == measures and d_found == dims,
    }


def _share(n: int, total: int) -> float | None:
    return round(n / total, 4) if total else None


def group_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and rates of one group of results (keys: passed, spec_match, status, confident_wrong, clarify_right,
    required, latency_ms, llm_calls)."""
    n = len(results)
    outcomes = Counter(outcome(r.get("status"), bool(r["passed"])) for r in results)
    req = [r["required"] for r in results if r.get("required")]
    req_m = [x["measure"] for x in req if x.get("measure")]
    lat = sorted(int(r["latency_ms"]) for r in results if r.get("latency_ms") is not None)
    wrong_sure = sum(bool(r.get("confident_wrong")) for r in results)
    calls = [int(r["llm_calls"]) for r in results if r.get("llm_calls") is not None]
    return {
        "total": n, "passed": outcomes["right"], "spec_matched": sum(bool(r.get("spec_match")) for r in results),
        "outcomes": {o: outcomes[o] for o in OUTCOMES if outcomes[o]},
        "confident_wrong": wrong_sure, "clarify_right": sum(r.get("clarify_right") is True for r in results),
        "pass_rate": _share(outcomes["right"], n),
        "spec_match_rate": _share(sum(bool(r.get("spec_match")) for r in results), n),
        "refusal_rate": _share(outcomes["refused"], n), "clarify_rate": _share(outcomes["clarified"], n),
        "confident_wrong_rate": _share(wrong_sure, n),
        "required_recall": {
            "total": len(req),
            "found": _mean([float(x["found"]) for x in req]),
            "measure_linked": _mean([float(x["linked"]) for x in req_m]),
            "measure_retrieved": _mean([float(x["retrieved"]) for x in req_m]),
        },
        "latency_ms": {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, math.ceil(len(lat) * 0.95) - 1)],
                       "max": lat[-1]} if lat else None,
        "llm_calls": {"total": sum(calls), "per_question": _mean([float(c) for c in calls])} if calls else None,
    }


def by_path(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``group_scores`` per planner path; ``none`` for examples that were never planned."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        groups.setdefault(r.get("planner") or "none", []).append(r)
    order = {p: i for i, p in enumerate(PATHS)}
    return {p: group_scores(rs) for p, rs in sorted(groups.items(), key=lambda kv: order.get(kv[0], len(order)))}


def by_set(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``group_scores`` and ``by_path`` per set (original, paraphrase, noise). On the original set a clarification is
    over-clarification: every gold question has one right reading."""
    out: dict[str, dict[str, Any]] = {}
    for name in ("original", "paraphrase", "noise"):
        rs = [r for r in results if example_set(r.get("tags")) == name]
        if rs:
            out[name] = {**group_scores(rs), "by_path": by_path(rs)}
    return out


# --------------------------------------------------------------------------- summary


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Run totals from per-example results (keys: passed, spec_match, tags, error_class, retrieval, values_linked)."""
    total = len(results)
    passed = sum(bool(r["passed"]) for r in results)
    spec_ok = sum(bool(r.get("spec_match")) for r in results)
    by_tag: dict[str, dict[str, Any]] = {}
    for r in results:
        for tag in r.get("tags") or ["untagged"]:
            t = by_tag.setdefault(tag, {"total": 0, "passed": 0, "spec_matched": 0})
            t["total"] += 1
            t["passed"] += int(bool(r["passed"]))
            t["spec_matched"] += int(bool(r.get("spec_match")))
    for t in by_tag.values():
        t["pass_rate"] = round(t["passed"] / t["total"], 4)
    by_error = dict(sorted(Counter(r["error_class"] for r in results if r.get("error_class")).items()))
    ret = [r["retrieval"] for r in results if r.get("retrieval")]
    rr = [x["reciprocal_rank"] for x in ret if x.get("reciprocal_rank") is not None]
    nd = [x["ndcg_at_8"] for x in ret if x.get("ndcg_at_8") is not None]
    by_kind = {kind: _mean([x["recall"][kind] for x in ret if x["recall"].get(kind) is not None])
               for kind in ("measure", "dimension")}
    linked = [r["values_linked"] for r in results if r.get("values_linked") is not None]
    return {
        "total": total, "passed": passed, "spec_matched": spec_ok,
        "pass_rate": round(passed / total, 4) if total else None,
        "spec_match_rate": round(spec_ok / total, 4) if total else None,
        "by_tag": by_tag, "by_error": by_error,
        "recall_at_8": by_kind["measure"], "recall_by_kind": by_kind,
        "mrr": _mean(rr), "ndcg_at_8": _mean(nd),
        "value_linking": {"total": len(linked), "correct": sum(linked),
                          "rate": round(sum(linked) / len(linked), 4) if linked else None},
        "by_path": by_path(results), "by_set": by_set(results),
        **({"shape_grid": shape_grid(results)} if any("shape" in r for r in results) else {}),
    }


# --------------------------------------------------------------------------- LLM review gate (US-246)


def _gate_counts(run: dict[str, Any]) -> dict[str, Any]:
    """Right, confident-wrong, over-clarification and refusals of a finished run, overall and per set."""
    def pick(g: dict[str, Any]) -> dict[str, Any]:
        out = g.get("outcomes") or {}
        return {"total": g.get("total", 0), "right": g.get("passed", 0), "confident_wrong": g.get("confident_wrong", 0),
                "clarified": out.get("clarified", 0), "clarify_right": g.get("clarify_right", 0),
                "refused": out.get("refused", 0), "latency_ms": g.get("latency_ms"), "llm_calls": g.get("llm_calls")}

    sets = run.get("by_set") or {}
    every = [g for g in sets.values() if g]
    total = {k: sum(int(g.get(k, 0) or 0) for g in map(pick, every))
             for k in ("total", "right", "confident_wrong", "clarified", "clarify_right", "refused")}
    return {**total, "by_set": {name: pick(g) for name, g in sets.items()}}


def gate(off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    """DEC-28 point 3: review stays on by default only if, on the same examples, right answers rise and confident-wrong
    answers do not. ``off`` and ``on`` are finished runs (their ``by_set``)."""
    a, b = _gate_counts(off), _gate_counts(on)
    same = a["total"] == b["total"]
    keep = same and b["right"] > a["right"] and b["confident_wrong"] <= a["confident_wrong"]
    if not same:
        why = f"The runs covered different examples ({a['total']} and {b['total']}); rerun on the same set."
    elif keep:
        why = (f"Review on: {b['right']} right against {a['right']}, confident-wrong {b['confident_wrong']} against "
               f"{a['confident_wrong']}. Keep the default on for a hosted provider.")
    else:
        why = (f"Review on: {b['right']} right against {a['right']}, confident-wrong {b['confident_wrong']} against "
               f"{a['confident_wrong']}. Right answers must rise and confident-wrong must not: turn the default off.")
    return {"keep_default_on": keep, "reason": why, "off": a, "on": b,
            "delta": {k: b[k] - a[k] for k in ("right", "confident_wrong", "clarified", "clarify_right", "refused")}}
