"""One place that picks the compiler for a connection (US-112, US-111): SQL dialects through ``SqlCompiler``
(Dataverse TDS with its T-SQL profile), MongoDB through ``MongoCompiler``, Dataverse in Web API mode through
``WebApiCompiler``. Every surface (answers, widgets, drill, filter values, freshness, scope preview, approval
dry runs) asks here, so a new engine cannot bypass the shared execution path."""

from __future__ import annotations

from datetime import date
from typing import Any

from tela_api.query import dataverse, mongo
from tela_api.query.compiler import DIALECTS, RecordCompiler, SqlCompiler
from tela_api.query.compiler import compile_field_query as sql_field_query
from tela_api.query.validator import FieldRef, ValidatedSpec
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelSnapshot

DOCUMENT_ENGINES = ("mongodb",)


def _conn(connection: dict[str, Any] | str) -> dict[str, Any]:
    return {"engine": connection, "descriptor": {}} if isinstance(connection, str) else connection


def is_document(engine: str) -> bool:
    return engine in DOCUMENT_ENGINES


def is_webapi(connection: dict[str, Any] | str) -> bool:
    c = _conn(connection)
    return c["engine"] == "dataverse" and (c.get("descriptor") or {}).get("mode") == "webapi"


def dialect_for(engine: str) -> str:
    return "mongodb" if is_document(engine) else DIALECTS.get(engine, "postgres")


def _webapi(connection: dict[str, Any]) -> dataverse.WebApiCompiler:
    fmt = str((connection.get("descriptor") or {}).get("webapi_format") or "odata")
    return dataverse.WebApiCompiler(connection.get("_columns") or {}, {}, fmt)


def compile_spec(connection: dict[str, Any] | str, vs: ValidatedSpec, *, as_of: date) -> Any:
    c = _conn(connection)
    if (vs.exists_in or vs.cohort is not None) and (is_document(c["engine"]) or is_webapi(c)):
        # The document and Web API compilers have no semi-join or cohort step yet: refuse rather than answer
        # without the condition.
        raise TelaError(ErrorCode.SPEC_INVALID, "Questions about linked records (for example projects with an open "
                        "risk) aren't available for this source yet.")
    if vs.spec.compare is not None and vs.time is not None and vs.time[1].grain and vs.time[1].range and (
            is_document(c["engine"]) or is_webapi(c)):
        # US-178's grained compare runs two windows joined in SQL; these compilers have no such step yet: refuse
        # rather than answer the current period alone.
        raise TelaError(ErrorCode.MEASURE_UNAVAILABLE, "Comparing with an earlier period by month, quarter or "
                        "year isn't available for this source yet.",
                        {"admin_detail": {"reason": "grained period compare"}})
    if is_document(c["engine"]):
        return mongo.MongoCompiler().compile(vs, as_of=as_of)
    if is_webapi(c):
        return _webapi(c).compile(vs, as_of=as_of)
    return SqlCompiler(dialect_for(c["engine"]), profile=c["engine"]).compile(vs, as_of=as_of)


def records_compiler(connection: dict[str, Any] | str) -> Any:
    c = _conn(connection)
    if is_document(c["engine"]):
        return mongo.MongoCompiler()
    if is_webapi(c):
        return _webapi(c)
    return RecordCompiler(dialect_for(c["engine"]), profile=c["engine"])


def field_query(connection: dict[str, Any] | str, snapshot: ModelSnapshot, fref: FieldRef, kind: str, *,
                limit: int | None = None) -> Any:
    c = _conn(connection)
    if is_document(c["engine"]):
        return mongo.compile_field_query(snapshot, fref, kind, limit=limit)
    if is_webapi(c):
        return _webapi(c).compile_field_query(snapshot, fref, kind, limit=limit)
    return sql_field_query(snapshot, fref, kind, dialect=dialect_for(c["engine"]), limit=limit)
