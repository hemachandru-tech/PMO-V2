"""LLM mapping suggestions with reasons (US-201; FEAT-067 / FEAT-059 / FEAT-049).

What the name rules (US-154) and the values (US-200) leave open — a pack field still unmapped, or mapped to a column
whose values don't fit, with no value suggestion of good confidence — is put to the tenant's model (AG-04, task
``map_suggest``), one call per pack table. The model sees metadata and value samples only, under the policy AG-03's
field context follows: the pack fields (label, description, type, the values its rules use) and the table's columns
(name, type, a few of the most frequent values of text columns; none for personal columns), never rows or counts.
Its reply is held to a schema (column or none, confidence, reason); a reply that stays invalid is ignored, and a
column the table doesn't have is dropped.

Nothing is applied: each suggestion is shown "Suggested by AI" with its reason, and flagged when it would fail
US-199's guards (a column another pack field reads, a type that doesn't fit, values that don't fit the pack's).
The Admin's confirmed choice goes through the preview and install like any other mapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tela_api.model.catalog import PERSONAL
from tela_api.packs.mapping import FREE_TEXT, GOOD_FIT, family, fits, pack_values, value_fit

if TYPE_CHECKING:
    from tela_api.packs.manifest import PackManifest, PackMatch

AGENT, TASK = "AG-04", "map_suggest"
SETTLED = 0.8  # a value suggestion this confident (US-200) settles the field: the model is not asked
MAX_FIELDS = 30  # pack fields asked about per table (required ones first)
MAX_COLUMNS = 150  # source columns listed per table
MAX_SAMPLED = 40  # text columns whose values are read per table
SAMPLES = 6  # values shown per column
SAMPLE_CHARS = 40

SYSTEM = (
    "You are Tela's data model assistant (AG-04). A starter pack describes fields a source table should hold; "
    "match each pack field listed to the source column that holds it. You see the pack fields (key, label, type, "
    "description, the values its rules use) and the table's columns (name, type, a few of their values). Answer "
    "only with columns listed, or null when none holds the field: never pick a column only because it is left "
    "over. One column stands for one pack field. confidence is 0 to 1. reason is one short sentence an Admin can "
    "check: what in the column's name, type or values shows it holds the field. Where the rules' guess is shown "
    "(US-245), it is a draft: keep its column unless the column's name, type or values show it is wrong."
)


class AiColumn(BaseModel):
    field: str = Field(max_length=100, description="The pack field's key, as listed")
    column: str | None = Field(default=None, max_length=200, description="A listed column, or null")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=400)


class AiMapReply(BaseModel):
    suggestions: list[AiColumn] = Field(default_factory=list, max_length=60)


def wanted(manifest: PackManifest, result: PackMatch, value_wanted: dict[str, str],
           value_suggested: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Per pack table (``entity`` → {field key → why}) the fields neither names nor values settle: ``unmapped`` (no
    column, and the Admin did not say "not in this source") or ``values_dont_fit`` (US-200's finding), unless a value
    suggestion of good confidence stands for it. Tables not found in the source are left out."""
    out: dict[str, dict[str, str]] = {}
    for ent in manifest.entities:
        em = result.entities[ent.key]
        if em.object is None:
            continue
        fields = sorted(ent.fields, key=lambda f: not f.required)
        asked: dict[str, str] = {}
        for f in fields:
            ref = f"{ent.key}.{f.key}"
            s = value_suggested.get(ref)
            if em.how.get(f.key) == "not_in_source" or (s and s["confidence"] >= SETTLED):
                continue
            if f.key not in em.mapping:
                asked[f.key] = "unmapped"
            elif value_wanted.get(ref) == "values_dont_fit":
                asked[f.key] = "values_dont_fit"
        if asked:
            out[ent.key] = dict(list(asked.items())[:MAX_FIELDS])
    return out


def offered(catalog: list[dict[str, Any]], result: PackMatch, entity: str,
            asked: dict[str, str]) -> list[dict[str, Any]]:
    """The table's columns no other pack field reads (a field asked about may keep its own), at most MAX_COLUMNS."""
    em = result.entities[entity]
    assert em.object is not None
    obj = next(c for c in catalog if c["qualified_name"].lower() == em.object.lower())
    taken = {c.lower() for k, c in em.mapping.items() if k not in asked}
    return [c for c in obj["columns"] if c["name"].lower() not in taken][:MAX_COLUMNS]


def sampled(columns: list[dict[str, Any]]) -> list[str]:
    """The text columns whose values may go to the model: not personal by flag or name (``model/catalog.PERSONAL``)."""
    return [c["name"] for c in columns if family(c.get("type")) in (None, "text") and not c.get("personal")
            and not PERSONAL.search(c["name"].lower())][:MAX_SAMPLED]


def prompt(manifest: PackManifest, entity: str, asked: dict[str, str], columns: list[dict[str, Any]],
           samples: dict[str, list[dict[str, Any]] | None], current: dict[str, str],
           drafts: dict[str, dict[str, Any]] | None = None) -> str:
    """The user message for one table: the pack fields asked about, then the columns with a few values each.

    US-245: ``drafts`` are the rules' own value suggestions (US-200, ref → column, confidence, reason) too weak to
    settle a field; each asked field shows its guess as the draft to confirm or correct (columns other fields
    read are still left out, as ``offered`` does)."""
    ent = manifest.entity(entity)
    assert ent is not None
    values = pack_values(manifest)
    lines = [f"Pack table: {ent.label} ({entity})", "Pack fields to match:"]
    for f in ent.fields:
        if f.key not in asked:
            continue
        line = f"- {f.key} — {f.label}, {f.data_type}"
        if f.description:
            line += f"; {f.description[:200]}"
        if vals := values.get(f"{entity}.{f.key}"):
            line += f"; values: {', '.join(vals[:12])}"
        if asked[f.key] == "values_dont_fit":
            line += f"; now mapped to {current[f.key]}, whose values don't fit"
        if (guess := (drafts or {}).get(f"{entity}.{f.key}")) and guess.get("column"):
            why = str(guess.get("reason") or "")[:120]
            line += f"; rules' guess: {guess['column']} ({guess['confidence']:.2f}, {why})"
        lines.append(line)
    lines.append("Source columns (name, type, example values):")
    lowered = {k.lower(): v for k, v in samples.items()}
    for c in columns:
        line = f"- {c['name']}, {c.get('type') or 'unknown'}"
        profile = lowered.get(c["name"].lower())
        shown = [str(v["value"])[:SAMPLE_CHARS] for v in (profile or []) if v["value"] not in (None, "")][:SAMPLES]
        if shown:
            line += f"; e.g. {', '.join(shown)}" + (" (free text)" if len(profile or []) >= FREE_TEXT else "")
        lines.append(line)
    return "\n".join(lines)[:16000]


def accept(manifest: PackManifest, catalog: list[dict[str, Any]], result: PackMatch, entity: str,
           asked: dict[str, str], reply: AiMapReply,
           samples: dict[str, list[dict[str, Any]] | None]) -> tuple[dict[str, dict[str, Any]], int]:
    """The model's answers for one table, checked: a field not asked about or a column the table lacks is dropped
    (counted in the second value). Each kept suggestion carries ``flags``, US-199's guards on it."""
    ent = manifest.entity(entity)
    em = result.entities[entity]
    assert ent is not None and em.object is not None
    obj = next(c for c in catalog if c["qualified_name"].lower() == em.object.lower())
    cols = {c["name"].lower(): c for c in obj["columns"]}
    labels = {f.key: f.label for f in ent.fields}
    pfs = {f.key: f for f in ent.fields}
    values = pack_values(manifest)
    lowered = {k.lower(): v for k, v in samples.items()}
    out: dict[str, dict[str, Any]] = {}
    dropped = 0
    for s in reply.suggestions:
        if s.field not in asked or f"{entity}.{s.field}" in out:
            dropped += 1
            continue
        col = cols.get(s.column.strip().lower()) if s.column and s.column.strip() else None
        if s.column and s.column.strip() and col is None:
            dropped += 1
            continue
        if col is not None and col["name"].lower() == (em.mapping.get(s.field) or "").lower():
            dropped += 1  # the column it already reads, whose values don't fit
            continue
        item: dict[str, Any] = {"column": col["name"] if col else None, "confidence": round(s.confidence, 2),
                                "reason": s.reason.strip(), "why": asked[s.field], "source": "ai", "flags": []}
        if col is not None:
            pf = pfs[s.field]
            other = next((k for k, c in em.mapping.items() if k != s.field and c.lower() == col["name"].lower()),
                         None)
            if other is not None:
                item["flags"].append(f"{col['name']} is already {labels[other]}'s column. One column can stand for "
                                     "one pack field only.")
            if not fits(pf.data_type, pf.semantic_type, col.get("type")):
                item["flags"].append(f"{col['name']} is {col.get('type')}; {pf.label} needs {pf.data_type}.")
            expected = values.get(f"{entity}.{s.field}") or []
            fit = value_fit(expected, lowered.get(col["name"].lower())) if expected else None
            if fit is not None and fit["score"] < GOOD_FIT:
                missing = [v for v in expected if v not in fit["matched"]]
                item["flags"].append(f"Its values don't fit the pack's {pf.label}: {', '.join(missing[:4])} not "
                                     f"found; it holds {', '.join(fit['others'][:4]) or 'none of them'}.")
        out[f"{entity}.{s.field}"] = item
    by_column: dict[str, list[str]] = {}
    for ref, item in out.items():
        if item["column"]:
            by_column.setdefault(item["column"].lower(), []).append(ref)
    for refs in by_column.values():
        for ref in refs if len(refs) > 1 else []:
            others = [labels[r.partition(".")[2]] for r in refs if r != ref]
            out[ref]["flags"].append(f"Also suggested for {', '.join(others)}. One column can stand for one pack "
                                     "field only.")
    return out, dropped
