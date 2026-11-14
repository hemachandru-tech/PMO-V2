"""Ask answer modes (ENH-028 US-256): which mode a source can take and which planner Ask runs."""

from __future__ import annotations

import pytest

from tela_api.ask import modes
from tela_api.core.settings import get_settings


def test_the_four_modes_with_certified_the_default() -> None:
    assert modes.MODES == ("certified", "fields", "hybrid", "direct_sql")
    assert modes.DEFAULT == "certified"
    listed = modes.choices("postgres", {})
    assert [c["mode"] for c in listed] == list(modes.MODES)
    assert [c["default"] for c in listed] == [True, False, False, False]
    assert all(c["label"] and c["note"] for c in listed)


@pytest.mark.parametrize("engine, descriptor", [("postgres", {}), ("mysql", {}), ("sqlserver", {}),
                                                ("dataverse", {"mode": "tds"})])
def test_direct_sql_on_sql_sources(engine: str, descriptor: dict[str, str]) -> None:
    assert modes.unavailable("direct_sql", engine, descriptor) is None
    assert modes.planner_for("direct_sql", engine, descriptor) == ("direct_sql", None)


@pytest.mark.parametrize("engine, descriptor", [("mongodb", {}), ("dataverse", {"mode": "webapi"})])
def test_direct_sql_refused_on_other_sources(engine: str, descriptor: dict[str, str]) -> None:
    why = modes.unavailable("direct_sql", engine, descriptor)
    assert why is not None and "SQL source" in why
    mode, note = modes.planner_for("direct_sql", engine, descriptor)
    assert mode == "certified" and note is not None and note.startswith("Direct SQL:")


def test_direct_sql_off_when_dec30_is_withdrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ask_direct_sql", False)
    assert modes.unavailable("direct_sql", "postgres", {}) == modes.MSG_DIRECT_OFF


@pytest.mark.parametrize("mode", ["hybrid"])
def test_modes_without_a_planner_fall_back_to_certified(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modes, "BUILT", modes.BUILT - {mode})  # every mode is built since US-255: one taken away
    assert modes.unavailable(mode, "postgres", {}) == modes.MSG_NOT_BUILT
    got, note = modes.planner_for(mode, "postgres", {})
    assert got == "certified" and note is not None and "certified measures" in note


def test_fields_is_built_on_every_engine() -> None:
    for engine in ("postgres", "mongodb"):
        assert modes.planner_for("fields", engine, {}) == ("fields", None)
        assert modes.planner_for("hybrid", engine, {}) == ("hybrid", None)  # US-255


def test_no_stored_mode_or_an_unknown_one() -> None:
    assert modes.planner_for(None, "postgres", {}) == ("certified", None)
    assert modes.unavailable("guess", "postgres", {}) == "Unknown answer mode."
    assert modes.planner_for("guess", "postgres", {})[0] == "certified"
