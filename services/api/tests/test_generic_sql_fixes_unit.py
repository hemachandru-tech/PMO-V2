"""Generic Direct SQL fixes: awkward schema names, compound table words, small-model catalogue, dialect rules,
column values, precise repair feedback and the zero-total check. No names here come from any one customer: the
fixtures use made-up schemas with a hyphen, the case that broke drafts."""

from __future__ import annotations

from typing import Any

from sqlglot import exp, parse_one

from tela_api.ask import direct_sql
from tela_api.content import sql_context
from tela_api.content.sql_context import relevant_tables, schema_context, sql_object
from tela_api.query import custom_sql
from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.model import ModelSnapshot


def field(key: str, data_type: str = "text", **kw: Any) -> dict[str, Any]:
    return {"key": key, "label": key.replace("_", " ").title(), "data_type": data_type, "source_column": key, **kw}


def snap() -> ModelSnapshot:
    """A small model in a schema whose name needs quoting, with a run-together table name."""
    return ModelSnapshot.model_validate({
        "model_version_id": "v1", "connection_id": "c1", "engine": "mysql",
        "entities": [
            {"key": "runs", "label": "Runs", "source_object": "shop-data.runs", "primary_key": ["id"],
             "fields": [field("id", "integer"), field("state"), field("created_at", "datetime")]},
            {"key": "sidecam_run", "label": "Sidecam run", "source_object": "shop-data.sidecam_run",
             "primary_key": ["id"],
             "fields": [field("id", "integer"), field("peak_kmh", "decimal"), field("owner_email", semantic_type="email"),
                        field("owner_name", sensitivity="personal"), field("lane")]},
            {"key": "people", "label": "People", "source_object": "shop-data.people", "primary_key": ["id"],
             "fields": [field("id", "integer"), field("kind"), field("role")]},
        ],
    })


# ---------------------------------------------------------------- names the dialect can read

def test_a_schema_with_a_hyphen_is_quoted_per_part_and_plain_names_stay_bare() -> None:
    assert sql_object("shop-data.runs", "mysql") == "`shop-data`.runs"
    assert sql_object("shop-data.runs", "postgres") == '"shop-data".runs'
    assert sql_object("dbo.tpg_project", "tsql") == "dbo.tpg_project"
    out = schema_context(snap(), frozenset(), "how many runs", dialect="mysql")
    assert "- `shop-data`.runs:" in out and "shop-data.runs:" not in out.replace("`shop-data`.runs", "")


def test_unquoted_awkward_names_are_quoted_outside_strings_and_quoted_names() -> None:
    s = snap()
    assert custom_sql._quote_awkward_names("SELECT COUNT(*) FROM shop-data.runs", s, "mysql") == \
        "SELECT COUNT(*) FROM `shop-data`.runs"
    text = "SELECT 'shop-data.runs' AS t FROM `shop-data`.runs WHERE x = 'a''shop-data'"
    assert custom_sql._quote_awkward_names(text, s, "mysql") == text  # strings and quoted names untouched
    assert custom_sql._quote_awkward_names("SELECT 1 FROM shop-data.runs", s, "postgres") == \
        'SELECT 1 FROM "shop-data".runs'


def test_a_schema_and_table_quoted_as_one_name_are_split() -> None:
    tree = parse_one("SELECT COUNT(*) FROM `shop-data.people` WHERE kind = 'x'", read="mysql")
    custom_sql._split_dotted_tables(tree)
    t = next(tree.find_all(exp.Table))
    assert (t.db, t.name) == ("shop-data", "people")
    assert "`shop-data`.people" in tree.sql(dialect="mysql")


def test_the_parser_s_words_are_kept_for_the_repair() -> None:
    assert custom_sql._parse_problem("SELECT FROM WHERE", "mysql")
    assert custom_sql._parse_problem("SELECT 1", "mysql") is None


def test_self_join_resolves_schema_qualified_column_to_unaliased_instance() -> None:
    from tela_api.identity.scope import EntityScope, ResolvedScope
    s = snap()
    scope = ResolvedScope("c1", {e.key: EntityScope(all_rows=True) for e in s.entities}, frozenset(), frozenset())
    sql = ("SELECT `shop-data`.people.id, p2.id FROM `shop-data`.people "
           "LEFT JOIN `shop-data`.people AS p2 ON `shop-data`.people.id = p2.id")
    prepared = custom_sql.validate(sql, engine="mysql", descriptor={}, snap=s, scope=scope)
    out = prepared.tree.sql(dialect="mysql")
    assert "people.id" in out and "`shop-data`.people.id" not in out


# ---------------------------------------------------------------- which tables the model sees

def test_run_together_table_names_meet_split_question_words() -> None:
    assert relevant_tables(snap(), "average peak speed of side-cam runs")[0] == "sidecam_run"
    assert relevant_tables(snap(), "sidecam runs by lane")[0] == "sidecam_run"


def test_a_small_model_lists_every_other_table_by_name_and_columns() -> None:
    out = schema_context(snap(), frozenset(), "how many runs", dialect="mysql")
    assert "Other tables (names and columns only" in out
    assert "- `shop-data`.people: id, kind, role" in out
    assert "other tables are not listed" not in out


def test_the_dialect_rules_reach_the_model() -> None:
    out = schema_context(snap(), frozenset(), "median peak", dialect="mysql")
    assert "ONLY_FULL_GROUP_BY" in out and "no PERCENTILE_CONT" in out


# ---------------------------------------------------------------- column values

def test_values_are_listed_on_their_column() -> None:
    out = schema_context(snap(), frozenset(), "how many people of each kind", dialect="mysql",
                         values={"people.kind": ["coach", "student"]})
    assert "kind (text, Kind; values: 'coach', 'student')" in out


def test_personal_email_and_non_text_columns_are_never_profiled() -> None:
    e = snap().entities[1]
    profiled = {f.key for f in e.fields if direct_sql.profiled(f)}
    assert profiled == {"lane"}  # not the email, not the personal name, not numbers


def test_the_values_query_is_a_short_distinct_list_without_blanks() -> None:
    sql = direct_sql.values_sql(snap().entities[2], "kind", "mysql")
    assert sql.startswith("SELECT DISTINCT `kind` FROM `shop-data`.people WHERE NOT `kind` IS NULL LIMIT ")


async def test_values_come_back_only_for_short_lists_and_failures_are_skipped() -> None:
    direct_sql._profiles.clear()

    async def run(statement: str) -> dict[str, Any]:
        if "`kind`" in statement:
            return {"rows": [["coach"], ["student"]]}
        if "`role`" in statement:  # too many for a list of codes: left out
            return {"rows": [[f"r{i}"] for i in range(direct_sql.PROFILE_CODES + 1)]}
        raise TelaError(ErrorCode.QUERY_FAILED, "nope")

    got = await direct_sql.column_values(run, snap(), frozenset(), "people by kind and role", "mysql", "c1")
    assert got == {"people.kind": ["coach", "student"]}


# ---------------------------------------------------------------- the answer checks

def test_a_zero_total_counts_as_matching_nothing() -> None:
    assert direct_sql.matched_nothing({"rows": []})
    assert direct_sql.matched_nothing({"rows": [[0, 0]]})
    assert direct_sql.matched_nothing({"rows": [[None]]})
    assert not direct_sql.matched_nothing({"rows": [[3]]})
    assert not direct_sql.matched_nothing({"rows": [[0], [0]]})  # a breakdown with zeros is an answer


def test_the_repair_request_carries_the_precise_reason() -> None:
    err = TelaError(ErrorCode.SQL_PARSE_ERROR, custom_sql.MESSAGES["parse"],
                    {"reason": "parse error", "parse_error": "Invalid expression / Unexpected token. Line 1, Col: 25."})
    text = direct_sql.repair_request("q?", "SELECT bad", direct_sql._Rejected("parse", err, []))
    assert "The parser said: Invalid expression" in text and "Your previous SQL:\nSELECT bad" in text
    scope = TelaError(ErrorCode.SCOPE_REWRITE_FAILED, "Could not apply your data rules to this query, so it was not run.",
                      {"admin_detail": {"reason": "object_not_in_model", "object": "x.y"}})
    rejected = direct_sql._Rejected("scope_applied", scope, [])
    assert "Reason: object_not_in_model (x.y)" in direct_sql.repair_request("q?", "SELECT 1", rejected)
    assert direct_sql._repairable(rejected)
    breach = TelaError(ErrorCode.SCOPE_REWRITE_FAILED, "x", {"admin_detail": {"reason": "predicate_mismatch"}})
    assert not direct_sql._repairable(direct_sql._Rejected("scope_applied", breach, []))
    assert sql_context.MAX_VALUES >= 10


# ---------------------------------------------------------------- longer code lists and the answer shape

def test_a_long_list_of_short_codes_is_shown_but_free_text_is_not() -> None:
    codes = [f"metric_{i}_kph" for i in range(44)]
    assert direct_sql.short_list(codes)
    assert not direct_sql.short_list([f"a long sentence of free text number {i}" for i in range(30)])
    assert not direct_sql.short_list([f"c{i}" for i in range(direct_sql.PROFILE_CODES + 1)])


def test_the_values_the_question_names_are_listed_first() -> None:
    vals = [f"metric_{i}" for i in range(40)] + ["speed_kph"]
    note = sql_context._values_note(vals, {"median", "speed", "kph"})
    assert note.startswith("values: 'speed_kph', 'metric_0'") and "(+29 more)" in note


def test_a_shape_only_rejection_keeps_the_checked_statement() -> None:
    prepared = custom_sql.Prepared(parse_one("SELECT 1", read="mysql"), "mysql", {}, False, "h", None)
    err = TelaError(ErrorCode.VALIDATION_ERROR, "The question asks for a comparison")
    rejected = direct_sql._Rejected("answer_shape", err, [], prepared)
    assert rejected.prepared is prepared  # every safety check passed: the loop may run it with a note
    assert direct_sql._Rejected("scope_applied", err, []).prepared is None
