"""Multi-connection routing for Ask (US-116; AG-01; REQ-033, REQ-034).

With several approved sources the question goes to the best match, in this order:

1. the source the person named ("…on MySQL", "in the workbook", a distinctive word of its name);
2. among sources whose certified measures match, the one this conversation already used (follow-ups stay on
   the same data);
3. the single source with the strongest measure match;
4. otherwise the person is asked to choose (every candidate is listed, with its id, so the UI offers buttons).

Sources come from the database on every turn, so a connection is answerable as soon as its model is approved,
without a restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from tela_api.ask.planner import match_measures, words

ENGINE_WORDS = {
    "postgres": {"postgres", "postgresql"}, "mysql": {"mysql"}, "sqlserver": {"sql server", "sqlserver", "mssql"},
    "mongodb": {"mongo", "mongodb"}, "excel": {"excel", "workbook", "spreadsheet", "xlsx"}, "csv": {"csv"},
    "dataverse": {"dataverse", "dynamics", "crm"},
}
GENERIC = {"sample", "tpg", "dev", "data", "source", "the", "and", "for", "with", "database", "connection", "fake",
           "web", "api", "tds", "pmo", "extract", "projects", "project", "test", "prod", "production", "copy"}


# US-242 (DEC-28): AG-01 reviews the rules' intent and source; it can't overturn a write refusal (never sent).
ROUTE_SYSTEM = (
    "You are AG-01, Tela's router. A question asks for data (a figure, a list or a chart), a definition (what a "
    "measure means or how it is counted), a report or dashboard to build, a new field (a grouping or bands), or is "
    "out of scope. The draft gives the intent the rules read and, with several sources, the source they chose and "
    "why; the context lists every source with the measures the question matched and whether the question names it. "
    "In a replacement give the intent, the source by its connection_id from the context, and for a definition the "
    "term in the question it asks about."
)
MAX_SOURCES = 10
ASKABLE = ("definition", "build")  # what a "data" reading is asked about rather than kept (US-242)


class RouteOut(BaseModel):
    intent: Literal["data", "definition", "build", "field", "out_of_scope"]
    connection_id: str | None = None
    subject: str | None = Field(default=None, max_length=120)


@dataclass
class RouteCheck:
    """What AG-01's review asks the person (US-242): the intents to choose between (``data`` first) or the other
    source to offer beside the rules' choice. Empty when the review is off, confirms or falls back."""

    verdict: str = "off"
    rules: Route | None = None
    intents: list[str] = field(default_factory=list)
    subject: str | None = None
    source: Any | None = None
    reason: str = ""


@dataclass
class Route:
    chosen: Any | None  # a Source
    candidates: list[Any]
    reason: str  # named | conversation | best_match | only | ambiguous


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def mentions(question: str, connection: dict[str, Any]) -> bool:
    q = question.lower()
    qw = _words(question)
    if any(w in q if " " in w else w in qw for w in ENGINE_WORDS.get(str(connection.get("engine")), set())):
        return True
    distinctive = {w for w in _words(str(connection.get("name") or "")) if len(w) >= 3 and w not in GENERIC}
    return bool(distinctive & qw)


def evidence(question: str, src: Any) -> dict[str, Any]:
    """One source as AG-01's review sees it: the measures the question matched and whether it names the source."""
    hits = sorted(((s, m) for s, m in match_measures(question, src.measures) if s > 0), key=lambda x: -x[0])
    return {"connection_id": str(src.connection["id"]), "name": src.connection["name"],
            "engine": src.connection.get("engine"), "named": mentions(question, src.connection),
            "measures": [m.label for _, m in hits[:3]]}


def route_draft(intent: Any, rules: Route | None) -> dict[str, Any]:
    """The rules' intent and source in plain words, the draft AG-01 reviews."""
    draft: dict[str, Any] = {"intent": intent.kind}
    if intent.subject:
        draft["subject"] = intent.subject
    if rules is not None:
        chosen = rules.chosen
        draft.update(source=chosen.connection["name"] if chosen else "ask which source",
                     connection_id=str(chosen.connection["id"]) if chosen else None, routing=rules.reason)
    return draft


def route_errors(out: RouteOut, question: str, ids: set[str]) -> list[str]:
    """Why AG-01's reading can't be used: a source not in the context, or a term the question doesn't hold."""
    errors = []
    if out.connection_id is not None and out.connection_id not in ids:
        errors.append("a source that is not one of the candidates")
    if out.subject and not set(words(out.subject)) <= set(words(question)):
        errors.append("a term that is not in the question")
    return errors


def intent_choice(clar: dict[str, Any] | None) -> tuple[str, str | None] | None:
    """The intent the person picked when the turn resumes after an intent question (US-242), else None."""
    if not clar or clar.get("kind") != "intent" or not clar.get("answer"):
        return None
    option = next((o for o in clar.get("options", []) if o["id"] == clar["answer"]), None)
    if option is None:
        return None
    return str(option["value"]).split(":", 1)[1], clar.get("subject")


def intent_question(intents: list[str], subject: str | None, reason: str) -> dict[str, Any]:
    """Ask whether the question wants the figures, a definition or a report: the clarify card's shape."""
    labels = {"data": "Show the figures",
              "definition": f"Explain what {subject} means" if subject else "Explain how it is defined",
              "build": "Plan a report"}
    return {"key": "intent", "kind": "intent", "question": "Do you want the figures, or something else?",
            "why": [reason] if reason else [], "subject": subject,
            "options": [{"id": chr(97 + i), "label": labels[k], "value": f"intent:{k}"}
                        for i, k in enumerate(intents)]}


def route(question: str, sources: list[Any], previous_connection_id: str | None) -> Route:
    if len(sources) <= 1:
        return Route(sources[0] if sources else None, sources, "only")
    scored = [(max((s for s, _ in match_measures(question, src.measures)), default=0.0), src) for src in sources]
    matched = [x for x in scored if x[0] > 0]
    pool = [src for _, src in matched] or list(sources)
    named = [src for src in pool if mentions(question, src.connection)]
    if len(named) == 1:
        return Route(named[0], pool, "named")
    if named:
        pool = named
    previous = next((src for src in pool if str(src.connection["id"]) == str(previous_connection_id)), None)
    if previous is not None:
        return Route(previous, pool, "conversation")
    in_pool = [(score, src) for score, src in matched if src in pool]
    if in_pool:
        top = max(score for score, _ in in_pool)
        best = [src for score, src in in_pool if score == top]
        if len(best) == 1:
            return Route(best[0], pool, "best_match")
        pool = best
    if len(pool) == 1:
        return Route(pool[0], pool, "only")
    return Route(None, pool, "ambiguous")
