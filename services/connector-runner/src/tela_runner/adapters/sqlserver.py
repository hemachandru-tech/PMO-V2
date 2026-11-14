"""SQL Server / Azure SQL adapter (US-114; API-064; RULE-001, RULE-006).

pyodbc with Microsoft ODBC Driver 18: ``Encrypt=yes`` always, ``TrustServerCertificate``
only in development, ``ApplicationIntent=ReadOnly`` so an availability group routes to
a readable secondary.

Read-only enforcement:

1. **Privilege proof**: not ``sysadmin``; not a member of ``db_owner``,
   ``db_datawriter``, ``db_ddladmin``, ``db_securityadmin``, ``db_accessadmin`` or
   ``db_backupoperator``; ``fn_my_permissions`` at server and database level holds
   no write, DDL, EXECUTE, CONTROL or impersonation permission; and
   ``HAS_PERMS_BY_NAME`` finds no INSERT/UPDATE/DELETE/ALTER/CONTROL on any object
   in the approved schemas (object grants do not show at database level).
2. **Session backstop**: SQL Server has no session read-only switch, so every
   checkout re-checks the fixed-role membership, the adapter itself refuses any
   statement that is not one SELECT, and every unit of work is rolled back. The
   ODBC query timeout and ``SET LOCK_TIMEOUT`` bound the statement.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

import pyodbc

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger
from tela_runner.adapters.base import SCHEMA_CHANGED_MESSAGE, ConnectionInfo, Limits
from tela_runner.adapters.dbapi import DbApiAdapter, RawObject

log = get_logger(__name__)

ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
WRITER_ROLES = ("db_owner", "db_datawriter", "db_ddladmin", "db_securityadmin", "db_accessadmin",
                "db_backupoperator")
WRITE_PERMISSIONS = frozenset({"INSERT", "UPDATE", "DELETE", "EXECUTE", "CONTROL", "CONTROL SERVER",
                               "TAKE OWNERSHIP", "IMPERSONATE", "IMPERSONATE ANY LOGIN", "CHECKPOINT",
                               "REFERENCES", "SHUTDOWN", "UNSAFE ASSEMBLY", "EXTERNAL ACCESS ASSEMBLY"})
WRITE_PREFIXES = ("ALTER", "CREATE", "BACKUP", "ADMINISTER", "KILL")

_ROLE_SQL = ("SELECT IS_SRVROLEMEMBER('sysadmin'), "
             + ", ".join(f"IS_ROLEMEMBER('{r}')" for r in WRITER_ROLES))
_OBJECT_WRITE_SQL = """
SELECT s.name + '.' + o.name, p.perm
FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
CROSS JOIN (VALUES ('INSERT'), ('UPDATE'), ('DELETE'), ('ALTER'), ('CONTROL')) AS p(perm)
WHERE o.type IN ('U', 'V') AND s.name IN ({marks})
  AND HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', p.perm) = 1
ORDER BY 1, 2
"""
_SCHEMA_WRITE_SQL = """
SELECT s.name, p.perm FROM sys.schemas s
CROSS JOIN (VALUES ('INSERT'), ('UPDATE'), ('DELETE'), ('ALTER'), ('CONTROL')) AS p(perm)
WHERE s.name IN ({marks}) AND HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', p.perm) = 1
"""
_TABLES_SQL = """
SELECT s.name, o.name, o.type,
       (SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = o.object_id AND p.index_id IN (0, 1))
FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0 AND s.name IN ({marks})
ORDER BY s.name, o.name
"""
_COLUMNS_SQL = """
SELECT s.name, o.name, c.name, t.name, c.max_length, c.precision, c.scale, c.is_nullable
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE o.type IN ('U', 'V') AND s.name IN ({marks})
ORDER BY s.name, o.name, c.column_id
"""
_PK_SQL = """
SELECT s.name, o.name, c.name
FROM sys.key_constraints k
JOIN sys.objects o ON o.object_id = k.parent_object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.index_columns ic ON ic.object_id = k.parent_object_id AND ic.index_id = k.unique_index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE k.type = 'PK' AND s.name IN ({marks})
ORDER BY s.name, o.name, ic.key_ordinal
"""
_FK_SQL = """
SELECT s.name, o.name, fk.name, pc.name, rs.name, ro.name, rc.name
FROM sys.foreign_keys fk
JOIN sys.objects o ON o.object_id = fk.parent_object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.objects ro ON ro.object_id = fk.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
WHERE s.name IN ({marks})
ORDER BY s.name, o.name, fk.name, fkc.constraint_column_id
"""


def odbc_value(value: str) -> str:
    """Quote a connection-string value so ``;`` or ``}`` in it cannot add attributes."""
    return "{" + value.replace("}", "}}") + "}"


def type_name(t: str, max_length: int, precision: int, scale: int) -> str:
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t}({'max' if max_length == -1 else max_length})"
    if t in ("nvarchar", "nchar"):
        return f"{t}({'max' if max_length == -1 else max_length // 2})"
    if t in ("decimal", "numeric"):
        return f"{t}({precision},{scale})"
    return t


def is_permission_write(name: str) -> bool:
    n = name.upper()
    return n in WRITE_PERMISSIONS or n.startswith(WRITE_PREFIXES)


class SqlServerAdapter(DbApiAdapter):
    engine = "sqlserver"
    dialect = "tsql"
    param_style = "qmark"
    statement_backstop = True
    read_only_checks = ("not sysadmin and no writer or owner database role",
                        "no write, DDL, EXECUTE or CONTROL permission (server, database, schemas, objects)",
                        "statements limited to one SELECT and always rolled back")

    def _base_attrs(self, info: ConnectionInfo) -> list[str]:
        d = info.descriptor
        tls = d.get("tls") or {}
        trust = bool(tls.get("trust_server_certificate"))
        if trust and not self.allow_insecure_tls:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Trusting the server certificate is only allowed in "
                            "development.")
        via = d.get("via") or {}  # SSH tunnel (ENH-003): connect to it, check the certificate for the real host
        server = f"{via['host']},{int(via['port'])}" if via else f"{d['host']},{int(d.get('port', 1433))}"
        attrs = [f"Driver={{{ODBC_DRIVER}}}", f"Server={odbc_value(server)}",
                 "Encrypt=yes", f"TrustServerCertificate={'yes' if trust else 'no'}", "APP=tela-connector-runner",
                 "ApplicationIntent=ReadOnly", "MultiSubnetFailover=no"]
        if via:
            attrs.append(f"HostNameInCertificate={odbc_value(str(d['host']))}")
        if d.get("database"):
            attrs.append(f"Database={odbc_value(str(d['database']))}")
        return attrs

    def _connect(self, info: ConnectionInfo) -> Any:
        c = info.credentials
        attrs = [*self._base_attrs(info), f"UID={odbc_value(c.get('username') or str(info.descriptor['username']))}",
                 f"PWD={odbc_value(c.get('password') or '')}"]
        return self._configure(pyodbc.connect(";".join(attrs), timeout=10, autocommit=False))

    @staticmethod
    def _configure(conn: Any) -> Any:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cur.execute("SET XACT_ABORT ON")
        conn.rollback()
        return conn

    def _begin(self, cur: Any, limits: Limits) -> bool:
        cur.execute(_ROLE_SQL)
        row = cur.fetchone()
        if row is None or any(v == 1 for v in row):
            return False
        cur.connection.timeout = max(1, math.ceil(limits.timeout_ms / 1000))
        cur.execute(f"SET LOCK_TIMEOUT {int(limits.timeout_ms)}")
        return True

    def _server_version(self, cur: Any) -> str | None:
        cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64))")
        row = cur.fetchone()
        return str(row[0]) if row else None

    def _auth_failed(self, exc: Exception) -> bool:
        return isinstance(exc, pyodbc.Error) and bool(exc.args) and exc.args[0] == "28000"

    def _privileges(self, conn: Any, info: ConnectionInfo) -> list[str]:
        reasons: list[str] = []
        cur = conn.cursor()
        cur.execute(_ROLE_SQL)
        row = cur.fetchone()
        names = ["sysadmin", *WRITER_ROLES]
        for name, flag in zip(names, row or [], strict=False):
            if flag == 1:
                reasons.append(f"account is a member of {name}")
        for scope in ("SERVER", "DATABASE"):
            cur.execute(f"SELECT permission_name FROM fn_my_permissions(NULL, '{scope}')")  # noqa: S608 - constant
            for (perm,) in cur.fetchall():
                if is_permission_write(perm):
                    reasons.append(f"account has {perm} on the {scope.lower()}")
        marks = ", ".join(["?"] * len(info.schemas))
        cur.execute(_SCHEMA_WRITE_SQL.format(marks=marks), info.schemas)
        for schema, perm in cur.fetchall():
            reasons.append(f"account has {perm} on schema {schema}")
        cur.execute(_OBJECT_WRITE_SQL.format(marks=marks), info.schemas)
        writes = cur.fetchall()
        for obj, perm in writes[:20]:
            reasons.append(f"account has {perm} on {obj}")
        if len(writes) > 20:
            reasons.append(f"and {len(writes) - 20} more write privileges")
        conn.rollback()
        return list(dict.fromkeys(reasons))

    def _catalog(self, conn: Any, info: ConnectionInfo, names: list[str] | None) -> list[RawObject]:
        marks = ", ".join(["?"] * len(info.schemas))
        wanted = {n.lower() for n in names} if names is not None else None
        cur = conn.cursor()
        cur.execute(_TABLES_SQL.format(marks=marks), info.schemas)
        objects: dict[tuple[str, str], RawObject] = {}
        for schema, name, kind, rows in cur.fetchall():
            if wanted is not None and f"{schema}.{name}".lower() not in wanted:
                continue
            objects[(schema, name)] = RawObject(schema, name, "view" if kind.strip() == "V" else "table",
                                                int(rows) if rows is not None else None)
        if objects:
            cur.execute(_COLUMNS_SQL.format(marks=marks), info.schemas)
            for schema, name, col, t, length, prec, scale, nullable in cur.fetchall():
                o = objects.get((schema, name))
                if o is not None:
                    o.columns.append((col, type_name(t, length, prec, scale), bool(nullable)))
            cur.execute(_PK_SQL.format(marks=marks), info.schemas)
            for schema, name, col in cur.fetchall():
                if (schema, name) in objects:
                    objects[(schema, name)].primary_key.append(col)
            cur.execute(_FK_SQL.format(marks=marks), info.schemas)
            fks: dict[tuple[str, str, str], dict[str, Any]] = {}
            for schema, name, fk_name, col, rschema, rtable, rcol in cur.fetchall():
                if (schema, name) not in objects:
                    continue
                fk = fks.setdefault((schema, name, fk_name), {"columns": [], "ref_object": f"{rschema}.{rtable}",
                                                              "ref_columns": []})
                fk["columns"].append(col)
                fk["ref_columns"].append(rcol)
            for (schema, name, _), fk in fks.items():
                objects[(schema, name)].foreign_keys.append(fk)
        conn.rollback()
        return list(objects.values())

    def _column_kind(self, description: tuple[Any, ...]) -> str:
        t = description[1]
        if t is bool:
            return "boolean"
        if t is int:
            return "integer"
        if t in (Decimal, float):
            return "decimal"
        if t is dt.datetime:
            return "datetime"
        if t is dt.date:
            return "date"
        return "text"

    def _classify(self, exc: Exception) -> TelaError:
        state = exc.args[0] if isinstance(exc, pyodbc.Error) and exc.args else None
        text = str(exc.args[1]) if isinstance(exc, pyodbc.Error) and len(exc.args) > 1 else ""
        if state == "HYT00" or "(1222)" in text:  # query timeout / lock request time out
            return TelaError(ErrorCode.QUERY_TIMEOUT, "The query took too long and was stopped. Try narrowing it.")
        if state == "HY008":  # operation cancelled
            return TelaError(ErrorCode.QUERY_TIMEOUT, "The request was cancelled.")
        if state in ("42S02", "42S22") or "(208)" in text or "(207)" in text:
            return TelaError(ErrorCode.SCHEMA_CHANGED, SCHEMA_CHANGED_MESSAGE)
        if "(229)" in text or "(230)" in text:  # permission denied on object / column
            return TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "The data source refused access to an object.")
        if state in ("08001", "08S01", "HYT01"):
            return TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.")
        log.warning("runner.query_failed", engine=self.engine, sqlstate=state)
        return TelaError(ErrorCode.QUERY_FAILED, "The data source could not run this query.", {"sqlstate": state})

    def _cancel_running(self, info: ConnectionInfo, conn: Any, cur: Any) -> None:
        cur.cancel()
