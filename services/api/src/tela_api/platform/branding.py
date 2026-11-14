"""Tenant branding: app name, tagline, logos and favicon (ENH-006, US-149) and the colour palette (US-150).

* ``GET /branding`` — what every signed-in person's app shows; ``GET /branding/public?issuer=`` the same for the
  screens shown before sign-in (an unknown issuer gets the defaults, so it can't be used to find tenants).
* Asset files are served without sign-in (``<img>`` can't send a token, and logos are not secret) at
  ``/branding/assets/<tenant>/<slot>?v=<version>``: cached for good when ``v`` is the current version, with a
  locked-down CSP so an SVG can never run anything even when opened directly.
* Admins with ``branding.manage`` change the names, upload or remove assets, or reset to the Tela defaults. Every
  change raises ``version`` and is audited as ``branding.updated`` with what it was before and after.
* Palettes (US-150): the browser reads the logo's main colours and asks for candidates
  (``POST /admin/branding/palettes``); saving one (``PUT /admin/branding/palette``) sends only its spec, which the
  server rebuilds and re-checks (``palettes.py``) before storing it. No palette = the Tela colours.
* Logo sizes (US-152): how tall the logo is drawn in the sidebar, on the sign-in screens and in the PDF header.
* Chart library (ENH-018, US-206): which library draws the standard charts (``echarts``, ``recharts``, ``plot``).
* Navigation colours (ENH-019, US-209): how the sidebar and the top bar take the brand's colours (``standard``,
  ``tinted``, ``brand``, ``dark``).
* History (US-151): every change stores what the branding looked like after it in ``ops.tenant_branding_history``;
  an Admin can roll back to any kept version (the last ``HISTORY_KEEP``). Replaced logo files stay in the object
  store while a kept version still points at them.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.exports import _rid, _store
from tela_api.core.db import fetch_one, tenant_tx
from tela_api.identity.deps import get_principal, require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.platform.brand_images import inspect_image
from tela_api.platform.palettes import MAX_SEEDS, Spec, Variant, build, candidates, rag_kind
from tela_common.errors import ErrorCode, TelaError
from tela_common.objectstore import tenant_key

router = APIRouter(prefix="/api/v1", tags=["branding"])

DEFAULTS: dict[str, Any] = {"app_name": "Tela", "tagline": "PMO analytics"}
Slot = Literal["logo_light", "logo_dark", "favicon"]
SLOTS: tuple[str, ...] = ("logo_light", "logo_dark", "favicon")
MAX_BYTES = {"logo_light": 1024 * 1024, "logo_dark": 1024 * 1024, "favicon": 256 * 1024}
ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
HISTORY_KEEP = 50
# Logo heights in px (US-152): default and allowed range per place.
LOGO_SIZES: dict[str, tuple[int, int, int]] = {"sidebar": (28, 20, 56), "sign_in": (48, 32, 128),
                                              "export": (28, 16, 64)}
# Chart libraries (US-206): ECharts, shadcn/ui Charts on Recharts, Observable Plot.
ChartLibrary = Literal["echarts", "recharts", "plot"]
DEFAULT_CHART_LIBRARY = "echarts"
# ENH-021: the company's default layout; people may pick their own (``/me/preferences``).
Layout = Literal["classic", "executive", "command", "aurora"]
DEFAULT_LAYOUT = "classic"
# Navigation colours (US-209): neutral surfaces, a wash of the accent, filled with the brand colour, or dark.
NavStyle = Literal["standard", "tinted", "brand", "dark"]
NAV_PLACES: tuple[str, ...] = ("sidebar", "topbar")
DEFAULT_NAV_STYLE = "standard"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _clean(value: str | None) -> str | None:
    return re.sub(r"\s+", " ", value).strip() if value is not None else None


class BrandingIn(BaseModel):
    app_name: str = Field(max_length=200)
    tagline: str | None = Field(default=None, max_length=200)
    expected_version: int | None = None

    @field_validator("app_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v) or ""
        if not 2 <= len(v) <= 40:
            raise ValueError("The app name must be 2 to 40 characters.")
        return v

    @field_validator("tagline")
    @classmethod
    def _tagline(cls, v: str | None) -> str | None:
        v = _clean(v) or None
        if v is not None and len(v) > 60:
            raise ValueError("The tagline can be at most 60 characters.")
        return v


HEX = r"^#[0-9a-fA-F]{6}$"


def _hex_list(v: list[str]) -> list[str]:
    if any(not re.match(HEX, s) for s in v):
        raise ValueError("Colours must be #rrggbb.")
    return [s.lower() for s in v]


class PalettesIn(BaseModel):
    seeds: list[str] = Field(default_factory=list, max_length=MAX_SEEDS)
    accent: str | None = Field(default=None, pattern=HEX)

    _seeds = field_validator("seeds")(_hex_list)


class PaletteSpecIn(BaseModel):
    accent: str = Field(pattern=HEX)
    seeds: list[str] = Field(default_factory=list, max_length=MAX_SEEDS)
    variant: Variant = "natural"
    source: Literal["logo", "custom"] = "logo"

    _seeds = field_validator("seeds")(_hex_list)


class PaletteIn(BaseModel):
    spec: PaletteSpecIn | None = None
    expected_version: int | None = None


class LogoSizeIn(BaseModel):
    sidebar: int = Field(ge=LOGO_SIZES["sidebar"][1], le=LOGO_SIZES["sidebar"][2])
    sign_in: int = Field(ge=LOGO_SIZES["sign_in"][1], le=LOGO_SIZES["sign_in"][2])
    export: int = Field(ge=LOGO_SIZES["export"][1], le=LOGO_SIZES["export"][2])
    expected_version: int | None = None


class ChartLibraryIn(BaseModel):
    library: ChartLibrary
    expected_version: int | None = None


class LayoutIn(BaseModel):
    layout: Layout
    expected_version: int | None = None


class NavColorsIn(BaseModel):
    sidebar: NavStyle
    topbar: NavStyle
    expected_version: int | None = None


class RollbackIn(BaseModel):
    version: int = Field(ge=1)
    expected_version: int | None = None


def logo_sizes(stored: dict[str, Any] | None) -> dict[str, int]:
    """The stored logo sizes over the defaults."""
    return {k: int((stored or {}).get(k, d)) for k, (d, _, _) in LOGO_SIZES.items()}


def nav_colors(stored: dict[str, Any] | None) -> dict[str, str]:
    """The stored navigation colours over the defaults (anything unknown is standard)."""
    styles: tuple[str, ...] = NavStyle.__args__  # type: ignore[attr-defined]
    out = {k: str((stored or {}).get(k)) for k in NAV_PLACES}
    return {k: v if v in styles else DEFAULT_NAV_STYLE for k, v in out.items()}


_SELECT = ("SELECT b.tenant_id, b.app_name, b.tagline, b.assets, b.palette, b.logo_sizes, b.chart_library, "
           "b.nav_colors, b.layout, b.version, b.updated_at, b.updated_by, u.display_name AS updated_by_name "
           "FROM ops.tenant_branding b LEFT JOIN iam.users u ON u.id = b.updated_by")


async def _row(conn: AsyncConnection, lock: bool = False) -> dict[str, Any] | None:
    return await fetch_one(conn, f"{_SELECT} FOR UPDATE OF b" if lock else _SELECT)


def _view(tenant_id: str, row: dict[str, Any] | None, *, admin: bool) -> dict[str, Any]:
    row = row or {}
    version = row.get("version", 0)
    stored = row.get("assets") or {}
    assets = {slot: ({"url": f"/api/v1/branding/assets/{tenant_id}/{slot}?v={version}",
                      "content_type": a["content_type"], "bytes": a["bytes"], "width": a.get("width"),
                      "height": a.get("height")} if (a := stored.get(slot)) else None) for slot in SLOTS}
    out: dict[str, Any] = {"app_name": row.get("app_name", DEFAULTS["app_name"]),
                           "tagline": row.get("tagline", DEFAULTS["tagline"]) if row else DEFAULTS["tagline"],
                           "assets": assets, "logo_sizes": logo_sizes(row.get("logo_sizes")),
                           "chart_library": row.get("chart_library") or DEFAULT_CHART_LIBRARY,
                           "nav_colors": nav_colors(row.get("nav_colors")),
                           "layout": row.get("layout") or DEFAULT_LAYOUT, "version": version,
                           "defaults": {**DEFAULTS, "logo_sizes": logo_sizes(None),
                                        "chart_library": DEFAULT_CHART_LIBRARY, "nav_colors": nav_colors(None),
                                        "layout": DEFAULT_LAYOUT,
                                        "logo_size_limits": {k: [lo, hi] for k, (_, lo, hi) in LOGO_SIZES.items()}}}
    palette = row.get("palette")
    out["palette"] = ({k: v for k, v in palette.items() if admin or k not in ("checks", "notes")}
                      if palette else None)
    out["is_default"] = (out["app_name"], out["tagline"]) == (DEFAULTS["app_name"], DEFAULTS["tagline"]) \
        and not any(assets.values()) and palette is None and not row.get("logo_sizes") \
        and out["chart_library"] == DEFAULT_CHART_LIBRARY and out["nav_colors"] == nav_colors(None) \
        and out["layout"] == DEFAULT_LAYOUT
    if admin:
        out["updated_at"] = row.get("updated_at")
        out["updated_by"] = ({"id": str(row["updated_by"]), "name": row.get("updated_by_name")}
                             if row.get("updated_by") else None)
    return out


def _summary(row: dict[str, Any] | None) -> dict[str, Any]:
    """What the audit keeps about the branding: names and each asset's type, size and hash (no keys)."""
    row = row or {"app_name": DEFAULTS["app_name"], "tagline": DEFAULTS["tagline"], "assets": {}}
    palette = row.get("palette")
    return {"app_name": row["app_name"], "tagline": row["tagline"],
            "assets": {slot: {k: a[k] for k in ("content_type", "bytes", "sha256")}
                       for slot, a in (row.get("assets") or {}).items()},
            "palette": {"name": palette["name"], **palette["spec"]} if palette else None,
            "logo_sizes": logo_sizes(row.get("logo_sizes")),
            "chart_library": row.get("chart_library") or DEFAULT_CHART_LIBRARY,
            "nav_colors": nav_colors(row.get("nav_colors")), "layout": row.get("layout") or DEFAULT_LAYOUT}


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in ("app_name", "tagline", "assets", "palette", "logo_sizes", "chart_library",
                                       "nav_colors", "layout")}


def _keys(snapshot: dict[str, Any] | None) -> set[str]:
    return {a["key"] for a in ((snapshot or {}).get("assets") or {}).values() if a and a.get("key")}


async def _locked(conn: AsyncConnection, principal: Principal) -> dict[str, Any] | None:
    """The tenant's branding row, locked for this change (created with the defaults on first change)."""
    await conn.execute(
        text("INSERT INTO ops.tenant_branding (tenant_id, app_name, tagline, version, updated_by) "
             "VALUES (:t, :n, :g, 0, :u) ON CONFLICT (tenant_id) DO NOTHING"),
        {"t": principal.tenant_id, "n": DEFAULTS["app_name"], "g": DEFAULTS["tagline"], "u": principal.user_id})
    return await _row(conn, lock=True)


async def _save(conn: AsyncConnection, request: Request, principal: Principal, before: dict[str, Any] | None,
                change: str, *, note: dict[str, Any] | None = None,
                **fields: Any) -> tuple[dict[str, Any], set[str]]:
    """Apply a change, keep it in the history and audit it. Returns the new view and the object keys no kept
    version uses any more (delete them once the transaction has committed, with ``_forget_keys``)."""
    jsonb = ("assets", "palette", "logo_sizes", "nav_colors")
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k in jsonb else f"{k} = :{k}" for k in fields)
    for k in jsonb:
        if k in fields:
            fields[k] = None if fields[k] is None else json.dumps(fields[k])
    await conn.execute(text(f"UPDATE ops.tenant_branding SET {sets}{', ' if sets else ''}"  # noqa: S608 - fixed names
                            "version = version + 1, updated_by = :u WHERE tenant_id = :t"),
                       {**fields, "u": principal.user_id, "t": principal.tenant_id})
    after = await _row(conn)
    assert after is not None  # created by _locked
    await conn.execute(
        text("INSERT INTO ops.tenant_branding_history (tenant_id, version, change, snapshot, changed_by) "
             "VALUES (:t, :v, :c, CAST(:s AS jsonb), :u) ON CONFLICT (tenant_id, version) DO NOTHING"),
        {"t": principal.tenant_id, "v": after["version"], "c": change, "s": json.dumps(_snapshot(after)),
         "u": principal.user_id})
    pruned = (await conn.execute(
        text("DELETE FROM ops.tenant_branding_history WHERE tenant_id = :t AND version <= :cut "
             "RETURNING snapshot"),
        {"t": principal.tenant_id, "cut": after["version"] - HISTORY_KEEP})).scalars().all()
    orphans: set[str] = set()
    for snap in pruned:
        orphans |= _keys(snap)
    if orphans:
        kept = (await conn.execute(text("SELECT snapshot FROM ops.tenant_branding_history WHERE tenant_id = :t"),
                                   {"t": principal.tenant_id})).scalars().all()
        for snap in [*kept, _snapshot(after)]:
            orphans -= _keys(snap)
    await audit.record(conn, tenant_id=principal.tenant_id, action="branding.updated", object_type="branding",
                       object_id=principal.tenant_id, outcome="success", actor_id=principal.user_id,
                       correlation_id=_rid(request),
                       details={"change": change, **(note or {}), "before": _summary(before),
                                "after": _summary(after)})
    return _view(principal.tenant_id, after, admin=True), orphans


# --------------------------------------------------------------------------- reading


@router.get("/branding")
async def get_branding(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _row(conn)
    return _view(principal.tenant_id, row, admin=principal.has(Perm.BRANDING_MANAGE))


@router.get("/branding/public")
async def public_branding(request: Request, issuer: str | None = Query(default=None, max_length=500)) -> dict[str, Any]:
    async with tenant_tx(None) as conn:
        tenant = await fetch_one(conn, "SELECT id, status FROM iam.f_tenant_for_issuer(:iss)",
                                 {"iss": issuer or request.app.state.settings.oidc_issuer})
    if tenant is None or tenant["status"] != "active":
        return {**_view("-", None, admin=False), "assets": dict.fromkeys(SLOTS)}
    async with tenant_tx(str(tenant["id"])) as conn:
        row = await _row(conn)
    return _view(str(tenant["id"]), row, admin=False)


@router.get("/branding/assets/{tenant_id}/{slot}", response_class=Response)
async def branding_asset(request: Request, tenant_id: str, slot: Slot,
                         v: int | None = Query(default=None)) -> Response:
    if not _UUID.match(tenant_id):
        raise TelaError(ErrorCode.NOT_FOUND, "That image was not found.")
    async with tenant_tx(tenant_id) as conn:
        row = await _row(conn)
    asset = ((row or {}).get("assets") or {}).get(slot)
    if row is None or asset is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That image was not found.")
    etag = f'"{asset["sha256"][:32]}"'
    current = v == row["version"]
    headers = {"Cache-Control": "public, max-age=31536000, immutable" if current else "public, max-age=60",
               "ETag": etag, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": ASSET_CSP,
               "Cross-Origin-Resource-Policy": "same-site"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    data = await (await _store(request)).get(asset["key"])
    return Response(content=data, media_type=asset["content_type"], headers=headers)


# --------------------------------------------------------------------------- changing


@router.put("/admin/branding")
async def update_branding(body: BrandingIn, request: Request,
                          principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        if body.expected_version is not None and before and body.expected_version != before["version"]:
            raise TelaError(ErrorCode.CONFLICT, "Someone else changed the branding. Reload to see their changes.",
                            {"version": before["version"]})
        out, orphans = await _save(conn, request, principal, before, "names", app_name=body.app_name,
                                   tagline=body.tagline)
    await _forget_keys(request, orphans)
    return out


@router.post("/admin/branding/assets/{slot}")
async def upload_asset(slot: Slot, request: Request, file: UploadFile = File(...),  # noqa: B008 - FastAPI form parameter
                       principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    limit = MAX_BYTES[slot]
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, f"The image is larger than {limit // 1024} KB.")
    image = inspect_image(data)
    digest = hashlib.sha256(data).hexdigest()
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        key = tenant_key(principal.tenant_id, "branding", f"{slot}-{before['version'] + 1}-{digest[:12]}.{image.ext}")
        await (await _store(request)).put(key, data, image.content_type)
        assets = {**(before["assets"] or {}), slot: {
            "key": key, "content_type": image.content_type, "bytes": len(data), "width": image.width,
            "height": image.height, "sha256": digest, "filename": (file.filename or "")[-120:]}}
        out, orphans = await _save(conn, request, principal, before, f"{slot}_uploaded", assets=assets)
    await _forget_keys(request, orphans)
    return out


@router.delete("/admin/branding/assets/{slot}")
async def remove_asset(slot: Slot, request: Request,
                       principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        assets = dict(before["assets"] or {})
        if assets.pop(slot, None) is None:
            return _view(principal.tenant_id, before, admin=True)
        out, orphans = await _save(conn, request, principal, before, f"{slot}_removed", assets=assets)
    await _forget_keys(request, orphans)
    return out


@router.post("/admin/branding/reset")
async def reset_branding(request: Request,
                         principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        out, orphans = await _save(conn, request, principal, before, "reset", app_name=DEFAULTS["app_name"],
                                   tagline=DEFAULTS["tagline"], assets={}, palette=None, logo_sizes={},
                                   chart_library=DEFAULT_CHART_LIBRARY, nav_colors={}, layout=DEFAULT_LAYOUT)
    await _forget_keys(request, orphans)
    return out


def _no_rag_accent(accent: str) -> None:
    if kind := rag_kind(accent):
        raise TelaError(ErrorCode.VALIDATION_ERROR,
                        f"{accent} reads as {kind}, which means {'Off track' if kind == 'red' else 'On track'} in "
                        "badges and charts, so it can't be the accent. Pick another colour.",
                        {"field": "accent", "rag": kind})


@router.post("/admin/branding/palettes")
async def palette_candidates(body: PalettesIn,
                             principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    """Candidate palettes for the logo's colours, or the one palette for an accent the Admin picked."""
    if body.accent:
        _no_rag_accent(body.accent)
        p = build(Spec(accent=body.accent.lower(), seeds=tuple(body.seeds), source="custom"))
        return {"candidates": [p] if p["ok"] else [], "dropped": 0 if p["ok"] else 1, "greys_only": False,
                "failed": None if p["ok"] else p}
    return candidates(body.seeds)


@router.put("/admin/branding/palette")
async def update_palette(body: PaletteIn, request: Request,
                         principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    palette = None
    if body.spec:
        if body.spec.source == "custom":
            _no_rag_accent(body.spec.accent)
        palette = build(Spec(accent=body.spec.accent.lower(), seeds=tuple(body.spec.seeds),
                             variant=body.spec.variant, source=body.spec.source))
        if not palette["ok"]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "That palette fails the contrast or colour-blind checks.",
                            {"checks": [c for c in palette["checks"] if not c["ok"]]})
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        if body.expected_version is not None and body.expected_version != before["version"]:
            raise TelaError(ErrorCode.CONFLICT, "Someone else changed the branding. Reload to see their changes.",
                            {"version": before["version"]})
        out, orphans = await _save(conn, request, principal, before, "palette" if palette else "palette_removed",
                                   palette=palette)
    await _forget_keys(request, orphans)
    return out


def _check_version(before: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != before["version"]:
        raise TelaError(ErrorCode.CONFLICT, "Someone else changed the branding. Reload to see their changes.",
                        {"version": before["version"]})


@router.put("/admin/branding/logo-size")
async def update_logo_size(body: LogoSizeIn, request: Request,
                           principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    sizes = body.model_dump(exclude={"expected_version"})
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        _check_version(before, body.expected_version)
        if sizes == logo_sizes(before["logo_sizes"]):
            return _view(principal.tenant_id, before, admin=True)
        out, orphans = await _save(conn, request, principal, before, "logo_size",
                                   logo_sizes={} if sizes == logo_sizes(None) else sizes)
    await _forget_keys(request, orphans)
    return out


@router.put("/admin/branding/chart-library")
async def update_chart_library(body: ChartLibraryIn, request: Request,
                               principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        _check_version(before, body.expected_version)
        if body.library == before["chart_library"]:
            return _view(principal.tenant_id, before, admin=True)
        out, orphans = await _save(conn, request, principal, before, "chart_library", chart_library=body.library)
    await _forget_keys(request, orphans)
    return out


@router.put("/admin/branding/layout")
async def update_layout(body: LayoutIn, request: Request,
                        principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    """The company's default layout (ENH-021); people who picked their own keep it."""
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        _check_version(before, body.expected_version)
        if body.layout == before["layout"]:
            return _view(principal.tenant_id, before, admin=True)
        out, orphans = await _save(conn, request, principal, before, "layout", layout=body.layout)
    await _forget_keys(request, orphans)
    return out


@router.put("/admin/branding/nav-colors")
async def update_nav_colors(body: NavColorsIn, request: Request,
                            principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    colors = body.model_dump(exclude={"expected_version"})
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        _check_version(before, body.expected_version)
        if colors == nav_colors(before["nav_colors"]):
            return _view(principal.tenant_id, before, admin=True)
        out, orphans = await _save(conn, request, principal, before, "nav_colors",
                                   nav_colors={} if colors == nav_colors(None) else colors)
    await _forget_keys(request, orphans)
    return out


@router.get("/admin/branding/history")
async def branding_history(principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    """The kept versions, newest first, each with what it looked like (for the history list and its previews)."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await _row(conn)
        rows = (await conn.execute(text(
            "SELECT h.version, h.change, h.snapshot, h.changed_at, h.changed_by, u.display_name AS changed_by_name "
            "FROM ops.tenant_branding_history h LEFT JOIN iam.users u ON u.id = h.changed_by "
            "ORDER BY h.version DESC"))).mappings().all()
    current = row["version"] if row else 0
    items = []
    for h in rows:
        view = _view(principal.tenant_id, {**h["snapshot"], "version": h["version"]}, admin=False)
        palette = view["palette"]
        items.append({"version": h["version"], "change": h["change"], "changed_at": h["changed_at"],
                      "changed_by": {"id": str(h["changed_by"]), "name": h["changed_by_name"]}
                      if h["changed_by"] else None, "current": h["version"] == current,
                      "app_name": view["app_name"], "tagline": view["tagline"], "logo_sizes": view["logo_sizes"],
                      "chart_library": view["chart_library"], "nav_colors": view["nav_colors"],
                      "layout": view["layout"],
                      "palette": {"name": palette["name"], "accent": palette["tokens"]["light"]["accent"]}
                      if palette else None,
                      "assets": {slot: bool(a) for slot, a in view["assets"].items()}})
    return {"items": items, "version": current, "keep": HISTORY_KEEP}


@router.post("/admin/branding/rollback")
async def rollback_branding(body: RollbackIn, request: Request,
                            principal: Principal = Depends(require(Perm.BRANDING_MANAGE))) -> dict[str, Any]:
    """Make a kept version current again, as a new version (the history is never rewritten)."""
    async with tenant_tx(principal.tenant_id) as conn:
        before = await _locked(conn, principal)
        assert before is not None  # created by _locked
        _check_version(before, body.expected_version)
        snap = (await conn.execute(text("SELECT snapshot FROM ops.tenant_branding_history WHERE version = :v"),
                                   {"v": body.version})).scalar_one_or_none()
        if snap is None:
            raise TelaError(ErrorCode.NOT_FOUND, f"Version {body.version} is no longer kept.")
        if body.version == before["version"]:
            return _view(principal.tenant_id, before, admin=True)
        out, orphans = await _save(conn, request, principal, before, "rollback", note={"rolled_back_to": body.version},
                                   app_name=snap["app_name"], tagline=snap["tagline"], assets=snap.get("assets") or {},
                                   palette=snap.get("palette"), logo_sizes=snap.get("logo_sizes") or {},
                                   chart_library=snap.get("chart_library") or DEFAULT_CHART_LIBRARY,
                                   nav_colors=snap.get("nav_colors") or {},
                                   layout=snap.get("layout") or DEFAULT_LAYOUT)
    await _forget_keys(request, orphans)
    return out


async def export_branding(request: Request, tenant_id: str) -> dict[str, Any]:
    """What a PDF export shows of the brand: name, light logo inline (the print page fetches nothing), its height,
    the palette's light colours and the chart library. Any failure gives the Tela look rather than failing the
    export."""
    async with tenant_tx(tenant_id) as conn:
        row = await _row(conn)
    view = _view(tenant_id, row, admin=False)
    out: dict[str, Any] = {"app_name": view["app_name"], "logo": None, "logo_height": view["logo_sizes"]["export"],
                           "palette": view["palette"]["tokens"]["light"] if view["palette"] else None,
                           "chart_library": view["chart_library"]}
    asset = ((row or {}).get("assets") or {}).get("logo_light")
    if asset:
        with contextlib.suppress(Exception):
            data = await (await _store(request)).get(asset["key"])
            out["logo"] = f"data:{asset['content_type']};base64,{base64.b64encode(data).decode()}"
    return out


async def _forget_keys(request: Request, keys: set[str]) -> None:
    """Delete files no kept version uses, once the change is committed. A failure only leaves an unused file."""
    for key in keys:
        with contextlib.suppress(Exception):
            await (await _store(request)).delete(key)
