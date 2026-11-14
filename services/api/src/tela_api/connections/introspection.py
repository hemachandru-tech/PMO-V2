"""Schema introspection (US-030; FEAT-006; REQ-015, REQ-016; API-010).

The runner reads tables, views, columns, types and keys. Nothing else is
stored: no sample values are requested, persisted or logged. The catalogue is
diffed against the previous one; objects that disappeared are marked removed
(never deleted, so approved versions keep their history). If a removed or
retyped column is used by the current approved model, the model becomes
``broken`` and the Admin is notified (A3).

Alternative flows: more than 2,000 objects → the Admin must pick schemas (A1);
a timeout or partial read → ``incomplete`` with retry (A2).

Deviation (recorded in task.md): introspection runs inline in the request,
bounded by the runner timeout, instead of on the ``introspect`` worker queue.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy import text

from tela_api.audit import service as audit
from tela_api.connections.router import _invalidate_cache, _load, _rid, _runner, _runner_payload
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.schema_checksum import columns_checksum

MAX_OBJECTS = 2000


async def run_introspection(request: Request, principal: Principal, connection_id: str) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _load(conn, connection_id)
        if row["status"] not in ("connected", "incomplete", "introspecting"):
            raise TelaError(ErrorCode.CONNECTION_NOT_READY,
                            "Test the connection successfully before reading its schema.")
        await conn.execute(text("UPDATE conn.connections SET status = 'introspecting' WHERE id = :id"),
                           {"id": row["id"]})
    try:
        catalog = await _runner(request).post("introspect", {**_runner_payload(principal, row),
                                                             "max_objects": MAX_OBJECTS}, timeout_s=90)
    except TelaError as exc:
        # Timeouts and outages leave the catalogue incomplete (retry, A2); a failed read-only check is an error.
        status = "error" if exc.code == ErrorCode.READ_ONLY_VIOLATION else "incomplete"
        async with tenant_tx(principal.tenant_id) as conn:
            await conn.execute(text("UPDATE conn.connections SET status = :s WHERE id = :id"),
                               {"s": status, "id": row["id"]})
            await audit.record(conn, tenant_id=principal.tenant_id, action="connection.introspected",
                               object_type="connection", object_id=connection_id, outcome="failure",
                               actor_id=principal.user_id, correlation_id=_rid(request), details={"code": exc.code})
        return {"status": "incomplete", "error": {"code": exc.code, "message": exc.message}, "retry": True}

    if not catalog["complete"]:
        async with tenant_tx(principal.tenant_id) as conn:
            await conn.execute(text("UPDATE conn.connections SET status = 'incomplete' WHERE id = :id"),
                               {"id": row["id"]})
        return {"status": "incomplete", "too_many_objects": True, "schemas": catalog["schemas"],
                "message": f"This database has more than {MAX_OBJECTS:,} objects. Choose the schemas to include."}

    objects = catalog["objects"]
    async with tenant_tx(principal.tenant_id) as conn:
        before = {r["qualified_name"]: r for r in await fetch_all(
            conn, "SELECT qualified_name, object_checksum, columns FROM conn.connection_objects "
                  "WHERE connection_id = :id AND removed_at IS NULL", {"id": row["id"]})}
        seen = set()
        for o in objects:
            seen.add(o["qualified_name"])
            await conn.execute(
                text(
                    """
                    INSERT INTO conn.connection_objects (id, tenant_id, connection_id, object_kind, qualified_name,
                      columns, keys, row_estimate, object_checksum, readable, introspected_at, removed_at, created_by)
                    VALUES (:id, :t, :c, :k, :q, CAST(:cols AS jsonb), CAST(:keys AS jsonb), :re, :cs, :rd, now(), NULL,
                      :by)
                    ON CONFLICT (connection_id, qualified_name) DO UPDATE SET object_kind = EXCLUDED.object_kind,
                      columns = EXCLUDED.columns, keys = EXCLUDED.keys, row_estimate = EXCLUDED.row_estimate,
                      object_checksum = EXCLUDED.object_checksum, readable = EXCLUDED.readable, introspected_at = now(),
                      removed_at = NULL
                    """
                ),
                {"id": new_id(), "t": principal.tenant_id, "c": row["id"], "k": o["kind"], "q": o["qualified_name"],
                 "cols": json.dumps(o["columns"]),
                 "keys": json.dumps({"primary": o["primary_key"], "foreign": o["foreign_keys"]}),
                 "re": o["row_estimate"], "cs": o["checksum"], "rd": o.get("readable"), "by": principal.user_id},
            )
        removed = sorted(set(before) - seen)
        if removed:
            await conn.execute(
                text("UPDATE conn.connection_objects SET removed_at = now() WHERE connection_id = :id "
                     "AND qualified_name = ANY(:names)"), {"id": row["id"], "names": removed})
        changed = sorted(q for q in seen & set(before)
                         if before[q]["object_checksum"] != next(o["checksum"] for o in objects
                                                                  if o["qualified_name"] == q))
        added = sorted(seen - set(before))
        broken = await _check_model(conn, str(row["id"]), {o["qualified_name"].lower(): o for o in objects})
        await conn.execute(
            text("UPDATE conn.connections SET status = 'connected', schema_checksum = :cs WHERE id = :id"),
            {"cs": catalog["checksum"], "id": row["id"]},
        )
        if (catalog.get("profile") or {}).get("sheets") is not None:  # file sources: sheets for confirmation
            await conn.execute(
                text("UPDATE conn.file_sources SET sheet_manifest = CAST(:m AS jsonb) WHERE connection_id = :id "
                     "AND status = 'active'"), {"m": json.dumps(catalog["profile"]["sheets"]), "id": row["id"]})
        if broken:
            await conn.execute(
                text("INSERT INTO ops.notifications (id, tenant_id, user_id, kind, title, body, link) "
                     "VALUES (:id, :t, :u, 'model_broken', :title, :body, :link)"),
                {"id": new_id(), "t": principal.tenant_id, "u": principal.user_id,
                 "title": "Data model needs attention",
                 "body": f"{len(broken)} certified definitions use columns that changed or disappeared.",
                 "link": f"/admin/connections/{connection_id}/model"},
            )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="connection.introspected", object_type="connection",
            object_id=connection_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
            details={"objects": len(objects), "added": len(added), "removed": len(removed), "changed": len(changed),
                     "model_broken": bool(broken)},
        )
        await _invalidate_cache(request, conn, connection_id)
    return {
        "status": "connected", "objects": len(objects), "schema_checksum": catalog["checksum"],
        "diff": {"added": added, "removed": removed, "changed": changed},
        "model_broken": broken, "samples_stored": False,
    }


async def _check_model(conn: Any, connection_id: str, catalog: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Mark the current model broken when a column it uses was removed or retyped (A3), or when the account can no
    longer read an object it uses."""
    model = await fetch_one(
        conn,
        "SELECT m.id, v.definition FROM model.data_models m JOIN model.model_versions v ON v.id = m.current_version_id "
        "WHERE m.connection_id = :c", {"c": connection_id},
    )
    if model is None:
        return []
    problems: list[dict[str, str]] = []
    for ent in model["definition"]["entities"]:
        obj = catalog.get(ent["source_object"].lower())
        if obj is None:
            problems.append({"entity": ent["key"], "problem": "source object removed"})
            continue
        if obj.get("readable") is False:
            problems.append({"entity": ent["key"], "problem": "the source account can no longer read this table"})
            continue
        used = {f["source_column"] for f in ent["fields"]}
        live = [(c["name"], c["type"]) for c in obj["columns"] if c["name"] in used]
        if len(live) != len(used) or (ent.get("source_checksum") and columns_checksum(live) != ent["source_checksum"]):
            problems.append({"entity": ent["key"], "problem": "columns removed or changed"})
    await conn.execute(
        text("UPDATE model.data_models SET status = :s, broken_reasons = CAST(:r AS jsonb) WHERE id = :id"),
        {"s": "broken" if problems else "ok", "r": json.dumps(problems), "id": model["id"]},
    )
    return problems
