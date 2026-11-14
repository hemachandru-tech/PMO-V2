"""Knowledge editors of a pack (ENH-025, US-233): each kind's checks and references, no database.

The PMO pack's own documents pass with no errors and every item names what it refers to; a rule naming a measure the
pack lacks is flagged at its path and the document still has no errors (it is saved with the warning); a schema
error lands on its item.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from tela_api.packs.studio.knowledge import KNOWLEDGE_KINDS, LISTS, check_document, empty, etag, item_refs

PACK = Path(__file__).resolve().parents[3] / "packs" / "pmo"


@pytest.fixture(scope="module")
def head() -> dict:
    return yaml.safe_load((PACK / "manifest.yaml").read_text())


def _doc(kind: str) -> dict:
    return yaml.safe_load((PACK / f"{kind}.yaml").read_text())


@pytest.mark.parametrize("kind", KNOWLEDGE_KINDS)
def test_pmo_documents_pass_and_round_trip_as_json(kind: str, head: dict) -> None:
    body = _doc(kind)
    out = check_document(kind, body, head)
    assert out["errors"] == [] and out["valid"]
    assert len(out["items"]) == len(body[LISTS[kind]]) > 0
    # What the API stores and returns (json keeps the key order) checks the same and keeps its revision.
    again = json.loads(json.dumps(body, default=str))
    assert check_document(kind, again, head)["errors"] == [] and etag(again) == etag(json.loads(json.dumps(again)))


def test_a_rule_naming_a_missing_measure_is_flagged(head: dict) -> None:
    body = copy.deepcopy(_doc("dashboard_rules"))
    body["rules"][0]["measures"] = ["forecast_cost_total", "actual_spend"]
    out = check_document("dashboard_rules", body, head)
    assert out["errors"] == []
    assert {"path": "dashboard_rules.rules[0].measures[1]",
            "message": "There is no measure 'actual_spend' in this pack."} in out["warnings"]
    item = out["items"][0]
    assert item["refs"]["measures"] == ["forecast_cost_total", "actual_spend"]
    assert item["missing"] == ["measure:actual_spend"] and item["warnings"][0]["path"].endswith("measures[1]")
    assert all(not i["missing"] for i in out["items"][1:])


def test_references_of_every_kind(head: dict) -> None:
    assert item_refs("glossary", {"term": "EAC", "fields": ["projects.cost_total_usd"], "reading": "x"}) == [
        ("field", "projects.cost_total_usd", "fields[0]"), ("measure", "x", "reading")]
    assert item_refs("value_synonyms", {"dimensions": ["region"], "value": "KSA"}) == [
        ("dimension", "region", "dimensions[0]")]
    assert item_refs("chart_patterns", {"when": {"measure": ["a"], "breakdown": ["b"]},
                                        "suggest": {"ops": [{"op": "add_measure", "ref": "measure:c"}]}}) == [
        ("measure", "a", "when.measure[0]"), ("dimension", "b", "when.breakdown[0]"),
        ("measure", "c", "suggest.ops[0].ref")]
    assert item_refs("analysis_recipes", {"slots": {"measure": {"kind": "measure", "options": ["m"]},
                                                    "grain": {"kind": "grain", "options": ["month"]}}}) == [
        ("measure", "m", "slots.measure.options[0]")]
    assert item_refs("gold_examples", {"spec": {"measure": {"ref": "measure:open_projects"},
                                                "breakdowns": [{"ref": "dimension:region"}]}}) == [
        ("measure", "open_projects", "spec.measure.ref"), ("dimension", "region", "spec.breakdowns[0].ref")]
    glossary = check_document("glossary", {**empty("glossary"), "terms": [
        {"term": "Zed", "means": "zed", "fields": ["projects.nope", "ghosts.name"]}]}, head)
    assert [w["message"] for w in glossary["warnings"]] == [
        "projects has no field 'nope'.", "There is no entity 'ghosts' in this pack (write fields as entity.field)."]


def test_schema_errors_land_on_their_item(head: dict) -> None:
    body = {**empty("glossary"), "terms": [{"term": "OK", "means": "fine"}, {"term": "Bad", "means": ""}]}
    out = check_document("glossary", body, head)
    assert not out["valid"] and out["items"][0]["errors"] == []
    assert [e["path"] for e in out["items"][1]["errors"]] == ["glossary.terms[1].means"]
    gold = check_document("gold_examples", {**empty("gold_examples"), "examples": [{"question": "Hi"}]}, head)
    assert gold["items"][0]["errors"][0]["path"] == "gold_examples.examples[0].spec"
    no_version = check_document("value_synonyms", {"values": []}, head)
    assert no_version["document_errors"][0]["path"] == "value_synonyms.version"
    for kind in KNOWLEDGE_KINDS:
        assert check_document(kind, empty(kind), head)["errors"] == []
