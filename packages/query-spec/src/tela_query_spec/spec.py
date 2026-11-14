"""Query Spec v1 (ADR-001; technical_document §Query Spec).

Agents propose a Query Spec against an approved model version; code validates
and compiles it. A spec names certified things by reference
(``measure:<key>``, ``dimension:<key>``, ``field:<entity>.<field>``,
``personal:<key>``) and carries filter values only as data.

Client-supplied scope is never part of a spec: unknown top-level keys such as
``scope`` or ``scope_hash`` are dropped on parse (US-020).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tela_query_spec.expr import Condition
from tela_query_spec.expr import check_ref as check_field_ref

SPEC_VERSION = "1"
MAX_LIMIT = 10_000

_KEY = r"[a-z_][a-z0-9_]{0,62}"
REF = re.compile(rf"^(measure|dimension|personal):{_KEY}$|^field:{_KEY}\.{_KEY}$")

Scalar = str | int | float | bool


def check_ref(value: str) -> str:
    if not REF.match(value):
        raise ValueError(f"'{value}' is not a valid reference")
    return value


def ref_kind(ref: str) -> str:
    return ref.split(":", 1)[0]


def ref_key(ref: str) -> str:
    return ref.split(":", 1)[1]


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


ADHOC_KEY = "_adhoc"
ADHOC_REF = f"measure:{ADHOC_KEY}"
MAX_ADHOC_FIELDS = 8
MAX_ADHOC_DEPTH = 4
MAX_ADHOC_BRANCHES = 12


class AdhocMeasure(_S):
    """A question's own rule (US-163; FEAT-060): one aggregate over one entity with a condition, in the measure
    expression format (never SQL). Runs only with a live ``ask.freehand`` grant on the connection and is always
    labelled not certified. ``plain_rule`` is the rule in plain words, shown with the answer."""

    entity: str
    agg: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: str | None = None
    where: Condition | None = None
    label: str = Field(min_length=1, max_length=120)
    plain_rule: str = Field(min_length=1, max_length=500)

    @field_validator("entity")
    @classmethod
    def _entity(cls, v: str) -> str:
        if not re.match(rf"^{_KEY}$", v):
            raise ValueError("invalid entity key")
        return v

    _f = field_validator("field")(lambda v: v if v is None else check_field_ref(v))

    @model_validator(mode="after")
    def _field_needed(self) -> AdhocMeasure:
        if self.agg != "count" and self.field is None:
            raise ValueError(f"'{self.agg}' needs a field")
        return self


class MeasureRef(_S):
    """A certified (or personal) measure by reference, or an ad-hoc rule (``adhoc``, whose ref is always
    ``measure:_adhoc`` so every reader of ``measure.ref`` keeps working)."""

    ref: str
    adhoc: AdhocMeasure | None = None

    @model_validator(mode="before")
    @classmethod
    def _adhoc_ref(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("adhoc") is not None and "ref" not in data:
            return {**data, "ref": ADHOC_REF}
        return data

    @field_validator("ref")
    @classmethod
    def _measure(cls, v: str) -> str:
        check_ref(v)
        if ref_kind(v) != "measure":
            raise ValueError("a measure reference must start with 'measure:'")
        return v

    @model_validator(mode="after")
    def _adhoc_matches(self) -> MeasureRef:
        if (self.ref == ADHOC_REF) != (self.adhoc is not None):
            raise ValueError(f"'{ADHOC_REF}' is used for an ad-hoc rule and only for it")
        return self


class Breakdown(_S):
    ref: str

    @field_validator("ref")
    @classmethod
    def _dim(cls, v: str) -> str:
        check_ref(v)
        if ref_kind(v) == "measure":
            raise ValueError("break down by a dimension, field or personal field, not a measure")
        return v


Grain = Literal["day", "week", "month", "quarter", "year"]
Relative = Literal[
    "today", "last_7_days", "last_30_days", "last_90_days", "this_month", "last_month", "this_quarter",
    "last_quarter", "this_year", "last_year", "last_6_months", "last_12_months", "next_30_days", "next_90_days",
]


class TimeRange(_S):
    relative: Relative | None = None
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _one(self) -> TimeRange:
        if self.relative is None and self.start is None and self.end is None:
            raise ValueError("give a relative range or from/to dates")
        if self.relative is not None and (self.start or self.end):
            raise ValueError("use either a relative range or from/to, not both")
        if self.start and self.end and self.start > self.end:
            raise ValueError("'from' must not be after 'to'")
        return self


class TimeSpec(_S):
    ref: str
    grain: Grain | None = None
    range: TimeRange | None = None

    _r = field_validator("ref")(check_ref)


FilterOp = Literal["=", "!=", "in", "not_in", "<", "<=", ">", ">=", "between", "is_null", "not_null"]


class Filter(_S):
    ref: str
    op: FilterOp
    values: list[Scalar] = Field(default_factory=list, max_length=500)

    _r = field_validator("ref")(check_ref)

    @model_validator(mode="after")
    def _arity(self) -> Filter:
        n = len(self.values)
        if self.op in ("is_null", "not_null") and n:
            raise ValueError(f"'{self.op}' takes no values")
        if self.op in ("=", "!=", "<", "<=", ">", ">=") and n != 1:
            raise ValueError(f"'{self.op}' takes exactly one value")
        if self.op == "between" and n != 2:
            raise ValueError("'between' takes two values")
        if self.op in ("in", "not_in") and n == 0:
            raise ValueError(f"'{self.op}' needs at least one value")
        return self


class Group(_S):
    label: str = Field(min_length=1, max_length=80)
    values: list[Scalar] = Field(min_length=1, max_length=500)


class Band(_S):
    label: str = Field(min_length=1, max_length=80)
    lower: float
    upper: float | None = None  # None = open-ended (last band only)


class PersonalField(_S):
    """A personal grouping (RULE-012) or band (RULE-013) over one source field."""

    key: str
    kind: Literal["grouping", "band"]
    source: str  # dimension:<key> or field:<entity>.<field>
    groups: list[Group] = Field(default_factory=list, max_length=50)
    catch_all: str = "Everything else"
    not_set: str = "Not set"
    case_sensitive: bool = False
    bands: list[Band] = Field(default_factory=list, max_length=50)
    closing_max: float | None = None  # upper-inclusive maximum of the last band

    @field_validator("key")
    @classmethod
    def _k(cls, v: str) -> str:
        if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", v):
            raise ValueError("invalid personal field key")
        return v

    _s = field_validator("source")(check_ref)

    @model_validator(mode="after")
    def _shape(self) -> PersonalField:
        if self.kind == "grouping":
            if not self.groups:
                raise ValueError("a grouping needs at least one group")
            seen: dict[str, str] = {}
            for g in self.groups:
                for v in g.values:
                    norm = str(v) if self.case_sensitive else str(v).strip().lower()
                    if norm in seen and seen[norm] != g.label:
                        raise ValueError(f"value '{v}' is in two groups: '{seen[norm]}' and '{g.label}'")
                    seen[norm] = g.label
        else:
            if not self.bands:
                raise ValueError("bands need at least one band")
            for i, b in enumerate(self.bands):
                last = i == len(self.bands) - 1
                if b.upper is None and not last:
                    raise ValueError(f"only the last band may be open-ended ('{b.label}')")
                if b.upper is not None and b.upper <= b.lower:
                    raise ValueError(f"band '{b.label}' upper edge must be above its lower edge")
                if i and self.bands[i - 1].upper != b.lower:
                    prev = self.bands[i - 1]
                    raise ValueError(
                        f"bands '{prev.label}' and '{b.label}' do not meet: {prev.upper} vs {b.lower}"
                    )
        return self


class PersonalMeasure(_S):
    """A Creator's cross-table measure (RULE-014): one aggregate over ``entity``, optionally counting only rows
    linked through approved links to ``linked_to``. Referenced in the spec as ``measure:<key>``."""

    key: str
    label: str = Field(min_length=1, max_length=120)
    entity: str
    agg: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: str | None = None
    linked_to: str | None = None

    @field_validator("key", "entity", "linked_to", "field")
    @classmethod
    def _ident(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[a-z_][a-z0-9_]{0,62}$", v):
            raise ValueError("invalid key")
        return v

    @model_validator(mode="after")
    def _field_needed(self) -> PersonalMeasure:
        if self.agg != "count" and self.field is None:
            raise ValueError(f"'{self.agg}' needs a field")
        return self


MAX_MEASURES = 6  # EXEC-172: 4 → 6, so a question listing five costs is one answer
DerivedOp = Literal["ratio", "difference", "percent_of", "percent_change"]


class Derived(_S):
    """A figure worked out from two of the spec's certified measures, row by row (US-176): ``ratio`` (of / to),
    ``difference`` (of - to), ``percent_of`` (of / to * 100) and ``percent_change`` ((of - to) / to * 100). A zero
    or blank ``to`` gives a blank, never an error. ``key`` names the result column."""

    key: str
    op: DerivedOp
    of: str
    to: str
    label: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("key")
    @classmethod
    def _k(cls, v: str) -> str:
        if not re.match(rf"^{_KEY}$", v) or v.endswith(SHARE_SUFFIX):
            raise ValueError("invalid derived key")
        return v

    @field_validator("of", "to")
    @classmethod
    def _measure(cls, v: str) -> str:
        check_ref(v)
        if ref_kind(v) != "measure":
            raise ValueError("a derived figure is worked out from measures")
        return v


SHARE_SUFFIX = "__share"


def share_key(measure_ref: str) -> str:
    """Result column of a measure's share of the total (US-176)."""
    return ref_key(measure_ref) + SHARE_SUFFIX


class Sort(_S):
    # "measure" (the primary), a measure ref, a breakdown ref, "derived:<key>" or "share:<measure key>" (US-176)
    by: str
    dir: Literal["asc", "desc"] = "desc"


class Compare(_S):
    kind: Literal["previous_period", "same_period_last_year"]


class ExistsIn(_S):
    """Semi-join filter (US-178): keep root rows that have at least one linked row matching all conditions.
    ``entity`` is the linked entity key; ``filters`` are applied on that entity. ``measure`` (a certified count
    measure of that entity, e.g. ``measure:high_exposure_risks``) adds that measure's own rule, so "projects with
    an open high-exposure risk" uses the certified definition. The compiler emits an ``EXISTS`` subquery joined on
    the approved model link; the root rows are distinct."""

    entity: str
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    measure: str | None = None

    @model_validator(mode="after")
    def _something(self) -> ExistsIn:
        if not self.filters and self.measure is None:
            raise ValueError("a semi-join needs at least one filter or a measure")
        if self.measure is not None and not self.measure.startswith("measure:"):
            raise ValueError("measure must be a measure: reference")
        return self

    @field_validator("entity")
    @classmethod
    def _entity(cls, v: str) -> str:
        if not re.match(rf"^{_KEY}$", v):
            raise ValueError("invalid entity key")
        return v


class Cohort(_S):
    """Cohort breakdown (US-178): group root records by a date bucket of a linked entity's date field.
    When the link is one-to-many, ``agg`` picks which date to use (earliest or latest). The compiler
    emits a subquery that maps each root primary key to its cohort period."""

    ref: str  # dimension:<key> — must be temporal and on a linked entity
    grain: Grain
    agg: Literal["min", "max"] = "min"

    _r = field_validator("ref")(check_ref)


Percentile = Literal["p50", "p85", "p90"]
PERCENTILE_VALUES: dict[str, float] = {"p50": 0.5, "p85": 0.85, "p90": 0.9}
MAX_TOP_N_PER_GROUP = 50
MAX_MOVING_AVG_WINDOW = 24
MAX_NTILE_BUCKETS = 10


class TopNPerGroup(_S):
    """Top/bottom N rows within each breakdown group (US-177): ``ROW_NUMBER() OVER (PARTITION BY group
    ORDER BY measure) <= n``. Requires at least one breakdown."""

    by: str  # breakdown ref to partition by
    n: int = Field(ge=1, le=MAX_TOP_N_PER_GROUP)
    order: Literal["desc", "asc"] = "desc"

    _b = field_validator("by")(check_ref)


class RunningTotal(_S):
    """Cumulative sum of the measure along the time grain (US-177), optionally per breakdown.
    Requires a time grain."""

    measure: str | None = None  # measure ref; None = the primary measure
    per: str | None = None  # breakdown ref to partition by; None = over the whole series

    @field_validator("measure")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        if v is not None:
            check_ref(v)
            if ref_kind(v) != "measure":
                raise ValueError("running_total.measure must be a measure reference")
        return v

    _p = field_validator("per")(lambda v: v if v is None else check_ref(v))


class MovingAvg(_S):
    """Moving average of the measure over ``window`` periods along the time grain (US-177).
    Requires a time grain."""

    window: int = Field(ge=2, le=MAX_MOVING_AVG_WINDOW)
    measure: str | None = None  # measure ref; None = the primary measure
    per: str | None = None  # breakdown ref to partition by

    @field_validator("measure")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        if v is not None:
            check_ref(v)
            if ref_kind(v) != "measure":
                raise ValueError("moving_avg.measure must be a measure reference")
        return v

    _p = field_validator("per")(lambda v: v if v is None else check_ref(v))


class Rank(_S):
    """RANK() of the measure per optional group (US-177). Result column is ``<measure_key>__rank``."""

    measure: str | None = None  # measure ref; None = the primary measure
    per: str | None = None  # breakdown ref to partition by
    order: Literal["desc", "asc"] = "desc"

    @field_validator("measure")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        if v is not None:
            check_ref(v)
            if ref_kind(v) != "measure":
                raise ValueError("rank.measure must be a measure reference")
        return v

    _p = field_validator("per")(lambda v: v if v is None else check_ref(v))


class Bucket(_S):
    """NTILE(k) bucket of the measure (US-177): splits rows into ``k`` equal-size buckets, labelled
    "Top quartile", "2nd quartile", … for k=4 or "Top N%", … for other k."""

    k: int = Field(ge=2, le=MAX_NTILE_BUCKETS)
    measure: str | None = None  # measure ref; None = the primary measure
    per: str | None = None  # breakdown ref to partition by
    order: Literal["desc", "asc"] = "desc"

    @field_validator("measure")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        if v is not None:
            check_ref(v)
            if ref_kind(v) != "measure":
                raise ValueError("bucket.measure must be a measure reference")
        return v

    _p = field_validator("per")(lambda v: v if v is None else check_ref(v))


class PercentileOp(_S):
    """Percentile of the measure per group (US-177): P50, P85 or P90 via ``PERCENTILE_CONT`` where
    available, an approximate equivalent elsewhere."""

    p: Percentile
    measure: str | None = None  # measure ref; None = the primary measure
    per: str | None = None  # breakdown ref to partition by

    @field_validator("measure")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        if v is not None:
            check_ref(v)
            if ref_kind(v) != "measure":
                raise ValueError("percentile.measure must be a measure reference")
        return v

    _p = field_validator("per")(lambda v: v if v is None else check_ref(v))


HavingOp = Literal["=", "!=", "<", "<=", ">", ">="]


class Having(_S):
    """Post-aggregate filter (US-177): keep only groups where the measure passes a threshold.
    Compiled as ``HAVING`` or an outer ``WHERE``."""

    measure: str | None = None  # measure ref; None = the primary measure
    op: HavingOp
    value: int | float

    @field_validator("measure")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        if v is not None:
            check_ref(v)
            if ref_kind(v) != "measure":
                raise ValueError("having.measure must be a measure reference")
        return v


class FigureTone(_S):
    """A figure's tone from a threshold (US-157): at or past ``warning`` it is a warning, at or past ``critical``
    critical, otherwise good. ``higher_is_worse`` says which way "past" is (overdue projects: true)."""

    warning: float | None = None
    critical: float | None = None
    higher_is_worse: bool = True


class DetailSort(_S):
    field: str
    dir: Literal["asc", "desc"] = "asc"

    @field_validator("field")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", v):
            raise ValueError("invalid field key")
        return v


class Detail(_S):
    """A detail table (US-158): the records of the measure's population, one row each, with these columns (fields
    of the measure's entity), a default sort and the name its export uses."""

    columns: list[str] = Field(min_length=1, max_length=12)
    sort: DetailSort | None = None
    export_name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("columns")
    @classmethod
    def _keys(cls, v: list[str]) -> list[str]:
        if any(not re.match(r"^[a-z_][a-z0-9_]{0,62}$", k) for k in v) or len(set(v)) != len(v):
            raise ValueError("columns are distinct field keys")
        return v


# ENH-023: a widget card's colour (US-223) and its chart's colours (US-224) are palette swatches, never free colours,
# so light, dark and a tenant's palette carry over and the contrast checks of US-150 still hold.
CARD_TONES = ("default", "accent", "neutral", "good", "warning", "critical", "brand", "dark")
FILLED_CARDS = frozenset({"brand", "dark"})  # filled cards hold a single figure only: a chart's axes need the page
CardTone = Literal["default", "accent", "neutral", "good", "warning", "critical", "brand", "dark"]
ChartScheme = Literal["brand", "single", "status", "warm", "cool", "grey", "vivid"]
Swatch = Literal["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6", "chart-7", "chart-8", "chart-other",
                 "accent", "status-ok", "status-warn", "status-danger"]
NO_CHART = frozenset({"figure", "table", "meter", "heat_grid"})  # visuals without series colours
MAX_CONTROLS = 3


class ChartColours(_S):
    """A chart's colours (US-224): a scheme, and a swatch for any series or bar named by its label."""

    scheme: ChartScheme = "brand"
    overrides: dict[str, Swatch] = Field(default_factory=dict, max_length=24)

    @field_validator("overrides")
    @classmethod
    def _labels(cls, v: dict[str, str]) -> dict[str, str]:
        if any(not k.strip() or len(k) > 120 for k in v):
            raise ValueError("each colour names a series or bar label (1 to 120 characters)")
        return v


class Visual(_S):
    type: Literal["figure", "ranked_bar", "columns", "trend", "paired_bars", "stacked_status", "donut", "heat_grid",
                  "meter", "dumbbell", "waterfall", "bullet", "pareto", "xmr", "gantt", "funnel", "small_multiples",
                  "calendar", "sankey", "upset", "beeswarm", "ridgeline", "survival", "bump", "treemap", "waffle",
                  "table"]
    tone: FigureTone | None = None  # figure: its tone; bullet: the target band (US-187)
    note: str | None = Field(default=None, max_length=200)  # meter only: what passed means
    detail: Detail | None = None  # table only: list records instead of a breakdown
    colour: bool | None = None  # table only: status words as RAG badges, figures tinted by size (ENH-020)
    card: CardTone | None = None  # the card's colour (US-223); brand and dark fill a figure's card only
    colours: ChartColours | None = None  # the chart's colours (US-224)
    # US-221: fields a reader can filter this widget by, from inside the widget (dimension or field refs).
    controls: list[str] | None = Field(default=None, max_length=MAX_CONTROLS)

    @model_validator(mode="after")
    def _detail_on_table(self) -> Visual:
        if self.detail is not None and self.type != "table":
            raise ValueError("only a table lists records")
        if self.colour and self.type != "table":
            raise ValueError("only a table has colours")
        if self.card in FILLED_CARDS and self.type != "figure":
            raise ValueError("only a single figure sits on a filled card")
        if self.colours is not None and self.type in NO_CHART:
            raise ValueError("this visual has no chart colours")
        return self

    @field_validator("controls")
    @classmethod
    def _controls(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        for r in v:
            check_ref(r)
            if ref_kind(r) not in ("dimension", "field"):
                raise ValueError("a widget filter is a dimension or a field")
        if len(set(v)) != len(v):
            raise ValueError("a widget filter is listed twice")
        return v or None


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")  # strips client-supplied scope keys (US-020)

    spec_version: Literal["1"] = "1"
    model_version_id: str = Field(min_length=1, max_length=64)
    connection_id: str = Field(min_length=1, max_length=64)
    measure: MeasureRef
    extra_measures: list[MeasureRef] = Field(default_factory=list, max_length=MAX_MEASURES - 1)
    derived: list[Derived] = Field(default_factory=list, max_length=3)
    share_of_total: list[str] = Field(default_factory=list, max_length=MAX_MEASURES)  # measure refs
    breakdowns: list[Breakdown] = Field(default_factory=list, max_length=3)
    time: TimeSpec | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    personal_fields: list[PersonalField] = Field(default_factory=list, max_length=5)
    personal_measures: list[PersonalMeasure] = Field(default_factory=list, max_length=3)
    sort: list[Sort] = Field(default_factory=list, max_length=4)
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)
    compare: Compare | None = None
    exists_in: list[ExistsIn] = Field(default_factory=list, max_length=3)
    cohort: Cohort | None = None
    # Window operations (US-177)
    top_n_per_group: TopNPerGroup | None = None
    running_total: RunningTotal | None = None
    moving_avg: MovingAvg | None = None
    rank: Rank | None = None
    bucket: Bucket | None = None
    percentile: PercentileOp | None = None
    having: Having | None = None
    visual: Visual | None = None
    follow_page_filters: bool = True

    @model_validator(mode="before")
    @classmethod
    def _measures_list(cls, data: Any) -> Any:
        """``measures: [a, b, …]`` (up to four, US-176) is the same as ``measure: a`` with ``extra_measures``."""
        if isinstance(data, dict) and "measures" in data:
            data = dict(data)
            listed = data.pop("measures")
            if not isinstance(listed, list) or not listed:
                raise ValueError("'measures' lists at least one measure")
            if "measure" in data or data.get("extra_measures"):
                raise ValueError("give 'measures' or 'measure' with 'extra_measures', not both")
            if len(listed) > MAX_MEASURES:
                raise ValueError(f"at most {MAX_MEASURES} measures in one answer")
            data["measure"], data["extra_measures"] = listed[0], listed[1:]
        return data

    @model_validator(mode="after")
    def _adhoc_primary(self) -> QuerySpec:
        if any(m.adhoc is not None for m in self.extra_measures):
            raise ValueError("an ad-hoc rule can only be the main measure")
        if any(p.key == ADHOC_KEY for p in self.personal_measures):
            raise ValueError(f"'{ADHOC_KEY}' is reserved")
        return self

    @model_validator(mode="after")
    def _derived_and_shares(self) -> QuerySpec:
        refs = self.all_measure_refs()
        if len(set(refs)) != len(refs):
            raise ValueError("the same measure is listed twice")
        for d in self.derived:
            if d.of == d.to:
                raise ValueError(f"'{d.key}' needs two different measures")
            for r in (d.of, d.to):
                if r not in refs:
                    raise ValueError(f"'{d.key}' uses {r}, which is not one of this answer's measures")
                if r == ADHOC_REF:
                    raise ValueError("a derived figure is worked out from certified measures")
        keys = [d.key for d in self.derived]
        if len(set(keys)) != len(keys) or set(keys) & {ref_key(r) for r in refs}:
            raise ValueError("each derived figure needs its own key")
        for r in self.share_of_total:
            if r not in refs:
                raise ValueError(f"share of total of {r}, which is not one of this answer's measures")
        if len(set(self.share_of_total)) != len(self.share_of_total):
            raise ValueError("share of total lists a measure twice")
        return self

    @property
    def adhoc(self) -> AdhocMeasure | None:
        return self.measure.adhoc

    def all_measure_refs(self) -> list[str]:
        return [self.measure.ref, *(m.ref for m in self.extra_measures)]


# Keys a client may try to smuggle in; they are dropped and counted (US-020).
STRIPPED_KEYS = frozenset({"scope", "scope_hash", "scope_predicates", "all_rows", "tenant_id", "sql"})


def parse_spec(raw: dict[str, Any]) -> tuple[QuerySpec, list[str]]:
    """Parse (and migrate) a stored or submitted spec. Returns the spec and any stripped keys."""
    data = migrate_spec(dict(raw))
    stripped = sorted(k for k in data if k in STRIPPED_KEYS)
    return QuerySpec.model_validate({k: v for k, v in data.items() if k not in STRIPPED_KEYS}), stripped


def migrate_spec(data: dict[str, Any]) -> dict[str, Any]:
    """Migration hook for stored specs (US-017). Version 1 is current; older shapes upgrade here."""
    version = str(data.get("spec_version", SPEC_VERSION))
    if version == SPEC_VERSION:
        data["spec_version"] = SPEC_VERSION
        return data
    raise ValueError(f"unsupported spec_version '{version}'")
