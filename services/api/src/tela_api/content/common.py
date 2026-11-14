"""Shared authoring helpers (P2): definition hashes, idempotent replays, the undo log (DATA-035),
notifications (DATA-051), the chart integrity guard (RULE-008, US-071) and preview principals.

* ``def_hash`` is the SHA-256 of the canonical spec without presentation (visual, page-filter following),
  so the same question pinned twice is recognised (MSG-023).
* The undo log stores whole-object snapshots as ``replace`` patches: forward = after, inverse = before.
* The guard decides from the result shape, never from the request: an unsuitable visual is refused with
  a one-click alternative, and a visual outside the catalogue is "Not available yet".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one
from tela_api.identity.principal import Principal, _merge_scope_attributes, live_grants
from tela_api.query.viz import result_shape
from tela_chart_spec import VISUALS, choose_visual, suitability
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import FILLED_CARDS, NO_CHART, ref_key, ref_kind

IDEMPOTENCY_HOURS = 24
VISUAL_NAMES = {"figure": "Figure", "ranked_bar": "Ranked bars", "columns": "Columns", "trend": "Trend",
                "paired_bars": "Paired bars", "stacked_status": "Stacked status bars", "donut": "Donut",
                "heat_grid": "Heat grid", "meter": "Meter", "dumbbell": "Dumbbell", "waterfall": "Waterfall",
                "bullet": "Bullet", "pareto": "Pareto", "xmr": "Control chart", "gantt": "Gantt",
                "funnel": "Funnel", "small_multiples": "Small multiples", "calendar": "Calendar heatmap",
                "sankey": "Sankey", "upset": "UpSet", "beeswarm": "Beeswarm", "ridgeline": "Ridgeline",
                "survival": "Time to event", "bump": "Bump chart", "treemap": "Treemap", "waffle": "Waffle",
                "table": "Table"}
DEFAULT_SIZES = {"figure": (3, 2), "meter": (4, 2), "bullet": (6, 3), "gantt": (12, 5),  # else 6 x 4 (US-062)
                 "small_multiples": (12, 5), "calendar": (12, 4), "sankey": (12, 5), "upset": (12, 5),
                 "ridgeline": (12, 5), "waffle": (4, 4), "table": (12, 5)}
NOT_AVAILABLE = "Not available yet"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def def_hash(spec: dict[str, Any]) -> str:
    """The widget's definition without its look; a detail table lists records, so its columns are part of it (a
    table of overdue projects is not the "Overdue go-lives" figure; US-158, US-159)."""
    core = {k: v for k, v in spec.items() if k not in ("visual", "follow_page_filters")}
    if detail := (spec.get("visual") or {}).get("detail"):
        core["detail"] = detail
    return hashlib.sha256(canonical(core).encode()).hexdigest()


def default_size(visual: str) -> tuple[int, int]:
    return DEFAULT_SIZES.get(visual, (6, 4))


# --------------------------------------------------------------------------- idempotency


async def replay(conn: AsyncConnection, principal: Principal, route: str, key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    row = await fetch_one(
        conn, "SELECT status_code, response FROM ops.idempotency_keys WHERE user_id = :u AND route = :r AND key = :k "
              "AND created_at > now() - make_interval(hours => :h)",
        {"u": principal.user_id, "r": route, "k": key[:200], "h": IDEMPOTENCY_HOURS})
    return row["response"] if row else None


async def remember(conn: AsyncConnection, principal: Principal, route: str, key: str | None, status: int,
                   body: dict[str, Any]) -> None:
    if not key:
        return
    await conn.execute(
        text("INSERT INTO ops.idempotency_keys (tenant_id, user_id, key, route, status_code, response) "
             "VALUES (:t, :u, :k, :r, :s, CAST(:b AS jsonb)) ON CONFLICT DO NOTHING"),
        {"t": principal.tenant_id, "u": principal.user_id, "k": key[:200], "r": route, "s": status,
         "b": canonical(body)})


# --------------------------------------------------------------------------- undo log


async def log_change(conn: AsyncConnection, principal: Principal, *, object_type: str, object_id: str,
                     source: str, before: Any, after: Any, summary: str) -> int:
    row = await fetch_one(conn, "SELECT coalesce(max(revision), 0) + 1 AS n FROM content.drafts_undo_log "
                                "WHERE object_type = :o AND object_id = CAST(:id AS uuid)",
                          {"o": object_type, "id": object_id})
    revision = int(row["n"]) if row else 1
    await conn.execute(
        text("INSERT INTO content.drafts_undo_log (tenant_id, object_type, object_id, revision, change_source, "
             "forward_patch, inverse_patch, summary, actor_id) VALUES (:t, :o, CAST(:id AS uuid), :rev, :src, "
             "CAST(:f AS jsonb), CAST(:i AS jsonb), :s, :a)"),
        {"t": principal.tenant_id, "o": object_type, "id": object_id, "rev": revision, "src": source,
         "f": canonical([{"op": "replace", "path": "", "value": after}]),
         "i": canonical([{"op": "replace", "path": "", "value": before}]), "s": summary[:300],
         "a": principal.user_id})
    # Keep the most recent 200 revisions per object (db_scheme DATA-035 note).
    await conn.execute(
        text("DELETE FROM content.drafts_undo_log WHERE object_type = :o AND object_id = CAST(:id AS uuid) "
             "AND revision <= :keep"), {"o": object_type, "id": object_id, "keep": revision - 200})
    return revision


# --------------------------------------------------------------------------- notifications


async def notify(conn: AsyncConnection, tenant_id: str, user_ids: list[str] | set[str], *, kind: str, title: str,
                 body: str, link: str | None = None, by: str | None = None) -> int:
    count = 0
    for uid in sorted(set(user_ids)):
        await conn.execute(
            text("INSERT INTO ops.notifications (id, tenant_id, user_id, kind, title, body, link, created_by) "
                 "VALUES (:id, :t, CAST(:u AS uuid), :k, :title, :body, :link, :by)"),
            {"id": new_id(), "t": tenant_id, "u": uid, "k": kind, "title": title[:200], "body": body[:1000],
             "link": link, "by": by})
        count += 1
    return count


async def users_with_roles(conn: AsyncConnection, role_ids: list[str]) -> list[str]:
    if not role_ids:
        return []
    rows = await fetch_all(
        conn, "SELECT u.id FROM iam.users u WHERE u.status = 'active' AND EXISTS (SELECT 1 FROM "
              "iam.f_effective_roles(u.id) r WHERE r.role_id = ANY(CAST(:roles AS uuid[])))", {"roles": role_ids})
    return [str(r["id"]) for r in rows]


async def users_with_permission(conn: AsyncConnection, permission: str) -> list[str]:
    roles = await fetch_all(conn, "SELECT DISTINCT role_id FROM iam.role_permissions WHERE permission_key = :p",
                            {"p": permission})
    return await users_with_roles(conn, [str(r["role_id"]) for r in roles])


# --------------------------------------------------------------------------- roles and preview principals


async def roles_by_ref(conn: AsyncConnection, refs: list[str]) -> list[dict[str, Any]]:
    """Resolve role keys or ids (tenant roles and global seed roles) to rows; unknown refs raise 400."""
    rows = await fetch_all(conn, "SELECT id, key, name, kind FROM iam.roles ORDER BY kind, name")
    out = []
    for ref in refs:
        hit = next((r for r in rows if str(r["id"]) == ref or r["key"] == ref), None)
        if hit is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Unknown role '{ref}'.")
        if hit not in out:
            out.append(hit)
    return out


async def principal_for_role(conn: AsyncConnection, caller: Principal, role: dict[str, Any]) -> Principal:
    """Everyone who holds only this role: its scope policies and field rules, no personal attributes."""
    perms = await fetch_all(conn, "SELECT permission_key FROM iam.role_permissions WHERE role_id = :r",
                            {"r": role["id"]})
    return Principal(user_id=caller.user_id, tenant_id=caller.tenant_id, display_name=f"Role: {role['name']}",
                     email=None, level="viewer", roles=(role["key"],), role_ids=(str(role["id"]),),
                     audience_roles=(role["key"],) if role["kind"] == "audience" else (),
                     permissions=frozenset(p["permission_key"] for p in perms), groups=())


async def principal_for_user(conn: AsyncConnection, caller: Principal, user_id: str) -> Principal:
    """A named person as they would resolve now (roles from their current group memberships)."""
    try:
        user = await fetch_one(conn, "SELECT id, display_name, email::text AS email, status FROM iam.users "
                                     "WHERE id = CAST(:u AS uuid)", {"u": user_id})
    except Exception:  # noqa: BLE001 - malformed id
        user = None
    if user is None or user["status"] not in ("active", "pending"):
        raise TelaError(ErrorCode.NOT_FOUND, "That person was not found.")
    roles = await fetch_all(conn, "SELECT role_id, role_key, kind, scope_attributes FROM iam.f_effective_roles(:u)",
                            {"u": user["id"]})
    role_ids = sorted({str(r["role_id"]) for r in roles})
    perms = await fetch_all(conn, "SELECT DISTINCT permission_key FROM iam.role_permissions "
                                  "WHERE role_id = ANY(CAST(:ids AS uuid[]))", {"ids": role_ids}) if role_ids else []
    keys = {r["role_key"] for r in roles}
    level = next((lvl for lvl in ("admin", "creator", "viewer") if lvl in keys), "viewer")
    return Principal(user_id=caller.user_id, tenant_id=caller.tenant_id, display_name=user["display_name"],
                     email=None, level=level, roles=tuple(sorted(keys)), role_ids=tuple(role_ids),
                     audience_roles=tuple(sorted(r["role_key"] for r in roles if r["kind"] == "audience")),
                     permissions=frozenset(p["permission_key"] for p in perms), groups=(),
                     scope_attributes=_merge_scope_attributes(roles), grants=await live_grants(conn, str(user["id"])))


# --------------------------------------------------------------------------- chart integrity guard (RULE-008)


def visual_options(result: dict[str, Any], snap: ModelSnapshot, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Suitable visuals and disabled ones with reasons (API-023). With the widget's ``spec``, a single figure also
    gets ``fixes``: per visual, the breakdown/date ops that would make it fit ("by Status")."""
    shape = result_shape(result, snap)
    table = suitability(shape)
    out = {"suitable": [s["visual"] for s in table if s["suitable"]],
           "disabled": [{"visual": s["visual"], "label": VISUAL_NAMES[s["visual"]], "reason": s["reason"]}
                        for s in table if not s["suitable"]],
           "recommended": choose_visual(shape)[0]}
    if spec is not None:
        out["fixes"] = breakdown_fixes(spec, snap)
    return out


def breakdown_fixes(spec: dict[str, Any], snap: ModelSnapshot) -> dict[str, dict[str, Any]]:
    """For a measure with no breakdown or date, the chip ops that make each chart (and a real table) possible.

    The ops go through ``apply_ops`` like any refinement, and the integrity guard still decides afterwards.
    """
    from tela_api.ask.refine import default_time
    from tela_query_spec.spec import ref_key

    if spec.get("breakdowns") or spec.get("time"):
        return {}
    m = snap.measure(ref_key(spec["measure"]["ref"]))
    if m is None:
        return {}
    reach = [d for d in snap.dimensions if snap.join_path(m.entity, d.entity) is not None]
    cats = [d for d in reach if not d.is_temporal and not d.is_status]
    statuses = [d for d in reach if d.is_status and not d.is_temporal]
    by = lambda d: {"op": "add_breakdown", "ref": f"dimension:{d.key}"}  # noqa: E731
    fixes: dict[str, dict[str, Any]] = {}

    def offer(visual: str, label: str, ops: list[dict[str, Any]]) -> None:
        fixes[visual] = {"label": label, "ops": ops}

    first = cats[0] if cats else (statuses[0] if statuses else None)
    # Several measures (EXEC-173): a breakdown keeps all of them, so only charts that take that many measures in one
    # unit are offered (paired bars for two, a trend for up to four) and a table; never a one-measure chart.
    extras = [x for e in spec.get("extra_measures", []) if (x := snap.measure(ref_key(e["ref"]))) is not None]
    if extras:
        one_unit = all(x.format == m.format for x in extras)
        if first is not None:
            offer("table", f"by {first.label.lower()}", [by(first)])
            if len(extras) == 1 and one_unit:
                offer("paired_bars", f"by {first.label.lower()}", [by(first)])
        if len(extras) <= 3 and one_unit and default_time(spec, snap) is not None:
            offer("trend", "by month, last 12 months",
                  [{"op": "set_time", "grain": "month"}, {"op": "set_range", "relative": "last_12_months"}])
        return fixes
    if first is not None:
        offer("ranked_bar", f"by {first.label.lower()}", [by(first)])
        offer("table", f"by {first.label.lower()}", [by(first)])
    part = statuses[0] if statuses else first
    if part is not None:
        offer("donut", f"by {part.label.lower()}", [by(part)])
    if cats and statuses:
        offer("stacked_status", f"by {cats[0].label.lower()} and {statuses[0].label.lower()}",
              [by(cats[0]), by(statuses[0])])
    if default_time(spec, snap) is not None:
        offer("columns", "by month", [{"op": "set_time", "grain": "month"}])
        offer("trend", "by month, last 12 months",
              [{"op": "set_time", "grain": "month"}, {"op": "set_range", "relative": "last_12_months"}])
    other = next((x for x in snap.measures if x.key != m.key and x.format == m.format and x.status == "certified"
                  and x.entity == m.entity), None)
    if first is not None and other is not None:
        offer("paired_bars", f"{other.label} beside it, by {first.label.lower()}",
              [by(first), {"op": "add_measure", "ref": f"measure:{other.key}"}])
    return fixes


def alternative_label(visual: str, result: dict[str, Any]) -> str:
    dims = [c for c in result.get("columns", []) if c["kind"] != "measure"]
    if not dims:
        return VISUAL_NAMES.get(visual, visual)
    return f"{VISUAL_NAMES.get(visual, visual)} by {dims[0]['label'].lower()}"


def guard_visual(result: dict[str, Any], snap: ModelSnapshot, requested: str) -> None:
    """Raise ``visual_not_suitable`` with a reason and one alternative when ``requested`` would mislead."""
    if result.get("hidden"):
        return
    if requested not in VISUALS:
        raise TelaError(ErrorCode.VISUAL_NOT_SUITABLE, f"{requested.replace('_', ' ').capitalize()}: {NOT_AVAILABLE}.",
                        {"visual": requested, "reason": NOT_AVAILABLE, "alternative": None})
    shape = result_shape(result, snap)
    row = next(s for s in suitability(shape) if s["visual"] == requested)
    if row["suitable"]:
        return
    alt = choose_visual(shape)[0]
    raise TelaError(ErrorCode.VISUAL_NOT_SUITABLE, f"{row['reason']}.",
                    {"visual": requested, "reason": row["reason"], "alternative": alt,
                     "alternative_label": alternative_label(alt, result)})


def filter_entity(ref: str, snap: ModelSnapshot) -> tuple[str | None, str | None]:
    """The entity a dimension or field ref filters, and its label; (None, None) when the model has no such field."""
    if ref_kind(ref) == "dimension":
        dim = snap.dimension(ref_key(ref))
        return (dim.entity, dim.label) if dim else (None, None)
    if ref_kind(ref) == "field":
        ent, _, key = ref_key(ref).partition(".")
        entity = snap.entity(ent)
        f = entity.field(key) if entity is not None else None
        return (ent, getattr(f, "label", None) or key) if f is not None else (None, None)
    return None, None


def home_entity(spec: dict[str, Any], snap: ModelSnapshot) -> str | None:
    """The entity a widget counts: its ad-hoc rule's (US-163) or its measure's."""
    primary = spec.get("measure") or next(iter(spec.get("measures") or []), None) or {}
    if primary.get("adhoc"):
        return str(primary["adhoc"]["entity"])
    m = snap.measure(ref_key(primary["ref"])) if str(primary.get("ref", "")).startswith("measure:") else None
    return m.entity if m is not None else None


def check_controls(controls: list[str], spec: dict[str, Any], snap: ModelSnapshot) -> None:
    """A widget filter (US-221) is a field of the model the widget's measure reaches through the joins."""
    home = home_entity(spec, snap)
    for ref in controls:
        entity, _ = filter_entity(ref, snap)
        if entity is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"The model has no field {ref_key(ref)} to filter by.")
        if home is None or snap.join_path(home, entity) is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR,
                            f"This widget can't be filtered by {filter_entity(ref, snap)[1]}: it counts records "
                            "that don't link to it.")


def stored_visual(current: dict[str, Any] | None, visual: str, *, tone: Any = None, note: str | None = None,
                  detail: Any = None, colour: bool | None = None, card: str | None = None, colours: Any = None,
                  controls: list[str] | None = None,
                  sent: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """The widget's ``spec.visual`` after an edit (US-157, US-158). A new chart type drops the old type's options;
    ``tone`` (a figure's threshold), ``note`` (a meter's), ``detail`` (a detail table's columns) and ``colour`` (a
    table in colour, ENH-020) change only when ``sent``, and null clears them.

    ENH-023: the card colour, chart colours and widget filter controls belong to the widget, not the chart type, so
    they survive a new chart type (a filled card or chart colours drop when the new type cannot show them)."""
    before = current or {}
    out = dict(before) if before.get("type") == visual else {}
    out["type"] = visual
    if before.get("type") != visual:
        if before.get("card") and (before["card"] not in FILLED_CARDS or visual == "figure"):
            out["card"] = before["card"]
        if before.get("colours") and visual not in NO_CHART:
            out["colours"] = before["colours"]
        if before.get("controls"):
            out["controls"] = before["controls"]
    if "card" in sent:
        out["card"] = card if card and card != "default" else None
    if "colours" in sent:
        out["colours"] = colours.model_dump() if colours is not None else None
    if "controls" in sent:
        out["controls"] = list(controls) if controls else None
    if out.get("card") in FILLED_CARDS and visual != "figure":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Only a single figure sits on a brand or dark card.")
    if out.get("colours") and visual in NO_CHART:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "This visual has no chart colours.")
    if "tone" in sent:
        out["tone"] = tone.model_dump() if tone is not None else None
    if "note" in sent:
        out["note"] = note.strip() if note and note.strip() else None
    if "detail" in sent:
        out["detail"] = detail.model_dump(exclude_none=True) if detail is not None else None
    if "colour" in sent:
        out["colour"] = True if colour else None
    if out.get("colour") and visual != "table":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Only a table has colours.")
    if out.get("detail") and visual != "table":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Only a table lists records.")
    if out.get("tone") and visual != "figure":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Only a figure has a tone threshold.")
    if out.get("note") and visual != "meter":
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Only a meter has a note.")
    t = out.get("tone") or {}
    if t.get("warning") is not None and t.get("critical") is not None and (
            t["critical"] < t["warning"] if t.get("higher_is_worse", True) else t["critical"] > t["warning"]):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "The critical threshold must be past the warning threshold.")
    return {k: v for k, v in out.items() if v is not None}


def refusal(exc: TelaError) -> dict[str, Any]:
    return {"reason": exc.details.get("reason") or exc.message, "alternative": exc.details.get("alternative"),
            "alternative_label": exc.details.get("alternative_label")}


# --------------------------------------------------------------------------- spec or custom SQL


# A free-hand rule (US-163) stays in its owner's library until an Admin certifies it (US-166).
MSG_FREEHAND_HELD = ("A free-hand widget stays in your library until an Admin certifies it. It can't go on a report, "
                     "be shared or be exported before then.")


def is_freehand(spec: dict[str, Any] | None) -> bool:
    return bool(spec and isinstance(spec.get("measure"), dict) and spec["measure"].get("adhoc"))


def is_custom(spec: dict[str, Any]) -> bool:
    return spec.get("kind") == "custom_sql"


def measure_ref(spec: dict[str, Any]) -> str | None:
    return None if is_custom(spec) else spec.get("measure", {}).get("ref")


async def run_spec(service: Any, spec: dict[str, Any], principal: Principal, ctx: Any,
                   page_filters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run a widget definition, whether a Query Spec or custom SQL, through the single scoped path."""
    if is_custom(spec):
        return dict(await service.execute_custom(
            spec["sql"], connection_id=spec["connection_id"], model_version_id=spec["model_version_id"],
            principal=principal, ctx=ctx,
            page_filters=(page_filters or []) if spec.get("follow_page_filters", True) else []))
    return dict(await service.execute({k: v for k, v in spec.items() if k != "compare"}, principal, ctx))
