"""Dataverse (Dynamics 365 / TPG) adapter (US-110 TDS, US-111 Web API; API-066, API-067; ADR-013; RISK-004).

Built against a fake environment (``tests/fakes/dataverse_fake.py``) because DEC-09 access is pending; the
calls follow Microsoft's documented shapes and must be confirmed by the US-058 spike on a real environment.

**Authentication.** An Entra ID app registration (tenant ID, client ID, client secret from the secrets store)
gets a client-credentials token from MSAL for ``{environment}/.default``. The TDS path injects it into ODBC
Driver 18 with ``SQL_COPT_SS_ACCESS_TOKEN`` (Encrypt on, port 1433 of the environment host); the Web API path
sends it as a bearer token.

**Read-only.** The application user (``systemusers`` with this ``applicationid``) must hold only roles whose
privileges read: any Create, Write, Delete, Append, AppendTo, Assign or Share privilege, or the System
Administrator / Customizer roles, makes the account unsafe. The TDS endpoint is read-only by design and the
adapter still accepts only one SELECT; the Web API path has no code that sends anything but GET.

**Metadata.** ``EntityDefinitions`` for the configured tables: logical and display names, attribute types
(DateOnly dates as ``date``), lookups as links (``ManyToOneRelationships``), and choice options. Each choice
column ``x`` also appears as ``xname`` (its label), as on the TDS endpoint.

**Web API execution.** The API compiles a spec to OData ``$apply`` / ``$filter`` or FetchXML; the runner sends
GETs, follows ``@odata.nextLink`` up to the row cap, maps choice codes to labels, merges a groups query
(so a group with no matching rows still shows 0, as SQL does) and sorts. A 429 is retried at most twice
after its ``Retry-After``; then MSG-025.

**Development seam.** The fake TDS endpoint is a SQL Server container, which cannot validate Entra tokens:
outside production only, ``descriptor.dev_tds`` (``host,port`` and a SQL login in the secret) replaces token
injection on the ODBC connection; the token is still acquired from the (fake) authority. Production never
reads it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import struct
import time
from dataclasses import dataclass
from typing import Any

import pyodbc

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_runner.adapters.base import Catalog, ConnectionInfo, Limits, ReadOnlyVerdict, ResultSet, TestResult
from tela_runner.adapters.dbapi import RawObject, build_catalog, jsonable
from tela_runner.adapters.sqlserver import ODBC_DRIVER, SqlServerAdapter, odbc_value

log = get_logger(__name__)

SQL_COPT_SS_ACCESS_TOKEN = 1256
API = "/api/data/v9.2"
AUTHORITY = "https://login.microsoftonline.com"
WRITE_PRIVILEGES = ("prvCreate", "prvWrite", "prvDelete", "prvAppend", "prvAssign", "prvShare")
ADMIN_ROLES = ("System Administrator", "System Customizer")
MSG_BUSY = "Source is busy, retrying…"  # MSG-025
MSG_BUSY_FINAL = "Source unavailable — Retry."  # MSG-025
MSG_TDS_OFF = "The TDS endpoint is off for this environment. Switch the connection to Web API mode."
TYPE_NAMES = {"String": "nvarchar", "Memo": "nvarchar", "EntityName": "nvarchar", "Integer": "int",
              "BigInt": "bigint", "Decimal": "decimal", "Double": "float", "Money": "money",
              "Boolean": "bit", "Uniqueidentifier": "uniqueidentifier", "Lookup": "uniqueidentifier",
              "Owner": "uniqueidentifier", "Customer": "uniqueidentifier", "State": "int", "Status": "int",
              "Picklist": "int"}
CHOICE_CASTS = {"State": "Microsoft.Dynamics.CRM.StateAttributeMetadata",
                "Status": "Microsoft.Dynamics.CRM.StatusAttributeMetadata",
                "Picklist": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata"}


def token_attribute(token: str) -> dict[int, bytes]:
    """ODBC pre-connect attribute carrying an Entra access token (UTF-16-LE, length-prefixed)."""
    raw = token.encode("utf-16-le")
    return {SQL_COPT_SS_ACCESS_TOKEN: struct.pack(f"<I{len(raw)}s", len(raw), raw)}


def unsafe_privileges(roles: list[dict[str, Any]], privileges: dict[str, list[str]]) -> list[str]:
    reasons = []
    for role in roles:
        name = str(role.get("name") or role.get("roleid"))
        if name in ADMIN_ROLES:
            reasons.append(f"application user has the {name} role")
        for priv in privileges.get(str(role.get("roleid")), []):
            if priv.startswith(WRITE_PRIVILEGES):
                reasons.append(f"role {name} grants {priv}")
    return list(dict.fromkeys(reasons))


class _RoutingClient:
    """An MSAL ``http_client`` that sends login.microsoftonline.com requests to a development fake."""

    def __init__(self, route_to: str) -> None:
        import httpx

        route = route_to.rstrip("/")
        if not route.startswith(("http://", "https://")):
            route = f"https://{route}"
        self.route_to = route
        self.http = httpx.Client(timeout=20)

    def _url(self, url: str) -> str:
        return self.route_to + url[len(AUTHORITY):] if url.startswith(AUTHORITY) else url

    def get(self, url: str, params: Any = None, headers: Any = None, **kw: Any) -> Any:
        return self.http.get(self._url(url), params=params, headers=headers, timeout=kw.get("timeout", 20))

    def post(self, url: str, params: Any = None, data: Any = None, headers: Any = None, **kw: Any) -> Any:
        return self.http.post(self._url(url), params=params, data=data, headers=headers,
                              timeout=kw.get("timeout", 20))

    def close(self) -> None:
        self.http.close()


class MsalTokens:
    def __init__(self, route_to: str | None = None) -> None:
        self.route_to = route_to
        self._apps: dict[str, Any] = {}

    @staticmethod
    def _cache_key(tenant_id: str, client_id: str, secret: str) -> str:
        return hashlib.sha256(f"{tenant_id}|{client_id}|{secret}".encode()).hexdigest()

    def forget(self, tenant_id: str, client_id: str, secret: str) -> None:
        """Drop a cached token (MSAL keeps one until it expires); the next call asks Entra ID again."""
        self._apps.pop(self._cache_key(tenant_id, client_id, secret), None)

    def acquire(self, tenant_id: str, client_id: str, secret: str, resource: str) -> str:
        import msal

        key = self._cache_key(tenant_id, client_id, secret)
        app = self._apps.get(key)
        if app is None:
            kwargs: dict[str, Any] = {}
            if self.route_to:
                kwargs = {"http_client": _RoutingClient(self.route_to), "instance_discovery": False}
            try:
                app = msal.ConfidentialClientApplication(client_id, authority=f"{AUTHORITY}/{tenant_id}",
                                                         client_credential=secret, **kwargs)
            except Exception as exc:
                raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "Entra ID could not be reached.") from exc
            self._apps[key] = app
        try:
            result = app.acquire_token_for_client(scopes=[f"{resource.rstrip('/')}/.default"])
        except Exception as exc:
            self._apps.pop(key, None)
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "Entra ID could not be reached.") from exc
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The app registration was refused by Entra ID.",
                            {"auth_failed": True, "aad_error": (result or {}).get("error")})
        return str(token)


@dataclass
class _Meta:
    entity_set: str
    primary_id: str
    columns: dict[str, dict[str, Any]]  # logical name -> {type, options, virtual_of}
    fetched: float


MISSING_PRIVILEGE = re.compile(r"is missing prvRead\w* privilege.*?for entity \\?'(?P<table>\w+)\\?'"
                               r"(?: \(LocalizedName=\\?'(?P<label>[^'\\]+)\\?'\))?", re.S)


class DataverseAdapter(SqlServerAdapter):
    engine = "dataverse"
    # The TDS endpoint drops an empty-string parameter ("expects the parameter '@P1', which was not supplied"),
    # so blank-text checks (``<> ''``) carry the constant instead.
    empty_literal = "N''"
    read_only_checks = ("application user roles grant read privileges only (no create, write, delete, "
                        "append, assign or share)", "TDS endpoint is read-only; one SELECT only",
                        "Web API calls are GET only")

    def __init__(self, *, tokens: MsalTokens | None = None, http: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        route = os.environ.get("TELA_DATAVERSE_AUTHORITY_ROUTE") if self.allow_insecure_tls else None
        self.tokens = tokens or MsalTokens(route)
        self._http = http
        self._meta: dict[str, _Meta] = {}

    # ------------------------------------------------------------------ auth and HTTP

    def _mode(self, info: ConnectionInfo) -> str:
        return str(info.descriptor.get("mode") or "tds")

    def _env(self, info: ConnectionInfo) -> str:
        url = str(info.descriptor.get("environment_url") or "").rstrip("/")
        if not url.startswith("https://") and not (self.allow_insecure_tls and url.startswith("http://")):
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The environment URL must use https.")
        return url

    def _token_sync(self, info: ConnectionInfo) -> str:
        d, c = info.descriptor, info.credentials
        return self.tokens.acquire(str(d.get("tenant_id")), str(c.get("client_id") or d.get("client_id")),
                                   str(c.get("client_secret") or ""), self._env(info))

    async def _token(self, info: ConnectionInfo) -> str:
        return await asyncio.to_thread(self._token_sync, info)

    def _client(self) -> Any:
        import httpx

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=20)
        return self._http

    async def _get(self, info: ConnectionInfo, path: str, params: dict[str, str] | None = None, *,
                   url: str | None = None, page_size: int = 5000) -> dict[str, Any]:
        """GET only (API-067): honour 429 Retry-After at most twice, then MSG-025."""
        token = await self._token(info)
        refreshed = False
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "OData-MaxVersion": "4.0",
                   "OData-Version": "4.0", "Prefer": f'odata.maxpagesize={page_size},odata.include-annotations='
                                                     '"OData.Community.Display.V1.FormattedValue"'}
        target = url or f"{self._env(info)}{API}{path}"
        for attempt in range(3):
            try:
                resp = await self._client().get(target, params=None if url else params, headers=headers)
            except Exception as exc:
                raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.") from exc
            if resp.status_code == 429:
                if attempt == 2:
                    raise TelaError(ErrorCode.SOURCE_BUSY, MSG_BUSY_FINAL, {"retries": 2})
                wait = float(resp.headers.get("Retry-After") or 1)
                log.info("runner.dataverse_throttled", retry_after=wait, attempt=attempt + 1)
                await asyncio.sleep(min(max(wait, 0.0), 10.0))
                continue
            if resp.status_code == 401 and not refreshed:  # a revoked or rotated token: ask Entra ID once more
                d, c = info.descriptor, info.credentials
                self.tokens.forget(str(d.get("tenant_id")), str(c.get("client_id") or d.get("client_id")),
                                   str(c.get("client_secret") or ""))
                headers["Authorization"] = f"Bearer {await self._token(info)}"
                refreshed = True
                resp = await self._client().get(target, params=None if url else params, headers=headers)
            if resp.status_code in (401, 403):
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "Dataverse refused the request.",
                                {"status": resp.status_code})
            if resp.status_code >= 400:
                log.warning("runner.dataverse_failed", status=resp.status_code)
                raise TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.",
                                {"status": resp.status_code})
            data: dict[str, Any] = resp.json()
            return data
        raise TelaError(ErrorCode.SOURCE_BUSY, MSG_BUSY_FINAL)

    # ------------------------------------------------------------------ TDS connection

    def _connect(self, info: ConnectionInfo) -> Any:
        d, c = info.descriptor, info.credentials
        dev = d.get("dev_tds") if self.allow_insecure_tls else None
        if dev:  # development fake only: a SQL Server container standing in for the TDS endpoint
            host, _, port = str(dev).partition(",")
            attrs = [f"Driver={{{ODBC_DRIVER}}}", f"Server={odbc_value(f'{host},{port or 1433}')}", "Encrypt=yes",
                     "TrustServerCertificate=yes", "APP=tela-connector-runner", "Database=tpg_dataverse",
                     f"UID={odbc_value(str(c.get('tds_username') or ''))}",
                     f"PWD={odbc_value(str(c.get('tds_password') or ''))}"]
            self._token_sync(info)  # the Entra flow still runs against the (fake) authority
            return self._configure(pyodbc.connect(";".join(attrs), timeout=10, autocommit=False))
        host = str(d.get("host") or "")
        attrs = [f"Driver={{{ODBC_DRIVER}}}", f"Server={odbc_value(f'{host},1433')}", "Encrypt=yes",
                 "TrustServerCertificate=no", "APP=tela-connector-runner"]
        token = self._token_sync(info)
        return self._configure(pyodbc.connect(";".join(attrs), attrs_before=token_attribute(token), timeout=10,
                                              autocommit=False))

    def _classify(self, exc: Exception) -> TelaError:
        # A missing table privilege comes back as a general error (42000) whose text names it: say which table and
        # what the Admin grants, without the principal's ids.
        text = str(exc.args[1]) if isinstance(exc, pyodbc.Error) and len(exc.args) > 1 else ""
        missing = MISSING_PRIVILEGE.search(text)
        if missing:
            table = f"{missing.group('label')} ({missing.group('table')})" if missing.group("label") \
                else missing.group("table")
            return TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED,
                             f"Dataverse refused to read the table {table}: the Tela application user's security "
                             f"role has no Read privilege on it. Add Read for this table to that role, or leave the "
                             f"table out of the model.", {"table": missing.group("table")})
        return super()._classify(exc)

    def _link_failed(self, exc: Exception) -> bool:
        # The endpoint can drop a new session on its first statement (communication link failure, 08S01).
        return isinstance(exc, pyodbc.Error) and bool(exc.args) and exc.args[0] == "08S01"

    def _begin(self, cur: Any, limits: Limits) -> bool:
        cur.connection.timeout = max(1, math.ceil(limits.timeout_ms / 1000))
        return True  # the TDS endpoint is read-only by design; the statement backstop still applies

    def _privileges(self, conn: Any, info: ConnectionInfo) -> list[str]:
        return []  # checked through the Web API (verify_read_only)

    @staticmethod
    def _configure(conn: Any) -> Any:
        return conn

    def _key_material(self, info: ConnectionInfo) -> str:
        return json.dumps([info.descriptor, sorted(info.credentials.items())], sort_keys=True, default=str)

    # ------------------------------------------------------------------ RULE-001

    async def verify_read_only(self, info: ConnectionInfo, *, force: bool = False) -> ReadOnlyVerdict:
        key = self._key(info)
        hit = self._verdicts.get(key)
        if hit and not force and time.monotonic() - hit[0] < self.read_only_ttl_s:
            return hit[1]
        client_id = str(info.credentials.get("client_id") or info.descriptor.get("client_id"))
        users = (await self._get(info, "/systemusers", {"$filter": f"applicationid eq {client_id}",
                                                        "$select": "systemuserid"})).get("value") or []
        reasons: list[str] = []
        if not users:
            reasons.append("no application user exists for this app registration")
        else:
            uid = users[0]["systemuserid"]
            roles = (await self._get(info, f"/systemusers({uid})/systemuserroles_association",
                                     {"$select": "roleid,name"})).get("value") or []
            if not roles:
                reasons.append("the application user has no security role")
            privileges = {}
            for role in roles:
                body = await self._get(info, f"/RetrieveRolePrivilegesRole(RoleId={role['roleid']})")
                privileges[str(role["roleid"])] = [p.get("PrivilegeName", "") for p in body.get("RolePrivileges") or []]
            reasons += unsafe_privileges(roles, privileges)
        verdict = ReadOnlyVerdict("unsafe" if reasons else "safe", list(self.read_only_checks), reasons[:30])
        self._verdicts[key] = (time.monotonic(), verdict)
        return verdict

    async def test_connection(self, info: ConnectionInfo) -> TestResult:
        started = time.monotonic()
        try:
            await self._token(info)
        except TelaError as exc:
            return TestResult(True, False, ReadOnlyVerdict("unsafe", reasons=["could not sign in"]),
                              int((time.monotonic() - started) * 1000), error="Entra ID refused the app registration "
                              "(tenant ID, client ID or secret).") if exc.details.get("auth_failed") else TestResult(
                False, False, ReadOnlyVerdict("unsafe", reasons=["not reachable"]),
                int((time.monotonic() - started) * 1000), error=exc.message)
        try:
            who = await self._get(info, "/WhoAmI")
            verdict = await self.verify_read_only(info, force=True)
        except TelaError as exc:
            return TestResult(False, True, ReadOnlyVerdict("unsafe", reasons=["Web API not reachable"]),
                              int((time.monotonic() - started) * 1000), error=exc.message)
        version = f"Dataverse ({self._mode(info).upper()}), organization {who.get('OrganizationId')}"
        result = TestResult(True, True, verdict, int((time.monotonic() - started) * 1000), version)
        if self._mode(info) == "tds":
            try:
                await asyncio.to_thread(lambda: self._close_all([self._connect(info)]))
            except Exception as exc:  # noqa: BLE001 - TDS off or blocked: offer the Web API (US-111)
                log.info("runner.dataverse_tds_unavailable", error=type(exc).__name__)
                return TestResult(True, True, ReadOnlyVerdict("unsafe", verdict.checks,
                                                              ["the TDS endpoint is not reachable"]),
                                  result.latency_ms, version, error=MSG_TDS_OFF, extra={"suggest_mode": "webapi"})
        return result

    def capabilities(self) -> dict[str, Any]:
        return {"dialect": "tsql", "profile": "dataverse", "custom_sql": True, "ctes": False,
                "window_functions": False, "session_read_only": True, "webapi": True}

    # ------------------------------------------------------------------ metadata (EntityDefinitions)

    async def _metadata(self, info: ConnectionInfo, logical: str) -> _Meta:
        key = f"{info.connection_id}|{logical}"
        hit = self._meta.get(key)
        if hit and time.monotonic() - hit.fetched < self.read_only_ttl_s:
            return hit
        ent = (await self._get(info, "/EntityDefinitions", {
            "$select": "LogicalName,EntitySetName,PrimaryIdAttribute,DisplayName",
            "$filter": f"LogicalName eq '{logical}'"})).get("value") or []
        if not ent:
            raise TelaError(ErrorCode.SCHEMA_CHANGED, f"Table {logical} is not in this environment.")
        attrs = (await self._get(info, f"/EntityDefinitions(LogicalName='{logical}')/Attributes", {
            "$select": "LogicalName,AttributeType,DisplayName,IsValidForRead"})).get("value") or []
        columns: dict[str, dict[str, Any]] = {}
        for a in attrs:
            if a.get("IsValidForRead") is False:
                continue
            kind = str(a.get("AttributeType") or "String")
            if kind in ("Virtual", "ManagedProperty", "CalendarRules", "PartyList"):
                continue
            t = "date" if kind == "DateTime" and a.get("Format") == "DateOnly" else (
                "datetime" if kind == "DateTime" else TYPE_NAMES.get(kind, "nvarchar"))
            label = ((a.get("DisplayName") or {}).get("UserLocalizedLabel") or {}).get("Label")
            columns[a["LogicalName"]] = {"type": t, "attribute_type": kind, "display_name": label}
        for kind, cast in CHOICE_CASTS.items():
            if not any(c.get("attribute_type") == kind for c in columns.values()):
                continue
            body = await self._get(info, f"/EntityDefinitions(LogicalName='{logical}')/Attributes/{cast}",
                                   {"$select": "LogicalName", "$expand": "OptionSet($select=Options)"})
            for item in body.get("value") or []:
                options = {str(o["Value"]): ((o.get("Label") or {}).get("UserLocalizedLabel") or {}).get("Label")
                           for o in (item.get("OptionSet") or {}).get("Options") or []}
                name = item["LogicalName"]
                if name in columns:
                    columns[name]["options"] = options
                    columns[f"{name}name"] = {"type": "nvarchar", "virtual_of": name, "options": options,
                                              "display_name": f"{columns[name].get('display_name') or name} (label)"}
        meta = _Meta(ent[0]["EntitySetName"], ent[0]["PrimaryIdAttribute"], columns, time.monotonic())
        self._meta[key] = meta
        return meta

    async def introspect(self, info: ConnectionInfo, *, max_objects: int = 2000,
                         names: list[str] | None = None, check_access: bool = True) -> Catalog:
        tables = [str(t) for t in info.descriptor.get("tables") or []]
        if not tables:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the Dataverse tables to include.")
        if names is not None:
            wanted = {n.lower().rpartition(".")[2] for n in names}
            tables = [t for t in tables if t.lower() in wanted]
        raw: list[RawObject] = []
        for logical in tables:
            meta = await self._metadata(info, logical)
            rels = (await self._get(info, f"/EntityDefinitions(LogicalName='{logical}')/ManyToOneRelationships", {
                "$select": "ReferencingAttribute,ReferencedEntity,ReferencedAttribute"})).get("value") or []
            o = RawObject("dbo", logical, "dataverse_table", None, display_name=logical)
            o.columns = [(n, c["type"], n != meta.primary_id) for n, c in meta.columns.items()]
            o.primary_key = [meta.primary_id]
            o.foreign_keys = [{"columns": [r["ReferencingAttribute"]], "ref_object": f"dbo.{r['ReferencedEntity']}",
                               "ref_columns": [r["ReferencedAttribute"]]} for r in rels
                              if r.get("ReferencedEntity") in tables]
            o.extra = {"columns": {n: {k: v for k, v in c.items() if k in ("options", "virtual_of", "display_name")}
                                   for n, c in meta.columns.items()}}
            raw.append(o)
        catalog = build_catalog(raw, {"dbo": len(raw)})
        if not check_access:  # column drift checks before queries need the shape only
            return catalog
        readable = await asyncio.gather(*(self._readable(info, o.name) for o in raw))
        for obj, ok in zip(catalog.objects, readable, strict=True):
            obj.readable = ok
        return catalog

    async def _readable(self, info: ConnectionInfo, logical: str) -> bool | None:
        """Whether the application user may read the table's rows. The metadata lists every configured table, but
        rows need the role's Read privilege (``prvRead<table>``); without it every query on the table fails. One
        row's key is read and dropped. None when the check itself could not run."""
        meta = await self._metadata(info, logical)
        try:
            await self._get(info, f"/{meta.entity_set}", {"$select": meta.primary_id, "$top": "1"}, page_size=1)
        except TelaError as exc:
            if exc.code == ErrorCode.SQL_OBJECT_NOT_ALLOWED:
                log.info("runner.dataverse_table_unreadable", table=logical)
                return False
            return None
        return True

    async def object_columns(self, info: ConnectionInfo, names: list[str]) -> dict[str, list[tuple[str, str]]]:
        catalog = await self.introspect(info, names=sorted(names), check_access=False)
        return {o.qualified_name.lower(): [(c["name"], c["type"]) for c in o.columns] for o in catalog.objects}

    # ------------------------------------------------------------------ execution

    async def execute(self, info: ConnectionInfo, sql: str, params: dict[str, Any], limits: Limits,
                      execution_id: str) -> ResultSet:
        if self._mode(info) == "tds":
            return await super().execute(info, sql, params, limits, execution_id)
        return await self._execute_webapi(info, json.loads(sql), limits)

    async def _fetch(self, info: ConnectionInfo, entity_set: str, query: dict[str, str], need: int,
                     limits: Limits) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        body = await self._get(info, f"/{entity_set}", query, page_size=min(max(need, 1), 5000))
        size = 0
        while True:
            for r in body.get("value") or []:
                rows.append(r)
                size += len(json.dumps(r, default=str))
                if size > limits.max_bytes * 2:
                    raise TelaError(ErrorCode.PAYLOAD_TOO_LARGE, "The result is too large. Add filters or fewer "
                                    "columns.")
            nxt = body.get("@odata.nextLink")
            if not nxt or len(rows) >= need:
                return rows
            body = await self._get(info, "", url=str(nxt), page_size=min(max(need, 1), 5000))

    async def _execute_webapi(self, info: ConnectionInfo, st: dict[str, Any], limits: Limits) -> ResultSet:
        started = time.monotonic()
        post = st.get("post") or {}
        # Sorted in the runner: every row first, then top N. Otherwise the source order (records by key) holds.
        need = limits.max_rows + 1 if post.get("sort") else int(post.get("skip") or 0) + int(
            post.get("top") or limits.max_rows + 1)
        logical = str(st["entity"]).rpartition(".")[2]
        entity_set = (await self._metadata(info, logical)).entity_set  # from metadata, not from the statement
        main = await self._fetch(info, entity_set, st["query"], need, limits)
        rows_out = [self._row(r, st) for r in main]
        if st.get("groups_query") is not None:  # every group, even those with no matching rows (SQL parity)
            keys = st["groups_keys"]
            groups = await self._fetch(info, entity_set, st["groups_query"], limits.max_rows + 1, limits)
            have = {tuple(r[: len(keys)]) for r in rows_out}
            filler = st.get("group_fill") or []
            for g in groups:
                k = tuple(self._row(g, {**st, "columns": keys})[: len(keys)])
                if k not in have:
                    rows_out.append([*k, *filler])
                    have.add(k)
        if not rows_out and st.get("empty_row") is not None:
            rows_out = [list(st["empty_row"])]
        for col, desc in reversed(post.get("sort") or []):  # stable multi-key sort, PostgreSQL null order
            i = st["columns"].index(col)
            present = [r for r in rows_out if r[i] is not None]
            nulls = [r for r in rows_out if r[i] is None]
            present.sort(key=_by(i), reverse=bool(desc))
            rows_out = nulls + present if desc else present + nulls
        skip = int(post.get("skip") or 0)
        rows_out = rows_out[skip:]
        cap = int(post["top"]) if post.get("top") else limits.max_rows
        truncated = len(rows_out) > cap if not post.get("top") else False
        rows_out = rows_out[: min(cap, limits.max_rows)]
        columns = [{"name": c, "type": next((self._kind(r[i]) for r in rows_out if r[i] is not None), "text")}
                   for i, c in enumerate(st["columns"])]
        return ResultSet(columns, rows_out, truncated, int((time.monotonic() - started) * 1000))

    @staticmethod
    def _row(r: dict[str, Any], st: dict[str, Any]) -> list[Any]:
        out = []
        sources = st.get("sources") or {}
        labels = st.get("labels") or {}
        for c in st["columns"]:
            src = sources.get(c, c)
            v = r.get(src)
            if c in labels and v is not None:
                v = labels[c].get(str(int(v)) if isinstance(v, int | float) else str(v), v)
            out.append(jsonable(v))
        return out

    @staticmethod
    def _kind(v: Any) -> str:
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, int):
            return "integer"
        if isinstance(v, float):
            return "decimal"
        return "text"

    async def close(self) -> None:
        await super().close()
        if self._http is not None:
            await self._http.aclose()


def _by(i: int) -> Any:
    def key(row: list[Any]) -> Any:
        return row[i]

    return key
