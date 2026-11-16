"""Direct SQL follow-ups read the earlier answers' SQL; the model sees a few real rows of the short-listed tables."""

from __future__ import annotations

import asyncio

from tela_api.ask.direct_sql import conversation_note, sample_columns, sample_rows, sample_sql, samples_context
from tela_query_spec.model import ModelSnapshot


def snap() -> ModelSnapshot:
    def f(c: str, t: str = "text", **kw: object) -> dict:
        return {"key": c, "source_column": c, "label": c, "data_type": t, **kw}
    return ModelSnapshot.model_validate({
        "model_version_id": "v", "connection_id": "c", "engine": "mysql",
        "entities": [{"key": "users", "label": "Users", "source_object": "app.users", "primary_key": ["id"],
                      "display_fields": ["full_name"],
                      "fields": [f("id", "integer"), f("full_name"), f("user_type", values=["student", "coach"]),
                                 f("email", sensitivity="personal"), f("prefs", "json"),
                                 f("secret", is_queryable=False)]}]})


def test_a_follow_up_sees_the_earlier_questions_with_their_sql() -> None:
    note = conversation_note([{"question": "how many students are there",
                               "sql": "SELECT COUNT(*) FROM app.users WHERE user_type = 'student'", "row_count": 1}])
    assert "keep the latest earlier answer's tables, WHERE conditions" in note
    assert "SQL: SELECT COUNT(*) FROM app.users WHERE user_type = 'student'" in note
    assert conversation_note([]) == "" and conversation_note(None) == ""


def test_samples_leave_out_personal_json_and_hidden_columns() -> None:
    e = snap().entity("users")
    assert [c.key for c in sample_columns(e, frozenset())] == ["full_name", "user_type", "id"]
    assert sample_sql(e, ["full_name", "user_type"], "mysql") == \
        "SELECT `full_name`, `user_type` FROM app.users LIMIT 5"


def test_sample_rows_are_read_once_and_shown_as_a_small_table() -> None:
    calls = []

    async def run(sql: str) -> dict:
        calls.append(sql)
        return {"rows": [["Arun", "student", 1], ["Meera", "coach", 2]]}

    s = snap()
    got = asyncio.run(sample_rows(run, s, frozenset(), ["users"], "mysql", "conn-1"))
    again = asyncio.run(sample_rows(run, s, frozenset(), ["users"], "mysql", "conn-1"))
    assert got == again == {"app.users": ["full_name | user_type | id", "Arun | student | 1", "Meera | coach | 2"]}
    assert len(calls) == 1
    assert "SAMPLE ROWS" in samples_context(got) and samples_context({}) == ""


def test_a_question_that_names_nothing_in_the_model_is_asked_about() -> None:
    from tela_api.ask.direct_sql import names_nothing
    s = snap()
    assert names_nothing(s, "give the list")
    assert names_nothing(s, "show me all of them")
    assert not names_nothing(s, "give the student list")  # a stored value of user_type
    assert not names_nothing(s, "list the users")  # a table


def test_plurals_and_near_spellings_still_name_the_model() -> None:
    from tela_api.ask.direct_sql import names_nothing
    s = snap()
    assert not names_nothing(s, "how many coaches are there")  # coach is a stored value
    assert not names_nothing(s, "how many studnts are there")  # a near spelling of student
    assert names_nothing(s, "how many are there")
