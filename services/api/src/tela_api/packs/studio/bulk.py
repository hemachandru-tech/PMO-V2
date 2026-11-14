"""One YAML file for a draft's measures, dimensions, report blueprints and starter dashboards (Pack Studio import).

The pack screen's **Import YAML** takes these four parts in one file, each written as in the pack folder::

    measures:     [ {key, label, entity, plain_rule, expression, …} ]   # manifest.yaml, matched by key
    dimensions:   [ {key, label, entity, field, …} ]                    # manifest.yaml, matched by key
    reports:      [ {id, title, dashboard, question, kpis, widgets, …} ] # report_blueprints.yaml, matched by id
    tones:        {measure: {warning, critical, …}}                     # report_blueprints.yaml (optional)
    dashboards:   {key: {title, reports: [blueprint ids], filters: [dimension keys]}}  # manifest.yaml

Every part is optional. ``merge`` adds new items and replaces those with the same key; ``replace`` makes each part
in the file the whole of that part (a part left out of the file is kept). Measures and dimensions go in first, so
the file's blueprints may use them; a dashboard in the file exists before its blueprints are placed on it, and its
``reports`` (when given) set its tabs in order afterwards. A data gap (a blueprint with ``unavailable``) listed as a tab
stays in the pack without a tab, with a warning.

* ``GET /packs/{id}/bundle`` — the draft's four parts as such a file (to edit and import back, or as a template).
* ``POST /packs/{id}/bundle`` — check (``dry_run``) or import the file on the revision in ``If-Match``. Nothing is
  saved when any part has an error; each error names its path in the file (``reports[2].kpis.0``).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from tela_api.audit import service as audit
from tela_api.core.db import tenant_tx
from tela_api.identity.principal import Principal
from tela_api.packs import blueprints as bps
from tela_api.packs.blueprint_router import DASHBOARD_KEY
from tela_api.packs.studio import knowledge, store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.transfer import dump
from tela_api.packs.studio.validate import validate
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])

PARTS = ("measures", "dimensions", "reports", "tones", "dashboards")
BLUEPRINTS = "report_blueprints"
MAX_YAML = 2 * 1024 * 1024  # characters (the PMO pack's four parts are ≈ 0.2 MB)


@dataclass
class Applied:
    head: dict[str, Any]
    doc: dict[str, Any]
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    # Part → added / updated / removed keys; unchanged is a count.
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)


def bundle_of(head: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """The draft's four parts as the import file writes them."""
    out: dict[str, Any] = {"measures": list(head.get("measures") or []),
                           "dimensions": list(head.get("dimensions") or []),
                           "reports": list(doc.get("blueprints") or [])}
    if doc.get("tones"):
        out["tones"] = dict(doc["tones"])
    out["dashboards"] = {k: {"title": d.get("title", k), "reports": list(d.get("reports", [])),
                             "filters": list(d.get("filters", []))} for k, d in (head.get("dashboards") or {}).items()}
    return out


def parse(text: str) -> dict[str, Any]:
    """The file as a mapping of known parts; a refusal names what is wrong."""
    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"This isn't valid YAML{where}.",
                        {"errors": [{"path": "", "message": str(exc).splitlines()[0]}], "warnings": []}) from exc
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Write the file as a mapping with measures, dimensions, reports "
                                                    "and dashboards.", {"errors": [], "warnings": []})
    unknown = sorted(str(k) for k in body if k not in PARTS)
    if unknown:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"These parts can't be imported: {', '.join(unknown)}.",
                        {"errors": [{"path": k, "message": f"Use {', '.join(PARTS)}."} for k in unknown],
                         "warnings": []})
    return body


def _listed(items: Any, part: str, ident: str, errors: list[dict[str, str]]) -> list[tuple[int, dict[str, Any]]]:
    """The part's items (with their place in the file) when it is a list of mappings each with its own ``ident``;
    errors for the rest."""
    if not isinstance(items, list):
        errors.append({"path": part, "message": "Write this part as a list."})
        return []
    seen: set[str] = set()
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get(ident), str) or not item[ident]:
            errors.append({"path": f"{part}[{i}]", "message": f"Each item needs a {ident}."})
            continue
        if item[ident] in seen:
            errors.append({"path": f"{part}[{i}].{ident}", "message": f"{item[ident]} is in the file twice."})
            continue
        seen.add(item[ident])
        out.append((i, item))
    return out


def _diff(before: dict[str, Any], after: dict[str, Any], touched: set[str]) -> dict[str, Any]:
    return {"added": [k for k in after if k not in before],
            "updated": [k for k in after if k in before and before[k] != after[k]],
            "removed": [k for k in before if k not in after],
            "unchanged": sum(1 for k in touched if k in before and before[k] == after.get(k))}


def _upsert(current: list[dict[str, Any]], items: list[dict[str, Any]], ident: str, replace: bool
            ) -> list[dict[str, Any]]:
    if replace:
        return list(items)
    by_key = {str(i.get(ident)): i for i in items}
    out = [by_key.pop(str(c.get(ident)), c) for c in current]
    return out + list(by_key.values())


def apply(head: dict[str, Any], doc: dict[str, Any], bundle: dict[str, Any], *, replace: bool = False) -> Applied:
    """The draft with the file's parts in it (merged by key, or each part replaced), and each part's errors."""
    head, doc = copy.deepcopy(head), copy.deepcopy(doc)
    out = Applied(head, doc)
    errors = out.errors
    for part in ("measures", "dimensions"):
        if part not in bundle:
            continue
        items = [it for _, it in _listed(bundle[part], part, "key", errors)]
        before = {str(m.get("key")): m for m in head.get(part) or []}
        head[part] = _upsert(head.get(part) or [], items, "key", replace)
        out.changes[part] = _diff(before, {str(m.get("key")): m for m in head[part]}, {i["key"] for i in items})

    dash_before = copy.deepcopy(head.get("dashboards") or {})
    dashboards = bundle.get("dashboards")
    if dashboards is not None:
        if not isinstance(dashboards, dict):
            errors.append({"path": "dashboards", "message": "Write the dashboards as a mapping of key to dashboard."})
            dashboards = {}
        for key, d in list(dashboards.items()):
            if not isinstance(d, dict) or not isinstance(d.get("title"), str) or not d["title"].strip():
                errors.append({"path": f"dashboards.{key}", "message": "Each dashboard needs a title."})
                dashboards.pop(key)
            elif not isinstance(key, str) or not re.match(DASHBOARD_KEY, key):
                errors.append({"path": f"dashboards.{key}", "message": "A dashboard key is lower case letters, digits "
                                                                     "and _."})
                dashboards.pop(key)
        # Shells first (title; tabs and filters as they are), so the file's blueprints can sit on them.
        kept = {} if replace else dict(head.get("dashboards") or {})
        for key, d in dashboards.items():
            was = kept.get(key) or dash_before.get(key) or {}
            kept[key] = {**was, "title": d["title"].strip(), "reports": [] if replace else list(was.get("reports", [])),
                         "filters": list(was.get("filters", []))}
        head["dashboards"] = kept

    if "tones" in bundle:
        tones = bundle["tones"]
        if not isinstance(tones, dict):
            errors.append({"path": "tones", "message": "Write the tones as a mapping of measure to thresholds."})
        else:
            doc["tones"] = dict(tones) if replace else {**(doc.get("tones") or {}), **tones}

    bp_before = {str(b.get("id")): b for b in doc.get("blueprints") or []}
    if "reports" in bundle:
        listed = _listed(bundle["reports"], "reports", "id", errors)
        if replace:
            doc["blueprints"] = []
            head["dashboards"] = {k: {**d, "reports": []} for k, d in (head.get("dashboards") or {}).items()}
        for i, raw in listed:
            problems = bps.check_blueprint(raw, head, doc)
            if problems:
                errors += [{"path": f"reports[{i}]" + (f".{p['path']}" if p["path"] else ""),
                            "message": p["message"] + (f": add {raw.get('dashboard')} under dashboards in the file"
                                                       if p["path"] == "dashboard" else "")} for p in problems]
                continue
            exists = raw["id"] in [b.get("id") for b in doc.get("blueprints", [])]
            head, doc = bps.put_blueprint(head, doc, raw, replaces=raw["id"] if exists else None)
        out.changes["reports"] = _diff(bp_before, {str(b.get("id")): b for b in doc.get("blueprints") or []},
                                       {raw["id"] for _, raw in listed})

    if dashboards:
        gaps = {str(b.get("id")) for b in doc.get("blueprints") or [] if b.get("unavailable")}
        for key, d in dashboards.items():
            now = head["dashboards"][key]
            tabs = d["reports"] if isinstance(d.get("reports"), list) else now["reports"]
            out.warnings += [{"path": f"dashboards.{key}.reports.{j}",
                              "message": f"{r} is a data gap: it stays in the pack without a tab."}
                             for j, r in enumerate(tabs) if r in gaps]
            body = {"title": now["title"], "reports": [r for r in tabs if r not in gaps],
                    "filters": d["filters"] if isinstance(d.get("filters"), list) else now["filters"]}
            head, doc, problems = bps.put_dashboard(head, doc, key, body)
            errors += [{"path": f"dashboards.{key}.{p['path']}", "message": p["message"]} for p in problems]
    if dashboards is not None:
        names = set(head.get("dashboards") or {})
        errors += [{"path": "dashboards", "message": f"Blueprint {b.get('id')} sits on dashboard {b.get('dashboard')}, "
                                                     "which the file leaves out."}
                   for b in doc.get("blueprints") or [] if b.get("dashboard") not in names]
        out.changes["dashboards"] = _diff(dash_before, head.get("dashboards") or {}, set(dashboards))
    out.head, out.doc = head, doc
    return out


# --------------------------------------------------------------------------- endpoints


@router.get("/{pack_id}/bundle")
async def get_bundle(pack_id: str, response: Response, principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
    response.headers["ETag"] = row.etag
    return {"yaml": dump(bundle_of(row.manifest, docs.get(BLUEPRINTS) or bps.empty_document())),
            "file_name": f"{row.pack_key}-{row.pack_version}-items.yaml"}


class BundleIn(BaseModel):
    yaml: str = Field(max_length=MAX_YAML)
    mode: Literal["merge", "replace"] = "merge"
    dry_run: bool = False


@router.post("/{pack_id}/bundle")
async def import_bundle(pack_id: str, body: BundleIn, request: Request, response: Response,
                        if_match: str | None = Header(default=None), principal: Principal = AUTHOR) -> dict[str, Any]:
    bundle = parse(body.yaml)
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=not body.dry_run)
        store.require_editable(row)
        if not body.dry_run:
            store.require_revision(row, if_match)
        docs = await store.documents(conn, row.id)
        applied = apply(row.manifest, docs.get(BLUEPRINTS) or bps.empty_document(), bundle,
                        replace=body.mode == "replace")
        new_docs = {**docs, BLUEPRINTS: applied.doc} if (BLUEPRINTS in docs or "reports" in bundle
                                                         or "tones" in bundle) else docs
        report = validate(applied.head, new_docs) if not applied.errors else None
        errors = applied.errors + (report.errors if report else [])
        checked = {"valid": not errors, "errors": errors,
                   "warnings": applied.warnings + (report.warnings if report else []),
                   "changes": applied.changes}
        if body.dry_run:
            return checked
        if errors:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Fix the errors before importing.", checked)
        await store.save_manifest(conn, row, applied.head)
        if new_docs is not docs:
            await knowledge.write_document(conn, row, BLUEPRINTS, applied.doc)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.bundle_imported", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "mode": body.mode,
                                    "changes": {p: {k: len(v) if isinstance(v, list) else v for k, v in c.items()}
                                                for p, c in applied.changes.items()}})
        saved = await store.load(conn, row.id)
    response.headers["ETag"] = saved.etag
    return {**store.summary(saved), "manifest": store.ordered(saved.manifest), "checks": checked}
