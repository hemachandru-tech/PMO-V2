"""Data models: drafts, questions, approval and versions (API-012, API-014; FEAT-007, FEAT-009; US-031, US-035).

* One model per connection and at most one draft at a time; the draft is a
  document edited with ETag / If-Match.
* Approval (``model.approve``) validates links, catalogue and types, requires
  every blocking question to be answered or dismissed and every link to be
  accepted, dry-runs every measure at the source (``LIMIT 0`` under a deny-all
  scope), then freezes an immutable version vN. The previous version becomes
  ``superseded``; cached results for the connection are dropped; an outbox
  event triggers re-indexing. The next request uses vN — no restart needed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.content.starters import build_starter_reports, install_starter_dashboards
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.model import guards
from tela_api.model.catalog import (
    baseline_definition,
    catalog_issues,
    extend_columns,
    extend_definition,
    stamp_source_types,
    unmodelled_columns,
    unmodelled_objects,
)
from tela_api.model.store import build_snapshot
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.schema_checksum import columns_checksum
from tela_query_spec.expr import measure_aggs
from tela_query_spec.model import ModelSnapshot

STORED_AGGS = ("count", "count_distinct", "sum", "avg", "min", "max")

router = APIRouter(prefix="/api/v1", tags=["models"])
SECTIONS = ("entities", "links", "measures", "dimensions", "settings")


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _draft_etag(revision: int) -> str:
    return f'W/"{revision}"'


def definition_hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


async def _model(conn: AsyncConnection, model_id: str) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        "SELECT m.*, c.engine, c.status AS connection_status FROM model.data_models m "
        "JOIN conn.connections c ON c.id = m.connection_id WHERE m.id = CAST(:id AS uuid) AND c.deleted_at IS NULL",
        {"id": model_id},
    )
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Model not found.")
    return row


async def _catalog(conn: AsyncConnection, connection_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = await fetch_all(
        conn, "SELECT qualified_name, columns FROM conn.connection_objects WHERE connection_id = :c "
              "AND removed_at IS NULL", {"c": connection_id})
    return {r["qualified_name"].lower(): r["columns"] for r in rows}


async def _readable_objects(conn: AsyncConnection, connection_id: str) -> list[dict[str, Any]]:
    """Catalogue rows a model may read: present at the last schema read and readable by the source account."""
    return await fetch_all(
        conn, "SELECT qualified_name, columns, keys FROM conn.connection_objects WHERE connection_id = :c "
              "AND removed_at IS NULL AND readable IS NOT FALSE ORDER BY qualified_name", {"c": connection_id})


async def _unreadable(conn: AsyncConnection, connection_id: str) -> set[str]:
    """Objects the source account may not read (checked when the schema was read)."""
    rows = await fetch_all(
        conn, "SELECT qualified_name FROM conn.connection_objects WHERE connection_id = :c AND removed_at IS NULL "
              "AND readable IS FALSE", {"c": connection_id})
    return {r["qualified_name"].lower() for r in rows}


def _snapshot_or_issues(definition: dict[str, Any], *, model: dict[str, Any], version_id: str
                        ) -> tuple[ModelSnapshot | None, list[dict[str, str]]]:
    try:
        snap = build_snapshot(definition, version_id=version_id, connection_id=str(model["connection_id"]),
                              version_no=None, engine=model["engine"])
    except ValidationError as exc:
        return None, [{"ref": ".".join(map(str, e["loc"])) or "model", "issue": e["msg"]} for e in exc.errors()[:50]]
    except ValueError as exc:
        return None, [{"ref": "model", "issue": str(exc)}]
    return snap, []


async def ensure_draft(conn: AsyncConnection, model: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Return the draft, creating it from the current approved version when none exists."""
    if model["draft_version_id"]:
        draft = await fetch_one(conn, "SELECT * FROM model.model_versions WHERE id = :id",
                                {"id": model["draft_version_id"]})
        assert draft is not None
        return draft
    current = await fetch_one(conn, "SELECT definition FROM model.model_versions WHERE id = :id",
                              {"id": model["current_version_id"]})
    if current is None:
        raise TelaError(ErrorCode.NOT_FOUND, "This model has no draft or approved version.")
    draft_id = new_id()
    await conn.execute(
        text("INSERT INTO model.model_versions (id, tenant_id, data_model_id, status, definition, created_by) "
             "VALUES (:id, :t, :m, 'draft', CAST(:d AS jsonb), :by)"),
        {"id": draft_id, "t": principal.tenant_id, "m": model["id"], "d": json.dumps(current["definition"]),
         "by": principal.user_id},
    )
    await conn.execute(text("UPDATE model.data_models SET draft_version_id = :d WHERE id = :id"),
                       {"d": draft_id, "id": model["id"]})
    draft = await fetch_one(conn, "SELECT * FROM model.model_versions WHERE id = :id", {"id": draft_id})
    assert draft is not None
    return draft


async def insert_questions(conn: AsyncConnection, tenant_id: str, version_id: str, questions: list[dict[str, Any]],
                           by: str) -> None:
    for q in questions:
        await conn.execute(
            text("INSERT INTO model.model_questions (id, tenant_id, model_version_id, question, target_ref, options, "
                 "blocking, created_by) VALUES (:id, :t, :v, :q, CAST(:ref AS jsonb), CAST(:o AS jsonb), :b, :by)"),
            {"id": new_id(), "t": tenant_id, "v": version_id, "q": q["question"],
             "ref": json.dumps(q.get("target_ref")), "o": json.dumps(q.get("options", [])),
             "b": q.get("blocking", True), "by": by},
        )


def _model_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"model_id": str(row["id"]), "connection_id": str(row["connection_id"]),
            "current_version_id": str(row["current_version_id"]) if row["current_version_id"] else None,
            "draft_version_id": str(row["draft_version_id"]) if row["draft_version_id"] else None,
            "installed_pack_id": str(row["installed_pack_id"]) if row["installed_pack_id"] else None,
            "status": row["status"], "broken_reasons": row["broken_reasons"]}


# --------------------------------------------------------------------------- create and read


class ModelCreate(BaseModel):
    connection_id: str
    from_introspection: bool = True


@router.post("/models", status_code=201)
async def create_model(body: ModelCreate, request: Request,
                       principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        connection = await fetch_one(conn, "SELECT id, status FROM conn.connections WHERE id = CAST(:id AS uuid) "
                                           "AND deleted_at IS NULL", {"id": body.connection_id})
        if connection is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")
        if await fetch_one(conn, "SELECT 1 FROM model.data_models WHERE connection_id = :c", {"c": connection["id"]}):
            raise TelaError(ErrorCode.CONFLICT, "This connection already has a model.")
        objects = await _readable_objects(conn, connection["id"])
        if not objects:
            raise TelaError(ErrorCode.CONNECTION_NOT_READY, "Read the schema (introspect) before building a model.")
        definition, questions = baseline_definition(objects) if body.from_introspection else (
            {"entities": [], "links": [], "measures": [], "dimensions": [], "settings": {}}, [])
        model_id, draft_id = new_id(), new_id()
        await conn.execute(
            text("INSERT INTO model.data_models (id, tenant_id, connection_id, draft_version_id, created_by) "
                 "VALUES (:id, :t, :c, :d, :by)"),
            {"id": model_id, "t": principal.tenant_id, "c": connection["id"], "d": draft_id, "by": principal.user_id},
        )
        await conn.execute(
            text("INSERT INTO model.model_versions (id, tenant_id, data_model_id, status, definition, created_by) "
                 "VALUES (:id, :t, :m, 'draft', CAST(:def AS jsonb), :by)"),
            {"id": draft_id, "t": principal.tenant_id, "m": model_id, "def": json.dumps(definition),
             "by": principal.user_id},
        )
        await insert_questions(conn, principal.tenant_id, draft_id, questions, principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.created", object_type="data_model",
                           object_id=model_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"entities": len(definition["entities"]), "questions": len(questions)})
        row = await _model(conn, model_id)
    return _model_out(row)


@router.get("/models")
async def list_models(connection_id: str | None = None,
                      principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT * FROM model.data_models WHERE CAST(:c AS uuid) IS NULL OR connection_id = CAST(:c AS uuid)",
            {"c": connection_id})
    return {"items": [_model_out(r) for r in rows]}


@router.get("/models/{model_id}")
async def get_model(model_id: str, principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        return _model_out(await _model(conn, model_id))


@router.get("/models/{model_id}/versions")
async def list_versions(model_id: str, principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await _model(conn, model_id)
        rows = await fetch_all(
            conn, "SELECT id, version_no, status, change_summary, approved_by, approved_at, schema_checksum "
                  "FROM model.model_versions WHERE data_model_id = CAST(:m AS uuid) AND status <> 'draft' "
                  "ORDER BY version_no DESC", {"m": model_id})
    return {"items": [{**r, "id": str(r["id"]), "approved_by": str(r["approved_by"]) if r["approved_by"] else None}
                      for r in rows]}


@router.get("/models/{model_id}/versions/{version_id}")
async def get_version(model_id: str, version_id: str,
                      principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(
            conn, "SELECT id, version_no, status, definition, approved_at FROM model.model_versions "
                  "WHERE id = CAST(:v AS uuid) AND data_model_id = CAST(:m AS uuid)", {"v": version_id, "m": model_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Model version not found.")
    return {**row, "id": str(row["id"])}


# --------------------------------------------------------------------------- draft


async def _draft_view(conn: AsyncConnection, model: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    questions = await fetch_all(
        conn, "SELECT id, question, target_ref, options, answer, blocking, status FROM model.model_questions "
              "WHERE model_version_id = :v ORDER BY created_at", {"v": draft["id"]})
    snap, issues = _snapshot_or_issues(draft["definition"], model=model, version_id=str(draft["id"]))
    if snap is not None:
        issues = catalog_issues(snap, await _catalog(conn, str(model["connection_id"])),
                                await _unreadable(conn, str(model["connection_id"])))
    readable = await _readable_objects(conn, str(model["connection_id"]))
    return {
        "model_id": str(model["id"]), "draft_version_id": str(draft["id"]), "draft_etag": _draft_etag(
            draft["draft_revision"]),
        **draft["definition"],
        "open_questions": [{**q, "id": str(q["id"])} for q in questions],
        "issues": issues,
        "unaccepted_links": [ln for ln in draft["definition"].get("links", []) if not ln.get("accepted", True)],
        # Tables read from the source that no entity uses yet, e.g. added after the model was built (BUG-018).
        "unmodelled_objects": unmodelled_objects(draft["definition"], readable),
        # Columns of those tables that no field reads yet, e.g. added to the source later (BUG-028).
        "unmodelled_columns": unmodelled_columns(draft["definition"], readable),
    }


@router.get("/models/{model_id}/draft")
async def get_draft(model_id: str, response: Response,
                    principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        view = await _draft_view(conn, await _model(conn, model_id), draft)
    response.headers["ETag"] = view["draft_etag"]
    return view


class DraftPatch(BaseModel):
    """Replace whole sections of the draft (entities, links, measures, dimensions, settings)."""

    entities: list[dict[str, Any]] | None = None
    links: list[dict[str, Any]] | None = None
    measures: list[dict[str, Any]] | None = None
    dimensions: list[dict[str, Any]] | None = None
    settings: dict[str, Any] | None = None


@router.patch("/models/{model_id}/draft")
async def patch_draft(model_id: str, body: DraftPatch, request: Request, response: Response,
                      if_match: str | None = Header(default=None),
                      principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        if if_match != _draft_etag(draft["draft_revision"]):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The draft changed since you loaded it. Reload it.")
        definition = dict(draft["definition"])
        for section in SECTIONS:
            value = getattr(body, section)
            if value is not None:
                definition[section] = value
        snap, issues = _snapshot_or_issues(definition, model=model, version_id=str(draft["id"]))
        if snap is None:
            raise TelaError(ErrorCode.MODEL_INVALID, "The model has problems to fix first.", {"issues": issues})
        stored = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no", "engine"},
                                 by_alias=True)
        await conn.execute(
            text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), draft_revision = draft_revision + 1 "
                 "WHERE id = :id"), {"d": json.dumps(stored), "id": draft["id"]})
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.draft_changed", object_type="data_model",
                           object_id=model_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request),
                           details={"sections": [s for s in SECTIONS if getattr(body, s) is not None]})
        draft = await ensure_draft(conn, await _model(conn, model_id), principal)
        view = await _draft_view(conn, model, draft)
    response.headers["ETag"] = view["draft_etag"]
    return view


class ObjectsIn(BaseModel):
    objects: list[str] = Field(min_length=1, max_length=500)


@router.post("/models/{model_id}/draft/objects")
async def add_draft_objects(model_id: str, body: ObjectsIn, request: Request, response: Response,
                            if_match: str | None = Header(default=None),
                            principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    """Add tables read from the source to the draft, one entity each, as the baseline would (BUG-018)."""
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        if if_match != _draft_etag(draft["draft_revision"]):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The draft changed since you loaded it. Reload it.")
        catalog = await _readable_objects(conn, str(model["connection_id"]))
        by_name = {o["qualified_name"].lower(): o for o in catalog}
        missing = [n for n in body.objects if n.lower() not in by_name]
        if missing:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Read the schema again: these tables are not in it or "
                            "cannot be read.", {"objects": missing})
        definition, questions, added = extend_definition(
            dict(draft["definition"]), [by_name[n.lower()] for n in body.objects], catalog)
        snap, issues = _snapshot_or_issues(definition, model=model, version_id=str(draft["id"]))
        if snap is None:
            raise TelaError(ErrorCode.MODEL_INVALID, "The model has problems to fix first.", {"issues": issues})
        stored = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no", "engine"},
                                 by_alias=True)
        await conn.execute(
            text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), draft_revision = draft_revision + 1 "
                 "WHERE id = :id"), {"d": json.dumps(stored), "id": draft["id"]})
        await insert_questions(conn, principal.tenant_id, str(draft["id"]), questions, principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.draft_changed", object_type="data_model",
                           object_id=model_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"sections": ["entities"], "added": added})
        draft = await ensure_draft(conn, await _model(conn, model_id), principal)
        view = await _draft_view(conn, model, draft)
    response.headers["ETag"] = view["draft_etag"]
    return {**view, "added": added}


class ColumnsIn(BaseModel):
    """Entity key → source column names to add as fields."""

    columns: dict[str, list[str]] = Field(min_length=1, max_length=500)


@router.post("/models/{model_id}/draft/columns")
async def add_draft_columns(model_id: str, body: ColumnsIn, request: Request, response: Response,
                            if_match: str | None = Header(default=None),
                            principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    """Add columns read from the source to entities the draft already has, one field each (BUG-028)."""
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        if if_match != _draft_etag(draft["draft_revision"]):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The draft changed since you loaded it. Reload it.")
        catalog = await _readable_objects(conn, str(model["connection_id"]))
        available = {(i["entity"], c.lower()) for i in unmodelled_columns(draft["definition"], catalog)
                     for c in i["columns"]}
        missing = sorted(f"{e}.{c}" for e, cols in body.columns.items() for c in cols
                         if (e, c.lower()) not in available)
        if missing:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Read the schema again: these columns are not in it, cannot "
                            "be read, or are already in the model.", {"columns": missing})
        definition, questions, added = extend_columns(dict(draft["definition"]), body.columns, catalog)
        snap, issues = _snapshot_or_issues(definition, model=model, version_id=str(draft["id"]))
        if snap is None:
            raise TelaError(ErrorCode.MODEL_INVALID, "The model has problems to fix first.", {"issues": issues})
        stored = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no", "engine"},
                                 by_alias=True)
        await conn.execute(
            text("UPDATE model.model_versions SET definition = CAST(:d AS jsonb), draft_revision = draft_revision + 1 "
                 "WHERE id = :id"), {"d": json.dumps(stored), "id": draft["id"]})
        await insert_questions(conn, principal.tenant_id, str(draft["id"]), questions, principal.user_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.draft_changed", object_type="data_model",
                           object_id=model_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=_rid(request), details={"sections": ["entities"], "fields_added": added})
        draft = await ensure_draft(conn, await _model(conn, model_id), principal)
        view = await _draft_view(conn, model, draft)
    response.headers["ETag"] = view["draft_etag"]
    return {**view, "added": added}


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    target_ref: dict[str, Any] | None = None
    options: list[str] = Field(default_factory=list, max_length=6)
    blocking: bool = True


@router.post("/models/{model_id}/questions", status_code=201)
async def add_question(model_id: str, body: QuestionIn,
                       principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        draft = await ensure_draft(conn, await _model(conn, model_id), principal)
        await insert_questions(conn, principal.tenant_id, str(draft["id"]), [body.model_dump()], principal.user_id)
    return {"status": "open"}


class AnswerIn(BaseModel):
    answer: str | None = Field(default=None, max_length=1000)
    dismiss: bool = False


@router.post("/models/{model_id}/questions/{question_id}/answer")
async def answer_question(model_id: str, question_id: str, body: AnswerIn, request: Request,
                          principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    if not body.dismiss and not body.answer:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Give an answer or dismiss the question.")
    async with tenant_tx(principal.tenant_id) as conn:
        draft = await ensure_draft(conn, await _model(conn, model_id), principal)
        res = await conn.execute(
            text("UPDATE model.model_questions SET status = :s, answer = :a, answered_by = :by "
                 "WHERE id = CAST(:q AS uuid) AND model_version_id = :v RETURNING id"),
            {"s": "dismissed" if body.dismiss else "answered", "a": body.answer, "by": principal.user_id,
             "q": question_id, "v": draft["id"]},
        )
        if res.first() is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Question not found.")
        await audit.record(conn, tenant_id=principal.tenant_id, action="model.question_answered",
                           object_type="model_question", object_id=question_id, outcome="success",
                           actor_id=principal.user_id, correlation_id=_rid(request),
                           details={"dismissed": body.dismiss})
    return {"status": "dismissed" if body.dismiss else "answered"}


# --------------------------------------------------------------------------- approval (US-035)


class ApproveIn(BaseModel):
    draft_etag: str
    note: str | None = Field(default=None, max_length=1000)
    # US-199: approve although a mapping guard blocks (fields sharing a column); the reason is audited.
    override_note: str | None = Field(default=None, min_length=3, max_length=1000)


async def _data_checks(service: QueryService, snap: ModelSnapshot, connection: dict[str, Any],
                       principal: Principal, request_id: str | None) -> list[dict[str, Any]]:
    """Mapping guards on the data (US-199): measure filters on values the column doesn't hold, measures that return
    0, and measures of one table with different rules and the same figure. Warnings, shown by ``draft/checks``; they
    don't block approval."""
    profiles, figures = await service.trial(
        snapshot=snap, connection=connection, principal=principal,
        ctx=QueryContext(surface="precheck", request_id=request_id, use_cache=False),
        fields=guards.filter_fields(snap), measures=True, top=guards.TOP_VALUES)
    measures = [m.model_dump(mode="json", by_alias=True) for m in snap.measures]
    return guards.filter_warnings(snap, profiles) + guards.measure_warnings(measures, figures)


def _impact(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Breaking changes relative to the previous version (RULE-015). Widgets and dashboards arrive in P2."""
    breaking: list[dict[str, str]] = []
    if previous is not None:
        before = {m["key"]: m for m in previous.get("measures", [])}
        after = {m["key"]: m for m in current.get("measures", [])}
        for key, m in before.items():
            if key not in after:
                breaking.append({"measure": key, "change": "removed"})
            elif json.dumps(m["expression"], sort_keys=True) != json.dumps(after[key]["expression"], sort_keys=True):
                breaking.append({"measure": key, "change": "rule_changed"})
        prev_fields = {f"{e['key']}.{f['key']}" for e in previous["entities"] for f in e["fields"]}
        cur_fields = {f"{e['key']}.{f['key']}" for e in current["entities"] for f in e["fields"]}
        breaking += [{"field": f, "change": "removed"} for f in sorted(prev_fields - cur_fields)]
    return {"widgets_pinned_to_previous": 0, "published_dashboards": 0, "breaking_changes": breaking}


def _refs(node: Any) -> set[str]:
    """Every ``ref`` string in a widget spec (measure, breakdowns, filters, time)."""
    if isinstance(node, dict):
        out = {node["ref"]} if isinstance(node.get("ref"), str) else set()
        for v in node.values():
            out |= _refs(v)
        return out
    if isinstance(node, list):
        return set().union(*(_refs(v) for v in node)) if node else set()
    return set()


def _diff(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """What a draft changes relative to the approved version, by kind (US-144)."""
    prev = previous or {"entities": [], "links": [], "measures": [], "dimensions": []}

    def by_key(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {i["key"]: i for i in items}

    def fields(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {f"{e['key']}.{f['key']}": {**f, "entity_label": e["label"]} for e in d["entities"] for f in e["fields"]}

    def link_key(ln: dict[str, Any]) -> str:
        return f"{ln['from_entity']} → {ln['to_entity']} ({', '.join(ln.get('from_fields', []))})"

    def same(a: Any, b: Any) -> bool:
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    pm, cm = by_key(prev["measures"]), by_key(current["measures"])
    pf, cf = fields(prev), fields(current)
    pd, cd = by_key(prev["dimensions"]), by_key(current["dimensions"])
    pe, ce = by_key(prev["entities"]), by_key(current["entities"])
    pl = {link_key(ln): ln for ln in prev["links"]}
    cl = {link_key(ln): ln for ln in current["links"]}

    def item(key: str, label: str, **extra: Any) -> dict[str, Any]:
        return {"key": key, "label": label, **extra}

    changed_measures = []
    for k in sorted(pm.keys() & cm.keys()):
        a, b = pm[k], cm[k]
        what = [w for w, x in (("rule", not same(a["expression"], b["expression"])),
                               ("blanks", a.get("blank_handling") != b.get("blank_handling")),
                               ("name", a["label"] != b["label"]),
                               ("description", a.get("plain_rule") != b.get("plain_rule"))) if x]
        if what:
            changed_measures.append(item(k, b["label"], what=what, before=a.get("plain_rule"),
                                         after=b.get("plain_rule")))
    changed_fields = []
    for k in sorted(pf.keys() & cf.keys()):
        a, b = pf[k], cf[k]
        what = [w for w in ("label", "data_type", "sensitivity", "description", "blank_handling")
                if a.get(w) != b.get(w)]
        if what:
            changed_fields.append(item(k, f"{b['entity_label']} · {b['label']}", what=what,
                                       before={w: a.get(w) for w in what}, after={w: b.get(w) for w in what}))
    return {
        "entities": {"added": [item(k, ce[k]["label"]) for k in sorted(ce.keys() - pe.keys())],
                     "removed": [item(k, pe[k]["label"]) for k in sorted(pe.keys() - ce.keys())]},
        "fields": {"added": [item(k, f"{cf[k]['entity_label']} · {cf[k]['label']}")
                             for k in sorted(cf.keys() - pf.keys())],
                   "removed": [item(k, f"{pf[k]['entity_label']} · {pf[k]['label']}")
                               for k in sorted(pf.keys() - cf.keys())],
                   "changed": changed_fields},
        "measures": {"added": [item(k, cm[k]["label"]) for k in sorted(cm.keys() - pm.keys())],
                     "removed": [item(k, pm[k]["label"]) for k in sorted(pm.keys() - cm.keys())],
                     "changed": changed_measures},
        "dimensions": {"added": [item(k, cd[k]["label"]) for k in sorted(cd.keys() - pd.keys())],
                       "removed": [item(k, pd[k]["label"]) for k in sorted(pd.keys() - cd.keys())]},
        "links": {"added": [item(k, k) for k in sorted(cl.keys() - pl.keys())],
                  "removed": [item(k, k) for k in sorted(pl.keys() - cl.keys())]},
    }


def _change_summary(previous: dict[str, Any] | None, current: dict[str, Any]) -> str:
    if previous is None:
        return (f"First version: {len(current['entities'])} entities, {len(current['measures'])} measures, "
                f"{len(current['dimensions'])} dimensions.")
    pm = {m["key"] for m in previous["measures"]}
    cm = {m["key"] for m in current["measures"]}
    return (f"{len(cm - pm)} measures added, {len(pm - cm)} removed; "
            f"{len(current['entities'])} entities, {len(current['dimensions'])} dimensions.")


async def _materialise(conn: AsyncConnection, tenant_id: str, version_id: str, snap: ModelSnapshot) -> None:
    entity_ids: dict[str, str] = {}
    field_ids: dict[tuple[str, str], str] = {}
    for e in snap.entities:
        eid = new_id()
        entity_ids[e.key] = eid
        await conn.execute(
            text("INSERT INTO model.model_entities (id, tenant_id, model_version_id, key, label, source_object, "
                 "primary_key, description, grain) VALUES (:id, :t, :v, :k, :l, :s, :pk, :d, :g)"),
            {"id": eid, "t": tenant_id, "v": version_id, "k": e.key, "l": e.label, "s": e.source_object,
             "pk": e.primary_key, "d": e.description, "g": e.grain},
        )
        for f in e.fields:
            fid = new_id()
            field_ids[(e.key, f.key)] = fid
            await conn.execute(
                text("INSERT INTO model.model_fields (id, tenant_id, model_entity_id, key, source_column, label, "
                     "description, data_type, semantic_type, format, sensitivity, is_queryable, blank_handling) "
                     "VALUES (:id, :t, :e, :k, :c, :l, :d, :dt, :st, CAST(:fmt AS jsonb), :sen, :q, :bh)"),
                {"id": fid, "t": tenant_id, "e": eid, "k": f.key, "c": f.source_column, "l": f.label,
                 "d": f.description, "dt": f.data_type, "st": f.semantic_type, "fmt": json.dumps(f.format),
                 "sen": f.sensitivity, "q": f.is_queryable, "bh": f.blank_handling},
            )
    for ln in snap.links:
        await conn.execute(
            text("INSERT INTO model.model_links (id, tenant_id, model_version_id, from_entity_id, to_entity_id, "
                 "from_field_keys, to_field_keys, cardinality, origin) "
                 "VALUES (:id, :t, :v, :f, :to, :ff, :tf, :c, :o)"),
            {"id": new_id(), "t": tenant_id, "v": version_id, "f": entity_ids[ln.from_entity],
             "to": entity_ids[ln.to_entity], "ff": ln.from_fields, "tf": ln.to_fields, "c": ln.cardinality,
             "o": ln.origin},
        )
    for m in snap.measures:
        aggs = measure_aggs(m.expression)
        # The column lists the classic aggregates; medians, percentiles and compound rules are "custom" (the
        # expression column holds the full rule).
        aggregation = aggs[0].agg if len(aggs) == 1 and aggs[0].agg in STORED_AGGS else "custom"
        await conn.execute(
            text("INSERT INTO model.measures (id, tenant_id, model_version_id, key, label, entity_key, aggregation, "
                 "expression, plain_rule, blank_handling, format, currency_field_key, rule_ref, synonyms, status) "
                 "VALUES (:id, :t, :v, :k, :l, :e, :a, CAST(:x AS jsonb), :r, :bh, :fmt, :cur, :ref, :syn, :s)"),
            {"id": new_id(), "t": tenant_id, "v": version_id, "k": m.key, "l": m.label, "e": m.entity,
             "a": aggregation, "x": json.dumps(m.model_dump(mode="json", by_alias=True)["expression"]),
             "r": m.plain_rule, "bh": m.blank_handling, "fmt": m.format, "cur": m.currency_field,
             "ref": m.rule_ref, "syn": m.synonyms, "s": m.status},
        )
    for d in snap.dimensions:
        await conn.execute(
            text("INSERT INTO model.dimensions (id, tenant_id, model_version_id, key, model_field_id, label, "
                 "is_temporal, is_status, sort_order, value_labels, synonyms) VALUES (:id, :t, :v, :k, :f, :l, :tmp, "
                 ":st, CAST(:so AS jsonb), CAST(:vl AS jsonb), :syn)"),
            {"id": new_id(), "t": tenant_id, "v": version_id, "k": d.key, "f": field_ids[(d.entity, d.field)],
             "l": d.label, "tmp": d.is_temporal, "st": d.is_status,
             "so": json.dumps(d.sort_order) if d.sort_order else None, "vl": json.dumps(d.value_labels),
             "syn": d.synonyms},
        )


@router.get("/models/{model_id}/draft/impact")
async def draft_impact(model_id: str, principal: Principal = Depends(require(Perm.MODEL_EDIT))) -> dict[str, Any]:
    """What approving the draft would change, before approval (US-144; RULE-015).

    Published dashboard versions stay pinned to the model version they were built on: their widgets listed here use a
    definition that changes or disappears and show it once upgraded. Starter dashboards are re-pointed to the new
    version on approval (``changes_at_approval``).
    Personal fields are checked against the draft as they would be against the new current version.
    """
    from tela_api.content.common import is_custom
    from tela_api.content.fields import broken_against
    from tela_api.content.render import widget_items

    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        previous = await fetch_one(conn, "SELECT version_no, definition FROM model.model_versions WHERE id = :id",
                                   {"id": model["current_version_id"]}) if model["current_version_id"] else None
        definition = draft["definition"]
        diff = _diff(previous["definition"] if previous else None, definition)
        snap, _issues = _snapshot_or_issues(definition, model=model, version_id=str(draft["id"]))
        versions = {str(r["id"]) for r in await fetch_all(
            conn, "SELECT id FROM model.model_versions WHERE data_model_id = :m AND id <> :d",
            {"m": model["id"], "d": draft["id"]})}
        dashboards = await fetch_all(
            conn, "SELECT d.id, d.title, v.version_no, v.frozen_content FROM content.dashboards d "
                  "JOIN content.dashboard_versions v ON v.id = d.live_version_id "
                  "WHERE d.status = 'published' AND d.connection_id = :c ORDER BY d.title",
            {"c": model["connection_id"]})
        # Starter dashboards are published without a frozen version; their widgets are rows (P1).
        starters = await fetch_all(
            conn, "SELECT d.id, d.title, d.version_no, w.title AS widget, w.spec FROM content.dashboards d "
                  "JOIN content.dashboard_tabs t ON t.dashboard_id = d.id JOIN content.widgets w "
                  "ON w.dashboard_tab_id = t.id WHERE d.status = 'published' AND d.live_version_id IS NULL "
                  "AND d.connection_id = :c ORDER BY d.title, t.position, w.position", {"c": model["connection_id"]})
        for r in starters:
            if not dashboards or dashboards[-1]["id"] != r["id"] or "starter" not in dashboards[-1]:
                dashboards.append({"id": r["id"], "title": r["title"], "version_no": r["version_no"], "starter": True,
                                   "frozen_content": {"reports": [{"sections": [{"items": []}]}]}})
            dashboards[-1]["frozen_content"]["reports"][0]["sections"][0]["items"].append(
                {"kind": "widget", "title": r["widget"], "spec": r["spec"]})
        personal = await fetch_all(
            conn, "SELECT f.id, f.label, f.kind, f.definition, u.display_name AS owner FROM content.personal_fields f "
                  "JOIN iam.users u ON u.id = f.owner_id WHERE f.connection_id = :c AND f.deleted_at IS NULL "
                  "AND f.broken_reason IS NULL AND f.kind <> 'freehand' ORDER BY f.label",
            {"c": model["connection_id"]})

    gone = ({f"measure:{m['key']}": f"{m['label']} is removed" for m in diff["measures"]["removed"]}
            | {f"measure:{m['key']}": f"{m['label']}: rule changes" for m in diff["measures"]["changed"]
               if {"rule", "blanks"} & set(m["what"])}
            | {f"dimension:{d['key']}": f"{d['label']} is removed" for d in diff["dimensions"]["removed"]}
            | {f"field:{f['key']}": f"{f['label']} is removed" for f in diff["fields"]["removed"]})
    removed_entities = {e["key"] for e in diff["entities"]["removed"]}
    affected = []
    for d in dashboards:
        widgets = []
        for i in widget_items(d["frozen_content"]):
            if i["spec"].get("model_version_id") not in versions:
                continue
            if is_custom(i["spec"]):
                reasons = [f"Reads {e}, which is removed" for e in i["spec"].get("entities", [])
                           if e in removed_entities]
            else:
                reasons = [gone[r] for r in sorted(_refs(i["spec"])) if r in gone]
            if reasons:
                widgets.append({"title": i["title"], "reasons": reasons,
                                "breaks": any("removed" in r for r in reasons)})
        if widgets:
            affected.append({"id": str(d["id"]), "title": d["title"], "version": d["version_no"], "widgets": widgets,
                             "changes_at_approval": bool(d.get("starter"))})
    broken = []
    if snap is not None:
        for f in personal:
            reason = broken_against(snap, f["kind"], f["definition"])
            if reason:
                broken.append({"id": str(f["id"]), "label": f["label"], "owner": f["owner"], "reason": reason})
    return {
        "draft_etag": _draft_etag(draft["draft_revision"]),
        "base_version": previous["version_no"] if previous else None,
        "changes": diff,
        "breaking_changes": _impact(previous["definition"] if previous else None, definition)["breaking_changes"],
        "dashboards": affected,
        "widget_count": sum(len(d["widgets"]) for d in affected),
        "personal_fields_broken": broken,
    }


@router.get("/models/{model_id}/draft/checks")
async def draft_checks(model_id: str, request: Request, data: bool = True,
                       principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    """The mapping guards (US-199), before approving: fields sharing a column (blocking; approval needs an override
    note), and with ``data`` the checks on the data (warnings; one read per measure, so they run here on request and
    not on every approval)."""
    service: QueryService = request.app.state.query_service
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        draft = await ensure_draft(conn, model, principal)
        snap, issues = _snapshot_or_issues(draft["definition"], model=model, version_id=str(draft["id"]))
        if snap is None:
            raise TelaError(ErrorCode.MODEL_INVALID, "The model has problems to fix first.", {"issues": issues})
        stamp_source_types(snap, await _catalog(conn, str(model["connection_id"])))
        connection = await service._connection(conn, str(model["connection_id"])) if data else None
    blocking = guards.shared_columns(draft["definition"])
    warnings = await _data_checks(service, snap, connection, principal, _rid(request)) \
        if connection is not None and snap.measures else None
    return {"draft_etag": _draft_etag(draft["draft_revision"]), "blocking": blocking, "warnings": warnings}


@router.post("/models/{model_id}/approve", status_code=201)
async def approve_model(model_id: str, body: ApproveIn, request: Request,
                        principal: Principal = Depends(require(Perm.MODEL_APPROVE))) -> dict[str, Any]:
    return await approve_draft(model_id, body, request, principal)


async def approve_draft(model_id: str, body: ApproveIn, request: Request, principal: Principal) -> dict[str, Any]:
    """Approve the model's draft as its next version (checks, dry run at the source, freeze, starter content). Also
    used when certifying a free-hand rule adds a measure (US-166); the caller has checked ``model.approve``."""
    service: QueryService = request.app.state.query_service
    # Phase 1: read and validate the draft (no locks held while the source is queried).
    async with tenant_tx(principal.tenant_id) as conn:
        model = await _model(conn, model_id)
        if not model["draft_version_id"]:
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "There is no draft to approve.")
        draft = await fetch_one(conn, "SELECT * FROM model.model_versions WHERE id = :id",
                                {"id": model["draft_version_id"]})
        assert draft is not None
        if body.draft_etag != _draft_etag(draft["draft_revision"]):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The draft changed since you reviewed it. Reload it.")
        snap, issues = _snapshot_or_issues(draft["definition"], model=model, version_id=str(draft["id"]))
        if snap is None:
            raise TelaError(ErrorCode.MODEL_INVALID, "The model has problems to fix first.", {"issues": issues})
        catalog = await _catalog(conn, str(model["connection_id"]))
        issues = catalog_issues(snap, catalog, await _unreadable(conn, str(model["connection_id"])))
        if issues:
            raise TelaError(ErrorCode.MODEL_INVALID, "The model does not match the source schema.", {"issues": issues})
        stamp_source_types(snap, catalog)  # before the dry run, so it compiles the casts the frozen version will
        # The draft keeps each entity's checksum from the approved version; it covers the columns the entity read
        # then. Fields added or removed since would look like source drift in the dry run (BUG-029), so check the
        # columns the draft reads against the last schema read instead (the same checksum the freeze will store).
        for ent in snap.entities:
            used = {f.source_column for f in ent.fields}
            ent.source_checksum = columns_checksum(
                (c["name"], c["type"]) for c in catalog.get(ent.source_object.lower(), []) if c["name"] in used)
        unaccepted = [ln for ln in snap.links if not ln.accepted]
        if unaccepted:
            raise TelaError(ErrorCode.UNAPPROVED_LINKS_IN_USE, "Accept or remove the proposed links first.",
                            {"links": [f"{ln.from_entity} → {ln.to_entity}" for ln in unaccepted]})
        open_q = await fetch_all(conn, "SELECT id, question FROM model.model_questions WHERE model_version_id = :v "
                                       "AND status = 'open' AND blocking", {"v": draft["id"]})
        if open_q:
            raise TelaError(ErrorCode.OPEN_QUESTIONS_UNRESOLVED, "Answer the open questions before approving.",
                            {"questions": [q["question"] for q in open_q]})
        if not snap.measures:
            raise TelaError(ErrorCode.MODEL_INVALID, "Add at least one measure before approving.")
        blocking = guards.shared_columns(draft["definition"])
        if blocking and not body.override_note:
            raise TelaError(ErrorCode.MODEL_INVALID, "Two fields read one source column. Fix the mapping, or approve "
                                                     "anyway with a note saying why.",
                            {"issues": blocking, "can_override": True})
        connection = await fetch_one(
            conn, "SELECT id, engine, status, descriptor, secret_ref, row_cap, timeout_ms, freshness_policy "
                  "FROM conn.connections WHERE id = :id", {"id": model["connection_id"]})
        assert connection is not None
        previous = await fetch_one(conn, "SELECT id, version_no, definition FROM model.model_versions WHERE id = :id",
                                   {"id": model["current_version_id"]}) if model["current_version_id"] else None

    # Phase 2: every measure must compile and run (LIMIT 0, deny-all scope) at the source.
    failures = await service.dry_run(snapshot=snap, connection=connection, principal=principal,
                                     ctx=QueryContext(surface="precheck", request_id=_rid(request), use_cache=False))
    if failures:
        raise TelaError(ErrorCode.MODEL_INVALID, "Some measures do not run against the source.",
                        {"issues": [{"ref": f"measure:{f['measure']}", "issue": f["message"]} for f in failures]})

    # Phase 3: freeze. Column checksums let the runner refuse queries if the source drifts (US-023).
    frozen = snap.model_dump(mode="json", exclude={"model_version_id", "connection_id", "version_no", "engine"},
                             by_alias=True)
    for ent in frozen["entities"]:
        cols = catalog[ent["source_object"].lower()]
        used = {f["source_column"] for f in ent["fields"]}
        ent["source_checksum"] = columns_checksum((c["name"], c["type"]) for c in cols if c["name"] in used)
    if connection["engine"] in ("excel", "csv"):  # US-108: the model pins the file version it was approved on
        pinned = dict((connection["descriptor"] or {}).get("file") or {})
        frozen["source_pin"] = {**pinned, "file_version": pinned.get("version")}
    else:
        frozen.pop("source_pin", None)
    async with tenant_tx(principal.tenant_id) as conn:
        locked = await fetch_one(conn, "SELECT draft_version_id, current_version_id, pending_pack_id FROM "
                                       "model.data_models WHERE id = :id FOR UPDATE", {"id": model["id"]})
        current_rev = await fetch_one(conn, "SELECT draft_revision FROM model.model_versions WHERE id = :id",
                                      {"id": draft["id"]})
        if (locked is None or locked["draft_version_id"] != draft["id"] or current_rev is None
                or current_rev["draft_revision"] != draft["draft_revision"]
                or locked["current_version_id"] != model["current_version_id"]):
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "Someone else changed or approved this model. Reload it.")
        version_no = (previous["version_no"] + 1) if previous else 1
        summary = body.note or _change_summary(previous["definition"] if previous else None, frozen)
        if previous:
            await conn.execute(text("UPDATE model.model_versions SET status = 'superseded' WHERE id = :id"),
                               {"id": previous["id"]})
        await conn.execute(
            text("UPDATE model.model_versions SET status = 'approved', version_no = :n, "
                 "definition = CAST(:d AS jsonb), schema_checksum = :cs, change_summary = :s, approved_by = :by, "
                 "approved_at = now() WHERE id = :id"),
            {"n": version_no, "d": json.dumps(frozen), "cs": definition_hash(frozen), "s": summary,
             "by": principal.user_id, "id": draft["id"]},
        )
        frozen_snap = build_snapshot(frozen, version_id=str(draft["id"]), connection_id=str(model["connection_id"]),
                                     version_no=version_no, engine=model["engine"])
        await _materialise(conn, principal.tenant_id, str(draft["id"]), frozen_snap)
        await conn.execute(
            text("UPDATE model.data_models SET current_version_id = :v, draft_version_id = NULL, status = 'ok', "
                 "broken_reasons = '[]' WHERE id = :id"), {"v": draft["id"], "id": model["id"]})
        await conn.execute(
            text("UPDATE conn.connection_objects SET is_allowed = (lower(qualified_name) = ANY(:objs)) "
                 "WHERE connection_id = :c"),
            {"objs": [e["source_object"].lower() for e in frozen["entities"]], "c": model["connection_id"]},
        )
        impact = _impact(previous["definition"] if previous else None, frozen)
        if blocking:
            await audit.record(
                conn, tenant_id=principal.tenant_id, action="model.approval_override", object_type="model_version",
                object_id=str(draft["id"]), outcome="success", actor_id=principal.user_id, severity="high",
                correlation_id=_rid(request),
                details={"model_id": model_id, "note": body.override_note, "issues": blocking})
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="model.approved", object_type="model_version",
            object_id=str(draft["id"]), outcome="success", actor_id=principal.user_id, severity="high",
            correlation_id=_rid(request),
            details={"model_id": model_id, "version": version_no, "snapshot_sha256": definition_hash(frozen),
                     "measures": len(frozen["measures"]), "breaking_changes": len(impact["breaking_changes"]),
                     "decision": "certified", "overridden": bool(blocking)},
        )
        await outbox.emit(conn, tenant_id=principal.tenant_id, topic="model.version_approved",
                          aggregate_id=str(draft["id"]),
                          payload={"model_id": model_id, "version": version_no,
                                   "connection_id": str(model["connection_id"])})
        await request.app.state.result_cache.invalidate_connection(conn, str(model["connection_id"]))
        if locked["pending_pack_id"]:
            # US-120: a pack update waited for this approval; the starter content moves to it before the rebuild.
            from tela_api.packs.updates import switch_pack  # the packs modules import this router

            await switch_pack(conn, tenant_id=principal.tenant_id, model_id=model_id,
                              to_pack_id=str(locked["pending_pack_id"]), by=principal.user_id, version_id=None,
                              request_id=_rid(request))
        await build_starter_reports(conn, tenant_id=principal.tenant_id, connection_id=str(model["connection_id"]),
                                    version_id=str(draft["id"]), by=principal.user_id)
        starters = await install_starter_dashboards(conn, tenant_id=principal.tenant_id,
                                                    connection_id=str(model["connection_id"]),
                                                    version_id=str(draft["id"]), by=principal.user_id)
    return {"model_version_id": str(draft["id"]), "version": version_no, "change_summary": summary, "impact": impact,
            "starter_dashboards": starters, "checks": {"blocking": blocking}}
