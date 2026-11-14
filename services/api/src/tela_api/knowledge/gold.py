"""Gold examples and the ground-truth evaluation suite (US-056, US-057; API-050; DATA-044; TEST-017, TEST-092).

A gold example is a verified question → Query Spec pair on one connection. The spec is validated against the
connection's current approved model when saved. Creators suggest examples from an answer; Admins add, approve,
edit and retire them. Approved examples are AG-03 few-shot context (by question similarity) and the suite.

An evaluation run plans every approved question through the same planner Ask uses (rules first, then AG-03),
compares the planned spec with the expected one (only the parts a person would notice: measures, breakdowns,
time, filters, comparison, and visual or limit when the example sets them) and, when they differ, runs both:
the example passes when the rows are the same (execution accuracy, US-184), since two specs can give the same
right answer. Each failure gets an error class, and retrieval is scored (reciprocal rank, nDCG@8, recall per
kind, value linking) by ``knowledge/scoring.py``. A multi-turn example plans its first message and applies the
later ones as refinements in words, as Ask does. When an example has a result fingerprint, the answer is run under
its test-scope role. Runs are queued (worker ``index`` queue) and reported by tag; a pass rate below the tenant
threshold warns the Admins but blocks nothing (FEAT-015).

US-190: each result also records the planner's status and confidence, a **confident-wrong** answer (wrong at or
above the confidence threshold), whether a clarification offered the right measure, required-item recall (linking
candidates and retrieval, also when the plan is refused) and planning latency; runs report them per planner path
and per set (original questions, held-out paraphrases). A **plan-only** run fetches no result rows (a live source):
an example passes on spec match alone.

A run can take longer than any HTTP timeout (150 questions, two queries for each spec that differs), so the worker's
call only claims it (``queued`` → ``running``, once) and the API runs it as a background task. A shutdown marks the
runs in progress as interrupted; a run left ``running`` by a crash fails after ``STALE_AFTER``.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.common import canonical, notify, principal_for_role, users_with_permission
from tela_api.content.exports import _rid, require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal, principal_for_user
from tela_api.knowledge import scoring
from tela_api.knowledge.index import embed
from tela_api.model.store import load_snapshot
from tela_api.packs.manifest import DEFAULT_PACKS_DIR
from tela_api.packs.source import FilePackSource, system_source
from tela_api.query.service import QueryContext, QueryService
from tela_api.query.validator import SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.scan import scan_text
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import QuerySpec

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
internal = APIRouter(prefix="/api/v1/internal/knowledge", tags=["internal"], include_in_schema=False)

DEFAULT_THRESHOLD = 0.80  # DEC-11 working position; tenants change it in rag.policies
FEWSHOTS = 5
FEWSHOT_POOL = 20  # most similar approved examples the five are chosen from
SHAPE_WEIGHT = 6.0  # a same-shape example may rank this many places below a merely similar one
# Shapes a question needs an example of (US-175): "top 5 regions by …" sees a top-N example.
STRONG_SHAPES = ("top_n", "trend", "compare", "two_breakdowns", "derived", "share")
SPEC_KEYS = ("measure", "measures", "extra_measures", "derived", "share_of_total", "breakdowns", "time", "filters",
             "compare", "sort", "limit", "visual", "personal_fields", "personal_measures")
ALWAYS = ("measure", "extra_measures", "derived", "share_of_total", "breakdowns", "time", "filters", "compare")
OPTIONAL = ("visual", "limit", "sort")  # compared only when the example sets them
SHAPE_KEYS = ("top_n_per_group", "running_total", "moving_avg", "rank", "bucket", "percentile", "having", "exists_in",
              "cohort")
STALE_AFTER = timedelta(hours=4)  # a run still ``running`` after this was lost by a crash
MSG_INTERRUPTED = "The evaluation was interrupted because the service restarted. Run it again."
MSG_STALE = "The evaluation stopped without finishing (the service may have restarted). Run it again."


# --------------------------------------------------------------------------- specs


def core_spec(raw: dict[str, Any]) -> dict[str, Any]:
    """The comparable part of a spec, parsed and dumped through QuerySpec so equivalent specs look the same."""
    data = {k: v for k, v in raw.items() if k in SPEC_KEYS}
    parsed = QuerySpec.model_validate({"model_version_id": "x", "connection_id": "x", **data})
    dumped = parsed.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
    dumped = {k: v for k, v in dumped.items() if k in SPEC_KEYS}
    dumped["measure"] = parsed.measure.model_dump(mode="json", exclude_none=True)
    return dumped


def _norm(key: str, value: Any) -> Any:
    if key == "filters":
        return sorted(({**f, "op": scoring.filter_op(f), "values": sorted(map(str, f.get("values", [])))}
                       for f in value or []),
                      key=lambda f: (f["ref"], f["op"], json.dumps(f["values"])))
    if key in ("breakdowns", "extra_measures"):
        return sorted(value or [], key=lambda b: json.dumps(b, sort_keys=True))
    if key == "derived":  # the column name is the planner's choice; the figure is op, of and to (US-176)
        return sorted((d["op"], d["of"], d["to"]) for d in value or [])
    if key == "share_of_total":
        return sorted(value or [])
    return value


def comparable(spec: dict[str, Any]) -> str:
    """One string per meaning of a spec (sorted keys, the core the gold diff compares, plus limit and sort): equal
    for two plans that would give the same answer. Voting compares AG-03's samples with it (US-182)."""
    try:
        core = core_spec(spec)
    except ValueError:  # not a valid spec: compared as written
        return json.dumps(spec, sort_keys=True, default=str)
    out = {k: _norm(k, core.get(k)) for k in (*ALWAYS, *OPTIONAL) if core.get(k)}
    out.update({k: spec[k] for k in SHAPE_KEYS if spec.get(k) is not None})  # window ops, links (US-177, US-178)
    if spec.get("uses"):  # a multi-step plan (US-179): its steps and what it takes of them
        out["uses"] = sorted(json.dumps(u, sort_keys=True) for u in spec["uses"])
        out["steps"] = [comparable(s) for s in spec.get("steps") or []]
    return json.dumps(out, sort_keys=True, default=str)


def spec_diff(expected: dict[str, Any], actual: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Differences between the expected spec and the planned one; empty means pass."""
    if actual is None:
        return [{"path": "spec", "expected": "a planned question", "actual": None}]
    exp, act = core_spec(expected), core_spec(actual)
    diff = []
    for key in (*ALWAYS, *(k for k in OPTIONAL if k in exp)):
        e, a = _norm(key, exp.get(key)), _norm(key, act.get(key))
        if (e or None) != (a or None):
            diff.append({"path": key, "expected": e, "actual": a})
    return diff


def validate_against(snap: ModelSnapshot, spec: dict[str, Any]) -> dict[str, Any]:
    """Raise SPEC_INVALID unless the spec is valid on this model version; return its comparable core."""
    try:
        core = core_spec(spec)
        qs = QuerySpec.model_validate({**core, "model_version_id": snap.model_version_id,
                                       "connection_id": snap.connection_id})
    except ValueError as exc:
        raise TelaError(ErrorCode.SPEC_INVALID, "The expected spec is not a valid Query Spec.",
                        {"reason": str(exc)[:300]}) from exc
    SpecValidator(snap).validate(qs)
    return core


def planner_example(spec: dict[str, Any]) -> dict[str, Any]:
    """A gold spec in AG-03's output shape, for few-shot context."""
    out: dict[str, Any] = {"measure": spec["measure"]["ref"]}
    if spec.get("breakdowns"):
        out["breakdowns"] = [b["ref"] for b in spec["breakdowns"]]
    if spec.get("filters"):
        out["filters"] = [{"ref": f["ref"], "op": f["op"], "values": f.get("values", [])} for f in spec["filters"]]
    if (spec.get("time") or {}).get("grain"):
        out["time_grain"] = spec["time"]["grain"]
    if spec.get("limit"):
        out["limit"] = spec["limit"]
    return out


def spec_shape(spec: dict[str, Any]) -> set[str]:
    """The analytic shape of a spec: top_n, breakdown, two_breakdowns, trend, range, filter, compare, derived (a
    ratio, difference or percentage of two measures), share (of the total) — or total."""
    out: set[str] = set()
    if spec.get("derived"):
        out.add("derived")
    if spec.get("share_of_total"):
        out.add("share")
    if spec.get("limit"):
        out.add("top_n")
    if spec.get("breakdowns"):
        out.add("breakdown")
        if len(spec["breakdowns"]) > 1:
            out.add("two_breakdowns")
    if (spec.get("time") or {}).get("grain"):
        out.add("trend")
    if (spec.get("time") or {}).get("range"):
        out.add("range")
    if spec.get("filters"):
        out.add("filter")
    if spec.get("compare"):
        out.add("compare")
    return out or {"total"}


def question_shape(question: str) -> set[str]:
    """The shape a question asks for, read from its words (the same names as ``spec_shape``)."""
    q = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    out: set[str] = set()
    if re.search(r"\b(top|bottom|highest|lowest|largest|smallest|first)\s+\d{1,4}\b", q):
        out.add("top_n")
    breakdowns = re.findall(r"\b(?:by|per|for each|across|split by|grouped by)\s+(?!(?:day|week|month|quarter|year)\b)"
                            r"[a-z]", q)
    if breakdowns or re.match(r"^(which|what)\s+[a-z]+\s+(have|has|are|is|with)\b", q):
        out.add("breakdown")
    if len(breakdowns) > 1 or re.search(r"\bby\s+[a-z ]+?\s+and\s+[a-z]", q):
        out.add("two_breakdowns")
    if re.search(r"\b(trend|over time|monthly|weekly|quarterly|by (day|week|month|quarter|year))\b", q):
        out.add("trend")
    if re.search(r"\b(last|this|next)\s+(\d+\s+)?(days?|weeks?|months?|quarters?|years?)\b", q):
        out.add("range")
    if re.search(r"\b(compare|compared|vs|versus|previous period)\b", q):
        out.add("compare")
    if re.search(r"\b(ratio|as a (percent|percentage) of|difference between|minus)\b", q) or "% of " in question:
        out.add("derived")
    if re.search(r"\b(share of (the )?total|(percent|percentage|proportion) of (the )?total|as a share)\b", q):
        out.add("share")
    return out or {"total"}


def pick_shots(question: str, candidates: list[dict[str, Any]], k: int = FEWSHOTS) -> list[dict[str, Any]]:
    """``k`` few-shots from candidates in similarity order, preferring the question's shape: each candidate's rank
    is lowered by the share of the question's shape it shares; a strong shape the question needs (top N, trend,
    compare, two breakdowns) gets at least one example when a candidate has it."""
    want = question_shape(question)
    shapes = [spec_shape(c["spec"]) for c in candidates]
    order = sorted(range(len(candidates)),
                   key=lambda i: (i - SHAPE_WEIGHT * len(want & shapes[i]) / len(want), i))
    picked = order[:k]
    for shape in (s for s in STRONG_SHAPES if s in want):
        if any(shape in shapes[i] for i in picked):
            continue
        extra = next((i for i in order[k:] if shape in shapes[i]), None)
        if extra is not None and picked:
            swap = next((i for i in reversed(picked) if not (shapes[i] & want & set(STRONG_SHAPES))), picked[-1])
            picked[picked.index(swap)] = extra
    return [candidates[i] for i in sorted(picked, key=order.index)]


async def fewshots(conn: AsyncConnection, connection_id: str, question: str, vector: list[float] | None,
                   visible_measures: set[str], k: int = FEWSHOTS) -> list[dict[str, Any]]:
    """The approved examples most like the question and of its shape (US-175), limited to measures the person can
    see."""
    if vector is not None:
        rows = await fetch_all(conn, "SELECT id, question, expected_spec FROM rag.gold_examples WHERE status = "
                                     "'approved' AND use_for_fewshot AND connection_id = CAST(:c AS uuid) AND "
                                     "embedding IS NOT NULL ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n",
                               {"c": connection_id, "v": json.dumps(vector), "n": FEWSHOT_POOL})
    else:
        rows = await fetch_all(conn, "SELECT id, question, expected_spec FROM rag.gold_examples WHERE status = "
                                     "'approved' AND use_for_fewshot AND connection_id = CAST(:c AS uuid) ORDER BY "
                                     "ts_rank_cd(to_tsvector('english', question), plainto_tsquery('english', :q)) "
                                     "DESC, created_at LIMIT :n", {"c": connection_id, "q": question,
                                                                   "n": FEWSHOT_POOL})
    candidates = [{"id": str(r["id"]), "question": r["question"], "spec": r["expected_spec"]} for r in rows
                  if r["expected_spec"]["measure"]["ref"].split(":", 1)[1] in visible_measures
                  and r["question"].strip().lower() != question.strip().lower()]
    return [{"id": c["id"], "question": c["question"], "plan": planner_example(c["spec"])}
            for c in pick_shots(question, candidates, k)]


def fewshot_block(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    lines = ["Verified examples (question → plan):"]
    lines += [f"- {json.dumps(e['question'])} → {json.dumps(e['plan'], sort_keys=True)}" for e in examples]
    return "\n".join(lines)


# --------------------------------------------------------------------------- helpers


async def _current_snapshot(conn: AsyncConnection, tenant_id: str, connection_id: str) -> ModelSnapshot:
    try:
        row = await fetch_one(conn, "SELECT m.current_version_id FROM model.data_models m JOIN conn.connections c ON "
                                    "c.id = m.connection_id WHERE m.connection_id = CAST(:c AS uuid) AND "
                                    "c.deleted_at IS NULL", {"c": connection_id})
    except Exception:  # noqa: BLE001 - malformed id
        row = None
    if row is None or row["current_version_id"] is None:
        raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "That connection has no approved model.")
    return await load_snapshot(conn, tenant_id, str(row["current_version_id"]))


async def _embed_question(request: Request, tenant_id: str, question: str) -> tuple[str | None, str | None]:
    if not scan_text(question).clean:
        return None, None
    vectors, model = await embed(request.app.state.gateway, tenant_id, [question])
    return (json.dumps(vectors[0]) if vectors else None), model


# --------------------------------------------------------------------------- knowledge from use (US-175)


async def suggest_example(conn: AsyncConnection, gateway: Any, principal: Principal, *, connection_id: str,
                          snap: ModelSnapshot, question: str, spec: dict[str, Any], origin: str,
                          turn_id: str | None = None, notes: str | None = None) -> str | None:
    """A gold example suggested by use — a question answered after a clarification, or a free-hand question whose
    rule was certified — waiting for Admin review like a Creator's "Save as gold example" (FEAT-014). None when the
    spec is not a certified one on this model, or the question is already a gold example."""
    question = " ".join(question.split())[:500]
    if len(question) < 3 or (spec.get("measure") or {}).get("adhoc"):
        return None
    try:
        core = validate_against(snap, spec)
    except (TelaError, ValueError):
        return None
    dup = await fetch_one(conn, "SELECT id FROM rag.gold_examples WHERE connection_id = CAST(:c AS uuid) AND "
                                "lower(question) = lower(:q) AND status <> 'rejected'",
                          {"c": connection_id, "q": question})
    if dup is not None:
        return None
    vec, model = None, None
    if scan_text(question).clean:
        vectors, model = await embed(gateway, principal.tenant_id, [question])
        vec = json.dumps(vectors[0]) if vectors else None
    gold_id = str(new_id())
    await conn.execute(
        text("INSERT INTO rag.gold_examples (id, tenant_id, connection_id, model_version_id, question, expected_spec, "
             "status, origin, source_turn_id, notes, embedding, embedding_model, suggested_by) VALUES (:id, :t, "
             "CAST(:c AS uuid), :mv, :q, CAST(:spec AS jsonb), 'suggested', :origin, CAST(:turn AS uuid), :notes, "
             "CAST(:v AS vector), :em, :u)"),
        {"id": gold_id, "t": principal.tenant_id, "c": connection_id, "mv": snap.model_version_id, "q": question,
         "spec": json.dumps(core), "origin": origin, "turn": turn_id, "notes": notes, "v": vec, "em": model,
         "u": principal.user_id})
    await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.suggested",
                       object_type="gold_example", object_id=gold_id, outcome="success", actor_id=principal.user_id,
                       correlation_id=None, details={"question": question, "measure": core["measure"]["ref"],
                                                     "origin": origin})
    admins = [u for u in await users_with_permission(conn, Perm.KNOWLEDGE_MANAGE.value) if u != principal.user_id]
    await notify(conn, principal.tenant_id, admins, kind="gold_example.suggested", title="Gold example suggested",
                 body=f"“{question}” was suggested from {ORIGIN_WORDS.get(origin, 'use')}.",
                 link="/admin/knowledge/gold?status=suggested", by=principal.user_id)
    return gold_id


ORIGIN_WORDS = {"clarification": "a clarified question", "freehand": "a certified free-hand question"}


async def answer_synonyms(conn: AsyncConnection, gateway: Any, principal: Principal, example: dict[str, Any],
                          snap: ModelSnapshot) -> int:
    """An approved answer whose question named a filter value its own way ("Saudi" for KSA, "Jordon" for Jordan)
    proposes those words as a value synonym, pending Admin review (US-175). Returns how many were proposed."""
    from tela_api.knowledge.values import indexable, link_question, propose_synonym

    wanted = {f["ref"].split(":", 1)[1]: {str(v) for v in f.get("values") or []}
              for f in example["expected_spec"].get("filters") or []
              if f["ref"].startswith("dimension:") and f.get("op", "=") in ("=", "in")}
    dims = [d for d in indexable(snap) if d.key in wanted]
    if not dims:
        return 0
    linking = await link_question(conn, gateway, principal.tenant_id, example["question"], snap,
                                  str(example["connection_id"]), dims)
    proposed = 0
    for v in linking.links:
        if v.how in ("partial", "spelling", "meaning") and v.stored in wanted.get(v.dimension, set()):
            proposed += await propose_synonym(conn, principal, str(example["connection_id"]), v.dimension, v.stored,
                                              v.phrase, source="answer")
    return proposed


async def _answer_synonyms(conn: AsyncConnection, request: Request, principal: Principal, example: dict[str, Any],
                           snap: ModelSnapshot) -> None:
    """``answer_synonyms`` on approval; a failure never blocks the approval itself."""
    try:
        async with conn.begin_nested():
            await answer_synonyms(conn, getattr(request.app.state, "gateway", None), principal, example, snap)
    except Exception as exc:  # noqa: BLE001 - synonyms are a by-product of the review
        log.warning("gold.answer_synonyms_failed", error_class=type(exc).__name__)


async def threshold(conn: AsyncConnection) -> float:
    row = await fetch_one(conn, "SELECT eval_threshold FROM rag.policies")
    return float(row["eval_threshold"]) if row else DEFAULT_THRESHOLD


GOLD_COLUMNS = ("g.id, g.connection_id, g.model_version_id, g.question, g.turns, g.expected_spec, "
                "g.expected_result_fingerprint, g.test_scope_role_id, g.tags, g.use_for_fewshot, g.status, g.origin, "
                "g.source_turn_id, g.notes, g.embedding IS NOT NULL AS embedded, g.suggested_by, g.verified_by, "
                "g.verified_at, g.created_at, g.updated_at")


def _gold_out(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: (str(v) if v is not None and (k == "id" or k.endswith(("_id", "_by"))) else v) for k, v in r.items()}
    if isinstance(r.get("expected_spec"), dict):  # US-250: the kind of answer the example expects
        out["shape"] = scoring.answer_shape(r["expected_spec"])
    return out


async def _gold(conn: AsyncConnection, gold_id: str) -> dict[str, Any]:
    try:
        row = await fetch_one(conn, f"SELECT {GOLD_COLUMNS} FROM rag.gold_examples g WHERE g.id = CAST(:id AS uuid)",  # noqa: S608
                              {"id": gold_id})
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That gold example was not found.")
    return dict(row)


def _tags(tags: list[str]) -> list[str]:
    return sorted({t.strip().lower().replace(" ", "_") for t in tags if t.strip()})[:12]


# --------------------------------------------------------------------------- API-050: examples


class GoldIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    expected_spec: dict[str, Any]
    connection_id: str = Field(max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=12)
    use_for_fewshot: bool = True
    notes: str | None = Field(default=None, max_length=1000)
    test_scope_role_id: str | None = Field(default=None, max_length=64)
    expected_result_fingerprint: dict[str, Any] | None = None
    turn_id: str | None = Field(default=None, max_length=64)
    turns: list[str] = Field(default_factory=list, max_length=5)  # earlier messages of a multi-turn example

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("turns")
    @classmethod
    def _turns(cls, v: list[str]) -> list[str]:
        return _clean_turns(v)


def _clean_turns(turns: list[str]) -> list[str]:
    out = [" ".join(str(t).split()) for t in turns if str(t).strip()]
    if any(len(t) > 500 for t in out):
        raise ValueError("Each earlier message must be at most 500 characters.")
    return out


@router.get("/gold-examples")
async def list_gold(status: str | None = Query(default=None, max_length=20),
                    connection_id: str | None = Query(default=None, max_length=64),
                    principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, f"SELECT {GOLD_COLUMNS}, c.name AS connection_name, s.display_name AS suggested_by_name, "  # noqa: S608
                  "v.display_name AS verified_by_name, r.passed AS last_passed, r.diff AS last_diff "
                  "FROM rag.gold_examples g JOIN conn.connections c ON c.id = g.connection_id "
                  "LEFT JOIN iam.users s ON s.id = g.suggested_by LEFT JOIN iam.users v ON v.id = g.verified_by "
                  "LEFT JOIN LATERAL (SELECT e.passed, e.diff FROM rag.eval_results e JOIN rag.eval_runs er ON "
                  "er.id = e.run_id WHERE e.gold_example_id = g.id AND er.status = 'done' ORDER BY e.created_at "
                  "DESC LIMIT 1) r ON true WHERE (CAST(:s AS text) IS NULL OR g.status = :s) AND "
                  "(CAST(:c AS uuid) IS NULL OR g.connection_id = CAST(:c AS uuid)) ORDER BY g.created_at DESC "
                  "LIMIT 1000", {"s": status, "c": connection_id})
        th = await threshold(conn)
    return {"items": [_gold_out(dict(r)) for r in rows], "threshold": th}


@router.post("/gold-examples", status_code=201)
async def create_gold(body: GoldIn, request: Request,
                      principal: Principal = Depends(require(Perm.WIDGETS_CREATE))) -> dict[str, Any]:
    """Admins add approved examples; a Creator's "Save as gold example" from an answer is a suggestion."""
    admin = principal.has(Perm.KNOWLEDGE_MANAGE)
    async with tenant_tx(principal.tenant_id) as conn:
        snap = await _current_snapshot(conn, principal.tenant_id, body.connection_id)
        core = validate_against(snap, body.expected_spec)
        if body.expected_result_fingerprint is not None and not body.test_scope_role_id:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "A result checksum needs a named test scope role.")
        if body.turn_id:
            turn = await fetch_one(conn, "SELECT user_id FROM chat.turns WHERE id = CAST(:id AS uuid)",
                                   {"id": body.turn_id})
            if turn is None or (str(turn["user_id"]) != principal.user_id and not admin):
                raise TelaError(ErrorCode.NOT_FOUND, "That answer was not found.")
        dup = await fetch_one(conn, "SELECT id FROM rag.gold_examples WHERE connection_id = CAST(:c AS uuid) AND "
                                    "lower(question) = lower(:q) AND status <> 'rejected'",
                              {"c": body.connection_id, "q": body.question})
        if dup is not None:
            raise TelaError(ErrorCode.CONFLICT, "There is already a gold example with this question.",
                            {"gold_example_id": str(dup["id"])})
    vec, model = await _embed_question(request, principal.tenant_id, body.question)
    gold_id = new_id()
    status = "approved" if admin else "suggested"
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO rag.gold_examples (id, tenant_id, connection_id, model_version_id, question, "
                 "expected_spec, expected_result_fingerprint, test_scope_role_id, tags, use_for_fewshot, status, "
                 "origin, source_turn_id, notes, embedding, embedding_model, suggested_by, verified_by, verified_at, "
                 "turns) VALUES (:id, :t, :c, :mv, :q, CAST(:spec AS jsonb), CAST(:fp AS jsonb), CAST(:role AS uuid), "
                 ":tags, :fs, :st, :origin, CAST(:turn AS uuid), :notes, CAST(:v AS vector), :em, :u, "
                 "CASE WHEN :admin THEN CAST(:u AS uuid) END, CASE WHEN :admin THEN now() END, :turns)"),
            {"id": gold_id, "t": principal.tenant_id, "c": body.connection_id, "mv": snap.model_version_id,
             "q": body.question, "spec": json.dumps(core), "fp": json.dumps(body.expected_result_fingerprint)
             if body.expected_result_fingerprint is not None else None, "role": body.test_scope_role_id,
             "tags": _tags(body.tags), "fs": body.use_for_fewshot, "st": status,
             "origin": "answer" if body.turn_id else "manual", "turn": body.turn_id, "notes": body.notes,
             "v": vec, "em": model, "u": principal.user_id, "admin": admin, "turns": body.turns})
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.created" if admin else
                           "gold_example.suggested", object_type="gold_example", object_id=gold_id,
                           outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"question": body.question, "measure": core["measure"]["ref"]})
        if not admin:
            admins = [u for u in await users_with_permission(conn, Perm.KNOWLEDGE_MANAGE.value)
                      if u != principal.user_id]
            await notify(conn, principal.tenant_id, admins, kind="gold_example.suggested",
                         title="Gold example suggested", body=f"{principal.display_name} suggested “{body.question}”.",
                         link="/admin/knowledge/gold?status=suggested", by=principal.user_id)
        elif body.turn_id:  # an Admin's own answer is approved at once: its words for values become synonyms too
            await _answer_synonyms(conn, request, principal, {"question": body.question, "expected_spec": core,
                                                              "connection_id": body.connection_id}, snap)
        return _gold_out(await _gold(conn, gold_id))


class GoldPatch(BaseModel):
    question: str | None = Field(default=None, min_length=3, max_length=500)
    expected_spec: dict[str, Any] | None = None
    tags: list[str] | None = Field(default=None, max_length=12)
    use_for_fewshot: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)
    test_scope_role_id: str | None = Field(default=None, max_length=64)
    expected_result_fingerprint: dict[str, Any] | None = None
    turns: list[str] | None = Field(default=None, max_length=5)

    @field_validator("turns")
    @classmethod
    def _turns(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_turns(v)


@router.patch("/gold-examples/{gold_id}")
async def update_gold(gold_id: str, body: GoldPatch, request: Request,
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    async with tenant_tx(principal.tenant_id) as conn:
        g = await _gold(conn, gold_id)
        sets: list[str] = []
        params: dict[str, Any] = {"id": gold_id}
        if "expected_spec" in fields and fields["expected_spec"] is not None:
            snap = await _current_snapshot(conn, principal.tenant_id, g["connection_id"])
            sets += ["expected_spec = CAST(:spec AS jsonb)", "model_version_id = :mv"]
            params |= {"spec": json.dumps(validate_against(snap, fields["expected_spec"])),
                       "mv": snap.model_version_id}
        if fields.get("question"):
            q = " ".join(fields["question"].split())
            sets += ["question = :q", "embedding = NULL", "embedding_model = NULL"]
            params["q"] = q
        if "tags" in fields and fields["tags"] is not None:
            sets.append("tags = :tags")
            params["tags"] = _tags(fields["tags"])
        if fields.get("turns") is not None:
            sets.append("turns = :turns")
            params["turns"] = fields["turns"]
        for key in ("use_for_fewshot", "notes"):
            if key in fields:
                sets.append(f"{key} = :{key}")
                params[key] = fields[key]
        if "test_scope_role_id" in fields or "expected_result_fingerprint" in fields:
            role = fields.get("test_scope_role_id", g["test_scope_role_id"])
            fp = fields.get("expected_result_fingerprint", g["expected_result_fingerprint"])
            if fp is not None and not role:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "A result checksum needs a named test scope role.")
            sets += ["test_scope_role_id = CAST(:role AS uuid)", "expected_result_fingerprint = CAST(:fp AS jsonb)"]
            params |= {"role": role, "fp": json.dumps(fp) if fp is not None else None}
        if not sets:
            return _gold_out(g)
        await conn.execute(text(f"UPDATE rag.gold_examples SET {', '.join(sets)} WHERE id = :id"), params)  # noqa: S608
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.updated",
                           object_type="gold_example", object_id=gold_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"fields": sorted(fields)})
    if fields.get("question"):
        vec, model = await _embed_question(request, principal.tenant_id, params["q"])
        if vec is not None:
            async with tenant_tx(principal.tenant_id) as conn:
                await conn.execute(text("UPDATE rag.gold_examples SET embedding = CAST(:v AS vector), "
                                        "embedding_model = :m WHERE id = :id"), {"v": vec, "m": model, "id": gold_id})
    async with tenant_tx(principal.tenant_id) as conn:
        return _gold_out(await _gold(conn, gold_id))


class GoldReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    ids: list[str] = Field(min_length=1, max_length=1000)  # the PMO draft set is 527 with the noise set (US-220)


@router.post("/gold-examples/review")
async def review_gold(body: GoldReviewIn, request: Request,
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Approve or reject suggestions (one or many); approving re-validates each spec on the current model."""
    done, skipped = [], []
    async with tenant_tx(principal.tenant_id) as conn:
        for gid in dict.fromkeys(body.ids):
            g = await _gold(conn, gid)
            if g["status"] != "suggested":
                skipped.append({"id": gid, "reason": f"already {g['status']}"})
                continue
            if body.decision == "approve":
                snap = await _current_snapshot(conn, principal.tenant_id, g["connection_id"])
                try:
                    core = validate_against(snap, g["expected_spec"])
                except TelaError as exc:
                    skipped.append({"id": gid, "reason": exc.message})
                    continue
                await conn.execute(text("UPDATE rag.gold_examples SET status = 'approved', expected_spec = "
                                        "CAST(:s AS jsonb), model_version_id = :mv, verified_by = :u, verified_at = "
                                        "now() WHERE id = :id"),
                                   {"s": json.dumps(core), "mv": snap.model_version_id, "u": principal.user_id,
                                    "id": gid})
                if g["origin"] == "answer":
                    await _answer_synonyms(conn, request, principal, {**g, "expected_spec": core}, snap)
            else:
                await conn.execute(text("UPDATE rag.gold_examples SET status = 'rejected', verified_by = :u, "
                                        "verified_at = now() WHERE id = :id"), {"u": principal.user_id, "id": gid})
            done.append(gid)
        if done:
            await audit.record(conn, tenant_id=principal.tenant_id, action=f"gold_example.{body.decision}d",
                               object_type="gold_example", object_id=done[0] if len(done) == 1 else None,
                               outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
                               details={"ids": done})
    return {"updated": done, "skipped": skipped}


@router.delete("/gold-examples/{gold_id}", status_code=204)
async def delete_gold(gold_id: str, request: Request,
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        g = await _gold(conn, gold_id)
        await conn.execute(text("DELETE FROM rag.gold_examples WHERE id = :id"), {"id": gold_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.deleted",
                           object_type="gold_example", object_id=gold_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"question": g["question"]})
    return Response(status_code=204)


class ImportIn(BaseModel):
    connection_id: str = Field(max_length=64)


def pack_draft(packs_dir: str | None = None) -> list[dict[str, Any]]:
    """The PMO pack folder's drafted gold set."""
    path = Path(packs_dir or DEFAULT_PACKS_DIR) / "pmo" / "gold_examples.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return list((data or {}).get("examples") or [])


async def stored_pack_draft(conn: AsyncConnection, packs_dir: str | None = None) -> list[dict[str, Any]]:
    """The PMO pack's drafted gold set as stored (its latest released version, US-231); its folder when the pack
    was never loaded."""
    folder = FilePackSource(Path(packs_dir or DEFAULT_PACKS_DIR) / "pmo")
    src = await system_source(conn, folder.pack_key, packs_dir=packs_dir) if folder.exists() else None
    data = src.document("gold_examples") if src else None
    return list((data or {}).get("examples") or [])


@router.post("/gold-examples/import-pack")
async def import_pack(body: ImportIn, request: Request,
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Load the drafted PMO gold set as suggestions for review (STK-008 curates; nothing is approved here)."""
    created, skipped = 0, []
    async with tenant_tx(principal.tenant_id) as conn:
        snap = await _current_snapshot(conn, principal.tenant_id, body.connection_id)
        existing = {r["q"] for r in await fetch_all(
            conn, "SELECT lower(question) AS q FROM rag.gold_examples WHERE connection_id = CAST(:c AS uuid) AND "
                  "status <> 'rejected'", {"c": body.connection_id})}
        for ex in await stored_pack_draft(conn, request.app.state.settings.packs_dir):
            q = " ".join(str(ex["question"]).split())
            if q.lower() in existing:
                skipped.append({"question": q, "reason": "already present"})
                continue
            try:
                core = validate_against(snap, ex["spec"])
            except TelaError as exc:
                skipped.append({"question": q, "reason": exc.message})
                continue
            await conn.execute(
                text("INSERT INTO rag.gold_examples (id, tenant_id, connection_id, model_version_id, question, "
                     "expected_spec, tags, status, origin, notes, suggested_by, turns, use_for_fewshot) VALUES (:id, "
                     ":t, :c, :mv, :q, CAST(:s AS jsonb), :tags, 'suggested', 'pack_draft', :n, :u, :turns, :fs)"),
                {"id": new_id(), "t": principal.tenant_id, "c": body.connection_id, "mv": snap.model_version_id,
                 "q": q, "s": json.dumps(core), "tags": _tags(ex.get("tags", [])), "n": ex.get("note"),
                 "u": principal.user_id, "turns": _clean_turns(ex.get("turns") or []),
                 # A held-out example is never shown to AG-03, so it measures paraphrases the planner hasn't seen.
                 "fs": not ex.get("held_out", False)})
            existing.add(q.lower())
            created += 1
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.pack_imported",
                           object_type="connection", object_id=body.connection_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"created": created, "skipped": len(skipped)})
    return {"created": created, "skipped": skipped}


# --------------------------------------------------------------------------- API-050: evaluation


async def queue_run(conn: AsyncConnection, tenant_id: str, run_as: str, *, trigger: str,
                    connection_id: str | None = None, tags: list[str] | None = None,
                    plan_only: bool = False, review: bool | None = None,
                    ask_mode: str | None = None) -> str | None:
    """Queue a run when there is something to evaluate; returns the run id or None. ``ask_mode`` plans every example
    in that answer mode (US-259); None follows each source's own mode."""
    has = await fetch_one(conn, "SELECT 1 FROM rag.gold_examples WHERE status = 'approved' AND (CAST(:c AS uuid) IS "
                                "NULL OR connection_id = CAST(:c AS uuid)) LIMIT 1", {"c": connection_id})
    if has is None:
        return None
    run_id: str = new_id()
    await conn.execute(
        text("INSERT INTO rag.eval_runs (id, tenant_id, connection_id, trigger, tags, threshold, run_as, plan_only, "
             "review, ask_mode) VALUES (:id, :t, CAST(:c AS uuid), :tr, :tags, :th, :u, :po, :rv, :m)"),
        {"id": run_id, "t": tenant_id, "c": connection_id, "tr": trigger, "tags": tags or [],
         "th": await threshold(conn), "u": run_as, "po": plan_only, "rv": review, "m": ask_mode})
    await outbox.emit(conn, tenant_id=tenant_id, topic="knowledge.evaluation_requested", aggregate_id=run_id,
                      payload={"run_id": run_id})
    return run_id


def run_mode_problem(mode: str, plan_only: bool) -> str | None:
    """Why a gold run can't be queued in ``mode`` (US-259), or None."""
    from tela_api.ask import modes

    if mode not in modes.BUILT:
        return f"{modes.LABELS.get(mode, mode)} isn't available yet, so it can't be measured."
    if mode == "direct_sql" and plan_only:
        return "Direct SQL has no plan to compare: run it with result rows (not plan-only)."
    return None


class EvaluateIn(BaseModel):
    connection_id: str | None = Field(default=None, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=12)
    plan_only: bool = False  # US-190: plan and compare specs, fetch no result rows (a live source)
    review: bool | None = None  # US-246: LLM review forced on or off for this run; None follows the AI settings
    # US-259: plan every example in this answer mode, whatever the source's setting; None follows each source's mode
    ask_mode: Literal["certified", "fields", "hybrid", "direct_sql"] | None = None


@router.post("/gold-examples/evaluate", status_code=202)
async def evaluate(body: EvaluateIn, request: Request,
                   principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    if body.ask_mode is not None and (why := run_mode_problem(body.ask_mode, body.plan_only)):
        raise TelaError(ErrorCode.VALIDATION_ERROR, why, {"ask_mode": body.ask_mode})
    async with tenant_tx(principal.tenant_id) as conn:
        await fail_stale(conn)
        # A running run blocks however long it takes; a queued one only while the worker can still pick it up.
        open_run = await fetch_one(conn, "SELECT id FROM rag.eval_runs WHERE status = 'running' OR (status = "
                                         "'queued' AND created_at > now() - interval '1 hour') LIMIT 1")
        if open_run is not None:
            raise TelaError(ErrorCode.CONFLICT, "An evaluation is already running.", {"run_id": str(open_run["id"])})
        run_id = await queue_run(conn, principal.tenant_id, principal.user_id, trigger="manual",
                                 connection_id=body.connection_id, tags=_tags(body.tags), plan_only=body.plan_only,
                                 review=body.review, ask_mode=body.ask_mode)
        if run_id is None:
            raise TelaError(ErrorCode.CONFLICT, "There are no approved gold examples to evaluate.")
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.evaluation_requested",
                           object_type="eval_run", object_id=run_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"tags": body.tags, "plan_only": body.plan_only,
                                                                      "review": body.review,
                                                                      "ask_mode": body.ask_mode})
    return {"run_id": run_id, "status": "queued"}


RUN_COLUMNS = ("r.id, r.connection_id, r.trigger, r.tags, r.status, r.total, r.passed, r.pass_rate, r.recall_at_8, "
               "r.by_tag, r.threshold, r.below_threshold, r.planner, r.error, r.run_as, r.started_at, r.finished_at, "
               "r.created_at, r.spec_match_rate, r.mrr, r.ndcg_at_8, r.recall_by_kind, r.by_error, r.value_linking, "
               "r.plan_only, r.by_path, r.by_set, r.review, r.ask_mode")


def _run_out(r: dict[str, Any]) -> dict[str, Any]:
    out = _gold_out(r)
    for k in ("pass_rate", "recall_at_8", "threshold", "spec_match_rate", "mrr", "ndcg_at_8", "reciprocal_rank",
              "confidence"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    return out


@router.get("/eval-runs")
async def list_runs(limit: int = Query(20, ge=1, le=100),
                    principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, f"SELECT {RUN_COLUMNS}, u.display_name AS run_as_name FROM rag.eval_runs r "  # noqa: S608
                                     "LEFT JOIN iam.users u ON u.id = r.run_as ORDER BY r.created_at DESC LIMIT :n",
                               {"n": limit})
        th = await threshold(conn)
    return {"items": [_run_out(dict(r)) for r in rows], "threshold": th}


@router.get("/eval-runs/{run_id}")
async def get_run(run_id: str, principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        try:
            run = await fetch_one(conn, f"SELECT {RUN_COLUMNS} FROM rag.eval_runs r WHERE r.id = CAST(:id AS uuid)",  # noqa: S608
                                  {"id": run_id})
        except Exception:  # noqa: BLE001
            run = None
        if run is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That evaluation run was not found.")
        results = await fetch_all(conn, "SELECT e.id, e.gold_example_id, e.question, e.tags, e.passed, e.planner, "
                                        "e.actual_spec, e.diff, e.retrieval_hit, e.fingerprint_ok, e.error, "
                                        "e.spec_match, e.exec_match, e.error_class, e.reciprocal_rank, e.ndcg_at_8, "
                                        "e.values_linked, e.status, e.confidence, e.confident_wrong, e.clarify_right, "
                                        "e.required, e.latency_ms, e.llm_calls, g.expected_spec FROM "
                                        "rag.eval_results e LEFT JOIN rag.gold_examples g ON g.id = e.gold_example_id "
                                        "WHERE e.run_id = :r ORDER BY e.passed, e.question", {"r": run["id"]})
    rows = [result_shape(dict(r)) for r in results]
    return {**_run_out(dict(run)), "shape_grid": scoring.shape_grid(rows), "results": [_run_out(r) for r in rows]}


def result_shape(r: dict[str, Any]) -> dict[str, Any]:
    """A stored result with its example's answer shape (US-250; no column: read from the shape check's diff entry,
    else from the example's expected spec, None when the example was deleted) and without that spec."""
    spec = r.pop("expected_spec", None)
    told = next((d.get("expected") for d in r.get("diff") or [] if d.get("path") == "answer_shape"), None)
    return {**r, "shape": told or scoring.answer_shape(spec)}


class PolicyIn(BaseModel):
    eval_threshold: float = Field(ge=0, le=1)


@router.put("/policy")
async def put_policy(body: PolicyIn, request: Request,
                     principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await conn.execute(text("INSERT INTO rag.policies (tenant_id, eval_threshold, updated_by) VALUES (:t, :th, :u) "
                                "ON CONFLICT (tenant_id) DO UPDATE SET eval_threshold = :th, updated_by = :u, "
                                "updated_at = now()"),
                           {"t": principal.tenant_id, "th": round(body.eval_threshold, 4), "u": principal.user_id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="gold_example.threshold_changed",
                           object_type="policy", outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"eval_threshold": body.eval_threshold})
    return {"eval_threshold": round(body.eval_threshold, 4)}


# --------------------------------------------------------------------------- running a suite


FRAME_PLANNERS = ("fields", "hybrid")  # US-254 / US-255 (as ``ask.orchestrator``; no import: a cycle)


def fingerprint(result: dict[str, Any]) -> dict[str, Any]:
    rows = result.get("rows") or []
    digest = hashlib.sha256(canonical(rows).encode()).hexdigest()[:16]
    return {"row_count": int(result.get("row_count", len(rows))), "checksum": digest}


async def plan_messages(orch: Any, principal: Principal, messages: list[str], src: Any,
                        on_retrieval: Any = None) -> tuple[Any, str, Any]:
    """Plan a multi-turn example as Ask does (US-175): each message is planned with the conversation so far, so a
    follow-up ("what about Jordan?") is read against the previous answer. A follow-up that can't be planned is
    ``unsupported``; the retrieval returned is the first question's, and ``on_retrieval`` sees the first only."""
    from tela_api.ask.planner import Plan
    from tela_api.ask.rewrite import Turn
    from tela_api.llm import trace

    history: list[Turn] = []
    with trace.paused():  # gold runs plan hundreds of questions: kept out of the agent run log (ENH-024)
        plan, used, knowledge = await orch.plan_for(principal, messages[0], src, {}, on_retrieval=on_retrieval)
        for text_ in messages[1:]:
            if plan.status != "ok" or plan.spec is None:
                break
            history.append(Turn(messages[len(history)], plan.spec))
            plan, used, _ = await orch.plan_for(principal, text_, src, {}, history=history)
            if plan.status == "unsupported":
                return (Plan(status="unsupported", reason=f"The follow-up “{text_}” was not understood."), used,
                        knowledge)
    return plan, used, knowledge


async def _rows(service: QueryService, spec: dict[str, Any], src: Any, principal: Principal, *,
                fields: bool = False) -> dict[str, Any]:
    """Run a spec for the suite; ``fields``: a Fields/Hybrid answer, whose ad-hoc measure runs under the answer
    mode as in Ask (US-254), not the runner's free-hand grant."""
    return await service.execute(
        {"model_version_id": src.snap.model_version_id, "connection_id": str(src.connection["id"]),
         **{k: v for k, v in spec.items() if k != "compare"}}, principal,
        QueryContext(surface="evaluation", use_cache=False, fields=fields))


async def counterpart(service: QueryService, spec: dict[str, Any], ref: str, src: Any, principal: Principal,
                      ran: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Run a Fields/Hybrid answer and its certified counterpart once each (US-255); the diff entry when they differ.
    A list cut short by the row cap can't be counted, so it is not compared."""
    other, how = scoring.counterpart_spec(spec, ref)
    b = ran or await _rows(service, spec, src, principal, fields=True)
    if b.get("truncated"):
        return None
    c = await _rows(service, other, src, principal)
    return scoring.counterpart_problem(how, b.get("rows") or [], c.get("rows") or [], ref)


def empty_result(ex: dict[str, Any]) -> dict[str, Any]:
    return {"gold_example_id": str(ex["id"]), "question": ex["question"], "tags": list(ex["tags"] or []),
            "passed": False, "planner": None, "actual_spec": None, "diff": [], "retrieval_hit": None,
            "fingerprint_ok": None, "error": None, "spec_match": False, "exec_match": None, "error_class": None,
            "retrieval": None, "values_linked": None, "status": "error", "confidence": None,
            "confident_wrong": False, "clarify_right": None, "required": None, "latency_ms": None,
            "llm_calls": None, "shape": scoring.answer_shape(ex.get("expected_spec"))}


async def evaluate_one(service: QueryService, orch: Any, principal: Principal, tenant_id: str, ex: dict[str, Any],
                       src: Any, *, plan_only: bool = False) -> dict[str, Any]:
    """Plan, compare and (when the specs differ, unless ``plan_only``) run one example; the result row of
    ``rag.eval_results``."""
    from tela_api.ask import confidence
    from tela_api.llm.client import counting

    if getattr(src, "ask_mode", None) == "direct_sql":
        return await evaluate_direct(service, orch, principal, tenant_id, ex, src, plan_only=plan_only)
    turns = list(ex.get("turns") or [])
    expected = ex["expected_spec"]
    res = empty_result(ex)
    offered: list[str] = []

    async def seen(_knowledge: Any, considered: list[str], _rw: Any, links: Any) -> None:
        # What the planner was offered, kept even when it then refuses (US-190 required-item recall).
        offered.extend(considered)
        if links is not None:
            offered.extend(links.refs)

    started = time.monotonic()
    with counting() as calls:
        try:
            plan, used, knowledge = await plan_messages(orch, principal, [*turns, ex["question"]], src,
                                                        on_retrieval=seen)
        except Exception as exc:  # noqa: BLE001 - one bad example must not stop the suite
            log.warning("gold.plan_failed", error_class=type(exc).__name__)
            return {**res, "error": "Planning failed.", "error_class": "error", "llm_calls": calls[0]}
    res["latency_ms"] = int((time.monotonic() - started) * 1000)
    res["llm_calls"] = calls[0]
    res["planner"], res["status"], res["confidence"] = used, plan.status, round(float(plan.confidence), 4)
    if not turns:  # a follow-up's retrieval is the first question's
        hits = [h.metadata for h in knowledge.hits]
        res["required"] = scoring.required_recall(expected, [*offered, *plan.measures_considered], hits)
        if knowledge.hits or knowledge.searched:
            res["retrieval"] = scoring.retrieval_scores(hits, expected)
            hit = res["retrieval"]["recall"]["measure"]
            res["retrieval_hit"] = None if hit is None else hit == 1.0
    if plan.status != "ok" or plan.spec is None:
        res["diff"] = [{"path": "spec", "expected": "a planned question",
                        "actual": plan.status if plan.status != "clarify" else "asked to clarify"}]
        res["values_linked"] = scoring.values_linked(expected, None)
        res["clarify_right"] = scoring.offered_right(expected, plan.question) if plan.status == "clarify" else None
        return {**res, "error": plan.reason, "error_class": scoring.classify(res["diff"], status=plan.status)}
    res["actual_spec"] = core_spec(plan.spec)
    # US-250: the kind of answer counts as well as the rows (a count is never the list that was asked for).
    wrong_shape = scoring.shape_diff(res["shape"], plan.spec)
    res["diff"] = spec_diff(expected, plan.spec) + wrong_shape
    res["spec_match"] = not res["diff"]
    res["values_linked"] = scoring.values_linked(expected, res["actual_spec"])
    if plan_only:
        res["passed"] = res["spec_match"]
        res["confident_wrong"] = not res["passed"] and plan.confidence >= confidence.threshold(None)
        if not res["passed"]:
            res["error_class"] = scoring.classify(res["diff"], expected=expected, actual=res["actual_spec"])
        return res

    runner = await _runner(principal, tenant_id, ex)
    actual_out: dict[str, Any] | None = None
    if res["spec_match"]:
        res["exec_match"] = True
    else:
        # Same rows from a different spec still answer the question; an empty or all-zero result proves nothing.
        try:
            want = await _rows(service, expected, src, runner)
        except TelaError as exc:
            return {**res, "error": f"The expected spec did not run: {exc.message}", "error_class": "error"}
        try:
            actual_out = await _rows(service, plan.spec, src, runner, fields=used in FRAME_PLANNERS)
        except TelaError as exc:
            res["exec_match"], res["error"] = False, exc.message
        else:
            match = scoring.frame_match if used in FRAME_PLANNERS else (  # US-259: ad-hoc measures by value
                lambda e, a, shape, ordered: scoring.informative(e) and scoring.same_result(e, a, ordered=ordered))
            res["exec_match"] = match(want.get("rows") or [], actual_out.get("rows") or [], shape=res["shape"],
                                      ordered=scoring.ordered_spec(expected))
    res["passed"] = bool(res["exec_match"]) and not wrong_shape
    if (twin := ((plan.signals or {}).get("fields") or {}).get("counterpart")) is not None:
        # US-255: a Fields/Hybrid answer with a certified counterpart (the same term): both run, any difference fails
        try:
            problem = await counterpart(service, plan.spec, twin, src, runner, actual_out)
        except TelaError as exc:
            problem = {"path": scoring.COUNTERPART_PATH, "expected": twin, "actual": exc.message}
        if problem is not None:
            res["passed"] = False
            res["diff"] = [*res["diff"], problem]
    res["values_linked"] = scoring.values_linked(expected, res["actual_spec"], passed=res["passed"])
    if res["passed"] and ex["expected_result_fingerprint"] and ex["role_id"]:
        try:
            got = fingerprint(actual_out or await _rows(service, plan.spec, src, runner,
                                                        fields=used in FRAME_PLANNERS))
            want_fp = ex["expected_result_fingerprint"]
            res["fingerprint_ok"] = all(got.get(k) == want_fp.get(k) for k in ("row_count", "checksum")
                                        if k in want_fp)
        except TelaError as exc:
            res["fingerprint_ok"] = False
            res["error"] = exc.message
        if not res["fingerprint_ok"]:
            res["passed"] = False
            res["diff"] = [{"path": "result", "expected": ex["expected_result_fingerprint"],
                            "actual": res.get("error") or "different rows"}]
    if not res["passed"]:
        res["error_class"] = scoring.classify(res["diff"], expected=expected, actual=res["actual_spec"])
        res["confident_wrong"] = plan.confidence >= confidence.threshold(None)
    return res


async def _runner(principal: Principal, tenant_id: str, ex: dict[str, Any]) -> Principal:
    """Who an example's rows are fetched as: its test scope role when it has a fingerprint, else the run's person."""
    if ex["expected_result_fingerprint"] and ex["role_id"]:
        async with tenant_tx(tenant_id) as conn:
            return await principal_for_role(conn, principal, {"id": ex["role_id"], "key": ex["role_key"],
                                                              "name": ex["role_name"], "kind": ex["role_kind"]})
    return principal


async def evaluate_direct(service: QueryService, orch: Any, principal: Principal, tenant_id: str,
                          ex: dict[str, Any], src: Any, *, plan_only: bool = False) -> dict[str, Any]:
    """One example in Direct SQL mode (US-259): the frame and AG-10's SQL as Ask builds them (US-257b), run through
    the Custom SQL pipeline, and its rows compared with the expected spec's (``scoring.direct_match``), with the
    frame's kind of answer against the example's. There is no spec, so a plan-only run can't grade it. A wrong
    answer counts as confident-wrong: Direct SQL never asks, it answers or refuses."""
    from tela_api.ask.direct_sql import answer as direct_answer
    from tela_api.ask.orchestrator import direct_question
    from tela_api.llm import trace
    from tela_api.llm.client import counting

    res = {**empty_result(ex), "planner": "direct_sql"}
    if plan_only:
        return {**res, "error": "Direct SQL has no plan to compare: run it with result rows.",
                "error_class": "error"}
    expected, turns = ex["expected_spec"], list(ex.get("turns") or [])
    question = ex["question"]
    previous = turns[-1] if turns else None
    runner = await _runner(principal, tenant_id, ex)
    started = time.monotonic()
    with counting() as calls:
        try:
            with trace.paused():
                framed = await orch.direct_frame(runner, question, src, previous=previous)
                got = await direct_answer(service, orch.gateway, runner, connection_id=str(src.connection["id"]),
                                          question=direct_question(question, previous, framed["frame"]),
                                          frame=framed["frame"], surface="evaluation")
        except Exception as exc:  # noqa: BLE001 - one bad example must not stop the suite
            log.warning("gold.direct_failed", error_class=type(exc).__name__)
            return {**res, "error": "Direct SQL failed.", "error_class": "error", "llm_calls": calls[0]}
    res["latency_ms"], res["llm_calls"] = int((time.monotonic() - started) * 1000), calls[0]
    if got.status != "ok" or got.result is None:
        res["status"] = "unsupported"
        res["diff"] = [{"path": "sql", "expected": "an answer", "actual": got.reason or "refused"}]
        return {**res, "error": got.message, "error_class": scoring.classify(res["diff"], status="unsupported")}
    res["status"], res["confidence"] = "ok", 1.0
    wrong_shape = [] if res["shape"] in (None, got.shape) else [
        {"path": "answer_shape", "expected": res["shape"], "actual": got.shape}]
    try:
        want = await _rows(service, expected, src, runner)
    except TelaError as exc:
        return {**res, "error": f"The expected spec did not run: {exc.message}", "error_class": "error"}
    res["exec_match"] = scoring.direct_match(want.get("rows") or [], got.result.get("rows") or [],
                                             shape=res["shape"], ordered=scoring.ordered_spec(expected))
    res["passed"] = bool(res["exec_match"]) and not wrong_shape
    if not res["passed"]:
        res["diff"] = wrong_shape or [{"path": "result", "expected": f"{len(want.get('rows') or [])} rows",
                                       "actual": f"{got.result.get('row_count')} rows, different values"}]
        res["error_class"] = scoring.classify(res["diff"])
        res["confident_wrong"] = True
        res["error"] = got.sql and f"SQL: {got.sql}"[:500]
    return res


async def fail_stale(conn: AsyncConnection) -> int:
    """Fail the runs a crash left ``running`` (a shutdown marks its own runs at once, see ``stop_runs``)."""
    rows = await fetch_all(conn, "UPDATE rag.eval_runs SET status = 'failed', finished_at = now(), error = :e WHERE "
                                 "status = 'running' AND started_at < now() - CAST(:age AS interval) RETURNING id",
                           {"e": MSG_STALE, "age": STALE_AFTER})
    return len(rows)


async def claim_run(tenant_id: str, run_id: str) -> tuple[dict[str, Any] | None, str]:
    """Move a queued run to running exactly once; a second call (a retry, a redelivered task) gets its status."""
    async with tenant_tx(tenant_id) as conn:
        try:
            run = await fetch_one(conn, "UPDATE rag.eval_runs SET status = 'running', started_at = now() WHERE id = "
                                        "CAST(:id AS uuid) AND status = 'queued' RETURNING *", {"id": run_id})
            current = run or await fetch_one(conn, "SELECT status FROM rag.eval_runs WHERE id = CAST(:id AS uuid)",
                                             {"id": run_id})
        except Exception:  # noqa: BLE001 - malformed id
            current = run = None
    if current is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That evaluation run was not found.")
    return (dict(run) if run else None), str(current["status"])


async def _fail_run(tenant_id: str, run_id: str, message: str) -> None:
    async with tenant_tx(tenant_id) as conn:
        await conn.execute(text("UPDATE rag.eval_runs SET status = 'failed', finished_at = now(), error = :e WHERE "
                                "id = CAST(:id AS uuid) AND status = 'running'"), {"e": message, "id": run_id})


async def _run_guarded(app: FastAPI, tenant_id: str, run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run["id"])
    try:
        return await run_suite(app, tenant_id, run)
    except asyncio.CancelledError:
        await _fail_run(tenant_id, run_id, MSG_INTERRUPTED)
        raise
    except Exception as exc:  # the run must end failed, never stay running
        log.exception("gold.run_failed", error_class=type(exc).__name__)
        await _fail_run(tenant_id, run_id, "The evaluation stopped unexpectedly.")
        return {"status": "failed"}


def start_run(app: FastAPI, tenant_id: str, run: dict[str, Any]) -> asyncio.Task[dict[str, Any]]:
    """Run a claimed suite in the background; the task is kept on the app so shutdown can stop it."""
    tasks: set[asyncio.Task[dict[str, Any]]] = app.state.eval_tasks
    task = asyncio.create_task(_run_guarded(app, tenant_id, run))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def stop_runs(app: FastAPI) -> None:
    """On shutdown: cancel the runs in progress; each is marked interrupted before the database closes."""
    tasks = list(getattr(app.state, "eval_tasks", ()))
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def in_mode(sources: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, str]]:
    """The sources as a run in ``mode`` plans them (US-259): each one a copy answering in that mode, and why the
    sources that can't (Direct SQL on MongoDB, a mode not built) are left out. The sources themselves are unchanged."""
    from tela_api.ask import modes

    out: dict[str, Any] = {}
    why: dict[str, str] = {}
    for cid, src in sources.items():
        problem = modes.unavailable(mode, src.connection.get("engine", ""), src.connection.get("descriptor"))
        if problem is not None:
            why[cid] = f"{modes.LABELS.get(mode, mode)} can't answer on {src.connection['name']}: {problem}"
            continue
        twin = copy.copy(src)
        twin.ask_mode, twin.mode_note = mode, None
        out[cid] = twin
    return out, why


async def run_suite(app: FastAPI, tenant_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a claimed (``running``) run to the end and store its results."""
    from tela_api.ask.orchestrator import Orchestrator  # the orchestrator imports this module for few-shots
    from tela_api.llm.review import forced

    run_id = str(run["id"])
    async with tenant_tx(tenant_id) as conn:
        principal = await principal_for_user(conn, tenant_id, str(run["run_as"]))
        examples = await fetch_all(
            conn, "SELECT g.id, g.connection_id, g.question, g.turns, g.expected_spec, g.expected_result_fingerprint, "
                  "g.test_scope_role_id, g.tags, ro.id AS role_id, ro.key AS role_key, ro.name AS role_name, "
                  "ro.kind AS role_kind FROM rag.gold_examples g LEFT JOIN iam.roles ro ON ro.id = "
                  "g.test_scope_role_id WHERE g.status = 'approved' AND (CAST(:c AS uuid) IS NULL OR g.connection_id "
                  "= CAST(:c AS uuid)) AND (cardinality(CAST(:tags AS text[])) = 0 OR g.tags && CAST(:tags AS text[])) "
                  "ORDER BY g.created_at",
            {"c": str(run["connection_id"]) if run["connection_id"] else None, "tags": list(run["tags"] or [])})
        # How many questions this run covers, so the screen can say so while it runs.
        await conn.execute(text("UPDATE rag.eval_runs SET total = :n WHERE id = :id"),
                           {"n": len(examples), "id": run["id"]})
    if principal is None:
        async with tenant_tx(tenant_id) as conn:
            await conn.execute(text("UPDATE rag.eval_runs SET status = 'failed', finished_at = now(), error = :e "
                                    "WHERE id = :id"),
                               {"e": "The person this suite runs as no longer has access.", "id": run["id"]})
        return {"status": "failed"}

    orch = Orchestrator(app.state.query_service, app.state.gateway)
    sources = {str(s.connection["id"]): s for s in await orch.sources(principal, None)}
    run_mode: str | None = run.get("ask_mode")
    unavailable: dict[str, str] = {}
    if run_mode is not None:  # US-259: every example in the run's mode, whatever each source is set to
        sources, unavailable = in_mode(sources, run_mode)
    else:  # US-256: each source's own mode; the run is that mode's score only when they all share one
        planned = {s.ask_mode for c, s in sources.items()
                   if run["connection_id"] is None or c == str(run["connection_id"])}
        if len(planned) == 1:
            async with tenant_tx(tenant_id) as conn:
                await conn.execute(text("UPDATE rag.eval_runs SET ask_mode = :m WHERE id = :id"),
                                   {"m": planned.pop(), "id": run["id"]})
    results: list[dict[str, Any]] = []
    planners: dict[str, int] = {}
    with forced(run.get("review")):  # US-246: the gate's runs plan with review forced on or off
        for ex in examples:
            src = sources.get(str(ex["connection_id"]))
            if (why := unavailable.get(str(ex["connection_id"]))) is not None:
                results.append({**empty_result(dict(ex)), "error_class": "error", "error": why})
                continue
            if src is None:
                results.append({**empty_result(dict(ex)), "error_class": "error",
                                "error": "The connection or its model is not visible to the person this suite runs "
                                         "as."})
                continue
            res = await evaluate_one(app.state.query_service, orch, principal, tenant_id, dict(ex), src,
                                     plan_only=bool(run.get("plan_only")))
            if res["planner"]:
                planners[res["planner"]] = planners.get(res["planner"], 0) + 1
            results.append(res)

    s = scoring.summarise(results)
    total, passed, rate, recall = s["total"], s["passed"], s["pass_rate"], s["recall_at_8"]
    below = rate is not None and rate < float(run["threshold"])
    async with tenant_tx(tenant_id) as conn:
        for r in results:
            ret = r["retrieval"] or {}
            await conn.execute(
                text("INSERT INTO rag.eval_results (id, tenant_id, run_id, gold_example_id, question, tags, passed, "
                     "planner, actual_spec, diff, retrieval_hit, fingerprint_ok, error, spec_match, exec_match, "
                     "error_class, reciprocal_rank, ndcg_at_8, values_linked, status, confidence, confident_wrong, "
                     "clarify_right, required, latency_ms, llm_calls) VALUES (:id, :t, :run, CAST(:g AS uuid), :q, "
                     ":tags, :p, :pl, CAST(:a AS jsonb), CAST(:d AS jsonb), :rh, :fp, :e, :sm, :xm, :ec, :rr, :nd, "
                     ":vl, :st, :cf, :cw, :cr, CAST(:rq AS jsonb), :lat, :calls)"),
                {"id": new_id(), "t": tenant_id, "run": run["id"], "g": r["gold_example_id"], "q": r["question"],
                 "tags": r["tags"], "p": r["passed"], "pl": r["planner"],
                 "a": json.dumps(r["actual_spec"]) if r["actual_spec"] is not None else None,
                 "d": json.dumps(r["diff"], default=str), "rh": r["retrieval_hit"], "fp": r["fingerprint_ok"],
                 "e": (r["error"] or None) and str(r["error"])[:500], "sm": r["spec_match"],
                 "xm": r["exec_match"], "ec": r["error_class"], "rr": ret.get("reciprocal_rank"),
                 "nd": ret.get("ndcg_at_8"), "vl": r["values_linked"], "st": r["status"], "cf": r["confidence"],
                 "cw": r["confident_wrong"], "cr": r["clarify_right"],
                 "rq": json.dumps(r["required"]) if r["required"] is not None else None, "lat": r["latency_ms"],
                 "calls": r.get("llm_calls")})
        await conn.execute(
            text("UPDATE rag.eval_runs SET status = 'done', total = :n, passed = :p, pass_rate = :r, recall_at_8 = "
                 ":rc, by_tag = CAST(:bt AS jsonb), below_threshold = :b, planner = CAST(:pl AS jsonb), finished_at = "
                 "now(), spec_match_rate = :sm, mrr = :mrr, ndcg_at_8 = :nd, recall_by_kind = CAST(:rk AS jsonb), "
                 "by_error = CAST(:be AS jsonb), value_linking = CAST(:vl AS jsonb), by_path = CAST(:bp AS jsonb), "
                 "by_set = CAST(:bs AS jsonb) WHERE id = :id"),
            {"n": total, "p": passed, "r": rate, "rc": recall, "bt": json.dumps(s["by_tag"]), "b": below,
             "pl": json.dumps(planners), "sm": s["spec_match_rate"], "mrr": s["mrr"], "nd": s["ndcg_at_8"],
             "rk": json.dumps(s["recall_by_kind"]), "be": json.dumps(s["by_error"]),
             "vl": json.dumps(s["value_linking"]), "bp": json.dumps(s["by_path"]), "bs": json.dumps(s["by_set"]),
             "id": run["id"]})
        await audit.record(conn, tenant_id=tenant_id, action="gold_example.evaluated", object_type="eval_run",
                           object_id=run_id, outcome="success", actor_id=str(run["run_as"]), correlation_id=None,
                           details={"trigger": run["trigger"], "total": total, "passed": passed, "pass_rate": rate,
                                    "spec_match_rate": s["spec_match_rate"], "recall_at_8": recall,
                                    "mrr": s["mrr"], "ndcg_at_8": s["ndcg_at_8"], "by_error": s["by_error"],
                                    "below_threshold": below, "plan_only": bool(run.get("plan_only")),
                                    "review": run.get("review")})
        if below:
            admins = await users_with_permission(conn, Perm.KNOWLEDGE_MANAGE.value)
            await notify(conn, tenant_id, admins, kind="gold_example.below_threshold",
                         title="Answer accuracy is below the threshold",
                         body=f"The gold set passed {passed} of {total} ({rate:.0%}); the threshold is "
                              f"{float(run['threshold']):.0%}. Nothing is blocked — review the failures.",
                         link=f"/admin/knowledge/gold?run={run_id}")
    return {"status": "done", "total": total, "passed": passed, "pass_rate": rate, "recall_at_8": recall,
            "spec_match_rate": s["spec_match_rate"], "mrr": s["mrr"], "ndcg_at_8": s["ndcg_at_8"],
            "by_error": s["by_error"], "below_threshold": below}


class RunIn(BaseModel):
    tenant_id: str = Field(max_length=64)


@internal.post("/eval-runs/{run_id}/run", status_code=202)
async def run_internal(run_id: str, body: RunIn, request: Request,
                       _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    """Claim a queued run and start it in the background; returns at once, whatever the run's length."""
    run, status = await claim_run(body.tenant_id, run_id)
    if run is None:
        return {"status": status}
    start_run(request.app, body.tenant_id, run)
    return {"status": "running", "run_id": run_id}


@internal.post("/eval-runs/nightly")
async def nightly(_: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    async with tenant_tx(None) as conn:
        tenants = await fetch_all(conn, "SELECT tenant_id, run_as FROM rag.f_eval_tenants()")
    queued = 0
    for t in tenants:
        async with tenant_tx(str(t["tenant_id"])) as conn:
            await fail_stale(conn)
            if await queue_run(conn, str(t["tenant_id"]), str(t["run_as"]), trigger="nightly"):
                queued += 1
    return {"tenants": len(tenants), "queued": queued}
