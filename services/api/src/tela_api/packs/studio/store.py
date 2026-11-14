"""Reading and writing pack rows for the Pack Studio routes (US-232).

A pack row is ``model.starter_packs`` plus its ``model.pack_documents``. Row-level security (migration 0048) lets a
tenant read the system packs and its own and write its own drafts only; these helpers add the API's answers on top:
a system pack is read-only ("clone to edit"), a released version is frozen, and every draft edit names the revision
it was made on (``If-Match``; the revision is the manifest's SHA-256, so it changes with every edit).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one
from tela_api.identity.principal import Principal
from tela_api.packs.source import KINDS, DbPackSource, document_sha
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

# Top-level manifest keys in the order a pack's manifest.yaml is written (jsonb keeps no order).
MANIFEST_ORDER = ("pack_key", "pack_version", "title", "released_at", "signature", "entities", "links", "measures",
                  "dimensions", "calculators", "reports_from", "reports", "dashboards", "unavailable_reports")
# Sections a draft edit may replace. The key is fixed when a pack is created (it names the pack everywhere).
SECTIONS = ("pack_version", "title", "signature", "entities", "links", "measures", "dimensions", "calculators",
            "reports", "dashboards", "unavailable_reports")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_COLS = ("p.id, p.tenant_id, p.pack_key, p.pack_version, p.title, p.status, p.manifest, p.manifest_sha256, "
         "p.released_at, p.created_at, p.updated_at, p.created_by, p.based_on_pack_id, p.submitted_by, p.submitted_at, "
         "p.submitted_revision, p.review, p.returned_by, p.returned_at, p.returned_reason, p.released_by")


@dataclass
class PackRow:
    id: str
    tenant_id: str | None
    pack_key: str
    pack_version: str
    title: str
    status: str
    manifest: dict[str, Any]
    sha: str
    released_at: Any
    created_at: Any
    updated_at: Any
    created_by: str | None
    based_on_pack_id: str | None
    # The review of a tenant draft (US-238): submitted for review, sent back, released by whom.
    submitted_by: str | None = None
    submitted_at: Any = None
    submitted_revision: str | None = None
    review: dict[str, Any] | None = None
    returned_by: str | None = None
    returned_at: Any = None
    returned_reason: str | None = None
    released_by: str | None = None

    @property
    def system(self) -> bool:
        return self.tenant_id is None

    @property
    def in_review(self) -> bool:
        return self.status == "draft" and self.submitted_at is not None

    @property
    def editable(self) -> bool:
        return not self.system and self.status == "draft" and not self.in_review

    @property
    def etag(self) -> str:
        return f'"{self.sha}"'

    @classmethod
    def of(cls, r: dict[str, Any]) -> PackRow:
        return cls(str(r["id"]), str(r["tenant_id"]) if r["tenant_id"] else None, r["pack_key"], r["pack_version"],
                   r["title"], r["status"], dict(r["manifest"]), r["manifest_sha256"], r["released_at"],
                   r["created_at"], r["updated_at"], str(r["created_by"]) if r["created_by"] else None,
                   str(r["based_on_pack_id"]) if r["based_on_pack_id"] else None,
                   _str(r.get("submitted_by")), r.get("submitted_at"), r.get("submitted_revision"),
                   dict(r["review"]) if r.get("review") else None, _str(r.get("returned_by")), r.get("returned_at"),
                   r.get("returned_reason"), _str(r.get("released_by")))


def _str(value: Any) -> str | None:
    return str(value) if value else None


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def ordered(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest with its top-level keys in file order (unknown keys last)."""
    head = {k: manifest[k] for k in MANIFEST_ORDER if k in manifest}
    return {**head, **{k: v for k, v in manifest.items() if k not in head}}


async def load(conn: AsyncConnection, pack_id: str, *, lock: bool = False) -> PackRow:
    """The pack the tenant can see with this id (404 otherwise, including another tenant's); ``lock`` holds the row
    until the transaction ends, so two edits on one revision can't both pass the revision check."""
    sql = f"SELECT {_COLS} FROM model.starter_packs p WHERE p.id = CAST(:id AS uuid)"  # noqa: S608 (constant columns)
    if not _UUID.match(pack_id):
        raise TelaError(ErrorCode.NOT_FOUND, "Pack not found.")
    row = await fetch_one(conn, sql + (" FOR UPDATE" if lock else ""), {"id": pack_id})
    if row is None and lock:  # row-level security hides rows the tenant can't change from FOR UPDATE: say why
        row = await fetch_one(conn, sql, {"id": pack_id})
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Pack not found.")
    return PackRow.of(row)


async def all_packs(conn: AsyncConnection) -> list[PackRow]:
    rows = await fetch_all(conn, f"SELECT {_COLS} FROM model.starter_packs p "  # noqa: S608 (constant columns)
                                 "ORDER BY p.tenant_id NULLS FIRST, p.pack_key, p.created_at")
    return [PackRow.of(r) for r in rows]


async def documents(conn: AsyncConnection, pack_id: str) -> dict[str, Any]:
    rows = await fetch_all(conn, "SELECT kind, body FROM model.pack_documents WHERE pack_id = CAST(:p AS uuid)",
                           {"p": pack_id})
    by_kind = {str(r["kind"]): r["body"] for r in rows}
    return {k: by_kind[k] for k in KINDS if k in by_kind}


async def installs(conn: AsyncConnection, pack_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Where each pack version is installed: the connections whose model was built from it."""
    if not pack_ids:
        return {}
    rows = await fetch_all(conn, "SELECT m.installed_pack_id AS pack_id, c.id AS connection_id, c.name FROM "
                                 "model.data_models m JOIN conn.connections c ON c.id = m.connection_id WHERE "
                                 "c.deleted_at IS NULL AND m.installed_pack_id = ANY(CAST(:ids AS uuid[])) "
                                 "ORDER BY c.name", {"ids": pack_ids})
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["pack_id"]), []).append({"connection_id": str(r["connection_id"]), "name": r["name"]})
    return out


def source(row: PackRow, docs: dict[str, Any]) -> DbPackSource:
    return DbPackSource(row.id, row.pack_key, row.pack_version, row.tenant_id, row.status, row.manifest, docs)


def require_editable(row: PackRow) -> None:
    if row.system:
        raise TelaError(ErrorCode.CONFLICT, "System packs are read-only. Clone the pack to edit it.")
    if row.status != "draft":
        raise TelaError(ErrorCode.CONFLICT, f"Version {row.pack_version} is {row.status} and can't change. "
                                            "Edit a new draft version instead.")
    if row.in_review:
        raise TelaError(ErrorCode.CONFLICT, f"Version {row.pack_version} is waiting for review and can't change. "
                                            "Withdraw it from review to edit it.", {"reason": "in_review"})


def require_revision(row: PackRow, if_match: str | None) -> None:
    if not if_match:
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Send the pack's revision in If-Match.")
    if if_match.strip() not in (row.etag, row.sha, f'W/{row.etag}'):
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Someone changed this pack since you opened it. Reload to see "
                                                       "their changes, then make yours again.",
                        {"current_revision": row.sha})


async def key_taken(conn: AsyncConnection, pack_key: str) -> bool:
    """Whether a pack the tenant can see already has this key (system packs and its own; the database also refuses a
    system pack's key it can't see)."""
    return await fetch_one(conn, "SELECT 1 FROM model.starter_packs WHERE pack_key = :k LIMIT 1",
                           {"k": pack_key}) is not None


async def insert(conn: AsyncConnection, principal: Principal, head: dict[str, Any], docs: dict[str, Any], *,
                 based_on: str | None = None) -> str:
    """Store a new tenant draft with its documents; returns its id."""
    pack_id = str(new_id())
    head = ordered(head)
    await _write(conn, head["pack_key"], head["pack_version"], text(
        "INSERT INTO model.starter_packs (id, tenant_id, pack_key, pack_version, title, detection_signature, manifest, "
        "manifest_sha256, released_at, status, created_by, based_on_pack_id) VALUES (CAST(:id AS uuid), "
        "CAST(:t AS uuid), :k, :v, :title, CAST(:sig AS jsonb), CAST(:m AS jsonb), :sha, NULL, 'draft', "
        "CAST(:by AS uuid), CAST(:based AS uuid))"),
        {"id": pack_id, "t": principal.tenant_id, "k": head["pack_key"], "v": head["pack_version"],
         "title": head["title"], "sig": json.dumps(head.get("signature") or {}), "m": json.dumps(head, default=str),
         "sha": document_sha(head), "by": principal.user_id, "based": based_on})
    for kind, body in docs.items():
        await conn.execute(text(
            "INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES (CAST(:id AS uuid), "
            "CAST(:p AS uuid), CAST(:t AS uuid), :kind, CAST(:b AS json), :sha)"),
            {"id": new_id(), "p": pack_id, "t": principal.tenant_id, "kind": kind,
             "b": json.dumps(body, default=str), "sha": document_sha(body)})
    return pack_id


async def save_manifest(conn: AsyncConnection, row: PackRow, head: dict[str, Any]) -> str:
    """Replace a draft's manifest; returns the new revision."""
    head = ordered(head)
    sha = document_sha(head)
    await _write(conn, head["pack_key"], head["pack_version"], text(
        "UPDATE model.starter_packs SET manifest = CAST(:m AS jsonb), manifest_sha256 = :sha, title = :title, "
        "pack_version = :v, detection_signature = CAST(:sig AS jsonb) WHERE id = CAST(:id AS uuid)"),
        {"m": json.dumps(head, default=str), "sha": sha, "title": head["title"], "v": head["pack_version"],
         "sig": json.dumps(head.get("signature") or {}), "id": row.id})
    return sha


async def _write(conn: AsyncConnection, key: str, version: str, stmt: Any, params: dict[str, Any]) -> None:
    """Run a pack write; a key or version another pack holds is a 409 (the database checks keys the tenant can't
    see too: a system pack's key, or the same key and version)."""
    try:
        async with conn.begin_nested():
            await conn.execute(stmt, params)
    except IntegrityError as exc:
        raise TelaError(ErrorCode.CONFLICT, f"A pack {key} {version} already exists. Pick another key or version.",
                        {"field": "pack_key"}) from exc


def summary(row: PackRow, where: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    m = row.manifest
    return {"id": row.id, "pack_key": row.pack_key, "version": row.pack_version, "title": row.title,
            "status": row.status, "owner": "system" if row.system else "tenant", "editable": row.editable,
            "released_at": row.released_at.isoformat() if hasattr(row.released_at, "isoformat") else row.released_at,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "based_on_pack_id": row.based_on_pack_id, "revision": row.sha, "review": review_state(row),
            "counts": {k: len(m.get(k) or []) for k in ("entities", "links", "measures", "dimensions")},
            "installed_on": where or []}


def review_state(row: PackRow) -> dict[str, Any] | None:
    """Where a tenant version is in its review (US-238): ``in_review`` (submitted, by whom, when), ``returned`` (sent
    back with the reason) or ``released`` (by whom); None for a system pack or a draft never submitted."""
    if row.system:
        return None
    if row.status != "draft":
        return {"state": row.status, "released_by": row.released_by, "submitted_by": row.submitted_by,
                "created_by": row.created_by}
    if row.in_review:
        return {"state": "in_review", "submitted_by": row.submitted_by, "submitted_at": _iso(row.submitted_at),
                "created_by": row.created_by, "note": (row.review or {}).get("note")}
    if row.returned_at is not None:
        return {"state": "returned", "returned_by": row.returned_by, "returned_at": _iso(row.returned_at),
                "reason": row.returned_reason, "created_by": row.created_by}
    return {"state": "draft", "created_by": row.created_by}


async def delete(conn: AsyncConnection, row: PackRow) -> None:
    """Delete a draft and its documents (released versions are never deleted)."""
    await conn.execute(text("DELETE FROM model.starter_packs WHERE id = CAST(:id AS uuid) AND status = 'draft'"),
                       {"id": row.id})


def as_written(manifest: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any]:
    """The manifest as its folder writes it. ``tela-admin load-packs`` stores the starter reports already expanded
    from the report blueprints (``reports_from``); this puts ``reports_from: report_blueprints.yaml`` back and keeps
    only the manifest's own reports, so a clone or an export takes its reports from the blueprints document."""
    if "reports_from" in manifest or "report_blueprints" not in docs:
        return dict(manifest)
    from tela_api.packs.manifest import blueprint_reports

    try:
        reports, unavailable = blueprint_reports(docs["report_blueprints"], manifest)
    except (ValueError, KeyError, TypeError):
        return dict(manifest)
    own = {k: v for k, v in (manifest.get("reports") or {}).items() if reports.get(k) != v}
    own_gaps = {k: v for k, v in (manifest.get("unavailable_reports") or {}).items() if unavailable.get(k) != v}
    if (not reports and not unavailable) or len(own) == len(manifest.get("reports") or {}):
        return dict(manifest)  # the blueprints are not where these reports came from
    out = {k: v for k, v in manifest.items() if k not in ("reports", "unavailable_reports")}
    out["reports_from"] = "report_blueprints.yaml"
    if own:
        out["reports"] = own
    if own_gaps:
        out["unavailable_reports"] = own_gaps
    return ordered(out)
