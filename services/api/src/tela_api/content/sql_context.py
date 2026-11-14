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

from typing import Any

from pydantic import BaseModel, Field

from tela_api.ask.planner import words
from tela_query_spec.expr import measure_fields


class SqlDraftOut(BaseModel):
    """What AG-10 returns, for the Custom SQL drafter and for Direct SQL in Ask (US-257)."""

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
MAX_COLUMNS = 40  # columns listed per table
BUDGET = 12_000  # characters of the whole context
# Plain English filler only: Ask's own stop list also drops "projects", which names a table here.
FILLER = frozenset({"the", "and", "for", "with", "from", "that", "this", "what", "which", "who", "how", "many", "much",
                    "per", "each", "every", "all", "any", "show", "list", "give", "get", "tell", "are", "was", "were",
                    "have", "has", "had", "will", "can", "there", "their", "them", "our", "its", "into", "than",
                    "number", "count", "total", "some", "something", "please"})


# Plain English variants a column name may use for a question word (no business terms: those are the pack's).
VARIANTS: dict[str, tuple[str, ...]] = {"state": ("status",), "status": ("state",), "colour": ("color",),
                                        "color": ("colour",)}


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in words(text.replace("_", " ")) if w not in FILLER and len(w) > 2}


def _split(entity: str, ref: str) -> tuple[str, str]:
    return (ref.split(".", 1)[0], ref.split(".", 1)[1]) if "." in ref else (entity, ref)


def relevant_tables(snap: Any, question: str) -> list[str]:
    """Entity keys for the question: the best scored, then the tables that join them."""
    asked = _terms(question)
    score: dict[str, float] = {e.key: 0.0 for e in snap.entities}
    for e in snap.entities:
        score[e.key] += 3 * len(asked & (_terms(e.key) | _terms(e.label) | _terms(e.source_object.split(".")[-1])))
        cols = sum(1 for f in e.fields if asked & (_terms(f.label) | _terms(f.source_column) | _terms(f.key)))
        score[e.key] += min(cols, 2) * 0.5  # a "project_id" column makes a table about projects only a little
    for m in snap.measures:  # by the words shared: "Open projects" outweighs "Open risks" for "open projects"
        if m.entity in score:
            score[m.entity] += 3 * len(asked & (_terms(m.label) | _terms(m.key)))
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
    picked = list(dict.fromkeys([*ranked[:MAX_TABLES], *sorted(by_name, key=lambda k: -score[k])]))[:MAX_TABLES]

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
    text_of, covered, helpers = _pairs(visible)
    # A question word inside a run-together column name ("kpi" in tpg_overallkpiname); words naming the table
    # itself ("project") are left out, or every tpg_project* column would match.
    inside = {w for w in asked - _terms(e.key) - _terms(e.label) - _terms(e.source_object.split(".")[-1])
              if len(w) > 2}
    inside |= {v for w in inside for v in VARIANTS.get(w, ())}

    def rank(f: Any) -> int:
        col = f.source_column.lower()
        if f.key in keys:
            return 0
        if col in helpers:
            return 6
        code = text_of.get(col)
        named = [f, code] if code is not None else [f]
        if any(asked & (_terms(g.label) | _terms(g.source_column) | _terms(g.key)) for g in named):
            return 1
        if any(w in part for g in named for part in _parts(g) for w in inside if w != part):
            return 2
        if any(g.key in used for g in named):
            return 3
        return 4 if any(g.key in e.display_fields for g in named) else 5

    listed = [f for f in visible if f.key in keys or f.source_column.lower() not in covered]
    ordered = sorted(listed, key=lambda f: (rank(f), listed.index(f)))
    notes: dict[str, str] = {}
    for f in ordered[:MAX_COLUMNS]:
        col = f.source_column.lower()
        if (code := text_of.get(col)) is not None:
            notes[f.key] = f"text of {code.source_column}"
        elif col in helpers:
            notes[f.key] = "roll-up calculation status/time, not a business value"
    return ordered[:MAX_COLUMNS], max(0, len(ordered) - MAX_COLUMNS), notes


MAX_MEASURES = 8  # certified measure rules listed: the question's first, then others on the listed tables


def _measures(snap: Any, listed: set[str], asked: set[str]) -> list[str]:
    """Certified rules for the listed tables, those the question's words name first (EXEC-148)."""
    ms = [m for m in snap.measures if m.entity in listed and m.status == "certified"]
    ms.sort(key=lambda m: -len(asked & (_terms(m.label) | _terms(m.key))))
    return [f"- {m.label} ({m.entity}): {m.plain_rule}" for m in ms[:MAX_MEASURES]]


def schema_context(snap: Any, hidden: frozenset[str], question: str, *, dialect: str | None = None) -> str:
    """AG-10's context for this question: the SQL dialect, the tables and links, and certified measure rules."""
    asked = _terms(question)
    tables = relevant_tables(snap, question)
    chosen = set(tables)
    lines: list[str] = []
    listed: set[str] = set()
    size = 0
    for key in tables:
        e = next(x for x in snap.entities if x.key == key)
        cols, left, notes = _columns(snap, e, hidden, asked, chosen)
        line = f"- {e.source_object}: " + ", ".join(
            f"{f.source_column} ({f.data_type}{', ' + f.label if f.label else ''}"
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
    others = len(snap.entities) - len(listed)
    if others:
        lines.append(f"({others} other tables are not listed: they don't match the question.)")
    out = ("SQL dialect: " + dialect + "\n\n" if dialect else "") + "Tables:\n" + "\n".join(lines)
    if rules := _measures(snap, listed, asked):
        out += "\n\nCertified measures (their rules):\n" + "\n".join(rules)
    return out
