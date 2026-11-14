"""The AI pack author's rules (US-237): what Tela drafts without a model, and the draft the model starts from.

Each function proposes one stage's items from the read schema (``conn.connection_objects``: names, types, keys) and
Tela's own value profile of text columns (US-199: the most frequent values with their row counts). Nothing here
calls the model or writes to the draft; ``author.py`` validates every proposal before it is shown.

* **entities** — ``catalog.baseline_definition``: one entity per table, one field per column, keys from the primary
  key, personal names tagged ``personal`` (as AG-04 does for a model), then written as pack items (the table and
  column names become the candidates a later install matches).
* **links** — foreign keys first, then names: a field named like another entity's single key field
  (``project_id`` → ``projects.project_id``). The cardinality is checked by two counts in ``author.py``.
* **measures** — per entity, at most ``BATCH``: the count; counts by each frequent state value, open ones and a
  share; sums and averages of money and effort; overdue and due in 30 days on due dates; average age on start dates.
* **dimensions** — low-cardinality text (from the profile), dates as time dimensions, and banded dates: age
  (``how_long``), lateness (``how_late``) and horizon (``when``); reason columns answer ``why``.
* **synonyms** — everyday words for measure labels ("Number of X" → "how many X").
* **glossary** — one term per measure whose label can be a term, read as that measure.

Filter values are only ever the profile's own values (DEC-27: the rules never need the model to see them).
"""

from __future__ import annotations

import re
from typing import Any

from tela_api.model.catalog import MONEY, PERSONAL, STATE, _key, baseline_definition
from tela_api.model.guards import TOP_VALUES

BATCH = 20  # measures proposed per entity at a time (the model's batch too)
STATE_VALUES = 6  # state values given their own count
DIMENSION_VALUES = 30  # a text column with more distinct values is not proposed as a breakdown
CLOSED = re.compile(r"(complet|closed|done|cancel|drop|archiv|expired|reject|deployed|resolved|inactive|finished)",
                    re.I)
DUE = re.compile(r"(due|planned|target|deadline|expected|go_?live|end_date|finish)")
START = re.compile(r"(created|opened|start|submitted|requested|raised|reported|logged|hold_date|on_hold)")
EFFORT = re.compile(r"(hours|effort|fte|story_points|points|man_days)")
REASON = re.compile(r"(reason|cause)")
IDENTIFIER = re.compile(r"(^|_)(id|key|code|number|no|guid|uuid)$")
TERM = re.compile(r"^[A-Za-z0-9][\w&/.-]*( [\w&/.-]+){0,3}$")  # glossary.GlossaryTerm.term
QUIET = ("personal", "confidential", "sensitive")
ASOF = {"asof_offset_days": 0}


def table_name(qualified: str) -> str:
    return qualified.rpartition(".")[2]


def plural(label: str) -> str:
    """The entity label as a lower-case plural noun ("Projects" → "projects", "Change request" → "change requests")."""
    word = label.strip().lower()
    return word if word.endswith("s") else f"{word}s"


def singular(label: str) -> str:
    word = label.strip().lower()
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


# --------------------------------------------------------------------------- entities


def objects_in(head: dict[str, Any], catalog: list[dict[str, Any]]) -> set[str]:
    """Qualified names of the catalogue tables some draft entity already reads (by its candidate names)."""
    names = {c.lower() for e in head.get("entities") or [] for c in e.get("object_candidates") or []}
    return {o["qualified_name"] for o in catalog if o["qualified_name"].lower() in names
            or table_name(o["qualified_name"]).lower() in names}


def pack_entity(ent: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    """A model entity from ``baseline_definition`` as a pack entity: the table and column names are the candidates;
    key fields are required."""
    pk = set(ent["primary_key"])
    fields = []
    for f in ent["fields"]:
        item: dict[str, Any] = {"key": f["key"], "label": f["label"], "data_type": f["data_type"],
                                "columns": [f["source_column"]]}
        if f["key"] in pk:
            item["required"] = True
        semantic = f.get("semantic_type") or ("identifier" if f["key"] in pk or (IDENTIFIER.search(f["key"])
                                              and f["data_type"] == "text") else None)
        if semantic:
            item["semantic_type"] = semantic
        if f.get("sensitivity") and f["sensitivity"] != "internal":
            item["sensitivity"] = f["sensitivity"]
        fields.append(item)
    name = table_name(obj["qualified_name"])
    return {"key": ent["key"], "label": ent["label"], "grain": f"One row per {singular(ent['label'])}",
            "object_candidates": [name], "primary_key": list(ent["primary_key"]), "fields": fields}


def entity_proposals(objects: list[dict[str, Any]], taken: set[str]) -> list[dict[str, Any]]:
    """One pack entity per table; a key the draft already has gets a number."""
    base, _ = baseline_definition(objects)
    by_name = {o["qualified_name"]: o for o in objects}
    out = []
    for ent in base["entities"]:
        key, n = ent["key"], 2
        while key in taken:
            key, n = f"{ent['key']}_{n}", n + 1
        taken.add(key)
        item = pack_entity({**ent, "key": key}, by_name[ent["source_object"]])
        out.append({"item": item, "reason": f"Table {ent['source_object']}: {len(item['fields'])} columns."})
    return out


# --------------------------------------------------------------------------- links


def _entity_of_object(head: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Catalogue qualified name (lower) → draft entity reading it."""
    by_table: dict[str, dict[str, Any]] = {}
    for e in head.get("entities") or []:
        for c in e.get("object_candidates") or []:
            by_table.setdefault(c.lower(), e)
    out = {}
    for o in catalog:
        qn = o["qualified_name"].lower()
        ent = by_table.get(qn) or by_table.get(table_name(qn))
        if ent is not None:
            out[qn] = ent
    return out


def _field_for(ent: dict[str, Any], column: str) -> str | None:
    return next((f["key"] for f in ent.get("fields") or [] if column.lower() in
                 {c.lower() for c in f.get("columns") or []}), None)


def link_proposals(head: dict[str, Any], catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Foreign keys between draft entities, then name-based links; links the draft has are left out."""
    have = {(ln.get("from_entity"), ln.get("to_entity"), tuple(ln.get("from_fields") or []))
            for ln in head.get("links") or []}
    ents = _entity_of_object(head, catalog)
    out: list[dict[str, Any]] = []

    def add(src: dict[str, Any], tgt: dict[str, Any], frm: list[str], to: list[str], reason: str,
            origin: str) -> None:
        ident = (src["key"], tgt["key"], tuple(frm))
        if ident in have or src["key"] == tgt["key"]:
            return
        have.add(ident)
        out.append({"item": {"from_entity": src["key"], "to_entity": tgt["key"], "from_fields": frm,
                             "to_fields": to, "cardinality": "many_to_one"}, "reason": reason, "origin": origin})

    for o in catalog:
        src = ents.get(o["qualified_name"].lower())
        if src is None:
            continue
        for fk in (o.get("keys") or {}).get("foreign") or []:
            tgt = ents.get(str(fk.get("ref_object") or "").lower())
            if tgt is None:
                continue
            frm = [k for c in fk["columns"] if (k := _field_for(src, c))]
            to = [k for c in fk["ref_columns"] if (k := _field_for(tgt, c))]
            if frm and len(frm) == len(fk["columns"]) and len(to) == len(fk["ref_columns"]):
                add(src, tgt, frm, to, f"Foreign key {o['qualified_name']}({', '.join(fk['columns'])}) → "
                    f"{fk['ref_object']}.", "foreign_key")
    keyed = {e["key"]: e["primary_key"][0] for e in head.get("entities") or [] if len(e.get("primary_key") or []) == 1}
    for src in head.get("entities") or []:
        for f in src.get("fields") or []:
            if f["key"] in src.get("primary_key", []):
                continue
            for tkey, pk in keyed.items():
                tgt = next(e for e in head["entities"] if e["key"] == tkey)
                if tkey != src["key"] and f["key"] == pk:
                    add(src, tgt, [f["key"]], [pk], f"{src['label']}.{f['label']} has the name of {tgt['label']}'s "
                        "key.", "name")
    return out


# --------------------------------------------------------------------------- measures


def _values(profile: list[dict[str, Any]] | None) -> list[str] | None:
    """A profile's non-blank values, most frequent first; None when unread or free text."""
    if profile is None or len(profile) >= TOP_VALUES:
        return None
    return [str(v["value"]) for v in profile if v.get("value") not in (None, "")]


def _quiet(f: dict[str, Any]) -> bool:
    return f.get("sensitivity") in QUIET or bool(PERSONAL.search(f["key"]))


def state_field(ent: dict[str, Any], profiles: dict[str, list[dict[str, Any]] | None]) -> dict[str, Any] | None:
    """The entity's status-like text field with readable values (``STATE`` by name)."""
    for f in ent.get("fields") or []:
        if f["data_type"] == "text" and STATE.search(f["key"]) and _values(profiles.get(f"{ent['key']}.{f['key']}")):
            return dict(f)
    return None


def measure_proposals(ent: dict[str, Any], profiles: dict[str, list[dict[str, Any]] | None],
                      taken: set[str]) -> list[dict[str, Any]]:
    """At most ``BATCH`` measures for one entity; keys the draft has are left out."""
    e, label, many = ent["key"], ent["label"], plural(ent["label"])
    one = singular(ent["label"])
    out: list[dict[str, Any]] = []

    def add(key: str, mlabel: str, expression: dict[str, Any], rule: str, definition: str, fmt: str,
            reason: str) -> None:
        key = _key(key)[:60]
        if key in taken or len(out) >= BATCH:
            return
        taken.add(key)
        out.append({"item": {"key": key, "label": mlabel[:80], "entity": e, "expression": expression,
                             "plain_rule": rule, "definition": definition, "format": fmt}, "reason": reason})

    add(f"{e}_count", f"Number of {many}", {"agg": "count"}, f"Counts every {one} record.",
        f"All {many}.", "integer", "Every entity needs its count.")
    state = state_field(ent, profiles)
    open_cond: dict[str, Any] | None = None
    if state is not None:
        values = _values(profiles.get(f"{e}.{state['key']}")) or []
        flabel = state["label"].lower()
        for v in values[:STATE_VALUES]:
            add(f"{e}_{v}", f"{v} {many}", {"agg": "count", "where": {"field": state["key"], "op": "=", "value": v}},
                f"Counts {many} whose {flabel} is {v}.", f"{many.capitalize()} with {flabel} {v}.", "integer",
                f"{v} is a frequent {flabel} value.")
        closed = [v for v in values if CLOSED.search(v)]
        if closed and len(closed) < len(values):
            open_cond = {"field": state["key"], "op": "not_in", "values": closed}
            add(f"open_{e}", f"Open {many}", {"agg": "count", "where": open_cond},
                f"Counts {many} whose {flabel} is not {', '.join(closed)}.",
                f"{many.capitalize()} not {', '.join(closed)}.", "integer",
                f"{', '.join(closed)} read as closed.")
        if values:
            top = values[0]
            add(f"{e}_{top}_pct", f"{top} share of {many} (%)", {
                "op": "div", "scale": 100,
                "left": {"agg": "count", "where": {"field": state["key"], "op": "=", "value": top}},
                "right": {"agg": "count"}}, f"{many.capitalize()} whose {flabel} is {top}, as a share of all "
                f"{many}.", f"Share of {many} with {flabel} {top}.", "percent", "A share between two counts.")
    for f in ent.get("fields") or []:
        name, flabel = f["key"], f["label"].lower()
        if f["key"] in ent.get("primary_key", []) or _quiet(f):
            continue
        if f["data_type"] in ("integer", "decimal") and (MONEY.search(name) or f.get("semantic_type") == "currency"):
            add(f"{e}_{name}_total", f"Total {flabel}", {"agg": "sum", "field": name},
                f"Sum of {flabel} over every {one}.", f"Total {flabel}.", "currency", "Money column.")
            add(f"{e}_{name}_avg", f"Average {flabel}", {"agg": "avg", "field": name},
                f"Average {flabel} over {many} that have one.", f"Average {flabel}.", "currency", "Money column.")
        elif f["data_type"] in ("integer", "decimal") and EFFORT.search(name):
            add(f"{e}_{name}_total", f"Total {flabel}", {"agg": "sum", "field": name},
                f"Sum of {flabel} over every {one}.", f"Total {flabel}.", "decimal", "Effort column.")
            add(f"{e}_{name}_avg", f"Average {flabel}", {"agg": "avg", "field": name},
                f"Average {flabel} over {many} that have one.", f"Average {flabel}.", "decimal", "Effort column.")
        elif f["data_type"] in ("date", "datetime") and DUE.search(name):
            past: list[dict[str, Any]] = [{"field": name, "op": "<", "value": ASOF}]
            soon: list[dict[str, Any]] = [{"field": name, "op": ">=", "value": ASOF},
                                          {"field": name, "op": "<=", "value": {"asof_offset_days": 30}}]
            note = ""
            if open_cond is not None:
                past.append(open_cond)
                soon.append(open_cond)
                note = " that are open"
            add(f"{e}_{name}_overdue", f"Overdue {many} ({flabel})", {"agg": "count", "where": {"all": past}},
                f"{many.capitalize()}{note} whose {flabel} is before the reporting date. A {flabel} on the reporting "
                "date is not overdue.", f"{many.capitalize()}{note} past their {flabel}.", "integer",
                "Due date: overdue.")
            add(f"{e}_{name}_due_30", f"{label} due in 30 days ({flabel})", {"agg": "count", "where": {"all": soon}},
                f"{many.capitalize()}{note} whose {flabel} is from the reporting date up to 30 days after it.",
                f"{many.capitalize()}{note} due within 30 days.", "integer", "Due date: due soon.")
        elif f["data_type"] in ("date", "datetime") and START.search(name):
            add(f"{e}_{name}_age", f"Average days since {flabel}", {
                "agg": "avg", "value": {"days_from": name, "days_to": ASOF}},
                f"Average whole days from {flabel} to the reporting date, over {many} with a {flabel}.",
                f"Mean age in days since {flabel}.", "decimal", "Start date: aging.")
    return out


# --------------------------------------------------------------------------- dimensions


AGE_BANDS = [{"label": "0–30 days", "upto": 30}, {"label": "31–60 days", "above": 30, "upto": 60},  # noqa: RUF001
             {"label": "61–90 days", "above": 60, "upto": 90}, {"label": "More than 90 days", "above": 90}]  # noqa: RUF001
LATE_BANDS = [{"label": "Not past due", "upto": 0}, {"label": "1–30 days", "above": 0, "upto": 30},  # noqa: RUF001
              {"label": "31–60 days", "above": 30, "upto": 60}, {"label": "61–90 days", "above": 60, "upto": 90},  # noqa: RUF001
              {"label": "More than 90 days", "above": 90}]
HORIZON_BANDS = [{"label": "Past due", "upto": -1}, {"label": "Next 30 days", "above": -1, "upto": 30},
                 {"label": "31–60 days", "above": 30, "upto": 60}, {"label": "61–90 days", "above": 60, "upto": 90},  # noqa: RUF001
                 {"label": "Later", "above": 90}]


def dimension_proposals(ent: dict[str, Any], profiles: dict[str, list[dict[str, Any]] | None],
                        taken: set[str]) -> list[dict[str, Any]]:
    e = ent["key"]
    out: list[dict[str, Any]] = []

    def add(key: str, item: dict[str, Any], reason: str) -> None:
        key = _key(key)[:60]
        if key in taken:
            key = _key(f"{e}_{key}")[:60]
        if key in taken:
            return
        taken.add(key)
        out.append({"item": {"key": key, "entity": e, **item}, "reason": reason})

    for f in ent.get("fields") or []:
        name, flabel = f["key"], f["label"]
        if name in ent.get("primary_key", []) or _quiet(f) or f.get("semantic_type") == "identifier":
            continue
        if f["data_type"] == "text":
            values = _values(profiles.get(f"{e}.{name}"))
            if values is None or not 2 <= len(values) <= DIMENSION_VALUES:
                continue
            item: dict[str, Any] = {"label": flabel, "field": name}
            if STATE.search(name):
                item["is_status"] = True
            if REASON.search(name):
                item.update(answers=["why"], not_set="Not recorded")
            else:
                item["not_set"] = "Not set"
            add(name, item, f"{len(values)} values: a breakdown.")
        elif f["data_type"] in ("date", "datetime") and (DUE.search(name) or START.search(name)):
            lower = flabel.lower()
            add(name, {"label": flabel, "field": name, "is_temporal": True}, "Date: trends over time.")
            if START.search(name):
                add(f"{name}_aging", {"label": f"Days since {lower}", "field": name, "answers": ["how_long"],
                                      "of": {"days_from": name, "days_to": ASOF}, "bands": AGE_BANDS,
                                      "not_set": "Not recorded"}, "Start date: how long.")
            else:
                add(f"{name}_lateness", {"label": f"Days past {lower}", "field": name, "answers": ["how_late"],
                                         "of": {"days_from": name, "days_to": ASOF}, "bands": LATE_BANDS,
                                         "not_set": "No date"}, "Due date: how late.")
                add(f"{name}_horizon", {"label": f"{flabel} horizon", "field": name, "answers": ["when"],
                                        "of": {"days_from": ASOF, "days_to": name}, "bands": HORIZON_BANDS,
                                        "not_set": "No date"}, "Due date: when.")
    return out


# --------------------------------------------------------------------------- synonyms and glossary


_WORDS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (re.compile(r"^Number of (.+)$", re.I), lambda m: [f"how many {m.group(1)}", f"count of {m.group(1)}"]),
    (re.compile(r"^Total (.+)$", re.I), lambda m: [f"sum of {m.group(1)}"]),
    (re.compile(r"^Average (.+)$", re.I), lambda m: [f"mean {m.group(1)}"]),
    (re.compile(r"^Overdue ([^(]+?)(?: \(.*\))?$", re.I), lambda m: [f"late {m.group(1)}"]),
    (re.compile(r"^Open (.+)$", re.I), lambda m: [f"active {m.group(1)}", f"live {m.group(1)}"]),
)


def synonym_proposals(head: dict[str, Any]) -> list[dict[str, Any]]:
    """Everyday words for measure labels the draft has no synonyms for."""
    out = []
    for m in head.get("measures") or []:
        if m.get("synonyms"):
            continue
        words: list[str] = []
        for pattern, make in _WORDS:
            if hit := pattern.match(str(m.get("label") or "")):
                words += make(hit)
        words = [w for w in dict.fromkeys(w.strip().lower() for w in words) if w != str(m["label"]).lower()]
        if words:
            out.append({"item": {"target": f"measure:{m['key']}", "label": m["label"], "synonyms": words},
                        "reason": "Everyday words for the label."})
    return out


def glossary_proposals(head: dict[str, Any], existing: set[str]) -> list[dict[str, Any]]:
    """A term per measure whose label can be a term (four words at most), read as that measure."""
    out = []
    for m in head.get("measures") or []:
        term = str(m.get("label") or "").strip()
        if not TERM.match(term) or len(term) > 40 or term.lower() in existing:
            continue
        existing.add(term.lower())
        means = str(m.get("definition") or m.get("plain_rule") or term)
        out.append({"item": {"term": term, "means": means[:80].rstrip(), "explain": str(m.get("plain_rule"))[:600],
                             "reading": m["key"]}, "reason": f"Read “{term}” as the measure {m['key']}."})
        if len(out) >= BATCH:
            break
    return out
