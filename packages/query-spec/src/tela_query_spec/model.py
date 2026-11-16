"""Model snapshot: the frozen meaning of one connection's data (DATA-014..021).

An approved model version is stored as a snapshot of this shape
(``model_versions.definition_snapshot``). The validator and compiler read only
the snapshot, so a spec always runs against exactly the version it pins
(RULE-015). Packs (FEAT-051) describe their content in the same shape.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tela_query_spec.expr import MeasureExpr, Value, check_ident, measure_fields, split_ref, value_fields

FieldType = Literal["text", "integer", "decimal", "date", "datetime", "boolean", "json"]
Sensitivity = Literal["public", "internal", "confidential", "personal", "sensitive"]
Cardinality = Literal["one_to_one", "many_to_one", "one_to_many"]
NUMERIC_TYPES = frozenset({"integer", "decimal"})
TEMPORAL_TYPES = frozenset({"date", "datetime"})


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelField(_M):
    key: str
    source_column: str
    label: str
    data_type: FieldType
    # How the source stores the column, stamped from the catalogue at approval. A ``date`` field over a
    # ``datetime`` column (SQL Server and Dataverse keep dates as date-times) is read as its day: see ``reads_day``.
    source_type: FieldType | None = None
    description: str | None = None
    semantic_type: str | None = None  # currency, percent, email, phone, person_name, identifier, free_text
    format: dict[str, str | int] = Field(default_factory=dict)
    sensitivity: Sensitivity = "internal"
    is_queryable: bool = True
    blank_handling: str | None = None
    values: list[str] = Field(default_factory=list)  # the column's values when it has few (model builder profile)

    _k = field_validator("key")(check_ident)

    @property
    def reads_day(self) -> bool:
        """A date field over a date-time column: queries cast the column to a date."""
        return self.data_type == "date" and self.source_type == "datetime"

    @property
    def reads_text(self) -> bool:
        """A text field over a yes/no, number or date column (a pack field mapped onto it, BUG-021): queries read
        the column as text, a yes/no column as "Yes" / "No"."""
        return self.data_type == "text" and self.source_type in ("boolean", "integer", "decimal", "date", "datetime")


class ModelEntity(_M):
    key: str
    label: str
    source_object: str  # qualified name in the source, e.g. "public.projects"
    primary_key: list[str] = Field(min_length=1)  # field keys
    source_checksum: str | None = None  # checksum of the used columns when the version was approved
    display_fields: list[str] = Field(default_factory=list)  # record view / drill columns (FEAT-044)
    description: str | None = None
    grain: str | None = None
    fields: list[ModelField] = Field(min_length=1)

    _k = field_validator("key")(check_ident)

    @model_validator(mode="after")
    def _keys(self) -> ModelEntity:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"entity '{self.key}' has duplicate field keys")
        missing = set(self.primary_key) - set(keys)
        if missing:
            raise ValueError(f"entity '{self.key}' primary key uses unknown fields {sorted(missing)}")
        unknown = set(self.display_fields) - set(keys)
        if unknown:
            raise ValueError(f"entity '{self.key}' display fields use unknown fields {sorted(unknown)}")
        return self

    def record_fields(self) -> list[str]:
        """Fields listed for a record (drill): configured display fields, else the first queryable ones."""
        if self.display_fields:
            return list(dict.fromkeys([*self.primary_key, *self.display_fields]))
        return list(dict.fromkeys([*self.primary_key, *(f.key for f in self.fields if f.is_queryable)]))[:9]

    def field(self, key: str) -> ModelField | None:
        return next((f for f in self.fields if f.key == key), None)


class ModelLink(_M):
    from_entity: str
    to_entity: str
    from_fields: list[str] = Field(min_length=1)
    to_fields: list[str] = Field(min_length=1)
    cardinality: Cardinality
    origin: Literal["foreign_key", "proposed", "admin", "pack"] = "admin"
    accepted: bool = True
    # Rows of ``from_entity`` are visible only when their ``to_entity`` row is in the person's scope (US-155), e.g.
    # a project's risks follow the project's CIO scope. Many-to-one on one field only.
    inherits_scope: bool = False

    @model_validator(mode="after")
    def _same_len(self) -> ModelLink:
        if len(self.from_fields) != len(self.to_fields):
            raise ValueError("link field lists must have the same length")
        if self.inherits_scope and (self.cardinality != "many_to_one" or len(self.from_fields) != 1):
            raise ValueError("a link that passes scope on is many-to-one on one field")
        return self


class Measure(_M):
    key: str
    label: str
    entity: str
    expression: MeasureExpr
    plain_rule: str
    definition: str | None = None  # one-line tip shown with the figure (US-156)
    blank_handling: str = "Blank values are not counted."
    format: Literal["integer", "decimal", "currency", "percent"] = "integer"
    currency_field: str | None = None  # RULE-023
    rule_ref: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    # "adhoc": a question's own rule built for one run (US-163); never stored in a model version.
    status: Literal["certified", "deprecated", "adhoc"] = "certified"

    _k = field_validator("key")(check_ident)


class Band(_M):
    """One labelled range of a banded dimension: value > ``above`` and value <= ``upto`` (either side open)."""

    label: str = Field(min_length=1, max_length=60)
    above: float | None = None
    upto: float | None = None

    @model_validator(mode="after")
    def _range(self) -> Band:
        if self.above is not None and self.upto is not None and self.upto <= self.above:
            raise ValueError(f"band '{self.label}' is empty")
        return self


class Dimension(_M):
    """A breakdown. ``field`` is the field it is about (catalogue, hiding, gaps). A computed dimension (US-156)
    shows a row value instead: ``of`` alone for a text value (e.g. calculated health), ``of`` with ``bands`` for
    labelled ranges of a number (e.g. days on hold). ``not_set`` labels blanks; ``other`` values outside every band."""

    key: str
    label: str
    entity: str
    field: str
    is_temporal: bool = False
    is_status: bool = False
    sort_order: list[str] | None = None
    value_labels: dict[str, str] = Field(default_factory=dict)  # display mapping, e.g. Yellow -> Amber
    synonyms: list[str] = Field(default_factory=list)
    of: Value | None = None
    bands: list[Band] | None = Field(default=None, min_length=1, max_length=12)
    not_set: str | None = None
    other: str = "Other"
    # The question shapes this breakdown answers (US-194): "why" → reasons, "how long" → aging bands, "how late" →
    # lateness bands, "when" → time horizon. The rules planner adds it when a question asks that way.
    answers: list[Literal["why", "how_long", "how_late", "when"]] = Field(default_factory=list)

    _k = field_validator("key")(check_ident)

    @model_validator(mode="after")
    def _computed(self) -> Dimension:
        if self.bands is not None and self.of is None:
            raise ValueError(f"dimension '{self.key}' has bands but no value ('of')")
        if self.of is not None and self.is_temporal:
            raise ValueError(f"dimension '{self.key}' is computed and can't be a time dimension")
        return self

    @property
    def computed(self) -> bool:
        return self.of is not None

    def labels(self) -> list[str]:
        """Every label a computed or banded dimension can show, in order."""
        out = [b.label for b in self.bands or []]
        if self.bands:
            out.append(self.other)
        if self.not_set:
            out.append(self.not_set)
        return list(dict.fromkeys(out))

    def fields(self) -> set[str]:
        return {self.field} | value_fields(self.of)


class BusinessRule(_M):
    """A business term and the condition that defines it, read by the model builder from the Admin's documents (or
    inferred from the schema when there are none) and checked against the source columns. Direct SQL is given the
    rules a question touches, so the model writes the agreed condition instead of guessing (no rule text in code)."""

    key: str
    term: str = Field(min_length=1, max_length=120)
    meaning: str = Field(default="", max_length=600)
    entity: str | None = None  # entity key the condition is written on; None for a rule that is not about one table
    sql: str | None = Field(default=None, max_length=2000)  # a boolean condition or expression on the source columns
    # condition: a filter for the term; expression: a calculation; population: the filter that applies when a
    # question names the records without a status of its own ("how many projects"); display: how a column's values
    # are shown (an expression to select instead of the bare column).
    kind: Literal["condition", "expression", "population", "display"] = "condition"
    synonyms: list[str] = Field(default_factory=list)
    always: bool = False  # applies to every question on its entity (default scope, what "open" means)
    origin: Literal["document", "inferred", "admin"] = "document"
    cite: str | None = Field(default=None, max_length=200)  # the document and section it came from
    status: Literal["ok", "needs_review", "off"] = "ok"
    issue: str | None = Field(default=None, max_length=300)  # why it needs review (an unknown column, bad SQL)

    _k = field_validator("key")(check_ident)


class ModelSnapshot(_M):
    model_version_id: str
    connection_id: str
    version_no: int | None = None
    engine: str = "postgres"
    entities: list[ModelEntity] = Field(min_length=1)
    links: list[ModelLink] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)  # business terms and their conditions (model builder)
    settings: dict[str, str] = Field(default_factory=dict)  # e.g. reporting_date for date-relative pack rules
    # File sources (US-108): the uploaded file version the model was approved on; queries read that version.
    source_pin: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _integrity(self) -> ModelSnapshot:
        ents = {e.key: e for e in self.entities}
        if len(ents) != len(self.entities):
            raise ValueError("duplicate entity keys")
        for link in self.links:
            for ent_key, fkeys in ((link.from_entity, link.from_fields), (link.to_entity, link.to_fields)):
                ent = ents.get(ent_key)
                if ent is None:
                    raise ValueError(f"link references unknown entity '{ent_key}'")
                bad = [k for k in fkeys if ent.field(k) is None]
                if bad:
                    raise ValueError(f"link references unknown fields {bad} on '{ent_key}'")
        if len({m.key for m in self.measures}) != len(self.measures):
            raise ValueError("duplicate measure keys")
        if len({d.key for d in self.dimensions}) != len(self.dimensions):
            raise ValueError("duplicate dimension keys")
        def unknown(entity: str, refs: set[str]) -> list[str]:
            bad = []
            for r in refs:
                ek, fk = split_ref(entity, r)
                e = ents.get(ek)
                if e is None or e.field(fk) is None or (ek != entity and self.join_path(entity, ek) is None):
                    bad.append(r)
            return sorted(bad)

        for m in self.measures:
            if m.status == "adhoc":
                raise ValueError(f"measure '{m.key}' is a free-hand rule and can't be part of a model")
            if m.entity not in ents:
                raise ValueError(f"measure '{m.key}' uses unknown entity '{m.entity}'")
            bad = unknown(m.entity, measure_fields(m.expression))
            if bad:
                raise ValueError(f"measure '{m.key}' uses unknown fields {bad}")
        for d in self.dimensions:
            ent = ents.get(d.entity)
            if ent is None or ent.field(d.field) is None:
                raise ValueError(f"dimension '{d.key}' uses unknown field '{d.entity}.{d.field}'")
            bad = [r for r in unknown(d.entity, d.fields()) if "." not in r] or [r for r in d.fields() if "." in r]
            if bad:
                raise ValueError(f"dimension '{d.key}' uses unknown fields {sorted(bad)}")
        return self

    # ------------------------------------------------------------------ lookups

    def entity(self, key: str) -> ModelEntity | None:
        return next((e for e in self.entities if e.key == key), None)

    def measure(self, key: str) -> Measure | None:
        return next((m for m in self.measures if m.key == key), None)

    def dimension(self, key: str) -> Dimension | None:
        return next((d for d in self.dimensions if d.key == key), None)

    def join_path(self, start: str, goal: str) -> list[tuple[ModelLink, bool]] | None:
        """Shortest path of accepted links from ``start`` to ``goal``.

        Each step is (link, forward) where forward means the link is walked from
        its ``from_entity``. Only accepted links are join paths (RULE-014).
        """
        if start == goal:
            return []
        seen = {start}
        queue: deque[tuple[str, list[tuple[ModelLink, bool]]]] = deque([(start, [])])
        while queue:
            node, path = queue.popleft()
            for link in self.links:
                if not link.accepted:
                    continue
                for forward, here, there in ((True, link.from_entity, link.to_entity),
                                             (False, link.to_entity, link.from_entity)):
                    if here != node or there in seen:
                        continue
                    step = [*path, (link, forward)]
                    if there == goal:
                        return step
                    seen.add(there)
                    queue.append((there, step))
        return None


def step_fans_out(link: ModelLink, forward: bool) -> bool:
    """True when walking this step can multiply rows of the side we came from."""
    if link.cardinality == "one_to_one":
        return False
    if link.cardinality == "one_to_many":
        return forward
    return not forward  # many_to_one walked backwards is one_to_many
