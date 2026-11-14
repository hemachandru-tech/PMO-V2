"""Admin access APIs (FEAT-002, FEAT-003; API-004, API-005, API-006; US-014, US-015).

* Group → role mappings are replaced as a whole set under optimistic
  concurrency (ETag / If-Match).
* A mapping to a protected role (Admin) stays *pending* until a different
  Admin approves it (S03 §9; REQ-006); it is audited with high severity.
* Scope policies hold structured predicates only (never SQL). When the
  connection has an approved model, the entity and every predicate field must
  exist in it (US-015 / US-031); before approval they are checked as identifiers.
* Any policy change drops the connection's cached results (ADR-010).
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api import outbox
from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity.deps import require
from tela_api.identity.grants import grants_for
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal, PrincipalResolver
from tela_api.identity.scope import Predicate, ScopePolicyIn, combine_policies, scope_links
from tela_common.errors import ErrorCode, TelaError
from tela_common.ids import new_id

router = APIRouter(prefix="/api/v1/admin", tags=["admin-access"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _invalidate(request: Request, tenant_id: str) -> None:
    resolver: PrincipalResolver = request.app.state.principal_resolver
    resolver.invalidate(tenant_id=tenant_id)


async def _invalidate_results(request: Request, conn: AsyncConnection, connection_id: str | None) -> None:
    cache = getattr(request.app.state, "result_cache", None)
    if cache is None:
        return
    if connection_id is None:
        await cache.invalidate_tenant(conn)
    else:
        await cache.invalidate_connection(conn, connection_id)


async def _check_against_model(conn: AsyncConnection, tenant_id: str, connection_id: str, entity: str,
                               fields: list[str]) -> None:
    """Scope policy entity and fields must exist in the approved model, when there is one (US-015)."""
    await _check_many_against_model(conn, tenant_id, connection_id, [entity], fields)


async def _check_many_against_model(conn: AsyncConnection, tenant_id: str, connection_id: str, entities: list[str],
                                    fields: list[str]) -> None:
    """Every entity, and the condition's fields on each of them, must be in the approved model (US-172). One error
    names every problem so a form with many entities can show them all at once."""
    from tela_api.model.store import current_version_id, load_snapshot

    version_id = await current_version_id(conn, connection_id)
    if version_id is None:
        return
    snap = await load_snapshot(conn, tenant_id, version_id)
    unknown: list[str] = []
    missing: dict[str, list[str]] = {}
    for entity in entities:
        ent = snap.entity(entity)
        if ent is None:
            unknown.append(entity)
            continue
        if gaps := [f for f in fields if ent.field(f) is None]:
            missing[entity] = gaps
    if len(entities) == 1 and unknown:
        raise TelaError(ErrorCode.FIELD_NOT_IN_MODEL, f"'{unknown[0]}' is not an entity of the approved model.",
                        {"entity": unknown[0]})
    if len(entities) == 1 and missing:
        ent = snap.entity(entities[0])
        assert ent is not None
        raise TelaError(ErrorCode.FIELD_NOT_IN_MODEL, f"Not fields of {ent.label}: {', '.join(missing[entities[0]])}.",
                        {"fields": missing[entities[0]]})
    if unknown or missing:
        parts = ([f"not entities of the approved model: {', '.join(unknown)}"] if unknown else []) + [
            f"{e} has no {', '.join(f)}" for e, f in missing.items()]
        raise TelaError(ErrorCode.FIELD_NOT_IN_MODEL, f"Cannot save: {'; '.join(parts)}.",
                        {"entities": unknown, "fields_by_entity": missing})


async def _connection_exists(conn: AsyncConnection, connection_id: str) -> None:
    row = await fetch_one(
        conn, "SELECT 1 AS ok FROM conn.connections WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL",
        {"id": connection_id},
    )
    if row is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")


async def _role_by_key(conn: AsyncConnection, key: str) -> dict[str, Any]:
    role = await fetch_one(conn, "SELECT id, key, kind, is_protected FROM iam.roles WHERE key = :k", {"k": key})
    if role is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Unknown role '{key}'.", {"role": key})
    return role


# --------------------------------------------------------------------------- roles, groups, users


@router.get("/roles")
async def list_roles(principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn, "SELECT id, key, name, kind, is_protected FROM iam.roles ORDER BY kind DESC, key"
        )
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}


class GroupIn(BaseModel):
    external_group_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)


@router.get("/groups")
async def list_groups(
    q: str | None = Query(default=None, max_length=100),
    principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE)),
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT g.id, g.external_group_id, g.display_name, g.source, g.last_synced_at,
                   (SELECT count(*) FROM iam.idp_group_members m WHERE m.group_id = g.id) AS member_count
            FROM iam.idp_groups g
            WHERE g.scim_deleted_at IS NULL  -- gone from the directory; a kept mapping still lists under mappings
              AND (CAST(:q AS text) IS NULL
                   OR g.display_name ILIKE '%' || :q || '%' OR g.external_group_id ILIKE '%' || :q || '%')
            ORDER BY g.display_name LIMIT 200
            """,
            {"q": q},
        )
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}


@router.post("/groups", status_code=201)
async def register_group(
    body: GroupIn, request: Request, principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))
) -> dict[str, Any]:
    """Register an Entra group by object id before SCIM provisioning exists (FEAT-004 is P3)."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            INSERT INTO iam.idp_groups (id, tenant_id, external_group_id, display_name, source, created_by)
            VALUES (:id, :t, :ext, :name, 'admin', :by)
            ON CONFLICT (tenant_id, external_group_id) DO UPDATE SET display_name = EXCLUDED.display_name
            RETURNING id, external_group_id, display_name, source
            """,
            {"id": new_id(), "t": principal.tenant_id, "ext": body.external_group_id,
             "name": body.display_name, "by": principal.user_id},
        )
        assert row is not None
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.group.registered", object_type="idp_group",
            object_id=str(row["id"]), outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
            details={"external_group_id": body.external_group_id},
        )
    return {**row, "id": str(row["id"])}


@router.get("/users")
async def list_users(
    q: str | None = Query(default=None, max_length=100),
    principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE)),
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT id, display_name, email::text AS email, status, last_login_at, effective_roles_cache AS roles
            FROM iam.users
            WHERE (CAST(:q AS text) IS NULL OR display_name ILIKE '%' || :q || '%'
                   OR email::text ILIKE '%' || :q || '%')
            ORDER BY display_name LIMIT 100
            """,
            {"q": q},
        )
    return {"items": [{**r, "id": str(r["id"])} for r in rows]}


# --------------------------------------------------------------------------- group role mappings


class MappingIn(BaseModel):
    idp_group_id: str = Field(min_length=1, max_length=200, description="Entra group object id")
    role: str = Field(min_length=2, max_length=40)
    audience_role: str | None = Field(default=None, description="Convenience: adds a second mapping row")
    scope_attributes: dict[str, list[str]] = Field(default_factory=dict)


class MappingsPut(BaseModel):
    mappings: list[MappingIn] = Field(max_length=500)


async def _load_mappings(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn,
        """
        SELECT m.id, g.external_group_id AS idp_group_id, g.display_name AS group_name, r.key AS role,
               r.kind AS role_kind, r.is_protected, m.scope_attributes, m.approved_at, m.approved_by, m.created_by
        FROM iam.group_role_mappings m
        JOIN iam.idp_groups g ON g.id = m.group_id
        JOIN iam.roles r ON r.id = m.role_id
        ORDER BY g.display_name, r.kind DESC, r.key
        """,
    )
    out = []
    for r in rows:
        status = "pending_approval" if r["is_protected"] and r["approved_at"] is None else "active"
        out.append({
            "id": str(r["id"]), "idp_group_id": r["idp_group_id"], "group_name": r["group_name"],
            "role": r["role"], "role_kind": r["role_kind"], "requires_approval": r["is_protected"],
            "status": status, "scope_attributes": r["scope_attributes"],
            "requested_by": str(r["created_by"]) if r["created_by"] else None,
        })
    return out


def _etag(mappings: list[dict[str, Any]]) -> str:
    canon = sorted(
        (m["idp_group_id"], m["role"], json.dumps(m["scope_attributes"], sort_keys=True), m["status"])
        for m in mappings
    )
    return 'W/"' + hashlib.sha256(json.dumps(canon).encode()).hexdigest()[:20] + '"'


@router.get("/group-mappings")
async def get_mappings(
    response: Response, principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        mappings = await _load_mappings(conn)
    etag = _etag(mappings)
    response.headers["ETag"] = etag
    return {"etag": etag, "mappings": mappings}


@router.put("/group-mappings")
async def put_mappings(
    body: MappingsPut,
    request: Request,
    response: Response,
    if_match: str | None = Header(default=None),
    principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE)),
) -> dict[str, Any]:
    if not if_match:
        raise TelaError(ErrorCode.PRECONDITION_FAILED, "Reload the mappings and try again (If-Match required).")

    desired: dict[tuple[str, str], dict[str, list[str]]] = {}
    for item in body.mappings:
        desired[(item.idp_group_id, item.role)] = item.scope_attributes
        if item.audience_role:
            desired.setdefault((item.idp_group_id, item.audience_role), item.scope_attributes)

    async with tenant_tx(principal.tenant_id) as conn:
        # Serialise concurrent edits of the same tenant's mapping set.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('mappings:' || :t))"), {"t": principal.tenant_id}
        )
        current = await _load_mappings(conn)
        if _etag(current) != if_match:
            raise TelaError(ErrorCode.PRECONDITION_FAILED, "The mappings changed since you loaded them. Reload.")

        groups = {
            g["external_group_id"]: str(g["id"])
            for g in await fetch_all(conn, "SELECT id, external_group_id FROM iam.idp_groups")
        }
        unknown = sorted({g for g, _ in desired if g not in groups})
        if unknown:
            raise TelaError(ErrorCode.UNKNOWN_GROUP, "Some groups are not known to Tela.", {"groups": unknown})
        roles = {r["key"]: r for r in await fetch_all(conn, "SELECT id, key, kind, is_protected FROM iam.roles")}
        bad_roles = sorted({r for _, r in desired if r not in roles})
        if bad_roles:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Unknown roles.", {"roles": bad_roles})

        current_by_key = {(m["idp_group_id"], m["role"]): m for m in current}
        added: list[tuple[str, str]] = []
        removed: list[tuple[str, str]] = []
        changed: list[tuple[str, str]] = []

        for key, cur in current_by_key.items():
            if key not in desired:
                await conn.execute(text("DELETE FROM iam.group_role_mappings WHERE id = :id"), {"id": cur["id"]})
                removed.append(key)

        for key, attrs in desired.items():
            group_external, role_key = key
            role = roles[role_key]
            existing = current_by_key.get(key)
            if existing is None:
                await conn.execute(
                    text(
                        "INSERT INTO iam.group_role_mappings (id, tenant_id, group_id, role_id, scope_attributes, "
                        "created_by) VALUES (:id, :t, :g, :r, CAST(:attrs AS jsonb), :by)"
                    ),
                    {"id": new_id(), "t": principal.tenant_id, "g": groups[group_external], "r": role["id"],
                     "attrs": json.dumps(attrs), "by": principal.user_id},
                )
                added.append(key)
            elif existing["scope_attributes"] != attrs:
                # A changed protected mapping needs fresh approval.
                await conn.execute(
                    text(
                        "UPDATE iam.group_role_mappings SET scope_attributes = CAST(:attrs AS jsonb), "
                        "created_by = :by, approved_at = CASE WHEN :protected THEN NULL ELSE approved_at END, "
                        "approved_by = CASE WHEN :protected THEN NULL ELSE approved_by END WHERE id = :id"
                    ),
                    {"attrs": json.dumps(attrs), "by": principal.user_id, "protected": role["is_protected"],
                     "id": existing["id"]},
                )
                changed.append(key)

        # Never leave the tenant without an approved Admin mapping (raising rolls the whole set back).
        after = await _load_mappings(conn)
        if not any(m["role"] == "admin" and m["status"] == "active" for m in after):
            raise TelaError(
                ErrorCode.PENDING_APPROVAL,
                "This change would leave no active Admin mapping. Keep one approved Admin group.",
                {"reason": "last_active_admin"},
            )

        touches_protected = any(roles[r]["is_protected"] for _, r in [*added, *removed, *changed])
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.mapping.changed", object_type="group_role_mappings",
            outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
            severity="high" if touches_protected else "normal",
            details={
                "added": [list(k) for k in added], "removed": [list(k) for k in removed],
                "changed": [list(k) for k in changed],
            },
        )
        if added or removed or changed:
            await outbox.emit(
                conn, tenant_id=principal.tenant_id, topic="access.mappings_changed",
                aggregate_id=principal.tenant_id, payload={"added": len(added), "removed": len(removed)},
            )
        result = await _load_mappings(conn)

    _invalidate(request, principal.tenant_id)
    etag = _etag(result)
    response.headers["ETag"] = etag
    return {"etag": etag, "mappings": result}


@router.post("/group-mappings/{mapping_id}/approve")
async def approve_mapping(
    mapping_id: str, request: Request, principal: Principal = Depends(require(Perm.MAPPINGS_MANAGE))
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            SELECT m.id, m.created_by, m.approved_at, r.is_protected, r.key AS role
            FROM iam.group_role_mappings m JOIN iam.roles r ON r.id = m.role_id
            WHERE m.id = CAST(:id AS uuid)
            """,
            {"id": mapping_id},
        )
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Mapping not found.")
        if not row["is_protected"] or row["approved_at"] is not None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "This mapping does not need approval.")
        if row["created_by"] is not None and str(row["created_by"]) == principal.user_id:
            raise TelaError(
                ErrorCode.FORBIDDEN, "A different Admin must approve a mapping that grants Admin.",
                {"reason": "self_approval"},
            )
        await conn.execute(
            text("UPDATE iam.group_role_mappings SET approved_by = :by, approved_at = now() WHERE id = :id"),
            {"by": principal.user_id, "id": row["id"]},
        )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.mapping.approved", object_type="group_role_mapping",
            object_id=mapping_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
            severity="high", details={"role": row["role"]},
        )
        mappings = await _load_mappings(conn)
    _invalidate(request, principal.tenant_id)
    return {"etag": _etag(mappings), "mappings": mappings}


# --------------------------------------------------------------------------- scope policies


def _policy_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "connection_id": str(row["connection_id"]), "entity": row["entity_key"],
        "role": row["role"], "all_rows": row["all_rows"], "predicates": row["predicate"],
        "description": row["description"], "is_active": row["is_active"],
    }


_POLICY_SELECT = """
    SELECT p.id, p.connection_id, p.entity_key, r.key AS role, p.all_rows, p.predicate, p.description, p.is_active
    FROM iam.scope_policies p JOIN iam.roles r ON r.id = p.role_id
"""


@router.get("/scope-policies")
async def list_scope_policies(
    connection_id: str | None = None, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            _POLICY_SELECT + " WHERE (CAST(:cid AS uuid) IS NULL OR p.connection_id = CAST(:cid AS uuid)) "
            "ORDER BY p.entity_key, r.key",
            {"cid": connection_id},
        )
    return {"items": [_policy_out(r) for r in rows]}


@router.get("/scope-policies/entities")
async def scope_policy_entities(
    connection_id: str, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> dict[str, Any]:
    """Entities and fields a policy can name: the approved model's, not the admin's own scope. No approved model yet
    means any entity is accepted, so the form falls back to typing it (US-015)."""
    from tela_api.model.store import current_version_id, load_snapshot

    async with tenant_tx(principal.tenant_id) as conn:
        await _connection_exists(conn, connection_id)
        version_id = await current_version_id(conn, connection_id)
        if version_id is None:
            return {"model_version_id": None, "entities": []}
        snap = await load_snapshot(conn, principal.tenant_id, version_id)
    return {
        "model_version_id": version_id,
        "entities": [{"key": e.key, "label": e.label, "table": e.source_object,
                      "fields": [{"key": f.key, "label": f.label} for f in e.fields]}
                     for e in sorted(snap.entities, key=lambda e: e.label.lower())],
    }


async def _insert_policy(conn: AsyncConnection, request: Request, principal: Principal, body: ScopePolicyIn,
                         role_id: Any, batch: int | None = None) -> str:
    policy_id: str = new_id()
    await conn.execute(
        text(
            "INSERT INTO iam.scope_policies (id, tenant_id, role_id, connection_id, entity_key, all_rows, "
            "predicate, description, created_by) VALUES (:id, :t, :r, CAST(:c AS uuid), :e, :all, "
            "CAST(:p AS jsonb), :d, :by)"
        ),
        {"id": policy_id, "t": principal.tenant_id, "r": role_id, "c": body.connection_id,
         "e": body.entity, "all": body.all_rows,
         "p": json.dumps([p.model_dump(exclude_none=True) for p in body.predicates]),
         "d": body.description, "by": principal.user_id},
    )
    details: dict[str, Any] = {"entity": body.entity, "role": body.role, "all_rows": body.all_rows}
    if batch is not None:
        details["batch"] = batch
    await audit.record(
        conn, tenant_id=principal.tenant_id, action="access.scope_policy.created", object_type="scope_policy",
        object_id=policy_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
        severity="high" if body.all_rows else "normal", details=details,
    )
    return policy_id


@router.post("/scope-policies", status_code=201)
async def create_scope_policy(
    body: ScopePolicyIn, request: Request, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await _connection_exists(conn, body.connection_id)
        await _check_against_model(conn, principal.tenant_id, body.connection_id, body.entity,
                                   [p.field for p in body.predicates])
        role = await _role_by_key(conn, body.role)
        policy_id = await _insert_policy(conn, request, principal, body, role["id"])
        await _invalidate_results(request, conn, body.connection_id)
        row = await fetch_one(conn, _POLICY_SELECT + " WHERE p.id = :id", {"id": policy_id})
    _invalidate(request, principal.tenant_id)
    assert row is not None
    return _policy_out(row)


class ScopePolicyBulkIn(BaseModel):
    """One condition (or all rows) for every pair of entity and role (US-172)."""

    connection_id: str
    entities: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(min_length=1, max_length=500)
    roles: list[Annotated[str, Field(min_length=2, max_length=40)]] = Field(min_length=1, max_length=50)
    all_rows: bool = False
    predicates: list[Predicate] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _each(self) -> ScopePolicyBulkIn:
        self.policies()  # the single-policy rules, for every entity
        return self

    def policies(self) -> list[ScopePolicyIn]:
        """Each pair once, in the order given."""
        return [ScopePolicyIn(connection_id=self.connection_id, entity=e, role=r, all_rows=self.all_rows,
                              predicates=self.predicates, description=self.description)
                for e in dict.fromkeys(self.entities) for r in dict.fromkeys(self.roles)]


@router.post("/scope-policies/bulk", status_code=201)
async def create_scope_policies_bulk(
    body: ScopePolicyBulkIn, request: Request, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> dict[str, Any]:
    """Create the same policy for several entities and roles at once, all or nothing. A pair that already has an
    identical policy (same rows, active or not) is skipped and reported, so re-running a selection adds no copies."""
    wanted = body.policies()
    entities = list(dict.fromkeys(body.entities))
    async with tenant_tx(principal.tenant_id) as conn:
        await _connection_exists(conn, body.connection_id)
        await _check_many_against_model(conn, principal.tenant_id, body.connection_id, entities,
                                        [p.field for p in body.predicates])
        roles = {k: await _role_by_key(conn, k) for k in dict.fromkeys(body.roles)}
        predicate = [p.model_dump(exclude_none=True) for p in body.predicates]
        existing = await fetch_all(
            conn,
            _POLICY_SELECT + " WHERE p.connection_id = CAST(:cid AS uuid) AND p.entity_key = ANY(:ents) "
            "AND r.key = ANY(:roles)",
            {"cid": body.connection_id, "ents": entities, "roles": list(roles)},
        )
        same = {(r["entity_key"], r["role"]): r for r in existing
                if r["all_rows"] == body.all_rows and r["predicate"] == predicate}
        created: list[str] = []
        skipped: list[dict[str, Any]] = []
        for policy in wanted:
            if (dup := same.get((policy.entity, policy.role))) is not None:
                skipped.append({"entity": policy.entity, "role": policy.role, "id": str(dup["id"]),
                                "is_active": dup["is_active"]})
                continue
            created.append(await _insert_policy(conn, request, principal, policy, roles[policy.role]["id"],
                                                batch=len(wanted)))
        if created:
            await _invalidate_results(request, conn, body.connection_id)
        rows = await fetch_all(conn, _POLICY_SELECT + " WHERE p.id = ANY(CAST(:ids AS uuid[])) "
                               "ORDER BY p.entity_key, r.key", {"ids": created}) if created else []
    if created:
        _invalidate(request, principal.tenant_id)
    return {"created": [_policy_out(r) for r in rows], "skipped": skipped}


class ScopePolicyPatch(BaseModel):
    all_rows: bool | None = None
    predicates: list[Predicate] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=300)
    is_active: bool | None = None


@router.patch("/scope-policies/{policy_id}")
async def patch_scope_policy(
    policy_id: str, body: ScopePolicyPatch, request: Request,
    principal: Principal = Depends(require(Perm.POLICIES_MANAGE)),
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await fetch_one(conn, _POLICY_SELECT + " WHERE p.id = CAST(:id AS uuid)", {"id": policy_id})
        if row is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Scope policy not found.")
        merged = ScopePolicyIn(
            connection_id=str(row["connection_id"]), entity=row["entity_key"], role=row["role"],
            all_rows=row["all_rows"] if body.all_rows is None else body.all_rows,
            predicates=row["predicate"] if body.predicates is None else body.predicates,
            description=body.description or row["description"],
        )
        await _check_against_model(conn, principal.tenant_id, merged.connection_id, merged.entity,
                                   [p.field for p in merged.predicates])
        await conn.execute(
            text(
                "UPDATE iam.scope_policies SET all_rows = :all, predicate = CAST(:p AS jsonb), description = :d, "
                "is_active = :active WHERE id = :id"
            ),
            {"all": merged.all_rows, "p": json.dumps([p.model_dump(exclude_none=True) for p in merged.predicates]),
             "d": merged.description, "active": row["is_active"] if body.is_active is None else body.is_active,
             "id": row["id"]},
        )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.scope_policy.changed", object_type="scope_policy",
            object_id=policy_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
            details={"fields": sorted(body.model_dump(exclude_none=True).keys())},
        )
        await _invalidate_results(request, conn, str(row["connection_id"]))
        updated = await fetch_one(conn, _POLICY_SELECT + " WHERE p.id = :id", {"id": row["id"]})
    _invalidate(request, principal.tenant_id)
    assert updated is not None
    return _policy_out(updated)


@router.delete("/scope-policies/{policy_id}", status_code=204)
async def delete_scope_policy(
    policy_id: str, request: Request, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> Response:
    async with tenant_tx(principal.tenant_id) as conn:
        res = await conn.execute(
            text("DELETE FROM iam.scope_policies WHERE id = CAST(:id AS uuid) RETURNING connection_id"),
            {"id": policy_id},
        )
        deleted = res.first()
        if deleted is None:
            raise TelaError(ErrorCode.NOT_FOUND, "Scope policy not found.")
        await _invalidate_results(request, conn, str(deleted[0]))
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.scope_policy.deleted", object_type="scope_policy",
            object_id=policy_id, outcome="success", actor_id=principal.user_id, correlation_id=_rid(request),
        )
    _invalidate(request, principal.tenant_id)
    return Response(status_code=204)


@router.post("/scope-policies/preview")
async def preview_scope(
    connection_id: str,
    request: Request,
    user_id: str | None = None,
    role: str | None = None,
    principal: Principal = Depends(require(Perm.POLICIES_MANAGE)),
) -> dict[str, Any]:
    """Resolved level, roles and scope of a named person, or of a role on its own, on one connection.

    FEAT-002 step 3; the Admin "Effective access" panel (US-145). Pass ``user_id`` or ``role`` (a role key). A role
    resolves as someone holding only that role: no group memberships and no personal attributes, so a policy that
    reads a person's attribute reports it as missing. When the connection has an approved model, the row count per
    entity under that scope is computed through the single scoped path (US-015, US-020).
    """
    if (user_id is None) == (role is None):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose a person or a role to preview.")
    async with tenant_tx(principal.tenant_id) as conn:
        await _connection_exists(conn, connection_id)
        groups: list[str] = []
        if user_id is not None:
            try:
                user = await fetch_one(
                    conn, "SELECT id, display_name FROM iam.users WHERE id = CAST(:id AS uuid)", {"id": user_id}
                )
            except Exception:  # noqa: BLE001 - malformed id
                user = None
            if user is None:
                raise TelaError(ErrorCode.NOT_FOUND, "User not found.")
            roles = await fetch_all(
                conn, "SELECT role_id, role_key, kind, scope_attributes FROM iam.f_effective_roles(:uid)",
                {"uid": user["id"]},
            )
            groups = [g["display_name"] for g in await fetch_all(
                conn, "SELECT g.display_name FROM iam.idp_group_members m JOIN iam.idp_groups g ON g.id = m.group_id "
                      "WHERE m.user_id = :uid ORDER BY g.display_name", {"uid": user["id"]})]
            subject = {"kind": "person", "id": str(user["id"]), "display_name": user["display_name"]}
        else:
            found = await fetch_one(conn, "SELECT id, key, name, kind FROM iam.roles WHERE key = :k", {"k": role})
            if found is None:
                raise TelaError(ErrorCode.NOT_FOUND, "Role not found.")
            roles = [{"role_id": found["id"], "role_key": found["key"], "kind": found["kind"], "scope_attributes": {}}]
            subject = {"kind": "role", "id": str(found["id"]), "display_name": found["name"], "key": found["key"]}
        role_ids = [str(r["role_id"]) for r in roles]
        permissions = sorted({p["permission_key"] for p in await fetch_all(
            conn, "SELECT DISTINCT permission_key FROM iam.role_permissions WHERE role_id = ANY(CAST(:ids AS uuid[]))",
            {"ids": role_ids})}) if role_ids else []
        attrs: dict[str, dict[str, list[str]]] = {}
        for r in roles:
            for k, v in (r["scope_attributes"] or {}).items():
                attrs.setdefault(r["role_key"], {}).setdefault(k, [])
                attrs[r["role_key"]][k] = sorted({*attrs[r["role_key"]][k], *(v if isinstance(v, list) else [v])})
        policies = await fetch_all(
            conn,
            """
            SELECT r.key AS role_key, p.entity_key, p.all_rows, p.predicate, p.description
            FROM iam.scope_policies p JOIN iam.roles r ON r.id = p.role_id
            WHERE p.connection_id = CAST(:cid AS uuid) AND p.is_active AND p.role_id = ANY(CAST(:roles AS uuid[]))
            """,
            {"cid": connection_id, "roles": role_ids},
        )
        rules = await fetch_all(
            conn,
            "SELECT model_field_key, effect FROM iam.field_policies "
            "WHERE connection_id = CAST(:cid AS uuid) AND role_id = ANY(CAST(:roles AS uuid[]))",
            {"cid": connection_id, "roles": role_ids},
        )
        try:
            scope = combine_policies(
                connection_id=connection_id, policies=policies, field_rules=rules, scope_attributes=attrs,
                links=await scope_links(conn, connection_id),
            )
            resolved: dict[str, Any] = {
                "summary": scope.summary,
                "entities": {
                    k: {"all_rows": v.all_rows,
                        "alternatives": [[c.__dict__ for c in alt] for alt in v.alternatives],
                        **({"via": v.via.__dict__} if v.via else {})}
                    for k, v in scope.entities.items()
                },
                "hidden_fields": sorted(scope.hidden_fields),
                "masked_fields": sorted(scope.masked_fields),
                "error": None,
            }
        except TelaError as exc:
            resolved = {"summary": None, "entities": {}, "hidden_fields": [], "masked_fields": [],
                        "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.scope.previewed",
            object_type="user" if user_id else "role", object_id=subject["id"], outcome="success",
            actor_id=principal.user_id, correlation_id=_rid(request), details={"connection_id": connection_id},
        )
        granted = await grants_for(conn, subject["id"], connection_id) if user_id is not None else []
    system = [r["role_key"] for r in roles if r["kind"] == "system"]
    level = next((lvl for lvl in ("admin", "creator", "viewer") if lvl in system), None)
    rows_in_scope = await _rows_in_scope(request, principal, connection_id, scope if resolved["error"] is None
                                         else None)
    return {
        "subject": subject,
        # Kept for the first preview screen (FEAT-002); ``subject`` says whether it is a person or a role.
        "user": {"id": subject["id"], "display_name": subject["display_name"]},
        "groups": groups,
        "permissions": permissions,
        # Per-person grants on this connection (US-161), apart from what the roles give.
        "grants": granted,
        "level": level,
        "audience_roles": sorted(r["role_key"] for r in roles if r["kind"] == "audience"),
        "scope": resolved,
        "rows_in_scope": rows_in_scope,
    }


async def _rows_in_scope(request: Request, principal: Principal, connection_id: str,
                         scope: Any) -> dict[str, int] | None:
    from tela_api.model.store import current_version_id, load_snapshot
    from tela_api.query.service import QueryContext

    if scope is None:
        return None
    async with tenant_tx(principal.tenant_id) as conn:
        version_id = await current_version_id(conn, connection_id)
        if version_id is None:
            return None
        snap = await load_snapshot(conn, principal.tenant_id, version_id)
    service = request.app.state.query_service
    counts: dict[str, int] = {}
    for entity in sorted(scope.entities):
        if snap.entity(entity) is None:
            continue
        counts[entity] = await service.rows_in_scope(
            snapshot=snap, scope=scope, connection_id=connection_id, entity=entity, principal=principal,
            ctx=QueryContext(surface="preview", request_id=_rid(request), use_cache=False))
    return counts


# --------------------------------------------------------------------------- field policies


class FieldRuleIn(BaseModel):
    field_ref: str = Field(pattern=r"^(field:)?[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
    hidden_for: list[str] = Field(min_length=1, description="Role keys, e.g. ['cio']")
    effect: str = Field(default="hide", pattern="^(hide|mask)$")


class FieldPoliciesPut(BaseModel):
    connection_id: str
    rules: list[FieldRuleIn] = Field(max_length=1000)


@router.get("/field-policies")
async def get_field_policies(
    connection_id: str, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT f.model_field_key, f.effect, array_agg(r.key ORDER BY r.key) AS roles
            FROM iam.field_policies f JOIN iam.roles r ON r.id = f.role_id
            WHERE f.connection_id = CAST(:cid AS uuid)
            GROUP BY f.model_field_key, f.effect ORDER BY f.model_field_key
            """,
            {"cid": connection_id},
        )
    return {
        "connection_id": connection_id,
        "rules": [{"field_ref": f"field:{r['model_field_key']}", "hidden_for": list(r["roles"]), "effect": r["effect"]}
                  for r in rows],
    }


@router.put("/field-policies")
async def put_field_policies(
    body: FieldPoliciesPut, request: Request, principal: Principal = Depends(require(Perm.POLICIES_MANAGE))
) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        await _connection_exists(conn, body.connection_id)
        roles = {r["key"]: r for r in await fetch_all(conn, "SELECT id, key FROM iam.roles")}
        await conn.execute(
            text("DELETE FROM iam.field_policies WHERE connection_id = CAST(:cid AS uuid)"),
            {"cid": body.connection_id},
        )
        for rule in body.rules:
            key = rule.field_ref.removeprefix("field:")
            for role_key in rule.hidden_for:
                role = roles.get(role_key)
                if role is None:
                    raise TelaError(ErrorCode.VALIDATION_ERROR, f"Unknown role '{role_key}'.")
                await conn.execute(
                    text(
                        "INSERT INTO iam.field_policies (id, tenant_id, role_id, connection_id, model_field_key, "
                        "effect, created_by) VALUES (:id, :t, :r, CAST(:c AS uuid), :k, :e, :by) "
                        "ON CONFLICT (role_id, connection_id, model_field_key) DO UPDATE SET effect = EXCLUDED.effect"
                    ),
                    {"id": new_id(), "t": principal.tenant_id, "r": role["id"], "c": body.connection_id,
                     "k": key, "e": rule.effect, "by": principal.user_id},
                )
        await audit.record(
            conn, tenant_id=principal.tenant_id, action="access.field_policy.changed", object_type="connection",
            object_id=body.connection_id, outcome="success", actor_id=principal.user_id,
            correlation_id=_rid(request), details={"rule_count": len(body.rules)},
        )
        await _invalidate_results(request, conn, body.connection_id)
    _invalidate(request, principal.tenant_id)
    return await get_field_policies(body.connection_id, principal)
