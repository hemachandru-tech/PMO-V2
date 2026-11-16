"""Check drafted SQL against the model's business rules and name what it leaves out (no rule text in code).

A language model given the rules in its prompt still drops some of them (a default population, a display mapping, a
term the question names). This check reads the SQL with sqlglot and compares it with the rules the question was
given, so the omission goes back to the model by name for one repair:

* an **always** rule on a queried table must be in the WHERE clause (its columns appear in a filter);
* a table's **population** rule must be applied unless the query already filters that table on the population's
  columns itself (a question that names its own status, "On Hold", "Active", keeps it);
* a rule whose **term or synonym the question uses as a phrase** must be in the query: its table queried and its
  columns used;
* a **display** rule replaces its column when the query shows that column bare.

Filters are compared by the columns they use, not their text, so a rule written with a different date expression or
bracket style still counts as applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlglot import exp, parse_one

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DATE = "{data_date}"


@dataclass
class Gap:
    rule: Any
    why: str

    def text(self, as_of: str | None, object_name: str) -> str:
        cond = (self.rule.sql or "").replace(f"'{DATE}'", f"'{as_of}'").replace(DATE, f"'{as_of}'") if as_of else (
            self.rule.sql or "")
        return f"- {self.rule.term} [{object_name}]: {self.why}" + (f" Condition: {cond}" if cond else "")


def relabels(sql: str, dialect: str | None) -> bool:
    """True when a display rule only relabels values of one column, one stored value at a time (CASE WHEN c = 'Yellow'
    THEN 'Amber' ELSE c END, COALESCE(c, '(not set)')). A rule that groups several values into one label (IN lists,
    several values to one label) or bands a number or date (<, BETWEEN, a calculation) is a breakdown the question
    must ask for, not a label to force on every query showing the column."""
    try:
        tree = parse_one(f"SELECT {sql.replace(f'{chr(39)}{DATE}{chr(39)}', 'NULL').replace(DATE, 'NULL')}",
                         read=dialect)
    except Exception:  # noqa: BLE001
        return False
    node = tree.expressions[0] if tree.expressions else None
    if isinstance(node, exp.Alias):
        node = node.this
    if isinstance(node, (exp.Coalesce,)) or type(node).__name__ in ("Nvl", "IfNull"):
        return True
    if not isinstance(node, exp.Case):
        return False
    labels: list[str] = []
    simple = isinstance(node.this, exp.Column)  # CASE col WHEN 'a' THEN ... (the simple form)
    for when in node.args.get("ifs") or []:
        cond = when.this
        if simple:
            if not isinstance(cond, exp.Literal):
                return False
        elif not isinstance(cond, exp.EQ) or not isinstance(cond.right, exp.Literal):
            return False
        labels.append(str(when.args.get("true")))
    return len(labels) == len(set(labels))


def _words(text: str) -> set[str]:
    """Lower-case words of a name, singular ("Projects" and "project" are one word)."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")):
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def _cols(sql: str, names: set[str]) -> set[str]:
    return {w.lower() for w in IDENT.findall(sql)} & names


def _conjuncts(where: exp.Expression | None) -> list[exp.Expression]:
    if where is None:
        return []
    node = where.this if isinstance(where, exp.Where) else where
    out: list[exp.Expression] = []

    def walk(n: exp.Expression) -> None:
        while isinstance(n, exp.Paren):
            n = n.this
        if isinstance(n, exp.And):
            walk(n.left)
            walk(n.right)
        else:
            out.append(n)
    walk(node)
    return out


def _columns_of(node: exp.Expression) -> set[str]:
    return {c.name.lower() for c in node.find_all(exp.Column) if c.name}


def _pieces(sql: str, names: set[str], dialect: str | None) -> list[set[str]]:
    """The column sets of a rule's own AND-parts (one set for a rule with no top-level AND)."""
    probe = sql.replace(f"'{DATE}'", "'2000-01-01'").replace(DATE, "'2000-01-01'")
    try:
        tree = parse_one(f"SELECT 1 FROM t WHERE {probe}", read=dialect)  # noqa: S608 - parsed only, never run
    except Exception:  # noqa: BLE001
        return [_cols(sql, names)]
    parts = [_columns_of(p) & names for p in _conjuncts(tree.args.get("where"))]
    return [p for p in parts if p] or [_cols(sql, names)]


def _always_wholes(rules: list[Any], entity: str, by_key: dict[str, Any]) -> list[set[str]]:
    out = []
    for r in rules:
        if r.status == "ok" and r.always and r.sql and r.entity == entity:
            out.append(_cols(r.sql, {f.source_column.lower() for f in by_key[entity].fields}))
    return out


def rule_gaps(sql: str, snap: Any, question: str, rules: list[Any], dialect: str | None) -> list[Gap]:
    """The selected rules the SQL leaves out, each with why; [] when it applies them all (or can't be read)."""
    try:
        tree = parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - the checks before this one already parsed it; nothing to add
        return []
    by_table = {e.source_object.split(".")[-1].lower(): e for e in snap.entities}
    # A table may come as `schema`.`table`, or as one quoted name with a dot inside (`schema.table`).
    tables = [t.name.lower().split(".")[-1] for t in tree.find_all(exp.Table) if t.name]
    queried = {by_table[t].key for t in tables if t in by_table}
    by_key = {e.key: e for e in snap.entities}
    where_parts: list[exp.Expression] = []
    for sel in tree.find_all(exp.Select):
        where_parts += _conjuncts(sel.args.get("where"))
        for j in sel.args.get("joins") or []:
            if j.args.get("on") is not None:
                where_parts += _conjuncts(j.args["on"])
    filtered = [_columns_of(p) for p in where_parts]
    used = _columns_of(tree)
    bare: set[str] = set()
    for sel in tree.find_all(exp.Select):
        for e in sel.expressions:
            inner = e.this if isinstance(e, exp.Alias) else e
            if isinstance(inner, exp.Column) and inner.name:
                bare.add(inner.name.lower())
    q = question.lower()
    gaps: list[Gap] = []
    # Each always rule as its pieces (its own AND-parts): the SQL may write the rule whole or piece by piece.
    always_pieces: dict[str, list[set[str]]] = {}
    for r in rules:
        if r.status == "ok" and r.always and r.sql and r.entity in by_key:
            names = {f.source_column.lower() for f in by_key[r.entity].fields}
            always_pieces.setdefault(r.entity, []).extend(_pieces(r.sql, names, dialect))
    for r in rules:
        if r.status != "ok" or not r.sql or r.entity not in by_key:
            continue
        names = {f.source_column.lower() for f in by_key[r.entity].fields}
        rcols = _cols(r.sql, names)
        if not rcols:
            continue
        kind = getattr(r, "kind", "condition")
        ent = by_key[r.entity]
        subject = _words(ent.key) | _words(ent.label) | _words(ent.source_object.split(".")[-1])
        # A term that only names the table ("Project" for the project's name column) is not a term to enforce.
        named = any(len(n) > 3 and n.lower() in q and (_words(n) - subject) for n in (r.term, *r.synonyms) if n)
        if r.always:
            # Applied when the SQL holds the rule whole or each of its pieces (same columns as the piece).
            pieces = _pieces(r.sql, names, dialect)
            whole = any(f == rcols for f in filtered)
            if r.entity in queried and not whole and not all(p in filtered for p in pieces):
                gaps.append(Gap(r, "always applies to this table and is missing from the WHERE clause."))
        elif kind == "population":
            if r.entity not in queried:
                continue
            # A filter that is just an always rule (same columns) doesn't name a status; any other filter on the
            # population's columns does (the question chose its own records, e.g. "On Hold").
            own = [f for f in filtered if f & rcols and f not in always_pieces.get(r.entity, [])
                   and not any(f == a for a in _always_wholes(rules, r.entity, by_key))]
            if not own:
                gaps.append(Gap(r, "the question names these records without a status of its own, so this "
                                   "population applies and is missing."))
        elif kind == "display":
            shown = bare & rcols
            if r.entity in queried and shown and relabels(r.sql, dialect):
                gaps.append(Gap(r, f"select this expression instead of the bare column {', '.join(sorted(shown))}, "
                                   "under the same name."))
        elif named:
            if r.entity not in queried:
                # Bringing in another table needs a phrase ("critical path"); one word ("active") names a status of
                # whatever table the question is about.
                phrase = any(len(n.split()) >= 2 and n.lower() in q and (_words(n) - subject)
                             for n in (r.term, *r.synonyms) if n)
                if phrase:
                    gaps.append(Gap(r, "the question names this term, and the query does not use its table."))
            elif not rcols <= used:
                gaps.append(Gap(r, "the question names this term, and the query does not apply its condition."))
    return gaps


def repair_prompt(question: str, sql: str, gaps: list[Gap], snap: Any, as_of: str | None,
                  notes: list[str] | None = None) -> str:
    by_key = {e.key: e for e in snap.entities}
    lines = "\n".join([*(g.text(as_of, by_key[g.rule.entity].source_object) for g in gaps),
                        *(f"- {n}" for n in notes or [])])
    return (f"Question: {question}\nYour SQL:\n{sql}\nIt leaves out these business rules of the model:\n{lines}\n"
            "Rewrite the query so it applies each of them (join the table a rule needs on its link), keeping "
            "everything else the question asks for.")


ASKS = re.compile(r"\b(how many|how much|number of|count of|total|average|avg|sum of|share of|percentage of)\b", re.I)


def figures_missing(question: str, sql: str, dialect: str | None) -> str | None:
    """When the question asks for several figures ("how many coaches and how many students") and the SQL returns
    fewer aggregate columns, the note that sends it back; None otherwise. Breakdowns (GROUP BY) are left alone."""
    asked = len(ASKS.findall(question))
    if asked < 2:
        return None
    try:
        tree = parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return None
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if sel is None or sel.args.get("group"):
        return None
    found = sum(1 for e in sel.expressions if e.find(exp.AggFunc) is not None)
    if found >= asked:
        return None
    return (f"The question asks for {asked} figures and the query returns {found}: select each figure as its own "
            "aggregate column (for example SUM(CASE WHEN ... THEN 1 ELSE 0 END) AS one_count, ... AS other_count).")
