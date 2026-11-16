"""Reranker and lexical weighting, no database (US-174; FEAT-062).

The SQL side (weighted vector, word rarity over the real index, rerank through the API) is covered in
``test_knowledge_api.py``; here the pure parts: tsvector parsing, BM25 over field weights, the score floor, the
contextual header, and how retrieval treats a reranker that is off, down or answering.
"""

from __future__ import annotations

from typing import Any

from conftest import FakeGateway

from tela_api.knowledge.index import context_line, contextualise, embed_text
from tela_api.knowledge.retrieval import (
    LIMITED_NOTE,
    RERANK_NOTE,
    Retrieval,
    _rerank,
    bm25,
    parse_tsvector,
    top_reranked,
)


def test_parse_tsvector_reads_weights_per_position() -> None:
    raw = "'heatmap':1A,9C 'region':3B,12C,14 'it''s':5 'status'"
    assert parse_tsvector(raw) == {"heatmap": ["A", "C"], "region": ["B", "C", "D"], "it's": ["D"], "status": ["D"]}


def test_bm25_prefers_a_rare_word_in_the_title_over_a_common_word_repeated() -> None:
    """US-159's miss: "Heatmap of region by reported status" put Regional portfolio first because it repeats
    "region" and "status"; with word rarity and title weight the heatmap blueprint wins."""
    df = {"heatmap": 1, "region": 12, "report": 30, "status": 25}
    heatmap = {"heatmap": ["A", "B", "C"], "region": ["C"], "report": ["C"], "status": ["C"]}
    regional = {"region": ["A", "B", "C", "C", "C", "C"], "report": ["C", "C"], "status": ["C", "C", "C", "C"]}
    a = bm25(heatmap, 40, df, 40, 45.0)
    b = bm25(regional, 60, df, 40, 45.0)
    assert a > b
    # The same word counts more in the title (A) than in the text (C).
    assert bm25({"heatmap": ["A"]}, 40, df, 40, 45.0) > bm25({"heatmap": ["C"]}, 40, df, 40, 45.0)
    # A word in every chunk adds little; one the chunk lacks adds nothing.
    assert bm25({"x": ["C"]}, 40, {"x": 40}, 40, 45.0) < 0.1
    assert bm25({}, 40, df, 40, 45.0) == 0.0


def test_top_reranked_keeps_the_best_above_the_floor() -> None:
    keys = ["a", "b", "c", "d", "e"]
    scores = {"a": 0.01, "b": 0.9, "c": 0.5, "d": 0.5, "e": 0.001}
    assert top_reranked(keys, scores, floor=0.02, k=8) == ["b", "c", "d"]  # fewer chunks rather than noise
    assert top_reranked(keys, scores, floor=0.02, k=2) == ["b", "c"]  # ties keep the fused order
    assert top_reranked(keys, scores, floor=0.95, k=8) == []


def test_contextual_header_names_kind_entity_term_and_document() -> None:
    meta = {"kind": "certified_measure", "entity": "projects", "entity_label": "Projects",
            "term": "Forecast cost (EAC)"}
    assert context_line("model_definition", meta, "Model definitions v3") == (
        "Certified measure · Projects · Forecast cost (EAC) — Model definitions v3")
    # An uploaded passage: its heading is the term; the document title closes the line.
    up = contextualise({"heading": "Go-live"}, "A go-live is…", kind="glossary", doc_title="PMO glossary")
    assert up["term"] == "Go-live" and up["ctx"] == 1
    assert up["context"] == "Glossary · Go-live — PMO glossary"
    # No heading: the document title is the term, and is not repeated.
    plain = contextualise({}, "Text", kind="uploaded_doc", doc_title="Delivery notes")
    assert plain["context"] == "Document · Delivery notes"
    assert embed_text("Text", plain) == "Document · Delivery notes\nText"
    assert embed_text("Text", {}) == "Text"


def _rows(*keys: str) -> dict[str, dict[str, Any]]:
    return {k: {"content": f"text {k}", "metadata": {"context": f"Dashboard rule · {k}"}} for k in keys}


async def test_rerank_off_down_and_answering() -> None:
    keys = ["a", "b"]
    off = FakeGateway()
    assert await _rerank(off.client(), "t", "q", keys, _rows(*keys)) == (keys, None, False)
    assert off.reranks == [{"query": "q", "texts": ["Dashboard rule · a\ntext a", "Dashboard rule · b\ntext b"]}]
    down = FakeGateway(rerank_fail=True)
    assert await _rerank(down.client(), "t", "q", keys, _rows(*keys)) == (keys, None, True)
    on = FakeGateway(reranker=lambda q, t: 0.9 if t.endswith("b") else 0.1)
    assert await _rerank(on.client(), "t", "q", keys, _rows(*keys)) == (keys, {"a": 0.1, "b": 0.9}, False)
    assert await _rerank(None, "t", "q", keys, _rows(*keys)) == (keys, None, False)
    assert await _rerank(on.client(), "t", "q", [], {}) == ([], None, False)


def test_notes_say_which_part_is_limited() -> None:
    assert Retrieval(hits=[]).note is None
    assert Retrieval(hits=[], limited=True).note == LIMITED_NOTE  # no embedding
    assert Retrieval(hits=[], limited=True, rerank_down=True, vector=[0.0]).note == RERANK_NOTE
    assert Retrieval(hits=[], limited=True, rerank_down=True).note == LIMITED_NOTE  # both: keywords only says more
