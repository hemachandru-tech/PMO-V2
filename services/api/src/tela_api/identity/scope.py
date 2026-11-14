"""Scope and field-policy resolution (FEAT-002, FEAT-003; RULE-004, RULE-005; DEC-03).

Semantics (working position, Q-003):

* A person's row scope for an entity is the **union** (OR) of the scope
  policies of all roles they hold. Each policy is an AND of predicates.
* No policy for a governed entity means **deny** (fail closed).
* An attribute-based predicate (``{"attr": "cio"}``) takes its values from the
  scope attributes on the person's group mappings for that role. A missing
  attribute makes scope unresolvable → the query is refused (MSG-008).
* Hidden fields: the most restrictive rule wins — a field hidden (or masked)
  for any of the person's roles is hidden (or masked). ``hide`` beats ``mask``.
* A model link marked ``inherits_scope`` (US-155) passes its parent's scope on:
  a child row (a project's risk) is visible only when its parent row is, ANDed
  with the child's own policies if it has any. A parent the person has no scope
  on denies the child too (fail closed).

The query engine (P1, US-020) turns a ResolvedScope into SQL / pipeline
predicates. This module never builds query text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.core.db import fetch_all, fetch_one
from tela_api.core.metrics import SCOPE_DENIALS
from tela_api.identity.principal import Principal
from tela_common.errors import ErrorCode, TelaError

SCOPE_UNRESOLVED_MESSAGE = "Could not apply your data rules to this query, so it was not run."
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")

Op = Literal["=", "in", "not in", "is null"]


class Predicate(BaseModel):
    """One structured condition. Values are literals or a reference to a scope attribute."""

    field: str
    op: Op
    values: list[str | int | float | bool] | None = None
    attr: str | None = None

    @field_validator("field")
    @classmethod
    def _field_ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError("field must be a plain identifier")
        return v

    @field_validator("attr")
    @classmethod
    def _attr_ident(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[a-z_][a-z0-9_]{0,63}$", v):
            raise ValueError("attr must be a lowercase identifier")
        return v

    @model_validator(mode="after")
    def _shape(self) -> Predicate:
        if self.op == "is null":
            if self.values or self.attr:
                raise ValueError("'is null' takes no values")
            return self
        if (self.values is None) == (self.attr is None):
            raise ValueError("give exactly one of 'values' or 'attr'")
        if self.values is not None:
            if not self.values:
                raise ValueError("values must not be empty")
            if self.op == "=" and len(self.values) != 1:
                raise ValueError("'=' takes exactly one value")
            if len(self.values) > 500:
                raise ValueError("too many values")
        return self


class ScopePolicyIn(BaseModel):
    connection_id: str
    entity: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=2, max_length=40)
    all_rows: bool = False
    predicates: list[Predicate] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=300)

    @field_validator("entity")
    @classmethod
    def _entity_ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError("entity must be a plain identifier")
        return v

    @model_validator(mode="after")
    def _all_rows(self) -> ScopePolicyIn:
        if self.all_rows and self.predicates:
            raise ValueError("'all rows' cannot be combined with conditions")
        if not self.all_rows and not self.predicates:
            raise ValueError("give at least one condition, or tick 'all rows' explicitly")
        return self


@dataclass(frozen=True)
class ResolvedCondition:
    """A fully resolved predicate: attribute references replaced by literal values."""

    field: str
    op: str
    values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ParentScope:
    """Rows are visible only when ``field`` names a ``parent`` row in scope (``parent_field``), US-155."""

    field: str
    parent: str
    parent_field: str


@dataclass
class EntityScope:
    all_rows: bool = False
    # OR of AND-groups; with ``via`` and no own policy it is empty (no condition of the entity's own)
    alternatives: list[tuple[ResolvedCondition, ...]] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    via: ParentScope | None = None


@dataclass
class ResolvedScope:
    connection_id: str
    entities: dict[str, EntityScope]
    hidden_fields: frozenset[str]
    masked_fields: frozenset[str]

    def for_entity(self, entity: str) -> EntityScope:
        scope = self.entities.get(entity)
        if scope is None:
            SCOPE_DENIALS.inc()
            raise TelaError(ErrorCode.SCOPE_UNRESOLVED, SCOPE_UNRESOLVED_MESSAGE, {"entity": entity})
        return scope

    @property
    def summary(self) -> str:
        """Restricted entities are named with their rules; two or more all-rows entities are counted, not listed,
        so a source with dozens of tables still reads as one short line."""
        open_ = sorted(e for e, s in self.entities.items() if s.all_rows)
        parts = [f"{entity}: " + " or ".join(dict.fromkeys(scope.descriptions))
                 for entity, scope in sorted(self.entities.items()) if not scope.all_rows]
        if len(open_) == 1:
            parts.append(f"{open_[0]}: all rows")
        elif open_ and parts:
            parts.append(f"all rows on {len(open_)} other tables")
        elif open_:
            parts.append(f"All rows ({len(open_)} tables)")
        return "; ".join(parts)

    @property
    def scope_hash(self) -> str:
        """Stable hash used in cache keys so scopes never share results (ADR-010, RISK-015)."""
        payload = {
            "c": self.connection_id,
            "e": {
                k: {"all": v.all_rows, "alt": sorted(json.dumps([c.__dict__ for c in alt], default=str)
                                                     for alt in v.alternatives),
                    "via": v.via.__dict__ if v.via else None}
                for k, v in sorted(self.entities.items())
            },
            "h": sorted(self.hidden_fields),
            "m": sorted(self.masked_fields),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def combine_policies(
    *,
    connection_id: str,
    policies: list[dict[str, Any]],
    field_rules: list[dict[str, Any]],
    scope_attributes: dict[str, dict[str, list[str]]],
    links: list[dict[str, Any]] | None = None,
) -> ResolvedScope:
    """Pure combination step (unit tested). ``policies`` rows carry role_key, entity_key,
    all_rows, predicate (list of dicts) and description; ``links`` are the model's links (those marked
    ``inherits_scope`` pass a parent's scope on to its child entity)."""
    entities: dict[str, EntityScope] = {}
    for pol in policies:
        role_attrs = scope_attributes.get(pol["role_key"], {})
        entity = entities.setdefault(pol["entity_key"], EntityScope())
        entity.descriptions.append(pol["description"])
        if pol["all_rows"]:
            entity.all_rows = True
            continue
        group: list[ResolvedCondition] = []
        for raw in pol["predicate"]:
            pred = Predicate.model_validate(raw)
            if pred.op == "is null":
                group.append(ResolvedCondition(pred.field, pred.op))
                continue
            if pred.attr is not None:
                values = role_attrs.get(pred.attr)
                if not values:
                    SCOPE_DENIALS.inc()
                    raise TelaError(
                        ErrorCode.SCOPE_UNRESOLVED, SCOPE_UNRESOLVED_MESSAGE,
                        {"entity": pol["entity_key"], "missing_attribute": pred.attr},
                    )
                op = "in" if pred.op == "=" and len(values) > 1 else pred.op
                group.append(ResolvedCondition(pred.field, op, tuple(values)))
            else:
                group.append(ResolvedCondition(pred.field, pred.op, tuple(pred.values or ())))
        entity.alternatives.append(tuple(group))

    for scope in entities.values():
        if scope.all_rows:
            scope.alternatives = []  # union with "all rows" is all rows
    inherit_scope(entities, links or [])

    hidden = {r["model_field_key"] for r in field_rules if r["effect"] == "hide"}
    masked = {r["model_field_key"] for r in field_rules if r["effect"] == "mask"} - hidden
    return ResolvedScope(connection_id, entities, frozenset(hidden), frozenset(masked))


def inherit_scope(entities: dict[str, EntityScope], links: list[dict[str, Any]]) -> None:
    """Apply ``inherits_scope`` links in place: each child follows its parent (a parent that itself follows a link
    is settled first, three levels at most); a child whose parent the person can't see is denied, and so is every
    entity of a cycle or of a chain that is too deep (fail closed)."""
    via = {ln["from_entity"]: ln for ln in links if ln.get("inherits_scope") and ln.get("accepted", True)
           and ln.get("cardinality") == "many_to_one" and len(ln["from_fields"]) == 1}
    state: dict[str, str] = {}

    def settle(child: str, depth: int) -> bool:
        """False when the chain from ``child`` loops or is too deep."""
        if state.get(child) == "done":
            return True
        if state.get(child) == "busy" or depth > 3:
            return False
        state[child] = "busy"
        ln = via[child]
        parent = ln["to_entity"]
        if parent in via and not settle(parent, depth + 1):
            return False
        ps, own = entities.get(parent), entities.get(child)
        desc = [*(own.descriptions if own else []), f"through its {parent.replace('_', ' ')}"]
        if ps is None:
            entities.pop(child, None)
        elif ps.all_rows and (own is None or own.all_rows):
            entities[child] = EntityScope(all_rows=True, descriptions=desc)
        elif ps.all_rows:
            entities[child] = EntityScope(alternatives=own.alternatives if own else [], descriptions=desc)
        else:
            entities[child] = EntityScope(
                alternatives=own.alternatives if own and not own.all_rows else [], descriptions=desc,
                via=ParentScope(ln["from_fields"][0], parent, ln["to_fields"][0]))
        state[child] = "done"
        return True

    for child in list(via):
        if not settle(child, 0):
            for name in [k for k, v in state.items() if v == "busy"]:
                entities.pop(name, None)
                state[name] = "done"


async def scope_links(conn: AsyncConnection, connection_id: str) -> list[dict[str, Any]]:
    """The links of the connection's current model version that pass scope on (US-155)."""
    row = await fetch_one(
        conn, "SELECT v.definition -> 'links' AS links FROM model.data_models m JOIN model.model_versions v "
              "ON v.id = m.current_version_id WHERE m.connection_id = CAST(:c AS uuid)", {"c": connection_id})
    return [ln for ln in (row["links"] or []) if ln.get("inherits_scope")] if row else []


async def resolve_scope(conn: AsyncConnection, principal: Principal, connection_id: str) -> ResolvedScope:
    """Load the person's policies for one connection and combine them. Admin is scoped too."""
    policies = await fetch_all(
        conn,
        """
        SELECT r.key AS role_key, p.entity_key, p.all_rows, p.predicate, p.description
        FROM iam.scope_policies p JOIN iam.roles r ON r.id = p.role_id
        WHERE p.connection_id = :cid AND p.is_active AND p.role_id = ANY(CAST(:roles AS uuid[]))
        ORDER BY p.created_at
        """,
        {"cid": connection_id, "roles": list(principal.role_ids)},
    )
    field_rules = await fetch_all(
        conn,
        "SELECT model_field_key, effect FROM iam.field_policies "
        "WHERE connection_id = :cid AND role_id = ANY(CAST(:roles AS uuid[]))",
        {"cid": connection_id, "roles": list(principal.role_ids)},
    )
    return combine_policies(
        connection_id=connection_id,
        policies=policies,
        field_rules=field_rules,
        scope_attributes=principal.scope_attributes,
        links=await scope_links(conn, connection_id),
    )
