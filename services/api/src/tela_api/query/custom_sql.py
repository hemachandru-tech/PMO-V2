"""Custom SQL validation (FEAT-031; RULE-002 to RULE-004, RULE-006, RULE-007; US-079).

The pipeline, in order, stops at the first failure and reports every step:

1. engine supported (not MongoDB or the Dataverse Web API, ADR-014);
2. ``:page_filters`` placeholder replaced by a sentinel (at most once, never inside a string);
3. parse in the connection's dialect; 4. exactly one statement; 5. SELECT root; 6. no forbidden nodes,
   functions, comments, recursive CTEs or system catalogues (the same guard the runner applies);
7. only objects of the approved model (allow-list); 8. no field hidden for the caller's role and no column
   outside the model;
9. the placeholder sits in a WHERE/AND position;
10. scope rewrite: every governed table becomes the caller's scoped derived table, then the independent
    ``assert_scoped`` check runs (fail closed);
11. page filters substituted (or ``1=1``) and the row cap / timeout applied;
12. the statement hash is bound into a short-lived check token; Run requires it, and the runner re-verifies
    the signed statement before executing.

Custom SQL is read by the same single scoped execution path as every other query (``QueryService._run``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from tela_api.identity.scope import ResolvedScope
from tela_api.query.compiler import DIALECTS, ColumnMeta, CompiledQuery, Params
from tela_api.query.scope_injector import assert_scoped, governed_objects, inject
from tela_common.errors import ErrorCode, TelaError
from tela_common.sql_guard import check_statement
from tela_query_spec.model import ModelEntity, ModelSnapshot

SENTINEL = "tela_page_filters_sentinel"
PLACEHOLDER = re.compile(r"(?<![\w:]):page_filters\b", re.I)
TOKEN_TTL_S = 300
MSG_NO_PLACEHOLDER = ("This query has no :page_filters placeholder, so it won't follow the page's filters. "
                      "\"Follows page filters\" is turned off.")  # MSG-017
MSG_ENGINE = "Custom SQL isn't available for this source."  # MSG-018
STEP_NAMES = ["engine_supported", "page_filters_placeholder", "parse", "single_select", "read_only",
              "allowed_objects", "visible_fields", "placeholder_position", "scope_applied", "limits"]
GUARD_STEP = {ErrorCode.SQL_PARSE_ERROR: "parse", ErrorCode.SQL_MULTIPLE_STATEMENTS: "single_select",
              ErrorCode.SQL_NOT_SELECT: "single_select", ErrorCode.SQL_FORBIDDEN_NODE: "read_only",
              ErrorCode.SQL_OBJECT_NOT_ALLOWED: "allowed_objects"}
SECURITY_CODES = {ErrorCode.SQL_MULTIPLE_STATEMENTS, ErrorCode.SQL_NOT_SELECT, ErrorCode.SQL_FORBIDDEN_NODE,
                  ErrorCode.SQL_OBJECT_NOT_ALLOWED, ErrorCode.SQL_FIELD_HIDDEN, ErrorCode.SCOPE_REWRITE_FAILED}
MESSAGES = {
    "parse": "This SQL could not be read. Check the syntax.",
    "single_select": "Write exactly one SELECT statement.",
    "read_only": "Only read-only SELECT statements are allowed (no changes, comments, locks or system functions).",
    "allowed_objects": "The query uses a table that is not in the approved model.",
}


@dataclass
class Prepared:
    """A checked statement: the aliased tree (sentinel still in place) and what it reads."""

    tree: exp.Expression
    dialect: str
    aliases: dict[str, ModelEntity]  # alias -> entity
    has_placeholder: bool
    ast_hash: str
    requested_limit: int | None
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CheckFailed(Exception):
    def __init__(self, step: str, error: TelaError, checks: list[dict[str, Any]]) -> None:
        super().__init__(error.message)
        self.step, self.error, self.checks = step, error, checks


def _norm(name: str) -> str:
    return name.strip('"').lower()


def dialect_for(engine: str, descriptor: dict[str, Any] | None = None) -> str:
    if (engine == "dataverse" and (descriptor or {}).get("mode") == "webapi") or engine not in DIALECTS:
        raise TelaError(ErrorCode.ENGINE_NOT_SUPPORTED, MSG_ENGINE)
    return DIALECTS[engine]


def _allowed_names(snap: ModelSnapshot) -> tuple[set[str], dict[str, ModelEntity]]:
    """Qualified source objects plus bare names that identify exactly one entity."""
    governed = governed_objects(snap)
    bare: dict[str, list[ModelEntity]] = {}
    for qualified, ent in governed.items():
        bare.setdefault(qualified.rpartition(".")[2], []).append(ent)
    lookup = dict(governed)
    for name, ents in bare.items():
        if len(ents) == 1:
            lookup.setdefault(name, ents[0])
    return set(lookup), lookup


def _sentinels(tree: exp.Expression) -> list[exp.Expression]:
    return [n for n in tree.find_all(exp.Anonymous) if str(n.this).lower() == SENTINEL]


def validate(sql: str, *, engine: str, descriptor: dict[str, Any] | None, snap: ModelSnapshot,
             scope: ResolvedScope) -> Prepared:
    checks: list[dict[str, Any]] = []

    def ok(step: str, detail: str | None = None) -> None:
        checks.append({"name": step, "ok": True, **({"detail": detail} if detail else {})})

    def fail(step: str, error: TelaError) -> CheckFailed:
        checks.append({"name": step, "ok": False, "code": str(error.code), "detail": error.message})
        return CheckFailed(step, error, checks)

    try:
        dialect = dialect_for(engine, descriptor)
    except TelaError as exc:
        raise fail("engine_supported", exc) from exc
    ok("engine_supported", dialect)

    count = len(PLACEHOLDER.findall(sql))
    if count > 1:
        raise fail("page_filters_placeholder", TelaError(
            ErrorCode.SQL_PLACEHOLDER_MISPLACED, "Use :page_filters at most once."))
    text = PLACEHOLDER.sub(f"{SENTINEL}()", sql)
    ok("page_filters_placeholder", "found" if count else "not used")

    allowed, lookup = _allowed_names(snap)
    try:
        tree = check_statement(text, dialect, allowed)
    except TelaError as exc:
        step = GUARD_STEP.get(exc.code, "read_only")
        for s in STEP_NAMES[2:STEP_NAMES.index(step)]:
            ok(s)
        raise fail(step, TelaError(exc.code, MESSAGES[step], {"reason": exc.details.get("reason")})) from exc
    for s in ("parse", "single_select", "read_only", "allowed_objects"):
        ok(s)

    # Alias every governed table (the scope injector keeps the alias, so `projects.region` keeps working).
    cte_names = {_norm(c.alias) for c in tree.find_all(exp.CTE)}
    aliases: dict[str, ModelEntity] = {}
    for table in tree.find_all(exp.Table):
        if not table.db and _norm(table.name) in cte_names:
            continue
        qualified = _norm(f"{table.db}.{table.name}" if table.db else table.name)
        ent = lookup[qualified]
        if not table.db:  # qualify so the injector and assert_scoped see the governed object
            schema, _, _ = ent.source_object.rpartition(".")
            if schema:
                table.set("db", exp.to_identifier(schema))
        alias = table.alias or table.name
        if not table.alias:
            table.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
        aliases[_norm(alias)] = ent
    _columns_to_aliases(tree)

    hidden = scope.hidden_fields
    try:
        _check_columns(tree, aliases, hidden, simple=not cte_names and not list(tree.find_all(exp.Subquery)))
    except TelaError as exc:
        raise fail("visible_fields", exc) from exc
    ok("visible_fields")

    for lit in tree.find_all(exp.Literal):
        if lit.is_string and SENTINEL in str(lit.this).lower():
            raise fail("placeholder_position", TelaError(ErrorCode.SQL_PLACEHOLDER_MISPLACED,
                                                         ":page_filters can't be inside a text value."))
    marks = _sentinels(tree)
    for m in marks:
        node: exp.Expression | None = m.parent
        while isinstance(node, exp.And | exp.Paren):
            node = node.parent
        if not isinstance(node, exp.Where):
            raise fail("placeholder_position", TelaError(
                ErrorCode.SQL_PLACEHOLDER_MISPLACED, "Put :page_filters in the WHERE clause, joined with AND."))
    ok("placeholder_position", None if marks else "not used")

    probe = substitute(tree.copy(), [], aliases, Params("chk"))[0]
    sp = Params("s")
    try:
        scoped = inject(probe, snap, scope, sp)
        assert_scoped(scoped.sql(dialect=dialect), dialect, snap, scope, sp.values)
    except TelaError as exc:
        raise fail("scope_applied", exc) from exc
    ok("scope_applied", scope.summary or "All rows")

    limit = tree.args.get("limit")
    requested = None
    if limit is not None:
        try:
            requested = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            raise fail("limits", TelaError(ErrorCode.SQL_FORBIDDEN_NODE, "LIMIT must be a whole number.")) from None
    ok("limits", "10,000 rows, 20 s")
    warnings = [] if marks else [MSG_NO_PLACEHOLDER]
    ast_hash = hashlib.sha256(tree.sql(dialect=dialect, normalize=True).encode()).hexdigest()
    return Prepared(tree, dialect, aliases, bool(marks), ast_hash, requested, checks, warnings)


def _columns_to_aliases(tree: exp.Expression) -> None:
    """Point a column written with its table's schema (``dbo.t.col``) at the table's alias.

    The scope injector replaces the table with a subquery under its alias, so ``dbo.t`` is no longer in the FROM
    and the database can't bind ``dbo.t.col`` (run 01a1ce49: SQL Server error 4104 on Dataverse). A table used
    twice under two aliases is left alone: the column is ambiguous, as it is in the database."""
    by_object: dict[str, set[str]] = {}
    for table in tree.find_all(exp.Table):
        if table.db and table.alias:
            by_object.setdefault(_norm(f"{table.db}.{table.name}"), set()).add(table.alias)
    for column in tree.find_all(exp.Column):
        db = column.args.get("db")
        if db is None or not column.table:
            continue
        found = by_object.get(_norm(f"{db.name}.{column.table}"), set())
        if len(found) == 1:
            column.set("catalog", None)
            column.set("db", None)
            column.set("table", exp.to_identifier(next(iter(found))))


def _check_columns(tree: exp.Expression, aliases: dict[str, ModelEntity], hidden: frozenset[str],
                   simple: bool) -> None:
    hidden_cols: dict[str, str] = {}
    visible: dict[str, set[str]] = {}
    for alias, ent in aliases.items():
        visible[alias] = set()
        for f in ent.fields:
            col = f.source_column.lower()
            if f"{ent.key}.{f.key}" in hidden:
                hidden_cols[col] = f.label
            else:
                visible[alias].add(col)
    output_aliases = {_norm(a.alias) for a in tree.find_all(exp.Alias)}
    for column in tree.find_all(exp.Column):
        name = _norm(column.name)
        if isinstance(column.this, exp.Star) or not name:
            continue
        table = _norm(column.table) if column.table else None
        if table in aliases:
            if name in hidden_cols and name not in visible[table]:
                raise TelaError(ErrorCode.SQL_FIELD_HIDDEN, f"{hidden_cols[name]} is not available to your role.")
            if name not in visible[table]:
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, f"'{column.name}' is not a field of the approved "
                                "model.")
            continue
        if table is None:
            if any(name in v for v in visible.values()):
                continue
            if name in hidden_cols:
                raise TelaError(ErrorCode.SQL_FIELD_HIDDEN, f"{hidden_cols[name]} is not available to your role.")
            if simple and name not in output_aliases:
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, f"'{column.name}' is not a field of the approved "
                                "model.")


def substitute(tree: exp.Expression, page_filters: list[dict[str, Any]], aliases: dict[str, ModelEntity],
               p: Params, snap: ModelSnapshot | None = None) -> tuple[exp.Expression, list[str], list[str]]:
    """Replace the sentinel with the page-filter predicate on columns the query exposes, or TRUE (RULE-007)."""
    applied, skipped = [], []
    conds: list[exp.Expression] = []
    for f in page_filters:
        ref = f["ref"]
        target = None
        if snap is not None and ref.startswith("dimension:"):
            dim = snap.dimension(ref.split(":", 1)[1])
            if dim is not None:
                for alias, ent in aliases.items():
                    if ent.key == dim.entity:
                        fld = ent.field(dim.field)
                        target = exp.column(fld.source_column, table=alias, quoted=True) if fld else None
                        break
        if target is None or not f.get("values"):
            skipped.append(ref)
            continue
        values = f["values"]
        if f.get("op") in ("=", None) and len(values) == 1:
            conds.append(exp.EQ(this=target, expression=p.add(values[0])))
        else:
            node = exp.In(this=target, expressions=[p.add(v) for v in values])
            conds.append(exp.Not(this=node) if f.get("op") in ("not_in", "!=") else node)
        applied.append(ref)
    replacement: exp.Expression = exp.paren(exp.and_(*conds)) if conds else exp.EQ(
        this=exp.Literal.number(1), expression=exp.Literal.number(1))
    for m in _sentinels(tree):
        m.replace(replacement.copy())
    return tree, applied, skipped


def output_columns(tree: exp.Expression) -> list[ColumnMeta]:
    select = tree if isinstance(tree, exp.Select) else next(iter(tree.find_all(exp.Select)))
    out = []
    for i, e in enumerate(select.expressions):
        name = e.alias_or_name or f"column_{i + 1}"
        is_measure = bool(list(e.find_all(exp.AggFunc)))
        label = name.replace("_", " ").strip().capitalize() or f"Column {i + 1}"
        out.append(ColumnMeta(name, "measure" if is_measure else "dimension", label,
                              f"sql:{name}", "decimal" if is_measure else "text",
                              "integer" if is_measure and isinstance(e.unalias(), exp.Count) else None))
    return out


def compiled(prepared: Prepared, page_filters: list[dict[str, Any]], snap: ModelSnapshot) -> tuple[
        CompiledQuery, list[str], list[str]]:
    p = Params()
    tree, applied, skipped = substitute(prepared.tree.copy(), page_filters, prepared.aliases, p, snap)
    entities = {alias: ent.key for alias, ent in prepared.aliases.items()}
    cq = CompiledQuery(tree, p, output_columns(tree), entities, False, prepared.dialect, {})
    return cq, applied, skipped


# --------------------------------------------------------------------------- check tokens


def issue_token(key: bytes, *, user_id: str, connection_id: str, ast_hash: str) -> str:
    body = json.dumps({"u": user_id, "c": connection_id, "h": ast_hash, "exp": int(time.time()) + TOKEN_TTL_S},
                      separators=(",", ":")).encode()
    sig = hmac.new(key, body, hashlib.sha256).digest()
    b64 = base64.urlsafe_b64encode
    return b64(body).decode().rstrip("=") + "." + b64(sig).decode().rstrip("=")


def verify_token(key: bytes, token: str, *, user_id: str, connection_id: str, ast_hash: str) -> None:
    bad = TelaError(ErrorCode.PRECONDITION_FAILED, "Check the query again before running it.")
    try:
        b64, s64 = token.split(".", 1)
        body = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        sig = base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4))
    except (ValueError, TypeError) as exc:
        raise bad from exc
    if not hmac.compare_digest(sig, hmac.new(key, body, hashlib.sha256).digest()):
        raise bad
    claims = json.loads(body)
    if claims.get("exp", 0) < time.time() or claims.get("u") != user_id or claims.get("c") != connection_id \
            or claims.get("h") != ast_hash:
        raise bad


def statement_text(sql: str) -> str:
    """Normalise for storage: strip one trailing semicolon and surrounding whitespace."""
    return sql.strip().removesuffix(";").rstrip()


