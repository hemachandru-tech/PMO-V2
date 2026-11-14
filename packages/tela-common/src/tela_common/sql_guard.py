"""Read-only statement guard (RULE-002, RULE-003): the runner's defence in depth (US-023) and the first steps
of the custom SQL validator (FEAT-031).

The API compiles and scopes every statement; the runner still refuses to run
anything that is not exactly one read-only SELECT over the objects named in
the signed envelope. The same checks back the Custom SQL validator (FEAT-031).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from tela_common.errors import ErrorCode, TelaError

_FORBIDDEN = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable", "Command", "Grant",
    "Revoke", "Transaction", "Commit", "Rollback", "Set", "Use", "Into", "Lock", "Copy", "LoadData", "Pragma",
    "Execute", "Call", "Analyze", "Describe", "Kill", "Cache", "Uncache", "Refresh", "Comment",
)
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(getattr(exp, n) for n in _FORBIDDEN if hasattr(exp, n))

FORBIDDEN_FUNCTIONS = frozenset({
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "sleep", "benchmark", "load_file", "pg_read_file",
    "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export", "dblink", "dblink_exec",
    "xp_cmdshell", "openrowset", "opendatasource", "openquery", "waitfor", "pg_terminate_backend",
    "pg_cancel_backend", "set_config", "pg_reload_conf", "current_setting", "query_to_xml", "copy",
    "pg_advisory_lock", "pg_advisory_xact_lock", "txid_current", "nextval", "setval",
})
SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast", "mysql", "sys", "performance_schema"})


def _reject(code: ErrorCode, reason: str) -> TelaError:
    return TelaError(code, "This statement is not allowed.", {"reason": reason})


def check_statement(sql: str, dialect: str, allowed_objects: set[str]) -> exp.Expression:
    """Parse and check one statement. ``allowed_objects`` holds lower-case qualified names."""
    stripped = sql.strip()
    if stripped.endswith(";"):  # one trailing semicolon is allowed and stripped (RULE-002 boundary)
        stripped = stripped[:-1].rstrip()
    if "--" in stripped or "/*" in stripped:
        raise _reject(ErrorCode.SQL_FORBIDDEN_NODE, "comments are not allowed")
    try:
        statements = [s for s in sqlglot.parse(stripped, read=dialect) if s is not None]
    except Exception as exc:
        raise _reject(ErrorCode.SQL_PARSE_ERROR, "parse error") from exc
    if len(statements) != 1:
        raise _reject(ErrorCode.SQL_MULTIPLE_STATEMENTS, "multiple statements")
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.Union | exp.Intersect | exp.Except):
        raise _reject(ErrorCode.SQL_NOT_SELECT, f"root is {type(tree).__name__}")
    cte_names: set[str] = set()
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise _reject(ErrorCode.SQL_FORBIDDEN_NODE, type(node).__name__)
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise _reject(ErrorCode.SQL_FORBIDDEN_NODE, "locking clause")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise _reject(ErrorCode.SQL_FORBIDDEN_NODE, "recursive CTE")
        if isinstance(node, exp.CTE):
            cte_names.add(node.alias.lower())
        if isinstance(node, exp.Anonymous) and str(node.this).lower() in FORBIDDEN_FUNCTIONS:
            raise _reject(ErrorCode.SQL_FORBIDDEN_NODE, f"function {node.this}")
        if isinstance(node, exp.Func) and not isinstance(node, exp.Anonymous):
            name = node.sql_name().lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise _reject(ErrorCode.SQL_FORBIDDEN_NODE, f"function {name}")
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        schema = (table.db or "").lower()
        if table.catalog:
            raise _reject(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "cross-database reference")
        if schema in SYSTEM_SCHEMAS or name.startswith("pg_"):
            raise _reject(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "system catalogue")
        if not schema and name in cte_names:
            if name in {o.rpartition(".")[2] for o in allowed_objects}:
                raise _reject(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "CTE shadows a governed table")
            continue
        qualified = f"{schema}.{name}" if schema else name
        if qualified not in allowed_objects:
            raise _reject(ErrorCode.SQL_OBJECT_NOT_ALLOWED, f"object {qualified}")
    return tree
