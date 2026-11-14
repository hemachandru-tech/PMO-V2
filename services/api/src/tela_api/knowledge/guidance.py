"""Dashboard rules and report blueprints as knowledge (US-159, FEAT-059 / FEAT-016).

A pack can ship ``dashboard_rules.yaml`` (house rules for building dashboards: forecast is not spend, the
reporting date, reported status vs calculated health, the populations …, plus the wording check's terms) and
``report_blueprints.yaml`` (each report catalogue screen as a layout of certified measures: question, population,
headline figures, visuals, detail table, footnote). Like chart patterns (``knowledge/patterns.py``), each approved
model version of a connection built from that pack gets one ``dashboard_rule`` and one ``report_blueprint``
document, approved with the model and retired with older versions, so Ask, the planner and the advisor retrieve
the guidance of the version they work on. The report planner (AG-05) also reads the blueprints directly and
builds the catalogue layout from the parts the model has (``content/planner.py``).

``analysis_recipes.yaml`` (US-179) names parameterised analyses (variance bridge, Pareto 80/20, aging buckets …): a
Query Spec template with slots, the operations it uses, its chart and the insight checks that matter. Each approved
model version gets one ``analysis_recipe`` document the same way (one chunk per recipe, cut to the model, the whole
recipe in the chunk's metadata); Ask plans with the approved ones only (``ask/recipes.py``).
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_one
from tela_api.knowledge.patterns import index_pack_document, pack_source_for
from tela_api.packs.manifest import PackManifest
from tela_api.packs.source import PackSource, kind_of, resolve
from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import FigureTone, Visual

_S = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- dashboard rules


class DashboardRule(BaseModel):
    model_config = _S

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")
    title: str = Field(max_length=120)
    rule: str = Field(max_length=1200)
    measures: list[str] = Field(default_factory=list)  # shown only to people who can see all of them


class Forbidden(BaseModel):
    model_config = _S

    words: list[str] = Field(min_length=1)
    use: str = Field(max_length=60)


class StatusWords(BaseModel):
    model_config = _S

    mixed: list[str] = Field(default_factory=list)
    reported: str = "Reported status"
    calculated: str = "Calculated health"
    unknown_as_good: list[str] = Field(default_factory=list)  # regular expressions


class Wording(BaseModel):
    model_config = _S

    forbidden: list[Forbidden] = Field(default_factory=list)
    status: StatusWords = Field(default_factory=StatusWords)


class RulesFile(BaseModel):
    model_config = _S

    version: str
    status: str = "draft"
    rules: list[DashboardRule] = Field(default_factory=list)
    wording: Wording = Field(default_factory=Wording)


# --------------------------------------------------------------------------- glossary (US-164)


class GlossaryTerm(BaseModel):
    model_config = _S

    term: str = Field(pattern=r"^[A-Za-z0-9][\w&/.-]*( [\w&/.-]+){0,3}$", max_length=40)
    means: str = Field(min_length=1, max_length=80)
    explain: str = Field(default="", max_length=600)
    fields: list[str] = Field(default_factory=list)  # entity.field; the term is kept only when the model has them
    # Ambiguity library (US-182): a phrase with more than one meaning, read as the ``reading`` measure (key) and not
    # as those ``instead_of``; kept only when the model has the reading measure. The rules planner reads it.
    reading: str | None = Field(default=None, max_length=80)
    instead_of: list[str] = Field(default_factory=list, max_length=4)


class GlossaryFile(BaseModel):
    model_config = _S

    version: str
    status: str = "draft"
    terms: list[GlossaryTerm] = Field(default_factory=list)


# --------------------------------------------------------------------------- report blueprints


class BlueprintWidget(BaseModel):
    """A blueprint visual, in the starter-report widget form (``manifest.yaml`` ``reports``)."""

    model_config = _S

    title: str = Field(max_length=120)
    measure: str
    extra_measures: list[str] = Field(default_factory=list, max_length=3)  # a query's limit
    breakdowns: list[str] = Field(default_factory=list, max_length=2)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    time: dict[str, Any] | None = None
    visual: str
    note: str | None = Field(default=None, max_length=200)  # a meter's note

    @model_validator(mode="after")
    def _visual(self) -> BlueprintWidget:
        Visual.model_validate({"type": self.visual, **({"note": self.note} if self.note else {})})
        return self


class BlueprintTable(BaseModel):
    model_config = _S

    title: str = Field(max_length=120)
    measure: str
    columns: list[str] = Field(min_length=1, max_length=12)
    sort: dict[str, Any] | None = None
    export_name: str | None = Field(default=None, max_length=80)

    def widget(self) -> BlueprintWidget:
        return BlueprintWidget(title=self.title, measure=self.measure, visual="table")

    def detail(self) -> dict[str, Any]:
        return {"columns": self.columns, **({"sort": self.sort} if self.sort else {}),
                **({"export_name": self.export_name} if self.export_name else {})}


class BlueprintKpi(BaseModel):
    model_config = _S

    title: str = Field(max_length=120)
    measure: str
    filters: list[dict[str, Any]] = Field(default_factory=list)


class BlueprintCallout(BaseModel):
    model_config = _S

    title: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=1000)
    level: Literal["info", "warning"] = "info"


class Blueprint(BaseModel):
    model_config = _S

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")
    tab: str | None = Field(default=None, max_length=40)  # its tab on the starter dashboard (US-155)
    title: str = Field(max_length=120)
    dashboard: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")  # a key of the manifest's ``dashboards`` (US-234)
    section: str | None = None
    question: str | None = Field(default=None, max_length=300)
    population: str | None = Field(default=None, max_length=300)
    triggers: list[str] = Field(min_length=1)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    kpis: list[str | BlueprintKpi] = Field(default_factory=list, max_length=5)
    widgets: list[BlueprintWidget] = Field(default_factory=list, max_length=6)
    table: BlueprintTable | None = None
    callout: BlueprintCallout | None = None
    footnote: str | None = Field(default=None, max_length=600)
    unavailable: list[str] = Field(default_factory=list)  # data gaps: never built

    @model_validator(mode="after")
    def _complete(self) -> Blueprint:
        if self.unavailable and (self.kpis or self.widgets or self.table):
            raise ValueError(f"blueprint {self.id}: an unavailable blueprint has no figures, visuals or table")
        if not self.unavailable and not (self.kpis and self.question and self.population and self.footnote):
            raise ValueError(f"blueprint {self.id}: needs a question, a population, figures and a footnote")
        return self

    def figures(self) -> list[BlueprintKpi]:
        return [BlueprintKpi(title="", measure=k) if isinstance(k, str) else k for k in self.kpis]

    def refs(self) -> tuple[set[str], set[str]]:
        """(measure keys, dimension keys) the blueprint names."""
        measures = {k.measure for k in self.figures()}
        dims = {f["ref"] for f in self.filters} | {f["ref"] for k in self.figures() for f in k.filters}
        for w in self.widgets:
            measures |= {w.measure, *w.extra_measures}
            dims |= set(w.breakdowns) | {f["ref"] for f in w.filters} | ({w.time["ref"]} if w.time else set())
        if self.table:
            measures.add(self.table.measure)
        return measures, dims


def _word(w: str) -> str:
    stem = w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
    return re.escape(stem) + r"(?:s|es|ed|d|ing)?"


@lru_cache(maxsize=512)
def trigger_re(phrase: str) -> re.Pattern[str]:
    """A trigger phrase as a pattern over normalised text: whole words, plurals and simple endings allowed."""
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    return re.compile(r"\b" + r"\s+".join(_word(w) for w in words) + r"\b")


def normalise(text_: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text_.lower().replace("-", " ")))


def trigger_spans(bp: Blueprint, text_: str) -> list[tuple[int, int, int]]:
    """(start, end, words) of every trigger found in normalised text."""
    out = []
    for t in bp.triggers:
        for m in trigger_re(t).finditer(text_):
            out.append((m.start(), m.end(), len(t.split())))
    return out


REQUEST_WORDS = {"request", "requests", "demand", "intake", "pipeline"}
PROJECT_WORDS = {"project", "projects", "portfolio", "delivery"}


def match_blueprints(blueprints: list[Blueprint], request: str, *, limit: int = 3) -> list[Blueprint]:
    """The blueprints a sentence names, in sentence order: longest trigger first, never two on the same words.

    When projects and requests blueprints both match (on hold aging …), the sentence's own words decide: request
    words pick the requests one, otherwise the projects one.
    """
    q = normalise(request)
    words = set(q.split())
    found = [(s, e, n, bp) for bp in blueprints for s, e, n in trigger_spans(bp, q)]
    found.sort(key=lambda f: (-f[2], -(f[1] - f[0]), f[0]))
    taken: list[tuple[int, int]] = []
    picked: list[tuple[int, Blueprint]] = []
    for s, e, _, bp in found:
        if any(bp is p for _, p in picked) or any(s < te and ts < e for ts, te in taken):
            continue
        rival = next((p for s2, e2, _, p in found if p.dashboard != bp.dashboard and (s2, e2) == (s, e)), None)
        if rival is not None:
            wants_requests = bool(words & REQUEST_WORDS) and not words & {"project", "projects"}
            if (bp.dashboard == "requests") != wants_requests:
                continue
        taken.append((s, e))
        picked.append((s, bp))
    return [bp for _, bp in sorted(picked, key=lambda p: p[0])][:limit]


class BlueprintsFile(BaseModel):
    model_config = _S

    version: str
    status: str = "draft"
    tones: dict[str, dict[str, Any]] = Field(default_factory=dict)  # headline figure thresholds by measure (US-155)
    blueprints: list[Blueprint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _tones(self) -> BlueprintsFile:
        for t in self.tones.values():
            FigureTone.model_validate(t)
        return self


# --------------------------------------------------------------------------- analysis recipes (US-179)


GRAINS = ("day", "week", "month", "quarter", "year")
RECIPE_OPERATIONS = ("share_of_total", "sort", "limit", "moving_avg", "running_total", "rank", "bucket", "percentile",
                     "having", "top_n_per_group", "compare")
INSIGHT_TYPES = ("change", "driver", "trend", "break", "concentration", "outlier", "distribution", "gap", "data_note")
_SLOT = re.compile(r"\{([a-z][a-z0-9_]{0,30})\}")


class RecipeSlot(BaseModel):
    model_config = _S

    kind: Literal["measure", "dimension", "grain"]
    options: list[str] = Field(min_length=1, max_length=20)  # pack keys, or grains
    default: str

    @model_validator(mode="after")
    def _default(self) -> RecipeSlot:
        if self.default not in self.options:
            raise ValueError(f"slot default {self.default} is not one of its options")
        if self.kind == "grain" and not set(self.options) <= set(GRAINS):
            raise ValueError(f"grains are {', '.join(GRAINS)}")
        return self


def _placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_SLOT.findall(value))
    if isinstance(value, dict):
        return set().union(*(_placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_placeholders(v) for v in value)) if value else set()
    return set()


class Recipe(BaseModel):
    model_config = _S

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")
    title: str = Field(max_length=120)
    triggers: list[str] = Field(min_length=1)
    question: str = Field(max_length=300)
    slots: dict[str, RecipeSlot] = Field(min_length=1, max_length=6)
    spec: dict[str, Any]
    operations: list[Literal[RECIPE_OPERATIONS]] = Field(default_factory=list)  # type: ignore[valid-type]
    chart: str
    insights: list[Literal[INSIGHT_TYPES]] = Field(default_factory=list)  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _template(self) -> Recipe:
        Visual.model_validate({"type": self.chart})
        if self.spec.get("measure") != "{measure}" or self.slots.get("measure") is None or \
                self.slots["measure"].kind != "measure":
            raise ValueError(f"recipe {self.id}: the spec's measure is the measure slot")
        if unknown := _placeholders(self.spec) - set(self.slots):
            raise ValueError(f"recipe {self.id}: the spec names unknown slots {sorted(unknown)}")
        if unused := set(self.slots) - _placeholders(self.spec):
            raise ValueError(f"recipe {self.id}: slots {sorted(unused)} are not used")
        if missing := [o for o in self.operations if o not in self.spec]:
            raise ValueError(f"recipe {self.id}: operations {missing} are not in its spec")
        return self

    def keys(self, kind: str) -> set[str]:
        return {o for s in self.slots.values() if s.kind == kind for o in s.options}


class RecipesFile(BaseModel):
    model_config = _S

    version: str
    status: str = "draft"
    recipes: list[Recipe] = Field(default_factory=list)


# --------------------------------------------------------------------------- loading


def _load(pack: str | PackSource | None, name: str, packs_dir: str | Path | None) -> dict[str, Any] | None:
    """One knowledge document of a pack (a key reads its folder; a source reads where it is kept, US-230)."""
    src = resolve(pack, packs_dir)
    return src.document(kind_of(name)) if src else None


def load_rules(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> RulesFile | None:
    data = _load(pack, "dashboard_rules.yaml", packs_dir)
    return RulesFile.model_validate(data) if data is not None else None


def load_tones(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Headline figure thresholds by measure key (``tones`` in report_blueprints.yaml; US-155, US-185)."""
    data = _load(pack, "report_blueprints.yaml", packs_dir)
    return BlueprintsFile.model_validate(data).tones if data is not None else {}


def load_glossary(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> list[GlossaryTerm]:
    data = _load(pack, "glossary.yaml", packs_dir)
    return GlossaryFile.model_validate(data).terms if data is not None else []


def glossary_chunks(terms: list[GlossaryTerm], snap: ModelSnapshot) -> list[dict[str, Any]]:
    """One chunk per term the model can use, written so ``ask.freehand.glossary_terms`` reads it back
    ("S2 means: planning stage. …")."""
    out = []
    for t in terms:
        if not all((e := snap.entity(ref.split(".", 1)[0])) is not None and e.field(ref.split(".", 1)[1]) is not None
                   for ref in t.fields):
            continue
        if t.reading is not None:  # a trap: written so it is not read back as a word to replace ("cost" → …)
            m = snap.measure(t.reading)
            if m is None:
                continue
            others = [x.label for k in t.instead_of if (x := snap.measure(k)) is not None]
            instead = f", not {' or '.join(others)}" if others else ""
            out.append({"content": f"“{t.term}” is read as {m.label}{instead}. {t.explain}".strip()[:1900],
                        "metadata": {"kind": "glossary", "term": t.term, "fields": t.fields, "reading": t.reading,
                                     "instead_of": [k for k in t.instead_of if snap.measure(k) is not None]}})
            continue
        out.append({"content": f"{t.term} means: {t.means}. {t.explain}".strip()[:1900],
                    "metadata": {"kind": "glossary", "term": t.term, "fields": t.fields}})
    return out


def load_blueprints(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> list[Blueprint]:
    data = _load(pack, "report_blueprints.yaml", packs_dir)
    if data is None:
        return []
    out = BlueprintsFile.model_validate(data).blueprints
    ids = [b.id for b in out]
    if len(ids) != len(set(ids)):
        raise ValueError("report blueprints: ids must be distinct")
    return out


def load_recipes(pack: str | PackSource | None, packs_dir: str | Path | None = None) -> list[Recipe]:
    data = _load(pack, "analysis_recipes.yaml", packs_dir)
    if data is None:
        return []
    out = RecipesFile.model_validate(data).recipes
    ids = [r.id for r in out]
    if len(ids) != len(set(ids)):
        raise ValueError("analysis recipes: ids must be distinct")
    return out


def lint_recipes(recipes: list[Recipe], manifest: PackManifest) -> list[str]:
    """Every measure and dimension a recipe's slots offer is in the pack (CI pack lint)."""
    measures = {m["key"] for m in manifest.measures}
    dims = {d["key"] for d in manifest.dimensions}
    issues = []
    for r in recipes:
        issues += [f"{r.id}: unknown measure {m}" for m in sorted(r.keys("measure") - measures)]
        issues += [f"{r.id}: unknown dimension {d}" for d in sorted(r.keys("dimension") - dims)]
    return issues


def recipe_for_model(recipe: Recipe, snap: ModelSnapshot) -> Recipe | None:
    """The recipe with each slot cut to the options this model has (certified measures, its dimensions), its default
    the first left when the pack's is gone; None when a slot has no option left."""
    have = {"measure": {m.key for m in snap.measures if m.status == "certified"},
            "dimension": {d.key for d in snap.dimensions}, "grain": set(GRAINS)}
    slots = {}
    for name, slot in recipe.slots.items():
        options = [o for o in slot.options if o in have[slot.kind]]
        if not options:
            return None
        slots[name] = slot.model_copy(update={"options": options,
                                              "default": slot.default if slot.default in options else options[0]})
    return recipe.model_copy(update={"slots": slots})


def recipe_chunks(recipes: list[Recipe], snap: ModelSnapshot) -> list[dict[str, Any]]:
    """One chunk per recipe this model can run, naming only labels (never values); the recipe itself, cut to the
    model, is the chunk's ``recipe`` metadata, which Ask plans with once the document is approved. ``measures``
    hides the recipe from people who can't see every measure it offers."""
    mlabel = {m.key: m.label for m in snap.measures}
    dlabel = {d.key: d.label for d in snap.dimensions}
    out = []
    for raw in recipes:
        r = recipe_for_model(raw, snap)
        if r is None:
            continue
        slots = "; ".join(
            f"{name}: " + ", ".join(mlabel.get(o, o) if s.kind == "measure" else dlabel.get(o, o)
                                     if s.kind == "dimension" else o for o in s.options)
            for name, s in r.slots.items())
        defaults = {n: (mlabel.get(sl.default, sl.default) if sl.kind == "measure" else
                        dlabel.get(sl.default, sl.default) if sl.kind == "dimension" else sl.default).lower()
                    for n, sl in r.slots.items()}
        asks = _SLOT.sub(lambda m, d=defaults: d.get(m.group(1), m.group(1)), r.question)  # type: ignore[misc]
        lines = [f"Analysis recipe: {r.title}", f"Answers: {asks}", f"Asked as: {', '.join(r.triggers)}",
                 f"Slots: {slots}", f"Chart: {r.chart.replace('_', ' ')}"]
        if r.operations:
            lines.append(f"Operations: {', '.join(o.replace('_', ' ') for o in r.operations)}")
        if r.insights:
            lines.append(f"Insight checks: {', '.join(i.replace('_', ' ') for i in r.insights)}")
        out.append({"content": "\n".join(lines)[:1900],
                    "metadata": {"kind": "analysis_recipe", "recipe": r.id, "term": r.title,
                                 "synonyms": list(r.triggers), "measures": sorted(r.keys("measure")),
                                 "definition": r.model_dump(mode="json")}})
    return out


def lint(blueprints: list[Blueprint], rules: RulesFile | None, manifest: PackManifest) -> list[str]:
    """Every name a blueprint or rule uses exists in the pack (CI pack lint; a pack file can't name a measure, a
    dimension or a column the manifest doesn't have)."""
    measures = {m["key"]: m for m in manifest.measures}
    dims = {d["key"] for d in manifest.dimensions}
    fields = {e.key: {f.key for f in e.fields} for e in manifest.entities}
    issues = []
    for bp in blueprints:
        ms, ds = bp.refs()
        issues += [f"{bp.id}: unknown measure {m}" for m in sorted(ms - set(measures))]
        issues += [f"{bp.id}: unknown dimension {d}" for d in sorted(ds - dims)]
        if bp.table and bp.table.measure in measures:
            entity = measures[bp.table.measure]["entity"]
            cols = set(bp.table.columns) | ({bp.table.sort["field"]} if bp.table.sort else set())
            issues += [f"{bp.id}: {entity} has no field {c}" for c in sorted(cols - fields.get(entity, set()))]
    for r in rules.rules if rules else []:
        issues += [f"rule {r.id}: unknown measure {m}" for m in r.measures if m not in measures]
    return issues


# --------------------------------------------------------------------------- a blueprint on one model


def available(bp: Blueprint, snap: ModelSnapshot) -> tuple[Blueprint, list[str]]:
    """The blueprint cut to what this model has (certified measures, dimensions, the table's fields), and the
    parts left out with the reason."""
    measures = {m.key for m in snap.measures if m.status == "certified"}
    dims = {d.key for d in snap.dimensions}
    left: list[str] = []

    def ok_filters(fs: list[dict[str, Any]]) -> bool:
        return all(f["ref"] in dims for f in fs)

    if not ok_filters(bp.filters):
        return bp.model_copy(update={"kpis": [], "widgets": [], "table": None}), \
            [f"{bp.title}: the model has no {', '.join(f['ref'] for f in bp.filters if f['ref'] not in dims)}"]
    kpis: list[str | BlueprintKpi] = []
    for k in bp.kpis:
        key = k if isinstance(k, str) else k.measure
        if key in measures and (isinstance(k, str) or ok_filters(k.filters)):
            kpis.append(k)
        else:
            found = snap.measure(key)
            left.append(k.title if isinstance(k, BlueprintKpi) and k.title else found.label if found else
                        key.replace("_", " "))
    widgets = []
    for w in bp.widgets:
        need_dims = set(w.breakdowns) | ({w.time["ref"]} if w.time else set())
        if {w.measure, *w.extra_measures} <= measures and need_dims <= dims and ok_filters(w.filters):
            widgets.append(w)
        else:
            left.append(w.title)
    table = bp.table
    if table is not None:
        m = snap.measure(table.measure)
        entity = next((e for e in snap.entities if m is not None and e.key == m.entity), None)
        have = {f.key for f in entity.fields} if entity else set()
        cols = [c for c in table.columns if c in have]
        if table.measure not in measures or not cols:
            left.append(table.title)
            table = None
        elif len(cols) != len(table.columns) or (table.sort and table.sort["field"] not in have):
            table = table.model_copy(update={"columns": cols, "sort": table.sort if table.sort and
                                             table.sort["field"] in have else None})
    return bp.model_copy(update={"kpis": kpis, "widgets": widgets, "table": table}), left


def blueprint_spec(bp: Blueprint, w: BlueprintWidget | BlueprintKpi, *, model_version_id: str,
                   connection_id: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """The Query Spec of one blueprint figure, visual or table; the blueprint's filters apply to all of them."""
    filters = [*bp.filters, *w.filters]
    visual = w.visual if isinstance(w, BlueprintWidget) else "figure"
    spec: dict[str, Any] = {
        "model_version_id": model_version_id, "connection_id": connection_id,
        "measure": {"ref": f"measure:{w.measure}"},
        "extra_measures": [{"ref": f"measure:{m}"} for m in getattr(w, "extra_measures", [])],
        "breakdowns": [{"ref": f"dimension:{d}"} for d in getattr(w, "breakdowns", [])],
        "filters": [{**f, "ref": f"dimension:{f['ref']}"} for f in filters],
        "visual": {"type": visual, **({"note": w.note} if isinstance(w, BlueprintWidget) and w.note else {}),
                   **({"detail": detail} if detail else {})},
    }
    if isinstance(w, BlueprintWidget) and w.time:
        spec["time"] = {**w.time, "ref": f"dimension:{w.time['ref']}"}
    return spec


# --------------------------------------------------------------------------- knowledge chunks


def rules_for_model(rules: list[DashboardRule], snap: ModelSnapshot) -> list[DashboardRule]:
    measures = {m.key for m in snap.measures if m.status == "certified"}
    return [r for r in rules if set(r.measures) <= measures]


def rule_chunks(rules: list[DashboardRule]) -> list[dict[str, Any]]:
    out = []
    for r in rules:
        meta: dict[str, Any] = {"kind": "dashboard_rule", "rule": r.id, "term": r.title, "measures": sorted(r.measures)}
        if len(r.measures) == 1:
            meta["measure"] = r.measures[0]
        out.append({"content": f"Dashboard rule: {r.title}\n{r.rule}"[:1900], "metadata": meta})
    return out


def blueprint_chunks(blueprints: list[Blueprint], snap: ModelSnapshot) -> list[dict[str, Any]]:
    """One chunk per blueprint, naming only the parts this model has (labels, never values); metadata names the
    measures so retrieval hides a blueprint from people who can't see one of them."""
    mlabel = {m.key: m.label for m in snap.measures}
    dlabel = {d.key: d.label for d in snap.dimensions}
    out = []
    for raw in blueprints:
        if raw.unavailable:
            content = (f"Report blueprint: {raw.title}\nNot available: the data has no {', '.join(raw.unavailable)}. "
                       f"Asked as: {', '.join(raw.triggers)}.")
            out.append({"content": content[:1900], "metadata": {"kind": "report_blueprint", "blueprint": raw.id,
                                                                 "term": raw.title, "synonyms": list(raw.triggers),
                                                                 "unavailable": True, "measures": []}})
            continue
        bp, left = available(raw, snap)
        if not bp.kpis and not bp.widgets:
            continue
        lines = [f"Report blueprint: {bp.title} ({bp.section or bp.dashboard})", f"Question: {bp.question}",
                 f"Asked as: {', '.join(bp.triggers)}", f"Population: {bp.population}",
                 "Headline figures: " + ", ".join(k.title or mlabel.get(k.measure, k.measure) for k in bp.figures())]
        for w in bp.widgets:
            by = " by " + " and ".join(dlabel.get(d, d) for d in w.breakdowns) if w.breakdowns else ""
            lines.append(f"Visual: {w.title} — {mlabel.get(w.measure, w.measure)}{by} as "
                         f"{w.visual.replace('_', ' ')}")
        if bp.table:
            lines.append(f"Detail table: {bp.table.title} ({', '.join(bp.table.columns)})")
        if bp.callout:
            lines.append(f"Callout: {bp.callout.text}")
        lines.append(f"Footnote: {bp.footnote}")
        if left:
            lines.append(f"Not in this model: {', '.join(left)}")
        measures, dims = bp.refs()
        out.append({"content": "\n".join(lines)[:1900],
                    "metadata": {"kind": "report_blueprint", "blueprint": bp.id, "term": bp.title,
                                 "synonyms": list(bp.triggers), "measures": sorted(measures),
                                 "dimensions": sorted(dims)}})
    return out


async def index_guidance(conn: AsyncConnection, gateway: Any, tenant_id: str, version: dict[str, Any],
                         snap: ModelSnapshot, *, status: str, packs_dir: str | None = None) -> dict[str, int]:
    """(Re-)index the dashboard rules, report blueprints and analysis recipes of one model version (beside its chart
    patterns)."""
    pack = await pack_source_for(conn, version["data_model_id"], packs_dir)
    rules = load_rules(pack) if pack else None
    blueprints = load_blueprints(pack) if pack else []
    recipes = load_recipes(pack) if pack else []
    n_rules = await index_pack_document(conn, gateway, tenant_id, version, kind="dashboard_rule",
                                        title="Dashboard rules", status=status,
                                        chunks=rule_chunks(rules_for_model(rules.rules, snap)) if rules else [])
    n_blueprints = await index_pack_document(conn, gateway, tenant_id, version, kind="report_blueprint",
                                             title="Report blueprints", status=status,
                                             chunks=blueprint_chunks(blueprints, snap))
    n_recipes = await index_pack_document(conn, gateway, tenant_id, version, kind="analysis_recipe",
                                          title="Analysis recipes", status=status,
                                          chunks=recipe_chunks(recipes, snap))
    return {"dashboard_rules": n_rules, "report_blueprints": n_blueprints, "analysis_recipes": n_recipes}


async def index_glossary(conn: AsyncConnection, gateway: Any, tenant_id: str, version: dict[str, Any],
                         snap: ModelSnapshot, *, current: bool, packs_dir: str | None = None) -> int:
    """(Re-)index the pack glossary of one model version as Admin-reviewed knowledge (FEAT-016): pending review,
    unless the connection's previous pack glossary had the same text, whose review it keeps. Older versions'
    glossaries are retired."""
    pack = await pack_source_for(conn, version["data_model_id"], packs_dir) if current else None
    chunks = glossary_chunks(load_glossary(pack), snap) if pack else []
    digest = hashlib.sha256(repr(sorted(c["content"] for c in chunks)).encode()).hexdigest()
    prior = await fetch_one(conn, "SELECT review_status, content_hash FROM rag.knowledge_documents WHERE "
                                  "connection_id = :c AND source_kind = 'glossary' AND model_version_id IS NOT NULL "
                                  "AND model_version_id <> :v ORDER BY created_at DESC LIMIT 1",
                            {"c": version["connection_id"], "v": version["id"]})
    own = await fetch_one(conn, "SELECT review_status, content_hash FROM rag.knowledge_documents WHERE "
                                "model_version_id = :v AND source_kind = 'glossary'", {"v": version["id"]})
    keep = own if own is not None and own["content_hash"] == digest else prior
    status = keep["review_status"] if keep is not None and keep["content_hash"] == digest and keep["review_status"] \
        in ("approved", "rejected") else "pending_review"
    stored = await index_pack_document(conn, gateway, tenant_id, version, kind="glossary", title="Glossary",
                                       chunks=chunks, status=status)
    if stored and status == "pending_review":
        await conn.execute(text("UPDATE rag.knowledge_documents SET reviewed_by = NULL, reviewed_at = NULL WHERE "
                                "model_version_id = :v AND source_kind = 'glossary'"), {"v": version["id"]})
    await conn.execute(text("UPDATE rag.knowledge_documents d SET review_status = 'retired' FROM "
                            "model.model_versions m WHERE d.model_version_id = m.id AND m.data_model_id = :dm "
                            "AND d.source_kind = 'glossary' AND d.model_version_id <> :v "
                            "AND d.review_status <> 'retired'"),
                       {"dm": version["data_model_id"], "v": version["id"]})
    return stored
