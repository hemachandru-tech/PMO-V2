"""Text normalisation and chunking for the knowledge index (US-100; technical_document §RAG).

The design asks for ≈ 800-token chunks with 120 tokens of overlap, while DATA-043 caps a chunk at 2,000
characters. Both are kept by measuring in characters: chunks of at most ``MAX_CHARS`` with the same 15 %
overlap ratio. Splits prefer paragraph, then sentence, then word boundaries; a heading is carried into the
chunks under it so each chunk reads on its own.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MAX_CHARS = 1900
OVERLAP_CHARS = 280

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile("[ \t\u00a0]+")
_BLANKS = re.compile(r"\n{3,}")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class Chunk:
    content: str
    metadata: dict[str, object] = field(default_factory=dict)


def normalise(text: str) -> str:
    """NFKC, no control characters, single spaces, at most one blank line in a row."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def _sections(text: str) -> list[tuple[str | None, str]]:
    """Split Markdown-style text at headings: (heading, body)."""
    out: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.split("\n"):
        m = _HEADING.match(line)
        if m:
            out.append((m.group(2).strip(), []))
        else:
            out[-1][1].append(line)
    return [(h, "\n".join(body).strip()) for h, body in out if "\n".join(body).strip() or h]


def _pieces(body: str, limit: int) -> list[str]:
    """Paragraphs, then sentences, then words, each no longer than ``limit``."""
    out: list[str] = []
    for para in (p.strip() for p in body.split("\n\n")):
        if not para:
            continue
        if len(para) <= limit:
            out.append(para)
            continue
        for sent in _SENTENCE.split(para):
            if len(sent) <= limit:
                out.append(sent)
                continue
            words, cur = sent.split(" "), ""
            for w in words:
                w = w[:limit]
                if cur and len(cur) + 1 + len(w) > limit:
                    out.append(cur)
                    cur = w
                else:
                    cur = f"{cur} {w}".strip()
            if cur:
                out.append(cur)
    return out


def _tail(text: str, size: int) -> str:
    """The last ``size`` characters, starting at a word boundary."""
    if len(text) <= size:
        return text
    cut = text[-size:]
    space = cut.find(" ")
    return cut[space + 1:] if 0 <= space < size // 2 else cut


def _meta(metadata: dict[str, object] | None, heading: str | None) -> dict[str, object]:
    return {**(metadata or {}), **({"heading": heading} if heading else {})}


def chunk_text(text: str, *, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS,
               metadata: dict[str, object] | None = None) -> list[Chunk]:
    """Chunk normalised text; every chunk is 1 to ``max_chars`` characters."""
    text = normalise(text)
    chunks: list[Chunk] = []
    for heading, body in _sections(text):
        prefix = f"{heading}\n" if heading else ""
        room = max_chars - len(prefix)
        current = ""
        for piece in _pieces(body, room - overlap - 1) if body else []:
            if current and len(current) + 2 + len(piece) > room:
                chunks.append(Chunk(prefix + current, _meta(metadata, heading)))
                current = _tail(current, overlap) + "\n" + piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
        if current:
            chunks.append(Chunk(prefix + current, _meta(metadata, heading)))
    return [c for c in chunks if c.content.strip()]
