"""A pack draft from an approved model (ENH-025, US-236; FEAT-075): Create → *From an approved model*.

An Admin who built a model on one connection turns it into a pack, so another connection or tenant can reuse it.
The draft takes, from the connection's current approved model version:

* **entities and fields** — labels, types, semantic and sensitivity tags (personal and confidential kept), formats,
  descriptions; the source names become the candidates (``object_candidates``, ``columns``), followed by the
  candidates of the pack the model was installed from, if any, so the new pack matches this source exactly and the
  template's sources too. A field hidden from queries is left out unless a key, link, measure or dimension reads it.
* **accepted links** (proposed links nobody accepted are left out);
* **certified measures** — an uncertified (deprecated) one is offered but only taken when asked for (``measures``);
* **dimensions**, as the model has them;
* **knowledge** of the kinds a pack holds, when approved on this connection: the glossary, dashboard rules, chart
  patterns and analysis recipes of the installed pack whose knowledge document for this model version is approved
  (each cut to the measures and dimensions the new pack has, as indexing cuts them); approved value synonyms; approved
  gold examples (their Query Spec without the connection and model version);
* **chosen reports and dashboards** as report blueprints (``packs/blueprints.py``: each report a blueprint, a
  dashboard a starter dashboard with its tabs and shared filters); widgets on uncertified items, own rules or custom
  SQL are named and left out.

Everything not taken is listed with the reason (``left_out``). Routes: ``GET /packs/from-model/models`` (connections
with an approved model) and ``GET /packs/from-model/models/{connection_id}`` (what the Create dialog offers); the
draft itself is ``POST /packs`` with ``method: model`` and ``options`` (``create.CREATORS``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.content.access import readable_dashboard_draft, readable_report
from tela_api.content.render import freeze_dashboard
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.knowledge.guidance import (
    GlossaryFile,
    RecipesFile,
    RulesFile,
    recipe_for_model,
    rules_for_model,
)
from tela_api.knowledge.patterns import ChartPattern, for_model
from tela_api.model.store import build_snapshot
from tela_api.packs import blueprints as bps
from tela_api.packs.blueprint_router import frozen_report, snapshots
from tela_api.packs.source import PackSource, installed_source
from tela_api.packs.studio.deps import AUTHOR
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import measure_refs
from tela_query_spec.model import Dimension, ModelSnapshot

if TYPE_CHECKING:
    from tela_api.packs.studio.create import CreateArgs, Draft

router = APIRouter(prefix="/api/v1/packs/from-model", tags=["packs"])

# Pack knowledge kinds read from the installed pack, by the source kind of their knowledge document (its review).
INDEXED = {"glossary": "glossary", "dashboard_rules": "dashboard_rule", "chart_patterns": "chart_pattern",
           "analysis_recipes": "analysis_recipe"}
KNOWLEDGE = (*INDEXED, "value_synonyms", "gold_examples")
DOC_HEAD = {"version": "0.1.0", "status": "draft"}
MIN_CONFIDENCE = 0.6


@dataclass
class Model:
    """A connection's current approved model version."""

    connection_id: str
    connection: str
    engine: str
    version_id: str
    version_no: int | None
    data_model_id: str
    definition: dict[str, Any]
    pack: PackSource | None  # the pack version it was installed from

    @property
    def snap(self) -> ModelSnapshot:
        return build_snapshot(self.definition, version_id=self.version_id, connection_id=self.connection_id,
                              version_no=self.version_no, engine=self.engine)


@dataclass
class Built:
    head: dict[str, Any]
    documents: dict[str, Any]
    left_out: list[dict[str, str]] = field(default_factory=list)

    def leave(self, kind: str, key: str, reason: str) -> None:
        self.left_out.append({"kind": kind, "key": key, "reason": reason})


_MODEL_SQL = (
    "SELECT c.id AS connection_id, c.name, c.engine, m.id AS data_model_id, v.id AS version_id, v.version_no, "
    "v.definition FROM model.data_models m JOIN conn.connections c ON c.id = m.connection_id AND c.deleted_at IS NULL "
    "JOIN model.model_versions v ON v.id = m.current_version_id AND v.status = 'approved'")


async def load_model(conn: AsyncConnection, connection_id: str) -> Model:
    try:
        row = await fetch_one(conn, _MODEL_SQL + " WHERE c.id = CAST(:c AS uuid)", {"c": connection_id})
    except Exception:  # noqa: BLE001 - not a uuid: not found
        row = None
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "That connection has no approved model.")
    pack = await installed_source(conn, str(row["data_model_id"]))
    return Model(str(row["connection_id"]), row["name"], row["engine"], str(row["version_id"]), row["version_no"],
                 str(row["data_model_id"]), dict(row["definition"]), pack)


# --------------------------------------------------------------------------- manifest


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _bare(qualified: str) -> str:
    return qualified.rpartition(".")[2]


def _used_fields(defn: dict[str, Any], measures: list[dict[str, Any]]) -> set[str]:
    """``entity.field`` every key, display field, accepted link, measure and dimension reads."""
    out: set[str] = set()
    for e in defn.get("entities", []):
        out |= {f"{e['key']}.{k}" for k in [*e.get("primary_key", []), *e.get("display_fields", [])]}
    for ln in defn.get("links", []):
        if ln.get("accepted", True):
            out |= {f"{ln['from_entity']}.{k}" for k in ln["from_fields"]}
            out |= {f"{ln['to_entity']}.{k}" for k in ln["to_fields"]}
    from tela_query_spec.model import Measure

    for m in measures:
        mm = Measure.model_validate(m)
        out |= measure_refs(mm.entity, mm.expression)
        if mm.currency_field:
            out.add(f"{mm.entity}.{mm.currency_field}")
    for d in defn.get("dimensions", []):
        dd = Dimension.model_validate(d)
        out |= {f"{dd.entity}.{f}" for f in dd.fields()}
    return out


def manifest_from_model(model: Model, *, key: str, title: str, version: str, released_at: str,
                        measures: list[str] | None = None) -> Built:
    """The pack manifest (no reports yet) from the model; ``measures``: uncertified measure keys to take too."""
    defn = model.definition
    tmpl = model.pack.head() if model.pack else {}
    tmpl_entities = {e["key"]: e for e in tmpl.get("entities", [])}
    built = Built({}, {})
    wanted = set(measures or [])
    kept_measures = []
    for m in defn.get("measures", []):
        if m.get("status", "certified") == "certified" or m["key"] in wanted:
            kept_measures.append(copy.deepcopy(m))
        else:
            built.leave("measure", m["key"], "not certified (tick it to take it)")
    used = _used_fields(defn, kept_measures)
    entities = []
    for e in defn.get("entities", []):
        te = tmpl_entities.get(e["key"], {})
        tf = {f["key"]: f for f in te.get("fields", [])}
        fields = []
        for f in e.get("fields", []):
            if not f.get("is_queryable", True) and f"{e['key']}.{f['key']}" not in used:
                built.leave("field", f"{e['key']}.{f['key']}", "hidden from queries in the model")
                continue
            t = tf.get(f["key"], {})
            fields.append({k: v for k, v in {
                "key": f["key"], "label": f["label"], "data_type": f["data_type"],
                "columns": _dedupe([f["source_column"], *t.get("columns", [])]),
                "required": f["key"] in e.get("primary_key", []) or None,
                "semantic_type": f.get("semantic_type"), "sensitivity": f.get("sensitivity", "internal"),
                "format": f.get("format") or None, "description": f.get("description"),
                "blank_handling": f.get("blank_handling"), "synonyms": t.get("synonyms") or None,
                "values": t.get("values") or None,
            }.items() if v is not None})
        entities.append({k: v for k, v in {
            "key": e["key"], "label": e["label"], "grain": e.get("grain"),
            "object_candidates": _dedupe([_bare(e["source_object"]), *te.get("object_candidates", [])]),
            "primary_key": list(e["primary_key"]), "display_fields": list(e.get("display_fields", [])) or None,
            "synonyms": te.get("synonyms") or None, "fields": fields,
        }.items() if v is not None})
    links = []
    for ln in defn.get("links", []):
        name = f"{ln['from_entity']} → {ln['to_entity']}"
        if not ln.get("accepted", True):
            built.leave("link", name, "not accepted in the model")
            continue
        links.append({k: v for k, v in {
            "from_entity": ln["from_entity"], "to_entity": ln["to_entity"], "from_fields": list(ln["from_fields"]),
            "to_fields": list(ln["to_fields"]), "cardinality": ln["cardinality"],
            "inherits_scope": True if ln.get("inherits_scope") else None}.items() if v is not None})
    primary = (tmpl.get("signature") or {}).get("primary_entity")
    if primary not in {e["key"] for e in entities}:
        counts: dict[str, int] = {}
        for m in kept_measures:
            counts[m["entity"]] = counts.get(m["entity"], 0) + 1
        primary = max(entities, key=lambda e: counts.get(e["key"], 0))["key"] if entities else None
    built.head = {"pack_key": key, "pack_version": version, "title": title, "released_at": released_at,
                  "signature": {**({"primary_entity": primary} if primary else {}), "min_confidence": MIN_CONFIDENCE},
                  "entities": entities, "links": links, "measures": kept_measures,
                  "dimensions": copy.deepcopy(defn.get("dimensions", []))}
    return built


def pack_snapshot(model: Model, head: dict[str, Any]) -> ModelSnapshot:
    """The model as the pack has it (its measures and dimensions), for cutting knowledge to what the pack holds."""
    return model.snap.model_copy(update={
        "measures": [m for m in model.snap.measures if m.key in {x["key"] for x in head["measures"]}]})


# --------------------------------------------------------------------------- knowledge


def _refs(node: Any) -> set[str]:
    """Every ``measure:…`` / ``dimension:…`` reference in a Query Spec."""
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "ref" and isinstance(v, str) and ":" in v:
                out.add(v)
            else:
                out |= _refs(v)
    elif isinstance(node, list):
        for v in node:
            out |= _refs(v)
    return out


def _cut_indexed(kind: str, body: dict[str, Any], snap: ModelSnapshot, built: Built) -> dict[str, Any] | None:
    """An installed pack document cut to the measures and dimensions the new pack has (as indexing cuts it)."""
    if kind == "glossary":
        gf = GlossaryFile.model_validate(body)
        keep = []
        for t, raw in zip(gf.terms, body.get("terms", []), strict=True):
            fields_ok = all((e := snap.entity(r.split(".", 1)[0])) is not None and e.field(r.split(".", 1)[1])
                            is not None for r in t.fields)
            if fields_ok and (t.reading is None or snap.measure(t.reading) is not None):
                keep.append({**raw, **({"instead_of": [k for k in t.instead_of if snap.measure(k)]}
                                       if t.instead_of else {})})
            else:
                built.leave("glossary", t.term, "names a field or measure the pack doesn't have")
        return {**body, "terms": keep} if keep else None
    if kind == "dashboard_rules":
        rf = RulesFile.model_validate(body)
        ok = {r.id for r in rules_for_model(rf.rules, snap)}
        for r in rf.rules:
            if r.id not in ok:
                built.leave("dashboard_rule", r.id, "names a measure the pack doesn't have")
        rules = [r for r in body.get("rules", []) if r.get("id") in ok]
        return {**body, "rules": rules} if rules or body.get("wording") else None
    if kind == "chart_patterns":
        pats = [ChartPattern.model_validate(p) for p in body.get("patterns", [])]
        ok = {p.id for p in for_model(pats, snap)}
        for p in pats:
            if p.id not in ok:
                built.leave("chart_pattern", p.id, "names a measure or dimension the pack doesn't have")
        kept = [p for p in body.get("patterns", []) if p.get("id") in ok]
        return {**body, "patterns": kept} if kept else None
    # analysis_recipes: each slot cut to the options the pack has; a slot left with none drops the recipe.
    recipes = RecipesFile.model_validate(body).recipes
    out = []
    for rc in recipes:
        cut = recipe_for_model(rc, snap)
        if cut is None:
            built.leave("analysis_recipe", rc.id, "a slot has no measure or dimension the pack has")
        else:
            out.append(cut.model_dump(mode="json", exclude_defaults=True, exclude_none=True))
    return {**body, "recipes": out} if out else None


async def _approved_kinds(conn: AsyncConnection, model: Model) -> dict[str, str]:
    """Review status of this model version's indexed pack knowledge, by pack kind."""
    rows = await fetch_all(conn, "SELECT source_kind, review_status FROM rag.knowledge_documents WHERE "
                                 "model_version_id = CAST(:v AS uuid) AND source_kind = ANY(:k)",
                           {"v": model.version_id, "k": list(INDEXED.values())})
    by_source = {r["source_kind"]: r["review_status"] for r in rows}
    return {kind: by_source.get(src, "not indexed") for kind, src in INDEXED.items()}


async def _value_synonyms(conn: AsyncConnection, model: Model, dims: set[str]) -> list[dict[str, Any]]:
    rows = await fetch_all(conn, "SELECT dimension, stored_value, synonym FROM rag.value_synonyms WHERE "
                                 "connection_id = CAST(:c AS uuid) AND status = 'approved' ORDER BY dimension, "
                                 "stored_value, synonym", {"c": model.connection_id})
    grouped: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        if r["dimension"] in dims:
            grouped.setdefault((r["stored_value"], r["dimension"]), []).append(r["synonym"])
    # One entry per value and synonym list, naming every dimension it applies to.
    merged: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for (value, dim), syns in grouped.items():
        merged.setdefault((value, tuple(_dedupe(syns))), []).append(dim)
    return [{"dimensions": d, "value": v, "synonyms": list(s)} for (v, s), d in merged.items()]


async def _gold(conn: AsyncConnection, model: Model, measures: set[str], dims: set[str],
                built: Built) -> list[dict[str, Any]]:
    rows = await fetch_all(conn, "SELECT question, expected_spec, tags, notes FROM rag.gold_examples WHERE "
                                 "connection_id = CAST(:c AS uuid) AND status = 'approved' ORDER BY created_at, "
                                 "question", {"c": model.connection_id})
    have = {f"measure:{k}" for k in measures} | {f"dimension:{k}" for k in dims}
    out, seen = [], set()
    for r in rows:
        spec = {k: v for k, v in dict(r["expected_spec"]).items() if k not in ("model_version_id", "connection_id")}
        if r["question"] in seen:
            continue
        missing = _refs(spec) - have
        if missing:
            built.leave("gold_example", r["question"], f"uses {', '.join(sorted(missing))}, not in the pack")
            continue
        seen.add(r["question"])
        out.append({k: v for k, v in {"question": r["question"], "tags": list(r["tags"] or []) or None,
                                      "note": r["notes"], "spec": spec}.items() if v is not None})
    return out


async def add_knowledge(conn: AsyncConnection, model: Model, built: Built, kinds: set[str]) -> None:
    snap = pack_snapshot(model, built.head)
    measures = {m.key for m in snap.measures}
    dims = {d.key for d in snap.dimensions}
    status = await _approved_kinds(conn, model)
    for kind in INDEXED:
        if kind not in kinds:
            continue
        body = model.pack.document(kind) if model.pack else None
        if body is None:
            continue
        if status[kind] != "approved":
            built.leave("knowledge", kind, f"not approved on this connection ({status[kind].replace('_', ' ')})")
            continue
        cut = _cut_indexed(kind, copy.deepcopy(body), snap, built)
        if cut is not None:
            built.documents[kind] = {**cut, **DOC_HEAD}
    if "value_synonyms" in kinds and (values := await _value_synonyms(conn, model, dims)):
        built.documents["value_synonyms"] = {**DOC_HEAD, "values": values}
    if "gold_examples" in kinds and (examples := await _gold(conn, model, measures, dims, built)):
        built.documents["gold_examples"] = {**DOC_HEAD, "examples": examples}


# --------------------------------------------------------------------------- reports and dashboards


async def add_reports(conn: AsyncConnection, principal: Principal, built: Built, report_ids: list[str],
                      dashboard_ids: list[str]) -> None:
    """The chosen dashboards as starter dashboards and the chosen reports as blueprints (on a dashboard named after
    the primary entity); a blueprint with nothing left, or with problems, is listed instead."""
    head = built.head
    doc = bps.empty_document()
    for did in dashboard_ids:
        d = await readable_dashboard_draft(conn, principal, did)
        frozen = await freeze_dashboard(conn, d)
        snaps = await snapshots(conn, principal.tenant_id, frozen["reports"])
        key = bps.slug(d["title"], set(head.get("dashboards") or {}))[:41]
        made = bps.blueprints_from_dashboard(frozen, snaps, head, doc, key=key, title=d["title"],
                                             shared_filters=d["shared_filters"])
        keep = set()
        for raw in made.blueprints:
            if _usable(raw, made.problems.get(raw["id"]), built):
                keep.add(raw["id"])
        built.left_out += [{"kind": "widget", "key": x["title"], "reason": x["reason"],
                            **({"report": x["report"]} if "report" in x else {})} for x in made.left_out]
        head, doc, problems = bps.add_dashboard(doc, made, only=keep)
        for p in problems:
            built.leave("dashboard", d["title"], p["message"])
    if report_ids:
        primary = head["signature"].get("primary_entity") or "reports"
        label = next((e["label"] for e in head["entities"] if e["key"] == primary), "Reports")
        key = bps.slug(primary, set(head.get("dashboards") or {}))[:41]
        head = {**head, "dashboards": {**(head.get("dashboards") or {}),
                                       key: {"title": f"{head['title']} — {label}", "reports": [], "filters": []}}}
        taken = {str(b.get("id")) for b in doc["blueprints"]}
        for rid in report_ids:
            report = await readable_report(conn, principal, rid)
            frozen = await frozen_report(conn, report["id"], report["title"])
            snaps = await snapshots(conn, principal.tenant_id, [frozen])
            bid = bps.slug(report["title"], taken)
            raw, tones, left = bps.blueprint_from_report(frozen, snaps, head, bid=bid, dashboard=key,
                                                         taken_tones=doc.get("tones"))
            built.left_out += [{"kind": "widget", "key": x["title"], "reason": x["reason"],
                                "report": report["title"]} for x in left]
            doc2 = {**doc, "tones": {**(doc.get("tones") or {}), **tones}}
            if _usable(raw, bps.check_blueprint(raw, head, doc2), built, title=report["title"]):
                taken.add(bid)
                head, doc = bps.put_blueprint(head, doc2, raw)
        if not head["dashboards"][key]["reports"]:
            head = {**head, "dashboards": {k: v for k, v in head["dashboards"].items() if k != key}}
    for k, d in list((head.get("dashboards") or {}).items()):
        if not d.get("reports"):  # every tab was left out
            built.leave("dashboard", str(d.get("title") or k), "none of its reports can be a blueprint")
            head = {**head, "dashboards": {x: v for x, v in head["dashboards"].items() if x != k}}
    if doc["blueprints"]:
        built.head = {**head, "reports_from": bps.BLUEPRINTS_FILE}
        built.documents["report_blueprints"] = doc
    else:
        built.head = {**head, "dashboards": {}}


def _usable(raw: dict[str, Any], problems: list[dict[str, str]] | None, built: Built, *,
            title: str | None = None) -> bool:
    name = title or str(raw.get("title"))
    if not raw.get("kpis"):
        built.leave("report", name, "none of its widgets can be a blueprint")
        return False
    if problems:
        built.leave("report", name, problems[0]["message"])
        return False
    return True


# --------------------------------------------------------------------------- the creator and the dialog's routes


def _strings(value: Any, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"{what} must be a list of ids.")
    return list(dict.fromkeys(value))


async def create(conn: AsyncConnection, principal: Principal, args: CreateArgs) -> Draft:
    """``options``: ``connection_id`` (required), ``measures`` (uncertified measure keys to take), ``report_ids``,
    ``dashboard_ids``, ``knowledge`` (kinds to take; default every one)."""
    from datetime import UTC, datetime

    from tela_api.packs.studio.create import Draft

    opts = args.options
    if not isinstance(opts.get("connection_id"), str):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the connection whose approved model becomes the pack.")
    model = await load_model(conn, opts["connection_id"])
    kinds = set(_strings(opts.get("knowledge"), "knowledge")) if "knowledge" in opts else set(KNOWLEDGE)
    if unknown := kinds - set(KNOWLEDGE):
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Unknown knowledge: {', '.join(sorted(unknown))}.")
    measures = _strings(opts.get("measures"), "measures")
    have = {m["key"] for m in model.definition.get("measures", [])}
    if unknown := set(measures) - have:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"The model has no measure {', '.join(sorted(unknown))}.")
    built = manifest_from_model(model, key=args.pack_key, title=args.title, version=args.version,
                                released_at=datetime.now(UTC).strftime("%Y-%m-%dT00:00:00Z"), measures=measures)
    await add_knowledge(conn, model, built, kinds)
    await add_reports(conn, principal, built, _strings(opts.get("report_ids"), "report_ids"),
                      _strings(opts.get("dashboard_ids"), "dashboard_ids"))
    return Draft(built.head, built.documents, based_on=None, left_out=built.left_out,
                 source={"connection_id": model.connection_id, "model_version_id": model.version_id,
                         "version_no": model.version_no,
                         "reporting_date": (model.definition.get("settings") or {}).get("reporting_date")})


@router.get("/models")
async def list_models(principal: Principal = AUTHOR) -> dict[str, Any]:
    """Connections with an approved model, the ones a pack can be made from."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(conn, _MODEL_SQL + " ORDER BY c.name")
    return {"items": [{"connection_id": str(r["connection_id"]), "name": r["name"], "engine": r["engine"],
                       "model_version_id": str(r["version_id"]), "version_no": r["version_no"],
                       "counts": {k: len(r["definition"].get(k) or []) for k in
                                  ("entities", "links", "measures", "dimensions")}} for r in rows]}


@router.get("/models/{connection_id}")
async def model_contents(connection_id: str, principal: Principal = AUTHOR) -> dict[str, Any]:
    """What a pack from this model would hold, and the choices: uncertified measures (off by default), the reports
    and dashboards on this connection the Admin can read, and the knowledge with its review."""
    async with tenant_tx(principal.tenant_id) as conn:
        model = await load_model(conn, connection_id)
        status = await _approved_kinds(conn, model)
        syn = await fetch_one(conn, "SELECT count(*) AS n FROM rag.value_synonyms WHERE connection_id = "
                                    "CAST(:c AS uuid) AND status = 'approved'", {"c": model.connection_id})
        gold = await fetch_one(conn, "SELECT count(*) AS n FROM rag.gold_examples WHERE connection_id = "
                                     "CAST(:c AS uuid) AND status = 'approved'", {"c": model.connection_id})
        reports = await fetch_all(conn, (
            "SELECT r.id, r.title, r.origin, r.updated_at FROM content.reports r WHERE r.deleted_at IS NULL AND "
            "(r.origin = 'starter' OR r.owner_id = CAST(:u AS uuid) OR EXISTS (SELECT 1 FROM content.shares s "
            "WHERE s.object_type = 'report' AND s.object_id = r.id AND s.grantee_user_id = CAST(:u AS uuid))) AND "
            "EXISTS (SELECT 1 FROM content.report_items i JOIN content.widgets w ON w.id = i.widget_id WHERE "
            "i.report_id = r.id AND w.connection_id = CAST(:c AS uuid)) ORDER BY r.origin <> 'user', r.title "
            "LIMIT 300"), {"u": principal.user_id, "c": model.connection_id})
        dashboards = await fetch_all(conn, (
            "SELECT d.id, d.title, d.updated_at, (SELECT count(*) FROM content.dashboard_reports dr WHERE "
            "dr.dashboard_id = d.id) AS tabs FROM content.dashboards d WHERE d.kind = 'user' AND d.deleted_at IS "
            "NULL AND EXISTS (SELECT 1 FROM content.dashboard_reports dr JOIN content.report_items i ON i.report_id = "
            "dr.report_id JOIN content.widgets w ON w.id = i.widget_id WHERE dr.dashboard_id = d.id AND "
            "w.connection_id = CAST(:c AS uuid)) AND (d.owner_id = CAST(:u AS uuid) OR :pub OR EXISTS "
            "(SELECT 1 FROM content.shares s WHERE s.object_type = 'dashboard' AND s.object_id = d.id AND "
            "s.grantee_user_id = CAST(:u AS uuid))) ORDER BY d.title LIMIT 200"),
            {"u": principal.user_id, "c": model.connection_id, "pub": principal.has(Perm.DASHBOARDS_PUBLISH)})
    defn = model.definition
    links = defn.get("links", [])
    knowledge = []
    for kind in KNOWLEDGE:
        if kind in INDEXED:
            body = model.pack.document(kind) if model.pack else None
            count = bps_size(body)
            ok = body is not None and status[kind] == "approved"
            reason = None if ok else ("the model was not installed from a pack with this knowledge" if body is None
                                      else f"not approved on this connection ({status[kind].replace('_', ' ')})")
        else:
            count = int((syn if kind == "value_synonyms" else gold)["n"])  # type: ignore[index]
            ok, reason = count > 0, None if count else "none approved on this connection"
        knowledge.append({"kind": kind, "count": count, "available": ok, "reason": reason})
    return {
        "connection_id": model.connection_id, "name": model.connection, "model_version_id": model.version_id,
        "version_no": model.version_no, "installed_pack": model.pack.pack_key if model.pack else None,
        "counts": {"entities": len(defn.get("entities", [])),
                   "fields": sum(len(e.get("fields", [])) for e in defn.get("entities", [])),
                   "links": sum(1 for ln in links if ln.get("accepted", True)),
                   "links_not_accepted": sum(1 for ln in links if not ln.get("accepted", True)),
                   "dimensions": len(defn.get("dimensions", [])),
                   "personal_fields": sum(1 for e in defn.get("entities", []) for f in e.get("fields", [])
                                          if f.get("sensitivity") in ("confidential", "personal", "sensitive"))},
        "measures": [{"key": m["key"], "label": m["label"], "entity": m["entity"],
                      "status": m.get("status", "certified")} for m in defn.get("measures", [])],
        "knowledge": knowledge,
        "reports": [{"id": str(r["id"]), "title": r["title"], "origin": r["origin"]} for r in reports],
        "dashboards": [{"id": str(d["id"]), "title": d["title"], "tabs": int(d["tabs"])} for d in dashboards],
        "reporting_date": (defn.get("settings") or {}).get("reporting_date"),
    }


def bps_size(body: dict[str, Any] | None) -> int:
    if not isinstance(body, dict):
        return 0
    return max((len(v) for v in body.values() if isinstance(v, list)), default=0)
