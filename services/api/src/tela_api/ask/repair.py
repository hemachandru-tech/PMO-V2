"""Spec repair (ENH-013, US-181; FEAT-064): AG-03's refused plans go back to it as typed errors.

A planned spec is checked before it is answered: references first (``precheck``: a measure or dimension that isn't in
the model, or that the person can't see), then the one query path's validator and compiler
(``QueryService.check``). Each refusal becomes a ``SpecError`` whose ``typed`` text names the references
("dimension:region is not reachable from measure:forecast_cost; reachable: …", "value 'KSA' not found in
dimension:region; closest: 'Saudi Arabia'") and goes back to AG-03 with its previous plan, at most ``MAX_REPAIRS``
times. Every repair reason is logged in ``rag.plan_repairs`` (kind, reference, typed text without filter values) with
whether the plan was repaired in the end; ``GET /knowledge/plan-repairs`` lists the top reasons — what context the
planner lacks.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from tela_api.ask import steps as plan_steps
from tela_api.ask.planner import PlannerOut
from tela_api.core.db import fetch_all, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_common.errors import TelaError
from tela_common.ids import new_id
from tela_query_spec.model import Dimension, Measure, ModelSnapshot

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

MAX_REPAIRS = 2
KINDS = ("unknown_measure", "unknown_dimension", "not_reachable", "value_not_found", "invalid_extra", "invalid",
         "compile")


@dataclass(frozen=True)
class SpecError:
    kind: str  # one of KINDS
    ref: str | None
    typed: str  # for AG-03: names references and, for values, the closest stored ones
    message: str  # for the person, when the repairs run out
    logged: str  # for ``rag.plan_repairs``: ``typed`` without filter values

    def public(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "error": self.typed}


def _ref(raw: str, kind: str) -> str:
    return raw if raw.startswith(f"{kind}:") else f"{kind}:{raw.split(':', 1)[-1]}"


def closest_refs(raw: str, options: dict[str, str], n: int = 3) -> list[str]:
    """The references closest to a wrong one, by key and by label (``options``: ref → label)."""
    key = raw.split(":", 1)[-1].replace("_", " ").lower()
    by_name = {**{r.split(":", 1)[1].replace("_", " ").lower(): r for r in options},
               **{label.lower(): r for r, label in options.items()}}
    hits = difflib.get_close_matches(key, list(by_name), n=n * 2, cutoff=0.4)
    return list(dict.fromkeys(by_name[h] for h in hits))[:n]


def precheck(out: PlannerOut, snap: ModelSnapshot, measures: list[Measure], dims: list[Dimension]) -> list[SpecError]:
    """References AG-03 named that the spec would otherwise drop silently: a measure or dimension not in the
    model or not visible to the person, an extra measure of another table, a derived figure of a measure not asked
    for; in a multi-step plan (US-179) the same for each step ("step 1: …"), and step references that can't run."""
    errors: list[SpecError] = []
    if out.unsupported or (out.adhoc is not None and not out.measure):
        return errors
    for n, step in enumerate(out.steps, 1):
        for e in precheck(step.model_copy(update={"steps": []}), snap, measures, dims):
            errors.append(SpecError(e.kind, e.ref, f"step {n}: {e.typed}", f"Step {n}: {e.message}",
                                    f"step {n}: {e.logged}"))
    try:
        plan_steps.lift({"filters": out.filters, "having": out.having,
                         "steps": [{"filters": s.filters, "having": s.having} for s in out.steps]})
    except plan_steps.StepError as exc:
        errors.append(SpecError("invalid", None, f"steps: {exc}", str(exc), f"steps: {exc}"))
    seen_m = {f"measure:{m.key}": m.label for m in measures}
    seen_d = {f"dimension:{d.key}": d.label for d in dims}
    if not out.measure:
        return [SpecError("unknown_measure", None, "No measure was chosen; choose one of the candidate measures.",
                          "I couldn't match that to a certified measure.", "no measure chosen")]
    main = _ref(out.measure, "measure")
    if main not in seen_m:
        near = closest_refs(main, seen_m)
        hint = f"; closest: {', '.join(near)}" if near else ""
        errors.append(SpecError("unknown_measure", main, f"{main} is not a certified measure you can use{hint}",
                                "I couldn't match that to a certified measure.",
                                f"{main} is not a certified measure"))
    root = snap.measure(main.split(":", 1)[1])
    for raw in out.extra_measures:
        ref = _ref(raw, "measure")
        m = snap.measure(ref.split(":", 1)[1])
        if ref not in seen_m:
            errors.append(SpecError("unknown_measure", ref, f"{ref} is not a certified measure you can use",
                                    "I couldn't match that to a certified measure.",
                                    f"{ref} is not a certified measure"))
        elif root is not None and m is not None and m.entity != root.entity:
            typed = f"{ref} counts {m.entity}, not {root.entity} like {main}; extra measures must count the same thing"
            errors.append(SpecError("invalid_extra", ref, typed,
                                    "Extra measures must count the same thing as the main measure.", typed))
    asked = {main, *(_ref(r, "measure") for r in out.extra_measures)}
    for d in out.derived:
        for side in ("of", "to"):
            ref = _ref(str(d.get(side, "")), "measure")
            if ref not in asked:
                typed = f"derived {d.get('op')} uses {ref}, which is not the measure or an extra measure"
                errors.append(SpecError("invalid_extra", ref, typed, "That figure can't be worked out here.", typed))
    for raw in [*out.breakdowns, *(str(f.get("ref", "")) for f in out.filters)]:
        ref = _ref(raw, "dimension")
        if ref not in seen_d:
            near = closest_refs(ref, seen_d)
            hint = f"; closest: {', '.join(near)}" if near else ""
            errors.append(SpecError("unknown_dimension", ref, f"{ref} is not a dimension you can use{hint}",
                                    "That breakdown or filter is not in the approved model.",
                                    f"{ref} is not a dimension"))
    return list({e.typed: e for e in errors}.values())


def from_refusal(exc: TelaError, spec: dict[str, Any], snap: ModelSnapshot, dims: list[Dimension],
                 closest: dict[str, str | None] | None = None, *, compiling: bool = False) -> SpecError:
    """A validator or compiler refusal as a typed error. ``closest`` maps each unknown filter value to the closest
    stored value the person may see (from the value index, else by spelling)."""
    admin = (exc.details or {}).get("admin_detail") or {}
    ref = admin.get("ref")
    main = spec["measure"]["ref"]
    if compiling:
        typed = f"the {main} plan can't be run on this source: {exc.message}"
        return SpecError("compile", ref or main, typed, exc.message, typed)
    if "root" in admin and "entity" in admin and ref and str(ref).startswith("dimension:"):
        root = admin["root"]
        reach = [f"dimension:{d.key}" for d in dims if snap.join_path(root, d.entity) is not None]
        typed = f"{ref} is not reachable from {main}"
        hint = f"; reachable: {', '.join(reach[:8])}" if reach else ""
        return SpecError("not_reachable", ref, typed + hint, exc.message, typed)
    if admin.get("unknown") and ref:
        parts = []
        for v in admin["unknown"][:3]:
            near = (closest or {}).get(v)
            parts.append(f"value '{v}' not found in {ref}" + (f"; closest: '{near}'" if near else ""))
        return SpecError("value_not_found", ref, "; ".join(parts), exc.message, f"value not found in {ref}")
    if str(ref or "").startswith("measure:"):
        typed = f"{ref} is not a certified measure"
        return SpecError("unknown_measure", ref, typed, exc.message, typed)
    target = ref or admin.get("share") or admin.get("sort") or admin.get("field") or main
    typed = f"{target}: {exc.message}"
    return SpecError("invalid", str(target), typed, exc.message, typed)


def closest_value(value: str, known: list[str]) -> str | None:
    """The stored value spelt most like ``value`` (case and spacing ignored)."""
    by_norm = {" ".join(k.lower().split()): k for k in known}
    hits = difflib.get_close_matches(" ".join(value.lower().split()), list(by_norm), n=1, cutoff=0.6)
    return by_norm[hits[0]] if hits else None


def repair_block(previous: PlannerOut, errors: list[SpecError]) -> str:
    """The repair request appended to the question: the previous plan and why it was refused."""
    plan = previous.model_dump(mode="json", exclude_defaults=True)
    lines = "\n".join(f"- {e.typed}" for e in errors)
    return ("Your previous plan was refused. Fix what the errors name, keep the rest, and reply with the whole "
            f"corrected plan in the same JSON shape.\nPrevious plan: {json.dumps(plan, sort_keys=True)}\n"
            f"Errors:\n{lines}")


# --------------------------------------------------------------------------- log


_INSERT = text(
    "INSERT INTO rag.plan_repairs (id, tenant_id, connection_id, model_version_id, turn_id, attempt, kind, ref, "
    "detail, repaired) VALUES (:id, :t, :c, :v, CAST(:turn AS uuid), :a, :k, :r, :d, :ok)")


async def record(conn: Any, *, tenant_id: str, connection_id: str, model_version_id: str, turn_id: str | None,
                 attempts: list[list[SpecError]], repaired: bool) -> None:
    """One row per reason per refused plan (``attempts[0]`` AG-03's first plan, then its repairs); ``repaired``
    says whether a later plan passed."""
    for attempt, errors in enumerate(attempts, 1):
        for e in errors:
            await conn.execute(
                _INSERT, {"id": new_id(), "t": tenant_id, "c": connection_id, "v": model_version_id,
                          "turn": turn_id, "a": attempt, "k": e.kind, "r": (e.ref or "")[:200] or None,
                          "d": e.logged[:500], "ok": repaired})


@router.get("/plan-repairs")
async def top_reasons(connection_id: str = Query(max_length=64), days: int = Query(30, ge=1, le=365),
                      limit: int = Query(20, ge=1, le=100),
                      principal: Principal = Depends(require(Perm.KNOWLEDGE_MANAGE))) -> dict[str, Any]:
    """The most frequent reasons AG-03's plans were sent back, with how often a repair then passed."""
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT kind, ref, detail, count(*) AS n, count(*) FILTER (WHERE repaired) AS repaired, "
                  "max(created_at) AS last_at FROM rag.plan_repairs WHERE connection_id = CAST(:c AS uuid) AND "
                  "created_at > now() - make_interval(days => :d) GROUP BY kind, ref, detail "
                  "ORDER BY n DESC, last_at DESC LIMIT :l", {"c": connection_id, "d": days, "l": limit})
        kinds = await fetch_all(
            conn, "SELECT kind, count(*) AS n, count(*) FILTER (WHERE repaired) AS repaired FROM rag.plan_repairs "
                  "WHERE connection_id = CAST(:c AS uuid) AND created_at > now() - make_interval(days => :d) "
                  "GROUP BY kind ORDER BY n DESC", {"c": connection_id, "d": days})
    return {"days": days, "reasons": [dict(r) for r in rows], "by_kind": [dict(r) for r in kinds]}
