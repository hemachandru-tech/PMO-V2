"""A fake Dataverse environment for US-110 / US-111 — development and tests only, never shipped.

The real environment needs DEC-09 access (environment, app registration, application user), which Tela does
not have yet. This fake answers the calls the Dataverse adapter makes, from the synthetic TPG extract under
Dataverse logical names (``packs/pmo/fixtures/tpg_synthetic.py``):

* Entra ID: ``/{tenant}/v2.0/.well-known/openid-configuration`` and the client-credentials token endpoint
  (two registered apps: a read-only one and one whose role can write);
* the application user's security roles and ``RetrieveRolePrivilegesRole``;
* ``EntityDefinitions`` metadata: attributes, choice options (statecode / statuscode), many-to-one links;
* entity-set reads with ``$select``, ``$filter``, ``$orderby``, ``$top``, ``$apply`` (filter, groupby, aggregate)
  and ``fetchXml``, with ``Prefer: odata.maxpagesize`` paging through ``@odata.nextLink``;
* service-protection throttling on demand (HTTP 429 with ``Retry-After``) and 405 for any write.

It is not a Dataverse emulator: it follows Microsoft's documented request shapes for the subset Tela sends,
and its OData semantics are OData's (``x ne 'A'`` is true when x is null; ``not`` is two-valued).

    uvicorn dataverse_fake:app --app-dir tests/fakes --port 8099
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packs" / "pmo" / "fixtures"))
import tpg_synthetic as tpg

API = "/api/data/v9.2"
TENANT = os.environ.get("FAKE_DATAVERSE_TENANT", "00000000-0000-4000-8000-000000000001")
APPS = {  # client id -> (secret, role)
    os.environ.get("FAKE_DATAVERSE_CLIENT_ID", "00000000-0000-4000-8000-0000000000a1"):
        (os.environ.get("FAKE_DATAVERSE_SECRET", "fake-reader-secret"), "Tela Reader"),
    os.environ.get("FAKE_DATAVERSE_WRITER_ID", "00000000-0000-4000-8000-0000000000a2"):
        (os.environ.get("FAKE_DATAVERSE_WRITER_SECRET", "fake-writer-secret"), "Basic User"),
}
ROLES = {
    "Tela Reader": [f"prvRead{t}" for t, _ in tpg.DATAVERSE_TABLES.values()],
    "Basic User": [p for t, _ in tpg.DATAVERSE_TABLES.values()
                   for p in (f"prvRead{t}", f"prvWrite{t}", f"prvCreate{t}", f"prvAppend{t}")],
}
ENTITY_SET = {"tpg_project": "tpg_projects", "tpg_projectrequest": "tpg_projectrequests"}
STATE = {"throttle_every": int(os.environ.get("FAKE_DATAVERSE_THROTTLE_EVERY", "0")), "reads": 0,
         "writes": 0, "tokens": {}}

app = FastAPI(title="Fake Dataverse (tests only)", docs_url=None, openapi_url=None)
db = duckdb.connect()
TYPES: dict[str, dict[str, str]] = {}
for _table, (_logical, _) in tpg.DATAVERSE_TABLES.items():
    _cols = tpg.dataverse_columns(_table)
    TYPES[_logical] = dict(_cols)
    _duck = {"text": "VARCHAR", "date": "DATE", "datetime": "TIMESTAMP", "money": "DECIMAL(19,4)", "guid": "VARCHAR",
             "int": "INTEGER"}
    db.execute(f"CREATE TABLE {_logical} ({', '.join(f'{c} {_duck[k]}' for c, k in _cols)})")
    db.executemany(f"INSERT INTO {_logical} VALUES ({', '.join('?' for _ in _cols)})",
                   [tuple(r.get(c) for c, _ in _cols) for r in tpg.dataverse_rows(_table)])
OPTIONS = {
    "statecode": {v: k for k, v in tpg.STATE_CODES.items()},
    "statuscode": {"tpg_project": {v: k for k, v in tpg.STATUS_CODES["projects"].items()},
                   "tpg_projectrequest": {v: k for k, v in tpg.STATUS_CODES["project_requests"].items()}},
}


def _odata_error(status: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": str(status), "message": message}}, status_code=status, headers=headers)


def _authorised(request: Request) -> str | None:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    return STATE["tokens"].get(token)


# ----------------------------------------------------------------------------- Entra ID


@app.get("/{tenant}/v2.0/.well-known/openid-configuration")
async def openid(tenant: str) -> dict[str, Any]:
    base = f"https://login.microsoftonline.com/{tenant}"
    return {"issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
            "authorization_endpoint": f"{base}/oauth2/v2.0/authorize", "token_endpoint": f"{base}/oauth2/v2.0/token",
            "jwks_uri": f"{base}/discovery/v2.0/keys", "response_types_supported": ["code"],
            "subject_types_supported": ["pairwise"], "id_token_signing_alg_values_supported": ["RS256"]}


@app.post("/{tenant}/oauth2/v2.0/token")
async def token(tenant: str, request: Request) -> JSONResponse:
    from urllib.parse import parse_qs

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    grant_type, client_id = form.get("grant_type", ""), form.get("client_id", "")
    client_secret, scope = form.get("client_secret", ""), form.get("scope", "")
    app_ = APPS.get(client_id)
    if tenant != TENANT or grant_type != "client_credentials" or app_ is None or not secrets.compare_digest(
            app_[0], client_secret) or not scope.endswith("/.default"):
        return JSONResponse({"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"},
                            status_code=401)
    access = secrets.token_urlsafe(24)
    STATE["tokens"][access] = client_id
    return JSONResponse({"token_type": "Bearer", "expires_in": 3599, "ext_expires_in": 3599, "access_token": access})


# ----------------------------------------------------------------------------- users, roles, metadata


@app.get(API + "/WhoAmI")
async def whoami(request: Request) -> Any:
    client = _authorised(request)
    if client is None:
        return _odata_error(401, "Unauthorized")
    return {"UserId": tpg._guid(f"user-{client}"), "BusinessUnitId": tpg._guid("bu"),
            "OrganizationId": tpg._guid("org")}


@app.get(API + "/systemusers")
async def systemusers(request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    m = re.search(r"applicationid eq ([0-9a-fA-F-]{36})", request.query_params.get("$filter", ""))
    client = m.group(1) if m else ""
    if client not in APPS:
        return {"value": []}
    return {"value": [{"systemuserid": tpg._guid(f"user-{client}"), "applicationid": client}]}


@app.get(API + "/systemusers({user_id})/systemuserroles_association")
async def user_roles(user_id: str, request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    client = next((c for c in APPS if tpg._guid(f"user-{c}") == user_id), None)
    if client is None:
        return _odata_error(404, "User not found")
    role = APPS[client][1]
    return {"value": [{"roleid": tpg._guid(f"role-{role}"), "name": role}]}


@app.get(API + "/RetrieveRolePrivilegesRole(RoleId={role_id})")
async def role_privileges(role_id: str, request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    role = next((r for r in ROLES if tpg._guid(f"role-{r}") == role_id), None)
    if role is None:
        return _odata_error(404, "Role not found")
    return {"RolePrivileges": [{"PrivilegeName": p, "Depth": "Global", "PrivilegeId": tpg._guid(p)}
                               for p in ROLES[role]]}


ATTRIBUTE_TYPES = {"text": "String", "date": "DateTime", "datetime": "DateTime", "money": "Money",
                   "guid": "Uniqueidentifier", "int": "Integer"}


def _attributes(logical: str) -> list[dict[str, Any]]:
    out = []
    for col, kind in TYPES[logical].items():
        if col.endswith("codename"):
            continue  # virtual label columns exist on the TDS endpoint only
        attr_type = {"statecode": "State", "statuscode": "Status"}.get(col, ATTRIBUTE_TYPES[kind])
        if col == logical + "id":
            attr_type = "Uniqueidentifier"
        out.append({"LogicalName": col, "AttributeType": attr_type, "IsValidForRead": True,
                    "AttributeTypeName": {"Value": f"{attr_type}Type"},
                    "Format": "DateOnly" if kind == "date" else None,
                    "DisplayName": {"UserLocalizedLabel": {"Label": col.replace("tpgmea_", "").replace("_", " ")
                                                           .title()}}})
    return out


@app.get(API + "/EntityDefinitions")
async def entity_definitions(request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    names = re.findall(r"LogicalName eq '([a-z_]+)'", request.query_params.get("$filter", ""))
    return {"value": [{"LogicalName": n, "EntitySetName": ENTITY_SET[n], "PrimaryIdAttribute": f"{n}id",
                       "DisplayName": {"UserLocalizedLabel": {"Label": n.replace("tpg_", "").title()}}}
                      for n in (names or list(TYPES)) if n in TYPES]}


@app.get(API + "/EntityDefinitions(LogicalName='{logical}')/Attributes")
async def attributes(logical: str, request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    if logical not in TYPES:
        return _odata_error(404, "Entity not found")
    return {"value": _attributes(logical)}


@app.get(API + "/EntityDefinitions(LogicalName='{logical}')/Attributes/{cast}")
async def choice_attributes(logical: str, cast: str, request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    kind = {"Microsoft.Dynamics.CRM.StateAttributeMetadata": "statecode",
            "Microsoft.Dynamics.CRM.StatusAttributeMetadata": "statuscode"}.get(cast)
    if kind is None or logical not in TYPES:
        return {"value": []}
    labels = OPTIONS["statecode"] if kind == "statecode" else OPTIONS["statuscode"][logical]
    return {"value": [{"LogicalName": kind, "OptionSet": {"Options": [
        {"Value": v, "Label": {"UserLocalizedLabel": {"Label": lbl}}} for v, lbl in sorted(labels.items())]}}]}


@app.get(API + "/EntityDefinitions(LogicalName='{logical}')/ManyToOneRelationships")
async def relationships(logical: str, request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    return {"value": []}  # no documented lookup between the two TPG tables (db_scheme §18)


# ----------------------------------------------------------------------------- OData and FetchXML reads

_TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<guid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                    r"[0-9a-fA-F]{12})|(?P<dt>\d{4}-\d{2}-\d{2}T[\d:.]+Z?)|(?P<d>\d{4}-\d{2}-\d{2})|"
                    r"(?P<num>-?\d+(?:\.\d+)?)|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<p>[(),/$]))")


class OData:
    """A recursive-descent parser for the OData subset Tela sends; renders DuckDB SQL with OData semantics."""

    def __init__(self, text: str, columns: dict[str, str]) -> None:
        self.toks: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"cannot parse OData at {text[pos:pos + 20]!r}")
            kind = m.lastgroup or ""
            self.toks.append((kind, m.group(kind)))
            pos = m.end()
        self.i = 0
        self.columns = columns
        self.params: list[Any] = []

    def peek(self) -> tuple[str, str]:
        return self.toks[self.i] if self.i < len(self.toks) else ("", "")

    def take(self, value: str | None = None) -> str:
        _, v = self.peek()
        if value is not None and v.lower() != value.lower():
            raise ValueError(f"expected {value}, got {v}")
        self.i += 1
        return v

    def expr(self) -> str:
        left = self.conj()
        while self.peek()[1].lower() == "or":
            self.take()
            left = f"({left} OR {self.conj()})"
        return left

    def conj(self) -> str:
        left = self.unary()
        while self.peek()[1].lower() == "and":
            self.take()
            left = f"({left} AND {self.unary()})"
        return left

    def unary(self) -> str:
        if self.peek()[1].lower() == "not":
            self.take()
            return f"(NOT COALESCE({self.unary()}, FALSE))"
        if self.peek()[1] == "(":
            self.take("(")
            inner = self.expr()
            self.take(")")
            return f"({inner})"
        return self.comparison()

    def operand(self) -> tuple[str, bool]:
        kind, v = self.peek()
        self.take()
        if kind == "id" and v.lower() == "null":
            return "NULL", True
        if kind == "id" and v.lower() in ("true", "false"):
            self.params.append(v.lower() == "true")
            return "?", False
        if kind == "id":
            if v not in self.columns:
                raise ValueError(f"Could not find a property named '{v}'")
            return v, False
        if kind == "str":
            self.params.append(v[1:-1].replace("''", "'"))
        elif kind == "guid":
            self.params.append(v.lower())
        elif kind == "dt":
            self.params.append(datetime.fromisoformat(v.rstrip("Z")))
        elif kind == "d":
            self.params.append(date.fromisoformat(v))
        else:
            self.params.append(Decimal(v))
        return "?", False

    def comparison(self) -> str:
        left, _ = self.operand()
        op = self.take().lower()
        right, is_null = self.operand()
        if is_null:
            return f"({left} IS NULL)" if op == "eq" else f"({left} IS NOT NULL)"
        sql_op = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}[op]
        if op == "ne":  # OData: null ne 'A' is true
            return f"({left} <> {right} OR {left} IS NULL)"
        return f"({left} {sql_op} {right})"


def _apply(text: str, table: str, cols: dict[str, str]) -> tuple[str, list[Any]]:
    """filter(...)/filter(...)/groupby((a,b),aggregate(...)) or aggregate(...) → SQL."""
    parts = []
    depth = start = 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "/" and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    wheres: list[str] = []
    params: list[Any] = []
    groups: list[str] = []
    aggs: list[str] = []
    for part in parts:
        name, _, body = part.partition("(")
        body = body[:-1]
        if name == "filter":
            p = OData(body, cols)
            wheres.append(p.expr())
            params += p.params
        elif name == "groupby":
            m = re.match(r"^\(([^)]*)\)(?:,aggregate\((.*)\))?$", body)
            if not m:
                raise ValueError("bad groupby")
            groups = [g.strip() for g in m.group(1).split(",") if g.strip()]
            for g in groups:
                if g not in cols:
                    raise ValueError(f"Could not find a property named '{g}'")
            aggs += _aggregates(m.group(2) or "", cols)
        elif name == "aggregate":
            aggs += _aggregates(body, cols)
        else:
            raise ValueError(f"transformation {name} is not supported")
    select = ", ".join([*groups, *aggs]) or "count(*) AS n"
    sql = f"SELECT {select} FROM {table}"
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    if groups:
        sql += " GROUP BY " + ", ".join(groups)
    return sql, params


def _aggregates(text: str, cols: dict[str, str]) -> list[str]:
    out = []
    for item in [x.strip() for x in text.split(",") if x.strip()]:
        m = re.match(r"^\$count as ([a-z_][a-z0-9_]*)$", item)
        if m:
            out.append(f"count(*) AS {m.group(1)}")
            continue
        m = re.match(r"^([a-z_][a-z0-9_]*) with (sum|average|min|max|countdistinct) as ([a-z_][a-z0-9_]*)$", item)
        if not m or m.group(1) not in cols:
            raise ValueError(f"bad aggregate {item}")
        fn = {"sum": "sum", "average": "avg", "min": "min", "max": "max", "countdistinct": "count(DISTINCT"}[m.group(2)]
        out.append(f"{fn}({m.group(1)}) AS {m.group(3)}" if fn != "count(DISTINCT" else
                   f"count(DISTINCT {m.group(1)}) AS {m.group(3)}")
    return out


def _fetchxml(text: str, cols: dict[str, str]) -> tuple[str, list[Any]]:
    root = ET.fromstring(text)  # noqa: S314 - the fake parses the adapter's own generated FetchXML
    ent = root.find("entity")
    if ent is None or ent.get("name") not in TYPES:
        raise ValueError("unknown entity")
    table = str(ent.get("name"))
    params: list[Any] = []

    def value(raw: str, col: str) -> Any:
        kind = cols[col]
        return {"date": date.fromisoformat, "datetime": lambda s: datetime.fromisoformat(s.rstrip("Z")),
                "money": Decimal, "int": int}.get(kind, str)(raw)

    def cond(node: ET.Element) -> str:
        if node.tag == "filter":
            joiner = " OR " if node.get("type") == "or" else " AND "
            inner = [cond(c) for c in node]
            return "(" + joiner.join(inner) + ")" if inner else "TRUE"
        col, op = node.get("attribute") or "", node.get("operator") or ""
        if col not in cols:
            raise ValueError(f"unknown attribute {col}")
        if op == "null":
            return f"({col} IS NULL)"
        if op == "not-null":
            return f"({col} IS NOT NULL)"
        if op in ("in", "not-in"):
            vals = [value(v.text or "", col) for v in node.findall("value")]
            params.extend(vals)
            marks = ", ".join("?" for _ in vals)
            return f"({col} IN ({marks}))" if op == "in" else f"({col} NOT IN ({marks}) OR {col} IS NULL)"
        params.append(value(node.get("value") or "", col))
        sql_op = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
                  "on-or-after": ">=", "on-or-before": "<="}[op]
        if op == "ne":
            return f"({col} <> ? OR {col} IS NULL)"
        return f"({col} {sql_op} ?)"

    wheres = [cond(f) for f in ent.findall("filter")]
    groups, aggs = [], []
    for a in ent.findall("attribute"):
        name, alias = a.get("name") or "", a.get("alias") or a.get("name") or ""
        if name not in cols or not re.match(r"^[a-z_][a-z0-9_]*$", alias):
            raise ValueError("bad attribute")
        if a.get("groupby") == "true":
            groups.append(f"{name} AS {alias}")
        elif a.get("aggregate"):
            fn = a.get("aggregate")
            distinct = a.get("distinct") == "true"
            if fn == "count":
                aggs.append(f"count(*) AS {alias}")
            elif fn == "countcolumn":
                aggs.append(f"count({'DISTINCT ' if distinct else ''}{name}) AS {alias}")
            else:
                aggs.append(f"{ {'sum': 'sum', 'avg': 'avg', 'min': 'min', 'max': 'max'}[str(fn)] }({name}) AS {alias}")
        else:
            groups.append(name)
    sql = f"SELECT {', '.join([*groups, *aggs])} FROM {table}"
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    if root.get("aggregate") == "true" and any(a.get("groupby") == "true" for a in ent.findall("attribute")):
        sql += " GROUP BY " + ", ".join(a.get("name") or "" for a in ent.findall("attribute")
                                        if a.get("groupby") == "true")
    return sql, params


def _json(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat() + "Z"
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


@app.get(API + "/{entity_set}")
async def read(entity_set: str, request: Request) -> Any:
    if _authorised(request) is None:
        return _odata_error(401, "Unauthorized")
    table = next((t for t, s in ENTITY_SET.items() if s == entity_set), None)
    if table is None:
        return _odata_error(404, f"Resource not found for the segment '{entity_set}'.")
    STATE["reads"] += 1
    every = STATE["throttle_every"]
    if every and STATE["reads"] % every == 0:
        return _odata_error(429, "Number of requests exceeded the limit of 6000 over time window of 300 seconds.",
                            {"Retry-After": "1"})
    cols = TYPES[table]
    q = request.query_params
    try:
        if "fetchXml" in q:
            sql, params = _fetchxml(q["fetchXml"], cols)
        elif "$apply" in q:
            sql, params = _apply(q["$apply"], table, cols)
        else:
            select = [c.strip() for c in q.get("$select", "").split(",") if c.strip()] or [
                c for c in cols if not c.endswith("codename")]
            for c in select:
                if c not in cols:
                    raise ValueError(f"Could not find a property named '{c}'")
            sql = f"SELECT {', '.join(select)} FROM {table}"
            params = []
            if "$filter" in q:
                p = OData(q["$filter"], cols)
                sql += " WHERE " + p.expr()
                params = p.params
            if "$orderby" in q:
                order = []
                for item in q["$orderby"].split(","):
                    col, _, direction = item.strip().partition(" ")
                    if col not in cols:
                        raise ValueError("bad orderby")
                    order.append(f"{col} {'DESC' if direction.lower() == 'desc' else 'ASC'}")
                sql += " ORDER BY " + ", ".join(order)
            if "$top" in q:
                sql += f" LIMIT {int(q['$top'])}"
    except (ValueError, KeyError, ET.ParseError) as exc:
        return _odata_error(400, str(exc))
    cur = db.execute(sql, params)
    names = [d[0] for d in cur.description or []]
    rows = [dict(zip(names, map(_json, r), strict=True)) for r in cur.fetchall()]
    for r in rows:
        for choice in ("statecode", "statuscode"):
            if choice in r and r[choice] is not None:
                labels = OPTIONS["statecode"] if choice == "statecode" else OPTIONS["statuscode"][table]
                r[f"{choice}@OData.Community.Display.V1.FormattedValue"] = labels.get(int(r[choice]))
    m = re.search(r"odata\.maxpagesize=(\d+)", request.headers.get("prefer", ""))
    page = int(m.group(1)) if m else 5000
    skip = int(q.get("$skiptoken", "0") or 0)
    body: dict[str, Any] = {"@odata.context": f"{API}/$metadata#{entity_set}", "value": rows[skip:skip + page]}
    if skip + page < len(rows):
        params_out = {k: v for k, v in q.items() if k != "$skiptoken"}
        from urllib.parse import urlencode

        body["@odata.nextLink"] = (f"{request.base_url}{API.lstrip('/')}/{entity_set}?"
                                   f"{urlencode({**params_out, '$skiptoken': skip + page})}")
    return body


@app.api_route(API + "/{path:path}", methods=["POST", "PATCH", "PUT", "DELETE"])
async def write(path: str) -> Any:
    STATE["writes"] += 1
    return _odata_error(405, "The fake refuses every write; Tela's adapter should never send one.")


@app.get("/fake/state")
async def state() -> dict[str, Any]:
    return {"reads": STATE["reads"], "writes": STATE["writes"], "throttle_every": STATE["throttle_every"],
            "time": time.time()}


@app.post("/fake/throttle/{every}")
async def throttle(every: int) -> dict[str, int]:
    STATE["throttle_every"] = every
    STATE["reads"] = 0
    return {"throttle_every": every}
