"""SCIM 2.0 provisioning from Microsoft Entra (US-118; FEAT-004; API-003; REQ-007, REQ-008; TEST-005).

``/scim/v2`` with a per-tenant provisioning bearer token (``iam.scim_tokens``, hash only):

* ``ServiceProviderConfig``, ``ResourceTypes``, ``Schemas`` for discovery;
* ``Users``: list (filters ``userName eq`` and ``externalId eq``, ``startIndex``/``count``), create, read, replace,
  patch, delete; ``Groups``: list (``displayName eq``, ``externalId eq``; ``excludedAttributes=members``), create,
  read, patch, delete.

Behaviour:

* Idempotent on ``externalId`` (the Entra object id). A retried create updates the same person, never a second
  one. A person who signed in before provisioning was turned on is matched by the Entra object id their sign-in
  recorded (``external_identities.directory_object_id``) — never by e-mail alone (S03 §5.2).
* ``active:false`` → ``suspended``; DELETE → ``deprovisioned``. Both end sessions: tokens issued before
  ``sessions_revoked_at`` are refused and a ``revoke_sessions`` task ends the Keycloak sessions. The person's row,
  content and audit history stay (S03 §7); a deprovisioned person reads as 404 here and loses group memberships.
* Group membership changes recompute the affected people's roles and drop their cached principals, so role and
  scope changes apply on the next request.
* Deleting a group removes its memberships; its role mappings stay so the Admin sees what it used to grant.
* Every call is written to ``ops.provisioning_log`` (no payloads) and changes are audited as ``scim.*``.
* 600 requests per minute per tenant (``scim_rate_per_minute``), counted in Redis when configured.

Errors use the SCIM error schema (``urn:ietf:params:scim:api:messages:2.0:Error``) with ``scimType`` where RFC 7644
defines one (``uniqueness``, ``invalidFilter``, ``invalidValue``, ``invalidSyntax``).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.core.db import fetch_all, fetch_one, tenant_tx
from tela_api.identity import grants, provisioning
from tela_common.ids import new_id
from tela_common.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["scim"], include_in_schema=False)

SCIM_JSON = "application/scim+json"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
MAX_COUNT = 200
DEFAULT_COUNT = 100

_FILTER = re.compile(r'^\s*([A-Za-z.]+)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE)
_EMAIL_VALUE_PATH = re.compile(r"^emails(\[.*\])?\.value$", re.IGNORECASE)
_MEMBER_PATH = re.compile(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', re.IGNORECASE)


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.scim_type, self.headers = status, detail, scim_type, headers


def _resp(body: dict[str, Any], status: int = 200, location: str | None = None) -> JSONResponse:
    headers = {"Location": location} if location else None
    return JSONResponse(body, status_code=status, media_type=SCIM_JSON, headers=headers)


async def _write_log(tenant_id: str, method: str, resource: str, resource_id: str | None, operation: str,
                     status: int, detail: str | None = None, conn: AsyncConnection | None = None) -> None:
    params = {"id": new_id(), "t": tenant_id, "m": method, "r": resource, "rid": resource_id, "op": operation,
              "s": status, "o": "ok" if status < 400 else "error", "d": (detail or None) and detail[:500]}
    sql = text("INSERT INTO ops.provisioning_log (id, tenant_id, method, resource, resource_id, operation, status, "
               "outcome, detail) VALUES (:id, :t, :m, :r, CAST(:rid AS uuid), :op, :s, :o, :d)")
    if conn is not None:
        await conn.execute(sql, params)
        return
    async with tenant_tx(tenant_id) as own:
        await own.execute(sql, params)


def _resource_of(path: str) -> str:
    for name in ("Users", "Groups"):
        if f"/{name}" in path:
            return name[:-1]
    return "Discovery"


async def scim_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ScimError)
    tenant_id = getattr(request.state, "scim_tenant", None)
    if tenant_id and exc.status != 429:
        try:
            await _write_log(tenant_id, request.method, _resource_of(request.url.path), None, "error", exc.status,
                             exc.detail)
        except Exception:  # noqa: BLE001 - the log must never hide the real error
            log.warning("scim.log_failed")
    body: dict[str, Any] = {"schemas": [ERROR_SCHEMA], "status": str(exc.status), "detail": exc.detail}
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return JSONResponse(body, status_code=exc.status, media_type=SCIM_JSON, headers=exc.headers)


# --------------------------------------------------------------------------- auth and rate limit


class RateLimiter:
    """Fixed one-minute window per tenant; Redis when the app has it, otherwise this process."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._local: dict[str, tuple[int, int]] = {}

    async def retry_after(self, redis: Any, tenant_id: str) -> int | None:
        now = time.time()
        window = int(now // 60)
        if redis is not None:
            key = f"tela:scim:rate:{tenant_id}:{window}"
            count = int(await redis.incr(key))
            if count == 1:
                await redis.expire(key, 70)
        else:
            w, n = self._local.get(tenant_id, (window, 0))
            count = (n if w == window else 0) + 1
            self._local[tenant_id] = (window, count)
        return max(1, 60 - int(now) % 60) if count > self.per_minute else None


@dataclass(frozen=True)
class Ctx:
    tenant_id: str
    request: Request

    @property
    def request_id(self) -> str | None:
        return getattr(self.request.state, "request_id", None)

    @property
    def base(self) -> str:
        return str(self.request.base_url).rstrip("/") + "/scim/v2"


async def scim_context(request: Request) -> Ctx:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ScimError(401, "A provisioning bearer token is required.", headers={"WWW-Authenticate": "Bearer"})
    async with tenant_tx(None) as conn:
        row = await fetch_one(conn, "SELECT tenant_id, status, token_id FROM iam.f_tenant_for_scim_token(:h)",
                              {"h": provisioning.token_hash(token.strip())})
    if row is None or row["status"] != "active":
        raise ScimError(401, "The provisioning token is not valid.", headers={"WWW-Authenticate": "Bearer"})
    tenant_id = str(row["tenant_id"])
    request.state.scim_tenant = tenant_id
    app = request.app
    limiter: RateLimiter | None = getattr(app.state, "scim_limiter", None)
    if limiter is None:
        limiter = app.state.scim_limiter = RateLimiter(app.state.settings.scim_rate_per_minute)
    wait = await limiter.retry_after(getattr(app.state, "redis", None), tenant_id)
    if wait is not None:
        raise ScimError(429, "Too many provisioning requests. Slow down.", headers={"Retry-After": str(wait)})
    async with tenant_tx(tenant_id) as conn:
        await conn.execute(text("UPDATE iam.scim_tokens SET last_used_at = now() WHERE id = :id AND "
                                "(last_used_at IS NULL OR last_used_at < now() - interval '1 minute')"),
                           {"id": row["token_id"]})
    return Ctx(tenant_id, request)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError as exc:
        raise ScimError(400, "The request body is not valid JSON.", "invalidSyntax") from exc
    if not isinstance(body, dict):
        raise ScimError(400, "The request body must be a JSON object.", "invalidSyntax")
    return body


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"  # Entra sends "False" as a string in PATCH
    raise ScimError(400, "active must be true or false.", "invalidValue")


def _text(value: Any, name: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str | int):
        raise ScimError(400, f"{name} must be a string.", "invalidValue")
    out = str(value).strip()
    if len(out) > limit:
        raise ScimError(400, f"{name} is longer than {limit} characters.", "invalidValue")
    return out or None


def _paging(request: Request) -> tuple[int, int]:
    try:
        start = max(1, int(request.query_params.get("startIndex", "1")))
        count = min(max(0, int(request.query_params.get("count", str(DEFAULT_COUNT)))), MAX_COUNT)
    except ValueError as exc:
        raise ScimError(400, "startIndex and count must be integers.", "invalidValue") from exc
    return start, count


def parse_filter(raw: str | None, allowed: dict[str, str]) -> tuple[str, str] | None:
    """``attr eq "value"`` for the allowed attributes only → (SQL condition, value)."""
    if raw is None or not raw.strip():
        return None
    m = _FILTER.match(raw)
    if not m or m.group(1).lower() not in allowed:
        raise ScimError(400, f"Only these filters are supported: {', '.join(sorted(allowed))} eq \"value\".",
                        "invalidFilter")
    return allowed[m.group(1).lower()], m.group(2).replace('\\"', '"').replace("\\\\", "\\")


def _list(resources: list[dict[str, Any]], total: int, start: int) -> dict[str, Any]:
    return {"schemas": [LIST_SCHEMA], "totalResults": total, "startIndex": start, "itemsPerPage": len(resources),
            "Resources": resources}


def _patch_ops(body: dict[str, Any]) -> list[tuple[str, str | None, Any]]:
    ops = body.get("Operations")
    if not isinstance(ops, list) or not ops:
        raise ScimError(400, "Operations must be a non-empty list.", "invalidSyntax")
    out = []
    for op in ops:
        if not isinstance(op, dict) or str(op.get("op", "")).lower() not in ("add", "replace", "remove"):
            raise ScimError(400, "Each operation needs op add, replace or remove.", "invalidSyntax")
        path = op.get("path")
        out.append((str(op["op"]).lower(), str(path).strip() if path else None, op.get("value")))
    return out


# --------------------------------------------------------------------------- discovery


@router.get("/ServiceProviderConfig")
async def service_provider_config(ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    return _resp({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://tela.prodian.co.in/docs/scim",
        "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_COUNT}, "changePassword": {"supported": False},
        "sort": {"supported": False}, "etag": {"supported": False},
        "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Provisioning token", "primary": True,
                                   "description": "Per-tenant bearer token from Admin → Provisioning"}],
        "meta": {"resourceType": "ServiceProviderConfig", "location": f"{ctx.base}/ServiceProviderConfig"},
    })


def _resource_types(base: str) -> list[dict[str, Any]]:
    return [
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": name, "name": name,
         "endpoint": f"/{name}s", "schema": schema,
         "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/{name}"}}
        for name, schema in (("User", USER_SCHEMA), ("Group", GROUP_SCHEMA))
    ]


@router.get("/ResourceTypes")
async def resource_types(ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    items = _resource_types(ctx.base)
    return _resp(_list(items, len(items), 1))


def _attr(name: str, type_: str = "string", **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, "multiValued": False, "required": False, "mutability": "readWrite",
            "returned": "default", "uniqueness": "none", **extra}


def _schemas(base: str) -> list[dict[str, Any]]:
    user = [_attr("userName", required=True, uniqueness="server"), _attr("externalId"), _attr("displayName"),
            _attr("active", "boolean"),
            _attr("name", "complex", subAttributes=[_attr("givenName"), _attr("familyName"), _attr("formatted")]),
            _attr("emails", "complex", multiValued=True,
                  subAttributes=[_attr("value"), _attr("type"), _attr("primary", "boolean")])]
    group = [_attr("displayName", required=True), _attr("externalId"),
             _attr("members", "complex", multiValued=True, subAttributes=[_attr("value"), _attr("display")])]
    return [
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"], "id": sid, "name": name, "attributes": attrs,
         "meta": {"resourceType": "Schema", "location": f"{base}/Schemas/{sid}"}}
        for sid, name, attrs in ((USER_SCHEMA, "User", user), (GROUP_SCHEMA, "Group", group))
    ]


@router.get("/Schemas")
async def schemas(ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    items = _schemas(ctx.base)
    return _resp(_list(items, len(items), 1))


# --------------------------------------------------------------------------- users

USER_COLS = ("u.id, u.display_name, u.email::text AS email, u.status, u.scim_external_id, u.scim_user_name, "
             "u.scim_name, u.created_at, u.updated_at")


@dataclass
class UserIn:
    user_name: str
    external_id: str | None = None
    display_name: str | None = None
    name: dict[str, str] = field(default_factory=dict)
    email: str | None = None
    active: bool = True

    def display(self) -> str:
        given_family = " ".join(p for p in (self.name.get("givenName"), self.name.get("familyName")) if p)
        return (self.display_name or self.name.get("formatted") or given_family or self.user_name)[:200]


def _name(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ScimError(400, "name must be an object.", "invalidValue")
    return {k: v for k in ("givenName", "familyName", "formatted") if (v := _text(value.get(k), f"name.{k}", 200))}


def _email(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ScimError(400, "emails must be a list.", "invalidValue")
    entries = [e for e in value if isinstance(e, dict) and e.get("value")]
    chosen = (next((e for e in entries if e.get("primary") in (True, "true", "True")), None)
              or next((e for e in entries if str(e.get("type", "")).lower() == "work"), None)
              or (entries[0] if entries else None))
    return _text(chosen["value"], "emails.value", 320) if chosen else None


def parse_user(body: dict[str, Any]) -> UserIn:
    user_name = _text(body.get("userName"), "userName", 320)
    if not user_name:
        raise ScimError(400, "userName is required.", "invalidValue")
    return UserIn(user_name=user_name, external_id=_text(body.get("externalId"), "externalId", 200),
                  display_name=_text(body.get("displayName"), "displayName", 200), name=_name(body.get("name")),
                  email=_email(body.get("emails")), active=_as_bool(body.get("active", True)))


def _user_in(row: dict[str, Any]) -> UserIn:
    return UserIn(user_name=row["scim_user_name"] or row["email"] or str(row["id"]),
                  external_id=row["scim_external_id"], display_name=row["display_name"],
                  name=dict(row["scim_name"] or {}), email=row["email"], active=row["status"] in ("active", "pending"))


def apply_user_patch(current: UserIn, ops: list[tuple[str, str | None, Any]]) -> UserIn:
    """Apply PatchOp operations; attributes Tela does not keep (department, title…) are accepted and ignored."""
    data = replace(current, name=dict(current.name))

    def set_attr(path: str, value: Any, op: str) -> None:
        p = path.strip()
        if ":" in p and not p.lower().startswith("urn:ietf:params:scim:schemas:core:2.0:user:"):
            return  # an extension attribute
        p = p.split(":User:", 1)[-1] if ":" in p else p
        key = p.lower()
        removing = op == "remove"
        if key == "active":
            data.active = False if removing else _as_bool(value)
        elif key == "username":
            if removing or not _text(value, "userName", 320):
                raise ScimError(400, "userName cannot be removed.", "invalidValue")
            data.user_name = _text(value, "userName", 320) or data.user_name
        elif key == "displayname":
            data.display_name = None if removing else _text(value, "displayName", 200)
        elif key == "externalid":
            data.external_id = None if removing else _text(value, "externalId", 200)
        elif key == "name":
            data.name = {} if removing else _name(value)
        elif key.startswith("name."):
            sub = {"givenname": "givenName", "familyname": "familyName", "formatted": "formatted"}.get(key[5:])
            if sub:
                v = None if removing else _text(value, p, 200)
                if v:
                    data.name[sub] = v
                else:
                    data.name.pop(sub, None)
        elif key == "emails":
            data.email = None if removing else _email(value)
        elif _EMAIL_VALUE_PATH.match(p):
            data.email = None if removing else _text(value, "emails.value", 320)

    for op, path, value in ops:
        if path is None:
            if not isinstance(value, dict):
                raise ScimError(400, "An operation without a path needs an object value.", "invalidValue")
            for k, v in value.items():
                set_attr(k, v, op)
        else:
            set_attr(path, value, op)
    return data


def user_resource(row: dict[str, Any], base: str) -> dict[str, Any]:
    uid = str(row["id"])
    out: dict[str, Any] = {
        "schemas": [USER_SCHEMA], "id": uid, "userName": row["scim_user_name"] or row["email"] or uid,
        "displayName": row["display_name"], "active": row["status"] in ("active", "pending"),
        "meta": {"resourceType": "User", "created": _iso(row["created_at"]),
                 "lastModified": _iso(row["updated_at"]), "location": f"{base}/Users/{uid}"},
    }
    if row["scim_external_id"]:
        out["externalId"] = row["scim_external_id"]
    if row["scim_name"]:
        out["name"] = row["scim_name"]
    if row["email"]:
        out["emails"] = [{"value": row["email"], "type": "work", "primary": True}]
    return out


async def _user(conn: AsyncConnection, user_id: str) -> dict[str, Any]:
    uid = _uuid(user_id)
    row = await fetch_one(conn, f"SELECT {USER_COLS} FROM iam.users u WHERE u.id = CAST(:id AS uuid) "  # noqa: S608 - fixed columns and filters
                                "AND u.status <> 'deprovisioned'", {"id": uid}) if uid else None
    if row is None:
        raise ScimError(404, "User not found.")
    return row


@dataclass
class Effects:
    """What a change means beyond the row: sessions to end, caches to drop."""

    revoke_tasks: list[str] = field(default_factory=list)
    users: set[str] = field(default_factory=set)


async def _save_user(conn: AsyncConnection, ctx: Ctx, current: dict[str, Any] | None, data: UserIn,
                     fx: Effects) -> tuple[dict[str, Any], str]:
    """Insert or update a user from SCIM data; returns the stored row and the operation name."""
    uid = str(current["id"]) if current else new_id()
    if await fetch_one(conn, "SELECT 1 FROM iam.users WHERE lower(scim_user_name) = lower(:n) AND id <> :id "
                             "AND status <> 'deprovisioned'", {"n": data.user_name, "id": uid}):
        raise ScimError(409, "userName is already in use by another user.", "uniqueness")
    if data.external_id and await fetch_one(conn, "SELECT 1 FROM iam.users WHERE scim_external_id = :x "
                                                  "AND id <> :id", {"x": data.external_id, "id": uid}):
        raise ScimError(409, "externalId is already in use by another user.", "uniqueness")
    email = data.email or (data.user_name if "@" in data.user_name else None)
    if email:
        holder = await fetch_one(conn, "SELECT status FROM iam.users WHERE lower(email::text) = lower(:e) "
                                       "AND id <> :id", {"e": email, "id": uid})
        if holder is not None:
            if holder["status"] != "deprovisioned":
                raise ScimError(409, "That e-mail address belongs to another user.", "uniqueness")
            email = None  # a former user keeps their record; the new person is matched by externalId
    params = {"id": uid, "t": ctx.tenant_id, "dn": data.display(), "e": email, "x": data.external_id,
              "un": data.user_name, "nm": json.dumps(data.name)}
    if current is None:
        status = "active" if data.active else "suspended"
        await conn.execute(text(
            "INSERT INTO iam.users (id, tenant_id, display_name, email, status, scim_external_id, scim_user_name, "
            "scim_name, provisioned_by, sessions_revoked_at) VALUES (:id, :t, :dn, :e, :st, :x, :un, "
            "CAST(:nm AS jsonb), 'scim', CASE WHEN :st = 'suspended' THEN now() END)"), {**params, "st": status})
        operation = "create"
    else:
        await conn.execute(text(
            "UPDATE iam.users SET display_name = :dn, email = :e, scim_external_id = :x, scim_user_name = :un, "
            "scim_name = CAST(:nm AS jsonb) WHERE id = :id"), params)
        operation = await _set_active(conn, ctx, current, data.active, fx) or "update"
    row = await fetch_one(conn, f"SELECT {USER_COLS} FROM iam.users u WHERE u.id = :id", {"id": uid})  # noqa: S608 - fixed columns and filters
    assert row is not None
    return row, operation


async def _set_active(conn: AsyncConnection, ctx: Ctx, current: dict[str, Any], active: bool,
                      fx: Effects) -> str | None:
    uid, old = str(current["id"]), current["status"]
    if active and old in ("suspended", "deprovisioned"):
        await conn.execute(text("UPDATE iam.users SET status = 'active', deprovisioned_at = NULL WHERE id = :id"),
                           {"id": uid})
        fx.users.add(uid)
        return "reactivate"
    if not active and old == "deprovisioned":  # re-created by Entra as disabled: back, but without access
        await conn.execute(text("UPDATE iam.users SET status = 'suspended', deprovisioned_at = NULL WHERE id = :id"),
                           {"id": uid})
        fx.users.add(uid)
        return "update"
    if not active and old in ("active", "pending"):
        await conn.execute(text("UPDATE iam.users SET status = 'suspended', sessions_revoked_at = now() "
                                "WHERE id = :id"), {"id": uid})
        fx.revoke_tasks.append(await provisioning.enqueue_revoke(conn, ctx.tenant_id, uid))
        fx.users.add(uid)
        return "suspend"
    return None


async def _audit(conn: AsyncConnection, ctx: Ctx, action: str, object_type: str, object_id: str,
                 details: dict[str, Any] | None = None, severity: str = "normal") -> None:
    await audit.record(conn, tenant_id=ctx.tenant_id, action=action, object_type=object_type, object_id=object_id,
                       outcome="success", correlation_id=ctx.request_id, details=details or {},
                       severity="security" if severity == "security" else "normal")


async def _after(ctx: Ctx, fx: Effects) -> None:
    """After commit: drop cached principals and try the sign-outs now (reconcile retries failures)."""
    provisioning.invalidate_principals(ctx.request.app, fx.users)
    if fx.revoke_tasks:
        try:
            await provisioning.run_tasks(ctx.request.app, ctx.tenant_id, fx.revoke_tasks)
        except Exception:  # noqa: BLE001 - the task stays pending for scim_reconcile
            log.warning("scim.revoke_deferred", tasks=len(fx.revoke_tasks))


_USER_AUDIT = {"create": "scim.user.created", "update": "scim.user.updated", "suspend": "scim.user.suspended",
               "reactivate": "scim.user.reactivated"}


@router.get("/Users")
async def list_users(request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    flt = parse_filter(request.query_params.get("filter"),
                       {"username": "lower(u.scim_user_name) = lower(:v)", "externalid": "u.scim_external_id = :v"})
    start, count = _paging(request)
    where = "u.status <> 'deprovisioned'" + (f" AND {flt[0]}" if flt else "")
    params = {"v": flt[1] if flt else None, "off": start - 1, "lim": count}
    async with tenant_tx(ctx.tenant_id) as conn:
        total = await fetch_one(conn, f"SELECT count(*) AS n FROM iam.users u WHERE {where}", params)  # noqa: S608 - fixed columns and filters
        rows = await fetch_all(conn, f"SELECT {USER_COLS} FROM iam.users u WHERE {where} "  # noqa: S608 - fixed columns and filters
                                     "ORDER BY u.created_at, u.id OFFSET :off LIMIT :lim", params)
        await _write_log(ctx.tenant_id, "GET", "User", None, "list", 200, conn=conn)
    assert total is not None
    return _resp(_list([user_resource(r, ctx.base) for r in rows], int(total["n"]), start))


@router.post("/Users")
async def create_user(request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    data = parse_user(await _body(request))
    fx = Effects()
    async with tenant_tx(ctx.tenant_id) as conn:
        current = None
        if data.external_id:
            current = await fetch_one(conn, f"SELECT {USER_COLS} FROM iam.users u WHERE u.scim_external_id = :x",  # noqa: S608 - fixed columns and filters
                                      {"x": data.external_id})
            if current is None:  # signed in before provisioning: linked by the Entra object id, never by e-mail
                current = await fetch_one(
                    conn, f"SELECT {USER_COLS} FROM iam.users u JOIN iam.external_identities e ON e.user_id = u.id "  # noqa: S608 - fixed columns and filters
                          "WHERE e.directory_object_id = :x AND u.scim_external_id IS NULL LIMIT 1",
                    {"x": data.external_id})
        row, operation = await _save_user(conn, ctx, current, data, fx)
        if current is not None:
            await conn.execute(text("UPDATE iam.users SET provisioned_by = 'scim' WHERE id = :id"), {"id": row["id"]})
        await _audit(conn, ctx, _USER_AUDIT[operation], "user", str(row["id"]),
                     {"matched": current is not None, "active": data.active},
                     "security" if operation in ("suspend", "reactivate") else "normal")
        await _write_log(ctx.tenant_id, "POST", "User", str(row["id"]), operation, 201, conn=conn)
    await _after(ctx, fx)
    res = user_resource(row, ctx.base)
    return _resp(res, 201, res["meta"]["location"])


@router.get("/Users/{user_id}")
async def get_user(user_id: str, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    async with tenant_tx(ctx.tenant_id) as conn:
        row = await _user(conn, user_id)
    return _resp(user_resource(row, ctx.base))


async def _update_user(ctx: Ctx, user_id: str, method: str, build: Any) -> JSONResponse:
    fx = Effects()
    async with tenant_tx(ctx.tenant_id) as conn:
        current = await _user(conn, user_id)
        data = build(current)
        row, operation = await _save_user(conn, ctx, current, data, fx)
        fx.users.add(str(row["id"]))  # name and e-mail show from the principal too
        await _audit(conn, ctx, _USER_AUDIT[operation], "user", str(row["id"]), {"method": method},
                     "security" if operation in ("suspend", "reactivate") else "normal")
        await _write_log(ctx.tenant_id, method, "User", str(row["id"]), operation, 200, conn=conn)
    await _after(ctx, fx)
    return _resp(user_resource(row, ctx.base))


@router.put("/Users/{user_id}")
async def replace_user(user_id: str, request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    data = parse_user(await _body(request))
    return await _update_user(ctx, user_id, "PUT", lambda _current: data)


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    ops = _patch_ops(await _body(request))
    return await _update_user(ctx, user_id, "PATCH", lambda current: apply_user_patch(_user_in(current), ops))


@router.delete("/Users/{user_id}", status_code=204)
async def delete_user(user_id: str, ctx: Ctx = Depends(scim_context)) -> Response:
    fx = Effects()
    async with tenant_tx(ctx.tenant_id) as conn:
        current = await _user(conn, user_id)
        uid = str(current["id"])
        await conn.execute(text("UPDATE iam.users SET status = 'deprovisioned', deprovisioned_at = now(), "
                                "sessions_revoked_at = now() WHERE id = :id"), {"id": uid})
        groups = await fetch_all(conn, "DELETE FROM iam.idp_group_members WHERE user_id = :id RETURNING group_id",
                                 {"id": uid})
        await provisioning.recompute_roles(conn, [uid])
        removed_grants = await grants.remove_for_user(conn, ctx.tenant_id, uid, reason="scim",
                                                      correlation_id=ctx.request_id)
        fx.revoke_tasks.append(await provisioning.enqueue_revoke(conn, ctx.tenant_id, uid))
        fx.users.add(uid)
        owned = (await provisioning.owned_content(conn, uid))[0]["owned"]
        await _audit(conn, ctx, "scim.user.deprovisioned", "user", uid,
                     {"previous_status": current["status"], "memberships_removed": len(groups),
                      "grants_removed": removed_grants, "owned": owned},
                     "security")
        await _write_log(ctx.tenant_id, "DELETE", "User", uid, "deprovision", 204, conn=conn)
    await _after(ctx, fx)
    return Response(status_code=204)


# --------------------------------------------------------------------------- groups

GROUP_COLS = "g.id, g.external_group_id, g.display_name, g.source, g.created_at, g.updated_at"


async def _members(conn: AsyncConnection, group_id: str) -> list[dict[str, Any]]:
    return await fetch_all(conn, "SELECT u.id, u.display_name FROM iam.idp_group_members m JOIN iam.users u "
                                 "ON u.id = m.user_id WHERE m.group_id = :g AND u.status <> 'deprovisioned' "
                                 "ORDER BY u.display_name, u.id", {"g": group_id})


def group_resource(row: dict[str, Any], base: str, members: list[dict[str, Any]] | None) -> dict[str, Any]:
    gid = str(row["id"])
    out: dict[str, Any] = {
        "schemas": [GROUP_SCHEMA], "id": gid, "displayName": row["display_name"],
        "externalId": row["external_group_id"],
        "meta": {"resourceType": "Group", "created": _iso(row["created_at"]),
                 "lastModified": _iso(row["updated_at"]), "location": f"{base}/Groups/{gid}"},
    }
    if members is not None:
        out["members"] = [{"value": str(m["id"]), "display": m["display_name"], "$ref": f"{base}/Users/{m['id']}"}
                          for m in members]
    return out


async def _group(conn: AsyncConnection, group_id: str, *, lock: bool = False) -> dict[str, Any]:
    gid = _uuid(group_id)
    row = await fetch_one(conn, f"SELECT {GROUP_COLS} FROM iam.idp_groups g WHERE g.id = CAST(:id AS uuid) "  # noqa: S608 - fixed columns and filters
                                f"AND g.scim_deleted_at IS NULL{' FOR UPDATE' if lock else ''}",
                          {"id": gid}) if gid else None
    if row is None:
        raise ScimError(404, "Group not found.")
    return row


def _member_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    items = value if isinstance(value, list) else [value]
    out = set()
    for item in items:
        ref = item.get("value") if isinstance(item, dict) else item
        if ref is None or (uid := _uuid(str(ref))) is None:
            raise ScimError(400, "Each member needs the value of a user id.", "invalidValue")
        out.add(uid)
    return out


async def _change_members(conn: AsyncConnection, ctx: Ctx, group_id: str, *, add: set[str] | None = None,
                          remove: set[str] | None = None, replace_with: set[str] | None = None) -> dict[str, int]:
    """Apply membership changes; recompute roles of everyone affected. Unknown or former users are ignored."""
    existing = {str(r["user_id"]) for r in await fetch_all(
        conn, "SELECT user_id FROM iam.idp_group_members WHERE group_id = :g", {"g": group_id})}
    if replace_with is not None:
        add, remove = replace_with - existing, existing - replace_with
    add, remove = (add or set()) - existing, (remove or set()) & existing
    added: list[str] = []
    if add:
        valid = {str(r["id"]) for r in await fetch_all(
            conn, "SELECT id FROM iam.users WHERE id = ANY(CAST(:ids AS uuid[])) AND status <> 'deprovisioned'",
            {"ids": sorted(add)})}
        for uid in sorted(valid):
            await conn.execute(text(
                "INSERT INTO iam.idp_group_members (tenant_id, group_id, user_id, source) VALUES (:t, :g, :u, 'scim') "
                "ON CONFLICT (group_id, user_id) DO UPDATE SET source = 'scim', observed_at = now()"),
                {"t": ctx.tenant_id, "g": group_id, "u": uid})
            added.append(uid)
    if remove:
        await conn.execute(text("DELETE FROM iam.idp_group_members WHERE group_id = :g "
                                "AND user_id = ANY(CAST(:ids AS uuid[]))"), {"g": group_id, "ids": sorted(remove)})
    affected = sorted(set(added) | remove)
    changed = await provisioning.recompute_roles(conn, affected) if affected else []
    if affected:
        await _audit(conn, ctx, "scim.group.members_changed", "idp_group", group_id,
                     {"added": len(added), "removed": len(remove), "roles_changed": len(changed),
                      "ignored": len((add or set()) - set(added))})
    provisioning.invalidate_principals(ctx.request.app, affected)
    return {"added": len(added), "removed": len(remove)}


@router.get("/Groups")
async def list_groups(request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    flt = parse_filter(request.query_params.get("filter"),
                       {"displayname": "lower(g.display_name) = lower(:v)", "externalid": "g.external_group_id = :v"})
    start, count = _paging(request)
    excluded = {a.strip().lower() for a in request.query_params.get("excludedAttributes", "").split(",")}
    where = "g.scim_deleted_at IS NULL" + (f" AND {flt[0]}" if flt else "")
    params = {"v": flt[1] if flt else None, "off": start - 1, "lim": count}
    async with tenant_tx(ctx.tenant_id) as conn:
        total = await fetch_one(conn, f"SELECT count(*) AS n FROM iam.idp_groups g WHERE {where}", params)  # noqa: S608 - fixed columns and filters
        rows = await fetch_all(conn, f"SELECT {GROUP_COLS} FROM iam.idp_groups g WHERE {where} "  # noqa: S608 - fixed columns and filters
                                     "ORDER BY g.created_at, g.id OFFSET :off LIMIT :lim", params)
        out = [group_resource(r, ctx.base, None if "members" in excluded else await _members(conn, str(r["id"])))
               for r in rows]
        await _write_log(ctx.tenant_id, "GET", "Group", None, "list", 200, conn=conn)
    assert total is not None
    return _resp(_list(out, int(total["n"]), start))


@router.post("/Groups")
async def create_group(request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    body = await _body(request)
    name = _text(body.get("displayName"), "displayName", 200)
    if not name:
        raise ScimError(400, "displayName is required.", "invalidValue")
    external = _text(body.get("externalId"), "externalId", 200) or name
    members = _member_ids(body.get("members")) if "members" in body else None
    async with tenant_tx(ctx.tenant_id) as conn:
        # A group first seen in a token, registered by an Admin, or deleted earlier is adopted, not duplicated.
        row = await fetch_one(
            conn,
            f"""
            INSERT INTO iam.idp_groups AS g (id, tenant_id, external_group_id, display_name, source, last_synced_at)
            VALUES (:id, :t, :x, :n, 'scim', now())
            ON CONFLICT (tenant_id, external_group_id) DO UPDATE SET display_name = EXCLUDED.display_name,
              source = 'scim', scim_deleted_at = NULL, last_synced_at = now()
            RETURNING {GROUP_COLS}, (xmax = 0) AS inserted
            """,  # noqa: S608 - fixed columns
            {"id": new_id(), "t": ctx.tenant_id, "x": external, "n": name},
        )
        assert row is not None
        gid = str(row["id"])
        changes = await _change_members(conn, ctx, gid, replace_with=members) if members is not None else {}
        await _audit(conn, ctx, "scim.group.created" if row["inserted"] else "scim.group.adopted", "idp_group", gid,
                     {"external_group_id": external, **changes})
        await _write_log(ctx.tenant_id, "POST", "Group", gid, "create", 201, conn=conn)
        res = group_resource(row, ctx.base, await _members(conn, gid))
    return _resp(res, 201, res["meta"]["location"])


@router.get("/Groups/{group_id}")
async def get_group(group_id: str, request: Request, ctx: Ctx = Depends(scim_context)) -> JSONResponse:
    excluded = {a.strip().lower() for a in request.query_params.get("excludedAttributes", "").split(",")}
    async with tenant_tx(ctx.tenant_id) as conn:
        row = await _group(conn, group_id)
        members = None if "members" in excluded else await _members(conn, str(row["id"]))
    return _resp(group_resource(row, ctx.base, members))


@router.patch("/Groups/{group_id}")
async def patch_group(group_id: str, request: Request, ctx: Ctx = Depends(scim_context)) -> Response:
    ops = _patch_ops(await _body(request))
    async with tenant_tx(ctx.tenant_id) as conn:
        row = await _group(conn, group_id, lock=True)
        gid = str(row["id"])
        totals = {"added": 0, "removed": 0}
        renamed: dict[str, str] = {}

        async def members(op: str, path: str | None, value: Any) -> None:
            if op == "add":
                changed = await _change_members(conn, ctx, gid, add=_member_ids(value))
            elif op == "replace":
                changed = await _change_members(conn, ctx, gid, replace_with=_member_ids(value))
            elif path and (m := _MEMBER_PATH.match(path)):
                target = _uuid(m.group(1))
                changed = await _change_members(conn, ctx, gid, remove={target} if target else set())
            elif value is None:
                changed = await _change_members(conn, ctx, gid, replace_with=set())
            else:
                changed = await _change_members(conn, ctx, gid, remove=_member_ids(value))
            for k in totals:
                totals[k] += changed.get(k, 0)

        async def attribute(key: str, op: str, value: Any) -> None:
            if key == "displayname":
                name = None if op == "remove" else _text(value, "displayName", 200)
                if not name:
                    raise ScimError(400, "displayName cannot be removed.", "invalidValue")
                await conn.execute(text("UPDATE iam.idp_groups SET display_name = :n, last_synced_at = now() "
                                        "WHERE id = :id"), {"n": name, "id": gid})
                renamed["displayName"] = name
            elif key == "externalid":
                ext = None if op == "remove" else _text(value, "externalId", 200)
                if not ext:
                    raise ScimError(400, "externalId cannot be removed.", "invalidValue")
                if await fetch_one(conn, "SELECT 1 FROM iam.idp_groups WHERE external_group_id = :x AND id <> :id",
                                   {"x": ext, "id": gid}):
                    raise ScimError(409, "externalId is already in use by another group.", "uniqueness")
                await conn.execute(text("UPDATE iam.idp_groups SET external_group_id = :x WHERE id = :id"),
                                   {"x": ext, "id": gid})
                renamed["externalId"] = ext

        for op, path, value in ops:
            if path is None:
                if not isinstance(value, dict):
                    raise ScimError(400, "An operation without a path needs an object value.", "invalidValue")
                for k, v in value.items():
                    if k.lower() == "members":
                        await members(op, None, v)
                    else:
                        await attribute(k.lower(), op, v)
            elif path.lower() == "members" or _MEMBER_PATH.match(path):
                await members(op, path, value)
            else:
                await attribute(path.lower(), op, value)
        await conn.execute(text("UPDATE iam.idp_groups SET source = 'scim', last_synced_at = now() WHERE id = :id"),
                           {"id": gid})
        if renamed:
            await _audit(conn, ctx, "scim.group.updated", "idp_group", gid, {"changed": sorted(renamed)})
        await _write_log(ctx.tenant_id, "PATCH", "Group", gid, "members" if any(totals.values()) else "update", 204,
                         f"+{totals['added']} -{totals['removed']}" if any(totals.values()) else None, conn=conn)
    return Response(status_code=204)


@router.delete("/Groups/{group_id}", status_code=204)
async def delete_group(group_id: str, ctx: Ctx = Depends(scim_context)) -> Response:
    async with tenant_tx(ctx.tenant_id) as conn:
        row = await _group(conn, group_id, lock=True)
        gid = str(row["id"])
        changes = await _change_members(conn, ctx, gid, replace_with=set())
        await conn.execute(text("UPDATE iam.idp_groups SET scim_deleted_at = now() WHERE id = :id"), {"id": gid})
        mappings = await fetch_one(conn, "SELECT count(*) AS n FROM iam.group_role_mappings WHERE group_id = :id",
                                   {"id": gid})
        await _audit(conn, ctx, "scim.group.deleted", "idp_group", gid,
                     {"members_removed": changes["removed"], "mappings_kept": int(mappings["n"]) if mappings else 0})
        await _write_log(ctx.tenant_id, "DELETE", "Group", gid, "delete", 204, conn=conn)
    return Response(status_code=204)
