"""LLM mapping suggestions (US-201; FEAT-067): which fields the model is asked about, what it is shown, and how its
reply is checked — no database, no model."""

from __future__ import annotations

from typing import Any

import pytest
from pmo_oracle import open_by_status

from tela_api.packs import ai_suggest
from tela_api.packs.ai_suggest import AiColumn, AiMapReply
from tela_api.packs.manifest import PackManifest, discover, load_manifest, match
from tela_api.packs.mapping import PackMapping

_TYPES = {"text": "varchar", "integer": "int", "decimal": "decimal(12,2)", "date": "date", "datetime": "datetime",
          "boolean": "tinyint(1)"}
RENAME = {"status": "record_status", "planned_deployment_date": "go_live_target", "cio": "cio_full_name"}


@pytest.fixture(scope="module")
def manifest() -> PackManifest:
    """The PMO pack with open = Status Active, as 2.1.0 had RULE-026 (DEC-24): Status is filtered by its values."""
    pack = load_manifest(next(p for p in discover() if p.parent.name == "pmo"))[0]
    return PackManifest.model_validate(open_by_status(pack.model_dump()))


def _source(manifest: PackManifest) -> list[dict[str, Any]]:
    """The fixture source with three project columns renamed: the record state, the go-live date and the CIO."""
    out = []
    for e in manifest.entities:
        cols = [{"name": f.columns[0], "type": _TYPES[f.data_type]} for f in e.fields]
        if e.key == "projects":
            cols = [{**c, "name": RENAME.get(c["name"], c["name"])} for c in cols]
        out.append({"qualified_name": f"tpg.{e.object_candidates[0]}", "columns": cols})
    return out


def _profile(*values: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"value": v, "rows": n} for v, n in values]


SAMPLES = {"record_status": _profile(("Active", 420), ("Inactive", 131)),
           "current_state": _profile(("Active", 279), ("Completed", 120), ("On Hold", 79), ("Project Created", 73)),
           "cio_full_name": _profile(("Jane Doe", 40))}
VALUE_SUGGESTED = {"projects.status": {"column": "record_status", "confidence": 1.0}}


def test_asks_only_what_names_and_values_leave_open(manifest: PackManifest) -> None:
    catalog = _source(manifest)
    r = match(manifest, catalog)
    asked = ai_suggest.wanted(manifest, r, {"projects.status": "unmapped"}, VALUE_SUGGESTED)
    # Status is settled by its values (US-200 at 1.0); the go-live date and the CIO have neither.
    assert asked == {"projects": {"planned_deployment_date": "unmapped", "cio": "unmapped"}}
    # A tie between two columns (US-200 at 0.7) does not settle it: the model is asked too.
    tie = {"projects.status": {"column": "record_status", "confidence": 0.7}}
    assert ai_suggest.wanted(manifest, r, {"projects.status": "unmapped"}, tie)["projects"]["status"] == "unmapped"
    # "Not in this source" is the Admin's answer; a mapped column whose values don't fit is asked about, and so is
    # Current state, whose own column that mapping took (US-199's column_taken).
    r2 = match(manifest, catalog, mapping=PackMapping(fields={"projects.cio": None,
                                                              "projects.status": "current_state"}))
    asked = ai_suggest.wanted(manifest, r2, {"projects.status": "values_dont_fit"}, {})
    assert asked == {"projects": {"status": "values_dont_fit", "current_state": "unmapped",
                                  "planned_deployment_date": "unmapped"}}
    # A source whose names all match asks nothing: no call is made.
    plain = [{**c, "columns": [{**col, "name": next((k for k, v in RENAME.items() if v == col["name"]), col["name"])}
                               for col in c["columns"]]} for c in catalog]
    assert ai_suggest.wanted(manifest, match(manifest, plain), {}, {}) == {}


def test_prompt_has_metadata_and_a_few_values_never_counts_or_personal_values(manifest: PackManifest) -> None:
    catalog = _source(manifest)
    r = match(manifest, catalog)
    asked = {"status": "unmapped", "planned_deployment_date": "unmapped", "cio": "unmapped"}
    cols = ai_suggest.offered(catalog, r, "projects", asked)
    names = {c["name"] for c in cols}
    # Columns other pack fields read are not offered; the open ones are.
    assert {"record_status", "go_live_target", "cio_full_name"} <= names and "current_state" not in names
    sampled = ai_suggest.sampled(cols)
    assert "record_status" in sampled and "cio_full_name" not in sampled  # personal: name and type only
    assert "go_live_target" not in sampled  # values are read for text columns only
    text = ai_suggest.prompt(manifest, "projects", asked, cols, {k: v for k, v in SAMPLES.items() if k in sampled},
                             r.entities["projects"].mapping)
    assert "- status — Status, text" in text and "values: Active, Inactive" in text
    assert "- record_status, varchar; e.g. Active, Inactive" in text
    assert "- go_live_target, date" in text and "- cio_full_name, varchar" in text
    assert "Jane Doe" not in text and "420" not in text and "current_state" not in text


def test_reply_is_checked_and_guards_flag_what_they_would_refuse(manifest: PackManifest) -> None:
    catalog = _source(manifest)
    r = match(manifest, catalog)
    asked = {"status": "unmapped", "planned_deployment_date": "unmapped", "cio": "unmapped"}
    reply = AiMapReply(suggestions=[
        AiColumn(field="planned_deployment_date", column="go_live_target", confidence=0.9,
                 reason="A date column named for the go-live target."),
        AiColumn(field="status", column="current_state", confidence=0.6, reason="Holds Active."),
        AiColumn(field="cio", column="no_such_column", confidence=0.8, reason="Made up."),
        AiColumn(field="region", column="region", confidence=0.9, reason="Not asked."),
    ])
    got, dropped = ai_suggest.accept(manifest, catalog, r, "projects", asked, reply, SAMPLES)
    assert dropped == 2  # a column the table lacks, a field not asked about
    good = got["projects.planned_deployment_date"]
    assert good == {"column": "go_live_target", "confidence": 0.9, "reason": "A date column named for the go-live "
                    "target.", "why": "unmapped", "source": "ai", "flags": []}
    # EXEC-115 again, now from the model: current_state is Current state's column and lacks Inactive.
    flags = got["projects.status"]["flags"]
    assert flags[0] == "current_state is already Current state's column. One column can stand for one pack field only."
    assert flags[1].startswith("Its values don't fit the pack's Status: Inactive not found; it holds Completed")
    assert "projects.cio" not in got

    # A type that doesn't fit, one column for two fields, and "none" kept with its reason.
    reply = AiMapReply(suggestions=[
        AiColumn(field="planned_deployment_date", column="record_status", confidence=0.4, reason="?"),
        AiColumn(field="status", column="RECORD_STATUS", confidence=0.95, reason="Holds Active and Inactive."),
        AiColumn(field="cio", column=None, confidence=0.2, reason="No column names a person."),
    ])
    got, dropped = ai_suggest.accept(manifest, catalog, r, "projects", asked, reply, SAMPLES)
    assert dropped == 0
    assert got["projects.status"]["column"] == "record_status"  # the catalogue's spelling
    assert got["projects.status"]["flags"] == ["Also suggested for Planned deployment date. One column can stand "
                                               "for one pack field only."]
    assert got["projects.planned_deployment_date"]["flags"][0] == "record_status is varchar; Planned deployment " \
                                                                  "date needs date."
    assert got["projects.cio"] == {"column": None, "confidence": 0.2, "reason": "No column names a person.",
                                   "why": "unmapped", "source": "ai", "flags": []}


def test_the_column_a_field_already_reads_is_not_suggested_back(manifest: PackManifest) -> None:
    catalog = _source(manifest)
    r = match(manifest, catalog, mapping=PackMapping(fields={"projects.status": "cio_full_name"}))
    reply = AiMapReply(suggestions=[AiColumn(field="status", column="cio_full_name", confidence=0.5, reason="?")])
    got, dropped = ai_suggest.accept(manifest, catalog, r, "projects", {"status": "values_dont_fit"}, reply, {})
    assert got == {} and dropped == 1


def test_the_rules_guess_is_the_draft_in_the_prompt(manifest: PackManifest) -> None:
    """US-245: a value suggestion too weak to settle a field is shown as the rules' guess to confirm or correct."""
    catalog = _source(manifest)
    r = match(manifest, catalog)
    asked = {"status": "unmapped", "cio": "unmapped"}
    cols = ai_suggest.offered(catalog, r, "projects", asked)
    guess = {"projects.status": {"column": "record_status", "confidence": 0.7, "reason": "Values Active match"}}
    text = ai_suggest.prompt(manifest, "projects", asked, cols, {}, r.entities["projects"].mapping, guess)
    line = next(ln for ln in text.splitlines() if ln.startswith("- status"))
    assert "rules' guess: record_status (0.70, Values Active match)" in line
    assert "rules' guess" not in next(ln for ln in text.splitlines() if ln.startswith("- cio"))
    assert "rules' guess" in ai_suggest.SYSTEM
    assert ai_suggest.prompt(manifest, "projects", asked, cols, {}, r.entities["projects"].mapping) == text.replace(
        "; rules' guess: record_status (0.70, Values Active match)", "")
