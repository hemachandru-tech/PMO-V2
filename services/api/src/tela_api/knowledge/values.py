"""Dimension value index (ENH-011, US-173; FEAT-062 / FEAT-014).

People name values their own way: "KSA", "Saudi" and "Kingdom of Saudi Arabia" all mean the stored region ``KSA``;
"capex" is budget type ``CAPEX``; "Lina" is project manager Lina Farouk. Before planning, Ask links the words of a
question to stored values, so a filter uses the stored spelling first time.

* **Index** (``rag.dimension_values``, migration 0033): the distinct values of each text dimension of the current
  approved model version, read through the one query path with every row in view (surface ``value_index``), with
  a normalised spelling, the approved synonyms and an embedding of "Region: KSA". Refreshed on model approval (the
  outbox ``model.version_approved`` event) and nightly. Dimensions with more than ``value_index_max_values`` values
  are free text and are skipped, and so are dimensions whose values read as sentences (US-213/214: reasons, comments;
  ``free_text_values``), as are computed dimensions (their labels are the model's own words), sensitive
  fields, identifiers and e-mail or phone fields. Person names are indexed but never sent to the embedding service.
* **Linking**, in order, each word used once, longest phrases first: the stored value or its display label
  (*exact*), an approved synonym (*synonym*), a word that is part of only one value of a dimension ("Lina" →
  Lina Farouk, *partial*; only a name-like value of at most ``NAME_WORDS`` words, and only a word few values
  hold), a spelling slip ("Jordon", *spelling*: ``pg_trgm`` finds the candidates, an edit
  distance of 1, or 2 for long words, decides), then, for words the model doesn't know, the closest value by meaning
  (*meaning*, cosine ≥ ``value_link_vector_min``). Partial and spelling matches never guess between values: two
  candidates in one dimension become one clarifying question, and the person's choice is proposed as a synonym.
* **Scope** is applied when linking, not when indexing: only dimensions the person can see, and only values their
  scope lets them see (checked against their scoped value list), are ever linked.
* **Synonyms** (``rag.value_synonyms``) come from the pack (``value_synonyms.yaml``), from clarification choices
  and from Admins. Pack and clarification synonyms are pending until an Admin approves them (FEAT-014 review gate);
  only approved ones are linked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.planner import STOP, LinkedValue, ValueChoice, known_words, norm, words
from tela_api.audit import service as audit
from tela_api.content.exports import _rid, require_worker
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.core.settings import get_settings
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal, principal_for_user
from tela_api.knowledge.index import embed
from tela_api.knowledge.patterns import pack_source_for
from tela_api.llm.client import GatewayClient
from tela_api.packs.source import PackSource, resolve
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_query_spec.model import Dimension, ModelSnapshot

log = get_logger(__name__)

MAX_PHRASE = 5  # words
SKIP_SEMANTIC = frozenset({"email", "phone", "identifier", "free_text"})
SKIP_SENSITIVITY = frozenset({"sensitive"})
NO_EMBED_SEMANTIC = frozenset({"person_name"})
SCORES = {"exact": 1.0, "synonym": 0.95, "partial": 0.8, "spelling": 0.7, "meaning": 0.6}
NAME_WORDS = 4  # a partial match only links a value this short: a name ("Lina Farouk"), not a sentence (US-214)
PARTIAL_MAX_VALUES = 4  # ... and only by a word at most this many values hold ("uae": two regions; "the": many)
NUMBER_WORDS = frozenset({"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "first",
                          "second", "third"})
PARTIAL_MIN_LETTERS = 4  # ... and by a synonym's word only this long ("out" of "close out" is not Closure)
SENTENCE = re.compile(r"[.!?;]\s|[.!?]$")


def sentence_like(value: str) -> bool:
    """A sentence, not a label: sentence punctuation, or six words or more most of whose longer words are lowercase
    ("Training material will be prepared once the build i…"); a long Title Case label ("Schedule Slippage - Client
    (System / Resource Constraints)") is not one."""
    v = value.strip()
    if SENTENCE.search(v):
        return True
    longer = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", v) if len(w) >= 4]
    return len(words(v)) >= 6 and sum(w[0].isupper() for w in longer) < len(longer) / 2


def free_text_values(values: list[str]) -> bool:
    """Values that read as sentences, not categories (US-214): at least three, and 30 % or more of them
    ``sentence_like``."""
    vals = [str(v) for v in values if str(v).strip()]
    return len(vals) >= 3 and sum(sentence_like(v) for v in vals) >= 0.3 * len(vals)

# --------------------------------------------------------------------------- matching (no database)


@dataclass(frozen=True)
class ValueRow:
    dimension: str
    stored: str
    label: str
    synonyms: tuple[str, ...] = ()  # normalised

    @property
    def names(self) -> set[str]:
        return {n for n in (norm(self.stored), norm(self.label)) if n}

    def link(self, phrase: str, how: str, score: float | None = None) -> LinkedValue:
        return LinkedValue(self.dimension, self.stored, self.label, phrase, how, SCORES[how] if score is None
                           else score)


@dataclass
class Linking:
    links: list[LinkedValue] = field(default_factory=list)
    choices: list[ValueChoice] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # runs of unknown words nothing matched (meaning search)

    def public(self) -> dict[str, Any]:
        return {"links": [{"dimension": v.dimension, "stored": v.stored, "label": v.label, "phrase": v.phrase,
                           "how": v.how, "score": round(v.score, 3)} for v in self.links],
                "choices": [{"dimension": c.dimension, "phrase": c.phrase,
                             "options": [{"stored": s, "label": lbl} for s, lbl in c.options]} for c in self.choices]}


def edits(a: str, b: str, cap: int = 3) -> int:
    """Optimal string alignment distance (insert, delete, substitute, swap neighbours), stopped past ``cap``."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def close_spelling(a: str, b: str) -> bool:
    """A slip, not another word: one edit for words of 4 to 7 letters, two from 8 (never for 3 letters or fewer)."""
    if len(a) < 4 or a == b:
        return False
    return edits(a, b) <= (1 if min(len(a), len(b)) < 8 else 2)


def _windows(ws: list[str], used: list[bool], size: int) -> list[tuple[int, str]]:
    return [(i, " ".join(ws[i:i + size])) for i in range(len(ws) - size + 1) if not any(used[i:i + size])]


def _group(found: dict[str, list[ValueRow]], phrase: str, how: str, out: Linking) -> bool:
    """One candidate per dimension links; several in one dimension ask. True when anything was found."""
    if not found:
        return False
    for dim, rows in found.items():
        unique = sorted({r.stored: r for r in rows}.values(), key=lambda r: (r.label.lower(), r.stored))
        if len(unique) == 1:
            out.links.append(unique[0].link(phrase, how))
        else:
            out.choices.append(ValueChoice(dim, phrase, tuple((r.stored, r.label) for r in unique[:4])))
    return True


def link(question: str, rows: list[ValueRow], *, known: set[str],
         near: dict[str, list[tuple[ValueRow, float]]] | None = None, min_similarity: float = 0.85) -> Linking:
    """Link the words of a question to stored values (see the module notes for the order)."""
    ws = words(question)
    used = [False] * len(ws)
    out = Linking()
    exact: dict[str, list[tuple[ValueRow, str]]] = {}
    for r in rows:
        for n in r.names:
            exact.setdefault(n, []).append((r, "exact"))
        for s in r.synonyms:
            if s not in r.names:
                exact.setdefault(s, []).append((r, "synonym"))
    for size in range(min(MAX_PHRASE, len(ws)), 0, -1):
        for i, phrase in _windows(ws, used, size):
            if any(used[i:i + size]) or phrase in STOP or phrase.isdigit():
                continue
            hits = exact.get(phrase)
            if hits:
                best = "exact" if any(how == "exact" for _, how in hits) else "synonym"
                for r in {r.stored + "\x00" + r.dimension: r for r, how in hits if how == best}.values():
                    out.links.append(r.link(phrase, best))
                used[i:i + size] = [True] * size

    def unknown(i: int) -> bool:
        return not used[i] and ws[i] not in known and ws[i] not in STOP and len(ws[i]) >= 3 and not ws[i].isdigit()

    # A word that is part of a longer, name-like value ("lina" of "lina farouk"; "flex" of "network flex"); never a
    # word of a sentence, never a word many values hold (US-214), never a number word ("one" of "network one") and
    # never a short word of a synonym ("out" of "close out"; "uae" of the name "gcc uae" stays) (BUG-025).
    for i, w in enumerate(ws):
        if not unknown(i) or w in NUMBER_WORDS:
            continue
        found: dict[str, list[ValueRow]] = {}
        holding = 0
        for r in rows:
            names = (*r.names, *r.synonyms)
            if any(w in n.split() for n in names):
                holding += 1
            if any(1 < len(n.split()) <= NAME_WORDS and w in n.split() for n in names) and (
                    len(w) >= PARTIAL_MIN_LETTERS or any(w in n.split() for n in r.names)):
                found.setdefault(r.dimension, []).append(r)
        if holding > PARTIAL_MAX_VALUES:
            continue
        if _group(found, w, "partial", out):
            used[i] = True
    # Spelling slips, on phrases of unknown words ("jordon", "magnatti", "franco phone" is not attempted).
    for size in (2, 1):
        for i, phrase in _windows(ws, used, size):
            if not all(unknown(j) for j in range(i, i + size)):
                continue
            found = {}
            for r in rows:
                if any(close_spelling(phrase, n) for n in (*r.names, *r.synonyms)):
                    found.setdefault(r.dimension, []).append(r)
            if _group(found, phrase, "spelling", out):
                used[i:i + size] = [True] * size
    # Meaning: runs of words the model doesn't know, matched by embedding (computed by the caller).
    runs: list[str] = []
    run: list[str] = []
    for i, w in enumerate([*ws, ""]):
        if i < len(ws) and unknown(i):
            run.append(w)
        elif run:
            runs.append(" ".join(run[:MAX_PHRASE]))
            run = []
    for phrase in runs:
        closest = max((near or {}).get(phrase, []), key=lambda x: x[1], default=None)
        if closest is not None and closest[1] >= min_similarity:
            out.links.append(closest[0].link(phrase, "meaning", round(SCORES["meaning"] * closest[1], 3)))
        else:
            out.unmatched.append(phrase)
    return out


# --------------------------------------------------------------------------- pack synonyms


class PackValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: list[str] = Field(min_length=1)
    value: str = Field(min_length=1, max_length=500)
    synonyms: list[str] = Field(min_length=1)


class PackValuesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    status: str = "draft"
    values: list[PackValue] = Field(default_factory=list)


def load_pack_synonyms(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> list[PackValue]:
    """The pack's value synonyms (a key reads its folder; a source reads where it is kept, US-230)."""
    src = resolve(pack, packs_dir)
    data = src.document("value_synonyms") if src else None
    return PackValuesFile.model_validate(data).values if data is not None else []


def pack_synonym_map(entries: list[PackValue], snap: ModelSnapshot) -> dict[tuple[str, str], list[str]]:
    """(dimension, stored value) → synonyms, for the dimensions the model has."""
    have = {d.key for d in snap.dimensions}
    out: dict[tuple[str, str], list[str]] = {}
    for e in entries:
        for d in e.dimensions:
            if d in have:
                out.setdefault((d, e.value), []).extend(e.synonyms)
    return out


def indexable(snap: ModelSnapshot) -> list[Dimension]:
    """Text dimensions whose values are linked: not computed, not temporal, not sensitive, ids or contact details."""
    out = []
    for d in snap.dimensions:
        ent = snap.entity(d.entity)
        f = ent.field(d.field) if ent else None
        if d.is_temporal or d.of is not None or f is None or f.data_type != "text" or not f.is_queryable:
            continue
        if f.sensitivity in SKIP_SENSITIVITY or (f.semantic_type or "") in SKIP_SEMANTIC:
            continue
        out.append(d)
    return out


def embeddable(snap: ModelSnapshot, d: Dimension) -> bool:
    ent = snap.entity(d.entity)
    f = ent.field(d.field) if ent else None
    return f is not None and f.sensitivity != "personal" and (f.semantic_type or "") not in NO_EMBED_SEMANTIC


def value_text(d: Dimension, label: str) -> str:
    return f"{d.label}: {label}"


# --------------------------------------------------------------------------- index refresh


async def seed_pack_synonyms(conn: AsyncConnection, tenant_id: str, connection_id: str, snap: ModelSnapshot,
                             entries: list[PackValue]) -> int:
    """Add the pack's synonyms as pending review; one already there keeps its review."""
    added = 0
    for (dim, stored), syns in pack_synonym_map(entries, snap).items():
        for s in dict.fromkeys(syns):
            n = norm(s)
            if not n:
                continue
            row = await fetch_one(
                conn, "INSERT INTO rag.value_synonyms (id, tenant_id, connection_id, dimension, stored_value, synonym, "
                      "normalised, source) VALUES (:id, :t, :c, :d, :v, :s, :n, 'pack') ON CONFLICT (connection_id, "
                      "dimension, stored_value, normalised) DO NOTHING RETURNING id",
                {"id": new_id(), "t": tenant_id, "c": connection_id, "d": dim, "v": stored, "s": s[:120], "n": n[:120]})
            added += row is not None
    return added


async def sync_synonyms(conn: AsyncConnection, connection_id: str) -> None:
    """Copy the approved synonyms, and the display label when it differs (Yellow is shown as Amber), onto the
    indexed values, normalised as ``norm`` does."""
    await conn.execute(text(
        "UPDATE rag.dimension_values v SET synonyms = ARRAY(SELECT x FROM (SELECT s.normalised AS x FROM "
        "rag.value_synonyms s WHERE s.connection_id = v.connection_id AND s.dimension = v.dimension AND "
        "s.stored_value = v.stored_value AND s.status = 'approved' UNION SELECT trim(regexp_replace(lower(v.label), "
        "'[^a-z0-9]+', ' ', 'g')) WHERE v.label <> v.stored_value) u WHERE x <> '' AND x <> v.normalised ORDER BY x) "
        "WHERE v.connection_id = CAST(:c AS uuid)"), {"c": connection_id})


async def refresh_values(conn: AsyncConnection, *, service: Any, gateway: GatewayClient | None, principal: Principal,
                         snap: ModelSnapshot, connection_id: str, packs_dir: str | None = None) -> dict[str, Any]:
    """Rebuild the index of one current model version: pack synonyms, distinct values, embeddings, synonyms.

    A dimension whose values can't be read now (source down) keeps what it had; dimensions the version no longer
    has, or that now have too many values, are dropped.
    """
    settings = get_settings()
    version = await fetch_one(conn, "SELECT data_model_id FROM model.model_versions WHERE id = CAST(:v AS uuid)",
                              {"v": snap.model_version_id})
    pack = await pack_source_for(conn, version["data_model_id"], packs_dir) if version else None
    seeded = await seed_pack_synonyms(conn, principal.tenant_id, connection_id, snap,
                                      load_pack_synonyms(pack)) if pack else 0
    out: dict[str, Any] = {"dimensions": 0, "values": 0, "skipped": [], "free_text": [], "failed": [],
                           "pack_synonyms_added": seeded}
    previous = {(r["dimension"], r["stored_value"], r["label"]): (r["embedding"], r["embedding_model"])
                for r in await fetch_all(conn, "SELECT dimension, stored_value, label, embedding::text AS embedding, "
                                               "embedding_model FROM rag.dimension_values WHERE connection_id = "
                                               "CAST(:c AS uuid) AND embedding IS NOT NULL", {"c": connection_id})}
    fresh: list[tuple[Dimension, str, str]] = []
    kept: list[str] = []  # dimensions whose rows stay: refreshed now, or unreadable now (they keep the old ones)
    for d in indexable(snap):
        try:
            got = await service.index_values(snapshot=snap, connection_id=connection_id, ref=f"dimension:{d.key}",
                                             principal=principal, limit=settings.value_index_max_values + 1)
        except TelaError as exc:
            out["failed"].append({"dimension": d.key, "code": str(exc.code)})
            kept.append(d.key)
            continue
        if len(got) > settings.value_index_max_values:
            out["skipped"].append(d.key)
            continue
        if free_text_values([str(v) for v in got if v is not None]):  # US-214: sentences, not categories
            out["free_text"].append(d.key)
            continue
        await conn.execute(text("DELETE FROM rag.dimension_values WHERE connection_id = CAST(:c AS uuid) AND "
                                "dimension = :d"), {"c": connection_id, "d": d.key})
        kept.append(d.key)
        out["dimensions"] += 1
        for raw in dict.fromkeys(str(v)[:500] for v in got if v is not None and str(v).strip()):
            if norm(raw):
                fresh.append((d, raw, str(d.value_labels.get(raw, raw))[:500]))
    await conn.execute(text("DELETE FROM rag.dimension_values WHERE connection_id = CAST(:c AS uuid) AND "
                            "NOT dimension = ANY(CAST(:kept AS text[]))"), {"c": connection_id, "kept": kept})
    todo = [i for i, (d, raw, label) in enumerate(fresh)
            if embeddable(snap, d) and (d.key, raw, label) not in previous]
    vectors, model = await embed(gateway, principal.tenant_id, [value_text(fresh[i][0], fresh[i][2]) for i in todo])
    new_vec = dict(zip(todo, vectors, strict=True)) if vectors is not None else {}
    for i, (d, raw, label) in enumerate(fresh):
        vec, vmodel = (json.dumps(new_vec[i]), model) if i in new_vec else (
            previous.get((d.key, raw, label), (None, None)) if embeddable(snap, d) else (None, None))
        await conn.execute(
            text("INSERT INTO rag.dimension_values (id, tenant_id, connection_id, model_version_id, dimension, "
                 "stored_value, label, normalised, embedding, embedding_model) VALUES (:id, :t, :c, :v, :d, :s, :l, "
                 ":n, CAST(:e AS vector), :m)"),
            {"id": new_id(), "t": principal.tenant_id, "c": connection_id, "v": snap.model_version_id, "d": d.key,
             "s": raw, "l": label, "n": norm(raw)[:500], "e": vec, "m": vmodel})
    out["values"] = len(fresh)
    out["embedded"] = len(new_vec)
    await sync_synonyms(conn, connection_id)
    return out


# --------------------------------------------------------------------------- linking (database)


async def candidates(conn: AsyncConnection, connection_id: str, dims: list[str], question: str) -> list[ValueRow]:
    """Indexed values that could be named in the question: same words, a synonym, or a similar spelling."""
    ws = [w for w in words(question) if w not in STOP]
    grams = list(dict.fromkeys(" ".join(ws[i:i + n]) for n in range(1, MAX_PHRASE + 1)
                               for i in range(len(ws) - n + 1)))[:200]
    if not grams or not dims:
        return []
    rows = await fetch_all(
        conn, "SELECT dimension, stored_value, label, synonyms FROM rag.dimension_values WHERE connection_id = "
              "CAST(:c AS uuid) AND dimension = ANY(CAST(:d AS text[])) AND (normalised = ANY(CAST(:g AS text[])) "
              "OR synonyms && CAST(:g AS text[]) OR string_to_array(normalised, ' ') && CAST(:w AS text[]) "
              "OR EXISTS (SELECT 1 FROM unnest(synonyms) s WHERE string_to_array(s, ' ') && CAST(:w AS text[])) "
              "OR normalised % ANY(CAST(:w AS text[])) "
              "OR EXISTS (SELECT 1 FROM unnest(synonyms) s WHERE s % ANY(CAST(:w AS text[])))) LIMIT 400",
        {"c": connection_id, "d": dims, "g": grams, "w": ws})
    return [ValueRow(r["dimension"], r["stored_value"], r["label"], tuple(r["synonyms"] or ())) for r in rows]


async def nearest(conn: AsyncConnection, gateway: GatewayClient | None, tenant_id: str, connection_id: str,
                  dims: list[str], phrases: list[str]) -> dict[str, list[tuple[ValueRow, float]]]:
    """The closest indexed values by meaning for each phrase (one embedding call for all of them)."""
    if gateway is None or not phrases or not dims:
        return {}
    have = await fetch_one(conn, "SELECT 1 AS ok FROM rag.dimension_values WHERE connection_id = CAST(:c AS uuid) "
                                 "AND embedding IS NOT NULL LIMIT 1", {"c": connection_id})
    if have is None:
        return {}
    vectors, _ = await embed(gateway, tenant_id, phrases[:5])
    if vectors is None:
        return {}
    out: dict[str, list[tuple[ValueRow, float]]] = {}
    for phrase, vec in zip(phrases[:5], vectors, strict=True):
        rows = await fetch_all(
            conn, "SELECT dimension, stored_value, label, synonyms, 1 - (embedding <=> CAST(:e AS vector)) AS sim "
                  "FROM rag.dimension_values WHERE connection_id = CAST(:c AS uuid) AND dimension = "
                  "ANY(CAST(:d AS text[])) AND embedding IS NOT NULL ORDER BY embedding <=> CAST(:e AS vector) LIMIT 3",
            {"c": connection_id, "d": dims, "e": json.dumps(vec)})
        out[phrase] = [(ValueRow(r["dimension"], r["stored_value"], r["label"], tuple(r["synonyms"] or ())),
                        float(r["sim"])) for r in rows]
    return out


async def link_question(conn: AsyncConnection, gateway: GatewayClient | None, tenant_id: str, question: str,
                        snap: ModelSnapshot, connection_id: str, dims: list[Dimension]) -> Linking:
    """Links over the given dimensions (already limited to what the person may see); values are not yet
    checked against the person's row scope (see ``Orchestrator._linked``)."""
    keys = [d.key for d in dims]
    known = known_words(snap)
    rows = await candidates(conn, connection_id, keys, question)
    first = link(question, rows, known=known)
    if not first.unmatched:
        return first
    near = await nearest(conn, gateway, tenant_id, connection_id, keys, first.unmatched)
    return link(question, rows, known=known, near=near, min_similarity=get_settings().value_link_vector_min)


async def propose_synonym(conn: AsyncConnection, principal: Principal, connection_id: str, dimension: str,
                          stored: str, phrase: str, *, source: str = "clarification") -> bool:
    """A clarification choice ("network" → Network One), or an approved answer's own words for a value (``answer``,
    US-175), as a synonym waiting for Admin review."""
    n = norm(phrase)
    if not n or n == norm(stored):
        return False
    row = await fetch_one(
        conn, "INSERT INTO rag.value_synonyms (id, tenant_id, connection_id, dimension, stored_value, synonym, "
              "normalised, source, proposed_by) VALUES (:id, :t, :c, :d, :v, :s, :n, :src, :u) ON CONFLICT "
              "(connection_id, dimension, stored_value, normalised) DO NOTHING RETURNING id",
        {"id": new_id(), "t": principal.tenant_id, "c": connection_id, "d": dimension, "v": stored, "s": phrase[:120],
         "n": n[:120], "u": principal.user_id, "src": source})
    return row is not None


# --------------------------------------------------------------------------- Admin review (FEAT-014)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
internal = APIRouter(prefix="/api/v1/internal/knowledge", tags=["internal"], include_in_schema=False)


async def _labels(conn: AsyncConnection, tenant_id: str, connection_id: str) -> dict[str, str]:
    from tela_api.model.store import load_snapshot

    row = await fetch_one(conn, "SELECT current_version_id FROM model.data_models WHERE connection_id = "
                                "CAST(:c AS uuid) AND current_version_id IS NOT NULL", {"c": connection_id})
    if row is None:
        return {}
    snap = await load_snapshot(conn, tenant_id, str(row["current_version_id"]))
    return {d.key: d.label for d in snap.dimensions}


@router.get("/value-synonyms")
async def list_synonyms(connection_id: str = Query(max_length=64),
                        status: Literal["pending_review", "approved", "rejected"] | None = None,
                        principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT s.id, s.dimension, s.stored_value, s.synonym, s.source, s.status, s.note, s.created_at, "
                  "s.reviewed_at, p.display_name AS proposed_by, r.display_name AS reviewed_by, EXISTS (SELECT 1 FROM "
                  "rag.dimension_values v WHERE v.connection_id = s.connection_id AND v.dimension = s.dimension AND "
                  "v.stored_value = s.stored_value) AS in_data FROM rag.value_synonyms s LEFT JOIN iam.users p ON "
                  "p.id = s.proposed_by LEFT JOIN iam.users r ON r.id = s.reviewed_by WHERE s.connection_id = "
                  "CAST(:c AS uuid) AND (CAST(:st AS text) IS NULL OR s.status = :st) ORDER BY s.status = "
                  "'pending_review' DESC, s.dimension, s.stored_value, s.synonym LIMIT 2000",
            {"c": connection_id, "st": status})
        labels = await _labels(conn, principal.tenant_id, connection_id)
        counts = await fetch_all(conn, "SELECT status, count(*) AS n FROM rag.value_synonyms WHERE connection_id = "
                                       "CAST(:c AS uuid) GROUP BY status", {"c": connection_id})
        indexed = await fetch_one(conn, "SELECT count(*) AS n, count(DISTINCT dimension) AS d, max(refreshed_at) AS "
                                        "at FROM rag.dimension_values WHERE connection_id = CAST(:c AS uuid)",
                                  {"c": connection_id})
    return {"items": [{**{k: (str(v) if k == "id" else v) for k, v in r.items()},
                       "dimension_label": labels.get(r["dimension"], r["dimension"])} for r in rows],
            "counts": {r["status"]: r["n"] for r in counts},
            "index": {"values": indexed["n"] if indexed else 0, "dimensions": indexed["d"] if indexed else 0,
                      "refreshed_at": indexed["at"] if indexed else None}}


@router.get("/values")
async def list_values(connection_id: str = Query(max_length=64),
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """The indexed values of a source by dimension, for an Admin adding a synonym."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, "SELECT dimension, stored_value, label, synonyms FROM rag.dimension_values WHERE "
                                     "connection_id = CAST(:c AS uuid) ORDER BY dimension, lower(label)",
                               {"c": connection_id})
        labels = await _labels(conn, principal.tenant_id, connection_id)
    dims: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dims.setdefault(r["dimension"], {"key": r["dimension"], "label": labels.get(r["dimension"], r["dimension"]),
                                             "values": []})
        d["values"].append({"stored": r["stored_value"], "label": r["label"], "synonyms": list(r["synonyms"] or [])})
    return {"dimensions": sorted(dims.values(), key=lambda d: d["label"].lower())}


class ValueSynonymIn(BaseModel):
    connection_id: str = Field(max_length=64)
    dimension: str = Field(min_length=1, max_length=128)
    stored_value: str = Field(min_length=1, max_length=500)
    synonym: str = Field(min_length=1, max_length=120)


@router.post("/value-synonyms", status_code=201)
async def add_synonym(body: ValueSynonymIn, request: Request,
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """An Admin's own synonym: approved as it is added (the Admin is the reviewer)."""
    n = norm(body.synonym)
    if not n:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Type a word or phrase.")
    async with tenant_tx(principal.tenant_id) as conn:
        value = await fetch_one(conn, "SELECT label FROM rag.dimension_values WHERE connection_id = CAST(:c AS uuid) "
                                      "AND dimension = :d AND stored_value = :v LIMIT 1",
                                {"c": body.connection_id, "d": body.dimension, "v": body.stored_value})
        if value is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That value is not in the value index for this source.")
        if n == norm(body.stored_value) or n == norm(value["label"]):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That is the value itself; it already links.")
        row = await fetch_one(
            conn, "INSERT INTO rag.value_synonyms (id, tenant_id, connection_id, dimension, stored_value, synonym, "
                  "normalised, source, status, proposed_by, reviewed_by, reviewed_at) VALUES (:id, :t, :c, :d, :v, :s, "
                  ":n, 'admin', 'approved', :u, :u, now()) ON CONFLICT (connection_id, dimension, stored_value, "
                  "normalised) DO UPDATE SET status = 'approved', reviewed_by = :u, reviewed_at = now() RETURNING id",
            {"id": new_id(), "t": principal.tenant_id, "c": body.connection_id, "d": body.dimension,
             "v": body.stored_value, "s": body.synonym.strip(), "n": n[:120], "u": principal.user_id})
        assert row is not None
        await sync_synonyms(conn, body.connection_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.value_synonym_added",
                           object_type="value_synonym", object_id=str(row["id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"dimension": body.dimension, "stored_value": body.stored_value, "synonym": n})
    return {"id": str(row["id"]), "status": "approved"}


class SynonymReviewIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=300)


@router.post("/value-synonyms/review")
async def review_synonyms(body: SynonymReviewIn, request: Request,
                          principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    status = "approved" if body.decision == "approve" else "rejected"
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "UPDATE rag.value_synonyms SET status = :s, reviewed_by = :u, reviewed_at = now(), note = :n "
                  "WHERE id = ANY(CAST(:ids AS uuid[])) RETURNING id, connection_id",
            {"s": status, "u": principal.user_id, "n": body.note, "ids": body.ids})
        if not rows:
            raise TelaError(ErrorCode.NOT_FOUND, "Those synonyms were not found.")
        for c in {str(r["connection_id"]) for r in rows}:
            await sync_synonyms(conn, c)
        await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.value_synonyms_reviewed",
                           object_type="value_synonym", object_id=str(rows[0]["id"]), outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"decision": status, "count": len(rows), "note": body.note})
    return {"updated": len(rows), "status": status}


class PackApproveIn(BaseModel):
    connection_id: str = Field(max_length=64)


@router.post("/value-synonyms/approve-pack")
async def approve_pack_synonyms(body: PackApproveIn, request: Request,
                                principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """Approve every pack synonym of a source still waiting for review (US-215); clarification and answer
    synonyms keep their own review, and rejected ones stay rejected."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "UPDATE rag.value_synonyms SET status = 'approved', reviewed_by = :u, reviewed_at = now() WHERE "
                  "connection_id = CAST(:c AS uuid) AND source = 'pack' AND status = 'pending_review' RETURNING id",
            {"u": principal.user_id, "c": body.connection_id})
        if rows:
            await sync_synonyms(conn, body.connection_id)
            await audit.record(conn, tenant_id=principal.tenant_id, action="knowledge.value_synonyms_reviewed",
                               object_type="value_synonym", object_id=str(rows[0]["id"]), outcome="success",
                               actor_id=principal.user_id, correlation_id=_rid(request),
                               details={"decision": "approved", "count": len(rows), "source": "pack",
                                        "connection_id": body.connection_id})
    return {"updated": len(rows), "status": "approved"}


class ValueLinkIn(BaseModel):
    connection_id: str = Field(max_length=64)
    text: str = Field(min_length=1, max_length=500)


@router.post("/values/link")
async def try_link(body: ValueLinkIn, request: Request,
                   principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """What a phrase links to on this source, over every indexed value (an Admin's check; no row scope)."""
    from tela_api.model.store import load_snapshot

    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(conn, "SELECT current_version_id FROM model.data_models WHERE connection_id = "
                                    "CAST(:c AS uuid) AND current_version_id IS NOT NULL", {"c": body.connection_id})
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "This source has no approved model.")
        snap = await load_snapshot(conn, principal.tenant_id, str(row["current_version_id"]))
        out = await link_question(conn, getattr(request.app.state, "gateway", None), principal.tenant_id, body.text,
                                  snap, body.connection_id, indexable(snap))
    return out.public()


def rows_for(snap: ModelSnapshot, values: dict[str, list[str]],
             synonyms: dict[tuple[str, str], list[str]] | None = None) -> list[ValueRow]:
    """Index rows built in memory (offline harnesses): stored values per dimension, approved synonyms."""
    out = []
    for d in indexable(snap):
        if free_text_values(values.get(d.key, [])):  # as a refresh skips them (US-214)
            continue
        for raw in values.get(d.key, []):
            label = d.value_labels.get(raw, raw)
            syns = {norm(s) for s in (synonyms or {}).get((d.key, raw), [])}
            if label != raw:
                syns.add(norm(label))
            out.append(ValueRow(d.key, raw, label, tuple(sorted(s for s in syns if s))))
    return out


# --------------------------------------------------------------------------- refresh (worker-facing)


class TenantIn(BaseModel):
    tenant_id: str = Field(max_length=64)


async def refresh_version(request: Request, tenant_id: str, version_id: str) -> dict[str, Any]:
    """Refresh the index of one approved model version when it is its connection's current one, reading as the
    person who approved it (recorded on the reads; their own scope is not applied)."""
    from tela_api.model.store import load_snapshot

    async with tenant_tx(tenant_id) as conn:
        row = await fetch_one(conn, "SELECT v.approved_by, m.connection_id, m.current_version_id FROM "
                                    "model.model_versions v JOIN model.data_models m ON m.id = v.data_model_id WHERE "
                                    "v.id = CAST(:v AS uuid)", {"v": version_id})
        if row is None or str(row["current_version_id"] or "") != version_id:
            return {"status": "not_current"}
        principal = await principal_for_user(conn, tenant_id, str(row["approved_by"])) if row["approved_by"] \
            else None
        if principal is None:
            log.info("knowledge.values_no_reader", version_id=version_id)
            return {"status": "no_reader"}
        snap = await load_snapshot(conn, tenant_id, version_id)
        out = await refresh_values(conn, service=request.app.state.query_service,
                                   gateway=getattr(request.app.state, "gateway", None), principal=principal,
                                   snap=snap, connection_id=str(row["connection_id"]),
                                   packs_dir=request.app.state.settings.packs_dir)
        await audit.record(conn, tenant_id=tenant_id, action="knowledge.values_indexed", object_type="model_version",
                           object_id=version_id, outcome="success" if not out["failed"] else "failure",
                           actor_id=None, correlation_id=_rid(request), details=out)
    return {"status": "refreshed", **out}


@internal.post("/model-versions/{version_id}/values")
async def refresh_on_approval(version_id: str, body: TenantIn, request: Request,
                              _: dict[str, Any] = Depends(require_worker)) -> dict[str, Any]:
    return await refresh_version(request, body.tenant_id, version_id)


@internal.post("/values/nightly")
async def refresh_nightly(request: Request, _: dict[str, Any] = Depends(require_worker)) -> dict[str, int]:
    async with tenant_tx(None) as conn:
        tenants = [str(r["tenant_id"]) for r in await fetch_all(conn, "SELECT tenant_id FROM rag.f_value_tenants()")]
    done = 0
    for t in tenants:
        async with tenant_tx(t) as conn:
            versions = [str(r["current_version_id"]) for r in await fetch_all(
                conn, "SELECT m.current_version_id FROM model.data_models m JOIN conn.connections c ON c.id = "
                      "m.connection_id WHERE m.current_version_id IS NOT NULL AND c.deleted_at IS NULL", {})]
        for v in versions:
            try:
                out = await refresh_version(request, t, v)
            except TelaError as exc:  # one source's model must not stop the others
                log.info("knowledge.values_refresh_failed", version_id=v, code=str(exc.code))
                continue
            done += out["status"] == "refreshed"
    return {"tenants": len(tenants), "refreshed": done}
