"""BUG-030: AG-04 prompts stay small on a Dataverse-sized model, and vague dimension requests stay with the rules."""

from __future__ import annotations

from typing import Any

from tela_api.model.assistant import PROMPT_BUDGET, _catalog_text, _draft_text, parse_correction

SYSTEM = ["createdby", "createdbyname", "createdbyyominame", "createdonbehalfby", "modifiedby", "modifiedbyyominame",
          "owningbusinessunit", "owningteam", "owninguser", "ownerid", "versionnumber", "importsequencenumber",
          "timezoneruleversionnumber", "utcconversiontimezonecode", "exchangerate", "transactioncurrencyid",
          "tpg_budget_base", *(f"address1_{p}" for p in ("city", "line1", "fax", "postalcode"))]


def field(key: str, dtype: str = "text") -> dict[str, Any]:
    return {"key": key, "label": key.replace("_", " ").capitalize(), "data_type": dtype, "source_column": key}


def entity(key: str, extra: int = 0) -> dict[str, Any]:
    fields = [field(f"{key}id"), field("createdon", "datetime"), field("tpg_status"), field("tpg_deptname"),
              field("tpg_budget", "decimal"), *map(field, SYSTEM), *(field(f"tpg_custom{i}") for i in range(extra))]
    return {"key": key, "label": key.replace("tpg_", "").capitalize(), "source_object": f"dbo.{key}",
            "primary_key": [f"{key}id"], "fields": fields}


def big_draft() -> dict[str, Any]:
    return {"entities": [entity(f"tpg_table{i}", extra=120) for i in range(10)] + [entity("tpg_project", extra=5)],
            "measures": [], "dimensions": []}


def test_draft_text_drops_system_columns_and_fits_the_budget() -> None:
    d = big_draft()
    full = sum(len(f["key"]) + len(f["label"]) + 12 for e in d["entities"] for f in e["fields"])
    assert full > 3 * PROMPT_BUDGET  # the unbounded text was the 110 s timeout
    out = _draft_text(d, "Add more dimensions to projects")
    assert len(out) < PROMPT_BUDGET + 2_000
    assert "yominame" not in out and "owningteam" not in out and "address1_" not in out and "_base" not in out
    assert out.splitlines()[1].startswith("tpg_project: ")  # the named table comes first
    assert "Not shown (ask about one by name): " in out and "tpg_table9 (" in out
    assert "createdon (" in out  # created/modified dates stay: a fair time axis


def test_draft_text_keeps_used_and_named_system_fields() -> None:
    d = {"entities": [entity("tpg_project")], "measures": [],
         "dimensions": [{"key": "owner", "label": "Owner", "entity": "tpg_project", "field": "owninguser"}]}
    out = _draft_text(d, "use versionnumber")
    assert "owninguser (" in out and "versionnumber (" in out and "owningteam" not in out


def test_catalog_text_keeps_keys_and_fits() -> None:
    cols = [{"name": n, "type": "text"} for n in ["tpg_projectid", "owninguser", "tpg_name", *SYSTEM]]
    objects = [{"qualified_name": f"dbo.t{i}", "columns": cols * 30,
                "keys": {"primary": ["tpg_projectid"], "foreign": [{"columns": ["owninguser"], "ref_object": "u"}]}}
               for i in range(40)]
    out = _catalog_text(objects)
    assert len(out) < PROMPT_BUDGET + 1_000 and "owninguser text" in out and "versionnumber" not in out
    assert "Not shown" in out


def test_add_more_dimensions_is_read_by_the_rules() -> None:
    d = big_draft()
    ops, clarify, unparsed = parse_correction(d, "Add more dimensions")
    assert unparsed == [] and clarify == [] and ops
    assert {o.op for o in ops} == {"add_dimension"}
    assert not any(o.field in SYSTEM for o in ops)
    ops, _, unparsed = parse_correction(d, "add more dimensions to projects")
    assert unparsed == [] and {o.entity for o in ops} == {"tpg_project"}
    assert {o.field for o in ops} >= {"createdon", "tpg_status", "tpg_deptname"}
    ops, _, unparsed = parse_correction(d, "suggest measures for project")
    assert unparsed == [] and [o.op for o in ops] == ["add_measure"] and ops[0].field == "tpg_budget"
    _, _, unparsed = parse_correction(d, "add more dimensions to invoices")
    assert unparsed == ["add more dimensions to invoices"]  # no such table: the LLM gets it (small prompt)


def test_add_dimension_by_name() -> None:
    d = {"entities": [entity("tpg_project"), entity("tpg_program")], "measures": [], "dimensions": []}
    ops, clarify, _ = parse_correction(d, "add dimension tpg_deptname")
    assert ops == [] and len(clarify[0]["options"]) == 2  # on both tables: ask
    ops, clarify, _ = parse_correction(d, "add dimension tpg_project.tpg_deptname")
    assert clarify == [] and [(o.entity, o.field) for o in ops] == [("tpg_project", "tpg_deptname")]
    ops, _, _ = parse_correction(d, "add tpg_project.tpg_status as a dimension")
    assert ops[0].is_status and ops[0].key == "tpg_project_tpg_status"


def test_dataverse_lookup_id_gives_way_to_its_name_column() -> None:
    e = entity("tpg_project")
    e["fields"] += [field("tpg_portfolio"), field("tpg_portfolioname")]
    ops, _, _ = parse_correction({"entities": [e], "measures": [], "dimensions": []}, "add more dimensions")
    fields = [o.field for o in ops]
    assert "tpg_portfolioname" in fields and "tpg_portfolio" not in fields
    assert fields.index("tpg_status") < fields.index("createdon")  # status and groupings before dates
