"""AG-10 sees only the tables a question points to (EXEC-146 follow-up: the whole Dataverse catalogue, over 64,000
characters, made a local model time out)."""

from __future__ import annotations

from typing import Any

from tela_api.content import sql_context
from tela_api.content.sql_context import relevant_tables, schema_context
from tela_query_spec.model import ModelSnapshot


def field(key: str, label: str | None = None, data_type: str = "text") -> dict[str, Any]:
    return {"key": key, "label": label or key.replace("_", " ").title(), "data_type": data_type, "source_column": key}


def snapshot() -> ModelSnapshot:
    wide = [field("id", data_type="integer"), field("region_id", data_type="integer"), field("state"),
            field("go_live", "Go-live date", "date")] + [field(f"custom_{i:03}") for i in range(200)]
    noise = [{"key": f"noise_{n}", "label": f"Audit trail {n}", "source_object": f"dbo.noise_{n}",
              "primary_key": ["id"], "fields": [field("id", data_type="integer")] + [field(f"col_{i}") for i in range(60)]}
             for n in range(30)]
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c1",
        "entities": [
            {"key": "projects", "label": "Projects", "source_object": "dbo.tpg_project", "primary_key": ["id"],
             "fields": wide},
            {"key": "regions", "label": "Regions", "source_object": "dbo.tpg_region", "primary_key": ["id"],
             "fields": [field("id", data_type="integer"), field("name", "Region name")]},
            {"key": "risks", "label": "Risks", "source_object": "dbo.tpg_risk", "primary_key": ["id"],
             "fields": [field("id", data_type="integer"), field("project_id", data_type="integer"), field("severity")]},
            *noise,
        ],
        "links": [{"from_entity": "projects", "to_entity": "regions", "from_fields": ["region_id"], "to_fields": ["id"],
                   "cardinality": "many_to_one"},
                  {"from_entity": "risks", "to_entity": "projects", "from_fields": ["project_id"], "to_fields": ["id"],
                   "cardinality": "many_to_one"}],
        "measures": [{"key": "open_projects", "label": "Open projects", "entity": "projects", "plain_rule": "Active.",
                      "expression": {"agg": "count", "where": {"field": "state", "op": "=", "value": "Active"}}}],
        "dimensions": [{"key": "region", "label": "Region", "entity": "regions", "field": "name"}],
    })


def test_only_the_tables_the_question_points_to_and_their_joins() -> None:
    s = snapshot()
    assert set(relevant_tables(s, "Open projects by region")) == {"projects", "regions"}
    # The region breakdown lives on regions: kept, and projects joins risks to it.
    assert set(relevant_tables(s, "high severity risks per region")) == {"risks", "regions", "projects"}
    assert relevant_tables(s, "risks by severity")[0] == "risks" and "noise_0" not in relevant_tables(s, "risks")


def test_the_context_is_short_keys_and_named_columns_first_and_says_what_was_left_out() -> None:
    s = snapshot()
    full = sum(len(", ".join(f.source_column for f in e.fields)) for e in s.entities)
    out = schema_context(s, frozenset(), "Open projects by region")
    assert len(out) < sql_context.BUDGET and len(out) * 5 < full
    project = next(line for line in out.splitlines() if line.startswith("- dbo.tpg_project:"))
    cols = project.split(": ", 1)[1]
    assert cols.startswith("id (integer, Id), region_id (integer, Region Id), state (text, State)")
    assert "(+164 more columns not listed)" in project  # 204 columns, 40 listed
    assert "- link: projects.['region_id'] -> regions.['id']" in out
    assert "noise_" not in out and "(31 other tables are not listed: they don't match the question.)" in out


def test_hidden_fields_stay_out_and_a_vague_question_gets_the_measured_tables() -> None:
    s = snapshot()
    out = schema_context(s, frozenset({"projects.state"}), "Open projects")
    assert "state (text" not in out
    assert relevant_tables(s, "show me something") == ["projects"]


def dataverse() -> ModelSnapshot:
    """Run 01a1ce59's table in small: code/text twins, roll-up helpers and 250 columns of noise first."""
    cols = [field("tpg_projectid"), *(field(f"tpg_custom{i:03}") for i in range(250)),
            field("tpg_totalactual", data_type="decimal"), field("tpg_totalactual_state", data_type="integer"),
            field("tpg_totalactual_date", data_type="datetime"),
            field("tpg_overallkpi", data_type="integer"), field("tpg_overallkpiname"),
            field("tpg_projectstatus", data_type="integer"), field("tpg_projectstatusname"),
            field("tpgmea_region", data_type="integer"), field("tpgmea_regionname"), field("tpgmea_regionyominame")]
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c1",
        "entities": [{"key": "tpg_project", "label": "Tpg project", "source_object": "dbo.tpg_project",
                      "primary_key": ["tpg_projectid"], "fields": cols}]})


def test_dataverse_text_twins_and_run_together_names_reach_the_model_and_rollup_helpers_dont() -> None:
    out = schema_context(dataverse(), frozenset(), "For open projects (current state Active or On Hold) with an "
                         "overall KPI colour, count projects for every region and colour pair, as a heatmap.")
    cols = next(ln for ln in out.splitlines() if ln.startswith("- dbo.tpg_project:")).split(": ", 1)[1]
    listed = [c.split(" (", 1)[0] for c in cols.split("), ")]
    for name in ("tpg_overallkpiname", "tpg_projectstatusname", "tpgmea_regionname"):
        assert name in listed[:5], listed[:8]  # "kpi" inside overallkpi, state ~ status, region
    assert "tpg_overallkpiname (text, Tpg Overallkpiname; text of tpg_overallkpi)" in cols
    # the code and yomi twins are covered by the text column; the roll-up helper is not in the first 40
    assert not {"tpg_overallkpi", "tpgmea_region", "tpgmea_regionyominame"} & set(listed)
    assert "tpg_totalactual_state" not in listed


def test_the_dialect_and_the_certified_rules_of_the_listed_tables_come_with_the_tables() -> None:
    """EXEC-148: a draft uses the agreed conditions ("Active") instead of guessing ("Open")."""
    out = schema_context(snapshot(), frozenset(), "Open projects by region", dialect="tsql")
    assert out.startswith("SQL dialect: tsql\n\nTables:\n")
    assert out.endswith("Certified measures (their rules):\n- Open projects (projects): Active.")
