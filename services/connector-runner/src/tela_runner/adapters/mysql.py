"""MySQL adapter (US-113; API-063; RULE-001, RULE-006).

Read-only enforcement:

1. **Privilege proof**: ``SHOW GRANTS FOR CURRENT_USER()`` (expanded with every
   granted role) may hold only ``USAGE`` and ``SELECT`` / ``SHOW VIEW``; any other
   privilege, ``WITH GRANT OPTION``, a proxy grant or a line the parser does not
   understand makes the account unsafe (unparseable = write-capable).
2. **Session backstop**: every session runs ``SET SESSION TRANSACTION READ ONLY``;
   each statement runs in ``START TRANSACTION READ ONLY`` with
   ``max_execution_time``, and the session flag is re-checked on every checkout.

TLS is on unless the descriptor disables it in development. Cancellation is
``KILL QUERY`` on the session id from a second session of the same account.
"""

from __future__ import annotations

import re
import ssl
from typing import Any

import pymysql
from pymysql.constants import FIELD_TYPE

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_runner.adapters.base import SCHEMA_CHANGED_MESSAGE, ConnectionInfo, Limits
from tela_runner.adapters.dbapi import DbApiAdapter, RawObject

log = get_logger(__name__)

READ_PRIVILEGES = frozenset({"SELECT", "SHOW VIEW", "USAGE"})
_GRANT = re.compile(r"^GRANT (?P<privs>.+?) ON (?P<obj>\S+) TO (?P<to>\S+?)(?P<opt> WITH GRANT OPTION)?$",
                    re.IGNORECASE)
_ROLE_GRANT = re.compile(r"^GRANT (?P<roles>`[^`]+`@`[^`]*`(?:\s*,\s*`[^`]+`@`[^`]*`)*) TO \S+?"
                         r"(?P<opt> WITH ADMIN OPTION)?$", re.IGNORECASE)
_PROXY = re.compile(r"^GRANT PROXY ON", re.IGNORECASE)

_TYPES = {
    "integer": {FIELD_TYPE.TINY, FIELD_TYPE.SHORT, FIELD_TYPE.LONG, FIELD_TYPE.LONGLONG, FIELD_TYPE.INT24,
                FIELD_TYPE.YEAR},
    "decimal": {FIELD_TYPE.DECIMAL, FIELD_TYPE.NEWDECIMAL, FIELD_TYPE.FLOAT, FIELD_TYPE.DOUBLE},
    "date": {FIELD_TYPE.DATE, FIELD_TYPE.NEWDATE},
    "datetime": {FIELD_TYPE.DATETIME, FIELD_TYPE.TIMESTAMP},
    "json": {FIELD_TYPE.JSON},
}


def parse_grants(lines: list[str]) -> tuple[list[str], list[str]]:
    """Return (reasons the account is unsafe, granted role names) from ``SHOW GRANTS`` output."""
    reasons: list[str] = []
    roles: list[str] = []
    for raw in lines:
        line = raw.strip().rstrip(";")
        if _PROXY.match(line):
            reasons.append("account has a PROXY grant")
            continue
        m = _GRANT.match(line)
        if m:
            privs = [p.strip().upper() for p in re.sub(r"\([^)]*\)", "", m.group("privs")).split(",")]
            extra = sorted({p for p in privs if p not in READ_PRIVILEGES})
            for p in extra:
                reasons.append(f"account has {p} on {m.group('obj')}")
            if m.group("opt"):
                reasons.append(f"account can grant privileges on {m.group('obj')}")
            continue
        r = _ROLE_GRANT.match(line)
        if r:
            roles += [x.strip() for x in r.group("roles").split(",")]
            if r.group("opt"):
                reasons.append("account can administer roles")
            continue
        reasons.append("a grant could not be read, so the account is treated as write-capable")
    return reasons, roles


class _ServerNameContext(ssl.SSLContext):
    """Through an SSH tunnel PyMySQL connects to 127.0.0.1 and would check the certificate against that name;
    this context sends and verifies the database's real host name instead (ENH-003)."""

    server_name = ""

    @classmethod
    def for_host(cls, host: str) -> _ServerNameContext:
        ctx = cls(ssl.PROTOCOL_TLS_CLIENT)  # hostname checks and CERT_REQUIRED, as create_default_context
        ctx.load_default_certs()
        ctx.server_name = host
        return ctx

    def wrap_socket(self, sock: Any, *args: Any, **kwargs: Any) -> ssl.SSLSocket:
        kwargs["server_hostname"] = self.server_name
        return super().wrap_socket(sock, *args, **kwargs)


class MySqlAdapter(DbApiAdapter):
    engine = "mysql"
    dialect = "mysql"
    param_style = "format"
    read_only_checks = ("only USAGE and SELECT / SHOW VIEW grants (roles expanded)", "no WITH GRANT OPTION",
                        "session read-only applied")

    def _ssl(self, info: ConnectionInfo) -> ssl.SSLContext | None:
        mode = (info.descriptor.get("tls") or {}).get("mode", "verify-full")
        if mode == "disable":
            if not self.allow_insecure_tls:
                raise TelaError(ErrorCode.VALIDATION_ERROR, "TLS is required for this connection.")
            return None
        via = info.descriptor.get("via")
        ctx = _ServerNameContext.for_host(str(info.descriptor["host"])) if via else ssl.create_default_context()
        if mode == "require":
            if not self.allow_insecure_tls:  # outside development the server certificate is always verified
                return ctx
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif mode == "verify-ca":
            ctx.check_hostname = False
        return ctx

    def _connect(self, info: ConnectionInfo) -> Any:
        d, c = info.descriptor, info.credentials
        via = d.get("via") or {}  # SSH tunnel (ENH-003)
        conn = pymysql.connect(
            host=via.get("host") or d["host"], port=int(via.get("port") or d.get("port", 3306)),
            user=c.get("username") or d.get("username"),
            password=c.get("password") or "", database=d.get("database"), ssl=self._ssl(info),
            connect_timeout=10, read_timeout=25, write_timeout=10, charset="utf8mb4", autocommit=False,
            program_name="tela-connector-runner", local_infile=False,
        )
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_DATE,"
                        "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
        return conn

    def _begin(self, cur: Any, limits: Limits) -> bool:
        cur.execute("SELECT @@session.transaction_read_only")
        row = cur.fetchone()
        if not row or int(row[0]) != 1:
            return False
        cur.execute(f"SET SESSION max_execution_time = {int(limits.timeout_ms)}")
        cur.execute("START TRANSACTION READ ONLY")
        return True

    def _server_version(self, cur: Any) -> str | None:
        cur.execute("SELECT VERSION()")
        row = cur.fetchone()
        return str(row[0]) if row else None

    def _auth_failed(self, exc: Exception) -> bool:
        return isinstance(exc, pymysql.err.OperationalError) and exc.args and exc.args[0] in (1045, 1044, 1698)

    def _privileges(self, conn: Any, info: ConnectionInfo) -> list[str]:
        with conn.cursor() as cur:
            cur.execute("SHOW GRANTS FOR CURRENT_USER()")
            own = [r[0] for r in cur.fetchall()]
            reasons, roles = parse_grants(own)
            if roles:
                # Expand the privileges carried by granted roles (identifiers quoted by the server itself).
                cur.execute(f"SHOW GRANTS FOR CURRENT_USER() USING {', '.join(roles)}")
                expanded, _ = parse_grants([r[0] for r in cur.fetchall()])
                reasons += [r for r in expanded if r not in reasons]
        return reasons

    def _catalog(self, conn: Any, info: ConnectionInfo, names: list[str] | None) -> list[RawObject]:
        schemas = info.schemas
        marks = ", ".join(["%s"] * len(schemas))
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_ROWS FROM information_schema.TABLES "  # noqa: S608
                f"WHERE TABLE_SCHEMA IN ({marks}) AND TABLE_TYPE IN ('BASE TABLE', 'VIEW') "
                f"ORDER BY TABLE_SCHEMA, TABLE_NAME", schemas)
            objects: dict[tuple[str, str], RawObject] = {}
            wanted = {n.lower() for n in names} if names is not None else None
            for schema, name, kind, rows in cur.fetchall():
                if wanted is not None and f"{schema}.{name}".lower() not in wanted:
                    continue
                objects[(schema, name)] = RawObject(schema, name, "view" if kind == "VIEW" else "table",
                                                    int(rows) if rows is not None else None)
            if not objects:
                return []
            cur.execute(
                f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "  # noqa: S608
                f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA IN ({marks}) "
                f"ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION", schemas)
            for schema, name, col, typ, nullable in cur.fetchall():
                o = objects.get((schema, name))
                if o is not None:
                    o.columns.append((col, str(typ), nullable == "YES"))
            cur.execute(
                f"SELECT k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, "  # noqa: S608
                f"k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
                f"FROM information_schema.KEY_COLUMN_USAGE k WHERE k.TABLE_SCHEMA IN ({marks}) "
                f"ORDER BY k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION", schemas)
            fks: dict[tuple[str, str, str], dict[str, Any]] = {}
            for schema, name, cname, col, rschema, rtable, rcol in cur.fetchall():
                o = objects.get((schema, name))
                if o is None:
                    continue
                if cname == "PRIMARY":
                    o.primary_key.append(col)
                elif rtable:
                    fk = fks.setdefault((schema, name, cname), {"columns": [], "ref_object": f"{rschema}.{rtable}",
                                                                "ref_columns": []})
                    fk["columns"].append(col)
                    fk["ref_columns"].append(rcol)
            for (schema, name, _), fk in fks.items():
                objects[(schema, name)].foreign_keys.append(fk)
        self._end(conn)
        return list(objects.values())

    def _column_kind(self, description: tuple[Any, ...]) -> str:
        code = description[1]
        for kind, codes in _TYPES.items():
            if code in codes:
                return kind
        return "text"

    def _classify(self, exc: Exception) -> TelaError:
        code = exc.args[0] if isinstance(exc, pymysql.err.MySQLError) and exc.args else None
        if code in (3024, 1317, 1028):  # max_execution_time exceeded, query interrupted (KILL QUERY)
            return TelaError(ErrorCode.QUERY_TIMEOUT, "The query took too long and was stopped. Try narrowing it.")
        if code in (1792, 1290):  # READ ONLY transaction / --read-only server
            return TelaError(ErrorCode.READ_ONLY_VIOLATION, "Write attempt refused by the source.")
        if code in (1142, 1143, 1044):
            return TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "The data source refused access to an object.")
        if code in (1054, 1146):
            return TelaError(ErrorCode.SCHEMA_CHANGED, SCHEMA_CHANGED_MESSAGE)
        if isinstance(exc, pymysql.err.OperationalError) and code in (2003, 2006, 2013, 2055):
            return TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.")
        log.warning("runner.query_failed", engine=self.engine, code=code)
        return TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.", {"sqlstate": code})

    def _cancel_running(self, info: ConnectionInfo, conn: Any, cur: Any) -> None:
        thread_id = int(conn.thread_id())
        killer = self._connect(info)
        try:
            with killer.cursor() as kc:
                kc.execute(f"KILL QUERY {thread_id}")
        finally:
            killer.close()
