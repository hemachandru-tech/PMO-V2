"""US-246: review forced on or off for a gold run, model calls counted, and the off-vs-on gate (DEC-28 point 3)."""

from __future__ import annotations

from typing import Any

from tela_api.knowledge import scoring
from tela_api.llm.client import _calls, counting
from tela_api.llm.review import forced, review_on

HOSTED_ON = {"provider": "anthropic", "llm_review": True}
HOSTED_OFF = {"provider": "anthropic", "llm_review": False}


def test_forced_overrides_the_switch_but_not_a_missing_provider() -> None:
    with forced(False):
        assert not review_on(HOSTED_ON)
    with forced(True):
        assert review_on(HOSTED_OFF)
        assert not review_on({"provider": None, "llm_review": True})  # no model: rules only
    with forced(None):
        assert review_on(HOSTED_ON) and not review_on(HOSTED_OFF)
    assert review_on(HOSTED_ON)  # reset after the block


def test_counting_is_scoped() -> None:
    assert _calls.get() is None
    with counting() as box:
        box[0] += 2
        assert _calls.get() is box
    assert box == [2] and _calls.get() is None


def res(passed: bool, status: str = "ok", *, sure: bool = False, latency: int = 100, calls: int = 1,
        tags: list[str] | None = None) -> dict[str, Any]:
    return {"passed": passed, "spec_match": passed, "status": status, "confident_wrong": sure, "clarify_right": None,
            "required": None, "latency_ms": latency, "llm_calls": calls, "tags": tags or ["baseline"]}


def test_group_scores_has_p95_and_calls() -> None:
    g = scoring.group_scores([res(True, latency=ms, calls=ms // 100) for ms in range(100, 2100, 100)])
    assert g["latency_ms"] == {"p50": 1100, "p95": 1900, "max": 2000}
    assert g["llm_calls"] == {"total": 210, "per_question": 10.5}
    assert scoring.group_scores([res(True, calls=None)])["llm_calls"] is None  # type: ignore[arg-type]


def run(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"by_set": scoring.by_set(results)}


def test_gate_keeps_review_only_when_right_rises_and_sure_wrong_does_not() -> None:
    off = run([res(True), res(False, sure=True), res(False, "clarify", tags=["paraphrase"])])
    better = run([res(True), res(True), res(False, "clarify", tags=["paraphrase"])])
    out = scoring.gate(off, better)
    assert out["keep_default_on"] and out["delta"]["right"] == 1 and out["delta"]["confident_wrong"] == -1
    assert out["on"]["by_set"]["paraphrase"]["clarified"] == 1

    more_wrong = run([res(True), res(True, tags=["paraphrase"]), res(False, sure=True), ])
    assert not scoring.gate(run([res(True), res(False), res(False)]), more_wrong)["keep_default_on"]
    assert not scoring.gate(off, off)["keep_default_on"]  # no gain: off

    out = scoring.gate(off, run([res(True)]))
    assert not out["keep_default_on"] and "different examples" in out["reason"]
