"""MongoDB pipeline guard (US-112; ADR-014; technical_document §Mongo compilation): the runner's own check of
every signed statement before it reaches a MongoDB source, the counterpart of the SQL statement guard.

* the statement is ``{"collection", "pipeline", "columns", "empty_row"}`` in Extended JSON;
* the collection, and every ``$lookup`` ``from``, is a governed collection of the same database;
* the pipeline, and every nested pipeline (``$lookup``, ``$facet``), starts with ``$match`` (the scope);
* every stage is on the allow-list; forbidden operators are refused anywhere, at any depth:
  ``$out $merge $function $accumulator $where $planCacheSetFilter``, plus stages that read other
  collections or server state (``$unionWith $graphLookup $currentOp $listSessions $collStats $indexStats
  $documents $changeStream $listLocalSessions $planCacheStats``);
* a ``$lookup`` must carry a ``pipeline`` (where the scope lives);
* bounded size and depth.
"""

from __future__ import annotations

from typing import Any

from tela_common.errors import ErrorCode, TelaError

ALLOWED_STAGES = frozenset({"$match", "$project", "$addFields", "$set", "$group", "$sort", "$limit", "$skip",
                            "$lookup", "$unwind", "$count", "$facet", "$bucket", "$bucketAuto", "$replaceRoot"})
FORBIDDEN = frozenset({"$out", "$merge", "$function", "$accumulator", "$where", "$planCacheSetFilter",
                       "$unionWith", "$graphLookup", "$currentOp", "$listSessions", "$listLocalSessions",
                       "$collStats", "$indexStats", "$documents", "$changeStream", "$planCacheStats",
                       "$listSearchIndexes", "$search", "$searchMeta", "$vectorSearch"})
MAX_BYTES = 1024 * 1024
MAX_DEPTH = 60


def _reject(reason: str, code: ErrorCode = ErrorCode.SQL_FORBIDDEN_NODE) -> TelaError:
    return TelaError(code, "This statement is not allowed.", {"reason": reason})


def _walk(node: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise _reject("pipeline too deep")
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key in FORBIDDEN:
                raise _reject(f"operator {key}")
            _walk(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk(item, depth + 1)


def _stages(pipeline: Any, database: str, collections: set[str], where: str, depth: int = 0) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise _reject(f"{where}: empty pipeline")
    if depth > 4:
        raise _reject("nested pipelines too deep")
    first = pipeline[0]
    if not (isinstance(first, dict) and list(first) == ["$match"]):
        raise _reject(f"{where}: the scope $match must come first")
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise _reject(f"{where}: malformed stage")
        name, body = next(iter(stage.items()))
        if name not in ALLOWED_STAGES:
            raise _reject(f"stage {name}")
        if name == "$lookup":
            if not isinstance(body, dict) or "pipeline" not in body:
                raise _reject("$lookup without a scoped pipeline")
            target = str(body.get("from") or "")
            if "." in target and not target.startswith(database + "."):
                raise _reject("cross-database $lookup", ErrorCode.SQL_OBJECT_NOT_ALLOWED)
            if f"{database}.{target}".lower() not in collections:
                raise _reject(f"collection {target}", ErrorCode.SQL_OBJECT_NOT_ALLOWED)
            _stages(body["pipeline"], database, collections, f"$lookup {target}", depth + 1)
        elif name == "$facet":
            if not isinstance(body, dict):
                raise _reject("malformed $facet")
            for key, sub in body.items():
                _stages(sub, database, collections, f"$facet {key}", depth + 1)


def check_pipeline(statement: str, allowed_objects: set[str]) -> dict[str, Any]:
    """Parse and check one signed statement; ``allowed_objects`` holds lower-case ``db.collection[#array]``."""
    from bson import json_util

    if len(statement.encode()) > MAX_BYTES:
        raise _reject("statement too large")
    try:
        doc = json_util.loads(statement)
    except Exception as exc:
        raise _reject("parse error", ErrorCode.SQL_PARSE_ERROR) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("collection"), str) or "pipeline" not in doc:
        raise _reject("not a pipeline statement", ErrorCode.SQL_NOT_SELECT)
    collections = {o.split("#", 1)[0] for o in allowed_objects}
    coll = doc["collection"].lower()
    if coll not in collections:
        raise _reject(f"collection {coll}", ErrorCode.SQL_OBJECT_NOT_ALLOWED)
    _walk(doc["pipeline"])
    _stages(doc["pipeline"], coll.split(".", 1)[0], collections, coll)
    columns = doc.get("columns")
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise _reject("columns missing")
    return dict(doc)
