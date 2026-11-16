"""Model builder: the columns an Admin picks, the documents they add, a model the LLM writes, a preview to approve.

Flow (Add connection → Choose columns → Knowledge → Generate):

* ``GET  /connections/{id}/model/selection`` lists every readable table and column, the columns the current model
  uses (the starting selection) and the knowledge documents that can be given to the builder.
* ``POST /connections/{id}/model/generate`` starts a job (two API workers, so its state lives in Redis) and
  returns its id; ``GET /model-jobs/{job_id}`` reports the steps and, when done, the preview.

What the job does, token-lean and checked:

1. **Selection.** A connection without a model gets a baseline of the picked tables (declared foreign keys
   accepted). A connection with a model is merged by source table and column: picked tables and columns are added,
   unpicked columns are hidden from the planners and the LLM (``is_queryable`` off) unless a key, link, measure,
   breakdown or rule needs them, so measures and data rules keep working. Columns that hold no value in any row
   (counted, never read) are hidden the same way.
2. **Descriptions (LLM).** Tables, and the columns the documents mention (all picked columns, at most
   ``NO_DOC_COLUMNS`` per table, when there are no documents), get a label and a one-line description. Each call
   sees only the document lines that name its columns.
3. **Business rules (LLM).** Document sections that are not a column dictionary are read in parts; every rule
   becomes a term, synonyms, a meaning and a condition on the listed columns. With no documents the LLM may only
   infer rules the schema itself shows (marked ``inferred``).
4. **Checks.** Each rule's SQL must parse and name only columns of its table; a column the rule needs is added or
   un-hidden; anything else is kept for review (``needs_review``) and never reaches Direct SQL. The model's
   business meaning therefore lives in the approved model, not in code.
5. The draft is saved; the Admin previews it, sets the data date and approves it in the usual way.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlglot import exp, parse_one

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.llm.client import AgentFailed, run_agent
from tela_api.model.catalog import _label as auto_label
from tela_api.model.profile import apply_profile, infer_links, profile_entity
from tela_api.model.catalog import baseline_definition, extend_columns, extend_definition
from tela_api.model.router import _model, _readable_objects, _snapshot_or_issues, ensure_draft, insert_questions
from tela_api.content.sql_context import DIALECT_RULES, sql_name, sql_object
from tela_api.content.sql_context import _terms as term_words
from tela_api.query.compiler import DIALECTS
from tela_api.query.service import QueryContext
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["model-builder"])

AGENT = "AG-11"
GATEWAY_TASK = "model_assist"  # the gateway's allowed task for work on a data model (as the model assistant)
JOB_TTL_S = 24 * 3600
NO_DOC_COLUMNS = 40  # columns per table described when no document is given (the rest keep their schema label)
DESCRIBE_COLUMNS = 110  # columns per describe call
EXCERPT_CHARS = 14_000  # document lines sent with one describe call
RULES_CHARS = 7_000  # document text per rules call (smaller parts: the model lists every rule of a part)
RULE_PASSES = (7_000, 4_500)  # part sizes of the two rule passes: other boundaries make the second read fresh
PROFILE_COLUMNS = 150  # columns counted per profile query
SCHEMA_CHARS = 24_000  # schema listed in a rules call
MAX_TOKENS = 8000
BACKOFF_S = (15, 30, 45)  # waits after a rate limit before the next try
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DATA_DATE = "{data_date}"
_LOCAL_JOBS: dict[str, dict[str, Any]] = {}  # used only when Redis is not configured (tests, single worker)


# --------------------------------------------------------------------------- LLM shapes


class FieldDesc(BaseModel):
    column: str
    label: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=240)


class TableDesc(BaseModel):
    object: str
    label: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=300)
    name_column: str = ""  # the column holding each record's name as people refer to it
    fields: list[FieldDesc] = Field(default_factory=list)


class DescribeOut(BaseModel):
    tables: list[TableDesc] = Field(default_factory=list)


class RuleIn(BaseModel):
    term: str = Field(max_length=120)
    synonyms: list[str] = Field(default_factory=list)
    meaning: str = Field(default="", max_length=600)
    table: str | None = None
    sql: str | None = None
    kind: str = "condition"  # condition | expression | population | display
    always: bool = False
    cite: str | None = None
    overridden: bool = False  # the document says another definition of this term wins: dropped by the builder


class RulesOut(BaseModel):
    rules: list[RuleIn] = Field(default_factory=list)


DESCRIBE_SYSTEM = (
    "You document a database for business users. For each table below give a short label (2-4 words) and a one-line "
    "description of what one row is. For each listed column give a short label people read and a description of at "
    "most 20 words: what it holds and, when the document says so, how it is used (for example that it is the record's "
    "name, the label of a code column, a date in UTC, an amount in a currency, or that it is empty or must not be "
    "used). Also give name_column: the listed column that holds each record's name or title as people refer to it "
    "(follow the document when it says which column a record's name is; empty when unsure). Take meanings from the "
    "document lines when they are given; otherwise describe only what the column name and type plainly show. Never guess business meaning, never invent values, and return only columns listed. "
    "Write object and column names exactly as listed.")

RULES_SYSTEM = (
    "You turn an organisation's documented business rules into conditions on its database. Return one rule for each "
    "definition the document gives: a business term or population (what 'open', 'active', 'late' or 'stale' means), "
    "a status grouping, a default scope or exclusion, a calculation, a band, a precedence between sources, or which "
    "column stands for a term people use (then sql is just that column name). For each rule: term (the words people use); synonyms (other phrasings a "
    "person may ask with, including singular/plural and the phrases the document lists); meaning (one or two "
    "sentences, keeping the document's exact values and exceptions); table (the qualified table it is written on, "
    "exactly as listed, or null when it is not about one table); sql (a SQL boolean condition or expression using "
    "ONLY column names listed for that table, written exactly as listed, no table prefix, text values exactly as the "
    "document writes them, blanks handled as the document says; write the bare placeholder {data_date} (no quotes "
    "around it: it is replaced by a quoted date) where the rule means today or the data date, and write any other "
    "date the document gives relative to the data date as an expression on {data_date} (for example the first day of "
    "its year), never a fixed calendar date the document does not state; use only functions of the SQL dialect "
    "named below; null when the listed columns cannot express it); kind: 'condition' for a filter that defines the "
    "term, 'expression' for a calculation or a column a term stands for, 'population' for what plain words mean when "
    "a question names records without a status of its own (the document says, for example, that 'how many X', "
    "'our X' or 'X right now' means open X: give the open condition, kind 'population', one per table), 'display' "
    "for how a column's values are shown (a stored value shown under another word, how blanks are labelled, an order "
    "to sort in: sql is the expression to select instead of the bare column, such as a CASE or COALESCE); always "
    "(true for a default scope or exclusion the document says applies before every count on that table, including "
    "records the document says to leave out of counts); cite (the section heading). Return every such definition "
    "the part contains, also short ones in tables and lists. When the document says a measure or term counts only "
    "certain records (for example 'this measure counts Active projects only'), put that condition into the sql of "
    "that measure's or term's own rule as well, so the rule carries its records with it. When the "
    "document says one part or source wins over another and both define the same term, return only the winning "
    "definition; if you do return a definition the document says loses (it is overridden by another part or "
    "source), set overridden to true. Do not invent rules, columns or values the document does "
    "not state.")

INFER_SYSTEM = (
    "You read a database schema with no business documents. Return only rules the schema itself makes certain, for "
    "example which text column holds the label of a code column, or which column is a record's name. Do not guess "
    "business meanings such as what 'open' or 'late' means: when unsure return nothing. Use the same fields as for "
    "documented rules; table and column names exactly as listed.")


# --------------------------------------------------------------------------- job state


async def _put(request_app: Any, job_id: str, state: dict[str, Any]) -> None:
    redis = getattr(request_app.state, "redis", None)
    if redis is None:
        _LOCAL_JOBS[job_id] = state
        return
    await redis.set(f"tela:modelgen:{job_id}", json.dumps(state, default=str), ex=JOB_TTL_S)


async def _get(request_app: Any, job_id: str) -> dict[str, Any] | None:
    redis = getattr(request_app.state, "redis", None)
    if redis is None:
        return _LOCAL_JOBS.get(job_id)
    raw = await redis.get(f"tela:modelgen:{job_id}")
    return json.loads(raw) if raw else None


# --------------------------------------------------------------------------- helpers


def _bare(name: str) -> str:
    return name.rpartition(".")[2]


def _key(name: str) -> str:
    key = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "rule"
    return key if re.match(r"^[a-z_]", key) else f"r_{key}"


def _twins(cols: list[str]) -> set[str]:
    """Code columns whose text twin (``x`` / ``xname``) is also present: listed once, as the text column."""
    low = {c.lower() for c in cols}
    return {c for c in cols if c.lower() + "name" in low}


def _doc_lines(docs: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(document title, line) for every non-empty line of the given documents."""
    out: list[tuple[str, str]] = []
    for d in docs:
        for line in d["text"].splitlines():
            if line.strip():
                out.append((d["title"], line.strip()))
    return out


def _sections(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """(heading, text) for each markdown section of a document."""
    out: list[tuple[str, str]] = []
    head, buf = doc["title"], []
    for line in doc["text"].splitlines():
        if re.match(r"^#{1,6}\s", line):
            if any(x.strip() for x in buf):
                out.append((head, "\n".join(buf)))
            head, buf = line.lstrip("#").strip(), [line]
        else:
            buf.append(line)
    if any(x.strip() for x in buf):
        out.append((head, "\n".join(buf)))
    return out


def _dictionary(section: str, columns: set[str]) -> bool:
    """A column dictionary section: most of its lines are table rows whose first cell is just a known column name.
    Those lines describe columns (the describe step reads them), not rules."""
    lines = [x.strip() for x in section.splitlines() if x.strip() and not x.strip().startswith("#")]
    if len(lines) < 4:
        return False
    rows = 0
    for x in lines:
        if x.startswith("|"):
            first = x.strip("|").split("|", 1)[0].strip().strip("`").strip()
            if IDENT.match(first) and first.lower() in columns:
                rows += 1
    return rows >= 0.6 * len(lines)


def _rule_parts(docs: list[dict[str, Any]], columns: set[str], size: int = RULES_CHARS) -> list[str]:
    """Document text for the rules step, in parts of at most ``size``, column dictionaries left out."""
    RULES_CHARS = size  # noqa: N806 - the part size of this pass
    parts: list[str] = []
    buf = ""
    for d in docs:
        for head, body in _sections(d):
            if _dictionary(body, columns):
                continue
            block = f"[{d['title']} · {head}]\n{body.strip()}\n"
            while len(block) > RULES_CHARS:  # a very long section on its own, cut on line ends
                cut = block.rfind("\n", 0, RULES_CHARS)
                cut = cut if cut > 0 else RULES_CHARS
                if buf:
                    parts.append(buf)
                    buf = ""
                parts.append(block[:cut])
                block = block[cut:]
            if len(buf) + len(block) > RULES_CHARS and buf:
                parts.append(buf)
                buf = ""
            buf += block
    if buf.strip():
        parts.append(buf)
    return parts


async def _call[T: BaseModel](gateway: Any, model: type[T], principal: Principal, task: str, system: str,
                               user: str, job: _Job) -> T | None:
    """One builder call, retried after a rate limit; None when the model gave nothing usable."""
    for attempt in range(len(BACKOFF_S) + 1):
        try:
            out = await run_agent(gateway, model, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                  agent=AGENT, task=GATEWAY_TASK, system=system, user=user, max_tokens=MAX_TOKENS)
            job.calls += 1
            job.chars += len(system) + len(user)
            return out
        except AgentFailed as exc:
            job.calls += 1
            if exc.code in ("rate_limited", "planner_unavailable", "llm_timeout") and attempt < len(BACKOFF_S):
                await job.step(f"The model is busy ({exc.code}); trying again in {BACKOFF_S[attempt]} s")
                await asyncio.sleep(BACKOFF_S[attempt])
                continue
            await job.warn(f"{task}: the model gave no usable answer ({exc.code})")
            return None
    return None


class _Job:
    def __init__(self, app: Any, job_id: str, connection_id: str) -> None:
        self.app, self.id = app, job_id
        self.state: dict[str, Any] = {"job_id": job_id, "connection_id": connection_id, "status": "running",
                                      "steps": [], "warnings": [], "started_at": time.time()}
        self.calls = 0
        self.chars = 0

    async def step(self, msg: str) -> None:
        self.state["steps"].append({"at": round(time.time() - self.state["started_at"], 1), "text": msg})
        await _put(self.app, self.id, self.state)

    async def warn(self, msg: str) -> None:
        self.state["warnings"].append(msg)
        await _put(self.app, self.id, self.state)

    async def done(self, result: dict[str, Any]) -> None:
        self.state.update({"status": "done", "result": result,
                           "seconds": round(time.time() - self.state["started_at"], 1),
                           "llm_calls": self.calls, "chars_sent": self.chars})
        await _put(self.app, self.id, self.state)

    async def fail(self, msg: str) -> None:
        self.state.update({"status": "failed", "error": msg,
                           "seconds": round(time.time() - self.state["started_at"], 1)})
        await _put(self.app, self.id, self.state)


# --------------------------------------------------------------------------- selection


def _used_fields(definition: dict[str, Any], *, display: bool = True) -> dict[str, set[str]]:
    """Field keys per entity the model itself needs: keys, links, measures, breakdowns (and display columns)."""
    used: dict[str, set[str]] = {e["key"]: set(e.get("primary_key") or []) | (
        set(e.get("display_fields") or []) if display else set()) for e in definition.get("entities", [])}
    for ln in definition.get("links", []):
        used.setdefault(ln["from_entity"], set()).update(ln["from_fields"])
        used.setdefault(ln["to_entity"], set()).update(ln["to_fields"])
    for d in definition.get("dimensions", []):
        used.setdefault(d["entity"], set()).add(d["field"])
    for m in definition.get("measures", []):
        blob = json.dumps(m.get("expression") or {})
        for e in definition.get("entities", []):
            for f in e["fields"]:
                if f'"{f["key"]}"' in blob or f'.{f["key"]}"' in blob:
                    used.setdefault(m["entity"], set()).add(f["key"])
    return used


def apply_selection(definition: dict[str, Any], picked: dict[str, set[str]]) -> dict[str, int]:
    """Show picked columns to the planners and the LLM, hide the rest unless the model needs them (the query
    compiler only reads visible fields, so keys, links, measures and breakdowns keep theirs)."""
    used = _used_fields(definition)
    shown = hidden = 0
    for e in definition["entities"]:
        want = picked.get(e["source_object"].lower())
        if want is None:  # a table that was not picked keeps what it had
            continue
        for f in e["fields"]:
            keep = "*" in want or f["source_column"].lower() in want or f["key"] in used.get(e["key"], set())
            f["is_queryable"] = keep
            shown += keep
            hidden += not keep
    return {"shown": shown, "hidden": hidden}


# --------------------------------------------------------------------------- profile


async def empty_columns(app: Any, principal: Principal, connection_id: str, version_id: str | None,
                        definition: dict[str, Any], dialect: str | None, job: _Job) -> dict[str, set[str]]:
    """Source columns (lower case) per entity that hold no value in any row: counts only, through the scoped query
    path of the approved version (a column the approved version doesn't have is not counted). An always-empty
    column misleads the LLM (a "name" column with no names), so it is hidden and never offered as a record's name."""
    if version_id is None:
        return {}
    service = app.state.query_service
    out: dict[str, set[str]] = {}
    for e in definition["entities"]:
        cols = [f["source_column"] for f in e["fields"] if f.get("is_queryable", True)]
        for i in range(0, len(cols), PROFILE_COLUMNS):
            part = cols[i:i + PROFILE_COLUMNS]
            sql = ("SELECT COUNT(*) AS n, " + ", ".join(f"COUNT({sql_name(c, dialect)}) AS c{j}"
                                                        for j, c in enumerate(part))
                   + f" FROM {sql_object(e['source_object'], dialect)}")
            try:
                res = await service.execute_custom(sql, connection_id=connection_id, model_version_id=version_id,
                                                   principal=principal, ctx=QueryContext(surface="preview",
                                                                                         use_cache=False))
            except TelaError:  # a table or column the approved version can't read: not counted
                continue
            row = (res.get("rows") or [[None]])[0]
            if not row or not row[0]:
                continue  # an empty table says nothing about its columns
            empty = {c.lower() for c, v in zip(part, row[1:], strict=False) if v == 0}
            if empty:
                out.setdefault(e["key"], set()).update(empty)
    if out:
        await job.step(f"{sum(len(v) for v in out.values())} columns hold no value in any row: hidden from the model")
    return out


def hide_empty(definition: dict[str, Any], empty: dict[str, set[str]]) -> None:
    """Hide always-empty columns from the planners and the LLM and drop them from the record columns. A column a key,
    link, measure or breakdown uses stays visible (the query compiler needs it), with a note that it is empty."""
    used = _used_fields(definition, display=False)
    for e in definition["entities"]:
        gone = empty.get(e["key"]) or set()
        if not gone:
            continue
        keys = set()
        for f in e["fields"]:
            if f["source_column"].lower() in gone and f["key"] not in used.get(e["key"], set()):
                f["is_queryable"] = False
                note = "Always empty in this data: do not use."
                f["description"] = note if not f.get("description") else f"{f['description'].rstrip('. ')}. {note}"
                keys.add(f["key"])
        e["display_fields"] = [k for k in e.get("display_fields") or [] if k not in keys]


# --------------------------------------------------------------------------- describe


def _describe_targets(definition: dict[str, Any], lines: list[tuple[str, str]], has_docs: bool
                      ) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """(entity, columns to describe) for each table: the queryable columns the documents mention, or at most
    ``NO_DOC_COLUMNS`` of them without documents (text twins listed once, as text)."""
    mentioned: set[str] = set()
    for _, line in lines:
        mentioned |= {w.lower() for w in WORD.findall(line)}
    out = []
    for e in definition["entities"]:
        visible = [f for f in e["fields"] if f.get("is_queryable", True)]
        twins = _twins([f["source_column"] for f in visible])
        cols = [f for f in visible if f["source_column"] not in twins]
        if has_docs:
            cols = [f for f in cols if f["source_column"].lower() in mentioned]
        else:
            cols = cols[:NO_DOC_COLUMNS]
        out.append((e, cols))
    return out


def _excerpt(lines: list[tuple[str, str]], names: set[str]) -> str:
    picked, size = [], 0
    for title, line in lines:
        if names & {w.lower() for w in WORD.findall(line)}:
            entry = f"[{title}] {line}"
            if size + len(entry) > EXCERPT_CHARS:
                break
            picked.append(entry)
            size += len(entry) + 1
    return "\n".join(picked)


async def describe(gateway: Any, principal: Principal, definition: dict[str, Any], lines: list[tuple[str, str]],
                   has_docs: bool, job: _Job) -> int:
    """Labels and descriptions for tables and columns, written onto ``definition``; returns columns described."""
    targets = _describe_targets(definition, lines, has_docs)
    batches: list[list[tuple[dict[str, Any], list[dict[str, Any]]]]] = []
    cur: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    count = 0
    for ent, cols in targets:
        for i in range(0, max(len(cols), 1), DESCRIBE_COLUMNS):  # a very wide table is described in pieces
            piece = cols[i:i + DESCRIBE_COLUMNS]
            if cur and count + len(piece) > DESCRIBE_COLUMNS:
                batches.append(cur)
                cur, count = [], 0
            cur.append((ent, piece))
            count += len(piece)
    if cur:
        batches.append(cur)
    described = 0
    for n, batch in enumerate(batches, 1):
        await job.step(f"Describing tables and columns ({n} of {len(batches)})")
        names = {_bare(e["source_object"]).lower() for e, _ in batch} | {
            f["source_column"].lower() for _, cols in batch for f in cols}
        schema = "\n".join(f"- {e['source_object']}: " + (", ".join(
            f"{f['source_column']} ({f['data_type']})" for f in cols) or "(table description only)")
            for e, cols in batch)
        excerpt = _excerpt(lines, names) if has_docs else ""
        user = f"Tables and columns:\n{schema}" + (f"\n\nDocument lines about them:\n{excerpt}" if excerpt else
                                                    "\n\nNo document lines: describe only what names show.")
        out = await _call(gateway, DescribeOut, principal, "describe", DESCRIBE_SYSTEM, user, job)
        if out is None:
            continue
        by_obj = {e["source_object"].lower(): e for e, _ in batch}
        by_bare = {_bare(e["source_object"]).lower(): e for e, _ in batch}
        for t in out.tables:
            ent = by_obj.get(t.object.lower()) or by_bare.get(_bare(t.object).lower())
            if ent is None:
                continue
            if t.description.strip() and not (ent.get("description") or "").strip():
                ent["description"] = t.description.strip()
            fields = {f["source_column"].lower(): f for f in ent["fields"]}
            name = fields.get(t.name_column.strip().lower()) if t.name_column.strip() else None
            if name is not None and not name.get("is_queryable", True):
                name = None  # a hidden (always-empty or unpicked) column is never a record's name
            if name is not None:  # the record's name leads the record columns and says so to the SQL model
                ent["display_fields"] = [name["key"], *[k for k in ent.get("display_fields") or [] if k != name["key"]]]
            for fd in t.fields:
                f = fields.get(fd.column.lower())
                if f is None:  # a column that is not in the table: ignored, never added
                    continue
                # A label someone chose (a pack, an Admin) stays; only a label still made from the column name changes.
                if fd.label.strip() and f.get("label") in (None, "", auto_label(f["source_column"])):
                    f["label"] = fd.label.strip()[:80]
                if fd.description.strip():
                    f["description"] = fd.description.strip()
                    described += 1
            if name is not None:
                note = "The record's name: show and group records by it."
                if note not in (name.get("description") or ""):
                    name["description"] = ((name.get("description") or "").rstrip(". ") + ". " + note).lstrip(". ")
    return described


# --------------------------------------------------------------------------- rules


def _schema_for_rules(definition: dict[str, Any]) -> str:
    lines, size = [], 0
    for e in definition["entities"]:
        visible = [f for f in e["fields"] if f.get("is_queryable", True)]
        twins = _twins([f["source_column"] for f in visible])
        cols = [f["source_column"] for f in visible if f["source_column"] not in twins]
        line = f"- {e['source_object']}: " + ", ".join(cols)
        if size + len(line) > SCHEMA_CHARS:
            line = line[: max(0, SCHEMA_CHARS - size)]
        lines.append(line)
        size += len(line)
        if size >= SCHEMA_CHARS:
            break
    return "\n".join(lines)


def _entity_for(table: str | None, definition: dict[str, Any]) -> dict[str, Any] | None:
    if not table:
        return None
    t = table.strip().strip("`\"[]").lower()
    for e in definition["entities"]:
        if t in (e["source_object"].lower(), _bare(e["source_object"]).lower(), e["key"].lower()):
            return e
    return None


def bind_date(sql: str, as_of: str) -> str:
    """The rule's SQL with the data date written in: ``'{data_date}'`` and ``{data_date}`` both become ``'as_of'``."""
    return sql.replace(f"'{DATA_DATE}'", f"'{as_of}'").replace(f'"{DATA_DATE}"', f"'{as_of}'").replace(
        DATA_DATE, f"'{as_of}'")


def normalise_date(sql: str) -> str:
    """Store the placeholder one way only: bare ``{data_date}``."""
    return sql.replace(f"'{DATA_DATE}'", DATA_DATE).replace(f'"{DATA_DATE}"', DATA_DATE)


def check_rule(sql: str, ent: dict[str, Any], catalog_cols: set[str], dialect: str | None) -> tuple[set[str], str | None]:
    """(columns the rule names, problem or None): the SQL must parse as a condition or expression and name only
    columns the source table has."""
    probe = bind_date(sql, "2000-01-01")
    tree = None
    for stmt in (f"SELECT 1 FROM t WHERE {probe}", f"SELECT {probe} FROM t"):
        try:
            tree = parse_one(stmt, read=dialect)
            break
        except Exception:  # noqa: BLE001 - sqlglot raises several error types
            continue
    if tree is None:
        return set(), "The condition is not valid SQL."
    cols = {c.name.lower() for c in tree.find_all(exp.Column) if c.name}
    if not cols:  # TRUE, FALSE, 1 = 1: says nothing about the data and would keep or drop every row
        return set(), "The condition uses no column of the table."
    unknown = sorted(c for c in cols if c not in catalog_cols)
    if unknown:
        return cols, f"Not a column of {_bare(ent['source_object'])}: {', '.join(unknown)}"
    return cols, None


async def extract_rules(gateway: Any, principal: Principal, definition: dict[str, Any], docs: list[dict[str, Any]],
                        all_columns: set[str], job: _Job, *, dialect: str | None = None,
                        as_of: str | None = None, size: int = RULES_CHARS, label: str = "") -> list[RuleIn]:
    schema = _schema_for_rules(definition)
    facts = f"SQL dialect: {dialect or 'standard SQL'}. {DIALECT_RULES.get(dialect or '', '')}" + (
        f" The data date is {as_of}; still write {DATA_DATE}, never this date." if as_of else "")
    found: list[RuleIn] = []
    if docs:
        parts = _rule_parts(docs, all_columns, size)
        for n, part in enumerate(parts, 1):
            await job.step(f"Reading business rules from the documents ({label}{n} of {len(parts)})")
            user = f"{facts}\n\nSchema (tables and their columns):\n{schema}\n\nDocument:\n{part}"
            out = await _call(gateway, RulesOut, principal, "rules", RULES_SYSTEM, user, job)
            if out is not None:
                found += out.rules
    else:
        await job.step("No documents: inferring only what the schema makes certain")
        out = await _call(gateway, RulesOut, principal, "infer_rules", INFER_SYSTEM, f"{facts}\n\nSchema:\n{schema}",
                          job)
        if out is not None:
            found += out.rules
    return found


def rule_sig(term: str, ent: dict[str, Any] | None) -> tuple[str, str | None]:
    """One term on one table, whatever its wording: the term's words less those naming the table ("Open" and "Open
    requests" on the requests table are one term)."""
    subject = (term_words(ent["key"]) | term_words(ent["label"]) | term_words(_bare(ent["source_object"]))
               if ent else set())
    return (" ".join(sorted(term_words(term) - subject)) or term.lower(), ent["key"] if ent else None)


def merge_passes(passes: list[list[dict[str, Any]]], definition: dict[str, Any]) -> list[dict[str, Any]]:
    """The rules of several reads of the documents as one list: per term and table, a checked rule beats one held
    for review, otherwise the earlier read stands; a term only one read found is kept. Keys stay unique."""
    by_key = {e["key"]: e for e in definition["entities"]}
    chosen: dict[tuple[str, str | None], dict[str, Any]] = {}
    order: list[tuple[str, str | None]] = []
    for rules in passes:
        for r in rules:
            sig = rule_sig(r["term"], by_key.get(r["entity"]) if r["entity"] else None)
            have = chosen.get(sig)
            if have is None:
                chosen[sig] = r
                order.append(sig)
            elif have["status"] != "ok" and r["status"] == "ok":
                chosen[sig] = r
            elif have["status"] == "ok" and r["status"] == "ok" and r["kind"] == "population" and have["kind"] != \
                    "population" and have["sql"]:
                have["kind"] = "population"  # a read that saw it as the default population; the definition stays
    out, keys = [], set()
    for sig in order:
        r = dict(chosen[sig])
        while r["key"] in keys:
            r["key"] = f"{r['key']}_x"
        keys.add(r["key"])
        out.append(r)
    return out


def place_rules(found: list[RuleIn], definition: dict[str, Any], catalog: dict[str, list[dict[str, Any]]],
                origin: str, dialect: str | None) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Checked rules for the draft and the columns they need that the model must show (entity key → columns)."""
    rules: list[dict[str, Any]] = []
    needs: dict[str, list[str]] = {}
    keys: set[str] = set()
    seen: set[tuple[str, str | None]] = set()
    first: dict[tuple[str, str | None], dict[str, Any]] = {}
    for r in found:
        term = r.term.strip()
        if not term or r.overridden:  # a definition the document itself says is overridden never reaches answers
            continue
        ent = _entity_for(r.table, definition)
        # The same term on the same table twice ("Open request" / "Open requests" from two parts of a document):
        # the first stands, as the document reads in order (a precedence statement puts the winning part first).
        sig = rule_sig(term, ent)
        if sig in seen:  # the same term on the same table from two parts of the document: the first stands
            kept = first.get(sig)
            if kept is not None and r.kind == "population" and kept["kind"] == "condition" and kept["sql"]:
                kept["kind"] = "population"  # the later part said it is the default population; the definition stays
            continue
        seen.add(sig)
        key = _key(f"{ent['key']}_{term}" if ent else term)[:60]
        while key in keys:
            key = f"{key}_x"
        keys.add(key)
        rule: dict[str, Any] = {"key": key, "term": term[:120], "meaning": r.meaning.strip()[:600],
                                "entity": ent["key"] if ent else None,
                                "sql": normalise_date((r.sql or "").strip()) or None,
                                "synonyms": [s.strip()[:80] for s in r.synonyms if s.strip()][:12],
                                "always": bool(r.always), "origin": origin,
                                "kind": r.kind if r.kind in ("condition", "expression", "population", "display")
                                else "condition",
                                "cite": (r.cite or "")[:200] or None, "status": "ok", "issue": None}
        if rule["sql"]:
            if ent is None:
                rule.update(status="needs_review", issue="The table it applies to is not in the model.")
            else:
                source = {c["name"].lower(): c["name"] for c in catalog.get(ent["source_object"].lower(), [])}
                cols, problem = check_rule(rule["sql"], ent, set(source), dialect)
                if problem:
                    rule.update(status="needs_review", issue=problem[:300])
                else:
                    have = {f["source_column"].lower(): f for f in ent["fields"]}
                    for c in cols:
                        if c not in have or not have[c].get("is_queryable", True):
                            needs.setdefault(ent["key"], []).append(source[c])
        if rule["kind"] == "population" and not rule["sql"]:
            rule["kind"] = "condition"
        first[sig] = rule
        rules.append(rule)
    return rules, needs


# --------------------------------------------------------------------------- value check


def positive_values(sql: str, dialect: str | None) -> dict[str, set[str]]:
    """Column → text values a rule matches positively (``col = 'x'``, ``col IN ('x', 'y')``, not under NOT, not
    ``<>``/``NOT IN``): those values must exist in the data, or the rule matches nothing."""
    try:
        tree = parse_one(f"SELECT 1 FROM t WHERE {bind_date(sql, '2000-01-01')}", read=dialect)
    except Exception:  # noqa: BLE001 - checked earlier; nothing to add here
        return {}
    out: dict[str, set[str]] = {}

    def negated(node: exp.Expression) -> bool:
        p = node.parent
        while p is not None:
            if isinstance(p, exp.Not):
                return True
            p = p.parent
        return False

    def column(node: exp.Expression) -> str | None:
        while isinstance(node, (exp.Trim, exp.Upper, exp.Lower, exp.Paren)):
            node = node.this
        return node.name.lower() if isinstance(node, exp.Column) else None

    for node in tree.find_all(exp.EQ):
        col, lit = column(node.left), node.right
        if col is None:
            col, lit = column(node.right), node.left
        if col and isinstance(lit, exp.Literal) and lit.is_string and lit.this.strip() and not negated(node):
            out.setdefault(col, set()).add(lit.this)
    for node in tree.find_all(exp.In):
        col = column(node.this)
        lits = [e.this for e in node.expressions if isinstance(e, exp.Literal) and e.is_string and e.this.strip()]
        if col and lits and not negated(node):
            out.setdefault(col, set()).update(lits)
    return out


def settle_populations(rules: list[dict[str, Any]]) -> None:
    """After the checks: per table, the first checked population rule stays one; the rest become conditions."""
    seen: set[str] = set()
    for r in rules:
        if r.get("kind") == "population" and r["status"] == "ok":
            if r["entity"] in seen:
                r["kind"] = "condition"
            else:
                seen.add(r["entity"])


async def check_values(app: Any, principal: Principal, connection_id: str, version_id: str | None,
                       definition: dict[str, Any], rules: list[dict[str, Any]], dialect: str | None,
                       job: _Job) -> int:
    """Hold for review every checked rule whose positive text values exist nowhere in their column (a value the LLM
    made up, such as 'Open' for a column that holds 'Active'). Distinct values are read through the scoped path and
    compared in code; they are never sent to the model. Returns how many rules were held."""
    if version_id is None:
        return 0
    service = app.state.query_service
    by_key = {e["key"]: e for e in definition["entities"]}
    wanted: dict[tuple[str, str], set[str]] = {}
    per_rule: list[tuple[dict[str, Any], dict[str, set[str]], dict[str, set[str]]]] = []
    for r in rules:
        if r["status"] != "ok" or not r["sql"] or r["entity"] not in by_key:
            continue
        pv = positive_values(r["sql"], dialect)
        if pv:
            per_rule.append((r, pv, {}))
            for c, vals in pv.items():
                wanted.setdefault((r["entity"], c), set()).update(vals)
    known: dict[tuple[str, str], set[str] | None] = {}
    for ent_key, col in wanted:
        e = by_key[ent_key]
        src = next((f["source_column"] for f in e["fields"] if f["source_column"].lower() == col), None)
        if src is None:
            known[(ent_key, col)] = None
            continue
        sql = (f"SELECT DISTINCT {sql_name(src, dialect)} AS v FROM {sql_object(e['source_object'], dialect)}")
        try:
            res = await service.execute_custom(sql, connection_id=connection_id, model_version_id=version_id,
                                               principal=principal, ctx=QueryContext(surface="preview",
                                                                                     use_cache=False))
            rows = res.get("rows") or []
            known[(ent_key, col)] = None if res.get("truncated") else {str(x[0]).strip() for x in rows
                                                                        if x and x[0] is not None}
        except TelaError:
            known[(ent_key, col)] = None  # can't read it: nothing to say
    held = 0
    for r, pv, nv in per_rule:
        missing = []
        for c, vals in pv.items():
            have = known.get((r["entity"], c))
            if have is not None and not any(v.strip() in have for v in vals):
                missing.append(f"{c} never holds {', '.join(repr(v) for v in sorted(vals))}")

        if missing:
            r.update(status="needs_review", issue=("Matches nothing in the data: " + "; ".join(missing))[:300])
            held += 1
    if held:
        await job.step(f"{held} rules use values the data never holds: kept for review")
    return held


# --------------------------------------------------------------------------- the job


class GenerateIn(BaseModel):
    objects: dict[str, list[str]] = Field(min_length=1, max_length=500)  # qualified name → columns, or ["*"]
    document_ids: list[str] = Field(default_factory=list, max_length=20)
    reporting_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


async def _load_docs(conn: Any, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = await fetch_all(
        conn, "SELECT d.id, d.title, c.chunk_no, c.content FROM rag.knowledge_documents d "
              "JOIN rag.knowledge_chunks c ON c.document_id = d.id WHERE d.id = ANY(CAST(:ids AS uuid[])) "
              "ORDER BY d.title, c.chunk_no", {"ids": ids})
    docs: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = docs.setdefault(str(r["id"]), {"id": str(r["id"]), "title": r["title"], "text": ""})
        d["text"] += ("" if not d["text"] or d["text"].endswith("\n") else "\n") + r["content"]
    # In the order the Admin picked them: on a conflict between documents, the earlier one's rule stands.
    return [docs[i] for i in ids if i in docs]


async def run_job(app: Any, principal: Principal, connection_id: str, body: GenerateIn, job: _Job) -> None:
    gateway = app.state.gateway
    try:
        await job.step("Reading the tables and columns you picked")
        async with tenant_tx(principal.tenant_id) as conn:
            connection = await fetch_one(conn, "SELECT id, engine FROM conn.connections WHERE id = CAST(:id AS uuid) "
                                               "AND deleted_at IS NULL", {"id": connection_id})
            if connection is None:
                raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")
            catalog_rows = await _readable_objects(conn, connection_id)
            existing = await fetch_one(conn, "SELECT id FROM model.data_models WHERE connection_id = :c",
                                       {"c": connection["id"]})
            docs = await _load_docs(conn, body.document_ids)
            current = None
            approved_version: str | None = None
            if existing is not None:
                model = await _model(conn, str(existing["id"]))
                approved_version = str(model["current_version_id"]) if model["current_version_id"] else None
                current = await ensure_draft(conn, model, principal)
        dialect = DIALECTS.get(connection["engine"])
        by_name = {o["qualified_name"].lower(): o for o in catalog_rows}
        missing = [n for n in body.objects if n.lower() not in by_name]
        if missing:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Read the schema again: these tables are not in it or can't "
                            "be read: " + ", ".join(missing[:10]))
        picked = {n.lower(): {c.lower() for c in cols} for n, cols in body.objects.items()}
        questions: list[dict[str, Any]] = []
        if current is None:
            mode = "new"
            definition, questions = baseline_definition([by_name[n] for n in picked])
            for ln in definition["links"]:  # foreign keys the database declares are real joins
                ln["accepted"] = True
            for q in questions:  # the Admin answers them in Model studio; they don't stop approval
                q["blocking"] = False
        else:
            mode = "merged"
            definition = json.loads(json.dumps(current["definition"]))
            have = {e["source_object"].lower() for e in definition["entities"]}
            new_tables = [by_name[n] for n in picked if n not in have]
            if new_tables:
                definition, questions, _ = extend_definition(definition, new_tables, catalog_rows)
                for q in questions:
                    q["blocking"] = False

            add: dict[str, list[str]] = {}
            for e in definition["entities"]:
                want = picked.get(e["source_object"].lower())
                if not want or "*" in want:
                    want = {c["name"].lower() for c in by_name.get(e["source_object"].lower(), {}).get(
                        "columns", [])} if want else set()
                cols = {f["source_column"].lower() for f in e["fields"]}
                extra = [c["name"] for c in by_name.get(e["source_object"].lower(), {}).get("columns", [])
                         if c["name"].lower() in want and c["name"].lower() not in cols]
                if extra:
                    add[e["key"]] = extra
            if add:
                definition, more, _ = extend_columns(definition, add, catalog_rows)
                questions += [{**q, "blocking": False} for q in more]
        for ln in definition["links"]:  # joins the database itself declares (foreign keys) are real joins
            if ln.get("origin") == "foreign_key":
                ln["accepted"] = True
        counts = apply_selection(definition, picked)
        await job.step(f"{len(definition['entities'])} tables · {counts['shown']} columns shown to the model, "
                       f"{counts['hidden']} hidden")

        profile_counts: dict[str, int] = {}
        inferred: list[dict[str, Any]] = []
        if approved_version is not None:
            await job.step("Profiling the data: counts, distinct values and formats (no rows are read out)")
            types = {o["qualified_name"].lower(): {c["name"].lower(): str(c.get("type") or "") for c in o["columns"]}
                     for o in catalog_rows}
            profiles = {}
            for e in definition["entities"]:
                profiles[e["key"]] = await profile_entity(app, principal, connection_id, approved_version, e,
                                                          types.get(e["source_object"].lower(), {}), dialect)
            profile_counts = apply_profile(definition, profiles, _used_fields(definition, display=False), dialect)
            await job.step("Profile: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in profile_counts.items()
                                                    if v) + " columns")
            inferred = await infer_links(app, principal, connection_id, approved_version, definition, profiles,
                                         dialect)
            if inferred:
                await job.step(f"{len(inferred)} joins proven by the data: " + ", ".join(
                    f"{x['from_entity']}.{x['from_fields'][0]} → {x['to_entity']}" for x in inferred[:6]))
        else:
            await job.step("No approved version yet: the data profile runs on the next generation, after approval")
        lines = _doc_lines(docs)
        catalog = {o["qualified_name"].lower(): o["columns"] for o in catalog_rows}
        all_columns = {c["name"].lower() for cols in catalog.values() for c in cols}
        described = await describe(gateway, principal, definition, lines, bool(docs), job)
        as_of = body.reporting_date or (definition.get("settings") or {}).get("reporting_date")
        # The documents are read twice, cut into parts of other sizes, and the checked rules of both reads are kept:
        # one read of a long document drops or garbles a few rules, rarely the same ones twice.
        passes: list[list[dict[str, Any]]] = []
        needs: dict[str, list[str]] = {}
        for i, size in enumerate(RULE_PASSES if docs else RULE_PASSES[:1], 1):
            found = await extract_rules(gateway, principal, definition, docs, all_columns, job, dialect=dialect,
                                        as_of=as_of, size=size, label=f"read {i}, " if docs else "")
            await job.step(f"Checking {len(found)} rules of read {i} against the source columns")
            got, more = place_rules(found, definition, catalog, "document" if docs else "inferred", dialect)
            await check_values(app, principal, connection_id, approved_version, definition, got, dialect, job)
            passes.append(got)
            for k, v in more.items():
                needs.setdefault(k, []).extend(c for c in v if c not in needs.get(k, []))
        rules = merge_passes(passes, definition)
        if len(passes) > 1:
            await job.step(f"{len(rules)} rules after combining both reads "
                           f"({sum(1 for r in rules if r['status'] == 'ok')} checked)")
        settle_populations(rules)
        if needs:  # a column a checked rule is written on must be visible to the model
            add = {}
            for ent_key, cols in needs.items():
                ent = next(e for e in definition["entities"] if e["key"] == ent_key)
                have = {f["source_column"].lower(): f for f in ent["fields"]}
                for c in cols:
                    if c.lower() in have:
                        have[c.lower()]["is_queryable"] = True
                    else:
                        add.setdefault(ent_key, []).append(c)
            if add:
                definition, more, _ = extend_columns(definition, add, catalog_rows)
                questions += [{**q, "blocking": False} for q in more]
        kept = [r for r in definition.get("rules", []) if r.get("origin") == "admin"]
        definition["rules"] = kept + rules
        settings = dict(definition.get("settings") or {})
        if body.reporting_date:
            settings["reporting_date"] = body.reporting_date
        definition["settings"] = settings

        await job.step("Saving the draft model")
        async with tenant_tx(principal.tenant_id) as conn:
            if current is None:
                model_id, draft_id = new_id(), new_id()
                model_row = {"id": model_id, "connection_id": connection_id, "engine": connection["engine"]}
                snap, issues = _snapshot_or_issues(definition, model=model_row, version_id=draft_id)
                if snap is None:
                    raise TelaError(ErrorCode.MODEL_INVALID, "The generated model has problems: " + "; ".join(
                        i["issue"] for i in issues[:5]))
                stored = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no",
                                                               "engine"}, by_alias=True)
                await conn.execute(
                    text("INSERT INTO model.data_models (id, tenant_id, connection_id, draft_version_id, created_by) "
                         "VALUES (:id, :t, :c, :d, :by)"),
                    {"id": model_id, "t": principal.tenant_id, "c": connection_id, "d": draft_id,
                     "by": principal.user_id})
                await conn.execute(
                    text("INSERT INTO model.model_versions (id, tenant_id, data_model_id, status, definition, "
                         "created_by) VALUES (:id, :t, :m, 'draft', CAST(:def AS jsonb), :by)"),
                    {"id": draft_id, "t": principal.tenant_id, "m": model_id, "def": json.dumps(stored),
                     "by": principal.user_id})
            else:
                model = await _model(conn, str(existing["id"]))
                draft = await ensure_draft(conn, model, principal)
                model_id, draft_id = str(model["id"]), str(draft["id"])
                snap, issues = _snapshot_or_issues(definition, model=model, version_id=draft_id)
                if snap is None:
                    raise TelaError(ErrorCode.MODEL_INVALID, "The generated model has problems: " + "; ".join(
                        i["issue"] for i in issues[:5]))
                stored = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no",
                                                               "engine"}, by_alias=True)
                await conn.execute(
                    text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), "
                         "draft_revision = draft_revision + 1 WHERE id = :id"), {"d": json.dumps(stored), "id": draft_id})
            if questions:
                await insert_questions(conn, principal.tenant_id, draft_id, questions, principal.user_id)
            await audit.record(conn, tenant_id=principal.tenant_id, action="model.generated", object_type="data_model",
                               object_id=model_id, outcome="success", actor_id=principal.user_id, correlation_id=None,
                               details={"mode": mode, "documents": len(docs), "rules": len(rules),
                                        "llm_calls": job.calls})
        await job.done(preview(definition, model_id=model_id, draft_id=draft_id, mode=mode, docs=docs,
                               described=described, counts=counts))
    except TelaError as exc:
        await job.fail(exc.message)
    except Exception as exc:  # noqa: BLE001 - the job reports any failure instead of dying silently
        log.exception("model.generate_failed")
        await job.fail(f"The model could not be generated ({type(exc).__name__}).")


def preview(definition: dict[str, Any], *, model_id: str, draft_id: str, mode: str, docs: list[dict[str, Any]],
            described: int, counts: dict[str, int]) -> dict[str, Any]:
    tables = []
    for e in definition["entities"]:
        shown = [f for f in e["fields"] if f.get("is_queryable", True)]
        tables.append({"object": e["source_object"], "entity": e["key"], "label": e["label"],
                       "description": e.get("description"), "columns_shown": len(shown),
                       "columns_total": len(e["fields"]),
                       "columns": [{"column": f["source_column"], "label": f["label"], "type": f["data_type"],
                                    "values": f.get("values") or [], "kind": f.get("semantic_type"),
                                    "description": f.get("description")} for f in shown]})
    rules = definition.get("rules", [])
    return {"model_id": model_id, "draft_version_id": draft_id, "mode": mode,
            "documents": [{"id": d["id"], "title": d["title"]} for d in docs],
            "settings": definition.get("settings") or {}, "tables": tables, "rules": rules,
            "counts": {"tables": len(tables), "columns_shown": counts["shown"], "columns_hidden": counts["hidden"],
                       "columns_described": described, "links": len(definition.get("links", [])),
                       "measures": len(definition.get("measures", [])),
                       "rules_ok": sum(1 for r in rules if r["status"] == "ok"),
                       "rules_review": sum(1 for r in rules if r["status"] == "needs_review")}}


# --------------------------------------------------------------------------- endpoints


@router.get("/connections/{connection_id}/model/selection")
async def selection(connection_id: str, principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    """Every readable table and column, the columns the current model shows, and the documents for the builder."""
    async with tenant_tx(principal.tenant_id) as conn:
        objects = await _readable_objects(conn, connection_id)
        model = await fetch_one(conn, "SELECT id, current_version_id, draft_version_id FROM model.data_models "
                                      "WHERE connection_id = CAST(:c AS uuid)", {"c": connection_id})
        definition = None
        if model is not None:
            vid = model["draft_version_id"] or model["current_version_id"]
            row = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :v", {"v": vid})
            definition = row["definition"] if row else None
        docs = await fetch_all(
            conn, "SELECT id, title, original_filename, review_status, index_status, chunk_count, created_at "
                  "FROM rag.knowledge_documents WHERE (connection_id IS NULL OR connection_id = CAST(:c AS uuid)) "
                  "AND index_status = 'indexed' AND model_version_id IS NULL "  # people's documents, not model output
                  "ORDER BY created_at DESC LIMIT 100", {"c": connection_id})
    selected: dict[str, list[str]] | None = None
    if definition:
        selected = {e["source_object"]: [f["source_column"] for f in e["fields"] if f.get("is_queryable", True)]
                    for e in definition.get("entities", [])}
    return {
        "model_id": str(model["id"]) if model else None,
        "objects": [{"object": o["qualified_name"], "columns": [
            {"name": c["name"], "type": c.get("type"), "pk": bool(c.get("pk")), "fk": bool(c.get("fk_ref"))}
            for c in o["columns"]]} for o in objects],
        "selected": selected,
        "settings": (definition or {}).get("settings") or {},
        "rules": (definition or {}).get("rules") or [],
        "documents": [{**d, "id": str(d["id"])} for d in docs],
    }


@router.post("/connections/{connection_id}/model/generate", status_code=202)
async def generate(connection_id: str, body: GenerateIn, request: Request,
                   principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    if request.app.state.gateway is None:
        raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "Generating a model needs a language model. Set one up in "
                        "AI settings.")
    job = _Job(request.app, new_id(), connection_id)
    await job.step("Started")
    task = asyncio.create_task(run_job(request.app, principal, connection_id, body, job))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"job_id": job.id}


_TASKS: set[asyncio.Task[None]] = set()  # keep running jobs referenced until they finish


@router.get("/model-jobs/{job_id}")
async def job_status(job_id: str, request: Request,
                     principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    state = await _get(request.app, job_id)
    if state is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That model job is not known (it may have expired).")
    return state
