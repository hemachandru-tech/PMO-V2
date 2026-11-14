"""Multi-step plans (ENH-012, US-179; FEAT-063): up to three specs, a later one using an earlier one's answer.

"Portfolios with above-average slip" needs two queries: the average slip, then slip by portfolio above it. A plan
carries its earlier steps in ``spec["steps"]`` and, on each step that uses one, the bindings in ``spec["uses"]``:

* ``{"step": 1, "at": "having", "op": ">", "take": "value"}`` — the step's single figure as a ``having`` value;
* ``{"step": 1, "at": "filter", "ref": "dimension:portfolio", "op": "in", "take": "values"}`` — the members of the
  step's first breakdown as a filter's values (a value list);
* ``take: "mean"`` / ``"median"`` — the step's figures across its rows reduced to one value ("above the average
  portfolio").

AG-03 writes a reference inline, ``{"from_step": 1}`` in a filter's ``values`` or a ``having`` ``value``; ``lift``
moves it into ``uses``, so every step without its bindings is a valid Query Spec (checked, described and repaired like
any other). The orchestrator runs the steps in order through ``QueryService`` under the person's scope (``take``
reads only what that step returned to them) and binds each answer before the next step runs. Step rows never go to a
model: only the final step is narrated, as any answer.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, TypeGuard

from tela_query_spec.model import ModelSnapshot

MAX_STEPS = 3  # the final step included
MAX_VALUES = 500  # a filter's values (Filter.values)
SINGLE_OPS = ("=", "!=", "<", "<=", ">", ">=")
LIST_OPS = ("in", "not_in")
TAKES = ("value", "values", "mean", "median")
PLAN_KEYS = ("steps", "uses")
OP_WORDS = {">": "above", ">=": "at least", "<": "below", "<=": "at most", "=": "equal to", "!=": "other than",
            "in": "in", "not_in": "not in"}


class StepError(ValueError):
    """A plan whose references can't be run as written: the message is for the person (and AG-03's repair)."""


@dataclass
class StepResult:
    """One earlier step, run: what it gave the later steps and its line in "How this was worked out"."""

    n: int
    spec: dict[str, Any]
    text: str
    took: Any
    row_count: int

    def public(self) -> dict[str, Any]:
        took = self.took if not isinstance(self.took, list) else self.took[:20]
        return {"step": self.n, "text": self.text, "spec": self.spec, "value": took,
                "values_count": len(self.took) if isinstance(self.took, list) else None,
                "row_count": self.row_count}


def is_ref(value: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and "from_step" in value


def _step_no(ref: dict[str, Any], own: int) -> int:
    try:
        n = int(ref["from_step"])
    except (TypeError, ValueError) as exc:
        raise StepError("A step reference names a step by its number.") from exc
    if not 1 <= n < own:
        raise StepError(f"Step {own} can only use an earlier step (1 to {own - 1}).")
    return n


def _take(ref: dict[str, Any], default: str) -> str:
    take = str(ref.get("take") or default)
    if take not in TAKES:
        raise StepError(f"A step answer is taken as one of {', '.join(TAKES)}.")
    return take


def lift_one(spec: dict[str, Any], own: int) -> dict[str, Any]:
    """One step's inline ``{"from_step": n}`` references moved into ``uses`` (``own``: this step's number)."""
    out = {k: v for k, v in spec.items() if k != "uses"}
    uses = list(spec.get("uses") or [])
    kept = []
    for f in spec.get("filters") or []:
        if not is_ref(f.get("values")):
            kept.append(f)
            continue
        op = f.get("op") or "in"
        take = _take(f["values"], "values" if op in LIST_OPS else "value")
        if (op in LIST_OPS and take != "values") or (op in SINGLE_OPS and take == "values") or \
                op not in (*LIST_OPS, *SINGLE_OPS):
            raise StepError(f"A value list goes with 'in' or 'not_in', a single value with a comparison (not {op}).")
        uses.append({"step": _step_no(f["values"], own), "at": "filter", "ref": f["ref"], "op": op, "take": take})
    out["filters"] = kept
    having = spec.get("having")
    if having and is_ref(having.get("value")):
        take = _take(having["value"], "value")
        if take == "values":
            raise StepError("A figure is compared with one value, not a list.")
        uses.append({"step": _step_no(having["value"], own), "at": "having", "op": having.get("op") or ">",
                     "take": take, **({"measure": having["measure"]} if having.get("measure") else {})})
        out.pop("having")
    for u in uses:
        if not 1 <= int(u.get("step", 0)) < own:
            raise StepError(f"Step {own} can only use an earlier step (1 to {own - 1}).")
    if uses:
        out["uses"] = uses
    return out


def lift(spec: dict[str, Any]) -> dict[str, Any]:
    """The plan with every step's references as bindings; a plan whose earlier steps nothing uses is a one-step plan.
    Raises ``StepError`` for references that can't run (a later or missing step, a list where one value goes)."""
    steps = list(spec.get("steps") or [])
    if not steps:
        return lift_one({k: v for k, v in spec.items() if k != "steps"}, 1) if spec.get("uses") or any(
            is_ref(f.get("values")) for f in spec.get("filters") or []) or is_ref(
            (spec.get("having") or {}).get("value")) else spec
    if len(steps) > MAX_STEPS - 1:
        raise StepError(f"A plan has at most {MAX_STEPS} steps.")
    lifted = [lift_one({k: v for k, v in s.items() if k not in ("steps",)}, i + 1) for i, s in enumerate(steps)]
    final = lift_one({k: v for k, v in spec.items() if k != "steps"}, len(steps) + 1)
    used = {u["step"] for s in [*lifted, final] for u in s.get("uses", [])}
    if not final.get("uses"):
        return {k: v for k, v in final.items() if k != "uses"}  # nothing uses the earlier steps: plan the last one
    if missing := [i + 1 for i in range(len(lifted)) if i + 1 not in used]:
        raise StepError(f"Step {missing[0]} is not used by a later step.")
    return {**final, "steps": lifted}


def skeleton(spec: dict[str, Any]) -> dict[str, Any]:
    """A step without its bindings or earlier steps: the part that is a Query Spec on its own."""
    return {k: v for k, v in spec.items() if k not in PLAN_KEYS}


def is_multi(spec: dict[str, Any] | None) -> bool:
    return bool(spec and spec.get("steps"))


def take(result: dict[str, Any], how: str) -> Any:
    """What one run step gives a later step, from the rows it returned to this person (never other rows)."""
    if result.get("hidden"):
        raise StepError("A step of this plan reads data hidden for your role.")
    cols, rows = result.get("columns") or [], result.get("rows") or []
    mi = next((i for i, c in enumerate(cols) if c["kind"] == "measure"
               and not str(c.get("ref") or "").startswith("window:")), None)
    if how == "values":
        di = next((i for i, c in enumerate(cols) if c["kind"] == "dimension"), None)
        if di is None:
            raise StepError("A step whose members are used needs a breakdown.")
        if result.get("truncated") or len(rows) > MAX_VALUES:
            raise StepError(f"A step found more than {MAX_VALUES} members, too many to use in the next step.")
        return list(dict.fromkeys(r[di] for r in rows if r[di] is not None))
    if mi is None:
        raise StepError("A step whose figure is used needs a measure.")
    figures = [r[mi] for r in rows if isinstance(r[mi], int | float) and not isinstance(r[mi], bool)]
    if how == "value":
        if len(rows) != 1:
            raise StepError("A step whose single figure is used must return one row; it returned "
                            f"{len(rows):,}.")
        if not figures:
            raise StepError("A step whose figure is used returned no figure.")
        return figures[0]
    if not figures:
        raise StepError("A step whose figures are averaged returned none.")
    value = statistics.fmean(figures) if how == "mean" else statistics.median(figures)
    return round(value, 6)


def wanted(spec: dict[str, Any]) -> dict[int, list[str]]:
    """What the later steps take of each earlier step: step number → takes."""
    out: dict[int, list[str]] = {}
    for s in [*(spec.get("steps") or []), spec]:
        for u in s.get("uses") or []:
            out.setdefault(int(u["step"]), [])
            if u["take"] not in out[int(u["step"])]:
                out[int(u["step"])].append(u["take"])
    return out


def bind(spec: dict[str, Any], got: dict[tuple[int, str], Any]) -> dict[str, Any]:
    """One step with the earlier steps' answers in place (``got``: (step, take) → answer): a filter on the value list
    or value, or a ``having``."""
    out = skeleton(spec)
    filters = list(out.get("filters") or [])
    for u in spec.get("uses") or []:
        value = got[(int(u["step"]), u["take"])]
        if u["at"] == "filter":
            if isinstance(value, list):
                if not value:
                    raise StepError(f"Step {u['step']} found nothing, so there is nothing to show.")
                filters.append({"ref": u["ref"], "op": u["op"], "values": value[:MAX_VALUES]})
            else:
                filters.append({"ref": u["ref"], "op": u["op"], "values": [value]})
        else:
            out["having"] = {"op": u["op"], "value": value, **({"measure": u["measure"]} if u.get("measure") else {})}
    out["filters"] = filters
    return out


def said(value: Any) -> str:
    if isinstance(value, list):
        shown = ", ".join(str(v) for v in value[:5])
        return f"{len(value):,} {'member' if len(value) == 1 else 'members'} ({shown}{', …' if len(value) > 5 else ''})"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:,}" if isinstance(value, int) else str(value)


def use_words(uses: list[dict[str, Any]], snap: ModelSnapshot) -> list[str]:
    """A step's bindings in plain words ("above step 1's average", "Portfolio in step 1's members")."""
    out = []
    for u in uses:
        what = {"value": "figure", "values": "members", "mean": "average across its rows",
                "median": "median across its rows"}[u["take"]]
        if u["at"] == "having":
            out.append(f"{OP_WORDS.get(u['op'], u['op'])} step {u['step']}'s {what}")
        else:
            d = snap.dimension(str(u["ref"]).split(":", 1)[-1])
            out.append(f"{d.label if d else u['ref']} {OP_WORDS.get(u['op'], u['op'])} step {u['step']}'s {what}")
    return out
