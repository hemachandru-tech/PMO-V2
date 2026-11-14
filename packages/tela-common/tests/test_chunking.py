"""Chunking and PII redaction for the knowledge index (US-100)."""

from __future__ import annotations

from tela_common.chunking import MAX_CHARS, chunk_text, normalise
from tela_common.scan import redact_cards, redact_pii, scan_text


def test_normalise() -> None:
    assert normalise("a\u00a0 b\r\n\n\n\nc\x07") == "a b\n\nc"


def test_chunks_respect_the_limit_and_overlap() -> None:
    para = " ".join(f"Sentence {i} explains the forecast cost rule." for i in range(400))
    chunks = chunk_text(f"# Finance\n\n{para}")
    assert len(chunks) > 3 and all(1 <= len(c.content) <= MAX_CHARS for c in chunks)
    assert all(c.content.startswith("Finance\n") and c.metadata == {"heading": "Finance"} for c in chunks)
    # Consecutive chunks overlap, so a rule split across a boundary is still whole in one of them.
    tail = chunks[0].content[-120:].split(" ", 1)[1]
    assert tail[:40] in chunks[1].content


def test_long_words_and_sections() -> None:
    chunks = chunk_text("intro line\n\n## Two\n\n" + "x" * 5000, metadata={"page": 2})
    assert chunks[0].content == "intro line" and chunks[0].metadata == {"page": 2}
    assert all(len(c.content) <= MAX_CHARS for c in chunks) and chunks[-1].metadata["heading"] == "Two"
    assert chunk_text("   ") == []


def test_redact_pii() -> None:
    text, found = redact_pii("Mail a.b@example.com or call +971 4 555 0100.")
    assert found == ("email", "phone") and "example.com" not in text and scan_text(text).clean


def test_redact_cards_keeps_other_numbers() -> None:
    text, n = redact_cards("Card 4111 1111 1111 1111, budget 1234567890123.")
    assert n == 1 and "[card number removed]" in text and "1234567890123" in text
