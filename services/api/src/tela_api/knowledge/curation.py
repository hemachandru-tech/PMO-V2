"""Curation queue from Ask's misses (ENH-016, US-198; FEAT-066 / FEAT-014).

The questions Ask missed become proposals, so the semantic layer grows from real use:

* **Sources** (read once each, ``rag.curation_evidence``): refused questions (``unsupported_question``), clarification
  choices (a measure or reading the person picked; value choices already become value synonyms, US-173), plans AG-03
  repaired (``rag.plan_repairs``) and down-votes marked wrong or "not what I asked" (``chat.answer_feedback``).
* **Proposals** (``rag.curation_proposals``), one per connection, kind and wording, with the questions behind it:
  - a **measure synonym** for the words of a question the model doesn't know ("stalled projects" → Projects on
    hold), from a refusal (the Admin picks the measure among those the planner considered), a clarification (the
    measure chosen) or a repair (the measure name AG-03 tried, in the person's words, → the measure it settled on);
  - a **glossary term** for an unknown abbreviation in capitals ("RFQ"); the Admin writes what it means;
  - a **gold example** (question → the answered spec) from a repair, and from a down-vote, whose spec the Admin
    corrects first (``needs_fix``). A question answered after a clarification is already suggested as a gold example
    by Ask (US-182), and a question that is already a gold example is not proposed again.
* **Never a held-out question** (US-190): a held-out question (the pack's ``held_out`` examples, gold examples tagged
  ``paraphrase`` or imported held out) is skipped, so the set keeps measuring wording nobody tuned on (W2).
* **Nothing applies until an Admin approves it** (FEAT-014 review gate), and each kind through its own gate: a
  synonym changes the model and is approved as its next version (``model.approve``; the approval re-indexes the
  certified measures, so linking reads it by word and by meaning); a glossary term is written to the connection's
  approved "Glossary from Ask misses" document (read by the rewriter, free-hand and AG-03); a gold example is saved
  as approved through the gold endpoint, and the down-votes behind it are marked fixed. A dismissed wording is not
  proposed again.
* **A Direct SQL answer** (US-260): its "Save as gold example" adds a gold-example proposal with no spec, the turn
  and its SQL as evidence (source ``direct_sql``); the Admin writes the spec before approving it.

``POST /knowledge/curation/collect`` reads new misses; ``GET /knowledge/curation`` lists the queue with counts;
``POST /knowledge/curation/{id}/approve`` and ``/dismiss`` decide; ``POST /knowledge/curation/from-answer`` takes a
Direct SQL answer. Audit: ``knowledge.curation_approved``,
``knowledge.curation_dismissed``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask import feedback as answer_feedback
from tela_api.ask.planner import GRAINS, STOP, known_words, norm, words
from tela_api.audit import service as audit
from tela_api.content.common import notify, users_with_permission
from tela_api.content.exports import _rid
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge import gold
from tela_api.knowledge.index import replace_chunks
from tela_api.model.change import apply_change
from tela_api.model.store import current_version_id, load_snapshot
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import Measure, ModelSnapshot

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

MISSES = ("refused", "clarification", "repair", "feedback")  # what collect reads
SOURCES = (*MISSES, "direct_sql")  # plus a Direct SQL answer offered as a gold example (US-260)
KINDS = ("measure_synonym", "glossary_term", "gold_example")
MAX_PHRASE = 4  # words
PINNED = ("model_version_id", "connection_id", "spec_version")  # set by the server when a gold example is saved
GLOSSARY_TITLE = "Glossary from Ask misses"
ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,5}s?\b")
LIMIT = 500  # misses read per source and collect


@dataclass
class Proposal:
    kind: str  # one of KINDS
    key: str  # what makes two proposals the same: the phrase, the term or the question, normalised
    question: str
    phrase: str | None = None
    measure_ref: str | None = None
    meaning: str | None = None
    spec: dict[str, Any] | None = None
    options: list[str] = field(default_factory=list)
    needs_fix: bool = False


# --------------------------------------------------------------------------- proposals (no database)


def _nouns(snap: ModelSnapshot) -> set[str]:
    out = {w for e in snap.entities for w in words(e.label)}
    return out | {f"{w}s" for w in out} | {w.rstrip("s") for w in out}


def phrase_for(question: str, snap: ModelSnapshot, *, measure: Measure | None = None) -> str | None:
    """The words of the question the model doesn't know, as one phrase of at most ``MAX_PHRASE`` words with the
    table word that follows them ("stalled projects"); None when every word is known, or the phrase already names
    ``measure``."""
    ws = words(question)
    known = known_words(snap)
    unknown = [i for i, w in enumerate(ws) if w not in known and w not in STOP and w not in GRAINS
               and not w.isdigit() and len(w) > 2]
    if not unknown:
        return None
    lo = hi = unknown[0]
    for i in unknown[1:]:
        if i - lo >= MAX_PHRASE:
            break
        hi = i
    if hi + 1 < len(ws) and hi + 1 - lo < MAX_PHRASE and ws[hi + 1] in _nouns(snap):
        hi += 1
    phrase = " ".join(ws[lo:hi + 1])
    if measure is not None and phrase in {norm(n) for n in (measure.label, *measure.synonyms)}:
        return None
    return phrase


def acronym(question: str, phrase: str) -> str | None:
    """The abbreviation in capitals the phrase starts with ("RFQ" of "How many RFQ projects?"), else None."""
    first = phrase.split()[0]
    return next((m for m in ACRONYM.findall(question) if m.lower().rstrip("s") == first.rstrip("s")), None)


def core_spec(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: v for k, v in spec.items() if k not in PINNED} if spec else None


def _ref(snap: ModelSnapshot, raw: str | None) -> str | None:
    """``measure:key`` for a measure of the model (``raw`` may be a key or a ref), else None."""
    if not raw:
        return None
    key = str(raw).split(":", 1)[-1]
    return f"measure:{key}" if snap.measure(key) is not None else None


def _gold(question: str, spec: dict[str, Any] | None, *, needs_fix: bool = False) -> Proposal | None:
    core = core_spec(spec)
    if not core or not isinstance(core.get("measure"), dict):
        return None
    return Proposal("gold_example", norm(question), question, spec=core, needs_fix=needs_fix)


def from_refusal(question: str, snap: ModelSnapshot, error: dict[str, Any] | None,
                 considered: list[str]) -> list[Proposal]:
    """A refused question: its unknown words as a glossary term (an abbreviation) or as a measure synonym, with the
    measures the planner considered as options (the "did you mean" one chosen in advance)."""
    phrase = phrase_for(question, snap)
    if phrase is None:
        return []
    meant = _ref(snap, ((error or {}).get("did_you_mean") or {}).get("measure"))
    options = list(dict.fromkeys(r for r in [meant, *(_ref(snap, c) for c in considered)] if r))[:6]
    if (term := acronym(question, phrase)) is not None:
        return [Proposal("glossary_term", norm(term), question, phrase=term, options=options)]
    return [Proposal("measure_synonym", phrase, question, phrase=phrase, measure_ref=meant, options=options)]


def chosen_measure(clar: dict[str, Any] | None, spec: dict[str, Any] | None) -> str | None:
    """The measure the person picked in a clarification: the option's measure, the chosen reading's, or the spec's."""
    clar = clar or {}
    option = next((o for o in clar.get("options") or [] if o.get("id") == clar.get("answer")), None)
    if option is not None and option.get("measure"):
        return str(option["measure"])
    if option is not None and clar.get("kind") == "reading" and str(option.get("value", "")).startswith("reading:"):
        i = int(str(option["value"]).split(":", 1)[1])
        readings = clar.get("readings") or []
        if 0 <= i < len(readings):
            return str(readings[i]["measure"]["ref"])
    return str(((spec or {}).get("measure") or {}).get("ref") or "") or None


def from_clarification(question: str, snap: ModelSnapshot, clar: dict[str, Any] | None,
                       spec: dict[str, Any] | None) -> list[Proposal]:
    """A clarification choice: the question's unknown words as a synonym of the measure chosen. (The answered
    question is already suggested as a gold example by Ask itself, US-182.)"""
    ref = _ref(snap, chosen_measure(clar, spec))
    measure = snap.measure(ref.split(":", 1)[1]) if ref else None
    if ref is None or measure is None or (phrase := phrase_for(question, snap, measure=measure)) is None:
        return []
    return [Proposal("measure_synonym", phrase, question, phrase=phrase, measure_ref=ref, options=[ref])]


def from_repair(question: str, snap: ModelSnapshot, spec: dict[str, Any] | None,
                refs: list[tuple[str, str | None]]) -> list[Proposal]:
    """A plan AG-03 repaired: the question with the spec it answered as a gold example; a measure name AG-03 tried
    that is the person's own words ("live projects") as a synonym of the measure it settled on."""
    out = [p for p in [_gold(question, spec)] if p]
    ref = _ref(snap, ((spec or {}).get("measure") or {}).get("ref"))
    measure = snap.measure(ref.split(":", 1)[1]) if ref else None
    asked = set(words(question))
    for kind, tried in refs:
        if kind != "unknown_measure" or not tried or measure is None:
            continue
        phrase = norm(tried.split(":", 1)[-1].replace("_", " "))
        named = {norm(n) for m in snap.measures for n in (m.label, m.key.replace("_", " "), *m.synonyms)}
        if ref and phrase and set(phrase.split()) <= asked and phrase not in named:
            out.append(Proposal("measure_synonym", phrase, question, phrase=phrase, measure_ref=ref, options=[ref]))
    return out


def from_feedback(question: str, spec: dict[str, Any] | None) -> list[Proposal]:
    """A down-vote marked wrong or "not what I asked": a gold example whose spec the Admin corrects first."""
    return [p for p in [_gold(question, spec, needs_fix=True)] if p]


# --------------------------------------------------------------------------- collecting


async def held_out(conn: AsyncConnection, packs_dir: str | None) -> set[str]:
    """The held-out questions, normalised: the pack's ``held_out`` examples (US-190's paraphrases, US-173's aliases)
    and gold examples tagged ``paraphrase`` or imported held out (never a few-shot). They are never proposed (W2)."""
    out = {norm(str(ex["question"])) for ex in await gold.stored_pack_draft(conn, packs_dir) if ex.get("held_out")}
    rows = await fetch_all(conn, "SELECT question FROM rag.gold_examples WHERE 'paraphrase' = ANY(tags) OR "
                                 "(origin = 'pack_draft' AND NOT use_for_fewshot)")
    return out | {norm(r["question"]) for r in rows}


async def _snapshot(conn: AsyncConnection, tenant_id: str, connection_id: str,
                    cache: dict[str, ModelSnapshot | None]) -> ModelSnapshot | None:
    if connection_id not in cache:
        version = await current_version_id(conn, connection_id)
        cache[connection_id] = await load_snapshot(conn, tenant_id, version) if version else None
    return cache[connection_id]


# Each source: new misses (not yet read) of the last ``:d`` days, on one connection or all (``:c``), oldest first.
_REFUSED = """
    SELECT t.id, t.question, t.connection_id, t.error, t.created_at,
           (SELECT e.data FROM chat.turn_events e WHERE e.turn_id = t.id AND e.event = 'retrieval'
            ORDER BY e.id DESC LIMIT 1) AS retrieval
    FROM chat.turns t
    WHERE t.status = 'error' AND t.error->>'code' = 'unsupported_question' AND t.connection_id IS NOT NULL
      AND t.created_at >= now() - make_interval(days => :d)
      AND (CAST(:c AS uuid) IS NULL OR t.connection_id = CAST(:c AS uuid))
      AND NOT EXISTS (SELECT 1 FROM rag.curation_evidence e WHERE e.source = 'refused' AND e.source_id = t.id)
    ORDER BY t.created_at LIMIT :n
"""
_CLARIFIED = """
    SELECT t.id, t.question, t.connection_id, t.clarification, t.spec, t.created_at
    FROM chat.turns t
    WHERE t.status = 'complete' AND t.clarification ? 'answer' AND coalesce(t.clarification->>'kind', '') <> 'value'
      AND t.connection_id IS NOT NULL AND t.created_at >= now() - make_interval(days => :d)
      AND (CAST(:c AS uuid) IS NULL OR t.connection_id = CAST(:c AS uuid))
      AND NOT EXISTS (SELECT 1 FROM rag.curation_evidence e WHERE e.source = 'clarification' AND e.source_id = t.id)
    ORDER BY t.created_at LIMIT :n
"""
_REPAIRED = """
    SELECT t.id, t.question, t.connection_id, t.spec, min(r.created_at) AS created_at,
           jsonb_agg(jsonb_build_array(r.kind, r.ref) ORDER BY r.attempt) AS refs
    FROM rag.plan_repairs r JOIN chat.turns t ON t.id = r.turn_id
    WHERE t.status = 'complete' AND t.spec IS NOT NULL AND r.created_at >= now() - make_interval(days => :d)
      AND (CAST(:c AS uuid) IS NULL OR r.connection_id = CAST(:c AS uuid))
      AND NOT EXISTS (SELECT 1 FROM rag.curation_evidence e WHERE e.source = 'repair' AND e.source_id = t.id)
    GROUP BY t.id HAVING bool_or(r.repaired)
    ORDER BY min(r.created_at) LIMIT :n
"""
_DOWNVOTED = """
    SELECT f.id, f.turn_id, f.question, f.connection_id, f.spec, f.reason, f.comment, f.created_at
    FROM chat.answer_feedback f
    WHERE f.vote = 'down' AND f.status = 'open' AND f.reason IN ('wrong', 'not_what_i_asked')
      AND f.connection_id IS NOT NULL AND f.created_at >= now() - make_interval(days => :d)
      AND (CAST(:c AS uuid) IS NULL OR f.connection_id = CAST(:c AS uuid))
      AND NOT EXISTS (SELECT 1 FROM rag.curation_evidence e WHERE e.source = 'feedback' AND e.source_id = f.id)
    ORDER BY f.created_at LIMIT :n
"""


async def _misses(conn: AsyncConnection, connection_id: str | None, days: int) -> list[dict[str, Any]]:
    """New misses of every source: source, source_id, turn_id, question, connection_id and what each source needs."""
    p = {"d": days, "c": connection_id, "n": LIMIT}
    out: list[dict[str, Any]] = []
    for r in await fetch_all(conn, _REFUSED, p):
        out.append({"source": "refused", "source_id": r["id"], "turn_id": r["id"], **dict(r),
                    "considered": list((r["retrieval"] or {}).get("measures_considered") or [])})
    out += [{"source": "clarification", "source_id": r["id"], "turn_id": r["id"], **dict(r)}
            for r in await fetch_all(conn, _CLARIFIED, p)]
    out += [{"source": "repair", "source_id": r["id"], "turn_id": r["id"], **dict(r)}
            for r in await fetch_all(conn, _REPAIRED, p)]
    out += [{"source": "feedback", "source_id": r["id"], **dict(r)} for r in await fetch_all(conn, _DOWNVOTED, p)]
    return out


def proposals_for(miss: dict[str, Any], snap: ModelSnapshot) -> list[Proposal]:
    q = miss["question"]
    if miss["source"] == "refused":
        return from_refusal(q, snap, miss.get("error"), miss.get("considered") or [])
    if miss["source"] == "clarification":
        return from_clarification(q, snap, miss.get("clarification"), miss.get("spec"))
    if miss["source"] == "repair":
        return from_repair(q, snap, miss.get("spec"), [(k, r) for k, r in miss.get("refs") or []])
    return from_feedback(q, miss.get("spec"))


def _detail(miss: dict[str, Any]) -> str | None:
    if miss["source"] == "feedback":
        why = {"wrong": "Marked wrong", "not_what_i_asked": "Not what I asked"}[miss["reason"]]
        return f"{why}: “{miss['comment']}”"[:500] if miss.get("comment") else why
    if miss["source"] == "repair":
        return ", ".join(sorted({str(k) for k, _ in miss.get("refs") or []}))[:500] or None
    if miss["source"] == "clarification":
        clar = miss.get("clarification") or {}
        option = next((o for o in clar.get("options") or [] if o.get("id") == clar.get("answer")), None)
        return f"Chose {option['label']}"[:500] if option else None
    return (miss.get("error") or {}).get("message")


async def collect(conn: AsyncConnection, tenant_id: str, *, connection_id: str | None, days: int,
                  packs_dir: str | None) -> dict[str, Any]:
    """Read new misses into the queue. Returns the counts: new proposals, misses read per source, held-out
    questions skipped."""
    skip = await held_out(conn, packs_dir)
    golds = {(str(r["connection_id"]), norm(r["question"])) for r in await fetch_all(
        conn, "SELECT connection_id, question FROM rag.gold_examples WHERE status <> 'rejected'")}
    cache: dict[str, ModelSnapshot | None] = {}
    added, read, held = 0, dict.fromkeys(MISSES, 0), 0
    for miss in await _misses(conn, connection_id, days):
        if norm(miss["question"]) in skip:
            held += 1
            continue
        cid = str(miss["connection_id"])
        snap = await _snapshot(conn, tenant_id, cid, cache)
        if snap is None:
            continue
        read[miss["source"]] += 1
        for p in proposals_for(miss, snap):
            if p.kind == "gold_example" and (cid, p.key) in golds:
                continue  # already a gold example: nothing to learn
            row = await fetch_one(
                conn, "INSERT INTO rag.curation_proposals AS p (id, tenant_id, connection_id, kind, key, question, "
                      "phrase, measure_ref, meaning, spec, options, needs_fix) VALUES (:id, :t, CAST(:c AS uuid), "
                      ":k, :key, :q, :ph, :m, :mean, CAST(:spec AS jsonb), CAST(:o AS jsonb), :fix) "
                      "ON CONFLICT (tenant_id, connection_id, kind, key) DO UPDATE SET "
                      "measure_ref = coalesce(p.measure_ref, EXCLUDED.measure_ref), "
                      "spec = coalesce(p.spec, EXCLUDED.spec), needs_fix = p.needs_fix OR EXCLUDED.needs_fix, "
                      "options = CASE WHEN p.options = '[]' THEN EXCLUDED.options ELSE p.options END "
                      "RETURNING id, (xmax = 0) AS inserted",
                {"id": new_id(), "t": tenant_id, "c": cid, "k": p.kind, "key": p.key[:500], "q": p.question,
                 "ph": p.phrase, "m": p.measure_ref, "mean": p.meaning,
                 "spec": json.dumps(p.spec) if p.spec is not None else None, "o": json.dumps(p.options),
                 "fix": p.needs_fix})
            assert row is not None
            added += int(bool(row["inserted"]))
            await conn.execute(
                text("INSERT INTO rag.curation_evidence (id, tenant_id, proposal_id, source, source_id, turn_id, "
                     "question, detail, created_at) VALUES (:id, :t, :p, :s, :sid, CAST(:turn AS uuid), :q, :d, :at) "
                     "ON CONFLICT DO NOTHING"),
                {"id": new_id(), "t": tenant_id, "p": row["id"], "s": miss["source"], "sid": miss["source_id"],
                 "turn": str(miss["turn_id"]) if miss.get("turn_id") else None, "q": miss["question"],
                 "d": _detail(miss), "at": miss["created_at"]})
    return {"added": added, "read": read, "held_out_skipped": held}


# --------------------------------------------------------------------------- API


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


_SELECT = """
    SELECT p.*, c.name AS connection_name, r.display_name AS reviewed_by_name,
           (SELECT count(*) FROM rag.curation_evidence e WHERE e.proposal_id = p.id) AS seen,
           (SELECT jsonb_object_agg(s.source, s.n) FROM (SELECT e.source, count(*) AS n FROM rag.curation_evidence e
             WHERE e.proposal_id = p.id GROUP BY e.source) s) AS by_source,
           (SELECT jsonb_agg(jsonb_build_object('question', x.question, 'source', x.source, 'detail', x.detail,
                                                'turn_id', x.turn_id, 'at', x.created_at) ORDER BY x.created_at DESC)
              FROM (SELECT * FROM rag.curation_evidence e WHERE e.proposal_id = p.id ORDER BY e.created_at DESC
                    LIMIT 5) x) AS questions
    FROM rag.curation_proposals p
    JOIN conn.connections c ON c.id = p.connection_id
    LEFT JOIN iam.users r ON r.id = p.reviewed_by
"""


def _label(snap: ModelSnapshot | None, ref: str | None) -> str | None:
    m = snap.measure(ref.split(":", 1)[1]) if snap is not None and ref else None
    return m.label if m is not None else None


def proposal_out(row: dict[str, Any], snap: ModelSnapshot | None) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "kind": row["kind"], "status": row["status"],
        "connection": {"id": str(row["connection_id"]), "name": row["connection_name"]},
        "question": row["question"], "phrase": row["phrase"], "meaning": row["meaning"],
        "measure": {"ref": row["measure_ref"], "label": _label(snap, row["measure_ref"])} if row["measure_ref"]
        else None,
        "options": [{"ref": r, "label": _label(snap, r) or r.split(":", 1)[-1]} for r in row["options"] or []],
        "spec": row["spec"], "needs_fix": row["needs_fix"], "applied": row["applied"],
        "seen": int(row["seen"]), "by_source": row["by_source"] or {},
        "questions": [{**q, "turn_id": str(q["turn_id"]) if q.get("turn_id") else None}
                      for q in row["questions"] or []],
        "reviewed_by": {"id": str(row["reviewed_by"]), "display_name": row["reviewed_by_name"]}
        if row["reviewed_by"] else None,
        "reviewed_at": _iso(row["reviewed_at"]), "review_note": row["review_note"],
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
    }


async def _proposal(conn: AsyncConnection, proposal_id: str, *, lock: bool = False) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, _SELECT + " WHERE p.id = CAST(:id AS uuid)" + (" FOR UPDATE OF p" if lock else ""),
                              {"id": proposal_id})
    except Exception:  # noqa: BLE001 - a malformed id is not found
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That proposal was not found.")
    return dict(row)


class CollectIn(BaseModel):
    connection_id: str | None = Field(default=None, max_length=64)
    days: int = Field(default=30, ge=1, le=365)


@router.post("/curation/collect")
async def collect_misses(body: CollectIn, request: Request,
                         principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return await collect(conn, principal.tenant_id, connection_id=body.connection_id, days=body.days,
                             packs_dir=request.app.state.settings.packs_dir)


@router.get("/curation")
async def list_proposals(status: Literal["pending", "approved", "dismissed", "all"] = Query(default="pending"),
                         kind: Literal["measure_synonym", "glossary_term", "gold_example"] | None = Query(default=None),
                         connection_id: str | None = Query(default=None, max_length=64),
                         principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, _SELECT + " WHERE (:s = 'all' OR p.status = :s) AND (CAST(:k AS text) IS NULL OR p.kind = :k) AND "
                            "(CAST(:c AS uuid) IS NULL OR p.connection_id = CAST(:c AS uuid)) "
                            "ORDER BY (p.status = 'pending') DESC, seen DESC, p.updated_at DESC LIMIT 500",
            {"s": status, "k": kind, "c": connection_id})
        counts = await fetch_all(conn, "SELECT status, kind, count(*) AS n FROM rag.curation_proposals "
                                       "GROUP BY status, kind")
        sources = await fetch_all(conn, "SELECT e.source, count(DISTINCT e.source_id) AS n FROM "
                                        "rag.curation_evidence e JOIN rag.curation_proposals p ON p.id = "
                                        "e.proposal_id WHERE p.status = 'pending' GROUP BY e.source")
        cache: dict[str, ModelSnapshot | None] = {}
        items = [proposal_out(dict(r), await _snapshot(conn, principal.tenant_id, str(r["connection_id"]), cache))
                 for r in rows]
    by_status = {s: sum(int(r["n"]) for r in counts if r["status"] == s) for s in ("pending", "approved", "dismissed")}
    return {"items": items, "counts": {
        **by_status,
        "pending_by_kind": {k: sum(int(r["n"]) for r in counts if r["status"] == "pending" and r["kind"] == k)
                            for k in KINDS},
        "pending_by_source": {s: next((int(r["n"]) for r in sources if r["source"] == s), 0) for s in SOURCES}}}


class ApproveIn(BaseModel):
    phrase: str | None = Field(default=None, max_length=100)  # the synonym or glossary term, as edited
    measure: str | None = Field(default=None, max_length=200)  # the synonym's measure
    meaning: str | None = Field(default=None, max_length=200)  # the glossary term's meaning
    question: str | None = Field(default=None, min_length=3, max_length=500)  # the gold example, as edited
    expected_spec: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=500)


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def add_synonym(definition: dict[str, Any], key: str, phrase: str) -> dict[str, Any]:
    """The model definition with ``phrase`` added to the measure's synonyms; refused when any measure is already
    named that way."""
    wanted = norm(phrase)
    for m in definition.get("measures", []):
        if wanted in {norm(n) for n in (m["label"], m["key"].replace("_", " "), *(m.get("synonyms") or []))}:
            same = m["key"] == key
            raise TelaError(ErrorCode.CONFLICT, f"“{phrase}” already names {m['label']}"
                            + ("." if same else ": pick another wording or dismiss this proposal."))
    if not any(m["key"] == key for m in definition.get("measures", [])):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick a certified measure of the approved model.",
                        {"field": "measure"})
    return {**definition, "measures": [{**m, "synonyms": [*(m.get("synonyms") or []), phrase]} if m["key"] == key
                                       else m for m in definition["measures"]]}


async def write_glossary(conn: AsyncConnection, gateway: Any, principal: Principal, connection_id: str) -> str:
    """(Re-)write the connection's "Glossary from Ask misses" document from its approved glossary proposals, approved
    (an Admin approved each term) and indexed, so the rewriter, free-hand and AG-03 read the terms."""
    rows = await fetch_all(conn, "SELECT phrase, meaning FROM rag.curation_proposals WHERE connection_id = "
                                 "CAST(:c AS uuid) AND kind = 'glossary_term' AND status = 'approved' AND "
                                 "meaning IS NOT NULL ORDER BY reviewed_at, phrase", {"c": connection_id})
    chunks = [{"content": f"{r['phrase']} means: {r['meaning']}.",
               "metadata": {"kind": "glossary", "term": r["phrase"], "fields": [], "curated": True}} for r in rows]
    digest = hashlib.sha256(repr([c["content"] for c in chunks]).encode()).hexdigest()
    doc = await fetch_one(conn, "SELECT id FROM rag.knowledge_documents WHERE connection_id = CAST(:c AS uuid) AND "
                                "source_kind = 'glossary' AND model_version_id IS NULL AND object_key IS NULL AND "
                                "title = :title", {"c": connection_id, "title": GLOSSARY_TITLE})
    doc_id = str(doc["id"]) if doc else new_id()
    if doc is None:
        await conn.execute(
            text("INSERT INTO rag.knowledge_documents (id, tenant_id, connection_id, source_kind, title, content_hash, "
                 "review_status, index_status, reviewed_by, reviewed_at, created_by) VALUES (:id, :t, CAST(:c AS "
                 "uuid), 'glossary', :title, :h, 'approved', 'indexing', CAST(:u AS uuid), now(), CAST(:u AS uuid))"),
            {"id": doc_id, "t": principal.tenant_id, "c": connection_id, "title": GLOSSARY_TITLE, "h": digest,
             "u": principal.user_id})
    stored, report, model = await replace_chunks(conn, gateway, principal.tenant_id, doc_id, chunks)
    await conn.execute(
        text("UPDATE rag.knowledge_documents SET content_hash = :h, review_status = 'approved', reviewed_by = "
             "CAST(:u AS uuid), reviewed_at = now(), index_status = 'indexed', index_error = NULL, scan_flags = "
             "CAST(:f AS jsonb), chunk_count = :n, embedding_model = :m, indexed_at = now() WHERE id = :id"),
        {"h": digest, "u": principal.user_id, "f": json.dumps(report.as_json()), "n": stored, "m": model,
         "id": doc_id})
    return doc_id


async def _approve_synonym(request: Request, principal: Principal, p: dict[str, Any],
                           body: ApproveIn) -> dict[str, Any]:
    phrase = _clean(body.phrase) or p["phrase"]
    ref = body.measure or p["measure_ref"]
    if not ref:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Pick the measure these words mean.", {"field": "measure"})
    if not 2 <= len(phrase) <= 60:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "A synonym is 2 to 60 characters.", {"field": "phrase"})
    key = ref.split(":", 1)[-1]
    out = await apply_change(request, principal, str(p["connection_id"]), lambda d: add_synonym(d, key, phrase),
                             note=f"Synonym “{phrase}” for measure:{key} (from Ask misses)",
                             what="approve this synonym", denied="A measure synonym changes the model, which needs "
                                                                 "model approval rights.")
    return {"model_version_id": out["model_version_id"], "version": out["version"], "measure": f"measure:{key}",
            "phrase": phrase}


async def _approve_gold(request: Request, principal: Principal, p: dict[str, Any], body: ApproveIn) -> dict[str, Any]:
    question = _clean(body.question) or p["question"]
    if p["needs_fix"] and body.expected_spec is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Someone marked this answer wrong: correct the spec first.",
                        {"field": "expected_spec"})
    spec = core_spec(body.expected_spec if body.expected_spec is not None else p["spec"])
    if not spec:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Give the spec this question should be planned as.",
                        {"field": "expected_spec"})
    async with tenant_tx(principal.tenant_id) as conn:
        if norm(question) in await held_out(conn, request.app.state.settings.packs_dir):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This is a held-out evaluation question: it can't become a "
                            "gold example.", {"field": "question"})
        turn = await fetch_one(conn, "SELECT e.turn_id FROM rag.curation_evidence e JOIN chat.turns t ON t.id = "
                                     "e.turn_id WHERE e.proposal_id = :p ORDER BY e.created_at LIMIT 1",
                               {"p": p["id"]})
        fbs = [str(r["source_id"]) for r in await fetch_all(
            conn, "SELECT e.source_id FROM rag.curation_evidence e JOIN chat.answer_feedback f ON f.id = e.source_id "
                  "WHERE e.proposal_id = :p AND e.source = 'feedback' AND f.status = 'open'", {"p": p["id"]})]
    payload = gold.GoldIn(question=question, expected_spec=spec, connection_id=str(p["connection_id"]),
                          tags=["from_misses"], turn_id=str(turn["turn_id"]) if turn else None)
    try:
        gold_id = (await gold.create_gold(payload, request, principal))["id"]
    except TelaError as exc:
        if exc.code != ErrorCode.CONFLICT or not (exc.details or {}).get("gold_example_id"):
            raise
        gold_id = exc.details["gold_example_id"]  # the question is a gold example already: link it
    for fid in fbs:  # the down-votes behind it are fixed by this gold example
        try:
            fixed = answer_feedback.ResolveIn(status="fixed", gold_example_id=gold_id)
            await answer_feedback.resolve_feedback(fid, fixed, request, principal)
        except TelaError as exc:
            if exc.code != ErrorCode.CONFLICT:
                raise
    return {"gold_example_id": gold_id, "feedback_fixed": len(fbs)}


@router.post("/curation/{proposal_id}/approve")
async def approve(proposal_id: str, body: ApproveIn, request: Request,
                  principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _proposal(conn, proposal_id)
    if p["status"] != "pending":
        raise TelaError(ErrorCode.CONFLICT, f"This proposal was already {p['status']}.")
    if p["kind"] == "measure_synonym":
        applied = await _approve_synonym(request, principal, p, body)
    elif p["kind"] == "gold_example":
        applied = await _approve_gold(request, principal, p, body)
    else:
        term, meaning = _clean(body.phrase) or p["phrase"], _clean(body.meaning) or p["meaning"]
        if not meaning:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Write what the term means.", {"field": "meaning"})
        applied = {"term": term, "meaning": meaning}
    async with tenant_tx(principal.tenant_id) as conn:
        done = await fetch_one(
            conn, "UPDATE rag.curation_proposals SET status = 'approved', phrase = coalesce(:ph, phrase), meaning = "
                  "coalesce(:mean, meaning), measure_ref = coalesce(:m, measure_ref), applied = CAST(:a AS jsonb), "
                  "reviewed_by = CAST(:u AS uuid), reviewed_at = now(), review_note = :note WHERE id = CAST(:id AS "
                  "uuid) AND status = 'pending' RETURNING id",
            {"ph": applied.get("phrase") or applied.get("term"), "mean": applied.get("meaning"),
             "m": applied.get("measure"), "a": json.dumps(applied), "u": principal.user_id,
             "note": _clean(body.note) or None, "id": proposal_id})
        if done is None:
            raise TelaError(ErrorCode.CONFLICT, "This proposal was decided meanwhile.")
        if p["kind"] == "glossary_term":
            applied["document_id"] = await write_glossary(conn, request.app.state.gateway, principal,
                                                          str(p["connection_id"]))
            await conn.execute(text("UPDATE rag.curation_proposals SET applied = CAST(:a AS jsonb) WHERE id = :id"),
                               {"a": json.dumps(applied), "id": p["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.curation_approved",
                           object_type="curation_proposal", object_id=proposal_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"kind": p["kind"], **{k: v for k, v in applied.items() if k != "meaning"}})
        cache: dict[str, ModelSnapshot | None] = {}
        row = await _proposal(conn, proposal_id)
        return proposal_out(row, await _snapshot(conn, principal.tenant_id, str(row["connection_id"]), cache))


class FromAnswerIn(BaseModel):
    turn_id: str = Field(min_length=1, max_length=64)


@router.post("/curation/from-answer", status_code=201)
async def from_answer(body: FromAnswerIn, request: Request,
                      principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    """"Save as gold example" on a Direct SQL answer (US-260). The answer has SQL, not a Query Spec, so it can't be a
    gold example as it stands: it becomes a gold-example proposal with no spec, its evidence the turn and the SQL the
    model wrote. An Admin writes the spec in the queue and approves it through the usual gold gate. Asking again for
    the same question adds the turn as more evidence; a question already a gold example or already reviewed is a
    conflict."""
    admin = principal.has(Perm.KNOWLEDGE_MANAGE)
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            turn = await fetch_one(conn, "SELECT id, user_id, question, connection_id, status, ask_mode FROM "
                                         "chat.turns WHERE id = CAST(:id AS uuid)", {"id": body.turn_id})
        except Exception:  # noqa: BLE001 - a malformed id is not found
            turn = None
        if turn is None or (str(turn["user_id"]) != principal.user_id and not admin):
            raise TelaError(ErrorCode.NOT_FOUND, "That answer was not found.")
        if turn["status"] != "complete" or turn["ask_mode"] != "direct_sql" or turn["connection_id"] is None:
            raise TelaError(ErrorCode.CONFLICT, "Only a finished Direct SQL answer goes to the curation queue.")
        event = await fetch_one(conn, "SELECT data FROM chat.turn_events WHERE turn_id = CAST(:t AS uuid) AND "
                                      "event = 'complete' ORDER BY id DESC LIMIT 1", {"t": body.turn_id})
        sql = str(((event or {}).get("data") or {}).get("sql") or "")
        question = " ".join(str(turn["question"]).removeprefix("Undo: ").split())[:2000]
        key, cid = norm(question)[:500], str(turn["connection_id"])
        if not key:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This question has no words to keep as a gold example.")
        if key in await held_out(conn, request.app.state.settings.packs_dir):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This is a held-out evaluation question: it can't become a "
                            "gold example.")
        golds = await fetch_all(conn, "SELECT id, question FROM rag.gold_examples WHERE connection_id = CAST(:c AS "
                                      "uuid) AND status <> 'rejected'", {"c": cid})
        dup = next((g for g in golds if norm(g["question"]) == key), None)
        if dup is not None:
            raise TelaError(ErrorCode.CONFLICT, "There is already a gold example with this question.",
                            {"gold_example_id": str(dup["id"])})
        row = await fetch_one(
            conn, "INSERT INTO rag.curation_proposals AS p (id, tenant_id, connection_id, kind, key, question) "
                  "VALUES (:id, :t, CAST(:c AS uuid), 'gold_example', :key, :q) ON CONFLICT (tenant_id, "
                  "connection_id, kind, key) DO UPDATE SET updated_at = now() RETURNING id, status, "
                  "(xmax = 0) AS inserted",
            {"id": new_id(), "t": principal.tenant_id, "c": cid, "key": key, "q": question})
        assert row is not None
        if row["status"] != "pending":
            raise TelaError(ErrorCode.CONFLICT, f"An Admin already {row['status']} this question as a gold example.")
        await conn.execute(
            text("INSERT INTO rag.curation_evidence (id, tenant_id, proposal_id, source, source_id, turn_id, "
                 "question, detail) VALUES (:id, :t, :p, 'direct_sql', CAST(:turn AS uuid), CAST(:turn AS uuid), :q, "
                 ":d) ON CONFLICT DO NOTHING"),
            {"id": new_id(), "t": principal.tenant_id, "p": row["id"], "turn": body.turn_id, "q": question,
             "d": f"SQL: {sql}"[:500] if sql else None})
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.suggested",
                           object_type="curation_proposal", object_id=str(row["id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"question": question, "ask_mode": "direct_sql", "turn_id": body.turn_id})
        if row["inserted"]:
            admins = [u for u in await users_with_permission(conn, Perm.KNOWLEDGE_MANAGE.value)
                      if u != principal.user_id]
            await notify(conn, principal.tenant_id, admins, kind="gold_example.suggested",
                              title="Gold example suggested",
                              body=f"{principal.display_name} suggested “{question[:200]}” from a Direct SQL answer.",
                              link="/admin/knowledge/curation", by=principal.user_id)
    return {"proposal_id": str(row["id"]), "status": "pending",
            "message": "Sent to Admin → Knowledge → From misses: an Admin writes its spec to make it a gold example."}


class DismissIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/curation/{proposal_id}/dismiss")
async def dismiss(proposal_id: str, body: DismissIn, request: Request,
                  principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        p = await _proposal(conn, proposal_id, lock=True)
        if p["status"] != "pending":
            raise TelaError(ErrorCode.CONFLICT, f"This proposal was already {p['status']}.")
        await conn.execute(text("UPDATE rag.curation_proposals SET status = 'dismissed', reviewed_by = CAST(:u AS "
                                "uuid), reviewed_at = now(), review_note = :note WHERE id = :id"),
                           {"u": principal.user_id, "note": _clean(body.note) or None, "id": p["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.curation_dismissed",
                           object_type="curation_proposal", object_id=proposal_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request), details={"kind": p["kind"]})
        cache: dict[str, ModelSnapshot | None] = {}
        row = await _proposal(conn, proposal_id)
        return proposal_out(row, await _snapshot(conn, principal.tenant_id, str(row["connection_id"]), cache))
