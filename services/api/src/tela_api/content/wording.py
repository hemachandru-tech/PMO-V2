"""Wording check for generated text (US-159; the PMO dashboard rules ``no_actual_spend`` and ``status_vs_health``).

Titles, summaries and suggestions Tela writes — a language model's headline or explanation, the advisor's
suggestions, a planned report's title — must not claim money already paid out when the data holds forecasts and
budgets only ("spend", "spent", "burn rate", "actual cost", "overspent"), must not mix the two status vocabularies
("reported health"), and must not call Not set or Insufficient data Green. The terms come from the packs'
``dashboard_rules.yaml`` (``wording``): the stored copies of the system packs once the API has read them at start
(``load_stored``, US-231), else the pack folders. A finance word is allowed when the model itself has a certified
measure whose label uses it (a source that really records actual spend).

Model text that fails is dropped for the deterministic text; a planned title is corrected (``fix``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.knowledge.guidance import RulesFile, Wording
from tela_api.packs.source import DEFAULT_PACKS_DIR, FilePackSource, PackSource, discover, released_sources
from tela_query_spec.model import ModelSnapshot


@dataclass(frozen=True)
class Issue:
    kind: str  # finance | status
    found: str
    use: str | None
    message: str


def combined(sources: Iterable[PackSource]) -> Wording:
    """Every pack's wording terms together (from each pack's dashboard rules)."""
    out = Wording()
    for src in sources:
        data = src.document("dashboard_rules")
        if data is not None:
            w = RulesFile.model_validate(data).wording
            out.forbidden += w.forbidden
            out.status.mixed += w.status.mixed
            out.status.unknown_as_good += w.status.unknown_as_good
            out.status.reported, out.status.calculated = w.status.reported, w.status.calculated
    return out


@lru_cache(maxsize=8)
def _wording(packs_dir: str) -> Wording:
    return combined(FilePackSource(m.parent) for m in discover(Path(packs_dir)))


_stored: Wording | None = None


async def load_stored(conn: AsyncConnection) -> Wording | None:
    """Read the wording of the released system packs from the database (API start); ``terms()`` then uses it.
    With no stored pack that has dashboard rules (packs not loaded yet), the folders stay in use."""
    global _stored
    sources = [s for s in await released_sources(conn, system_only=True) if s.document("dashboard_rules") is not None]
    _stored = combined(sources) if sources else None
    return _stored


def terms(packs_dir: str | Path | None = None) -> Wording:
    """The wording terms: the stored system packs' when read (``load_stored``), else the pack folders'. A folder
    given explicitly is always read."""
    if packs_dir is None and _stored is not None:
        return _stored
    return _wording(str(packs_dir or DEFAULT_PACKS_DIR))


def _phrase(p: str) -> re.Pattern[str]:
    return re.compile(r"\b" + r"[\s-]+".join(map(re.escape, p.lower().split())) + r"\b", re.I)


def allowed_from(snap: ModelSnapshot | None) -> set[str]:
    """Certified measure labels of the model, lower case: a forbidden word they use is the source's own word."""
    return {m.label.lower() for m in snap.measures if m.status == "certified"} if snap else set()


def issues(text_: str, *, allowed: Iterable[str] = (), packs_dir: str | Path | None = None) -> list[Issue]:
    w = terms(packs_dir)
    labels = list(allowed)
    out: list[Issue] = []
    for f in w.forbidden:
        for word in f.words:
            if _phrase(word).search(text_) and not any(_phrase(word).search(label) for label in labels):
                out.append(Issue("finance", word, f.use, f"“{word}” claims money already paid out; the data has "
                                 f"forecasts and budgets only — say “{f.use}”."))
    for mixed in w.status.mixed:
        if _phrase(mixed).search(text_):
            use = w.status.reported if mixed.lower().startswith("reported") else w.status.calculated
            out.append(Issue("status", mixed, use, f"“{mixed}” mixes reported status and calculated health — say "
                             f"“{use}”."))
    for pattern in w.status.unknown_as_good:
        if m := re.search(pattern, text_, re.I):
            out.append(Issue("status", m.group(0), None, "Not set and Insufficient data are never Green or on "
                             "track."))
    return out


def ok(text_: str, *, allowed: Iterable[str] = (), packs_dir: str | Path | None = None) -> bool:
    return not issues(text_, allowed=allowed, packs_dir=packs_dir)


def fix(text_: str, *, allowed: Iterable[str] = (), packs_dir: str | Path | None = None) -> str:
    """The text with each forbidden word replaced by the wording the rules give (for titles Tela writes)."""
    out = text_
    for issue in sorted(issues(text_, allowed=allowed, packs_dir=packs_dir), key=lambda i: -len(i.found)):
        if issue.use is None:
            continue
        pattern = _phrase(issue.found)

        def repl(m: re.Match[str], use: str = issue.use) -> str:
            return use[:1].upper() + use[1:] if m.group(0)[:1].isupper() else use.lower()

        out = pattern.sub(repl, out)
    return re.sub(r"\b(\w+) \1\b", r"\1", out, flags=re.I)  # "forecast forecast cost" → "forecast cost"
