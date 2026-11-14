"""Value-based mapping suggestions (US-200; FEAT-067): columns found by their values, no database."""

from __future__ import annotations

from typing import Any

import pytest
from pmo_oracle import open_by_status

from tela_api.packs.manifest import PackManifest, discover, load_manifest, match
from tela_api.packs.mapping import (
    PackMapping,
    candidate_columns,
    candidate_definition,
    step,
    suggestions,
    value_fields,
    value_fit,
    wanted_suggestions,
)

_TYPES = {"text": "varchar", "integer": "int", "decimal": "decimal(12,2)", "date": "date", "datetime": "datetime",
          "boolean": "tinyint(1)"}


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    """The PMO pack with open = Status Active, as 2.1.0 had RULE-026 (DEC-24): Status is filtered by its values."""
    pack = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    return PackManifest.model_validate(open_by_status(pack.model_dump()))


def _source(manifest: PackManifest, *, rename: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every pack name as is (the fixture source), with project columns renamed."""
    out = []
    for e in manifest.entities:
        cols = [{"name": f.columns[0], "type": _TYPES[f.data_type]} for f in e.fields]
        if e.key == "projects":
            cols = [{**c, "name": (rename or {}).get(c["name"], c["name"])} for c in cols]
        out.append({"qualified_name": f"tpg.{e.object_candidates[0]}", "columns": cols})
    return out


def _profile(*values: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"value": v, "rows": n} for v, n in values]


CURRENT_STATE = _profile(("Active", 279), ("Completed", 120), ("On Hold", 79), ("Project Created", 73))
RECORD_STATUS = _profile(("Active", 420), ("Inactive", 131))
RAG = _profile(("Green", 300), ("Amber", 100), ("Red", 51))


def _columns(manifest: PackManifest, catalog: list[dict[str, Any]], r: Any, wanted: dict[str, str],
             values: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, str], list[dict[str, Any]] | None]:
    """The candidate columns as the preview reads them, with ``values`` by column name (others: a code column)."""
    cols = candidate_columns(manifest, catalog, r, wanted)
    _, refs = candidate_definition(manifest, r, cols)
    return {pair: values.get(pair[1], _profile(*((f"X{i}", 1) for i in range(60)))) for pair in refs.values()}


def test_value_fit_scores_overlap_and_purity() -> None:
    assert value_fit(["Active", "Inactive"], RECORD_STATUS)["score"] == 1.0
    cs = value_fit(["Active", "Inactive"], CURRENT_STATE)
    assert cs["coverage"] == 0.5 and cs["score"] < 0.4 and cs["others"] == ["Completed", "On Hold", "Project Created"]
    assert value_fit(["active", "INACTIVE"], RECORD_STATUS)["score"] == 1.0  # case aside
    assert value_fit(["Active"], _profile(*((f"v{i}", 1) for i in range(50)))) is None  # free text
    assert value_fit(["Active"], None) is None
    # A source value the Admin value-mapped counts as the pack's value.
    assert value_fit(["Active", "Inactive"], _profile(("A", 3), ("I", 1)), {"A": "Active", "I": "Inactive"})[
        "score"] == 1.0


def test_one_value_fields_are_not_looked_for(manifest: PackManifest) -> None:
    fields = value_fields(manifest)
    assert fields["projects.status"] == ["Active", "Inactive"]
    assert "projects.key_priority_project" not in fields  # only "Yes": too many columns hold it
    assert "projects.budget_code" not in fields  # only the blank


def test_tpg_mysql_status_suggests_record_status_not_current_state(manifest: PackManifest) -> None:
    catalog = _source(manifest, rename={"status": "record_status"})
    r = match(manifest, catalog)
    assert "status" not in r.entities["projects"].mapping  # no name rule finds record_status
    wanted = wanted_suggestions(manifest, r, PackMapping(), {"projects.current_state": CURRENT_STATE})
    assert wanted == {"projects.status": "unmapped"}
    cols = _columns(manifest, catalog, r, wanted, {"record_status": RECORD_STATUS, "current_state": CURRENT_STATE,
                                                   "schedule_kpi": RAG, "overall_kpi": RAG})
    assert ("projects", "current_state") not in cols  # Current state's column: one column, one pack field
    got = suggestions(manifest, r, PackMapping(), wanted, cols)
    s = got["projects.status"]
    assert s["column"] == "record_status" and s["confidence"] == 1.0 and s["reason"] == "unmapped"
    assert s["evidence"] == "Values Active, Inactive match the pack's Status."
    row = next(f for e in step(manifest, catalog, r, PackMapping(), suggested=got)["entities"]
               if e["key"] == "projects" for f in e["fields"] if f["key"] == "status")
    assert row["column"] is None and row["suggestion"]["column"] == "record_status"  # suggested, not applied


def test_exec115_hand_mapping_is_flagged_with_record_status(manifest: PackManifest) -> None:
    catalog = _source(manifest, rename={"status": "record_status"})
    wrong = PackMapping(fields={"projects.status": "current_state"})
    r = match(manifest, catalog, mapping=wrong)
    wanted = wanted_suggestions(manifest, r, wrong, {"projects.status": CURRENT_STATE})
    assert wanted["projects.status"] == "values_dont_fit"
    cols = _columns(manifest, catalog, r, wanted, {"record_status": RECORD_STATUS, "current_state": CURRENT_STATE})
    s = suggestions(manifest, r, wrong, wanted, cols)["projects.status"]
    assert s["column"] == "record_status" and s["reason"] == "values_dont_fit"


def test_a_column_that_fits_is_not_questioned(manifest: PackManifest) -> None:
    catalog = _source(manifest)  # the fixture source: every exact name
    r = match(manifest, catalog)
    profiles = {"projects.status": RECORD_STATUS, "projects.current_state": CURRENT_STATE,
                "projects.schedule_kpi": RAG}
    wanted = wanted_suggestions(manifest, r, PackMapping(), profiles)
    assert not {"projects.status", "projects.current_state", "projects.schedule_kpi"} & set(wanted)
    assert wanted_suggestions(manifest, r, PackMapping(), {}) == wanted  # values not read: no mapped column is questioned


def test_not_in_source_and_weak_evidence_get_no_suggestion(manifest: PackManifest) -> None:
    catalog = _source(manifest, rename={"status": "record_status"})
    none = PackMapping(fields={"projects.status": None})
    r = match(manifest, catalog, mapping=none)
    assert "projects.status" not in wanted_suggestions(manifest, r, none, {})
    r = match(manifest, catalog)
    wanted = {"projects.status": "unmapped"}
    weak = _columns(manifest, catalog, r, wanted, {"record_status": _profile(("Active", 9), ("Archived", 9))})
    assert suggestions(manifest, r, PackMapping(), wanted, weak) == {}  # one value of two: not enough


def test_two_columns_holding_the_values_lower_the_confidence(manifest: PackManifest) -> None:
    catalog = _source(manifest, rename={"status": "record_status"})
    catalog = [{**c, "columns": [*c["columns"], {"name": "state_name", "type": "varchar"}]}
               if c["qualified_name"] == "tpg.projects" else c for c in catalog]
    r = match(manifest, catalog)
    wanted = {"projects.status": "unmapped"}
    cols = _columns(manifest, catalog, r, wanted, {"record_status": RECORD_STATUS, "state_name": RECORD_STATUS})
    s = suggestions(manifest, r, PackMapping(), wanted, cols)["projects.status"]
    assert s["column"] == "record_status" and s["confidence"] == 0.7 and s["alternatives"] == ["state_name"]
    assert "state_name holds them too" in s["evidence"]
