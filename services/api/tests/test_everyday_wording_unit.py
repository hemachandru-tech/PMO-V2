"""Everyday wording (ENH-022 Wave 1: US-213 grammar words, US-214 free-text fields; FEAT-072), no database.

EXEC-139: "How many number of project will be active in UAE region" answered 0 at confidence 1.0 because "will" was
linked by a partial match to a hold-reason sentence. Grammar words are never matched against data now (US-213), a
field whose values are sentences is kept out of the value index, and a partial match links only a short, name-like
value by a word few values hold (US-214).
"""

from __future__ import annotations

from test_noise_set_unit import DATAVERSE_HOLD_REASONS, FIXTURE_VALUES
from test_planner_gold_unit import KNOWN, SNAP, plan

from tela_api.ask.planner import GRAMMAR, STOP, words
from tela_api.knowledge.values import ValueRow, free_text_values, link, norm, rows_for

EXEC139 = "How many number of project will be active in UAE region"


def _row(dim: str, value: str) -> ValueRow:
    return ValueRow(dim, value, value)


def test_grammar_words_are_stop_words_and_meaning_words_are_not() -> None:
    assert {"will", "would", "should", "going", "kindly", "being", "been"} <= GRAMMAR <= STOP
    # Negation, comparison, time and words labels use still carry meaning.
    assert not {"not", "no", "without", "over", "under", "more", "than", "next", "last", "before", "after",
                "within", "open", "active", "need", "share", "expected", "done"} & GRAMMAR


def test_no_pack_label_or_synonym_uses_a_grammar_word() -> None:
    """A grammar word is dropped from questions and labels alike, so a label made of one ("Risks past their due
    date") would lose a word the question needs; keep such words out of ``GRAMMAR``."""
    texts = [t for m in SNAP.measures for t in (m.label, *m.synonyms)]
    texts += [t for d in SNAP.dimensions for t in (d.label, *d.synonyms, *d.value_labels.values())]
    assert {(w, t) for t in texts for w in words(t) if w in GRAMMAR} == set()


def test_a_grammar_word_never_links_a_value() -> None:
    rows = [_row("hold_reason", v) for v in DATAVERSE_HOLD_REASONS] + [_row("region", "GCC - UAE")]
    out = link(EXEC139, rows, known=KNOWN)
    assert [(v.dimension, v.stored) for v in out.links] == [("region", "GCC - UAE")]
    assert "will" not in " ".join(out.unmatched)


def test_a_partial_match_needs_a_name_like_value_and_a_rare_word() -> None:
    sentences = [_row("hold_reason", v) for v in DATAVERSE_HOLD_REASONS]
    # "training" is a word of one sentence: a sentence is not a name, so nothing links.
    assert link("projects on hold for training", sentences, known=KNOWN).links == []
    # "lina" of "Lina Farouk" still links (US-173).
    people = [_row("project_manager", "Lina Farouk"), _row("project_manager", "Omar Haddad")]
    assert [(v.stored, v.how) for v in link("projects of lina", people, known=KNOWN).links] == [
        ("Lina Farouk", "partial")]
    # A word five values hold is too common to name one of them.
    many = [_row("platform", f"Network {n}") for n in ("One", "Flex", "Core", "Edge", "Pay")]
    out = link("projects on network", many, known=KNOWN)
    assert out.links == [] and out.choices == []
    # Four still ask which one (US-173 behaviour kept).
    assert link("projects on network", many[:4], known=KNOWN).choices


def test_sentence_values_are_free_text() -> None:
    assert free_text_values(DATAVERSE_HOLD_REASONS)
    assert free_text_values([*FIXTURE_VALUES["hold_reason"], *DATAVERSE_HOLD_REASONS])
    for key in ("hold_reason", "region", "pcr_reason", "project_manager", "lifecycle_phase"):
        assert not free_text_values(FIXTURE_VALUES[key]), key
    assert not free_text_values(["Waiting on the vendor to confirm the date.", "Budget"])  # too few to tell
    # The dev Dataverse PCR reasons: long Title Case labels, not sentences (EXEC-140's live check).
    assert not free_text_values([
        "Additional Scope Requested by Client", "Dependency on Other Project", "Initial Baseline",
        "Regulatory / Compliance Requirement (e.g., Central Bank)", "Schedule Slippage - Build Delay",
        "Schedule Slippage - Client (System / Resource Constraints)",
        "Schedule Slippage - NI (System / Resource Constraints)", "Schedule Slippage - Vendor",
        "Warranty Extension Due to Post-Go-Live Issues"])


def test_free_text_fields_are_left_out_of_the_index() -> None:
    index = rows_for(SNAP, {"hold_reason": DATAVERSE_HOLD_REASONS, "region": FIXTURE_VALUES["region"]})
    assert {r.dimension for r in index} == {"region"}
    assert norm("GCC - UAE") in {n for r in index for n in r.names}


def test_exec139_links_no_hold_reason_and_keeps_its_region() -> None:
    index = rows_for(SNAP, {**FIXTURE_VALUES, "hold_reason": [*FIXTURE_VALUES["hold_reason"],
                                                               *DATAVERSE_HOLD_REASONS]})
    p = plan(EXEC139, index=index)
    assert p.status == "ok" and p.spec is not None
    refs = {f["ref"] for f in p.spec.get("filters", [])}
    assert "dimension:hold_reason" not in refs and "dimension:region" in refs
    assert p.spec["measure"]["ref"] == "measure:open_projects"


def test_grammar_words_leave_plans_as_they_were() -> None:
    for plain, noisy in [("How many projects are in delivery in KSA", "How many projects will be in delivery in KSA"),
                         ("Projects on hold by region", "I would like to know projects on hold by region"),
                         ("How many open projects are there", "Kindly tell me how many open projects are there")]:
        a, b = plan(plain), plan(noisy)
        assert a.status == b.status == "ok" and a.spec == b.spec, (plain, noisy)
