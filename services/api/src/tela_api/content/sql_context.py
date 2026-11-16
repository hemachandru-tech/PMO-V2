"""The schema AG-10 drafts custom SQL from: only the tables a question points to (EXEC-146 follow-up).

The whole catalogue went to the model before: on the Dataverse model every column of every table, over 64,000
characters, which a local 8B model can't read in time (qwen3:8b timed out after 110 s). Now:

* Tables are scored by the question's words against the table's name and label, a little for its columns, and the
  measures and breakdowns defined on it (by the words shared: "Open projects" outweighs "Open risks"). The best
  ``MAX_TABLES`` scoring at least ``KEEP`` of the best are kept, with any table the question names in full; a
  breakdown the question names that no kept table holds brings its table; then the tables that join them (accepted
  links only, RULE-014). Words that name a table are the subject, never a breakdown ("Risk project"). A question
  that names nothing gets the tables with the most measures.
* Each table lists at most ``MAX_COLUMNS`` columns, most useful first: keys and join columns, columns the question
  names, columns whose run-together name holds a question word ("kpi" in ``tpg_overallkpiname``, plain English
  variants such as state/status), columns measures and dimensions use, display columns, then the rest in model
  order; the count left out is said, so the model knows the list is partial. A Dataverse code column with a text
  twin (``x`` / ``xname``) is listed once, as its text, unless it is a key; roll-up helpers (``x_state``,
  ``x_date``) come last and say what they are (run 01a1ce59).
* The whole context stays under ``BUDGET`` characters.

Hidden fields stay out, as before. The context also names the SQL dialect and the certified rules of measures on
the listed tables (``MAX_MEASURES``, the question's first), so a draft uses the agreed conditions (EXEC-148).
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, Field
from sqlglot import exp

from tela_api.ask.planner import words
from tela_query_spec.expr import measure_fields


class SqlDraftOut(BaseModel):
    """What AG-10 returns, for the Custom SQL drafter and for Direct SQL in Ask (US-257). ``plan`` comes first, so the
    model lays out the steps (tables, joins, filters, calculations, order) before it writes the SQL."""

    plan: list[str] = Field(default_factory=list, max_length=8)
    sql: str = Field(max_length=8000)
    explanation: str = Field(default="", max_length=600)


# AG-10's instructions, in pieces: the Custom SQL drafter (US-080) asks for an aggregate with :page_filters; Direct
# SQL in Ask (US-257) asks for the frame's kind of answer and no placeholder.
AG10_ROLE = ("You are Tela's SQL drafter (AG-10). Write ONE read-only SELECT for the question, using only the tables "
             "and columns listed.")
AG10_PAGE_FILTERS = (" Put `:page_filters` once in the WHERE clause joined with AND. No comments, no semicolons, no "
                     "other tables. The draft is checked and never run automatically.\n")
AG10_AGGREGATE = (
    # EXEC-148: a local model listed every column of both tables and ran out of tokens; answer the question instead.
    "Answer with an aggregate query: select only the columns the question groups by and the count, sum or average it "
    "asks for, then GROUP BY those columns (no GROUP BY for a single figure). At most 6 columns; never list a "
    "table's columns or select *. Where a certified measure below matches the question, use its rule for the "
    "conditions instead of guessing values. Keep the explanation to one sentence."
)
AG10_SYSTEM = AG10_ROLE + AG10_PAGE_FILTERS + AG10_AGGREGATE

MAX_TABLES = 4  # tables the question points to; tables that join them come on top
MAX_JOINED = 6  # tables listed in all
KEEP = 0.4  # a table is kept when it scores at least this share of the best table's score
MAX_COLUMNS = 40  # columns listed per table (rule columns, named and described columns come first)
BUDGET = 32_000  # characters of the whole context
# A model with at most this many tables also gets a short catalogue of every table it did not pick (name and column
# names only), so a question whose words miss a table's name (``side-cam`` vs ``sidecam_run``) can still
# reach it. Bigger models keep the question-only context (a whole Dataverse catalogue made a local model time out).
SMALL_MODEL = 20
CATALOGUE_COLUMNS = 30  # column names per table in that catalogue
MAX_VALUES = 12  # distinct values listed for one column (values reach the model only when the tenant allows samples)
SIMPLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NAMES_AS_LISTED = ("Write every table and column name exactly as it is listed below: names are already quoted for "
                   "this SQL dialect where they need it (a schema or table name with a hyphen or a space must keep its "
                   "quotes, schema and table each quoted on their own).")
# Facts about each SQL dialect a model gets wrong without being told (no business terms, no table names).
DIALECT_RULES: dict[str, str] = {
    "mysql": ("MySQL rules: ONLY_FULL_GROUP_BY is on, so every selected expression that is not an aggregate must "
              "appear in GROUP BY exactly as selected (or select it through an alias of the same expression). There "
              "is no PERCENTILE_CONT, PERCENTILE_DISC or MEDIAN: compute a median or percentile with ROW_NUMBER() OVER "
              "(ORDER BY x) and COUNT(*) OVER (); never use GROUP_CONCAT for percentiles. For durations "
              "use TIMESTAMPDIFF; TIMEDIFF caps at 838 hours. Quote "
              "names with backticks. Never compare a DATE or DATETIME column with '' (an empty string): MySQL refuses "
              "it; a missing date is IS NULL."),
    "postgres": ("PostgreSQL rules: quote names with double quotes; medians and percentiles use PERCENTILE_CONT(...) "
                 "WITHIN GROUP (ORDER BY x); subtract timestamps or use EXTRACT(EPOCH FROM ...) for durations. Never "
                 "compare a date or timestamp column with '' (an empty string); a missing date is IS NULL."),
    "tsql": ("SQL Server rules: use TOP n, not LIMIT; quote names with [brackets]; medians use PERCENTILE_CONT(...) "
             "WITHIN GROUP (ORDER BY x) OVER (); durations use DATEDIFF."),
    "duckdb": "DuckDB rules: quote names with double quotes; MEDIAN(x) and QUANTILE_CONT(x, p) exist.",
}


def sql_name(name: str, dialect: str | None) -> str:
    """One identifier as the dialect writes it: bare when it is a plain name, quoted otherwise."""
    return str(exp.to_identifier(name, quoted=not SIMPLE_NAME.match(name)).sql(dialect=dialect or None))


def sql_object(source_object: str, dialect: str | None) -> str:
    """A ``schema.table`` source object as the dialect writes it, each part quoted on its own when it needs it
    (``shop-data.orders`` -> ```shop-data`.orders`` in MySQL), so a draft copies a name that parses."""
    schema, _, table = source_object.rpartition(".")
    return ".".join(sql_name(p, dialect) for p in (schema, table) if p)
# Plain English filler only: Ask's own stop list also drops "projects", which names a table here.
FILLER = frozenset({"the", "and", "for", "with", "from", "that", "this", "what", "which", "who", "how", "many", "much",
                    "per", "each", "every", "all", "any", "show", "list", "give", "get", "tell", "are", "was", "were",
                    "have", "has", "had", "will", "can", "there", "their", "them", "our", "its", "into", "than",
                    "number", "count", "total", "some", "something", "please"})


# Plain English variants a column name may use for a question word (no business terms: those are the pack's).
VARIANTS: dict[str, tuple[str, ...]] = {"state": ("status",), "status": ("state",), "colour": ("color",),
                                        "color": ("colour",)}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):  # orders / order, categories / category
        return w[:-3] + "y"
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in words(text.replace("_", " ")) if w not in FILLER and len(w) > 2}


def _asked(question: str) -> set[str]:
    """The question's terms plus each pair of neighbouring words run together, so "side-cam" or "side cam"
    meets a table or column whose name runs them together (``sidecam_run``)."""
    ws = words(question.replace("_", " "))
    joined = {_stem(a + b) for a, b in pairwise(ws) if a not in FILLER and b not in FILLER}
    return _terms(question) | joined


def _split(entity: str, ref: str) -> tuple[str, str]:
    return (ref.split(".", 1)[0], ref.split(".", 1)[1]) if "." in ref else (entity, ref)


def _forms(words_: set[str]) -> set[str]:
    """Each word with its plain singular forms ("coaches" -> coach, "batches" -> batch, "stories" -> story)."""
    out = set(words_)
    for w in words_:
        if len(w) > 4 and w.endswith("es"):
            out.add(w[:-2])
        if len(w) > 3 and w.endswith("e"):
            out.add(w[:-1])  # "coache" (the stem of "coaches") -> coach
    return out


def relevant_tables(snap: Any, question: str) -> list[str]:
    """Entity keys for the question: the best scored, then the tables that join them. A table scores for its name,
    its columns, its measures and breakdowns, the business rules the question names, and the stored values of its
    columns that the question uses ("coach" is a value of a user-type column, "done" a value of a stage column)."""
    asked = _asked(question)
    asked_forms = _forms(asked)
    raw = {w.lower() for w in words(question)}
    score: dict[str, float] = {e.key: 0.0 for e in snap.entities}
    for e in snap.entities:
        score[e.key] += 3 * len(asked & (_terms(e.key) | _terms(e.label) | _terms(e.source_object.split(".")[-1])))
        table = e.source_object.split(".")[-1].lower()
        if table in raw:  # the question names the table exactly ("deliveries"), not a table that contains the word
            score[e.key] += 2
        valued = {_stem(str(v).lower()) for f in e.fields if f.values and f.is_queryable
                  for v in f.values if len(str(v)) > 2}
        score[e.key] += 3 * min(len(asked_forms & valued), 3)
        cols = sum(1 for f in e.fields if asked & (_terms(f.label) | _terms(f.source_column) | _terms(f.key)))
        score[e.key] += min(cols, 2) * 0.5  # a "project_id" column makes a table about projects only a little
    for m in snap.measures:  # by the words shared: "Open projects" outweighs "Open risks" for "open projects"
        if m.entity in score:
            score[m.entity] += 3 * len(asked & (_terms(m.label) | _terms(m.key)))
    for r in getattr(snap, "rules", None) or []:  # a business term the question uses brings the table it is defined on
        if r.status == "ok" and r.entity in score:
            rule_words = _terms(r.term) | {t for s in r.synonyms for t in _terms(s)}
            whole = sum(1 for s in (r.term, *r.synonyms) if s and s.lower() in question.lower())
            score[r.entity] += 2 * len(asked & rule_words) + 4 * whole
    # Words that name a table are the subject ("projects"), not a breakdown ("Risk project", "Task project").
    subject = {w for e in snap.entities for t in (_terms(e.key), _terms(e.label)) if t and t <= asked for w in t}
    named: list[Any] = []  # breakdowns the question names
    for d in snap.dimensions:
        if d.entity in score and (shared := len((asked - subject) & (_terms(d.label) | _terms(d.key)))):
            score[d.entity] += 2 * shared
            named.append(d)
    top = max(score.values(), default=0.0)
    ranked = [k for k, s in sorted(score.items(), key=lambda kv: -kv[1]) if s > 0 and s >= top * KEEP]
    # Tables the question names in full: "regions", "risks" (not "project requests" for "projects")
    by_name = [e.key for e in snap.entities if any(t and t <= asked for t in (_terms(e.key), _terms(e.label)))]
    # A business term the question uses whole ("critical path") brings the table it is defined on, however much
    # more the other tables score.
    by_rule = [r.entity for r in getattr(snap, "rules", None) or [] if r.status == "ok" and r.entity in score
               and any(len(s) > 3 and s.lower() in question.lower() for s in (r.term, *r.synonyms))]
    picked = list(dict.fromkeys([*ranked[:MAX_TABLES], *sorted(by_name, key=lambda k: -score[k])]))[:MAX_TABLES]
    for k in dict.fromkeys(by_rule):
        if k not in picked and len(picked) < MAX_JOINED:
            picked.append(k)

    def held(w: str) -> bool:
        return any(d.entity in picked and w in (_terms(d.label) | _terms(d.key)) for d in named)

    for d in sorted(named, key=lambda d: -score[d.entity]):  # a breakdown word no picked table holds: its best holder
        words_ = (asked - subject) & (_terms(d.label) | _terms(d.key))
        if not all(held(w) for w in words_) and len(picked) < MAX_JOINED:
            picked.append(d.entity)
    if not picked:  # nothing named: the tables the model measures most
        counts = {e.key: sum(1 for m in snap.measures if m.entity == e.key) for e in snap.entities}
        picked = [k for k, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n > 0][:2] or \
            [e.key for e in snap.entities[:2]]
    out = list(picked)
    for a in picked:  # the tables on the join path between any two picked tables
        for b in picked:
            if a < b and (path := snap.join_path(a, b)):
                for link, _ in path:
                    for k in (link.from_entity, link.to_entity):
                        if k not in out:
                            out.append(k)
    return out[:MAX_JOINED]


def _pairs(visible: list[Any]) -> tuple[dict[str, Any], set[str], set[str]]:
    """Dataverse column pairs: ``x`` holds a code and ``xname`` its text (``tpg_overallkpi`` 3 /
    ``tpg_overallkpiname`` 'Red'; ``xyominame`` repeats it), and a roll-up ``x`` comes with ``x_state`` and
    ``x_date``, its calculation status and time, which a model reads as a state (run 01a1ce59: "KPI colour" became
    ``tpg_totalactual_state IN (1, 2, 3)``, 0 rows). Returns {text column: code field}, the code and yomi columns
    the text column stands for, and the roll-up helpers."""
    by_col = {f.source_column.lower(): f for f in visible}
    text_of: dict[str, Any] = {}
    covered: set[str] = set()
    helpers: set[str] = set()
    for c, f in by_col.items():
        if c + "name" in by_col:
            text_of[c + "name"] = f
            covered |= {c, c + "yominame"} & by_col.keys()
        for tail in ("_state", "_date"):
            if c.endswith(tail) and c[: -len(tail)] in by_col:
                helpers.add(c)
    return text_of, covered, helpers


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def rule_columns(snap: Any, e: Any) -> set[str]:
    """Lower-cased source columns of ``e`` that its checked business rules (and table-free rules) name."""
    names = {f.source_column.lower() for f in e.fields}
    out: set[str] = set()
    for r in getattr(snap, "rules", None) or []:
        if r.status == "ok" and r.sql and (r.entity == e.key or r.entity is None):
            out |= {w.lower() for w in _IDENT.findall(r.sql)} & names
    return out


def _parts(f: Any) -> set[str]:
    return {w for w in words(f"{f.source_column} {f.key}".replace("_", " ")) if len(w) > 2}


def _columns(snap: Any, e: Any, hidden: frozenset[str], asked: set[str],
             chosen: set[str]) -> tuple[list[Any], int, dict[str, str]]:
    """The columns listed for ``e``, how many more there are, and a note per column (a text column says whose
    text it is; a roll-up helper says what it is)."""
    visible = [f for f in e.fields if f"{e.key}.{f.key}" not in hidden and f.is_queryable]
    keys = set(e.primary_key)
    for link in snap.links:
        if link.accepted and link.from_entity == e.key and link.to_entity in chosen:
            keys |= set(link.from_fields)
        if link.accepted and link.to_entity == e.key and link.from_entity in chosen:
            keys |= set(link.to_fields)
    used: set[str] = set()
    for m in snap.measures:
        for ref in measure_fields(m.expression):
            ent, fk = _split(m.entity, ref)
            if ent == e.key:
                used.add(fk)
    used |= {d.field for d in snap.dimensions if d.entity == e.key}
    rule_cols = rule_columns(snap, e)  # columns a business rule on this table is written on: always listed
    text_of, covered, helpers = _pairs(visible)
    # A question word inside a run-together column name ("kpi" in tpg_overallkpiname); words naming the table
    # itself ("project") are left out, or every tpg_project* column would match.
    inside = {w for w in asked - _terms(e.key) - _terms(e.label) - _terms(e.source_object.split(".")[-1])
              if len(w) > 2}
    inside |= {v for w in inside for v in VARIANTS.get(w, ())}

    def rank(f: Any) -> int:
        col = f.source_column.lower()
        if f.key in keys or col in rule_cols:
            return 0
        if col in helpers:
            return 7
        code = text_of.get(col)
        named = [f, code] if code is not None else [f]
        if any(asked & (_terms(g.label) | _terms(g.source_column) | _terms(g.key)) for g in named):
            return 1
        if any(w in part for g in named for part in _parts(g) for w in inside if w != part):
            return 2
        if any(g.key in used for g in named):
            return 3
        if f.semantic_type in ("identifier", "json"):  # keys of other things and documents: after the descriptive
            return 6
        if any(g.key in e.display_fields for g in named):
            return 4
        return 5 if any((g.description or "").strip() for g in named) else 6

    listed = [f for f in visible if f.key in keys or f.source_column.lower() in rule_cols
              or f.source_column.lower() not in covered]
    ordered = sorted(listed, key=lambda f: (rank(f), listed.index(f)))
    notes: dict[str, str] = {}
    for f in ordered[:MAX_COLUMNS]:
        col = f.source_column.lower()
        if (code := text_of.get(col)) is not None:
            notes[f.key] = f"text of {code.source_column}"
        elif col in helpers:
            notes[f.key] = "roll-up calculation status/time, not a business value"
    return ordered[:MAX_COLUMNS], max(0, len(ordered) - MAX_COLUMNS), notes


def context_columns(snap: Any, hidden: frozenset[str], question: str) -> list[tuple[Any, Any]]:
    """(entity, field) for every column the context lists for this question, in the order it lists them: the
    columns whose values are worth showing the model come from here."""
    asked = _asked(question)
    tables = relevant_tables(snap, question)
    out: list[tuple[Any, Any]] = []
    for key in tables:
        e = next(x for x in snap.entities if x.key == key)
        cols, _, _ = _columns(snap, e, hidden, asked, set(tables))
        out += [(e, f) for f in cols]
    return out


MAX_MEASURES = 8  # certified measure rules listed: the question's first, then others on the listed tables


def ruled(term: str, rules: list[Any], snap: Any) -> bool:
    """True when a business rule covers the same thing as ``term`` (an agreed term or a measure label): the rule's own
    words, less the words naming its table, are all in ``term``, and ``term`` names the rule's table. "Open demand
    requests" is covered by the rule "Open requests" on the requests table, not by "Open projects"."""
    words = _terms(term)
    by_key = {e.key: e for e in snap.entities}
    for r in rules:
        e = by_key.get(r.entity) if r.entity else None
        if e is None:
            continue
        subject = _terms(e.key) | _terms(e.label) | _terms(e.source_object.split(".")[-1])
        for name in (r.term, *r.synonyms):
            core = _terms(name) - subject
            if core and core <= words and words & subject:
                return True
    return False


def _measures(snap: Any, listed: set[str], asked: set[str], rules: list[Any] | None = None) -> list[str]:
    """Certified rules for the listed tables, those the question's words name first (EXEC-148). A measure a business
    rule also defines is left out: the rule is the organisation's own definition."""
    ms = [m for m in snap.measures if m.entity in listed and m.status == "certified"
          and not (rules and ruled(m.label, rules, snap))]
    ms.sort(key=lambda m: -len(asked & (_terms(m.label) | _terms(m.key))))
    return [f"- {m.label} ({m.entity}): {m.plain_rule}" for m in ms[:MAX_MEASURES]]


def _values_note(values: list[str], asked: set[str] | None = None) -> str:
    """A column's values, those sharing a word with the question first (so ``net_sales`` leads for a question about
    sales among 44 metric names), at most ``MAX_VALUES`` shown and the rest counted."""
    asked = asked or set()
    ranked = sorted(values, key=lambda v: (-len(asked & _terms(str(v))), values.index(v)))
    shown = ", ".join(repr(v) for v in ranked[:MAX_VALUES])
    return "values: " + shown + (f" (+{len(values) - MAX_VALUES} more)" if len(values) > MAX_VALUES else "")


MAX_RULES = 20  # business rules listed: always, population, relabels, then the ones the question names
DESCRIPTION_CHARS = 110  # a column's description as listed


def _relabel(sql: str | None) -> bool:
    from tela_api.ask.rule_check import relabels  # the same test the rule check uses
    return bool(sql) and bool(relabels(sql or "", None))


def selected_rules(snap: Any, listed: set[str], question: str) -> list[Any]:
    """The model's business rules for this question: every checked rule ("ok") on the listed tables (or on no table),
    "always" rules first, then by how many of the question's words they share. Rules are short, so all of a table's
    rules go (a question says "exceeded their planned dates" where the rule says "overdue": word matching alone
    misses it); the SQL model picks the one that fits. Rules come from the approved model, never from code."""
    asked = _asked(question)
    by_key = {e.key: e for e in snap.entities}
    out: list[tuple[int, int, Any]] = []
    for i, r in enumerate(getattr(snap, "rules", None) or []):
        if r.status != "ok" or (r.entity is not None and r.entity not in listed):
            continue
        # Words that only name the rule's table ("order" in "Late order") don't make the question about the rule.
        e = by_key.get(r.entity) if r.entity else None
        subject = (_terms(e.key) | _terms(e.label) | _terms(e.source_object.split(".")[-1])) if e else set()
        named = (_terms(r.term) | {t for s in r.synonyms for t in _terms(s)}) - subject
        score = 3 * len(asked & named) + len((asked - subject) & _terms(r.meaning))
        # A phrase the question uses whole ("open projects") outweighs scattered words.
        score += 4 * sum(1 for s in (r.term, *r.synonyms) if s and s.lower() in question.lower())
        kind = getattr(r, "kind", "condition")
        if r.always:
            rank = 0
        elif kind == "population" or (kind == "display" and _relabel(r.sql)):
            rank = 1
        elif score > 0:
            rank = 2
        else:
            continue  # a rule the question doesn't touch only adds noise (fewer rules, fewer mixed-up conditions)
        out.append((rank, -score, i))
    out.sort()
    rules = list(getattr(snap, "rules", None) or [])
    return [rules[i] for _, _, i in out[:MAX_RULES]]


def _with_date(sql: str, as_of: str | None) -> str:
    """A rule's ``{data_date}`` placeholder written as the quoted data date (left as is when there is none)."""
    if not as_of:
        return sql
    return sql.replace("'{data_date}'", f"'{as_of}'").replace("{data_date}", f"'{as_of}'")


def rules_context(snap: Any, rules: list[Any], dialect: str | None, as_of: str | None = None) -> str:
    if not rules:
        return ""
    by_key = {e.key: e for e in snap.entities}
    lines = []
    for r in rules:
        where = f" [{sql_object(by_key[r.entity].source_object, dialect)}]" if r.entity in by_key else ""
        kind = getattr(r, "kind", "condition")
        tag = (" (always applies to this table)" if r.always else
               " (population: apply it when the question names these records without a status or state of its own)"
               if kind == "population" else
               " (display: when you show this column, select this expression instead, under the column's name)"
               if kind == "display" else "")
        lines.append(f"- {r.term}{where}{tag}: "
                     f"{r.meaning[:180]}" + (f" Condition: {_with_date(r.sql, as_of)}" if r.sql else ""))
    return ("Business rules of this model (the organisation's own definitions: when the question means a term below, "
            "write its condition exactly; apply every rule marked 'always applies' when you query that table; when a "
            "business rule and a certified measure or agreed term define the same thing differently, the business "
            "rule wins):\n" + "\n".join(lines))


def date_context(as_of: str | None) -> str:
    """The date the model's data counts as "today" (its reporting date, else the calendar date)."""
    if not as_of:
        return ""
    return (f"Today for this data (data date) is {as_of}. Wherever the question means today, now, overdue, ageing, "
            f"'last N days' or 'next N days', use the literal date '{as_of}', never CURDATE(), NOW(), "
            "CURRENT_DATE or GETDATE().")


def schema_context(snap: Any, hidden: frozenset[str], question: str, *, dialect: str | None = None,
                   values: dict[str, list[str]] | None = None, as_of: str | None = None) -> str:
    """AG-10's context for this question: the SQL dialect and its rules, the data date, the tables and links (names
    written as the dialect needs them, each column with its description), each column's distinct values when
    ``values`` gives them ({"entity.field": [...]}, only sent when the tenant allows samples), certified measure rules
    and the model's business rules the question needs."""
    asked = _asked(question)
    tables = relevant_tables(snap, question)
    chosen = set(tables)
    values = values or {}
    lines: list[str] = []
    listed: set[str] = set()
    size = 0
    for key in tables:
        e = next(x for x in snap.entities if x.key == key)
        cols, left, notes = _columns(snap, e, hidden, asked, chosen)
        for f in cols:
            desc = (f.description or "").strip()
            if desc:
                desc = desc[:DESCRIPTION_CHARS]
            if (vals := values.get(f"{e.key}.{f.key}")) is not None:
                notes[f.key] = "; ".join(x for x in (notes.get(f.key), _values_note(vals, asked)) if x)
            if desc:
                notes[f.key] = "; ".join(x for x in (desc, notes.get(f.key)) if x)
        head = f"- {sql_object(e.source_object, dialect)}"
        if e.description:
            head += f" ({e.description.strip()[:200]})"
        line = head + ": " + ", ".join(
            f"{sql_name(f.source_column, dialect)} ({f.data_type}{', ' + f.label if f.label else ''}"
            f"{'; ' + notes[f.key] if f.key in notes else ''})" for f in cols)
        if left:
            line += f" (+{left} more columns not listed)"
        if lines and size + len(line) > BUDGET:
            break
        lines.append(line[:BUDGET])
        listed.add(key)
        size += len(line)
    for link in snap.links:
        if link.accepted and link.from_entity in listed and link.to_entity in listed:
            lines.append(f"- link: {link.from_entity}.{link.from_fields} -> {link.to_entity}.{link.to_fields}")
    others = [e for e in snap.entities if e.key not in listed]
    catalogue: list[str] = []
    if others and len(snap.entities) <= SMALL_MODEL:
        for e in others:  # a small model: every other table, by name and column names, so none is out of reach
            cols = [f.source_column for f in e.fields if f"{e.key}.{f.key}" not in hidden and f.is_queryable]
            entry = f"- {sql_object(e.source_object, dialect)}: " + ", ".join(
                sql_name(c, dialect) for c in cols[:CATALOGUE_COLUMNS]) + (
                f" (+{len(cols) - CATALOGUE_COLUMNS} more)" if len(cols) > CATALOGUE_COLUMNS else "")
            if size + len(entry) > BUDGET:
                break
            catalogue.append(entry)
            size += len(entry)
    if catalogue:
        lines.append("Other tables (names and columns only; use one when the question is about it):")
        lines += catalogue
    if len(others) > len(catalogue):
        lines.append(f"({len(others) - len(catalogue)} other tables are not listed: they don't match the question.)")
    out = ("SQL dialect: " + dialect + "\n" + (DIALECT_RULES[dialect] + "\n" if dialect in DIALECT_RULES else "")
           + "\n" if dialect else "") + NAMES_AS_LISTED
    if when := date_context(as_of):
        out += "\n" + when
    out += "\n\nTables:\n" + "\n".join(lines)
    if rules := _measures(snap, listed, asked, selected_rules(snap, listed, question)):
        out += "\n\nCertified measures (their rules):\n" + "\n".join(rules)
    if business := rules_context(snap, selected_rules(snap, listed, question), dialect, as_of):
        out += "\n\n" + business
    return out
