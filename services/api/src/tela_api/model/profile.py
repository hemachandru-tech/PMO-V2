"""Data profile for the model builder: what each column holds, read with counts and short value lists only.

Names and types say little about how to query a column. The profile reads the data itself, through the scoped query
path of the approved version, and writes what it finds onto the model, for any domain and any SQL engine:

* **empty** (no value in any row) and **constant** (one value) columns are hidden from the planners and the LLM;
* **secret-like** columns (passwords, tokens, hashes by name) are hidden always and never profiled for values;
* **identifiers**: unique per row and code-like ("A-1042", a GUID, a number key); described as "use to look up, join
  or count distinct, never to group by", and listed after descriptive columns;
* **vocabularies**: text columns with few distinct values keep their exact values, so a question's "done" or "coach"
  is matched to the column that really holds it (and written as stored);
* **dates stored as text**: the format is detected from the values (ISO, day-first or month-first) and the conversion
  expression for the SQL dialect is written into the description;
* **flags** (0/1) and **JSON** documents are described for what they are;
* **joins**: a column with the same name in two tables, unique in one of them, becomes a link when the data shows that
  nearly all of its values in the other table are found there.

Values go to the model only for low-cardinality, non-personal text columns; the model reaches the LLM only through the
tenant's data policy (``allow_samples``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tela_api.content.sql_context import sql_name, sql_object
from tela_api.identity.principal import Principal
from tela_api.query.service import QueryContext
from tela_common.errors import TelaError

PROFILE_COLUMNS = 60  # columns per profile query
VOCAB_MAX = 60  # a text column with at most this many distinct values keeps them (a key/value table's keys too)
ID_MIN_ROWS = 10  # unique columns in tables smaller than this are not called identifiers
LINK_MATCH = 0.9  # share of a column's distinct values that must exist in the other table for a join
SECRET = re.compile(r"(pass(word|wd)?|secret|token|api_?key|hash|salt|otp)", re.I)
CODE = re.compile(r"^[A-Za-z0-9_\-:.#/]+$")  # no spaces: a code or key, not a name
HAS_DIGIT = re.compile(r"\d")  # an identifier holds a number somewhere ("P-001", a GUID); a one-word name doesn't
LONG_TEXT = ("text", "mediumtext", "longtext", "tinytext", "blob", "longblob", "mediumblob", "clob", "ntext", "image",
             "xml", "bytea", "varbinary")
DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?"), "YYYY-MM-DD"),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4}"), "slash"),
    (re.compile(r"^\d{1,2}-\d{1,2}-\d{4}"), "dash"),
    (re.compile(r"^\d{8}$"), "YYYYMMDD"),
]


@dataclass
class ColumnProfile:
    column: str
    rows: int
    filled: int
    distinct: int | None = None
    low: Any = None
    high: Any = None
    values: list[tuple[str, int]] = field(default_factory=list)
    date_format: str | None = None


def _long(source_type: str) -> bool:
    t = (source_type or "").lower()
    return t.startswith("json") or any(t.startswith(x) for x in LONG_TEXT)


async def _run(app: Any, principal: Principal, connection_id: str, version_id: str, sql: str) -> dict[str, Any] | None:
    try:
        result: dict[str, Any] = await app.state.query_service.execute_custom(
            sql, connection_id=connection_id, model_version_id=version_id, principal=principal,
            ctx=QueryContext(surface="preview", use_cache=False))
    except TelaError:
        return None
    return result


async def profile_entity(app: Any, principal: Principal, connection_id: str, version_id: str, entity: dict[str, Any],
                         source_types: dict[str, str], dialect: str | None) -> dict[str, ColumnProfile]:
    """Counts, distinct counts and min/max per visible column (one query per ``PROFILE_COLUMNS``), then the values of
    low-cardinality text columns and a sample of text that looks like dates."""
    table = sql_object(entity["source_object"], dialect)
    fields = [f for f in entity["fields"] if f.get("is_queryable", True) and not SECRET.search(f["source_column"])]
    out: dict[str, ColumnProfile] = {}
    for i in range(0, len(fields), PROFILE_COLUMNS):
        part = fields[i:i + PROFILE_COLUMNS]
        exprs = ["COUNT(*) AS n"]
        for j, f in enumerate(part):
            c = sql_name(f["source_column"], dialect)
            exprs.append(f"COUNT({c}) AS f{j}")
            if not _long(source_types.get(f["source_column"].lower(), "")):
                exprs += [f"COUNT(DISTINCT {c}) AS d{j}", f"MIN({c}) AS lo{j}", f"MAX({c}) AS hi{j}"]
        res = await _run(app, principal, connection_id, version_id, f"SELECT {', '.join(exprs)} FROM {table}")  # noqa: S608 - quoted identifiers
        if res is None and len(part) > 1:  # one column the approved version can't read fails the whole query:
            for f in part:                 # count the others one by one
                sub = await profile_entity(app, principal, connection_id, version_id,
                                           {**entity, "fields": [f]}, source_types, dialect)
                out.update(sub)
            continue
        if res is None or not res.get("rows"):
            continue
        cols = [c["name"] for c in res["columns"]]
        row = dict(zip(cols, res["rows"][0], strict=False))
        for j, f in enumerate(part):
            out[f["source_column"].lower()] = ColumnProfile(
                column=f["source_column"], rows=int(row.get("n") or 0), filled=int(row.get(f"f{j}") or 0),
                distinct=None if row.get(f"d{j}") is None else int(row[f"d{j}"]), low=row.get(f"lo{j}"),
                high=row.get(f"hi{j}"))
    # Values of low-cardinality text columns, and a sample of text that may hold dates.
    for f in fields:
        p = out.get(f["source_column"].lower())
        if p is None or not p.filled or f.get("data_type") != "text" or p.distinct is None:
            continue
        c = sql_name(f["source_column"], dialect)
        looks_date = any(rx.match(str(p.low or "")) for rx, _ in DATE_PATTERNS) and any(
            rx.match(str(p.high or "")) for rx, _ in DATE_PATTERNS)
        if p.distinct <= VOCAB_MAX:
            res = await _run(app, principal, connection_id, version_id,
                             f"SELECT {c} AS v, COUNT(*) AS n FROM {table} WHERE {c} IS NOT NULL GROUP BY {c} "  # noqa: S608
                             f"ORDER BY COUNT(*) DESC")
            if res and res.get("rows"):
                p.values = [(str(v), int(n)) for v, n in res["rows"][:VOCAB_MAX]]
        if looks_date:
            res = await _run(app, principal, connection_id, version_id,
                             f"SELECT DISTINCT {c} AS v FROM {table} WHERE {c} IS NOT NULL")  # noqa: S608
            if res and res.get("rows"):
                p.date_format = date_format([str(r[0]) for r in res["rows"][:300]])
    return out


def date_format(values: list[str]) -> str | None:
    """The format of dates written as text, from their values: ISO, compact, or day-first / month-first (decided by a
    part above 12); None when the values are not all dates or the order can't be told."""
    vals = [v.strip() for v in values if v and v.strip()]
    if not vals:
        return None
    for rx, name in DATE_PATTERNS:
        if all(rx.match(v) for v in vals):
            if name in ("YYYY-MM-DD", "YYYYMMDD"):
                return name
            sep = "/" if name == "slash" else "-"
            firsts = [int(v.split(sep)[0]) for v in vals]
            seconds = [int(v.split(sep)[1]) for v in vals]
            if any(x > 12 for x in firsts):
                return f"DD{sep}MM{sep}YYYY"
            if any(x > 12 for x in seconds):
                return f"MM{sep}DD{sep}YYYY"
            return None  # every part is 12 or less: the order can't be told from the data
    return None


def date_expression(column: str, fmt: str, dialect: str | None) -> str:
    """The SQL that turns a text date of ``fmt`` into a date, for the dialect."""
    py = {"YYYY-MM-DD": "%Y-%m-%d", "YYYYMMDD": "%Y%m%d", "DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y",
          "DD-MM-YYYY": "%d-%m-%Y", "MM-DD-YYYY": "%m-%d-%Y"}[fmt]
    if dialect == "mysql":
        return f"STR_TO_DATE({column}, '{py}')"
    if dialect == "tsql":
        style = {"YYYY-MM-DD": 23, "YYYYMMDD": 112, "DD/MM/YYYY": 103, "MM/DD/YYYY": 101, "DD-MM-YYYY": 105,
                 "MM-DD-YYYY": 110}[fmt]
        return f"CONVERT(date, {column}, {style})"
    return f"TO_DATE({column}, '{fmt}')"  # PostgreSQL, Oracle-style, DuckDB strptime-compatible names


PROFILE_KINDS = ("identifier", "json", "flag", "text_date", "category")
PROFILE_NOTES = re.compile(
    r"\s*(Always empty in this data: do not use\.|Secret value: never shown or queried\.|"
    r"Holds one value only \(.*?\)\.|"
    r"Identifier: unique per row\..*?never group or chart by it\.|JSON document: .*?whole document\.|"
    r"Date stored as text in .*?\)\.|Flag: 1 means yes, 0 means no\.)")


def clear_profile(f: dict[str, Any]) -> None:
    """Remove what an earlier profile wrote on a field (notes, kind, values), so a new profile replaces it instead of
    piling up when the model is generated again."""
    if f.get("description"):
        f["description"] = PROFILE_NOTES.sub("", f["description"]).strip() or None
    if f.get("semantic_type") in PROFILE_KINDS:
        f["semantic_type"] = None
    f.pop("values", None)


def _note(f: dict[str, Any], text: str) -> None:
    if text not in (f.get("description") or ""):
        f["description"] = ((f.get("description") or "").rstrip(". ") + ". " + text).lstrip(". ")


def apply_profile(definition: dict[str, Any], profiles: dict[str, dict[str, ColumnProfile]],
                  used: dict[str, set[str]], dialect: str | None) -> dict[str, int]:
    """Write the profile onto the model's fields; returns how many columns of each kind were found."""
    counts = {"empty": 0, "constant": 0, "secret": 0, "identifier": 0, "vocabulary": 0, "text_date": 0, "flag": 0,
              "json": 0}
    for e in definition["entities"]:
        prof = profiles.get(e["key"], {})
        keys = set(e.get("primary_key") or []) | used.get(e["key"], set())
        hidden: set[str] = set()
        for f in e["fields"]:
            col = f["source_column"]
            p = prof.get(col.lower())
            if p is None and not SECRET.search(col):
                # Not profiled this time (the approved version hides it, so it can't be read through the scoped
                # path): keep what the last profile found, and keep an empty or one-value column hidden.
                desc = f.get("description") or ""
                if ("Always empty in this data" in desc or "Holds one value only" in desc) and f["key"] not in keys:
                    f["is_queryable"] = False
                    hidden.add(f["key"])
                    counts["empty" if "Always empty" in desc else "constant"] += 1
                continue
            clear_profile(f)
            if SECRET.search(col):
                f["is_queryable"] = False
                f["sensitivity"] = "personal"
                _note(f, "Secret value: never shown or queried.")
                counts["secret"] += 1
                hidden.add(f["key"])
                continue
            if p is None:
                continue
            if p.filled == 0 and p.rows > 0:
                counts["empty"] += 1
                _note(f, "Always empty in this data: do not use.")
                if f["key"] not in keys:
                    f["is_queryable"] = False
                    hidden.add(f["key"])
                continue
            if f.get("data_type") == "json":
                counts["json"] += 1
                f["semantic_type"] = "json"
                _note(f, "JSON document: read one key with JSON_EXTRACT(column, '$.key'); never group by the "
                         "whole document.")
                continue
            if p.distinct == 1 and p.rows > 1:
                counts["constant"] += 1
                _note(f, f"Holds one value only ({p.low!r}).")
                if f["key"] not in keys:
                    f["is_queryable"] = False
                    hidden.add(f["key"])
                continue
            if (p.distinct is not None and p.filled >= ID_MIN_ROWS and p.distinct == p.filled
                    and f.get("data_type") in ("text", "integer") and CODE.match(str(p.low or ""))
                    and CODE.match(str(p.high or ""))
                    and (HAS_DIGIT.search(str(p.low or "")) or HAS_DIGIT.search(str(p.high or "")))):
                counts["identifier"] += 1
                f["semantic_type"] = "identifier"
                _note(f, "Identifier: unique per row. Use it to look up a record, join or count distinct; never "
                         "group or chart by it.")
                continue
            if p.date_format and f.get("data_type") == "text":
                counts["text_date"] += 1
                f["semantic_type"] = "text_date"
                _note(f, f"Date stored as text in {p.date_format}: compare or sort it as "
                         f"{date_expression(sql_name(col, dialect), p.date_format, dialect)}.")
                continue
            if f.get("data_type") in ("integer", "boolean") and p.distinct is not None and p.distinct <= 2 and {
                    str(p.low), str(p.high)} <= {"0", "1", "True", "False", "true", "false"}:
                counts["flag"] += 1
                f["semantic_type"] = "flag"
                _note(f, "Flag: 1 means yes, 0 means no.")
                continue
            if p.values and f.get("data_type") == "text" and f.get("sensitivity") != "personal":
                counts["vocabulary"] += 1
                f["values"] = [v for v, _ in p.values]
                f["semantic_type"] = f.get("semantic_type") or "category"
        if hidden:
            e["display_fields"] = [k for k in e.get("display_fields") or [] if k not in hidden]
    return counts


async def infer_links(app: Any, principal: Principal, connection_id: str, version_id: str,
                      definition: dict[str, Any], profiles: dict[str, dict[str, ColumnProfile]],
                      dialect: str | None) -> list[dict[str, Any]]:
    """Joins the data proves: a column name shared by two tables, unique (or the key) in one of them, whose values in
    the other table are found there at least ``LINK_MATCH`` of the time. Declared links are left as they are."""
    ents = {e["key"]: e for e in definition["entities"]}
    have = {(ln["from_entity"], tuple(ln["from_fields"]), ln["to_entity"]) for ln in definition.get("links", [])}
    by_col: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for e in definition["entities"]:
        for f in e["fields"]:
            if f.get("is_queryable", True) and f.get("data_type") in ("text", "integer"):
                by_col.setdefault(f["source_column"].lower(), []).append((e["key"], f))
    # Candidate pairs: the same column name in two tables, and "<table>_id" pointing at a table's one-column key
    # ("delivery_id" -> deliveries.id, "player_id" -> players.id), whatever the table is called.
    for e in definition["entities"]:
        pk = e.get("primary_key") or []
        if len(pk) != 1:
            continue
        pf = next((f for f in e["fields"] if f["key"] == pk[0]), None)
        if pf is None:
            continue
        table = e["source_object"].rpartition(".")[2].lower()
        stems = {table, table[:-1] if table.endswith("s") else table,
                 table[:-3] + "y" if table.endswith("ies") else table}
        for stem in stems:
            alias = f"{stem}_id"
            if alias != pf["source_column"].lower() and alias in by_col:
                by_col.setdefault(f"@{alias}", []).append((e["key"], pf))
                by_col[f"@{alias}"] += [h for h in by_col[alias] if h[0] != e["key"]]
    added: list[dict[str, Any]] = []
    for col, holders in by_col.items():
        if len(holders) < 2:
            continue
        parents = holders[:1] if col.startswith("@") else holders  # an alias group: its first holder is the key's table
        for parent_key, pf in parents:
            p = profiles.get(parent_key, {}).get(pf["source_column"].lower())
            unique = (pf["key"] in ents[parent_key].get("primary_key", [])) or (
                p is not None and p.distinct is not None and p.filled and p.distinct == p.filled)
            if not unique:
                continue
            for child_key, cf in holders:
                if child_key == parent_key or (child_key, (cf["key"],), parent_key) in have:
                    continue
                # A table's own key is never the child side ("id" is in every table), and a pair already linked
                # the other way (one-to-one) is not linked twice.
                if cf["key"] in ents[child_key].get("primary_key", []) or (parent_key, (pf["key"],), child_key) in have:
                    continue
                c, pt = sql_name(cf["source_column"], dialect), sql_name(pf["source_column"], dialect)
                sql = (f"SELECT COUNT(DISTINCT x.{c}) AS n, COUNT(DISTINCT CASE WHEN y.{pt} IS NOT NULL THEN x.{c} END)"  # noqa: S608
                       f" AS hit FROM {sql_object(ents[child_key]['source_object'], dialect)} x LEFT JOIN "
                       f"{sql_object(ents[parent_key]['source_object'], dialect)} y ON y.{pt} = x.{c}")
                res = await _run(app, principal, connection_id, version_id, sql)
                if not res or not res.get("rows"):
                    continue
                n, hit = (int(v or 0) for v in res["rows"][0][:2])
                if n and hit / n >= LINK_MATCH:
                    link = {"from_entity": child_key, "to_entity": parent_key, "from_fields": [cf["key"]],
                            "to_fields": [pf["key"]], "cardinality": "many_to_one", "origin": "proposed",
                            "accepted": True}
                    definition.setdefault("links", []).append(link)
                    have.add((child_key, (cf["key"],), parent_key))
                    added.append({**link, "match": round(hit / n, 3)})
    return added
