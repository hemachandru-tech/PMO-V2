"""Grounding and citation checks (US-103; TEST-094; technical_document §citation check).

An explanation built from retrieved knowledge is accepted only when every cited ID was supplied to the agent
and at least 70 % of its sentences cite something. Otherwise the caller uses deterministic text built from the
certified definitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from tela_common.scan import output_is_safe

COVERAGE = 0.70
_REF = re.compile(r"chunk:[0-9a-fA-F-]{8,64}")


class CitedSentence(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    cites: list[str] = Field(default_factory=list, max_length=5)


class ExplainOut(BaseModel):
    sentences: list[CitedSentence] = Field(min_length=1, max_length=6)


@dataclass(frozen=True)
class CitationCheck:
    ok: bool
    cited: tuple[str, ...]
    unknown: tuple[str, ...]
    coverage: float


def _refs(s: CitedSentence) -> list[str]:
    found = [c if c.startswith("chunk:") else f"chunk:{c}" for c in s.cites]
    found += _REF.findall(s.text)
    return list(dict.fromkeys(found))


def check(out: ExplainOut, supplied: list[str]) -> CitationCheck:
    allowed = set(supplied)
    cited: list[str] = []
    with_cite = 0
    for s in out.sentences:
        refs = _refs(s)
        cited.extend(refs)
        if refs:
            with_cite += 1
    unique = tuple(dict.fromkeys(cited))
    unknown = tuple(r for r in unique if r not in allowed)
    coverage = with_cite / len(out.sentences)
    safe = all(output_is_safe(s.text) for s in out.sentences)
    return CitationCheck(ok=not unknown and coverage >= COVERAGE and safe, cited=unique, unknown=unknown,
                         coverage=round(coverage, 3))


def strip_refs(text: str) -> str:
    return re.sub(r"\s*\[?" + _REF.pattern + r"\]?", "", text).strip()
