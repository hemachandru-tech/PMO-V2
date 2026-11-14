"""The wording-noise gold set, offline (ENH-022, US-220; FEAT-072 / FEAT-064; no database).

The pack's `noise` examples wrap every original with filler, grammar and tense ("Kindly tell me …", "How many number
of …", "… will be …"), a singular and a typo (``knowledge/noise.py``). Each must plan its original's spec. This file
checks the pack still holds the generator's output and scores the set on the rules path the way the paraphrase set is
scored, with the fixture's value index plus the free-text values a real source has: the Dataverse hold reasons are
sentences ("Training material will be prepared once the build i…"), and EXEC-139's "will" linked to one of them.
The live suite runs the same set with ``tela-admin gold-run --tag noise --plan-only``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_gold_scoring_unit import db, snapshot  # noqa: F401 - pytest fixtures
from test_paraphrase_set_unit import evaluate
from test_planner_gold_unit import INDEX, ROWS, SNAP

from tela_api.knowledge import noise, scoring
from tela_api.knowledge.gold import pack_draft
from tela_api.knowledge.values import indexable, rows_for
from tela_api.packs.manifest import DEFAULT_PACKS_DIR
from tela_query_spec.model import ModelSnapshot

EXAMPLES = pack_draft()
NOISE = [e for e in EXAMPLES if "noise" in (e.get("tags") or [])]
# The hold reasons of the dev Dataverse source (EXEC-139), cut at 50 characters as its extract is.
DATAVERSE_HOLD_REASONS = [
    "Delivery checkpoints were agreed with the workstream owners", "Guided Data", "Joint Analytics",
    "Mitigation steps are in place and residual exposure is considered low. Wee",
    "Resource allocation was rebalanced to cover the integration effort. The be",
    "Training material will be prepared once the build i",
    "User acceptance preparation is underway with business repres", "Wide Onboarding"]
FIXTURE_VALUES = {d.key: sorted({str(r[d.field]) for r in ROWS[d.entity] if r.get(d.field) is not None})
                  for d in indexable(SNAP)}
# The fixture's index with the source's free-text hold reasons added, built as a refresh builds it.
REAL_INDEX = rows_for(SNAP, {**FIXTURE_VALUES, "hold_reason": [*FIXTURE_VALUES["hold_reason"],
                                                               *DATAVERSE_HOLD_REASONS]},
                      {(r.dimension, r.stored): list(r.synonyms) for r in INDEX if r.synonyms})
# Baseline before US-213 to US-219 (2026-11-05, EXEC-140): 184 of 278 right (wrong 24, clarified 55, refused 15),
# 21 confident wrong — 12 of them "… will be …" and EXEC-139's own question. After US-213/214 (grammar words, free-text
# fields): 240 right, 9 confident wrong (7 typos; "active project" and EXEC-139, US-216's). Later stories raise the
# floor and lower the ceiling; never the other way. After US-216/217 (names across word order, guessed filters ask):
# 242 right, 6 confident wrong (all typos; EXEC-139 and "How many active project do we have" now right).
RIGHT_FLOOR = 242
CONFIDENT_WRONG_CEILING = 6


def test_noise_set_is_the_generators_output() -> None:
    """The pack holds exactly what ``noise.noise_examples`` makes of its originals (regenerate, don't edit)."""
    base = [e for e in EXAMPLES if "noise" not in (e.get("tags") or [])]
    assert NOISE == noise.noise_examples(base)
    text = (Path(DEFAULT_PACKS_DIR) / "pmo" / "gold_examples.yaml").read_text()
    assert text.count(noise.MARKER) == 1 and text.rstrip().split(noise.MARKER)[0].count("tags: [noise") == 0
    kinds = Counter(t for e in NOISE for t in e["tags"] if t in noise.KINDS)
    assert set(kinds) == set(noise.KINDS) and min(kinds.values()) >= 10, kinds
    for e in NOISE:
        assert e["held_out"] is True, e["question"]  # never an AG-03 few-shot
        if "exec139" in e["tags"]:
            continue
        orig = next(o for o in base if o["question"] == e["noise_of"])
        assert e["spec"] == orig["spec"] and not e["spec"].get("filters"), e["question"]
        assert e["question"].lower() != orig["question"].lower().rstrip("?")


def test_variants_are_deterministic() -> None:
    assert noise.variants("How many projects are in delivery?") == [
        ("polite", "Kindly tell me how many projects are in delivery"),
        ("filler", "I would like to know how many projects are in delivery"),
        ("number_of", "How many number of projects are in delivery"),
        ("future", "How many projects will be in delivery"),
        ("singular", "How many project are in delivery"),
        ("typo", "How many proejcts are in delivery")]
    assert dict(noise.variants("Open projects by region"))["future"] == "Open projects by region going forward"
    assert "number_of" not in dict(noise.variants("Open projects by region"))


@pytest.fixture(scope="module")
def results(db: duckdb.DuckDBPyConnection, snapshot: ModelSnapshot) -> list[dict[str, Any]]:  # noqa: F811
    return [evaluate(db, snapshot, e, index=REAL_INDEX) for e in NOISE]


def test_noise_set_is_scored(results: list[dict[str, Any]]) -> None:
    s = scoring.summarise(results)
    n = s["by_set"]["noise"]
    by_kind = {k: scoring.group_scores([r for r in results if k in r["tags"]]) for k in (*noise.KINDS, "exec139")}
    print(f"\nnoise {n['passed']}/{n['total']} · outcomes {n['outcomes']} · confident wrong {n['confident_wrong']}")
    for k, g in by_kind.items():
        print(f"  {k}: {g['passed']}/{g['total']} · outcomes {g['outcomes']} · confident wrong {g['confident_wrong']}")
    wrong = [r["question"] for r in results if r["confident_wrong"]]
    print("  confident wrong:", wrong)
    assert n["total"] == len(NOISE) and sum(n["outcomes"].values()) == n["total"]
    assert n["passed"] >= RIGHT_FLOOR
    assert n["confident_wrong"] <= CONFIDENT_WRONG_CEILING
