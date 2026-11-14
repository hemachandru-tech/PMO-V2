"""Prompts and reply schemas of the AI pack author (US-237; AG-04 pack mode, gateway task ``pack_author``).

The model sees, per call: the stage's instructions; the template pack's items of the same kind as worked examples
(PMO today: entities, measures, dimensions, knowledge items, trimmed); the read schema as names and types; the draft's
names it may refer to; and Tela's rules draft for the stage, which it may keep, correct or add to. With DEC-27 off
nothing else leaves Tela: no values, no counts, no rows. With DEC-27 on (the tenant's *send samples* switch, and a
local provider) up to ``SAMPLES`` frequent values of each non-personal, non-confidential text column are added, and the
values sent are kept in the agent run log.

Replies are typed loosely (an expression is a mapping) so a small model can fill them; ``author_checks`` holds every
item to the real schema and to the source.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from tela_api.model.catalog import _key
from tela_api.packs.studio.author_rules import BATCH, table_name

AGENT, TASK = "AG-04", "pack_author"
SAMPLES = 20  # DEC-27: frequent values per text column, local model only
EXAMPLES = 6  # template items shown per kind
PROMPT_CHARS = 24000

SYSTEM = (
    "You are Tela's pack author (AG-04, pack mode). A pack describes a kind of database for business people: "
    "entities and fields, links, measures with a plain-language rule, breakdowns, and the words people use. You "
    "draft one part of a new pack from a source's schema. You see table and column names and types{values}. Never "
    "invent tables, columns, entities, fields, measures or dimensions: use only the names listed. Never invent a "
    "filter value: {value_rule} Follow the worked examples from the template pack: the same style of keys, labels, "
    "plain rules and definitions. Keys are lowercase identifiers (letters, digits, underscores). Tela's rules have "
    "drafted this part already; keep what is right, correct what is wrong and add what is missing."
)
VALUES_ON = ", and a few frequent values of some text columns"
VALUES_OFF_RULE = ("you are not shown the data's values, so filter text fields only on values written in the rules "
                   "draft; Tela checks every value against the data and refuses one it doesn't hold.")
VALUES_ON_RULE = "filter text fields only on values listed for that column; Tela refuses any other value."


def system(samples: bool) -> str:
    return SYSTEM.format(values=VALUES_ON if samples else "",
                         value_rule=VALUES_ON_RULE if samples else VALUES_OFF_RULE)


# --------------------------------------------------------------------------- values the model may use, not read


TOKEN = re.compile(r"\[\[([a-z_][a-z0-9_]*):(\d+)\]\]")


class Masker:
    """With DEC-27 off, the rules draft goes to the model with each profile value replaced by a token such as
    ``[[status:1]]`` (the first value of ``status``), in rules, labels and keys alike. The model can keep or reuse a
    token but never sees the value; ``unmask`` puts the stored value back (``_key`` of it in a key), so Tela fills
    every filter value from its own profile. An unknown token is left as it is and fails the value check."""

    def __init__(self, values: dict[str, list[str]]) -> None:
        self.values = values
        self._pairs = sorted(((v, f, i) for f, vs in values.items() for i, v in enumerate(vs, 1) if v),
                             key=lambda t: -len(t[0]))

    def _mask_text(self, s: str, key: bool) -> str:
        for v, f, i in self._pairs:
            token = f"[[{f}:{i}]]"
            s = re.sub(rf"(?<![\w]){re.escape(v)}(?![\w])", token, s)
            if key:
                s = re.sub(rf"(?<![a-z0-9]){re.escape(_key(v))}(?![a-z0-9])", token, s)
        return s

    def mask(self, obj: Any, key: bool = False) -> Any:
        if isinstance(obj, str):
            return self._mask_text(obj, key)
        if isinstance(obj, list):
            return [self.mask(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.mask(v, key=k == "key") for k, v in obj.items()}
        return obj

    def _value(self, m: re.Match[str], key: bool) -> str:
        vals = self.values.get(m.group(1)) or []
        n = int(m.group(2))
        if not 1 <= n <= len(vals):
            return m.group(0)
        return _key(vals[n - 1]) if key else vals[n - 1]

    def unmask(self, obj: Any, key: bool = False) -> Any:
        if isinstance(obj, str):
            out = TOKEN.sub(lambda m: self._value(m, key), obj)
            return _key(out) if key and out != obj else out
        if isinstance(obj, list):
            return [self.unmask(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v, key=k == "key") for k, v in obj.items()}
        return obj


# --------------------------------------------------------------------------- replies


class FieldNote(BaseModel):
    column: str = Field(max_length=200)
    label: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    semantic_type: str | None = Field(default=None, max_length=40,
                                      description="currency, percent, identifier, email, phone, person_name, "
                                                  "free_text or null")
    sensitivity: str | None = Field(default=None, description="public, internal, confidential or personal")
    synonyms: list[str] = Field(default_factory=list, max_length=6)


class TableNote(BaseModel):
    table: str = Field(max_length=200)
    label: str | None = Field(default=None, max_length=80)
    grain: str | None = Field(default=None, max_length=120)
    synonyms: list[str] = Field(default_factory=list, max_length=6)
    fields: list[FieldNote] = Field(default_factory=list, max_length=200)


class EntitiesReply(BaseModel):
    tables: list[TableNote] = Field(default_factory=list, max_length=20)


class AiMeasure(BaseModel):
    key: str = Field(max_length=60)
    label: str = Field(max_length=80)
    expression: dict[str, Any]
    plain_rule: str = Field(max_length=600)
    definition: str | None = Field(default=None, max_length=300)
    format: str = "integer"
    synonyms: list[str] = Field(default_factory=list, max_length=6)
    reason: str | None = Field(default=None, max_length=300)


class MeasuresReply(BaseModel):
    measures: list[AiMeasure] = Field(default_factory=list, max_length=BATCH)


class AiDimension(BaseModel):
    key: str = Field(max_length=60)
    label: str = Field(max_length=80)
    field: str = Field(max_length=80)
    is_temporal: bool = False
    is_status: bool = False
    of: dict[str, Any] | None = None
    bands: list[dict[str, Any]] | None = None
    not_set: str | None = Field(default=None, max_length=60)
    answers: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list, max_length=6)
    reason: str | None = Field(default=None, max_length=300)


class DimensionsReply(BaseModel):
    dimensions: list[AiDimension] = Field(default_factory=list, max_length=BATCH)


class SynonymItem(BaseModel):
    target: str = Field(max_length=100, description="measure:key, dimension:key or entity:key, as listed")
    synonyms: list[str] = Field(min_length=1, max_length=6)


class SynonymsReply(BaseModel):
    items: list[SynonymItem] = Field(default_factory=list, max_length=60)


class KnowledgeReply(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=BATCH)
    reasons: list[str] = Field(default_factory=list, max_length=BATCH)


# --------------------------------------------------------------------------- worked examples


def _trim_entity(e: dict[str, Any]) -> dict[str, Any]:
    return {**{k: v for k, v in e.items() if k in ("key", "label", "grain", "primary_key")},
            "fields": [{k: v for k, v in f.items() if k in ("key", "label", "data_type", "semantic_type",
                                                             "sensitivity", "description")}
                       for f in (e.get("fields") or [])[:8]]}


def _pick_measures(measures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Template measures of different shapes: a filtered count, a sum, an average, a date rule, a ratio."""
    picked: list[dict[str, Any]] = []
    tests: tuple[Any, ...] = (lambda x: x.get("agg") == "count" and "where" in x, lambda x: x.get("agg") == "sum",
             lambda x: x.get("agg") == "avg" and "field" in x, lambda x: "asof_offset_days" in json.dumps(x),
             lambda x: x.get("op") == "div", lambda x: x.get("agg") == "count_distinct")
    for test in tests:
        hit = next((m for m in measures if test(m.get("expression") or {}) and m not in picked
                    and "calculator" not in json.dumps(m.get("expression"))), None)
        if hit is not None:
            picked.append({k: hit[k] for k in ("key", "label", "entity", "expression", "plain_rule", "definition",
                                              "format", "synonyms") if k in hit})
    return picked[:EXAMPLES]


def _pick_dimensions(dims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picked = [d for d in dims if d.get("is_status")][:1] + [d for d in dims if d.get("is_temporal")][:1]
    for answer in ("how_long", "how_late", "when", "why"):
        picked += [d for d in dims if answer in (d.get("answers") or []) and d not in picked][:1]
    return picked[:EXAMPLES]


def examples(kind: str, template: dict[str, Any] | None, docs: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The template pack's items of a kind, a few of each shape."""
    if not template:
        return []
    if kind == "entity":
        return [_trim_entity(e) for e in (template.get("entities") or [])[:2]]
    if kind == "link":
        return list((template.get("links") or [])[:3])
    if kind == "measure":
        return _pick_measures(template.get("measures") or [])
    if kind == "dimension":
        return _pick_dimensions(template.get("dimensions") or [])
    if kind == "synonyms":
        return [{"target": f"measure:{m['key']}", "synonyms": m["synonyms"]}
                for m in template.get("measures") or [] if m.get("synonyms")][:EXAMPLES]
    body = (docs or {}).get(kind) or {}
    lists = {"glossary": "terms", "value_synonyms": "values", "dashboard_rules": "rules",
             "chart_patterns": "patterns", "analysis_recipes": "recipes"}
    items = list(body.get(lists.get(kind, "")) or [])[:EXAMPLES - 2 if kind == "dashboard_rules" else EXAMPLES]
    if kind == "dashboard_rules":
        items += list((body.get("wording") or {}).get("forbidden") or [])[:2]
    return items


def _lines(items: list[Any]) -> str:
    return "\n".join(json.dumps(i, ensure_ascii=False, default=str) for i in items) or "(none)"


def _values_text(values: list[str] | None) -> str:
    return f"; values: {', '.join(v[:40] for v in values)}" if values else ""


# --------------------------------------------------------------------------- user messages


def entities_prompt(objects: list[dict[str, Any]], drafted: list[dict[str, Any]], template_title: str,
                    template: list[dict[str, Any]], samples: dict[str, dict[str, list[str]]]) -> str:
    """Stage 1: labels, grain, descriptions, semantic and sensitivity tags for the listed tables' columns."""
    lines = ["Stage 1: entities and fields. For each table give a business label, the grain (\"One row per …\") "
             "and, per column, a label, a one-line description, a semantic type and a sensitivity (personal for "
             "names, emails and phone numbers of people; confidential for salaries and contract values).",
             f"Worked examples ({template_title}):", _lines(template), "Tables:"]
    for o in objects:
        pk = ", ".join((o.get("keys") or {}).get("primary") or []) or "none"
        lines.append(f"- {o['qualified_name']} (key {pk})")
        for c in o["columns"]:
            vals = samples.get(o["qualified_name"], {}).get(c["name"])
            lines.append(f"  - {c['name']}, {c['type']}{_values_text(vals)}")
    lines += ["Tela's rules draft:", _lines([{"table": table_name(d["object_candidates"][0]), "label": d["label"],
                                              "fields": [{"column": f["columns"][0], "label": f["label"],
                                                          **({"sensitivity": f["sensitivity"]}
                                                             if f.get("sensitivity") else {})}
                                                         for f in d["fields"]]} for d in drafted])]
    return "\n".join(lines)[:PROMPT_CHARS]


def _entity_text(ent: dict[str, Any], values: dict[str, list[str]]) -> list[str]:
    out = [f"Entity {ent['key']} ({ent['label']}), key {', '.join(ent.get('primary_key') or [])}. Fields:"]
    for f in ent.get("fields") or []:
        out.append(f"- {f['key']} — {f['label']}, {f['data_type']}"
                   + (f", {f['semantic_type']}" if f.get("semantic_type") else "")
                   + _values_text(values.get(f["key"])))
    return out


def measures_prompt(ent: dict[str, Any], linked: list[dict[str, Any]], drafted: list[dict[str, Any]],
                    template_title: str, template: list[dict[str, Any]], have: list[str],
                    values: dict[str, list[str]]) -> str:
    """Stage 3, one entity: at most ``BATCH`` measures."""
    lines = [f"Stage 3: measures of one entity, at most {BATCH}. Cover counts by state, sums and averages of money "
             "and effort, date rules (overdue: a due date before the reporting date {\"asof_offset_days\": 0}; due "
             "in N days; aging: {\"days_from\": field, \"days_to\": {\"asof_offset_days\": 0}}) and ratios between "
             "two of them ({\"op\": \"div\", \"scale\": 100, \"left\": …, \"right\": …}). Each has a plain_rule a "
             "business person can check and a one-line definition. Aggregates: count, count_distinct, sum, avg, "
             "min, max. Conditions: {\"field\", \"op\" (=, !=, <, <=, >, >=), \"value\"}, {\"field\", \"op\" (in, "
             "not_in), \"values\"}, {\"field\", \"op\": \"is_null\" | \"not_null\"}, combined with "
             "{\"all\": […]}, {\"any\": […]}.",
             f"Worked examples ({template_title}):", _lines(template), *_entity_text(ent, values)]
    for other in linked:
        lines.append(f"Linked entity {other['key']} (fields as {other['key']}.<field>): "
                     + ", ".join(f"{f['key']} {f['data_type']}" for f in other.get("fields") or []))
    lines += [f"Measures the pack has (don't repeat): {', '.join(have) or 'none'}", "Tela's rules draft:",
              _lines(drafted)]
    return "\n".join(lines)[:PROMPT_CHARS]


def dimensions_prompt(ent: dict[str, Any], drafted: list[dict[str, Any]], template_title: str,
                      template: list[dict[str, Any]], have: list[str], values: dict[str, list[str]]) -> str:
    lines = ["Stage 4: dimensions (breakdowns) of one entity. Text columns with few values; dates as time "
             "dimensions (is_temporal); banded dates: age (answers how_long), lateness against a due date (how_late), "
             "time to a due date (when), each with \"of\": {\"days_from\", \"days_to\"} and bands {label, above, "
             "upto}; reason columns answer why. No personal fields and no identifiers.",
             f"Worked examples ({template_title}):", _lines(template), *_entity_text(ent, values),
             f"Dimensions the pack has (don't repeat): {', '.join(have) or 'none'}", "Tela's rules draft:",
             _lines(drafted)]
    return "\n".join(lines)[:PROMPT_CHARS]


def synonyms_prompt(head: dict[str, Any], drafted: list[dict[str, Any]], template_title: str,
                    template: list[dict[str, Any]]) -> str:
    lines = ["Stage 5: synonyms. For measures, dimensions and entities, give the other words business people use "
             "for them (at most 6 each, lower case, no repeats of the label).",
             f"Worked examples ({template_title}):", _lines(template), "Names:"]
    lines += [f"- measure:{m['key']} — {m['label']}" for m in head.get("measures") or []]
    lines += [f"- dimension:{d['key']} — {d['label']}" for d in head.get("dimensions") or []]
    lines += [f"- entity:{e['key']} — {e['label']}" for e in head.get("entities") or []]
    lines += ["Tela's rules draft:", _lines(drafted)]
    return "\n".join(lines)[:PROMPT_CHARS]


KNOWLEDGE_TASKS = {
    "glossary": "glossary terms: the abbreviations and business words of this domain, each with what it means "
                "(term, means, explain), and optionally the fields it is about (entity.field) or the measure a "
                "phrase is read as (reading)",
    "value_synonyms": "value synonyms: other words people use for a value of a text dimension (dimensions, value, "
                      "synonyms)",
    "dashboard_rules": "dashboard rules (id, title, rule, measures) that keep answers honest, and wording terms "
                       "(words, use) for words that must not be used",
    "chart_patterns": "chart patterns (id, title, when, suggest, reason, guidance): which chart reads a measure "
                      "and breakdown best",
    "analysis_recipes": "analysis recipes (id, title, triggers, question, slots, spec, operations, chart, insights) "
                        "for the questions people ask of this data",
}


def knowledge_prompt(kind: str, head: dict[str, Any], drafted: list[dict[str, Any]], template_title: str,
                     template: list[dict[str, Any]], dim_values: dict[str, list[str]]) -> str:
    lines = [f"Stage 6: knowledge. Draft {KNOWLEDGE_TASKS[kind]}, at most {BATCH}, for this pack's measures and "
             "dimensions. Name only the measures, dimensions and fields listed. Give one reason per item.",
             f"Worked examples ({template_title}):", _lines(template), "Measures:"]
    lines += [f"- {m['key']} — {m['label']}: {m.get('plain_rule', '')[:160]}" for m in head.get("measures") or []]
    lines.append("Dimensions:")
    lines += [f"- {d['key']} — {d['label']}{_values_text(dim_values.get(d['key']))}"
              for d in head.get("dimensions") or []]
    lines.append("Fields: " + ", ".join(f"{e['key']}.{f['key']}" for e in head.get("entities") or []
                                         for f in e.get("fields") or []))
    lines += ["Tela's rules draft:", _lines(drafted)]
    return "\n".join(lines)[:PROMPT_CHARS]
