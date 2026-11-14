"""Scope combination semantics (DEC-03) — pure unit tests, no database."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tela_api.identity.scope import Predicate, ScopePolicyIn, combine_policies
from tela_common.errors import ErrorCode, TelaError

CID = "0192f3b2-0000-7000-8000-0000000000c1"


def _pol(role: str, entity: str, preds: list[dict], all_rows: bool = False, desc: str = "d") -> dict:
    return {"role_key": role, "entity_key": entity, "all_rows": all_rows, "predicate": preds, "description": desc}


def test_union_of_policies_and_attribute_substitution() -> None:
    scope = combine_policies(
        connection_id=CID,
        policies=[
            _pol("cio", "projects", [{"field": "cio", "op": "=", "attr": "cio"}], desc="CIO: Nomsa Osei"),
            _pol("delivery_manager", "projects", [{"field": "region", "op": "in", "values": ["KSA"]}],
                 desc="Region: KSA"),
        ],
        field_rules=[],
        scope_attributes={"cio": {"cio": ["Nomsa Osei"]}},
    )
    projects = scope.for_entity("projects")
    assert not projects.all_rows
    assert len(projects.alternatives) == 2  # OR of the two policies
    first = projects.alternatives[0][0]
    assert (first.field, first.op, first.values) == ("cio", "=", ("Nomsa Osei",))
    assert scope.summary == "projects: CIO: Nomsa Osei or Region: KSA"


def test_summary_counts_all_rows_entities_instead_of_listing_them() -> None:
    def summary(*pols: dict) -> str:
        return combine_policies(connection_id=CID, policies=list(pols), field_rules=[], scope_attributes={}).summary

    open_ = [_pol("viewer", e, [], all_rows=True) for e in ("account", "risks", "team")]
    assert summary(*open_) == "All rows (3 tables)"
    assert summary(open_[0]) == "account: all rows"
    region = _pol("viewer", "projects", [{"field": "region", "op": "in", "values": ["KSA"]}], desc="Region: KSA")
    assert summary(region, *open_) == "projects: Region: KSA; all rows on 3 other tables"
    assert summary(region, open_[0]) == "projects: Region: KSA; account: all rows"


def test_multi_value_attribute_becomes_in() -> None:
    scope = combine_policies(
        connection_id=CID,
        policies=[_pol("cio", "projects", [{"field": "cio", "op": "=", "attr": "cio"}])],
        field_rules=[],
        scope_attributes={"cio": {"cio": ["A", "B"]}},
    )
    cond = scope.for_entity("projects").alternatives[0][0]
    assert cond.op == "in" and cond.values == ("A", "B")


def test_missing_attribute_fails_closed() -> None:
    with pytest.raises(TelaError) as exc:
        combine_policies(
            connection_id=CID,
            policies=[_pol("cio", "projects", [{"field": "cio", "op": "=", "attr": "cio"}])],
            field_rules=[],
            scope_attributes={},
        )
    assert exc.value.code == ErrorCode.SCOPE_UNRESOLVED


def test_entity_without_policy_is_denied() -> None:
    scope = combine_policies(connection_id=CID, policies=[], field_rules=[], scope_attributes={})
    with pytest.raises(TelaError) as exc:
        scope.for_entity("projects")
    assert exc.value.code == ErrorCode.SCOPE_UNRESOLVED


def test_all_rows_union_wins() -> None:
    scope = combine_policies(
        connection_id=CID,
        policies=[
            _pol("viewer", "projects", [{"field": "region", "op": "in", "values": ["KSA"]}]),
            _pol("admin", "projects", [], all_rows=True),
        ],
        field_rules=[],
        scope_attributes={},
    )
    projects = scope.for_entity("projects")
    assert projects.all_rows and projects.alternatives == []


def test_hidden_fields_most_restrictive_and_hide_beats_mask() -> None:
    scope = combine_policies(
        connection_id=CID,
        policies=[],
        field_rules=[
            {"model_field_key": "projects.cost_total_usd", "effect": "mask"},
            {"model_field_key": "projects.cost_total_usd", "effect": "hide"},
            {"model_field_key": "projects.delivery_manager", "effect": "mask"},
        ],
        scope_attributes={},
    )
    assert scope.hidden_fields == {"projects.cost_total_usd"}
    assert scope.masked_fields == {"projects.delivery_manager"}


def test_scope_hash_differs_between_scopes_and_is_stable() -> None:
    def make(region: str) -> str:
        return combine_policies(
            connection_id=CID,
            policies=[_pol("viewer", "projects", [{"field": "region", "op": "in", "values": [region]}])],
            field_rules=[], scope_attributes={},
        ).scope_hash

    assert make("KSA") == make("KSA")
    assert make("KSA") != make("GCC - UAE")


@pytest.mark.parametrize(
    "raw",
    [
        {"field": "region; DROP TABLE x", "op": "=", "values": ["a"]},
        {"field": "region", "op": "like", "values": ["a"]},
        {"field": "region", "op": "=", "values": ["a", "b"]},
        {"field": "region", "op": "in", "values": []},
        {"field": "region", "op": "in"},
        {"field": "region", "op": "in", "values": ["a"], "attr": "cio"},
        {"field": "region", "op": "is null", "values": ["a"]},
    ],
)
def test_predicate_validation_rejects_bad_shapes(raw: dict) -> None:
    with pytest.raises(ValidationError):
        Predicate.model_validate(raw)


def test_policy_requires_conditions_or_explicit_all_rows() -> None:
    with pytest.raises(ValidationError):
        ScopePolicyIn(connection_id=CID, entity="projects", role="viewer", description="x")
    with pytest.raises(ValidationError):
        ScopePolicyIn(connection_id=CID, entity="projects", role="viewer", description="x", all_rows=True,
                      predicates=[Predicate(field="region", op="=", values=["KSA"])])
    ok = ScopePolicyIn(connection_id=CID, entity="projects", role="viewer", description="x", all_rows=True)
    assert ok.all_rows
