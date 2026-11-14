"""Personal fields in words (US-075 chat entry, US-076 edit in words; FEAT-028/029).

Rules only, no LLM: the dev model rarely returns valid JSON and a field is a definition, so what Tela understood
is always shown back before anything is saved.

* ``draft_from_words`` reads "group platforms into Core (Network One, Magnati) and Growth" or "band forecast cost
  into <1M, 1-5M, >5M" into a draft for the field builder. Group values are optional: the builder lets the Creator
  pick them from the values in their scope.
* ``edit_bands_in_words`` applies "make the top band 5M and above" (and similar) to a saved band and returns the
  proposal with a before/after diff; nothing changes until the Creator applies it (FEAT-029 A2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tela_api.ask.planner import _bare, _score, match_dimension, norm, words
from tela_api.content.fields import NUMERIC_TYPES, _fmt
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelSnapshot

NUM = r"(?<![\w.])-?[$€£]?\d[\d,]*(?:\.\d+)?\s*(?:k|m|bn|b|thousand|million|billion)?\b"
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
LEAD = r"^\s*(?:please\s+|can you\s+|could you\s+)?"
GROUP_RE = re.compile(LEAD + r"(?P<verb>group|bucket|split)\s+(?:the\s+)?(?P<subject>.+?)\s+(?:values\s+)?into\s+"
                      r"(?P<rest>.+?)\s*[.?!]?\s*$", re.I)
BAND_RE = re.compile(LEAD + r"(?:band|bands for)\s+(?:the\s+)?(?P<subject>.+?)\s+(?:into|at|by)\s+(?P<rest>.+?)"
                     r"\s*[.?!]?\s*$", re.I)
EXAMPLES = ('"group platforms into Core (Network One, Magnati) and Growth" or '
            '"band forecast cost into <1M, 1-5M, >5M"')


def is_field_request(text: str) -> bool:
    """Does this read as building a grouping or band (the Ask intent), rather than a question?"""
    return bool(BAND_RE.match(text) or GROUP_RE.match(text))


def number(text: str) -> float | None:
    m = re.search(NUM, text.lower())
    if not m:
        return None
    raw = m.group(0).strip()
    unit = UNIT.search(raw)
    value = float(re.sub(r"[^\d.]", "", raw[: unit.start()] if unit else raw)) * (-1 if raw.startswith("-") else 1)
    return value * SCALE[unit.group(1)] if unit else value


UNIT = re.compile(r"(k|m|bn|b|thousand|million|billion)$")


def numbers(text: str) -> list[float]:
    """Every number in the text; "1-5M" reads as 1M and 5M (the unit of a range's end applies to its start)."""
    raw = [m.group(0).strip() for m in re.finditer(NUM, text.lower())]
    vals = [v for v in (number(r) for r in raw) if v is not None]
    if len(raw) >= 2 and not UNIT.search(raw[0]) and (u := UNIT.search(raw[1])) and \
            re.search(rf"{re.escape(raw[0])}\s*(-|\u2013|to)\s*[$€£]?{re.escape(raw[1])}", text.lower()):
        vals[0] *= SCALE[u.group(1)]
    return vals


def _items(rest: str) -> list[str]:
    """Split a list on top-level commas, semicolons and "and" (not inside parentheses)."""
    out, depth, cur = [], 0, ""
    i = 0
    while i < len(rest):
        ch = rest[i]
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and (ch in ",;" or rest[i:i + 5].lower() == " and " or rest[i:i + 4].lower() == " or "):
            out.append(cur)
            cur = ""
            i += 1 if ch in ",;" else (5 if rest[i:i + 5].lower() == " and " else 4)
            continue
        cur += ch
        i += 1
    out.append(cur)
    return [s.strip(" .") for s in out if s.strip(" .")]


def _numeric_field(phrase: str, snap: ModelSnapshot, hidden: frozenset[str]) -> tuple[str, str] | None:
    """(field:<entity>.<field> ref, label) of the numeric field this phrase names best. When the best is hidden
    for the person, nothing is offered: a weaker visible match ("Forecast cost: internal" for "forecast cost")
    would be a wrong guess."""
    p, pw = norm(phrase), set(words(phrase))
    best: tuple[float, tuple[str, str] | None] = (0.0, None)
    for ent in snap.entities:
        for f in ent.fields:
            if f.data_type not in NUMERIC_TYPES:
                continue
            s = max(_score(p, pw, f.label, f.key, []), 2.0 if _bare(f.label) == p else 0.0)
            if s > best[0] or (s == best[0] and f"{ent.key}.{f.key}" in hidden):
                best = (s, None if f"{ent.key}.{f.key}" in hidden else (f"field:{ent.key}.{f.key}", f.label))
    return best[1] if best[0] >= 0.6 else None


@dataclass
class Draft:
    kind: str
    name: str
    source_ref: str
    source_label: str
    groups: list[dict[str, Any]] = field(default_factory=list)
    edges: list[float] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    open_ended_top: bool = True
    understood: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_json(self, connection_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": self.kind, "name": self.name, "connection_id": connection_id,
                                "source_ref": self.source_ref}
        if self.kind == "grouping":
            body["groups"] = self.groups
        else:
            body |= {"edges": self.edges, "labels": self.labels, "open_ended_top": self.open_ended_top}
        return {"draft": body, "source_label": self.source_label, "understood": self.understood,
                "missing": self.missing}


def _band_draft(subject: str, rest: str, snap: ModelSnapshot, hidden: frozenset[str]) -> Draft:
    found = _numeric_field(subject, snap, hidden)
    if found is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"I couldn't find a number field called “{subject}” that you "
                        "can use. Bands need a number, like a cost or a count.")
    ref, label = found
    edges: set[float] = set()
    kinds: list[str] = []
    for item in _items(rest):
        low = item.lower()
        nums = numbers(low)
        if not nums:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"“{item}” has no number. Try {EXAMPLES}.")
        if re.match(r"^\s*(<|≤|under|below|less than|up to)", low):
            kinds.append("below")
        elif re.search(r"^\s*(>|≥|over|above|more than)|\+\s*$|and (above|over|up)|or more", low):
            kinds.append("above")
        elif len(nums) >= 2:
            kinds.append("range")
        else:
            kinds.append("edge")
        edges.update(nums[:2])
    ordered = sorted(edges)
    # "<1M, 1-5M, >5M" and "at 1M and 5M" start from 0; "0-1M, 1-5M" starts where its first range does.
    first_below = kinds[0] != "range"
    if first_below and ordered[0] > 0:
        ordered = [0.0, *ordered]
    if len(ordered) < 2:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Give at least one edge, for example “at 1M and 5M”.")
    top_open = "above" in kinds or kinds[-1] != "range"  # a list that ends on a range is closed at its top
    n = len(ordered) if top_open else len(ordered) - 1
    labels = [_label(ordered, i, top_open, first_below=first_below and ordered[0] == 0) for i in range(n)]
    draft = Draft(kind="band", name=f"{_bare(label).capitalize()} bands", source_ref=ref, source_label=label,
                  edges=ordered, labels=labels, open_ended_top=top_open)
    draft.understood = [f"Bands of {label}: {', '.join(labels)}.",
                        f"Exactly {_fmt(ordered[1])} goes into {labels[1] if len(labels) > 1 else labels[0]}."]
    if ordered[0] == 0 and min(edges) > 0:
        draft.understood.append("The first band starts at 0; values below 0 go to Below 0.")
    return draft


def _label(edges: list[float], i: int, top_open: bool, *, first_below: bool) -> str:
    if i == 0 and first_below and len(edges) > 1:
        return f"< {_fmt(edges[1])}"
    if i + 1 >= len(edges):
        return f"{_fmt(edges[i])}+"
    return f"{_fmt(edges[i])} - {_fmt(edges[i + 1])}"


def _group_draft(subject: str, rest: str, snap: ModelSnapshot, hidden: frozenset[str]) -> Draft:
    dims = [d for d in snap.dimensions if not d.is_temporal and f"{d.entity}.{d.field}" not in hidden]
    dim = match_dimension(subject, dims) or match_dimension(re.sub(r"s\b", "", subject), dims)
    if dim is None:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"I couldn't find a field called “{subject}” to group. Try "
                        f"one of: {', '.join(d.label for d in dims[:6])}.")
    groups: list[dict[str, Any]] = []
    for item in _items(rest):
        m = re.match(r"^(?P<name>[^()]+?)\s*(?:\((?P<values>[^)]*)\))?$", item)
        name = (m.group("name") if m else item).strip().strip("\"“”'")
        values = [v.strip().strip("\"“”'") for v in re.split(r",|;|\bor\b|\band\b", m.group("values") or "")
                  if v.strip()] if m and m.group("values") else []
        if name:
            groups.append({"label": name[:80], "values": values})
    if not groups:
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Name the groups, for example {EXAMPLES}.")
    if len({g["label"].lower() for g in groups}) != len(groups):
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Group names must be different from each other.")
    draft = Draft(kind="grouping", name=f"{dim.label} groups", source_ref=f"dimension:{dim.key}",
                  source_label=dim.label, groups=groups)
    names = [g["label"] for g in groups]
    listed = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]
    draft.understood = [f"Group {dim.label} into {listed}. Any other value goes to Everything else; blank values go "
                        "to Not set."]
    empty = [g["label"] for g in groups if not g["values"]]
    if empty:
        draft.missing.append(f"Pick the values for {', '.join(empty)}.")
    return draft


def draft_from_words(text: str, snap: ModelSnapshot, hidden: frozenset[str] = frozenset()) -> Draft:
    """A field-builder draft from one sentence, or a validation error that says what to write."""
    if m := BAND_RE.match(text):
        return _band_draft(m.group("subject"), m.group("rest"), snap, hidden)
    if m := GROUP_RE.match(text):
        rest = m.group("rest")
        items = _items(rest)
        numeric = all(re.search(r"\d", i) for i in items) and "(" not in rest
        if numeric:
            return _band_draft(m.group("subject"), rest, snap, hidden)
        return _group_draft(m.group("subject"), rest, snap, hidden)
    raise TelaError(ErrorCode.VALIDATION_ERROR, f"I can draft a grouping or bands from words like {EXAMPLES}.")


# --------------------------------------------------------------------------- edit bands in words (FEAT-029 A2)

NUMG = rf"({NUM})"
EDITS: list[tuple[str, re.Pattern[str]]] = [
    ("rename", re.compile(r"^(?:rename|call)\s+(?:the\s+)?(?:band\s+)?[\"“']?(?P<old>.+?)[\"”']?\s+(?:band\s+)?"
                          r"(?:to|as)\s+[\"“']?(?P<new>.+?)[\"”']?$", re.I)),
    ("move", re.compile(rf"^(?:move|change|shift)\s+(?:the\s+)?(?:edge|break)?\s*(?:at\s+|from\s+)?{NUMG}\s+to\s+"
                        rf"{NUMG}$", re.I)),
    ("top", re.compile(rf"(?:top|last|highest)\s+band\s+(?:should\s+)?(?:be\s+|to\s+|start\s+at\s+|starts\s+at\s+|"
                       rf"from\s+)?{NUMG}\s*(?:and\s+(?:above|over|up)|\+|or\s+more)?$", re.I)),
    ("cap", re.compile(rf"(?:(?:cap|close|end|stop)\s+(?:the\s+)?(?:top\s+band\s+|bands\s+)?at|nothing\s+(?:above|"
                       rf"over)|top\s+band\s+up\s+to)\s+{NUMG}$", re.I)),
    ("start", re.compile(rf"(?:start|begin)\s+(?:the\s+)?(?:bands\s+|first\s+band\s+)?at\s+{NUMG}$", re.I)),
    ("add", re.compile(rf"(?:(?:add|insert)\s+(?:an?\s+)?(?:edge|break|band|split)|split\s+(?:the\s+)?"
                       rf"(?:band\s+)?(?:.*?\s+)?)\s*at\s+{NUMG}$", re.I)),
    ("remove", re.compile(rf"(?:(?:remove|drop|delete)\s+(?:the\s+)?(?:edge|break)\s+(?:at\s+)?|merge\s+(?:the\s+)?"
                          rf"(?:bands\s+)?at\s+){NUMG}$", re.I)),
]


def band_state(definition: dict[str, Any]) -> tuple[list[float], list[str], bool]:
    """(edges, labels, open top) of a stored band definition: the builder's request shape."""
    bands = definition["bands"]
    closing = definition.get("closing_max")
    edges = [float(b["lower"]) for b in bands] + ([float(closing)] if closing is not None else [])
    return edges, [b["label"] for b in bands], closing is None


def _bands(edges: list[float], labels: list[str], open_top: bool) -> list[tuple[float, float | None, str]]:
    n = len(edges) if open_top else len(edges) - 1
    return [(edges[i], edges[i + 1] if i + 1 < len(edges) else None, labels[i]) for i in range(n)]


@dataclass
class Edit:
    edges: list[float]
    labels: list[str]
    open_top: bool
    changes: list[str]


def _apply(op: str, m: re.Match[str], edges: list[float], labels: list[str], open_top: bool) -> Edit:
    old = {(lo, hi): lab for lo, hi, lab in _bands(edges, labels, open_top)}
    first_below = bool(labels) and labels[0].startswith("<")
    if op == "rename":
        target = m.group("old").strip().lower()
        hit = next((i for i, lab in enumerate(labels) if lab.lower() == target), None)
        if hit is None:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"There is no band called “{m.group('old')}”. Bands: "
                            f"{', '.join(labels)}.")
        new = [*labels]
        new[hit] = m.group("new").strip()[:80]
        return Edit(edges, new, open_top, [f"{labels[hit]} is renamed {new[hit]}."])
    a = number(m.group(1))
    b = number(m.group(2)) if op == "move" else None
    assert a is not None
    new_edges, new_open = [*edges], open_top
    if op == "move":
        if a not in edges:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"There is no edge at {_fmt(a)}. Edges: "
                            f"{', '.join(_fmt(e) for e in edges)}.")
        assert b is not None
        new_edges[edges.index(a)] = b  # not re-sorted: an edge moved past its neighbour is refused by build()
        change = f"The edge at {_fmt(a)} moves to {_fmt(b)}."
    elif op in ("top", "cap"):
        if a <= edges[0]:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"{_fmt(a)} is not above the first edge, {_fmt(edges[0])}.")
        new_edges, new_open = [e for e in edges if e < a] + [a], op == "top"
        change = (f"The top band starts at {_fmt(a)} and has no upper edge." if op == "top"
                  else f"The bands end at {_fmt(a)}; values above it go to Above {_fmt(a)}.")
        dropped = [e for e in edges if e > a]
        if dropped:
            change += f" Edges above it ({', '.join(_fmt(e) for e in dropped)}) are removed."
    elif op == "start":
        new_edges[0] = a
        change = f"The first band starts at {_fmt(a)}."
    elif op == "add":
        if a in edges:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"There is already an edge at {_fmt(a)}.")
        new_edges = sorted([*edges, a])
        change = f"A new edge at {_fmt(a)} splits the band around it."
    else:  # remove
        if a not in edges:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"There is no edge at {_fmt(a)}. Edges: "
                            f"{', '.join(_fmt(e) for e in edges)}.")
        if len(edges) <= 2:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "Bands need at least two edges.")
        new_edges = [e for e in edges if e != a]
        change = f"The edge at {_fmt(a)} is removed, so the bands on either side merge."
    n = len(new_edges) if new_open else len(new_edges) - 1
    new_labels = []
    for i in range(n):
        lo, hi = new_edges[i], new_edges[i + 1] if i + 1 < len(new_edges) else None
        new_labels.append(old.get((lo, hi)) or _label(new_edges, i, new_open, first_below=first_below and i == 0))
    return Edit(new_edges, new_labels, new_open, [change])


def edit_bands_in_words(text: str, definition: dict[str, Any]) -> Edit:
    """Apply one or more edits ("…; …" or "… then …") to a band definition; the result is not yet validated."""
    edges, labels, open_top = band_state(definition)
    changes: list[str] = []
    for part in re.split(r";|\bthen\b", text):
        part = part.strip(" .")
        if not part:
            continue
        for op, rx in EDITS:
            m = rx.match(part) if op in ("rename", "move") else rx.search(part)
            if m:
                e = _apply(op, m, edges, labels, open_top)
                edges, labels, open_top = e.edges, e.labels, e.open_top
                changes += e.changes
                break
        else:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"I didn't understand “{part}”. Try “make the top band 5M "
                            "and above”, “add an edge at 2M”, “remove the edge at 1M”, “move 1M to 2M” or “rename "
                            "1M - 5M to Medium”.")
    return Edit(edges, labels, open_top, changes)


def band_diff(before: dict[str, Any], edges: list[float], labels: list[str], open_top: bool) -> dict[str, Any]:
    """Before/after rows for the confirmation: each new band is same, renamed or new; old ones may be removed."""
    old = _bands(*band_state(before))
    new = _bands(edges, labels, open_top)
    by_range = {(lo, hi): lab for lo, hi, lab in old}

    def row(lo: float, hi: float | None, lab: str, status: str) -> dict[str, Any]:
        return {"label": lab, "lower": lo, "upper": hi, "status": status}

    after = [row(lo, hi, lab, "same" if by_range.get((lo, hi)) == lab else
                 "renamed" if (lo, hi) in by_range else "new") for lo, hi, lab in new]
    kept = {(lo, hi) for lo, hi, _ in new}
    return {"before": [row(lo, hi, lab, "same" if (lo, hi) in kept else "removed") for lo, hi, lab in old],
            "after": after}
