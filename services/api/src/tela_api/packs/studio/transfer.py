"""Export and import a pack as the ``packs/<key>/`` folder layout, zipped (US-232; ENH-025 W3).

The zip holds ``<key>/manifest.yaml`` and one ``<key>/<kind>.yaml`` per knowledge document, so a pack authored in the
app can live in git and ``tela-admin load-packs`` reads the same folder. Import takes the same zip (or the files at
its root) and stores it as a new tenant draft, checked as any draft is.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import yaml
from fastapi import APIRouter, File, Form, Request, Response, UploadFile

from tela_api.audit import service as audit
from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.packs.source import KINDS
from tela_api.packs.studio import store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.validate import validate
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])

MAX_ZIP = 10 * 1024 * 1024  # bytes uploaded
MAX_UNZIPPED = 40 * 1024 * 1024  # bytes after unzipping (the PMO pack is ≈ 1 MB)
MAX_FILES = 50


class _Dumper(yaml.SafeDumper):  # type: ignore[misc]
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def dump(body: Any) -> str:
    return str(yaml.dump(body, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120))


def folder_zip(key: str, manifest: dict[str, Any], docs: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{key}/manifest.yaml", dump(store.as_written(store.ordered(manifest), docs)))
        for kind, body in docs.items():
            z.writestr(f"{key}/{kind}.yaml", dump(body))
    return buf.getvalue()


@router.get("/{pack_id}/export")
async def export_pack(pack_id: str, request: Request, principal: Principal = AUTHOR) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.exported", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request), details={"pack": row.pack_key, "version": row.pack_version})
    name = f"{row.pack_key}-{row.pack_version}.zip"
    return Response(folder_zip(row.pack_key, row.manifest, docs), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def read_zip(data: bytes) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """(manifest, documents, files skipped) from a pack zip: one folder holding ``manifest.yaml``, or the files at
    the zip's root. Files other than the manifest and the known kinds are skipped and named."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "This isn't a zip file. Export a pack, or zip its folder.") from exc
    infos = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")]
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The zip is too large for a pack.")
    manifests = [i.filename for i in infos if i.filename.rsplit("/", 1)[-1] == "manifest.yaml"]
    if len(manifests) != 1:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The zip must hold one pack folder with a manifest.yaml.")
    root = manifests[0][: -len("manifest.yaml")]
    head: dict[str, Any] = {}
    docs: dict[str, Any] = {}
    skipped: list[str] = []
    for info in infos:
        name = info.filename
        rel = name[len(root):] if name.startswith(root) else None
        kind = rel.removesuffix(".yaml") if rel else None
        if rel != "manifest.yaml" and kind not in KINDS:
            skipped.append(name)
            continue
        try:
            body = yaml.safe_load(z.read(info).decode("utf-8")) or {}
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"{rel} isn't valid YAML.",
                            {"errors": [{"path": str(rel), "message": str(exc).splitlines()[0]}], "warnings": []}
                            ) from exc
        if not isinstance(body, dict):
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"{rel} must be a mapping.")
        if rel == "manifest.yaml":
            head = body
        else:
            docs[str(kind)] = body
    return head, docs, sorted(skipped)


@router.post("/import", status_code=201)
async def import_pack(request: Request, response: Response,
                      file: UploadFile = File(...),  # noqa: B008 - FastAPI form parameter
                      pack_key: str | None = Form(default=None),
                      principal: Principal = AUTHOR) -> dict[str, Any]:
    data = await file.read(MAX_ZIP + 1)
    if len(data) > MAX_ZIP:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The zip is too large for a pack (10 MB at most).")
    head, docs, skipped = read_zip(data)
    if pack_key:
        head["pack_key"] = pack_key
    report = validate(head, docs)
    if report.errors:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The pack in this zip doesn't pass its checks.", report.out())
    async with tenant_tx(principal.tenant_id) as conn:
        pack_id = await store.insert(conn, principal, head, docs)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.imported", object_type="pack",
                           object_id=pack_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": head["pack_key"], "version": head["pack_version"],
                                    "documents": sorted(docs), "skipped": skipped})
        row = await store.load(conn, pack_id)
    response.headers["ETag"] = row.etag
    return {**store.summary(row), "skipped": skipped, "checks": report.out()}
