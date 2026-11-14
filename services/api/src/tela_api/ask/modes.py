"""Ask answer mode per connection (ENH-028, US-256; FEAT-078 / FEAT-018; DEC-29, DEC-30).

One setting per source says how Ask answers questions about it (``conn.connections.ask_mode``):

* ``certified`` — the certified measures (today's planner). The default until the US-259 gate says otherwise.
* ``fields`` — Fields + definitions (Plan B, US-254): the question frame grounded on fields and approved conditions.
* ``hybrid`` — Plan B, using the certified measures as trusted shortcuts and checks (US-255).
* ``direct_sql`` — AG-10 writes SQL, the Custom SQL pipeline checks, scopes and runs it (DEC-30, US-257).

A mode can be chosen only when its planner is built (``BUILT``; US-254 and US-255 add theirs as they merge) and the
source suits it (Direct SQL: SQL engines only, and only while DEC-30 stands — ``ask_direct_sql``). ``planner_for``
is what the orchestrator runs: a stored mode whose planner is not (or no longer) available falls back to
``certified`` and says so, never to another mode's guess.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from tela_api.core.settings import get_settings
from tela_api.query import custom_sql
from tela_common.errors import TelaError

AskMode = Literal["certified", "fields", "hybrid", "direct_sql"]
MODES: tuple[str, ...] = get_args(AskMode)
DEFAULT: AskMode = "certified"
# Modes whose planner exists (US-254 Plan B is "fields", US-255 "hybrid").
BUILT: frozenset[str] = frozenset({"certified", "fields", "hybrid", "direct_sql"})

LABELS: dict[str, str] = {
    "certified": "Certified measures",
    "fields": "Fields + definitions",
    "hybrid": "Hybrid",
    "direct_sql": "Direct SQL",
}
NOTES: dict[str, str] = {
    "certified": "Answers only with measures an Admin has certified. Exact, but only what has a measure.",
    "fields": "Builds each answer from the fields and your approved business terms, so any mix of terms works.",
    "hybrid": "Like Fields + definitions, but uses a certified measure whenever the question matches one.",
    "direct_sql": "The model writes SQL, which is checked and scoped before it runs. Never certified; shows the SQL.",
}
MSG_NOT_BUILT = "This answer mode isn't available yet."
MSG_DIRECT_OFF = "Direct SQL is switched off for this installation."


def unavailable(mode: str, engine: str, descriptor: dict[str, Any] | None) -> str | None:
    """Why ``mode`` can't be chosen for this source, or None when it can."""
    if mode not in MODES:
        return "Unknown answer mode."
    if mode not in BUILT:
        return MSG_NOT_BUILT
    if mode == "direct_sql":
        if not get_settings().ask_direct_sql:
            return MSG_DIRECT_OFF
        try:
            custom_sql.dialect_for(engine, descriptor)
        except TelaError as exc:
            return f"Direct SQL needs a SQL source. {exc.message}"
    return None


def planner_for(mode: str | None, engine: str, descriptor: dict[str, Any] | None) -> tuple[str, str | None]:
    """The mode Ask runs for a source stored in ``mode``, and why it differs (None when it doesn't)."""
    stored = mode or DEFAULT
    why = unavailable(stored, engine, descriptor)
    return (stored, None) if why is None else (DEFAULT, f"{LABELS.get(stored, stored)}: {why} Answered with "
                                                        f"{LABELS[DEFAULT].lower()}.")


def choices(engine: str, descriptor: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [{"mode": m, "label": LABELS[m], "note": NOTES[m], "default": m == DEFAULT,
             "unavailable": unavailable(m, engine, descriptor)} for m in MODES]
