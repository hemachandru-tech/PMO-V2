"""Approved models written to a folder as JSON files, so people can read, diff and keep them outside the app.

Turned on by ``TELA_MODEL_EXPORT_DIR`` (in dev the folder ``services/api/model_exports`` is mounted there). On every
approval, and on demand (``POST /models/{id}/export``), the approved version is written as::

    <dir>/<connection-name>/v<N>.json      the model definition, exactly as stored (entities, links, measures,
                                           dimensions, rules, settings)
    <dir>/<connection-name>/latest.json    the same, for the newest approved version
    <dir>/<connection-name>/manifest.json  connection, model id and every exported version with its approval time

Writing is best-effort: a full disk or a missing folder never blocks an approval; the reason is logged.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_one
from tela_api.core.settings import get_settings

log = structlog.get_logger()


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "connection"


def export_dir() -> Path | None:
    d = getattr(get_settings(), "model_export_dir", None)
    return Path(d) if d else None


async def export_model(conn: AsyncConnection, model_id: str, version_id: str | None = None) -> dict[str, Any] | None:
    """Write the model's approved version (or ``version_id``) to the export folder; returns what was written, or None
    when exporting is off or the version is not found."""
    base = export_dir()
    if base is None:
        return None
    row = await fetch_one(
        conn,
        "SELECT m.id AS model_id, c.name AS connection, c.id AS connection_id, c.engine, v.id AS version_id, "
        "v.version_no, v.status, v.approved_at, v.definition FROM model.data_models m "
        "JOIN conn.connections c ON c.id = m.connection_id "
        "JOIN model.model_versions v ON v.id = COALESCE(CAST(:v AS uuid), m.current_version_id) "
        "WHERE m.id = CAST(:m AS uuid)", {"m": model_id, "v": version_id})
    if row is None or row["version_no"] is None:
        return None
    folder = base / _slug(row["connection"])
    try:
        folder.mkdir(parents=True, exist_ok=True)
        body = json.dumps(row["definition"], indent=2, sort_keys=False, default=str)
        (folder / f"v{row['version_no']}.json").write_text(body + "\n", encoding="utf-8")
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        versions = {v["version"]: v for v in manifest.get("versions", [])}
        versions[row["version_no"]] = {
            "version": row["version_no"], "version_id": str(row["version_id"]), "status": row["status"],
            "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
            "file": f"v{row['version_no']}.json",
            "entities": len(row["definition"].get("entities", [])),
            "rules": len(row["definition"].get("rules", [])),
            "measures": len(row["definition"].get("measures", [])),
        }
        latest = max(versions)
        manifest = {"connection": row["connection"], "connection_id": str(row["connection_id"]),
                    "engine": row["engine"], "model_id": str(row["model_id"]), "latest_version": latest,
                    "exported_at": datetime.now(UTC).isoformat(),
                    "versions": [versions[k] for k in sorted(versions)]}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if row["version_no"] == latest:
            (folder / "latest.json").write_text(body + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("model.export_failed", model_id=model_id, folder=str(folder), error=str(exc))
        return None
    return {"folder": str(folder), "file": f"v{row['version_no']}.json", "version": row["version_no"],
            "latest": row["version_no"] == latest}
