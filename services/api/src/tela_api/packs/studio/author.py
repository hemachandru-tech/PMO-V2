"""The AI pack author (ENH-025, US-237; FEAT-075 / AG-04 pack mode; API-074): AI drafts a pack from the read schema
and the Admin reviews every item.

* ``GET /packs/{id}/author?connection_id=`` — what the author can do here: whether a model is set up, whether values
  may go to it (DEC-27), the template packs, the stages with what the draft holds, the tables not in the draft yet.
* ``POST /packs/{id}/author`` — one stage, streamed (SSE): ``stage``, then each proposal as it is checked
  (``proposal``), each one that failed its checks (``dropped``, with the reasons), ``note``, ``complete``, ``error``.
  Stages: ``entities`` (tables → entities and fields), ``links``, ``measures`` and ``dimensions`` (per entity, at most
  20 at a time), ``synonyms`` and ``knowledge`` (``kind``: glossary, value synonyms, dashboard rules and wording,
  chart patterns, analysis recipes). Nothing is written to the draft.
* ``POST /packs/{id}/author/check`` — the same checks on items the Admin edited (nothing is written).
* ``POST /packs/{id}/author/accept`` — check the accepted items again and add them to the draft (``If-Match`` for
  manifest items; knowledge documents are appended to under the row lock).

How a stage works: Tela's rules draft the stage (``author_rules``) from the catalogue and its own value profile
(US-199); with a model set up, the model gets the rules draft, the read schema and the template pack's items of the
same kind as worked examples, and its answer replaces the rules draft (one call per table batch, entity or kind). A
failed or invalid answer keeps the rules draft (marked "rules"), so a stage always proposes. Links are drafted by rules
only (foreign keys, then names) and their cardinality is checked by two counts. Every proposal is checked
(``author_checks``: schema, references, filter values from the profile) and, against the source, compiled and counted
(the trial figure, US-199, with its sanity warnings: a measure returning 0, measures with one figure). With DEC-27 off
only names and types reach the model: the rules draft's values go as tokens (``author_llm.Masker``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.principal import Principal
from tela_api.llm import trace
from tela_api.llm.client import AgentFailed, GatewayClient, run_agent
from tela_api.llm.planner_fit import planner_provider
from tela_api.model import guards
from tela_api.model.catalog import PERSONAL, stamp_source_types
from tela_api.model.store import build_snapshot
from tela_api.packs.manifest import PackManifest, bind, match
from tela_api.packs.studio import author_checks as checks
from tela_api.packs.studio import author_llm as llm
from tela_api.packs.studio import author_rules as rules
from tela_api.packs.studio import knowledge, store
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.studio.validate import Report, validate
from tela_api.query.asof import pin_dates, reporting_date, today_in
from tela_api.query.compilers import compile_spec
from tela_api.query.service import QueryContext, QueryService
from tela_api.query.validator import SpecValidator
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_common.logging import get_logger
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import QuerySpec

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])
log = get_logger(__name__)

Stage = Literal["entities", "links", "measures", "dimensions", "synonyms", "knowledge"]
STAGES: tuple[str, ...] = ("entities", "links", "measures", "dimensions", "synonyms", "knowledge")
STAGE_KIND = {"entities": "entity", "links": "link", "measures": "measure", "dimensions": "dimension",
              "synonyms": "synonyms"}
KNOWLEDGE_KINDS = tuple(checks.KNOWLEDGE_ITEMS)
MAX_TABLES = 30  # tables drafted per entities run
TABLE_BATCH = 8  # tables per model call
MAX_PROFILED = 80  # text fields whose values are read per run
SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "personal": 3, "sensitive": 4}


class AuthorIn(BaseModel):
    connection_id: str = Field(min_length=36, max_length=36)
    stage: Stage
    kind: str | None = Field(default=None, max_length=40, description="Knowledge kind, for the knowledge stage")
    template_pack_id: str | None = Field(default=None, max_length=36)
    tables: list[str] = Field(default_factory=list, max_length=200, description="Entities stage: qualified names")
    entities: list[str] = Field(default_factory=list, max_length=100, description="Measures, dimensions: keys")


class ItemsIn(BaseModel):
    connection_id: str = Field(min_length=36, max_length=36)
    kind: Literal["entity", "link", "measure", "dimension", "synonyms", "glossary", "value_synonyms",
                  "dashboard_rules", "chart_patterns", "analysis_recipes"]
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    # Where each item came from (``ai`` or ``rules``), for the audit; edited items keep their source.
    sources: list[str] = Field(default_factory=list, max_length=100)


# --------------------------------------------------------------------------- context


@dataclass
class Ctx:
    row: store.PackRow
    head: dict[str, Any]
    docs: dict[str, Any]
    catalog: list[dict[str, Any]]
    connection: dict[str, Any]
    template: store.PackRow | None
    template_docs: dict[str, Any]
    ai: bool
    provider: str | None
    samples: bool
    profiles: checks.Profiles = field(default_factory=dict)

    @property
    def template_title(self) -> str:
        return self.template.title if self.template else "no template"


async def catalog(conn: AsyncConnection, connection_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn, "SELECT qualified_name, columns, keys FROM conn.connection_objects WHERE connection_id = "
              "CAST(:c AS uuid) AND removed_at IS NULL AND readable IS NOT FALSE ORDER BY qualified_name",
        {"c": connection_id})
    if not rows:
        raise TelaError(ErrorCode.CONNECTION_NOT_READY, "Read this source's schema first (Connections: Read schema).")
    return [{**r, "keys": r["keys"] or {}} for r in rows]


async def templates(conn: AsyncConnection) -> list[store.PackRow]:
    """Packs that can be a template: released system packs first, then the tenant's released packs."""
    return [p for p in await store.all_packs(conn) if p.status == "released"]


async def template(conn: AsyncConnection, template_id: str | None) -> store.PackRow | None:
    if template_id:
        row = await store.load(conn, template_id)
        if row.status != "released":
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose a released pack as the template.")
        return row
    found = await templates(conn)
    return next((p for p in found if p.system), next(iter(found), None))


async def ai_settings(conn: AsyncConnection, request: Request) -> tuple[bool, str | None, bool]:
    """(a model is set up, its provider, values may go to it): DEC-27 sends values only when the tenant's *send
    samples* switch is on and the provider is local (Ollama)."""
    gateway: GatewayClient | None = getattr(request.app.state, "gateway", None)
    provider = await planner_provider(conn, gateway) if gateway is not None else None
    row = await fetch_one(conn, "SELECT allow_samples FROM ops.llm_settings")
    samples = bool(row and row["allow_samples"]) and provider == "ollama"
    return provider is not None, provider, samples and provider is not None


async def load(conn: AsyncConnection, request: Request, pack_id: str, connection_id: str,
               template_id: str | None = None, *, lock: bool = False) -> Ctx:
    service: QueryService = request.app.state.query_service
    row = await store.load(conn, pack_id, lock=lock)
    store.require_editable(row)
    objects = await catalog(conn, connection_id)
    try:
        connection = await service._connection(conn, connection_id)
    except TelaError as exc:
        raise TelaError(ErrorCode.CONNECTION_NOT_READY, "This data source can't be queried right now.") from exc
    tpl = await template(conn, template_id)
    ai, provider, samples = await ai_settings(conn, request)
    return Ctx(row, store.ordered(row.manifest), await store.documents(conn, row.id), objects, connection, tpl,
               await store.documents(conn, tpl.id) if tpl else {}, ai, provider, samples)


# --------------------------------------------------------------------------- the source: bind, profile, compile, count


def snapshot(ctx: Ctx, head: dict[str, Any], measures: list[dict[str, Any]] | None = None,
             dimensions: list[dict[str, Any]] | None = None) -> ModelSnapshot | None:
    """The draft's entities and links (with some measures and dimensions) bound to the connection as an install would
    bind them; None when nothing binds."""
    raw = {k: head[k] for k in ("pack_key", "pack_version", "title", "released_at", "signature", "entities", "links",
                                "calculators") if k in head}
    manifest = PackManifest.model_validate({**raw, "measures": measures or [], "dimensions": dimensions or []})
    binding = bind(manifest, match(manifest, ctx.catalog))
    if not binding.definition["entities"]:
        return None
    snap = build_snapshot(binding.definition, version_id=new_id(), connection_id=str(ctx.connection["id"]),
                          version_no=None, engine=ctx.connection["engine"])
    stamp_source_types(snap, {c["qualified_name"].lower(): c["columns"] for c in ctx.catalog})
    return snap


def profiled(head: dict[str, Any], entities: list[str] | None = None) -> list[str]:
    """``entity.field`` of the text fields whose values Tela reads for itself: not personal or confidential."""
    out = []
    for e in head.get("entities") or []:
        if entities is not None and e["key"] not in entities:
            continue
        for f in e.get("fields") or []:
            if f.get("data_type") == "text" and f.get("sensitivity", "internal") not in rules.QUIET \
                    and not PERSONAL.search(f["key"]) and f["key"] not in e.get("primary_key", []):
                out.append(f"{e['key']}.{f['key']}")
    return out[:MAX_PROFILED]


async def read_profiles(ctx: Ctx, service: QueryService, principal: Principal, request_id: str | None,
                        head: dict[str, Any], refs: list[str]) -> None:
    """The most frequent values of some text fields over every row (US-199's trial read), into ``ctx.profiles``."""
    refs = [r for r in refs if r not in ctx.profiles]
    if not refs:
        return
    try:
        snap = snapshot(ctx, head)
    except (ValueError, ValidationError) as exc:
        log.info("pack_author.profile_bind_failed", error=str(exc)[:200])
        snap = None
    if snap is None:
        ctx.profiles.update(dict.fromkeys(refs))
        return
    read, _ = await service.trial(snapshot=snap, connection=ctx.connection, principal=principal,
                                  ctx=QueryContext(surface="precheck", request_id=request_id, use_cache=False),
                                  fields=refs, measures=False, top=guards.TOP_VALUES)
    ctx.profiles.update(read)


def values_of(ctx: Ctx, entity: str) -> dict[str, list[str]]:
    """Field key → its profile's values (non-blank, not free text), for one entity."""
    out = {}
    for ref, profile in ctx.profiles.items():
        ek, _, fk = ref.partition(".")
        if ek == entity and profile is not None and len(profile) < guards.TOP_VALUES:
            out[fk] = [str(v["value"]) for v in profile if v.get("value") not in (None, "")]
    return out


def compile_error(snap: ModelSnapshot, connection: dict[str, Any], key: str) -> str | None:
    spec = QuerySpec.model_validate({"model_version_id": snap.model_version_id, "connection_id": snap.connection_id,
                                     "measure": {"ref": f"measure:{key}"}})
    try:
        as_of = reporting_date(snap.settings, None)
        compile_spec(connection, pin_dates(SpecValidator(snap).validate(spec), as_of=as_of, today=today_in(None)),
                     as_of=as_of)
    except TelaError as exc:
        return str(exc.message)
    except ValueError as exc:
        return str(exc).splitlines()[0]
    return None


@dataclass
class Checked:
    item: dict[str, Any]
    report: Report
    source: str = "rules"
    reason: str = ""
    count: Any = None
    count_of: str | None = None


async def source_checks(ctx: Ctx, service: QueryService, principal: Principal, request_id: str | None, kind: str,
                        head: dict[str, Any], batch: list[Checked], *, counts: bool) -> None:
    """Compile, count and sanity-check the batch's valid items against the source."""
    good = [c for c in batch if not c.report.errors]
    if not good:
        return
    qctx = QueryContext(surface="precheck", request_id=request_id, use_cache=False)
    if kind == "measure":
        for c in good:
            try:
                snap = snapshot(ctx, head, [c.item])
            except (ValueError, ValidationError) as exc:
                c.report.error("expression", f"The measure doesn't bind on this source: {str(exc).splitlines()[0]}")
                continue
            if snap is None or not snap.measures:
                c.report.error("expression", "The measure reads fields this source doesn't have.")
            elif (msg := compile_error(snap, ctx.connection, c.item["key"])) is not None:
                c.report.error("expression", f"The rule doesn't compile: {msg}")
        good = [c for c in good if not c.report.errors]
        if not counts or not good:
            return
        snap = snapshot(ctx, head, [c.item for c in good])
        assert snap is not None
        _, figures = await service.trial(snapshot=snap, connection=ctx.connection, principal=principal, ctx=qctx,
                                         fields=[], measures=True)
        by_ref: dict[str, list[str]] = {}
        for w in guards.measure_warnings([c.item for c in good], figures):
            for key in w.get("measures") or [w["ref"].partition(":")[2]]:
                by_ref.setdefault(key, []).append(w["issue"])
        for c in good:
            c.count, c.count_of = figures.get(c.item["key"]), "value"
            if c.count is None:
                c.report.warn("expression", "The source could not compute it on a trial run.")
            for issue in by_ref.get(c.item["key"], []):
                c.report.warn("expression", issue)
    elif kind == "entity" and counts:
        probe = [{"key": f"_rows_{c.item['key']}"[:60], "label": "Rows", "entity": c.item["key"],
                  "expression": {"agg": "count"}, "plain_rule": "Rows."} for c in good]
        try:
            snap = snapshot(ctx, checks.with_items(head, "entity", [c.item for c in good]), probe)
        except (ValueError, ValidationError) as exc:
            for c in good:
                c.report.error("fields", f"The entity doesn't bind on this source: {str(exc).splitlines()[0]}")
            return
        if snap is None:
            return
        _, figures = await service.trial(snapshot=snap, connection=ctx.connection, principal=principal, ctx=qctx,
                                         fields=[], measures=True)
        for c, p in zip(good, probe, strict=True):
            c.count, c.count_of = figures.get(p["key"]), "rows"
    elif kind == "link":
        for c in good:
            await _cardinality(ctx, service, principal, head, c)
    elif kind == "dimension":
        for c in good:
            d = c.item
            profile = ctx.profiles.get(f"{d['entity']}.{d['field']}")
            if not d.get("of") and profile is not None and len(profile) < guards.TOP_VALUES:
                c.count, c.count_of = len(profile), "values"
            elif d.get("bands"):
                c.count, c.count_of = len(d["bands"]), "bands"


async def _cardinality(ctx: Ctx, service: QueryService, principal: Principal, head: dict[str, Any],
                       c: Checked) -> None:
    """A many-to-one link's "one" side key must be unique: two counts at the source (AG-04's link check)."""
    link = c.item
    ent = next((e for e in head.get("entities") or [] if e["key"] == link["to_entity"]), None)
    f = checks.field_of(head, link["to_entity"], link["to_fields"][0]) if ent else None
    obj = next((o for o in ctx.catalog if ent and any(n.lower() in (o["qualified_name"].lower(),
                                                                   rules.table_name(o["qualified_name"]).lower())
                                                      for n in ent.get("object_candidates") or [])), None)
    if f is None or obj is None or len(link["to_fields"]) != 1:
        return
    column = next((col["name"] for col in obj["columns"] if col["name"].lower() in
                   {x.lower() for x in f.get("columns") or []}), None)
    if column is None:
        return
    try:
        unique = await service.column_is_unique(principal=principal, connection_id=str(ctx.connection["id"]),
                                                source_object=obj["qualified_name"], column=column)
    except TelaError:
        c.report.warn("to_fields", "The key could not be checked on this source.")
        return
    if unique:
        c.reason = (c.reason + " " if c.reason else "") + f"{link['to_entity']}.{f['label']} is unique (two counts)."
    else:
        c.report.warn("to_fields", f"{link['to_entity']}.{link['to_fields'][0]} is not unique, so this link may "
                                   "multiply rows. Check it is many-to-one.")


def _merge_entity(item: dict[str, Any], note: llm.TableNote) -> dict[str, Any]:
    """The model's labels, descriptions and tags over a rules entity. Names, types and keys stay the catalogue's; a
    sensitivity is never lowered."""
    out: dict[str, Any] = json.loads(json.dumps(item))
    if note.label:
        out["label"] = note.label.strip()[:80]
    if note.grain:
        out["grain"] = note.grain.strip()[:120]
    if note.synonyms:
        out["synonyms"] = [s.strip() for s in note.synonyms if s.strip()][:6]
    by_col = {n.column.lower(): n for n in note.fields}
    for f in out["fields"]:
        n = by_col.get(f["columns"][0].lower())
        if n is None:
            continue
        if n.label:
            f["label"] = n.label.strip()[:80]
        if n.description:
            f["description"] = n.description.strip()[:300]
        if n.semantic_type and n.semantic_type.lower() not in ("null", "none", ""):
            f["semantic_type"] = n.semantic_type.strip().lower()[:40]
        if n.sensitivity in SENSITIVITY_RANK and SENSITIVITY_RANK[n.sensitivity] > SENSITIVITY_RANK.get(
                f.get("sensitivity", "internal"), 1):
            f["sensitivity"] = n.sensitivity
        if n.synonyms:
            f["synonyms"] = [s.strip() for s in n.synonyms if s.strip()][:6]
    return out


# --------------------------------------------------------------------------- the stages


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _title(kind: str, item: dict[str, Any]) -> str:
    if kind == "link":
        return f"{item.get('from_entity')} → {item.get('to_entity')}"
    if kind == "synonyms":
        return str(item.get("label") or item.get("target"))
    if kind in KNOWLEDGE_KINDS:
        return knowledge.item_title(kind, item) or str(item.get("words") or "")
    return str(item.get("label") or item.get("key") or "")


def _key(kind: str, item: dict[str, Any]) -> str:
    if kind == "link":
        return f"{item.get('from_entity')}.{','.join(item.get('from_fields') or [])}->{item.get('to_entity')}"
    if kind == "synonyms":
        return str(item.get("target"))
    return str(item.get("key") or item.get("id") or item.get("term") or item.get("value") or "")


class Run:
    """One stage's run: proposals are checked one after another on the draft plus the ones before them, so two
    proposals with one key, or a measure named twice, can't both pass."""

    def __init__(self, ctx: Ctx, kind: str) -> None:
        self.ctx, self.kind = ctx, kind
        self.head = ctx.head
        self.docs = json.loads(json.dumps(ctx.docs, default=str))
        self.n = 0
        self.proposed = self.dropped = 0
        self.sources: set[str] = set()

    def check(self, raw: list[dict[str, Any]]) -> list[Checked]:
        out = []
        for r in raw:
            item, report = checks.check(self.kind, r["item"], self.head, self.docs, self.ctx.catalog,
                                        self.ctx.profiles)
            c = Checked(item, report, r.get("source", "rules"), r.get("reason") or "")
            if not report.errors:
                if self.kind in STAGE_KIND.values():
                    self.head = checks.with_items(self.head, self.kind, [item])
                else:
                    self.docs[self.kind] = checks.with_knowledge(self.docs.get(self.kind), self.kind, [item])
            out.append(c)
        return out

    def events(self, batch: list[Checked]) -> list[str]:
        out = []
        for c in batch:
            self.n += 1
            body = {"id": f"{self.kind}:{self.n}", "kind": self.kind, "key": _key(self.kind, c.item),
                    "title": _title(self.kind, c.item), "item": c.item, "source": c.source, "reason": c.reason,
                    "checks": {"errors": c.report.errors, "warnings": c.report.warnings, "count": c.count,
                               "count_of": c.count_of}}
            if c.report.errors:
                self.dropped += 1
                out.append(_sse("dropped", body))
            else:
                self.proposed += 1
                self.sources.add(c.source)
                out.append(_sse("proposal", body))
        return out


async def _ask[T: BaseModel](request: Request, principal: Principal, ctx: Ctx, model: type[T], user: str) -> T:
    gateway: GatewayClient | None = getattr(request.app.state, "gateway", None)
    return await run_agent(gateway, model, tenant_id=principal.tenant_id, user_id=principal.user_id,
                           agent=llm.AGENT, task=llm.TASK, system=llm.system(ctx.samples), user=user)


async def _record_values(ctx: Ctx, sent: dict[str, list[str]]) -> None:
    """DEC-27: the values sent to the model are kept in the run log (Admin only, 14 days)."""
    if sent:
        await trace.step("retrieve", f"Sent frequent values of {len(sent)} text column(s) to the local model",
                         agent=llm.AGENT, detail={"values_sent": sent})


async def _entities(request: Request, principal: Principal, ctx: Ctx, body: AuthorIn,
                    run: Run) -> AsyncIterator[str]:
    service: QueryService = request.app.state.query_service
    have = rules.objects_in(ctx.head, ctx.catalog)
    wanted = {t.lower() for t in body.tables}
    objects = [o for o in ctx.catalog if o["qualified_name"] not in have
               and (not wanted or o["qualified_name"].lower() in wanted)]
    if not objects:
        yield _sse("note", {"message": "Every table of this source is in the draft already." if not wanted else
                            "Those tables are in the draft already, or not on this source."})
        return
    if len(objects) > MAX_TABLES:
        yield _sse("note", {"message": f"{len(objects)} tables: drafting the first {MAX_TABLES}. Draft again for "
                                       "the rest."})
        objects = objects[:MAX_TABLES]
    taken = {e["key"] for e in ctx.head.get("entities") or []}
    drafted = rules.entity_proposals(objects, taken)
    by_table = {d["item"]["object_candidates"][0].lower(): d for d in drafted}
    await trace.step("rule", f"Rules drafted {len(drafted)} entit(ies)", agent=llm.AGENT)
    if ctx.ai:
        template = llm.examples("entity", ctx.template.manifest if ctx.template else None, None)
        for i in range(0, len(objects), TABLE_BATCH):
            part = objects[i:i + TABLE_BATCH]
            samples = await _table_samples(ctx, service, principal, request, part) if ctx.samples else {}
            await _record_values(ctx, {f"{t}.{c}": v for t, cols in samples.items() for c, v in cols.items()})
            items = [by_table[rules.table_name(o["qualified_name"]).lower()]["item"] for o in part]
            try:
                reply = await _ask(request, principal, ctx, llm.EntitiesReply,
                                   llm.entities_prompt(part, items, ctx.template_title, template, samples))
            except AgentFailed as exc:
                yield _sse("note", {"message": f"The AI model gave no usable answer for {len(part)} table(s) "
                                               f"({exc.code}): the rules draft stands."})
                continue
            for note in reply.tables:
                d = by_table.get(rules.table_name(note.table).lower())
                if d is not None:
                    d.update(item=_merge_entity(d["item"], note), source="ai")
    batch = run.check(drafted)
    await source_checks(ctx, service, principal, rid(request), "entity", ctx.head, batch, counts=True)
    for e in run.events(batch):
        yield e


async def _table_samples(ctx: Ctx, service: QueryService, principal: Principal, request: Request,
                         objects: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """DEC-27 on: up to ``SAMPLES`` frequent values of each non-personal text column of these tables (read through a
    throw-away binding of the rules entities)."""
    drafted = rules.entity_proposals(objects, set())
    head = checks.with_items({**ctx.head, "entities": []}, "entity", [d["item"] for d in drafted])
    refs = profiled(head)
    temp = Ctx(**{**ctx.__dict__, "profiles": {}})
    await read_profiles(temp, service, principal, rid(request), head, refs)
    out: dict[str, dict[str, list[str]]] = {}
    for o, d in zip(objects, drafted, strict=True):
        for f, vals in values_of(temp, d["item"]["key"]).items():
            col = next(x["columns"][0] for x in d["item"]["fields"] if x["key"] == f)
            out.setdefault(o["qualified_name"], {})[col] = vals[:llm.SAMPLES]
    return out


async def _links(request: Request, principal: Principal, ctx: Ctx, body: AuthorIn, run: Run) -> AsyncIterator[str]:
    service: QueryService = request.app.state.query_service
    if len(ctx.head.get("entities") or []) < 2:
        yield _sse("note", {"message": "Accept at least two entities first."})
        return
    drafted = rules.link_proposals(ctx.head, ctx.catalog)
    await trace.step("rule", f"Rules drafted {len(drafted)} link(s)", agent=llm.AGENT)
    if not drafted:
        yield _sse("note", {"message": "No foreign keys or key names link the draft's entities."})
    batch = run.check(drafted)
    await source_checks(ctx, service, principal, rid(request), "link", ctx.head, batch, counts=True)
    for e in run.events(batch):
        yield e


def _entities_for(ctx: Ctx, body: AuthorIn) -> list[dict[str, Any]]:
    ents = ctx.head.get("entities") or []
    return [e for e in ents if not body.entities or e["key"] in body.entities]


def _linked(head: dict[str, Any], ent: dict[str, Any]) -> list[dict[str, Any]]:
    keys = {ln["to_entity"] for ln in head.get("links") or [] if ln.get("from_entity") == ent["key"]} | \
           {ln["from_entity"] for ln in head.get("links") or [] if ln.get("to_entity") == ent["key"]}
    return [e for e in head.get("entities") or [] if e["key"] in keys]


async def _per_entity(request: Request, principal: Principal, ctx: Ctx, body: AuthorIn, run: Run,
                      kind: str) -> AsyncIterator[str]:
    """Measures or dimensions, one entity at a time (at most ``BATCH`` each)."""
    service: QueryService = request.app.state.query_service
    ents = _entities_for(ctx, body)
    if not ents:
        yield _sse("note", {"message": "Accept entities first."})
        return
    section = "measures" if kind == "measure" else "dimensions"
    await read_profiles(ctx, service, principal, rid(request), ctx.head, profiled(ctx.head, [e["key"] for e in ents]))
    template = llm.examples(kind, ctx.template.manifest if ctx.template else None, None)
    for ent in ents:
        taken = {x["key"] for x in run.head.get(section) or []}
        values = values_of(ctx, ent["key"])
        drafted = (rules.measure_proposals if kind == "measure" else rules.dimension_proposals)(
            ent, ctx.profiles, taken)
        await trace.step("rule", f"Rules drafted {len(drafted)} {section} for {ent['key']}", agent=llm.AGENT)
        if ctx.ai:
            masker = llm.Masker({} if ctx.samples else values)
            shown = [masker.mask({**d["item"], "reason": d["reason"]}) for d in drafted]
            have = sorted(taken)
            if ctx.samples:
                await _record_values(ctx, {f"{ent['key']}.{k}": v[:llm.SAMPLES] for k, v in values.items()})
            sample_values = {k: v[:llm.SAMPLES] for k, v in values.items()} if ctx.samples else {}
            user = llm.measures_prompt(ent, _linked(run.head, ent), shown, ctx.template_title, template, have,
                                       sample_values) if kind == "measure" else \
                llm.dimensions_prompt(ent, shown, ctx.template_title, template, have, sample_values)
            try:
                if kind == "measure":
                    reply_m = await _ask(request, principal, ctx, llm.MeasuresReply, user)
                    raw = [masker.unmask(m.model_dump(exclude_none=True)) for m in reply_m.measures]
                else:
                    reply_d = await _ask(request, principal, ctx, llm.DimensionsReply, user)
                    raw = [masker.unmask(d.model_dump(exclude_none=True)) for d in reply_d.dimensions]
                drafted = [{"item": _clean(kind, {**r, "entity": ent["key"]}), "reason": r.get("reason") or "",
                            "source": "ai"} for r in raw]
            except AgentFailed as exc:
                yield _sse("note", {"message": f"The AI model gave no usable answer for {ent['label']} "
                                               f"({exc.code}): the rules draft stands."})
        start = run.head
        batch = run.check(drafted)
        await source_checks(ctx, service, principal, rid(request), kind, start, batch, counts=True)
        for e in run.events(batch):
            yield e


def _clean(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    """A model item without the reply-only keys and empty defaults."""
    out: dict[str, Any] = {k: v for k, v in item.items() if k != "reason" and v not in (None, [], {})}
    if kind == "dimension":
        for flag in ("is_temporal", "is_status"):
            if out.get(flag) is False:
                out.pop(flag)
    return out


async def _synonyms(request: Request, principal: Principal, ctx: Ctx, body: AuthorIn,
                    run: Run) -> AsyncIterator[str]:
    drafted = rules.synonym_proposals(ctx.head)
    await trace.step("rule", f"Rules drafted synonyms for {len(drafted)} item(s)", agent=llm.AGENT)
    if ctx.ai:
        template = llm.examples("synonyms", ctx.template.manifest if ctx.template else None, None)
        try:
            reply = await _ask(request, principal, ctx, llm.SynonymsReply,
                               llm.synonyms_prompt(ctx.head, [d["item"] for d in drafted], ctx.template_title,
                                                   template))
            labels = {f"{w}:{x['key']}": x["label"] for w, s in (("measure", "measures"), ("dimension", "dimensions"),
                                                               ("entity", "entities")) for x in ctx.head.get(s) or []}
            drafted = [{"item": {"target": i.target, "label": labels.get(i.target, i.target),
                                 "synonyms": [w for w in dict.fromkeys(s.strip() for s in i.synonyms if s.strip())
                                              if w.lower() != str(labels.get(i.target, "")).lower()]},
                        "source": "ai", "reason": "Words people use for it."} for i in reply.items]
        except AgentFailed as exc:
            yield _sse("note", {"message": f"The AI model gave no usable answer ({exc.code}): the rules draft stands."})
    if not drafted:
        yield _sse("note", {"message": "Nothing to propose: accept measures first, or every one has synonyms."})
    for e in run.events(run.check(drafted)):
        yield e


async def _knowledge(request: Request, principal: Principal, ctx: Ctx, body: AuthorIn,
                     run: Run) -> AsyncIterator[str]:
    service: QueryService = request.app.state.query_service
    kind = run.kind
    if not ctx.head.get("measures"):
        yield _sse("note", {"message": "Accept measures first: knowledge is written about them."})
        return
    dims = ctx.head.get("dimensions") or []
    refs = [f"{d['entity']}.{d['field']}" for d in dims if not d.get("of")]
    await read_profiles(ctx, service, principal, rid(request), ctx.head, [r for r in refs if r in profiled(ctx.head)])
    if kind == "glossary":
        existing = {str(t.get("term", "")).lower() for t in (ctx.docs.get("glossary") or {}).get("terms") or []}
        drafted = rules.glossary_proposals(ctx.head, existing)
    else:
        drafted = []
    await trace.step("rule", f"Rules drafted {len(drafted)} {kind} item(s)", agent=llm.AGENT)
    if ctx.ai:
        dim_values: dict[str, list[str]] = {}
        if ctx.samples:
            for d in dims:
                vals = values_of(ctx, d["entity"]).get(d["field"])
                if vals and not d.get("of"):
                    dim_values[d["key"]] = vals[:llm.SAMPLES]
            await _record_values(ctx, dim_values)
        template = llm.examples(kind, None, ctx.template_docs)
        try:
            reply = await _ask(request, principal, ctx, llm.KnowledgeReply,
                               llm.knowledge_prompt(kind, ctx.head, [d["item"] for d in drafted], ctx.template_title,
                                                    template, dim_values))
            drafted = [{"item": item, "source": "ai",
                        "reason": reply.reasons[i] if i < len(reply.reasons) else ""}
                       for i, item in enumerate(reply.items)]
        except AgentFailed as exc:
            yield _sse("note", {"message": f"The AI model gave no usable answer ({exc.code}): the rules draft stands."})
    elif kind != "glossary":
        yield _sse("note", {"message": "Rules can't draft this kind. Set up an AI model under AI settings, or write "
                                       "the items on the Knowledge tab."})
    if not drafted and ctx.ai:
        yield _sse("note", {"message": "Nothing to propose."})
    for e in run.events(run.check(drafted)):
        yield e


STAGE_RUNNERS = {"entities": _entities, "links": _links, "synonyms": _synonyms, "knowledge": _knowledge}


@router.post("/{pack_id}/author")
async def author(pack_id: str, body: AuthorIn, request: Request, principal: Principal = AUTHOR) -> StreamingResponse:
    """SSE: ``stage``, ``proposal`` / ``dropped`` (each with its checks), ``note``, ``complete``, ``error``. Nothing
    is written to the draft."""
    kind = STAGE_KIND.get(body.stage) or body.kind or ""
    if body.stage == "knowledge" and kind not in KNOWLEDGE_KINDS:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Choose a knowledge kind: {', '.join(KNOWLEDGE_KINDS)}.")
    async with tenant_tx(principal.tenant_id) as conn:
        ctx = await load(conn, request, pack_id, body.connection_id, body.template_pack_id)

    async def events() -> AsyncIterator[str]:
        async with trace.agent_run(principal.tenant_id, principal.user_id, "model_assist",
                                   title=f"Pack author: {body.stage}" + (f" ({kind})" if body.stage == "knowledge"
                                                                         else ""),
                                   object_ref=f"pack:{pack_id}", agent=llm.AGENT):
            run = Run(ctx, kind)
            yield _sse("stage", {"stage": body.stage, "kind": kind, "ai": ctx.ai, "provider": ctx.provider,
                                 "samples": ctx.samples, "template": _template_out(ctx.template)})
            try:
                if body.stage in ("measures", "dimensions"):
                    gen = _per_entity(request, principal, ctx, body, run, kind)
                else:
                    gen = STAGE_RUNNERS[body.stage](request, principal, ctx, body, run)
                async for e in gen:
                    yield e
                await trace.step("validate", f"{run.proposed} proposal(s), {run.dropped} left out", agent=llm.AGENT,
                                 detail={"stage": body.stage, "kind": kind, "proposed": run.proposed,
                                         "dropped": run.dropped})
                async with tenant_tx(principal.tenant_id) as conn:
                    await audit.record(conn, tenant_id=principal.tenant_id, action="pack.ai_drafted",
                                       object_type="pack", object_id=pack_id, outcome="success",
                                       actor_id=principal.user_id, correlation_id=rid(request),
                                       details={"stage": body.stage, "kind": kind, "connection": body.connection_id,
                                                "proposed": run.proposed, "dropped": run.dropped,
                                                "sources": sorted(run.sources), "samples": ctx.samples})
                yield _sse("complete", {"stage": body.stage, "kind": kind, "proposed": run.proposed,
                                        "dropped": run.dropped, "sources": sorted(run.sources)})
            except TelaError as exc:
                yield _sse("error", {"code": exc.code, "message": exc.message})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _template_out(t: store.PackRow | None) -> dict[str, Any] | None:
    return {"id": t.id, "pack_key": t.pack_key, "version": t.pack_version, "title": t.title} if t else None


# --------------------------------------------------------------------------- overview, check, accept


@router.get("/{pack_id}/author")
async def author_overview(pack_id: str, connection_id: str, request: Request,
                          principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        objects = await catalog(conn, connection_id)
        ai, provider, samples = await ai_settings(conn, request)
        found = await templates(conn)
        default = await template(conn, None)
        docs = await store.documents(conn, row.id)
    head = row.manifest
    have = rules.objects_in(head, objects)
    kinds = {k: len((docs.get(k) or {}).get(knowledge.LISTS[k]) or []) for k in KNOWLEDGE_KINDS}
    return {"pack": store.summary(row), "ai": ai, "provider": provider, "samples": samples,
            "templates": [_template_out(t) for t in found], "template_id": default.id if default else None,
            "tables": [{"qualified_name": o["qualified_name"], "columns": len(o["columns"]),
                        "in_draft": o["qualified_name"] in have} for o in objects],
            "stages": [{"stage": s, "count": len(head.get(s) or []) if s in ("entities", "links", "measures",
                                                                              "dimensions") else None}
                       for s in STAGES],
            "knowledge": [{"kind": k, "count": n} for k, n in kinds.items()]}


async def _check_items(request: Request, principal: Principal, ctx: Ctx, body: ItemsIn, *,
                       counts: bool) -> list[Checked]:
    service: QueryService = request.app.state.query_service
    head = ctx.head
    if body.kind == "entity":
        refs: list[str] = []
    elif body.kind in ("measure", "dimension"):
        ents = {str(i.get("entity")) for i in body.items}
        refs = profiled(head, sorted(ents))
    else:
        refs = profiled(head)
    await read_profiles(ctx, service, principal, rid(request), head, refs)
    run = Run(ctx, body.kind)
    batch = run.check([{"item": i, "source": body.sources[n] if n < len(body.sources) else "ai"}
                       for n, i in enumerate(body.items)])
    await source_checks(ctx, service, principal, rid(request), body.kind, head, batch, counts=counts)
    return batch


def _out(batch: list[Checked]) -> list[dict[str, Any]]:
    return [{"index": i, "item": c.item, "errors": c.report.errors, "warnings": c.report.warnings, "count": c.count,
             "count_of": c.count_of} for i, c in enumerate(batch)]


@router.post("/{pack_id}/author/check")
async def check_items(pack_id: str, body: ItemsIn, request: Request, principal: Principal = AUTHOR) -> dict[str, Any]:
    """The proposal checks on items the Admin edited; nothing is written."""
    async with tenant_tx(principal.tenant_id) as conn:
        ctx = await load(conn, request, pack_id, body.connection_id)
    batch = await _check_items(request, principal, ctx, body, counts=True)
    return {"valid": not any(c.report.errors for c in batch), "items": _out(batch)}


@router.post("/{pack_id}/author/accept")
async def accept(pack_id: str, body: ItemsIn, request: Request, response: Response,
                 if_match: str | None = Header(default=None), principal: Principal = AUTHOR) -> dict[str, Any]:
    """Add accepted items to the draft after checking them again (a proposal is never trusted as sent)."""
    manifest_kind = body.kind in STAGE_KIND.values()
    async with tenant_tx(principal.tenant_id) as conn:
        ctx = await load(conn, request, pack_id, body.connection_id)
        if manifest_kind:
            store.require_revision(ctx.row, if_match)
    batch = await _check_items(request, principal, ctx, body, counts=False)
    bad = [c for c in batch if c.report.errors]
    if bad:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"{len(bad)} of the items don't pass their checks.",
                        {"items": [i for i in _out(batch) if i["errors"]]})
    items = [c.item for c in batch]
    sources = {s: body.sources.count(s) for s in set(body.sources)}
    async with tenant_tx(principal.tenant_id) as conn:
        if manifest_kind:
            row = await store.load(conn, pack_id, lock=True)
            store.require_editable(row)
            store.require_revision(row, if_match)
            head = checks.with_items(store.ordered(row.manifest), body.kind, items)
            report = validate(head, await store.documents(conn, row.id))
            if report.errors:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "The draft with these items doesn't pass its checks.",
                                report.out())
            await store.save_manifest(conn, row, head)
            checks_out = report.out()
        else:
            row = await knowledge.pack_for(conn, pack_id, write=True)
            current, _ = await knowledge.read_document(conn, row.id, body.kind)
            doc = checks.with_knowledge(current, body.kind, items)
            result = knowledge.check_document(body.kind, doc, row.manifest)
            if result["errors"]:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "The document with these items doesn't pass its checks.",
                                {k: result[k] for k in ("errors", "warnings")})
            await knowledge.write_document(conn, row, body.kind, doc)
            checks_out = {k: result[k] for k in ("valid", "errors", "warnings")}
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.ai_accepted", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "kind": body.kind,
                                    "items": len(items), "keys": [_key(body.kind, i) for i in items][:50],
                                    "sources": sources})
        saved = await store.load(conn, row.id)
    response.headers["ETag"] = saved.etag
    return {**store.summary(saved), "manifest": store.ordered(saved.manifest), "checks": checks_out,
            "accepted": len(items)}
