"""Free-hand answer → widget → Admin approval → certified widget and measure (ENH-008 US-166; FEAT-060 / EPIC-08).

* **Add as widget** on a free-hand answer keeps the rule as a personal definition of kind ``freehand`` (the ad-hoc
  measure, the question it came from, its breakdowns) and creates a widget in **Pending certification**: in its
  owner's library only, never on a report, shared or exported (``content.common.is_freehand`` guards those paths).
  A certification request is raised at once.
* The Admin reviews it in the certification queue: the question, the rule in words, the compiled preview, the number
  with a sample of records, and look-alikes (a certified measure with a similar name, the same rule, or the same
  number).
* **Approve** (D2) needs a name, key, definition and plain rule, and must not take a certified measure's name or key
  (D1). The rule becomes a certified measure through a model-version change: the measure is added to the draft and
  the draft approved (``model.approve``) — refused while the draft holds other unapproved changes. The question is
  kept as a synonym; every widget of the item switches to the new measure and is certified.
* **Ask for changes** sends a note back; **Reject** needs a reason and may point to an existing measure. Either way
  the widget stays out of reports as *Exploratory*. The owner is notified of every decision.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.ask.planner import STOP
from tela_api.content.common import def_hash
from tela_api.core.db import fetch_all, fetch_one
from tela_api.governance.lookalike import differs
from tela_api.identity.principal import Principal
from tela_api.model.change import apply_change
from tela_api.query.service import QueryContext, QueryService
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.expr import condition_fields
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import AdhocMeasure

KIND = "freehand"
FORMATS = {"count": "integer", "count_distinct": "integer", "sum": "decimal", "avg": "decimal", "min": "decimal",
           "max": "decimal"}


class MeasureIn(BaseModel):
    """What the Admin names the certified measure (Approve)."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    label: str = Field(min_length=2, max_length=120)
    definition: str = Field(min_length=3, max_length=300)
    plain_rule: str = Field(min_length=3, max_length=500)


# --------------------------------------------------------------------------- holding a rule (Add as widget)


async def hold(conn: AsyncConnection, principal: Principal, *, spec: dict[str, Any], question: str,
               connection_id: str, model_version_id: str) -> str:
    """Keep a free-hand rule as the owner's ``freehand`` definition; returns its id."""
    adhoc = AdhocMeasure.model_validate(spec["measure"]["adhoc"]).model_dump(mode="json", exclude_none=True)
    definition = {"adhoc": adhoc, "question": question[:500], "breakdowns": spec.get("breakdowns", []),
                  "filters": spec.get("filters", [])}
    h = def_hash(definition)
    same = await fetch_one(conn, "SELECT id FROM content.personal_fields WHERE owner_id = :u AND kind = 'freehand' "
                                 "AND definition_hash = :h AND model_version_id = :v AND deleted_at IS NULL",
                           {"u": principal.user_id, "h": h, "v": model_version_id})
    if same is not None:
        return str(same["id"])
    label = adhoc["label"][:110]
    taken = {r["l"] for r in await fetch_all(conn, "SELECT lower(label) AS l FROM content.personal_fields WHERE "
                                                   "owner_id = :u AND deleted_at IS NULL", {"u": principal.user_id})}
    n = 2
    base = label
    while label.lower() in taken:
        label, n = f"{base} ({n})", n + 1
    fid = new_id()
    await conn.execute(
        text("INSERT INTO content.personal_fields (id, tenant_id, owner_id, connection_id, model_version_id, kind, "
             "key, label, definition, definition_hash, plain_rule) VALUES (:id, :t, :u, :c, :v, 'freehand', :k, :l, "
             "CAST(:d AS jsonb), :h, :r)"),
        {"id": fid, "t": principal.tenant_id, "u": principal.user_id, "c": connection_id, "v": model_version_id,
         "k": f"fh_{fid.replace('-', '')[:12]}", "l": label, "d": json.dumps(definition), "h": h,
         "r": adhoc["plain_rule"]})
    return str(fid)


def item_spec(item: dict[str, Any]) -> dict[str, Any]:
    d: dict[str, Any] = item["definition"]
    return {"model_version_id": str(item["model_version_id"]), "connection_id": str(item["connection_id"]),
            "measure": {"adhoc": d["adhoc"]}, "breakdowns": d.get("breakdowns", []), "filters": d.get("filters", [])}


# --------------------------------------------------------------------------- review (queue detail)


async def preview(service: QueryService, principal: Principal, item: dict[str, Any], request_id: str | None,
                  ) -> tuple[dict[str, Any], str | None]:
    """The number across the reviewer's scope, a sample of records and the compiled query."""
    spec = {**item_spec(item), "breakdowns": []}
    ctx = QueryContext(surface="certification_preview", request_id=request_id)
    res = await service.execute(spec, principal, ctx)
    out: dict[str, Any] = {k: res[k] for k in ("columns", "row_count", "truncated", "as_of")} | {"rows": res["rows"]}
    try:
        sample = await service.records(spec, principal, ctx, marks={}, limit=10, cap=10)
        out["sample"] = {k: sample[k] for k in ("columns", "rows", "total")}
    except TelaError as exc:
        out["sample"] = {"error": exc.message}
    generated = (await service.explain(spec, principal, ctx)).get("sql")
    return out, generated


async def _value(service: QueryService, principal: Principal, spec: dict[str, Any]) -> float | None:
    try:
        res = await service.execute(spec, principal, QueryContext(surface="certification_preview"))
    except TelaError:
        return None
    if res.get("hidden"):
        return None
    if not res["rows"]:
        return 0.0
    v = res["rows"][0][-1]
    return float(v) if isinstance(v, int | float) else None


def _words(s: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


async def lookalikes(service: QueryService, principal: Principal, item: dict[str, Any], snap: ModelSnapshot,
                     ) -> list[dict[str, Any]]:
    """Certified measures that look like the rule (D1): a similar name or synonym, the same fields in the rule, or the
    same number. The finding says how the certified rule differs."""
    from tela_api.governance.lookalike import describe
    from tela_query_spec.expr import AggExpr

    adhoc = AdhocMeasure.model_validate(item["definition"]["adhoc"])
    name = _words(f"{adhoc.label} {item['definition'].get('question', '')}")
    fields = condition_fields(adhoc.where)
    base = {"model_version_id": str(item["model_version_id"]), "connection_id": str(item["connection_id"])}
    mine = await _value(service, principal, {**base, "measure": {"adhoc": adhoc.model_dump(mode="json")}})
    ent = snap.entity(adhoc.entity)
    out = []
    for m in snap.measures:
        if m.status != "certified" or m.entity != adhoc.entity:
            continue
        reasons = []
        names = [m.label, *m.synonyms]
        if any(n.lower().strip() == adhoc.label.lower().strip() for n in names):
            reasons.append("same name")
        elif any(len(_words(n)) >= 1 and _words(n) <= name for n in names):
            reasons.append("similar name")
        where = m.expression.where if isinstance(m.expression, AggExpr) else None
        theirs = condition_fields(where)
        if fields and theirs and fields == theirs:
            reasons.append("same fields in the rule")
        if not reasons:
            continue
        value = await _value(service, principal, {**base, "measure": {"ref": f"measure:{m.key}"}})
        if value is not None and mine is not None and not differs(value, mine):
            reasons.append("same number")
        summary = (f"{m.label} counts where {describe(where, ent)}." if where is not None else
                   f"{m.label}: {m.plain_rule}")
        out.append({"ref": f"measure:{m.key}", "label": m.label, "kind": "measure", "reasons": reasons,
                    "similarity": round(min(1.0, 0.6 + 0.15 * len(reasons)), 2), "difference": summary,
                    "result_delta": {"item": mine, "certified": value, "differs": differs(mine, value)}})
    out.sort(key=lambda f: -f["similarity"])
    return out[:5]


# --------------------------------------------------------------------------- decisions


def name_clash(snap_definition: dict[str, Any], body: MeasureIn) -> str | None:
    """A certified measure already has this key, name or synonym (D1: never under the same name)."""
    for m in snap_definition.get("measures", []):
        if m["key"] == body.key:
            return f"The key {body.key} is taken by {m['label']}. Choose another key."
        if m["label"].strip().lower() == body.label.strip().lower() or body.label.strip().lower() in (
                s.lower() for s in m.get("synonyms", [])):
            return f"{m['label']} already has this name. Certify the rule under a different name, or reject it in " \
                   "favour of the existing measure."
    return None


def certified_measure(item: dict[str, Any], body: MeasureIn) -> dict[str, Any]:
    """The model measure a certified rule becomes; the person's question is kept as a synonym (D2)."""
    adhoc = item["definition"]["adhoc"]
    expression = {"agg": adhoc["agg"], **({"field": adhoc["field"]} if adhoc.get("field") else {}),
                  **({"where": adhoc["where"]} if adhoc.get("where") else {})}
    question = re.sub(r"\s+", " ", item["definition"].get("question", "")).strip(" ?")[:200]
    return {"key": body.key, "label": body.label.strip(), "entity": adhoc["entity"], "expression": expression,
            "plain_rule": body.plain_rule.strip(), "definition": body.definition.strip(),
            "format": FORMATS[adhoc["agg"]], "synonyms": [question] if question else [], "status": "certified",
            "rule_ref": "Certified from a free-hand question"}


async def approve(request: Request, principal: Principal, item: dict[str, Any], body: MeasureIn) -> dict[str, Any]:
    """Add the rule to the model as a certified measure and approve it as the next version. Returns the new
    version and the measure ref; on any failure the draft is put back as it was."""

    def add(definition: dict[str, Any]) -> dict[str, Any]:
        if (clash := name_clash(definition, body)) is not None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, clash, {"field": "label"})
        return {**definition, "measures": [*definition.get("measures", []), certified_measure(item, body)]}

    out = await apply_change(request, principal, str(item["connection_id"]), add,
                             note=f"Certified free-hand rule: {body.label}", what="certify this rule",
                             denied="Certifying a free-hand rule adds a measure to the model, which needs model "
                                    "approval rights.")
    return {"model_version_id": out["model_version_id"], "version": out["version"], "measure": f"measure:{body.key}"}


async def switch_widgets(conn: AsyncConnection, item: dict[str, Any], *, measure_ref: str | None,
                         model_version_id: str | None, state: str) -> list[str]:
    """Every widget of the item: certified on the new measure (Approve), or kept out of reports as exploratory."""
    changed = []
    for w in await fetch_all(conn, "SELECT * FROM content.widgets WHERE definition_refs @> CAST(:r AS jsonb) AND "
                                   "deleted_at IS NULL", {"r": json.dumps([{"id": str(item["id"])}])}):
        if measure_ref is None:
            await conn.execute(text("UPDATE content.widgets SET certification_state = :s WHERE id = :id"),
                               {"s": state, "id": w["id"]})
        else:
            spec = {k: v for k, v in w["spec"].items() if k not in ("measure", "model_version_id")}
            spec = {"model_version_id": model_version_id, **spec, "measure": {"ref": measure_ref}}
            await conn.execute(
                text("UPDATE content.widgets SET spec = CAST(:s AS jsonb), model_version_id = :v, definition_refs = "
                     "'[]', certification_state = 'certified', def_hash = :h WHERE id = :id"),
                {"s": json.dumps(spec), "v": model_version_id, "h": def_hash(spec), "id": w["id"]})
        changed.append(w["title"])
    return changed
