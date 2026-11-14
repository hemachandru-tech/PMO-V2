"""The held-out paraphrase set and per-path scoring, offline (ENH-016, US-190; FEAT-066 / FEAT-064; no database).

The pack's `paraphrase` examples reword the gold questions in words nobody tuned the planner on. This file checks
the set is curated (held out, one original each, the original's spec, no filter values) and scores it the way the
live suite does — outcome, confident-wrong, required-item recall from schema linking, spec match then execution on
DuckDB — beside the original set, per planner path. Offline there is no AG-03 or retrieval, so only the rules path
plans; the live suite (``tela-admin gold-run --tag paraphrase --plan-only``) adds free-hand and the LLM path.
US-192 adds a lexical retrieval stand-in (``lexical_hits``) to check that the measures retrieval finds are linked.
US-193 checks each rules plan as Ask does (``confidence.check_rules``, then ``decide`` at the balanced threshold), so a
plan that doesn't answer the question asks or answers with a note instead of being a confident wrong number.
"""

from __future__ import annotations

import math
from collections import Counter
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from test_gold_scoring_unit import db, run, snapshot  # noqa: F401 - pytest fixtures
from test_planner_gold_unit import INDEX, KNOWN, SNAP, plan

from tela_api.ask import confidence, meaning
from tela_api.ask.linking import link_schema, rank_measures
from tela_api.ask.planner import STOP, words
from tela_api.knowledge import scoring
from tela_api.knowledge.corpus import model_chunks
from tela_api.knowledge.gold import pack_draft, spec_diff
from tela_api.knowledge.values import link
from tela_query_spec.model import ModelSnapshot

STYLES = {"reworded", "colloquial", "look_alike", "breakdown_word", "money", "percentage"}
EXAMPLES = pack_draft()
BY_QUESTION = {e["question"]: e for e in EXAMPLES}
PARAPHRASES = [e for e in EXAMPLES if "paraphrase" in (e.get("tags") or [])]
ORIGINALS = [e for e in EXAMPLES if not e.get("held_out") and not e.get("turns")]
# Rules-path floor on the fixture (US-190 baseline 21, 2026-11-01): later stories may raise it, never lower it.
# US-193 lowered it once, by design (W4): answers its checks find unsure now ask, with the right option offered.
RULES_RIGHT_FLOOR = 34  # of 125: US-192 open qualifiers + US-194 shape words (33), US-193 checks (31), US-216/217 (33),
# BUG-025 (34)
RETRIEVED_RIGHT_FLOOR = 36  # with the lexical retrieval stand-in: 40 before US-193 (7 now ask, 33); US-216/217 35;
# BUG-025 36
# BUG-025 8: "Go-lives one to two months out" always picked Go-lives per month; the guessed Lifecycle phase: Closure
# ("out") that hid it is gone, so it now counts (confident wrong overall stays 11).
CONFIDENT_WRONG_MEASURE_CEILING = 8  # with the stand-in: 33 before US-192, 34 after; US-193 8; US-216/217 7
# US-193: confident wrong (at or above "balanced") — 50 of 125 before, with and without the stand-in; 15 and 14 after
# ENH-022 Wave 1; 12 and 11 after US-216/217 (names across word order, guessed filters ask).
CONFIDENT_WRONG_CEILING = 12
EXEC102_CONFIDENT_WRONG_CEILING = 2  # of 38; 12 before US-193 offline (EXEC-102 live: 10)


def test_paraphrase_set_is_curated() -> None:
    assert len(PARAPHRASES) >= 100
    for e in PARAPHRASES:
        assert e.get("held_out") is True, e["question"]  # never an AG-03 few-shot
        orig = BY_QUESTION.get(e.get("paraphrase_of", ""))
        assert orig is not None and "paraphrase" not in orig["tags"], e["question"]
        assert e["spec"] == orig["spec"], e["question"]
        assert not e["spec"].get("filters"), e["question"]  # no sample-only values: runs on TPG and on Dataverse
        assert set(e["tags"]) - {"paraphrase", "exec102"} <= STYLES and set(e["tags"]) & STYLES, e["question"]
    styles = Counter(t for e in PARAPHRASES for t in e["tags"] if t in STYLES)
    assert set(styles) == STYLES and min(styles.values()) >= 5, styles
    assert sum("exec102" in e["tags"] for e in PARAPHRASES) == 38  # the EXEC-102 probe's paraphrases
    # Rewordings, not copies; and every original kind is covered (counts, breakdowns, money, percentages, trends).
    assert all(e["question"].lower() != e["paraphrase_of"].lower() for e in PARAPHRASES)
    assert len({e["paraphrase_of"] for e in PARAPHRASES}) >= 45


CHUNKS = model_chunks(SNAP)
DOCS = [[w for w in words(c["content"]) if w not in STOP] for c in CHUNKS]
DF = Counter(w for d in DOCS for w in set(d))


def lexical_hits(question: str, k: int = 8) -> list[Any]:
    """A stand-in for typed retrieval offline: BM25 over the model's knowledge chunks (entities and certified
    measures), top 8. Only the lexical half: the live dense half also finds "sanctioned" near "approved"."""
    n, avg = len(DOCS), sum(map(len, DOCS)) / len(DOCS)
    terms = [w for w in words(question) if w not in STOP]
    scored = []
    for c, d in zip(CHUNKS, DOCS, strict=True):
        tf = Counter(d)
        s = sum(math.log(1 + (n - DF[w] + 0.5) / (DF[w] + 0.5)) * tf[w] * 2.2 / (tf[w] + 1.2 * (
            0.25 + 0.75 * len(d) / avg)) for w in terms if w in tf)
        if s:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [SimpleNamespace(kind=c["metadata"]["kind"], metadata=c["metadata"]) for _, c in scored[:k]]


def evaluate(db: duckdb.DuckDBPyConnection, snap: ModelSnapshot, e: dict[str, Any],  # noqa: F811
             retrieval: bool = False, index: list[Any] | None = None) -> dict[str, Any]:
    """One example as ``gold.evaluate_one`` records it, on the rules path; ``retrieval`` adds the stand-in's
    measures as meaning (US-192); ``index`` another value index (US-220)."""
    q, expected = e["question"], e["spec"]
    meant = meaning.from_hits(lexical_hits(q)) if retrieval else None
    p = plan(q, meaning=meant, index=index)
    linked = link(q, INDEX if index is None else index, known=KNOWN)
    confidence.check_rules(p, q, SNAP, SNAP.measures, SNAP.dimensions, linked.links,
                           rank_measures(q, SNAP, SNAP.measures, meant))
    p = confidence.decide(p, q, SNAP, SNAP.measures, None)
    links = link_schema(q, SNAP, SNAP.measures, SNAP.dimensions, linked=linked.links, choices=linked.choices,
                        considered=p.measures_considered, meaning=meant)
    res: dict[str, Any] = {"question": q, "tags": e.get("tags") or [], "planner": "rules", "status": p.status,
                           "confidence": p.confidence, "passed": False, "spec_match": False,
                           "confident_wrong": False, "clarify_right": None, "latency_ms": None,
                           "required": scoring.required_recall(expected, [*links.refs, *p.measures_considered], []),
                           "retrieved": expected["measure"]["ref"].split(":", 1)[1] in (meant or {})}
    if p.status != "ok" or p.spec is None:
        res["clarify_right"] = scoring.offered_right(expected, p.question) if p.status == "clarify" else None
        return res
    diff = spec_diff(expected, p.spec)
    res["spec_match"] = not diff
    res["measure_ok"] = p.spec["measure"] == expected["measure"]
    if diff:
        want, got = run(db, snap, expected), run(db, snap, p.spec)
        res["passed"] = scoring.informative(want) and scoring.same_result(
            want, got, ordered=scoring.ordered_spec(expected))
    else:
        res["passed"] = True
    res["confident_wrong"] = not res["passed"] and p.confidence >= confidence.threshold(None)
    return res


@pytest.fixture(scope="module")
def results(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> list[dict[str, Any]]:  # noqa: F811
    return [evaluate(db, snapshot, e) for e in [*ORIGINALS, *PARAPHRASES]]


@pytest.fixture(scope="module")
def retrieved(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> list[dict[str, Any]]:  # noqa: F811
    return [evaluate(db, snapshot, e, retrieval=True) for e in [*ORIGINALS, *PARAPHRASES]]


def test_paraphrases_are_scored_beside_the_originals(results: list[dict[str, Any]]) -> None:
    s = scoring.summarise(results)
    orig, para = s["by_set"]["original"], s["by_set"]["paraphrase"]
    print(f"\noriginal {orig['passed']}/{orig['total']} · paraphrase {para['passed']}/{para['total']} "
          f"(spec {para['spec_matched']}) · outcomes {para['outcomes']} · confident wrong {para['confident_wrong']} "
          f"· clarify right {para['clarify_right']} · required found {para['required_recall']['found']}")
    probe = scoring.group_scores([r for r in results if "exec102" in r["tags"]])
    print(f"EXEC-102 paraphrases offline: {probe['passed']}/{probe['total']} · outcomes {probe['outcomes']} "
          f"· confident wrong {probe['confident_wrong']}")
    # The original set is unchanged: every question right, never asked (no over-clarification).
    assert orig["passed"] == orig["total"] == len(ORIGINALS)
    assert orig["outcomes"] == {"right": orig["total"]} and orig["confident_wrong"] == 0
    # The paraphrases are measured, not tuned for: a floor, and the failure kinds the stories target are reported.
    assert para["total"] == len(PARAPHRASES)
    assert para["passed"] >= RULES_RIGHT_FLOOR
    assert sum(para["outcomes"].values()) == para["total"]
    # US-193: a wrong answer the checks doubt is answered with a note (below the threshold), not confidently.
    assert para["confident_wrong"] <= min(para["outcomes"].get("wrong", 0), CONFIDENT_WRONG_CEILING)
    assert probe["confident_wrong"] <= EXEC102_CONFIDENT_WRONG_CEILING
    assert set(s["by_path"]) == {"rules"} and s["by_path"]["rules"]["total"] == len(results)
    assert para["by_path"]["rules"]["total"] == para["total"]
    assert 0 < para["required_recall"]["found"] <= 1


def test_required_recall_is_kept_for_refused_plans(results: list[dict[str, Any]]) -> None:
    """A refused or clarified paraphrase still says whether linking offered the right measure (US-192's input)."""
    missed = [r for r in results if r["status"] != "ok"]
    assert missed and all(r["required"] is not None for r in missed)
    assert any(r["required"]["measure"]["linked"] for r in missed)


def test_retrieved_measures_are_linked_without_loss_on_the_originals(retrieved: list[dict[str, Any]]) -> None:
    """US-192 acceptance offline: every paraphrase whose right measure retrieval found is linked to it, the original
    set stays right and never asked, and more paraphrases are planned right by rules."""
    s = scoring.summarise(retrieved)
    orig, para = s["by_set"]["original"], s["by_set"]["paraphrase"]
    had = [r for r in retrieved if "paraphrase" in r["tags"] and r["retrieved"]]
    wrong_measure = [r for r in retrieved if "paraphrase" in r["tags"] and r["confident_wrong"] and not r["measure_ok"]]
    print(f"\nwith retrieval: original {orig['passed']}/{orig['total']} · paraphrase {para['passed']}/{para['total']} "
          f"· outcomes {para['outcomes']} · confident wrong {para['confident_wrong']} · retrieval had the measure "
          f"{len(had)}, linked "
          f"{sum(r['required']['measure']['linked'] for r in had)} · confident wrong measure {len(wrong_measure)}")
    assert orig["passed"] == orig["total"] and orig["outcomes"] == {"right": orig["total"]}
    assert had and all(r["required"]["measure"]["linked"] for r in had)
    assert para["passed"] >= RETRIEVED_RIGHT_FLOOR
    # W4: refusals turn into plans or questions, not into more confident wrong measures (33 without US-192, 34 with
    # it; US-193's checks ask the rest).
    assert len(wrong_measure) <= CONFIDENT_WRONG_MEASURE_CEILING
    assert para["confident_wrong"] <= CONFIDENT_WRONG_CEILING
    probe = scoring.group_scores([r for r in retrieved if "exec102" in r["tags"]])
    assert probe["confident_wrong"] <= EXEC102_CONFIDENT_WRONG_CEILING
