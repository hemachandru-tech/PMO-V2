"""The AI pack author's rules, checks and value tokens (US-237), without a database."""

from __future__ import annotations

from typing import Any

from tela_api.packs.studio import author_checks as checks
from tela_api.packs.studio import author_rules as rules
from tela_api.packs.studio.author_llm import Masker, examples
from tela_query_spec.model import Dimension, Measure

CATALOG = [
    {"qualified_name": "public.projects", "keys": {"primary": ["project_id"]},
     "columns": [{"name": "project_id", "type": "varchar"}, {"name": "status", "type": "varchar"},
                 {"name": "region", "type": "varchar"}, {"name": "cio", "type": "varchar"},
                 {"name": "budget_usd", "type": "numeric"}, {"name": "planned_end_date", "type": "date"},
                 {"name": "created_on", "type": "timestamp"}, {"name": "effort_hours", "type": "integer"}]},
    {"qualified_name": "public.risks", "keys": {"primary": ["risk_id"]},
     "columns": [{"name": "risk_id", "type": "varchar"}, {"name": "project_id", "type": "varchar"},
                 {"name": "hold_reason", "type": "varchar"}]},
]
PROFILES: dict[str, Any] = {
    "projects.status": [{"value": "Active", "rows": 65}, {"value": "On Hold", "rows": 5},
                        {"value": "Completed", "rows": 30}],
    "projects.region": [{"value": v, "rows": 10} for v in ("KSA", "UAE", "Jordan")],
    "risks.hold_reason": [{"value": "Funding", "rows": 3}, {"value": "Vendor", "rows": 2}],
}


def _head() -> dict[str, Any]:
    ents = [p["item"] for p in rules.entity_proposals(CATALOG, set())]
    return checks.with_items({"entities": [], "links": [], "measures": [], "dimensions": [], "signature": {}},
                             "entity", ents)


def test_entities_are_pack_items_with_candidates_and_personal_tags() -> None:
    head = _head()
    proj = head["entities"][0]
    assert proj["object_candidates"] == ["projects"] and proj["primary_key"] == ["project_id"]
    fields = {f["key"]: f for f in proj["fields"]}
    assert fields["project_id"]["required"] is True and fields["cio"]["sensitivity"] == "personal"
    assert head["signature"]["primary_entity"] == "projects"  # the widest table
    _item, report = checks.check("entity", proj, {"entities": []}, {}, CATALOG, {})
    assert report.errors == []
    ghost = {**proj, "key": "ghost", "fields": [{**proj["fields"][0], "columns": ["nope"]}]}
    _, report = checks.check("entity", ghost, {"entities": []}, {}, CATALOG, {})
    assert report.errors[0]["message"] == "public.projects has no column 'nope'."


def test_links_by_name_when_there_is_no_foreign_key() -> None:
    [link] = rules.link_proposals(_head(), CATALOG)
    assert link["item"] == {"from_entity": "risks", "to_entity": "projects", "from_fields": ["project_id"],
                            "to_fields": ["project_id"], "cardinality": "many_to_one"} and link["origin"] == "name"


def test_measures_use_profile_values_only_and_validate() -> None:
    head = _head()
    proj = head["entities"][0]
    drafted = rules.measure_proposals(proj, PROFILES, set())
    keys = [d["item"]["key"] for d in drafted]
    assert len(keys) <= rules.BATCH and keys[0] == "projects_count"
    assert {"projects_active", "projects_on_hold", "open_projects", "projects_budget_usd_total",
            "projects_planned_end_date_overdue", "projects_created_on_age", "projects_effort_hours_total"} <= set(keys)
    assert "projects_cio_total" not in keys
    open_ = next(d for d in drafted if d["item"]["key"] == "open_projects")["item"]
    assert open_["expression"]["where"] == {"field": "status", "op": "not_in", "values": ["Completed"]}
    for d in drafted:
        Measure.model_validate(d["item"])
        _, report = checks.check("measure", d["item"], head, {}, CATALOG, PROFILES)
        assert report.errors == [], (d["item"]["key"], report.errors)


def test_filter_values_are_snapped_or_refused() -> None:
    head = _head()

    def measure(value: Any, op: str = "=") -> dict[str, Any]:
        cond = {"field": "status", "op": op, **({"values": value} if op in ("in", "not_in") else {"value": value})}
        return {"key": "x", "label": "X", "entity": "projects", "plain_rule": "X.",
                "expression": {"agg": "count", "where": cond}}

    item, report = checks.check("measure", measure("on hold"), head, {}, CATALOG, PROFILES)
    assert item["expression"]["where"]["value"] == "On Hold" and report.errors == []
    assert report.warnings[0]["message"] == "'on hold' is written 'On Hold' in the data."
    _, report = checks.check("measure", measure(["Active", "Paused"], "in"), head, {}, CATALOG, PROFILES)
    assert report.errors == [{"path": "expression.where.values[1]",
                              "message": "'Paused' is not a value of Status: it has Active, On Hold, Completed."}]
    _, report = checks.check("measure", measure("Active"), head, {}, CATALOG, {})
    assert "Tela has not read the values of Status" in report.errors[0]["message"]
    free = {"projects.status": [{"value": str(i), "rows": 1} for i in range(60)]}
    _, report = checks.check("measure", measure("Anything"), head, {}, CATALOG, free)
    assert report.errors == [] and "free text" in report.warnings[0]["message"]


def test_dimensions_bands_and_answers() -> None:
    head = _head()
    drafted = rules.dimension_proposals(head["entities"][0], PROFILES, set())
    by_key = {d["item"]["key"]: d["item"] for d in drafted}
    assert by_key["status"]["is_status"] is True and "cio" not in by_key
    assert by_key["created_on_aging"]["answers"] == ["how_long"]
    assert by_key["planned_end_date_lateness"]["answers"] == ["how_late"]
    assert by_key["planned_end_date_horizon"]["answers"] == ["when"]
    for item in drafted:
        Dimension.model_validate(item["item"])
        assert checks.check("dimension", item["item"], head, {}, CATALOG, PROFILES)[1].errors == []
    risks = rules.dimension_proposals(head["entities"][1], PROFILES, set(by_key))
    assert risks[0]["item"]["answers"] == ["why"]


def test_knowledge_names_only_existing_items_and_dedupes() -> None:
    head = _head()
    head["measures"] = [{"key": "open_projects", "label": "Open projects", "entity": "projects",
                         "plain_rule": "Open ones.", "expression": {"agg": "count"}}]
    head["dimensions"] = [{"key": "region", "label": "Region", "entity": "projects", "field": "region"}]
    [term] = rules.glossary_proposals(head, set())
    assert term["item"]["term"] == "Open projects" and term["item"]["reading"] == "open_projects"
    assert checks.check("glossary", term["item"], head, {}, CATALOG, PROFILES)[1].errors == []
    docs = {"glossary": {"version": "0.1.0", "terms": [term["item"]]}}
    assert checks.check("glossary", term["item"], head, docs, CATALOG, PROFILES)[1].errors[0]["message"] == (
        "The pack's document already has this item.")
    rule = {"id": "no_spend", "title": "Never spend", "rule": "Say forecast.", "measures": ["spend_total"]}
    assert checks.check("dashboard_rules", rule, head, {}, CATALOG, PROFILES)[1].errors[0]["message"] == (
        "There is no measure 'spend_total' in this pack.")
    value = {"dimensions": ["region"], "value": "ksa", "synonyms": ["Saudi"]}
    item, report = checks.check("value_synonyms", value, head, {}, CATALOG, PROFILES)
    assert item["value"] == "KSA" and report.errors == []
    doc = checks.with_knowledge(None, "dashboard_rules", [{**rule, "measures": []},
                                                          {"words": ["spend"], "use": "forecast"}])
    assert doc["rules"][0]["id"] == "no_spend" and doc["wording"]["forbidden"] == [{"words": ["spend"],
                                                                                    "use": "forecast"}]


def test_synonyms_merge_without_repeats() -> None:
    head = {"measures": [{"key": "m", "label": "Number of projects", "synonyms": []}]}
    [s] = rules.synonym_proposals(head)
    assert s["item"]["synonyms"] == ["how many projects", "count of projects"]
    merged = checks.with_items(head, "synonyms", [{**s["item"], "synonyms": ["How many projects", "project count"]}])
    assert merged["measures"][0]["synonyms"] == ["How many projects", "project count"]


def test_masker_hides_values_and_puts_them_back() -> None:
    m = Masker({"status": ["Active", "On Hold"]})
    item = {"key": "projects_on_hold", "label": "On Hold projects",
            "expression": {"where": {"field": "status", "op": "=", "value": "On Hold"}}}
    masked = m.mask(item)
    assert "On Hold" not in str(masked) and "on_hold" not in str(masked)
    assert masked["expression"]["where"]["value"] == "[[status:2]]"
    assert m.unmask(masked) == item
    assert m.unmask({"value": "[[status:9]]"}) == {"value": "[[status:9]]"}  # unknown: fails the value check


def test_template_examples_pick_shapes() -> None:
    template = {"measures": [
        {"key": "a", "label": "A", "entity": "p", "plain_rule": "", "expression": {"agg": "count", "where": {}}},
        {"key": "b", "label": "B", "entity": "p", "plain_rule": "", "expression": {"agg": "sum", "field": "x"}},
        {"key": "c", "label": "C", "entity": "p", "plain_rule": "",
         "expression": {"agg": "avg", "value": {"calculator": "h", "output": "s"}}}]}
    assert [e["key"] for e in examples("measure", template, None)] == ["a", "b"]  # no calculator measures
