"""Where a pack's manifest and knowledge files come from (ENH-025, US-230; FEAT-075).

A pack is a manifest plus knowledge documents: glossary, value synonyms, dashboard rules, chart patterns, analysis
recipes, report blueprints and gold examples. They live either in a folder (``packs/<key>/<kind>.yaml``, the form
``tela-admin load-packs`` reads and git keeps) or in the database (``model.starter_packs`` and
``model.pack_documents``, the form a tenant authors in the app). ``PackSource`` reads either the same way, so
install, pack updates, knowledge indexing and the wording check do not care where a pack is kept.

A database source is read in one round trip (``db_source``) and then answers synchronously, like a folder. A system
pack whose documents were never stored (loaded before migration 0048, until ``load-packs`` runs again) reads its
folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one

log = logging.getLogger(__name__)

KINDS = ("glossary", "value_synonyms", "dashboard_rules", "chart_patterns", "analysis_recipes",
         "report_blueprints", "gold_examples")
DEFAULT_PACKS_DIR = Path(__file__).resolve().parents[5] / "packs"


def kind_of(file_name: str) -> str:
    """``report_blueprints.yaml`` → ``report_blueprints``; an unknown name is refused."""
    kind = file_name.removesuffix(".yaml")
    if kind not in KINDS:
        raise ValueError(f"unknown pack document {file_name!r}")
    return kind


def document_sha(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


@runtime_checkable
class PackSource(Protocol):
    @property
    def pack_key(self) -> str: ...

    def head(self) -> dict[str, Any]:
        """The manifest as written (``reports_from`` not yet resolved)."""
        ...

    def document(self, kind: str) -> dict[str, Any] | None:
        """One knowledge document's body, or None when the pack has none of that kind."""
        ...


@dataclass
class FilePackSource:
    folder: Path
    _head: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def pack_key(self) -> str:
        return str(self.head().get("pack_key") or "") if self.exists() else ""

    def exists(self) -> bool:
        return (self.folder / "manifest.yaml").exists()

    def head(self) -> dict[str, Any]:
        if self._head is None:
            self._head = yaml.safe_load((self.folder / "manifest.yaml").read_text()) or {}
        return dict(self._head)

    def document(self, kind: str) -> dict[str, Any] | None:
        path = self.folder / f"{kind_of(kind)}.yaml"
        return (yaml.safe_load(path.read_text()) or {}) if path.exists() else None

    def documents(self) -> dict[str, dict[str, Any]]:
        return {k: d for k in KINDS if (d := self.document(k)) is not None}


@dataclass
class DbPackSource:
    pack_id: str
    pack_key: str
    pack_version: str
    tenant_id: str | None
    status: str
    manifest_raw: dict[str, Any]
    docs: dict[str, dict[str, Any]]

    def head(self) -> dict[str, Any]:
        return dict(self.manifest_raw)

    def document(self, kind: str) -> dict[str, Any] | None:
        body = self.docs.get(kind_of(kind))
        return dict(body) if body is not None else None


def discover(packs_dir: Path | str | None = None) -> list[Path]:
    root = Path(packs_dir) if packs_dir else DEFAULT_PACKS_DIR
    return sorted(root.glob("*/manifest.yaml"))


def file_source(pack_key: str, packs_dir: str | Path | None = None) -> FilePackSource | None:
    """The folder of the pack with this key, if any."""
    for path in discover(packs_dir):
        src = FilePackSource(path.parent)
        if src.pack_key == pack_key:
            return src
    return None


def resolve(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> PackSource | None:
    """A source from a pack key (its folder) or a source as given."""
    if pack is None or not isinstance(pack, str):
        return pack
    return file_source(pack, packs_dir) if pack else None


_PACK_COLS = "p.id, p.pack_key, p.pack_version, p.tenant_id, p.status, p.manifest"


async def _with_documents(conn: AsyncConnection, row: dict[str, Any] | None,
                          packs_dir: str | Path | None) -> PackSource | None:
    if row is None:
        return None
    docs = await fetch_all(conn, "SELECT kind, body FROM model.pack_documents WHERE pack_id = :p", {"p": row["id"]})
    if not docs and row["tenant_id"] is None:
        folder = file_source(str(row["pack_key"]), packs_dir)
        if folder is not None:
            log.warning("pack %s %s has no stored documents; reading its folder (run tela-admin load-packs)",
                        row["pack_key"], row["pack_version"])
            return folder
    return DbPackSource(str(row["id"]), str(row["pack_key"]), str(row["pack_version"]),
                        str(row["tenant_id"]) if row["tenant_id"] else None, str(row["status"]),
                        dict(row["manifest"]), {str(d["kind"]): d["body"] for d in docs})


async def db_source(conn: AsyncConnection, pack_id: str, *,
                    packs_dir: str | Path | None = None) -> PackSource | None:
    """The stored pack with this id (as the tenant's row-level security lets it be seen)."""
    row = await fetch_one(conn, f"SELECT {_PACK_COLS} FROM model.starter_packs p "  # noqa: S608 (constant columns)
                                "WHERE p.id = CAST(:id AS uuid)", {"id": pack_id})
    return await _with_documents(conn, row, packs_dir)


async def installed_source(conn: AsyncConnection, data_model_id: str, *,
                           packs_dir: str | Path | None = None) -> PackSource | None:
    """The pack version a data model was built from, as stored."""
    row = await fetch_one(conn, f"SELECT {_PACK_COLS} FROM model.data_models m JOIN model.starter_packs p ON "  # noqa: S608
                                "p.id = m.installed_pack_id WHERE m.id = CAST(:dm AS uuid)", {"dm": data_model_id})
    return await _with_documents(conn, row, packs_dir)


async def released_sources(conn: AsyncConnection, *, system_only: bool = False,
                           packs_dir: str | Path | None = None) -> list[PackSource]:
    """The latest released version of every pack the tenant can see (or of the system packs only)."""
    where = " AND p.tenant_id IS NULL" if system_only else ""
    rows = await fetch_all(conn, f"SELECT {_PACK_COLS} FROM model.starter_packs p "  # noqa: S608 (constant columns)
                                 f"WHERE p.status = 'released'{where}")
    latest: dict[tuple[str | None, str], dict[str, Any]] = {}
    for r in rows:
        k = (str(r["tenant_id"]) if r["tenant_id"] else None, str(r["pack_key"]))
        if k not in latest or _vkey(r["pack_version"]) > _vkey(latest[k]["pack_version"]):
            latest[k] = r
    out = []
    for _, r in sorted(latest.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
        src = await _with_documents(conn, r, packs_dir)
        if src is not None:
            out.append(src)
    return out


async def system_source(conn: AsyncConnection, pack_key: str, *,
                        packs_dir: str | Path | None = None) -> PackSource | None:
    """The latest released version of a system pack, as stored; its folder when it was never loaded."""
    for src in await released_sources(conn, system_only=True, packs_dir=packs_dir):
        if src.pack_key == pack_key:
            return src
    return file_source(pack_key, packs_dir)


def _vkey(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in str(version).split("."))
