"""The model builder's data profile: column kinds from counts and value lists, any domain."""

from __future__ import annotations

from tela_api.model.profile import ColumnProfile, apply_profile, date_expression, date_format


def test_text_date_formats_are_read_from_the_values() -> None:
    assert date_format(["2026-01-05", "2026-11-30"]) == "YYYY-MM-DD"
    assert date_format(["25/10/2026", "01/02/2026"]) == "DD/MM/YYYY"
    assert date_format(["10/25/2026", "02/01/2026"]) == "MM/DD/YYYY"
    assert date_format(["01/02/2026", "03/04/2026"]) is None  # can't tell day from month
    assert date_format(["2026-01-05", "soon"]) is None
    assert date_expression("`d`", "DD/MM/YYYY", "mysql") == "STR_TO_DATE(`d`, '%d/%m/%Y')"
    assert date_expression('"d"', "MM/DD/YYYY", "postgres") == "TO_DATE(\"d\", 'MM/DD/YYYY')"


def definition() -> dict:
    cols = {"id": "integer", "player_code": "text", "stage": "text", "status_code": "text", "ok": "integer",
            "payload": "json", "password_hash": "text", "empty_col": "text", "region": "text", "played_on": "text",
            "full_name": "text"}
    return {"entities": [{"key": "games", "label": "Games", "source_object": "db.games", "primary_key": ["id"],
                          "display_fields": ["empty_col", "full_name"],
                          "fields": [{"key": c, "source_column": c, "label": c, "data_type": t}
                                     for c, t in cols.items()]}], "links": [], "measures": [], "dimensions": []}


def test_each_column_gets_its_kind_from_the_data() -> None:
    d = definition()
    prof = {"games": {
        "id": ColumnProfile("id", 50, 50, 50, 1, 50),
        "player_code": ColumnProfile("player_code", 50, 50, 50, "P-001", "P-050"),
        "stage": ColumnProfile("stage", 50, 50, 3, "detect", "queued", [("done", 20), ("queued", 20), ("detect", 10)]),
        "status_code": ColumnProfile("status_code", 50, 50, 1, "P", "P"),
        "ok": ColumnProfile("ok", 50, 50, 2, 0, 1),
        "payload": ColumnProfile("payload", 50, 40),
        "empty_col": ColumnProfile("empty_col", 50, 0, 0),
        "region": ColumnProfile("region", 50, 48, 4, "East", "West", [("East", 20), ("West", 28)]),
        "played_on": ColumnProfile("played_on", 50, 50, 40, "01/02/2026", "31/12/2025", date_format=None),
        "full_name": ColumnProfile("full_name", 50, 50, 50, "Ann", "Zed"),  # unique, no spaces, still a name
    }}
    prof["games"]["played_on"].date_format = "DD/MM/YYYY"
    counts = apply_profile(d, prof, {}, "mysql")
    f = {x["source_column"]: x for x in d["entities"][0]["fields"]}
    assert f["player_code"]["semantic_type"] == "identifier"           # unique and code-like
    assert f["full_name"].get("semantic_type") != "identifier"         # unique but a name (has spaces)
    assert f["stage"]["values"] == ["done", "queued", "detect"]        # vocabulary, most common first
    assert f["status_code"]["is_queryable"] is False                   # one value only
    assert f["ok"]["semantic_type"] == "flag" and f["payload"]["semantic_type"] == "json"
    assert f["password_hash"]["is_queryable"] is False and f["password_hash"]["sensitivity"] == "personal"
    assert f["empty_col"]["is_queryable"] is False and d["entities"][0]["display_fields"] == ["full_name"]
    assert "STR_TO_DATE(played_on, '%d/%m/%Y')" in f["played_on"]["description"]
    assert counts["identifier"] == 2 and counts["vocabulary"] == 2  # id (the key) and player_code; stage and region


def test_a_new_profile_replaces_the_old_one_instead_of_piling_up() -> None:
    d = definition()
    prof = {"games": {"full_name": ColumnProfile("full_name", 50, 50, 50, "A-1", "Z-9")}}
    apply_profile(d, prof, {}, "mysql")
    f = next(x for x in d["entities"][0]["fields"] if x["source_column"] == "full_name")
    f["description"] = "The player's name. " + f["description"]
    assert f["semantic_type"] == "identifier"
    apply_profile(d, {"games": {"full_name": ColumnProfile("full_name", 50, 50, 50, "Ann", "Zed")}}, {}, "mysql")
    assert f.get("semantic_type") is None and f["description"] == "The player's name."


def test_a_column_the_last_profile_hid_stays_hidden_when_it_cannot_be_read_again() -> None:
    d = definition()
    apply_profile(d, {"games": {"empty_col": ColumnProfile("empty_col", 50, 0, 0)}}, {}, "mysql")
    f = next(x for x in d["entities"][0]["fields"] if x["source_column"] == "empty_col")
    f["is_queryable"] = True  # the next generation shows every picked column again
    counts = apply_profile(d, {"games": {}}, {}, "mysql")  # and the approved version won't let it be read
    assert f["is_queryable"] is False and counts["empty"] == 1
