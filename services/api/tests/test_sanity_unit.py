"""Result sanity checks (US-183; FEAT-064), no database server: each check with a fake query runner."""

from __future__ import annotations

from typing import Any

from tela_api.ask import sanity
from tela_common.errors import ErrorCode, TelaError

N = {"name": "m", "kind": "measure", "label": "Open projects", "format": "integer"}
REGION = {"name": "region", "kind": "dimension", "label": "Region"}
MONTH = {"name": "month", "kind": "period", "label": "Month"}
BASE = {"model_version_id": "mv", "connection_id": "c", "measure": {"ref": "measure:open_projects"}}
KSA = {"ref": "dimension:region", "op": "=", "values": ["KSA"]}
PHASE = {"ref": "dimension:phase", "op": "=", "values": ["Closed"]}
RANGE = {"ref": "dimension:go_live", "range": {"relative": "this_month"}}


def result(cols: list[dict[str, Any]], rows: list[list[Any]], **extra: Any) -> dict[str, Any]:
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": False, **extra}


def figure(v: float | None) -> dict[str, Any]:
    return result([N], [] if v is None else [[v]])


class Runner:
    """Answers a probe by its filters and time range; records every probe spec."""

    def __init__(self, answers: dict[tuple[tuple[str, ...], bool], float | None], fail: bool = False) -> None:
        self.answers, self.fail, self.seen = answers, fail, []

    async def __call__(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.seen.append(spec)
        if self.fail:
            raise TelaError(ErrorCode.QUERY_TIMEOUT, "slow")
        assert not spec.get("breakdowns") and "limit" not in spec and "visual" not in spec
        key = (tuple(sorted(f["ref"] for f in spec.get("filters") or [])), "time" in spec)
        return figure(self.answers.get(key))


async def test_empty_names_the_filter_that_removed_everything() -> None:
    spec = {**BASE, "filters": [KSA, PHASE]}
    run = Runner({(("dimension:region",), False): 4})  # dropping Phase brings data back
    [n] = await sanity.check(spec, result([N], []), run, snap=None, additive=True, agg="count")
    assert n["kind"] == "empty" and n["fact"] == "s1"
    assert "Phase: Closed" in n["text"]
    assert n["action"]["ops"] == [{"op": "remove_filter", "ref": "dimension:phase"}]


async def test_empty_date_range_and_filters_together() -> None:
    spec = {**BASE, "filters": [KSA], "time": RANGE}
    run = Runner({(("dimension:region",), False): 3})
    [n] = await sanity.check(spec, result([N], []), run, snap=None, additive=True, agg="count")
    assert "date range" in n["text"] and n["action"]["ops"] == [{"op": "clear_time"}]
    run = Runner({((), False): 9})  # only without both
    [n] = await sanity.check(spec, result([N], []), run, snap=None, additive=True, agg="count")
    assert "together" in n["text"]
    assert n["action"]["ops"] == [{"op": "remove_filter"}, {"op": "clear_time"}]


async def test_empty_without_filters_and_nothing_anywhere() -> None:
    [n] = await sanity.check(BASE, result([N], []), Runner({}), snap=None, additive=True, agg="count")
    assert n["kind"] == "empty" and "action" not in n
    assert await sanity.check({**BASE, "filters": [KSA]}, result([N], []), Runner({}), snap=None, additive=True,
                              agg="count") == []


async def test_single_row_when_a_breakdown_was_asked() -> None:
    spec = {**BASE, "breakdowns": [{"ref": "dimension:region"}]}
    [n] = sanity.single_row(spec, result([REGION, N], [["KSA", 4]]), None)
    assert n["kind"] == "single_row" and "KSA" in n["text"] and "action" not in n
    [n] = sanity.single_row({**spec, "filters": [KSA]}, result([REGION, N], [["KSA", 4]]), None)
    assert n["action"]["ops"] == [{"op": "remove_filter", "ref": "dimension:region"}]
    assert sanity.single_row(BASE, figure(4), None) == []
    assert sanity.single_row(spec, result([REGION, N], [["KSA", 4], ["UAE", 2]]), None) == []
    grain = {**BASE, "time": {"ref": "dimension:go_live", "grain": "month"}}
    [n] = sanity.single_row(grain, result([MONTH, N], [["2026-10-01", 4]]), None)
    assert "one month" in n["text"]


async def test_filters_that_change_nothing_and_a_filtered_total_above_the_whole() -> None:
    spec = {**BASE, "filters": [KSA]}
    [n] = await sanity.check(spec, figure(12), Runner({((), False): 12}), snap=None, additive=True, agg="count")
    assert n["kind"] == "filters_no_effect" and "Region: KSA" in n["text"] and "12" in n["text"]
    [n] = await sanity.check(spec, figure(15), Runner({((), False): 12}), snap=None, additive=True, agg="count")
    assert n["kind"] == "filtered_above_total"
    # a sum may legitimately be higher with filters (negative values); a smaller figure is normal
    assert await sanity.check(spec, figure(15), Runner({((), False): 12}), snap=None, additive=True,
                              agg="sum") == []
    assert await sanity.check(spec, figure(5), Runner({((), False): 12}), snap=None, additive=True,
                              agg="count") == []
    # not additive: no totals compared, no probe
    run = Runner({((), False): 12})
    assert await sanity.check(spec, figure(12), run, snap=None, additive=False, agg="avg") == []
    assert run.seen == []


async def test_fan_out_and_excluded_rows_against_the_root_total() -> None:
    spec = {**BASE, "breakdowns": [{"ref": "dimension:region"}]}
    res = result([REGION, N], [["KSA", 6], ["UAE", 5], [None, 1]])
    [n] = await sanity.check(spec, res, Runner({((), False): 10}), snap=None, additive=True, agg="count")
    assert n["kind"] == "fan_out" and "12" in n["text"] and "10" in n["text"]
    [n] = await sanity.check(spec, res, Runner({((), False): 15}), snap=None, additive=True, agg="count")
    assert n["kind"] == "excluded" and n["numbers"] == {"excluded": 3, "total": 15, "share_pct": 20.0}
    # a top N leaves the rest out on purpose
    assert await sanity.check({**spec, "limit": 2, "sort": [{"by": "measure"}]}, res, Runner({((), False): 15}),
                              snap=None, additive=True, agg="count") == []
    assert await sanity.check(spec, res, Runner({((), False): 12}), snap=None, additive=True, agg="count") == []


async def test_probes_keep_filters_and_range_but_drop_the_shape() -> None:
    spec = {**BASE, "breakdowns": [{"ref": "dimension:region"}], "filters": [KSA], "limit": 5, "sort": [],
            "visual": {"type": "ranked_bar"}, "extra_measures": [{"ref": "measure:x"}],
            "time": {**RANGE, "grain": "month"}}
    run = Runner({(("dimension:region",), True): 3, ((), True): 3})
    notes = await sanity.check(spec, result([REGION, N], [["KSA", 3]]), run, snap=None, additive=True, agg="count")
    assert [n["kind"] for n in notes] == ["single_row", "filters_no_effect"]
    assert [n["fact"] for n in notes] == ["s1", "s2"]
    assert run.seen[0] == {**BASE, "filters": [KSA], "time": RANGE}
    assert run.seen[1] == {**BASE, "time": RANGE}


async def test_failing_probe_hidden_or_truncated_result_skip_quietly() -> None:
    spec = {**BASE, "filters": [KSA]}
    assert await sanity.check(spec, result([N], []), Runner({}, fail=True), snap=None, additive=True,
                              agg="count") == []
    assert await sanity.check(spec, figure(12), Runner({}, fail=True), snap=None, additive=True, agg="count") == []
    assert await sanity.check(spec, result([], [], hidden=True), Runner({}), snap=None, additive=True,
                              agg="count") == []
    assert await sanity.check(spec, result([REGION, N], [["KSA", 1]], truncated=True), Runner({}), snap=None,
                              additive=True, agg="count") == []
