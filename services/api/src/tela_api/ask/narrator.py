"""Grounded narrative (AG-08; RULE-022; US-050; v2 US-186).

Facts are computed in code from the result (row count, total, top and bottom item, single value, latest
period). The headline is a template sentence built only from those facts. When the tenant allows
aggregates to be sent to the model (RULE-019, default off), the narrator may rephrase the facts, but any
number in its text that is not one of the facts makes Tela fall back to the template.

v2 (US-186): the model gets the headline facts as ``f0`` and the insight engine's facts (US-185) and writes one
headline and up to three insight sentences, each citing the fact ids it uses. A sentence is kept only if its ids are
ones supplied, every number in it is a number of a fact it cites, and it passes the wording check; otherwise the
cited facts' template sentences stand in (``compose``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

EMPTY = "No records match the current scope and filters."


def fmt(value: Any, kind: str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if kind == "currency":
        sign, value = ("-", -value) if value < 0 else ("", value)
        return f"{sign}${value:,.0f}" if value >= 1 or value == 0 else f"{sign}${value:,.2f}"
    if kind == "percent":
        return f"{value:,.2f}%"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def facts(result: dict[str, Any], additive: bool = True) -> dict[str, Any]:
    cols, rows = result["columns"], result["rows"]
    measures = [i for i, c in enumerate(cols) if c["kind"] == "measure"]
    dims = [i for i, c in enumerate(cols) if c["kind"] != "measure"]
    out: dict[str, Any] = {"rows": len(rows)}
    if not rows or not measures:
        return out
    mi = measures[0]
    mcol = cols[mi]
    out.update(measure=mcol["label"], format=mcol.get("format"))
    values = [r[mi] for r in rows if isinstance(r[mi], int | float)]
    if not dims:
        out["value"] = rows[0][mi]
        return out
    di = dims[0]
    out["dimension"] = cols[di]["label"]
    out["period"] = cols[di]["kind"] == "period"
    if values:
        ranked = sorted((r for r in rows if isinstance(r[mi], int | float)), key=lambda r: r[mi])
        out.update(top=(ranked[-1][di], ranked[-1][mi]), bottom=(ranked[0][di], ranked[0][mi]))
        leaders = [r[di] for r in ranked if r[mi] == ranked[-1][mi]]
        if len(leaders) > 1:
            out["tied"] = [x if x is not None else "Not set" for x in leaders]
        if additive:
            out["total"] = sum(values)
        if out["period"]:
            # the latest period that has a figure (a trailing month with no value would headline "—")
            latest = max((r for r in rows if r[di] is not None and isinstance(r[mi], int | float)),
                         key=lambda r: str(r[di]), default=None)
            if latest is not None:
                out["latest"] = (latest[di], latest[mi])
    return out


def template(f: dict[str, Any]) -> dict[str, Any]:
    if f["rows"] == 0:
        return {"headline": EMPTY, "sentences": []}
    kind = f.get("format")
    if "value" in f:
        return {"headline": f"{f['measure']}: {fmt(f['value'], kind)}.", "sentences": []}
    if f.get("period") and "latest" in f:
        period, v = f["latest"]
        return {"headline": f"{f['measure']} in {period}: {fmt(v, kind)}.",
                "sentences": [{"text": f"{f['rows']} periods shown. Highest: "
                                       f"{f['top'][0] if f['top'][0] is not None else 'Not set'} "
                                       f"({fmt(f['top'][1], kind)})."}]}
    if "top" not in f:
        return {"headline": f"{f['rows']} rows.", "sentences": []}
    top, bottom = f["top"], f["bottom"]
    label = top[0] if top[0] is not None else "Not set"
    if f.get("tied") and top[1] != 0:
        names = f["tied"]
        joined = ", ".join(map(str, names[:-1])) + f" and {names[-1]}" if len(names) <= 4 else f"{len(names)} values"
        total = f" of {fmt(f['total'], kind)}" if "total" in f and kind != "percent" else ""
        headline = f"{joined} are tied for the most {f['measure'].lower()} ({fmt(top[1], kind)} each{total})."
    elif "total" in f and kind != "percent":
        headline = f"{label} has the most {f['measure'].lower()} ({fmt(top[1], kind)} of {fmt(f['total'], kind)})."
    else:
        headline = f"{label} has the highest {f['measure'].lower()} ({fmt(top[1], kind)})."
    if top[1] == 0:
        headline = f"No {f['measure'].lower()} in any {f['dimension'].lower()}."
    sentences = [{"text": f"{f['rows']} {f['dimension'].lower()} values compared. Lowest: "
                          f"{bottom[0] if bottom[0] is not None else 'Not set'} ({fmt(bottom[1], kind)})."}]
    return {"headline": headline, "sentences": sentences}


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)}


def fact_numbers(f: dict[str, Any]) -> set[str]:
    nums: set[str] = {str(f["rows"])}
    for key in ("value", "total"):
        if isinstance(f.get(key), int | float):
            nums |= numbers_in(fmt(f[key], f.get("format"))) | {str(f[key])}
    for name in f.get("tied", []):
        nums |= numbers_in(str(name))
    for key in ("top", "bottom", "latest"):
        if key in f:
            nums |= numbers_in(str(f[key][0])) | numbers_in(fmt(f[key][1], f.get("format"))) | {str(f[key][1])}
    return nums


def grounded(text: str, f: dict[str, Any]) -> bool:
    """Every number in the text must be one of the computed facts (RULE-022)."""
    allowed = fact_numbers(f)
    return all(n in allowed or n.lstrip("-") in allowed for n in numbers_in(text))


# --------------------------------------------------------------------------- v2 (US-186)

SUMMARY = "f0"  # the headline facts' id
CITE = re.compile(r"\s*\[(?:fact|facts)\s*:\s*([^\]]*)\]", re.I)
MAX_INSIGHTS = 3


class Cited(BaseModel):
    text: str = Field(min_length=3, max_length=300)
    facts: list[str] = Field(default_factory=list, max_length=6)


class NarratorV2Out(BaseModel):
    headline: Cited
    insights: list[Cited] = Field(default_factory=list, max_length=MAX_INSIGHTS)


AG08_V2_SYSTEM = (
    "You are Tela's narrator (AG-08). You get facts computed from a query result, each with an id. Write one plain "
    "headline sentence and at most three insight sentences, using ONLY the facts given. Copy every number exactly as "
    "it appears in a fact's text, with its unit (%, $); never round, add, compute or compare numbers yourself. Do not "
    "give causes, advice or forecasts unless a fact states them. Each sentence lists the ids of the facts it uses in "
    "\"facts\" and may end with them as [fact:f1]. The headline uses f0. Do not restate the headline in an insight.")


def ids_of(raw: Iterable[str]) -> list[str]:
    """Fact ids as the model may write them ("3", "f3", "fact:3") in the engine's form ("f3")."""
    out: list[str] = []
    for r in raw:
        for part in re.split(r"[,\s]+", str(r)):
            part = part.strip().lower().removeprefix("fact:").strip()
            if part:
                fid = part if part.startswith("f") else f"f{part}"
                if fid not in out:
                    out.append(fid)
    return out


def strip_cites(text: str) -> tuple[str, list[str]]:
    """The sentence without its ``[fact:…]`` markers, and the ids in them."""
    ids = ids_of(m.group(1) for m in CITE.finditer(text))
    return CITE.sub("", text).strip(), ids


def summary_fact(f: dict[str, Any]) -> dict[str, Any]:
    """The headline facts as one fact (``f0``), for the model and the grounding check."""
    keep = {k: v for k, v in f.items() if k not in ("format",)}
    return {"id": SUMMARY, "type": "summary", "text": template(f)["headline"], "numbers": keep, "members": []}


def check(sentence: Cited, by_id: dict[str, set[str]], *,
          allowed_words: Iterable[str] = ()) -> tuple[str, list[str]] | None:
    """A model sentence that may be shown: (text, cited ids); None when it cites an unknown fact, cites nothing, has a
    number that is not a number of a fact it cites, or fails the wording check."""
    from tela_api.content import wording

    text, marked = strip_cites(sentence.text)
    ids = ids_of([*sentence.facts, *marked])
    if not ids or not text or any(i not in by_id for i in ids):
        return None
    allowed = set().union(*(by_id[i] for i in ids))
    if not all(n in allowed or n.lstrip("-") in allowed for n in numbers_in(text)):
        return None
    if not wording.ok(text, allowed=allowed_words):
        return None
    return text, ids


def compose(f: dict[str, Any], found: list[dict[str, Any]], out: NarratorV2Out | None = None, *,
            allowed_words: Iterable[str] = ()) -> dict[str, Any]:
    """The card's story: headline (cites ``f0``), insight sentences (each citing its facts) and data notes.

    ``found`` are the insight engine's facts; data notes are listed apart and never narrated. Without model output,
    or for a sentence that fails ``check``, each fact's own template sentence is used."""
    from tela_api.ask import insights as engine

    kind = f.get("format")
    notes = [x for x in found if x["type"] == "data_note"]
    told = [x for x in found if x["type"] != "data_note"][:MAX_INSIGHTS]
    by_id: dict[str, set[str]] = {SUMMARY: fact_numbers(f)} | {x["id"]: engine.numbers_of(x, kind) for x in told}
    headline = {"text": template(f)["headline"], "facts": [SUMMARY], "source": "template"}
    cited: list[dict[str, Any]] = []
    if out is not None and f["rows"]:
        if (ok := check(out.headline, by_id, allowed_words=allowed_words)) is not None:
            headline = {"text": ok[0], "facts": ok[1], "source": "llm"}
        for s in out.insights:
            ok = check(s, {k: v for k, v in by_id.items() if k != SUMMARY}, allowed_words=allowed_words)
            covered = {i for c in cited for i in c["facts"]}
            if ok is not None and not set(ok[1]) <= covered and len(cited) < MAX_INSIGHTS:
                cited.append({"text": ok[0], "facts": ok[1], "source": "llm"})
    covered = {i for c in cited for i in c["facts"]}
    cited += [{"text": x["text"], "facts": [x["id"]], "source": "template"} for x in told if x["id"] not in covered]
    order = {x["id"]: n for n, x in enumerate(told)}
    cited = sorted(cited, key=lambda c: min(order.get(i, 99) for i in c["facts"]))[:MAX_INSIGHTS]
    sources = {headline["source"], *(c["source"] for c in cited)}
    return {"headline": headline, "insights": cited, "notes": [{"text": x["text"], "fact": x["id"]} for x in notes],
            "source": sources.pop() if len(sources) == 1 else "mixed"}


def v2_prompt(f: dict[str, Any], found: list[dict[str, Any]]) -> str:
    """The model's input: the headline facts and the insight facts, each with its sentence and numbers."""
    import json

    told = [x for x in found if x["type"] != "data_note"][:MAX_INSIGHTS]
    facts_ = [summary_fact(f), *({k: x[k] for k in ("id", "type", "text", "numbers", "members")} for x in told)]
    return json.dumps({"measure": f.get("measure"), "format": f.get("format"), "facts": facts_}, default=str)
