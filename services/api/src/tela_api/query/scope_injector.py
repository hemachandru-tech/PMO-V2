"""Scope injection and independent verification (US-020; RULE-004, RULE-005; ADR-009).

``inject`` replaces every governed table reference ``FROM t AS a`` with
``FROM (SELECT <visible columns> FROM t WHERE <scope>) AS a``. Filters stay in
the outer query, so they are ANDed after scope and can only narrow it.

``assert_scoped`` then re-parses the final SQL text and checks, without
trusting the injector, that every table reference is a governed object wrapped
in exactly the expected scope predicate, that no CTE name shadows a governed
table, and that hidden columns are not selected. Any doubt fails closed.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from tela_api.core.metrics import SCOPE_DENIALS
from tela_api.identity.scope import ResolvedCondition, ResolvedScope
from tela_api.query.compiler import Params, _and, _or, _true, table_of
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelEntity, ModelSnapshot

REWRITE_FAILED_MESSAGE = "Could not apply your data rules to this query, so it was not run."


def _fail(reason: str, **detail: Any) -> TelaError:
    SCOPE_DENIALS.inc()
    return TelaError(ErrorCode.SCOPE_REWRITE_FAILED, REWRITE_FAILED_MESSAGE,
                     {"admin_detail": {"reason": reason, **detail}})


def _norm(name: str) -> str:
    return name.strip('"').lower()


def governed_objects(snapshot: ModelSnapshot) -> dict[str, ModelEntity]:
    return {_norm(e.source_object): e for e in snapshot.entities}


def _qualified(table: exp.Table) -> str:
    return _norm(f"{table.db}.{table.name}" if table.db else table.name)


def _condition(entity: ModelEntity, cond: ResolvedCondition, p: Params) -> exp.Expression:
    f = entity.field(cond.field)
    if f is None:
        raise _fail("scope_field_not_in_model", entity=entity.key, field=cond.field)
    column = exp.column(f.source_column, quoted=True)
    if cond.op == "is null":
        return exp.Is(this=column, expression=exp.Null())
    if cond.op == "=":
        return exp.EQ(this=column, expression=p.add(cond.values[0]))
    node = exp.In(this=column, expressions=[p.add(v) for v in cond.values])
    if cond.op == "not in":
        return exp.Not(this=node)
    if cond.op == "in":
        return node
    raise _fail("scope_operator", op=cond.op)


def scope_predicate(entity: ModelEntity, scope: ResolvedScope, p: Params,
                    snapshot: ModelSnapshot | None = None, depth: int = 0) -> exp.Expression:
    es = scope.for_entity(entity.key)  # raises scope_unresolved when no policy covers the entity
    if es.all_rows:
        return _true()
    if not es.alternatives and es.via is None:
        raise _fail("empty_scope", entity=entity.key)
    parts = [_or([_and([_condition(entity, c, p) for c in alt]) for alt in es.alternatives])] \
        if es.alternatives else []
    if es.via is not None:
        parts.append(_via_parent(entity, es.via, scope, p, snapshot, depth))
    return _and(parts)


def _via_parent(entity: ModelEntity, via: Any, scope: ResolvedScope, p: Params, snapshot: ModelSnapshot | None,
                depth: int) -> exp.Expression:
    """``<link field> IN (SELECT <parent key> FROM <parent> WHERE <parent scope>)`` (US-155): the parent reference
    is a governed table wrapped in its own scope, so ``assert_scoped`` checks it like any other."""
    parent = snapshot.entity(via.parent) if snapshot is not None else None
    if parent is None or depth >= 3:
        raise _fail("scope_parent_not_in_model", entity=entity.key, parent=via.parent)
    child_f, parent_f = entity.field(via.field), parent.field(via.parent_field)
    if child_f is None or parent_f is None:
        raise _fail("scope_link_field_not_in_model", entity=entity.key, parent=via.parent)
    inner = (exp.select(exp.column(parent_f.source_column, quoted=True))
             .from_(table_of(parent))
             .where(scope_predicate(parent, scope, p, snapshot, depth + 1)))
    return exp.In(this=exp.column(child_f.source_column, quoted=True), query=exp.Subquery(this=inner))


def visible_columns(entity: ModelEntity, snapshot: ModelSnapshot, hidden: frozenset[str]) -> list[str]:
    """Source columns exposed by the scoped derived table: visible fields plus keys needed for joins."""
    keys = set(entity.primary_key)
    for link in snapshot.links:
        if link.from_entity == entity.key:
            keys |= set(link.from_fields)
        if link.to_entity == entity.key:
            keys |= set(link.to_fields)
    cols = [f.source_column for f in entity.fields
            if f.key in keys or (f.is_queryable and f"{entity.key}.{f.key}" not in hidden)]
    return list(dict.fromkeys(cols))


def inject(tree: exp.Select, snapshot: ModelSnapshot, scope: ResolvedScope, p: Params) -> exp.Select:
    governed = governed_objects(snapshot)
    cte_names = {_norm(c.alias) for c in tree.find_all(exp.CTE)}
    clash = cte_names & ({q.rpartition(".")[2] for q in governed} | set(governed))
    if clash:
        raise _fail("cte_shadows_governed_table", names=sorted(clash))
    for table in list(tree.find_all(exp.Table)):
        if not table.db and _norm(table.name) in cte_names:
            continue
        entity = governed.get(_qualified(table))
        if entity is None:
            raise _fail("object_not_in_model", object=_qualified(table))
        alias = table.alias
        if not alias:
            raise _fail("unaliased_table", object=_qualified(table))
        cols = visible_columns(entity, snapshot, scope.hidden_fields)
        inner = (exp.select(*(exp.column(c, quoted=True) for c in cols))
                 .from_(table_of(entity))
                 .where(scope_predicate(entity, scope, p, snapshot)))
        table.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias))))
    return tree


def _inline(node: exp.Expression, params: dict[str, Any]) -> str:
    """Render a predicate with placeholders replaced by literals, for comparison only."""
    def sub(n: exp.Expression) -> exp.Expression:
        if isinstance(n, exp.Placeholder):
            v = params.get(n.name)
            if isinstance(v, bool):
                return exp.Boolean(this=v)
            if isinstance(v, int | float):
                return exp.Literal.number(v)
            return exp.Literal.string(str(v))
        return n
    return str(node.copy().transform(sub).sql(dialect="postgres", normalize=True))


def _same_name(e: exp.Expression) -> exp.Expression:
    """T-SQL output names every derived-table column (``[c] AS [c]``): the same projection as ``c``."""
    if isinstance(e, exp.Alias) and isinstance(e.this, exp.Column) and _norm(e.this.name) == _norm(e.alias):
        return e.this
    return e


def assert_scoped(sql: str, dialect: str, snapshot: ModelSnapshot, scope: ResolvedScope,
                  params: dict[str, Any]) -> None:
    """Independent post-rewrite check (S13 RULE-018 ``assert_scoped``). Raises scope_rewrite_failed."""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise _fail("reparse_failed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise _fail("not_single_statement")
    tree = statements[0]
    governed = governed_objects(snapshot)
    cte_names = {_norm(c.alias) for c in tree.find_all(exp.CTE)}
    if cte_names & ({q.rpartition(".")[2] for q in governed} | set(governed)):
        raise _fail("cte_shadows_governed_table")
    seen = 0
    for table in tree.find_all(exp.Table):
        if not table.db and _norm(table.name) in cte_names:
            continue
        entity = governed.get(_qualified(table))
        if entity is None:
            raise _fail("unscoped_reference", object=_qualified(table))
        frm = table.parent
        select = frm.parent if isinstance(frm, exp.From) else None
        if not isinstance(select, exp.Select) or not isinstance(select.parent, exp.Subquery):
            raise _fail("governed_table_not_wrapped", object=_qualified(table))
        if select.args.get("joins") or select.args.get("where") is None:
            raise _fail("scoped_table_shape", object=_qualified(table))
        projections = [_same_name(e) for e in select.expressions]
        if any(isinstance(e, exp.Star) or not isinstance(e, exp.Column) or e.table for e in projections):
            raise _fail("scoped_table_projection", object=_qualified(table))
        selected = {_norm(e.name) for e in projections}
        hidden_cols = {f.source_column.lower() for f in entity.fields
                       if f"{entity.key}.{f.key}" in scope.hidden_fields}
        allowed = {c.lower() for c in visible_columns(entity, snapshot, scope.hidden_fields)}
        if (selected & hidden_cols) - allowed or not selected <= allowed:
            raise _fail("hidden_column_selected", object=_qualified(table))
        expected_params = Params("x")
        expected = _inline(scope_predicate(entity, scope, expected_params, snapshot), expected_params.values)
        actual = _inline(select.args["where"].this, params)
        if actual != expected:
            raise _fail("scope_predicate_mismatch", object=_qualified(table))
        seen += 1
    if seen == 0:
        raise _fail("no_governed_reference")
