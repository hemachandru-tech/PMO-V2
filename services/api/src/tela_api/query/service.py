"""The single scoped execution path (US-022; REC-004, RISK-001; RULE-004…006, RULE-018).

``QueryService`` is the only caller of the runner's execute operation. Every
surface (widgets, chat, drill, export, filter options, previews, prechecks,
model approval dry runs) goes through ``_run``, which always:

1. resolves the caller's row scope and hidden fields for the connection;
2. validates the spec against the pinned approved model version;
3. compiles to dialect SQL with bound parameters only;
4. injects scope into every governed table and re-verifies it independently;
5. applies row and time limits, signs the statement and sends it to the runner;
6. records the execution (hashes and counts only, never values) and caches
   the result under a scope-keyed key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.core.metrics import CACHE_HIT_RATIO, QUERY_DURATION, VALIDATION_REJECTIONS
from tela_api.core.settings import Settings
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.identity.scope import EntityScope, ResolvedCondition, ResolvedScope, resolve_scope
from tela_api.model.store import current_version_id, load_snapshot
from tela_api.query import custom_sql
from tela_api.query import runner as runner_exec
from tela_api.query.asof import cache_ttl_seconds, declared_as_of, pin_dates, reporting_date, today_in
from tela_api.query.cache import ResultCache, cache_key
from tela_api.query.compiler import (
    ColumnMeta,
    CompiledQuery,
    Params,
)
from tela_api.query.compilers import (
    compile_spec,
    dialect_for,
    field_query,
    is_document,
    is_webapi,
    records_compiler,
)
from tela_api.query.dataverse import WebApiCompiled, assert_webapi_scoped
from tela_api.query.limits import Limits, apply_limit
from tela_api.query.mongo import MongoCompiled, assert_mongo_scoped
from tela_api.query.personal import with_personal
from tela_api.query.scope_injector import assert_scoped, inject
from tela_api.query.validator import HIDDEN_MESSAGE, Extra, FieldRef, SpecValidator
from tela_api.runner_transport import RunnerTransport
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_common.schema_checksum import columns_checksum
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import QuerySpec, parse_spec

log = get_logger(__name__)

USABLE_STATUSES = ("connected", "introspecting", "incomplete")
DRY_RUN_CONCURRENCY = 4  # TELA_RUNNER_CONCURRENCY_PER_CONNECTION default; more would only queue at the runner
_cache_hits = [0, 0]  # hits, lookups (for the hit-ratio gauge)


@dataclass
class QueryContext:
    surface: str = "preview"
    request_id: str | None = None
    widget_id: str | None = None
    turn_id: str | None = None
    page_filter_hash: str = ""
    use_cache: bool = True
    # US-254: an ad-hoc measure Plan B built from approved terms and fields runs under the connection's answer mode
    # (``ask_mode=fields``), not the person's free-hand grant; set by the orchestrator only, never from a request.
    fields: bool = False


@dataclass
class _Prepared:
    spec: QuerySpec
    connection: dict[str, Any]
    snapshot: ModelSnapshot
    scope: ResolvedScope
    vs: Any  # ValidatedSpec
    as_of_date: date


@dataclass
class _Plan:
    connection: dict[str, Any]
    snapshot: ModelSnapshot
    scope: ResolvedScope
    compiled: CompiledQuery
    requested_limit: int | None
    as_of_date: date
    model_version_id: str
    today: date | None = None  # set when the spec reads ``today`` (US-163): part of the cache key


def _statement_hash(sql: str, params: dict[str, Any]) -> str:
    return hashlib.sha256((sql + "\x00" + json.dumps(params, sort_keys=True, default=str)).encode()).hexdigest()


def source_descriptor(connection: dict[str, Any], snapshot: ModelSnapshot) -> dict[str, Any]:
    """The descriptor the runner reads: for a file source, the file version the model version pins (US-108)."""
    descriptor = dict(connection["descriptor"] or {})
    pin = snapshot.source_pin
    if connection["engine"] in ("excel", "csv") and pin and pin.get("object_key"):
        descriptor["file"] = {k: v for k, v in pin.items() if k != "file_version"}
    return descriptor


def file_as_of(connection: dict[str, Any], snapshot: ModelSnapshot) -> datetime | None:
    """An uploaded file is as fresh as its upload (RULE-018; US-108): the pinned version's upload time."""
    if connection["engine"] not in ("excel", "csv"):
        return None
    raw = (source_descriptor(connection, snapshot).get("file") or {}).get("uploaded_at")
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def deny_all_scope(snapshot: ModelSnapshot) -> ResolvedScope:
    """A scope that matches no rows (primary key IS NULL): used for model approval dry runs only."""
    entities = {
        e.key: EntityScope(alternatives=[(ResolvedCondition(e.primary_key[0], "is null"),)],
                           descriptions=["no rows (validation run)"])
        for e in snapshot.entities
    }
    return ResolvedScope(snapshot.connection_id, entities, frozenset(), frozenset())


def index_scope(snapshot: ModelSnapshot) -> ResolvedScope:
    """Every row, no field hidden: the value index's read only (US-173). The index is never shown as it is; each
    linked value is checked against the person's own scope when Ask uses it."""
    entities = {e.key: EntityScope(all_rows=True, descriptions=["all rows (value index)"]) for e in snapshot.entities}
    return ResolvedScope(snapshot.connection_id, entities, frozenset(), frozenset())


class QueryService:
    def __init__(self, transport: RunnerTransport | None, cache: ResultCache, settings: Settings) -> None:
        self.transport = transport
        self.cache = cache
        self.settings = settings

    # ------------------------------------------------------------------ public surfaces

    async def prepare(self, raw: dict[str, Any] | QuerySpec, principal: Principal,
                      ctx: QueryContext) -> _Prepared:
        """Parse, load the pinned model, resolve scope and validate: the common front half of every surface."""
        try:
            spec, stripped = (raw, []) if isinstance(raw, QuerySpec) else parse_spec(raw)
        except ValueError as exc:
            VALIDATION_REJECTIONS.labels(code="spec_invalid").inc()
            raise TelaError(ErrorCode.SPEC_INVALID, "The question could not be understood as a valid query.",
                            {"admin_detail": {"errors": str(exc)[:2000]}}) from exc
        if stripped:
            log.warning("query.client_scope_stripped", keys=stripped)

        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, spec.connection_id)
            snapshot = await load_snapshot(conn, principal.tenant_id, spec.model_version_id)
            if snapshot.connection_id != str(connection["id"]):
                raise TelaError(ErrorCode.ONE_DATABASE_PER_WIDGET, "A widget reads one database only.")
            scope = await self._scope(conn, principal, spec.connection_id, ctx)
        if isinstance(scope, TelaError):
            raise scope
        spec, snapshot = with_personal(spec, snapshot)
        as_of_date = reporting_date(snapshot.settings, principal.time_zone)

        enum_values = await self._enum_values(spec, snapshot, scope, connection, principal, ctx)
        validator = SpecValidator(snapshot, hidden_fields=scope.hidden_fields,
                                  row_cap=min(connection["row_cap"], self.settings.query_max_rows),
                                  enum_values=lambda f: enum_values.get(f.qualified),
                                  freehand=spec.adhoc is not None and (
                                      principal.can(Perm.ASK_FREEHAND, spec.connection_id)
                                      or ctx.fields
                                      # US-166: an Admin reviews a free-hand rule sent for certification.
                                      or (ctx.surface == "certification_preview"
                                          and principal.has(Perm.CERTIFICATION_DECIDE))))
        try:
            vs = pin_dates(validator.validate(spec), as_of=as_of_date, today=today_in(principal.time_zone))
        except TelaError as exc:
            VALIDATION_REJECTIONS.labels(code=exc.code).inc()
            if spec.adhoc is not None:
                await self._freehand_audit(principal, spec, ctx, "deny", exc.message)
            raise
        if not vs.certified:
            await self._freehand_audit(principal, spec, ctx, "allow", None)
        return _Prepared(spec, connection, snapshot, scope, vs, as_of_date)

    async def _freehand_audit(self, principal: Principal, spec: QuerySpec, ctx: QueryContext,
                              outcome: Literal["allow", "deny"], refusal: str | None) -> None:
        """Every free-hand run (and refusal) is audited as ``query.freehand``; a run marks the grant used."""
        from tela_api.identity import grants  # imports content modules that import this one

        assert spec.adhoc is not None
        async with tenant_tx(principal.tenant_id) as conn:
            if outcome == "allow":
                await grants.mark_used(conn, principal.user_id, Perm.ASK_FREEHAND, spec.connection_id)
            await audit.record(
                conn, tenant_id=principal.tenant_id, action="query.freehand", object_type="connection",
                object_id=spec.connection_id, outcome=outcome, actor_id=principal.user_id,
                correlation_id=ctx.request_id,
                details={"surface": ctx.surface, "entity": spec.adhoc.entity, "agg": spec.adhoc.agg,
                         "label": spec.adhoc.label, "plain_rule": spec.adhoc.plain_rule,
                         "rule": spec.adhoc.model_dump(mode="json", exclude_none=True, by_alias=True)["where"]
                         if spec.adhoc.where is not None else None, **({"refusal": refusal} if refusal else {})})

    async def execute(self, raw: dict[str, Any] | QuerySpec, principal: Principal,
                      ctx: QueryContext) -> dict[str, Any]:
        """Run a Query Spec for a person on one surface."""
        pr = await self.prepare(raw, principal, ctx)
        base = self._result_base(pr.spec, pr.snapshot, pr.scope, pr.vs.measures, certified=pr.vs.certified,
                                 extras=pr.vs.extras)
        if pr.vs.hidden:
            # A widget that depends on a hidden field shows "Hidden for your role" and runs nothing (US-020).
            return {**base, "hidden": True, "message": HIDDEN_MESSAGE, "columns": [], "rows": [], "row_count": 0,
                    "truncated": False, "as_of": None, "cache_hit": False}
        compiled = compile_spec(pr.connection, pr.vs, as_of=pr.as_of_date)
        plan = _Plan(pr.connection, pr.snapshot, pr.scope, compiled, pr.spec.limit, pr.as_of_date,
                     pr.spec.model_version_id, pr.vs.today)
        result = await self._run(plan, principal, ctx)
        return {**base, **result, "hidden": False, "visual_hint": pr.spec.visual.type if pr.spec.visual else None}

    async def custom_sql_check(self, sql: str, *, connection_id: str, principal: Principal, ctx: QueryContext,
                               model_version_id: str | None = None) -> tuple[Any, ModelSnapshot, dict[str, Any]]:
        """Validate custom SQL for this person (FEAT-031). Raises ``custom_sql.CheckFailed`` at the first failure."""
        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, connection_id)
            version_id = model_version_id or await current_version_id(conn, connection_id)
            if version_id is None:
                raise TelaError(ErrorCode.MODEL_NOT_APPROVED, "This data source has no approved model yet.")
            snapshot = await load_snapshot(conn, principal.tenant_id, version_id)
            scope = await self._scope(conn, principal, connection_id, ctx)
        if isinstance(scope, TelaError):
            raise custom_sql.CheckFailed("scope_applied", scope, [
                {"name": "scope_applied", "ok": False, "code": str(scope.code), "detail": scope.message}])
        prepared = custom_sql.validate(sql, engine=connection["engine"], descriptor=connection["descriptor"],
                                       snap=snapshot, scope=scope)
        return prepared, snapshot, connection

    async def execute_custom(self, sql: str, *, connection_id: str, model_version_id: str | None,
                             principal: Principal, ctx: QueryContext,
                             page_filters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Run checked custom SQL through the same scoped path: scope injection, assert, limits, signing."""
        try:
            prepared, snapshot, connection = await self.custom_sql_check(
                sql, connection_id=connection_id, principal=principal, ctx=ctx, model_version_id=model_version_id)
        except custom_sql.CheckFailed as failed:
            raise failed.error from failed
        async with tenant_tx(principal.tenant_id) as conn:
            scope = await self._scope(conn, principal, connection_id, ctx)
        if isinstance(scope, TelaError):
            raise scope
        cq, applied, skipped = custom_sql.compiled(prepared, page_filters or [], snapshot)
        plan = _Plan(connection, snapshot, scope, cq, prepared.requested_limit,
                     reporting_date(snapshot.settings, principal.time_zone), snapshot.model_version_id)
        result = await self._run(plan, principal, ctx)
        return {"model_version": {"id": snapshot.model_version_id, "version_no": snapshot.version_no},
                "connection_id": connection_id, "scope": {"summary": scope.summary, "locked": True},
                "badge": "custom_sql", "definitions": [], **result, "hidden": False,
                "filters_applied": applied, "filters_not_applied": skipped, "warnings": prepared.warnings}

    async def records(self, raw: dict[str, Any] | QuerySpec, principal: Principal, ctx: QueryContext, *,
                      marks: dict[str, Any], offset: int = 0, limit: int = 100, cap: int = 1000,
                      columns: list[str] | None = None, order: list[tuple[str, bool]] | None = None) -> dict[str, Any]:
        """Records behind a mark, through the same scoped path (FEAT-044; RULE-025).

        A detail table (US-158) names its ``columns`` (fields of the measure's entity; the key always comes first so
        rows never merge) and an ``order`` of ``(field, descending)``; drill uses the entity's record fields, 100 a
        page. Compilers that can't sort records server-side get the page sorted here.
        """
        pr = await self.prepare(raw, principal, ctx)
        if pr.vs.hidden:
            return {"hidden": True, "message": HIDDEN_MESSAGE, "columns": [], "rows": []}
        if offset >= cap:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Only the first {cap:,} records can be listed.")
        limit = min(limit, cap - offset) if columns is not None else min(limit, 100, cap - offset)
        hidden = pr.scope.hidden_fields
        root = pr.vs.root
        wanted = root.record_fields() if columns is None else \
            list(dict.fromkeys([*root.primary_key, *(k for k in columns if root.field(k) is not None)]))
        fields = [k for k in wanted if f"{root.key}.{k}" not in hidden]
        sort = [(k, desc) for k, desc in order or [] if k in fields]
        compiler = records_compiler(pr.connection)
        sorts = getattr(compiler, "sorts_records", False)
        page = compiler.compile_records(pr.vs, marks=marks, as_of=pr.as_of_date, fields=fields, limit=limit,
                                        offset=offset, **({"order": sort} if sorts and sort else {}))
        counter = compiler.compile_records(pr.vs, marks=marks, as_of=pr.as_of_date, fields=fields, limit=0,
                                           offset=0, count_only=True)
        common = (pr.connection, pr.snapshot, pr.scope)
        total = await self._run(_Plan(*common, counter, None, pr.as_of_date, pr.spec.model_version_id, pr.vs.today),
                                principal, ctx)
        rows = await self._run(_Plan(*common, page, limit, pr.as_of_date, pr.spec.model_version_id, pr.vs.today),
                               principal, ctx)
        n = int(total["rows"][0][0]) if total["rows"] else 0
        if sort and not sorts:
            names = [c["name"] for c in rows["columns"]]
            for k, desc in reversed(sort):
                i = names.index(k)
                rows["rows"].sort(key=lambda r, i=i: (r[i] is None, r[i] if r[i] is not None else ""), reverse=desc)
        return {"columns": rows["columns"], "rows": rows["rows"], "total": n, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < min(n, cap) else None,
                "capped": n > cap, "notice": f"Showing first {cap:,} of {n:,} — narrow filters to see more."
                if n > cap else None, "as_of": rows["as_of"], "scope": {"summary": pr.scope.summary, "locked": True},
                "entity": pr.vs.root.key, "primary_key": pr.vs.root.primary_key}

    async def check(self, raw: dict[str, Any] | QuerySpec, principal: Principal, ctx: QueryContext) -> None:
        """Validate and compile a spec without running it (US-181: a planned spec's refusals go back to AG-03);
        raises the ``TelaError`` that ``execute`` would."""
        pr = await self.prepare(raw, principal, ctx)
        if pr.vs.hidden:
            return
        try:
            compile_spec(pr.connection, pr.vs, as_of=pr.as_of_date)
        except TelaError as exc:
            exc.details = {**exc.details, "stage": "compile"}
            raise

    async def explain(self, raw: dict[str, Any] | QuerySpec, principal: Principal, ctx: QueryContext) -> dict[str, Any]:
        """The compiled SQL with the scope part labelled, without running it ("Show query", FEAT-043)."""
        pr = await self.prepare(raw, principal, ctx)
        if pr.vs.hidden:
            return {"hidden": True, "message": HIDDEN_MESSAGE}
        compiled = compile_spec(pr.connection, pr.vs, as_of=pr.as_of_date)
        if isinstance(compiled, MongoCompiled):
            import json as _json

            limits = Limits.for_connection(pr.connection["row_cap"], pr.connection["timeout_ms"])
            statement = compiled.render(pr.snapshot, pr.scope, pr.spec.limit or limits.max_rows + 1)
            from tela_api.query.mongo import scope_query

            return {"sql": _json.dumps(_json.loads(statement), indent=2), "dialect": "mongodb",
                    "scope": [{"entity": e, "object": ent.source_object,
                               "predicate": _json.dumps(scope_query(ent, pr.scope), default=str)}
                              for e in sorted(set(compiled.entities.values()))
                              if (ent := pr.snapshot.entity(e)) is not None],
                    "note": "The scope $match is the first stage of the pipeline and of every $lookup."}
        tree = inject(compiled.tree, pr.snapshot, pr.scope, compiled.params)
        tree, _, _ = apply_limit(tree, Limits.for_connection(pr.connection["row_cap"], pr.connection["timeout_ms"]),
                                 pr.spec.limit)
        sql = tree.sql(dialect=compiled.dialect, pretty=True)
        scope_parts = []
        for entity in sorted(set(compiled.entities.values())):
            ent = pr.snapshot.entity(entity)
            assert ent is not None
            from tela_api.query.scope_injector import _inline, scope_predicate

            bag = Params("x")
            scope_parts.append({"entity": entity, "object": ent.source_object,
                                "predicate": _inline(scope_predicate(ent, pr.scope, bag, pr.snapshot),
                                                     bag.values)})
        return {"sql": sql, "dialect": compiled.dialect, "scope": scope_parts,
                "note": "Values are sent as bound parameters; the scope predicate wraps every table."}

    async def filter_values(self, *, connection_id: str, model_version_id: str, ref: str, principal: Principal,
                            ctx: QueryContext, limit: int = 500) -> dict[str, Any]:
        """Filter options computed under the caller's scope: out-of-scope values are never listed (RULE-005)."""
        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, connection_id)
            snapshot = await load_snapshot(conn, principal.tenant_id, model_version_id)
            scope = await self._scope(conn, principal, connection_id, ctx)
        if isinstance(scope, TelaError):
            raise scope
        fref = self._field_ref(snapshot, ref)
        if fref.qualified in scope.hidden_fields:
            return {"ref": ref, "hidden": True, "message": HIDDEN_MESSAGE, "values": []}
        if fref.computed:  # computed dimensions (US-156) list their labels, not stored values
            return {"ref": ref, "hidden": False, "values": list(fref.sort_order or fref.computed.labels()),
                    "truncated": False, "scope": {"summary": scope.summary, "locked": True}}
        values = await self._distinct(snapshot, scope, connection, fref, principal, ctx, limit)
        labels = dict(fref.value_labels)
        return {"ref": ref, "hidden": False, "values": [labels.get(str(v), v) for v in values],
                "truncated": len(values) >= limit, "scope": {"summary": scope.summary, "locked": True}}

    async def field_profile(self, *, connection_id: str, model_version_id: str, ref: str, principal: Principal,
                            ctx: QueryContext, top: int = 5) -> dict[str, Any]:
        """One dimension's profile under the caller's scope, for AG-03 (US-175): type, fill rate, the most frequent
        values with their row counts and, for dates, the earliest and latest value. Counts only, never rows; a part
        the engine can't compute is left out. A hidden field says only that it is hidden."""
        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, connection_id)
            snapshot = await load_snapshot(conn, principal.tenant_id, model_version_id)
            scope = await self._scope(conn, principal, connection_id, ctx)
        if isinstance(scope, TelaError):
            raise scope
        fref = self._field_ref(snapshot, ref)
        if fref.qualified in scope.hidden_fields:
            return {"ref": ref, "label": fref.label, "hidden": True}
        out: dict[str, Any] = {"ref": ref, "label": fref.label, "type": fref.field.data_type}
        if fref.computed:
            return {**out, "values": list(fref.sort_order or fref.computed.labels())[:top]}
        sub = QueryContext(surface="field_profile", request_id=ctx.request_id, use_cache=ctx.use_cache)
        as_of = reporting_date(snapshot.settings, None)

        async def read(kind: str, limit: int | None = None) -> list[list[Any]] | None:
            try:
                compiled = field_query(connection, snapshot, fref, kind, limit=limit)
            except ValueError:  # not on this engine
                return None
            plan = _Plan(connection, snapshot, scope, compiled, limit, as_of, snapshot.model_version_id)
            try:
                return [list(r) for r in (await self._run(plan, principal, sub))["rows"]]
            except TelaError as exc:
                log.info("query.field_profile_part_failed", kind=kind, code=str(exc.code))
                return None

        total, filled = await read("count"), await read("filled")
        if total and filled and total[0][0]:
            out["rows"] = int(total[0][0])
            out["fill_rate"] = round(int(filled[0][0] or 0) / int(total[0][0]), 3)
        if fref.field.data_type in ("date", "datetime"):
            low, high = await read("min"), await read("max")
            if low and high and low[0][0] is not None:
                out["range"] = [str(low[0][0])[:10], str(high[0][0])[:10]]
            return out
        labels = dict(fref.value_labels)
        common = await read("top", top)
        if common is not None:
            out["values"] = [{"value": str(labels.get(str(v), v)), "rows": int(n)} for v, n in common]
        return out

    async def index_values(self, *, snapshot: ModelSnapshot, connection_id: str, ref: str, principal: Principal,
                           limit: int) -> list[Any]:
        """Distinct stored values of one dimension over every row, for the value index (US-173). ``principal`` is
        the approver the read is recorded against; their own scope is not applied (``index_scope``)."""
        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, connection_id)
        fref = self._field_ref(snapshot, ref)
        ctx = QueryContext(surface="value_index", use_cache=False)
        return await self._distinct(snapshot, index_scope(snapshot), connection, fref, principal, ctx, limit,
                                    surface="value_index")

    async def rows_in_scope(self, *, snapshot: ModelSnapshot, scope: ResolvedScope, connection_id: str,
                            entity: str, principal: Principal, ctx: QueryContext) -> int:
        """Row count of one entity under a given person's scope (scope preview, US-015)."""
        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, connection_id)
        ent = snapshot.entity(entity)
        if ent is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That entity is not in the approved model.")
        pk = ent.field(ent.primary_key[0])
        assert pk is not None
        compiled = field_query(connection, snapshot, FieldRef(entity, pk, pk.label), "count")
        plan = _Plan(connection, snapshot, scope, compiled, None, reporting_date(snapshot.settings, None),
                     snapshot.model_version_id)
        result = await self._run(plan, principal, ctx)
        return int(result["rows"][0][0]) if result["rows"] else 0

    async def dry_run(self, *, snapshot: ModelSnapshot, connection: dict[str, Any], principal: Principal,
                      ctx: QueryContext) -> list[dict[str, Any]]:
        """Compile and run every measure with LIMIT 0 under a deny-all scope (US-035 approval check).

        No row can be returned: every governed table is wrapped in ``WHERE <pk> IS NULL`` and the
        statement is limited to zero rows. The source still parses and plans the SQL, so a broken
        measure fails here instead of on a dashboard.
        """
        scope = deny_all_scope(snapshot)
        # A pack has ~140 measures: run them a few at a time, matching the runner's per-connection slots.
        slots = asyncio.Semaphore(DRY_RUN_CONCURRENCY)

        async def check(m: Any) -> dict[str, Any] | None:
            spec = QuerySpec.model_validate({"model_version_id": snapshot.model_version_id,
                                             "connection_id": snapshot.connection_id,
                                             "measure": {"ref": f"measure:{m.key}"}})
            try:
                as_of = reporting_date(snapshot.settings, None)
                vs = pin_dates(SpecValidator(snapshot).validate(spec), as_of=as_of, today=today_in(None))
                compiled = compile_spec(connection, vs, as_of=as_of)
                plan = _Plan(connection, snapshot, scope, compiled, 0, date.today(), snapshot.model_version_id)
                async with slots:
                    await self._run(plan, principal, ctx, dry_run=True)
            except TelaError as exc:
                if exc.code == ErrorCode.MEASURE_UNAVAILABLE:
                    return None  # no shape on this source (Dataverse Web API): shown as Unavailable, not a failure
                return {"measure": m.key, "code": exc.code, "message": exc.message}
            return None

        results = await asyncio.gather(*(check(m) for m in snapshot.measures))
        return [f for f in results if f is not None]

    async def trial(self, *, snapshot: ModelSnapshot, connection: dict[str, Any], principal: Principal,
                    ctx: QueryContext, fields: list[str], measures: bool,
                    top: int = 50) -> tuple[dict[str, list[dict[str, Any]] | None], dict[str, Any]]:
        """Mapping guards (US-199): the most frequent values of some fields (``entity.field``) with their row counts,
        and each measure's figure, over every row of a draft or a pack about to be bound (``index_scope``, as the
        value index). Aggregates only; a part that fails or has no shape on this source is ``None``."""
        scope = index_scope(snapshot)
        slots = asyncio.Semaphore(DRY_RUN_CONCURRENCY)
        as_of = reporting_date(snapshot.settings, None)

        async def values(ref: str) -> list[dict[str, Any]] | None:
            try:
                fref = self._field_ref(snapshot, f"field:{ref}")
                compiled = field_query(connection, snapshot, fref, "top", limit=top)
                plan = _Plan(connection, snapshot, scope, compiled, top, as_of, snapshot.model_version_id)
                async with slots:
                    rows = (await self._run(plan, principal, ctx))["rows"]
            except (TelaError, ValueError) as exc:
                log.info("query.trial_values_failed", ref=ref, error=str(getattr(exc, "code", exc)))
                return None
            return [{"value": None if v is None else str(v), "rows": int(n or 0)} for v, n in rows]

        async def figure(m: Any) -> Any:
            spec = QuerySpec.model_validate({"model_version_id": snapshot.model_version_id,
                                             "connection_id": snapshot.connection_id,
                                             "measure": {"ref": f"measure:{m.key}"}})
            try:
                vs = pin_dates(SpecValidator(snapshot).validate(spec), as_of=as_of, today=today_in(None))
                compiled = compile_spec(connection, vs, as_of=as_of)
                plan = _Plan(connection, snapshot, scope, compiled, None, as_of, snapshot.model_version_id)
                async with slots:
                    rows = (await self._run(plan, principal, ctx))["rows"]
            except TelaError as exc:
                log.info("query.trial_measure_failed", measure=m.key, code=str(exc.code))
                return None
            return rows[0][0] if rows and rows[0] else None

        profiles = dict(zip(fields, await asyncio.gather(*(values(r) for r in fields)), strict=True))
        figures: dict[str, Any] = {}
        if measures:
            got = await asyncio.gather(*(figure(m) for m in snapshot.measures))
            figures = {m.key: v for m, v in zip(snapshot.measures, got, strict=True)}
        return profiles, figures

    async def column_is_unique(self, *, principal: Principal, connection_id: str, source_object: str,
                               column: str) -> bool:
        """Model building (AG-04 link check): is a key column unique? Two counts, no values (TEST-009).

        Runs before a model is approved, so there is no scope model yet; it is limited to Admins with
        ``model.edit``, returns only whether two aggregate counts are equal, and is recorded and audited.
        """
        from sqlglot import exp as sqlexp

        async with tenant_tx(principal.tenant_id) as conn:
            connection = await self._connection(conn, connection_id)
            known = await fetch_one(conn, "SELECT columns FROM conn.connection_objects WHERE connection_id = "
                                          "CAST(:c AS uuid) AND lower(qualified_name) = lower(:o) "
                                          "AND removed_at IS NULL",
                                    {"c": connection_id, "o": source_object})
        if known is None or column not in {c["name"] for c in known["columns"]}:
            raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "That column is not in the introspected catalogue.")
        if is_document(connection["engine"]):
            from bson import json_util

            if "#" in source_object:
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "Array entities are keyed by their parent.")
            sql = json_util.dumps({"collection": source_object, "columns": ["n", "d"], "empty_row": [0, 0],
                                   "pipeline": [{"$match": {}},
                                                {"$group": {"_id": {"$ifNull": [f"${column}", None]},
                                                            "c": {"$sum": 1}}},
                                                {"$group": {"_id": None, "n": {"$sum": "$c"}, "d": {"$sum": 1}}},
                                                {"$project": {"_id": 0, "n": 1, "d": 1}}]})
        else:
            schema, _, name = source_object.rpartition(".")
            c = sqlexp.column(column, quoted=True)
            tree = (sqlexp.select(sqlexp.Count(this=sqlexp.Star()).as_("n"),
                                  sqlexp.Count(this=sqlexp.Distinct(expressions=[c])).as_("d"))
                    .from_(sqlexp.Table(this=sqlexp.to_identifier(name), db=sqlexp.to_identifier(schema) if schema
                                        else None)).limit(1))
            sql = tree.sql(dialect=dialect_for(connection["engine"]))
        if self.transport is None:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The connector service is not configured.")
        limits = Limits.for_connection(connection["row_cap"], connection["timeout_ms"])
        payload = {
            "execution_id": new_id(), "tenant_id": principal.tenant_id, "connection_id": str(connection["id"]),
            "engine": connection["engine"], "descriptor": connection["descriptor"],
            "secret_ref": connection["secret_ref"], "statement_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "params": {}, "scope_hash": "model-building", "limits": limits.as_dict(),
            "allowed_objects": {source_object.lower(): {}}, "column_tags": {"n": {"sensitivity": "public"},
                                                                            "d": {"sensitivity": "public"}},
        }
        raw = await runner_exec.execute(self.transport, payload=payload, statement=sql,
                                        timeout_s=limits.timeout_ms / 1000 + 10)
        n, d = raw["rows"][0] if raw["rows"] else (0, 0)
        async with tenant_tx(principal.tenant_id) as conn:
            await conn.execute(
                text("INSERT INTO ops.query_executions (id, tenant_id, connection_id, surface, user_id, scope_hash, "
                     "statement_hash, row_count, duration_ms, outcome) VALUES (:id, :t, CAST(:c AS uuid), 'precheck', "
                     "CAST(:u AS uuid), 'model-building', :sh, 1, :d, 'ok')"),
                {"id": new_id(), "t": principal.tenant_id, "c": connection_id, "u": principal.user_id,
                 "sh": _statement_hash(sql, {}), "d": raw.get("duration_ms")})
            await audit.record(conn, tenant_id=principal.tenant_id, action="model.cardinality_checked",
                               object_type="connection", object_id=connection_id, outcome="success",
                               actor_id=principal.user_id, correlation_id=None,
                               details={"object": source_object, "column": column, "unique": n == d})
        return bool(n == d)

    # ------------------------------------------------------------------ the one execution path

    async def _run(self, plan: _Plan, principal: Principal, ctx: QueryContext, *,
                   dry_run: bool = False, nested: bool = False) -> dict[str, Any]:
        connection, snapshot, scope = plan.connection, plan.snapshot, plan.scope
        compiled = plan.compiled
        limits = Limits.for_connection(connection["row_cap"], connection["timeout_ms"])
        try:
            if isinstance(compiled, MongoCompiled | WebApiCompiled):
                if dry_run:
                    rows_wanted, detect = 0, False
                elif plan.requested_limit is not None:
                    rows_wanted, detect = min(plan.requested_limit, limits.max_rows), False
                else:
                    rows_wanted, detect = limits.max_rows, True
                sql = compiled.render(snapshot, scope, 1 if dry_run else rows_wanted + (1 if detect else 0))
                if isinstance(compiled, WebApiCompiled):
                    assert_webapi_scoped(sql, snapshot, scope, connection.get("_columns") or {})
                else:
                    assert_mongo_scoped(sql, snapshot, scope)
            else:
                tree = inject(compiled.tree, snapshot, scope, compiled.params)
                if dry_run:
                    tree, rows_wanted, detect = tree.limit(0, copy=False), 0, False
                else:
                    tree, rows_wanted, detect = apply_limit(tree, limits, plan.requested_limit)
                sql = tree.sql(dialect=compiled.dialect)
                assert_scoped(sql, compiled.dialect, snapshot, scope, compiled.params.values)
        except TelaError as exc:
            if exc.code in (ErrorCode.SCOPE_REWRITE_FAILED, ErrorCode.SCOPE_UNRESOLVED):
                await self._scope_refusal(principal, connection, ctx, exc)
            raise
        params = compiled.params.values
        statement_hash = _statement_hash(sql, params)
        policy = connection["freshness_policy"] or {"mode": "live"}
        ttl = 0 if dry_run or not ctx.use_cache else cache_ttl_seconds(policy, self.settings.cache_ttl_seconds)
        key = cache_key(tenant_id=principal.tenant_id, connection_id=str(connection["id"]),
                        model_version_id=plan.model_version_id, statement_hash=statement_hash,
                        scope_hash=scope.scope_hash, page_filter_hash=ctx.page_filter_hash,
                        as_of_bucket=plan.as_of_date.isoformat() + (f"/{plan.today}" if plan.today else ""))
        if ttl > 0:
            _cache_hits[1] += 1
            hit = await self.cache.get(key)
            if hit is not None:
                _cache_hits[0] += 1
                CACHE_HIT_RATIO.set(_cache_hits[0] / _cache_hits[1])
                await self._record(principal, connection, ctx, scope, statement_hash, hit["row_count"],
                                   hit["truncated"], 0, "ok", cache_hit=True, sql=sql)
                return {**hit, "cache_hit": True}

        if self.transport is None:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The connector service is not configured.")
        execution_id = new_id()
        payload = {
            "execution_id": execution_id,
            "tenant_id": principal.tenant_id,
            "connection_id": str(connection["id"]),
            "engine": connection["engine"],
            "descriptor": source_descriptor(connection, snapshot),
            "secret_ref": connection["secret_ref"],
            "statement_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "params": runner_exec.encode_params(params),
            "scope_hash": scope.scope_hash,
            "limits": {**limits.as_dict(), "max_rows": rows_wanted if not detect else limits.max_rows},
            "allowed_objects": self._allowed_objects(snapshot, compiled, scope),
            "column_tags": self._column_tags(snapshot, compiled.columns),
            "request_id": ctx.request_id,
        }
        started = time.monotonic()
        outcome = "ok"
        try:
            raw = await runner_exec.execute(self.transport, payload=payload, statement=sql,
                                            timeout_s=limits.timeout_ms / 1000 + 10)
        except TelaError as exc:
            outcome = {"query_timeout": "timeout", "signature_invalid": "rejected",
                       "sql_forbidden_node": "rejected", "sql_object_not_allowed": "rejected",
                       "schema_changed": "rejected"}.get(exc.code, "error")
            await self._record(principal, connection, ctx, scope, statement_hash, None, False,
                               int((time.monotonic() - started) * 1000), outcome, sql=sql)
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        QUERY_DURATION.labels(engine=connection["engine"]).observe(duration_ms / 1000)
        rows = raw["rows"]
        truncated = bool(raw.get("truncated")) and detect
        rows = self._mask_policy_fields(scope, compiled.columns, rows)
        # DuckDB post-step for window operations on MongoDB/Dataverse results (US-177)
        post = compiled.duckdb_post if isinstance(compiled, MongoCompiled | WebApiCompiled) else None
        if post is not None:  # with no rows too, so the window columns are still listed
            import dataclasses as _dc

            from tela_api.query.duckdb_post import apply_post
            rows, all_columns = apply_post(rows, compiled.columns, *post)
            compiled = _dc.replace(compiled, columns=all_columns)
        queried_at = datetime.fromisoformat(raw["as_of"])
        as_of = declared_as_of(policy) or file_as_of(connection, snapshot) or queried_at
        if policy.get("mode") == "source_asof" and policy.get("entity") and policy.get("field") \
                and not dry_run and not nested:
            as_of = await self._source_column_as_of(plan, principal, ctx) or as_of
        notes: list[str] = []
        if truncated:
            notes.append(f"Showing first {rows_wanted:,} rows (truncated)")
        if not rows and not dry_run:
            notes.append("No records match the current scope and filters.")
        result = {
            "execution_id": execution_id,
            "columns": [self._column_out(c) for c in compiled.columns],
            "rows": rows, "row_count": len(rows), "truncated": truncated,
            "as_of": as_of.isoformat(), "freshness": policy.get("mode", "live"), "notes": notes,
        }
        await self._record(principal, connection, ctx, scope, statement_hash, len(rows), truncated, duration_ms,
                           outcome, sql=sql, cache_entry=(key, queried_at, ttl, result) if ttl > 0 else None)
        return {**result, "cache_hit": False}

    # ------------------------------------------------------------------ helpers

    async def _connection(self, conn: Any, connection_id: str) -> dict[str, Any]:
        row = await fetch_one(
            conn,
            "SELECT id, engine, status, descriptor, secret_ref, row_cap, timeout_ms, freshness_policy "
            "FROM conn.connections WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL",
            {"id": connection_id},
        )
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "That data source does not exist.")
        if is_webapi(row):  # choice options and label columns for the Web API compiler (US-111)
            row = dict(row)
            objects = await fetch_all(conn, "SELECT qualified_name, columns FROM conn.connection_objects WHERE "
                                            "connection_id = :c AND removed_at IS NULL", {"c": row["id"]})
            row["_columns"] = {o["qualified_name"].lower(): {c["name"]: c for c in o["columns"]} for o in objects}
        if row["status"] == "disabled":
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "This data source is unavailable right now.")
        if row["status"] not in USABLE_STATUSES:
            raise TelaError(ErrorCode.CONNECTION_NOT_READY, "This data source has not passed its connection test.")
        return row

    async def _scope(self, conn: Any, principal: Principal, connection_id: str,
                     ctx: QueryContext) -> ResolvedScope | TelaError:
        """Resolve scope; a refusal is audited and *returned*, so the caller raises it after the commit
        (raising inside the transaction would roll the audit event back)."""
        try:
            return await resolve_scope(conn, principal, connection_id)
        except TelaError as exc:
            if exc.code != ErrorCode.SCOPE_UNRESOLVED:
                raise
            await audit.record(
                conn, tenant_id=principal.tenant_id, action="query.scope_unresolved", object_type="connection",
                object_id=connection_id, outcome="deny", actor_id=principal.user_id,
                correlation_id=ctx.request_id, details={"surface": ctx.surface, **exc.details},
            )
            return exc

    async def _scope_refusal(self, principal: Principal, connection: dict[str, Any], ctx: QueryContext,
                             exc: TelaError) -> None:
        """Audit a refused query in its own transaction. A failed rewrite is a security event (fail closed);
        an unresolved scope (no policy or missing attribute) is a normal deny."""
        rewrite = exc.code == ErrorCode.SCOPE_REWRITE_FAILED
        async with tenant_tx(principal.tenant_id) as conn:
            await audit.record(
                conn, tenant_id=principal.tenant_id,
                action="query.scope_refused" if rewrite else "query.scope_unresolved", object_type="connection",
                object_id=str(connection["id"]), outcome="deny", actor_id=principal.user_id,
                severity="security" if rewrite else "normal", correlation_id=ctx.request_id,
                details={"surface": ctx.surface,
                         "reason": exc.details.get("admin_detail", {}).get("reason") if rewrite else None,
                         "entity": exc.details.get("entity")},
            )

    async def _record(self, principal: Principal, connection: dict[str, Any], ctx: QueryContext,
                      scope: ResolvedScope, statement_hash: str, row_count: int | None, truncated: bool,
                      duration_ms: int, outcome: str, *, sql: str, cache_hit: bool = False,
                      cache_entry: tuple[str, datetime, int, dict[str, Any]] | None = None) -> None:
        async with tenant_tx(principal.tenant_id) as conn:
            await conn.execute(
                text(
                    "INSERT INTO ops.query_executions (id, tenant_id, connection_id, surface, turn_id, widget_id, "
                    "user_id, scope_hash, statement_hash, statement_text, row_count, truncated, duration_ms, "
                    "outcome, cache_hit) VALUES (:id, :t, CAST(:c AS uuid), :s, CAST(:turn AS uuid), "
                    "CAST(:w AS uuid), CAST(:u AS uuid), :sh, :st, :txt, :rc, :tr, :d, :o, :ch)"
                ),
                {"id": new_id(), "t": principal.tenant_id, "c": str(connection["id"]), "s": ctx.surface,
                 "turn": ctx.turn_id, "w": ctx.widget_id, "u": principal.user_id, "sh": scope.scope_hash,
                 "st": statement_hash, "txt": sql if self.settings.store_statement_text else None,
                 "rc": row_count, "tr": truncated, "d": duration_ms, "o": outcome, "ch": cache_hit},
            )
            if cache_entry is not None:
                key, filled_at, ttl, value = cache_entry
                await self.cache.put(conn, key=key, tenant_id=principal.tenant_id, connection_id=str(connection["id"]),
                                     widget_id=ctx.widget_id, as_of=filled_at, ttl_s=ttl, value=value)

    @staticmethod
    def _allowed_objects(snapshot: ModelSnapshot, compiled: CompiledQuery,
                         scope: ResolvedScope | None = None) -> dict[str, dict[str, Any]]:
        """The source objects the statement may read: the query's entities and the parents whose scope they follow
        through an ``inherits_scope`` link (the scope's semi-join reads them, US-155)."""
        out: dict[str, dict[str, Any]] = {}
        entities = set(compiled.entities.values())
        for e in list(entities):
            seen = scope.entities.get(e) if scope is not None else None
            while seen is not None and seen.via is not None and seen.via.parent not in entities:
                entities.add(seen.via.parent)
                seen = scope.entities.get(seen.via.parent) if scope is not None else None
        for alias_entity in entities:
            ent = snapshot.entity(alias_entity)
            assert ent is not None
            # The approval checksum covers every source column the entity's model fields use.
            used = sorted({f.source_column for f in ent.fields})
            out[ent.source_object.lower()] = {"columns": used, "checksum": ent.source_checksum}
        return out

    @staticmethod
    def _column_tags(snapshot: ModelSnapshot, columns: list[ColumnMeta]) -> dict[str, dict[str, str]]:
        """Model field tags for the runner's masking (RULE-020): tags override name heuristics."""
        tags: dict[str, dict[str, str]] = {}
        for c in columns:
            if c.kind in ("measure", "period"):
                tags[c.name] = {"sensitivity": "public"}
                continue
            if not c.field or (c.kind == "dimension" and c.ref.startswith("personal:")):
                continue
            ent_key, field_key = c.field.split(".", 1)
            ent = snapshot.entity(ent_key)
            f = ent.field(field_key) if ent else None
            if f is not None:
                tags[c.name] = {k: v for k, v in {"semantic_type": f.semantic_type,
                                                  "sensitivity": f.sensitivity}.items() if v}
        return tags

    @staticmethod
    def _mask_policy_fields(scope: ResolvedScope, columns: list[ColumnMeta], rows: list[list[Any]]) -> list[list[Any]]:
        """Field policies with effect ``mask`` show that a value exists but not the value."""
        if not scope.masked_fields:
            return rows
        idx = [i for i, c in enumerate(columns)
               if c.kind == "dimension" and c.field in scope.masked_fields and not c.ref.startswith("personal:")]
        for row in rows:
            for i in idx:
                if row[i] is not None:
                    row[i] = "••••"
        return rows

    @staticmethod
    def _column_out(c: ColumnMeta) -> dict[str, Any]:
        out = {"name": c.name, "kind": c.kind, "label": c.label, "ref": c.ref, "data_type": c.data_type,
               "format": c.format}
        return {**out, "of": c.of} if c.of else out

    @staticmethod
    def _result_base(spec: QuerySpec, snapshot: ModelSnapshot, scope: ResolvedScope,
                     measures: list[Any], *, certified: bool = True, extras: Sequence[Extra] = ()) -> dict[str, Any]:
        return {
            "model_version": {"id": snapshot.model_version_id, "version_no": snapshot.version_no},
            "connection_id": spec.connection_id,
            "scope": {"summary": scope.summary, "locked": True},
            # A free-hand answer (US-163) is never shown as certified: "Not certified · built from your question".
            "badge": "certified" if certified else "not_certified",
            "certified": certified,
            "definitions": [{"ref": f"measure:{m.key}", "label": m.label, "plain_rule": m.plain_rule,
                             "blank_handling": m.blank_handling, "rule_ref": m.rule_ref, "format": m.format}
                            for m in measures] + [
                {"ref": e.ref, "label": e.label, "plain_rule": e.plain_rule, "blank_handling": None, "rule_ref": None,
                 "format": e.format} for e in extras],
        }

    def _field_ref(self, snapshot: ModelSnapshot, ref: str) -> FieldRef:
        fref, _ = SpecValidator(snapshot)._field(ref, {})
        return fref

    async def _distinct(self, snapshot: ModelSnapshot, scope: ResolvedScope, connection: dict[str, Any],
                        fref: FieldRef, principal: Principal, ctx: QueryContext, limit: int, *,
                        surface: str = "filter_values") -> list[Any]:
        compiled = field_query(connection, snapshot, fref, "distinct")
        plan = _Plan(connection, snapshot, scope, compiled, limit, reporting_date(snapshot.settings, None),
                     snapshot.model_version_id)
        sub = QueryContext(surface=surface, request_id=ctx.request_id, use_cache=ctx.use_cache)
        result = await self._run(plan, principal, sub)
        return [r[0] for r in result["rows"]]

    async def _enum_values(self, spec: QuerySpec, snapshot: ModelSnapshot, scope: ResolvedScope,
                           connection: dict[str, Any], principal: Principal,
                           ctx: QueryContext) -> dict[str, list[str] | None]:
        """Scoped distinct values for text dimensions used in equality filters (S30 enum-fabrication lesson).

        Only enum-like dimensions are checked: if a dimension has more distinct values than
        ``enum_check_max_values`` it is treated as free text and not checked.
        """
        out: dict[str, list[str] | None] = {}
        for flt in spec.filters:
            if not flt.ref.startswith("dimension:") or flt.op not in ("=", "!=", "in", "not_in"):
                continue
            try:
                fref = self._field_ref(snapshot, flt.ref)
            except TelaError:
                continue  # the validator reports unknown references
            if (fref.field.data_type != "text" or fref.computed or fref.qualified in scope.hidden_fields
                    or fref.qualified in out):
                continue
            cap = self.settings.enum_check_max_values
            values = await self._distinct(snapshot, scope, connection, fref, principal, ctx, cap + 1)
            out[fref.qualified] = None if len(values) > cap else [str(v) for v in values]
        return out

    async def _source_column_as_of(self, plan: _Plan, principal: Principal, ctx: QueryContext) -> datetime | None:
        policy = plan.connection["freshness_policy"]
        try:
            fref = self._field_ref(plan.snapshot, f"field:{policy['entity']}.{policy['field']}")
        except TelaError:
            return None
        compiled = field_query(plan.connection, plan.snapshot, fref, "max")
        sub = _Plan(plan.connection, plan.snapshot, plan.scope, compiled, None, plan.as_of_date, plan.model_version_id)
        result = await self._run(sub, principal, QueryContext(surface=ctx.surface, request_id=ctx.request_id,
                                                              use_cache=False), nested=True)
        value = result["rows"][0][0] if result["rows"] else None
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def column_checksum_for(entity_columns: list[dict[str, Any]], used: set[str]) -> str:
    return str(columns_checksum((c["name"], c["type"]) for c in entity_columns if c["name"] in used))


async def load_connection_columns(conn: Any, connection_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = await fetch_all(
        conn, "SELECT qualified_name, columns FROM conn.connection_objects WHERE connection_id = CAST(:c AS uuid) "
              "AND removed_at IS NULL", {"c": connection_id})
    return {r["qualified_name"].lower(): r["columns"] for r in rows}
