"""Agent catalogue and prompt composition (ENH-030, US-264 / US-265)."""

from __future__ import annotations

from tela_api.agents import catalog
from tela_api.agents.prompts import SKILLS_HEAD, Overrides, compose


def test_every_agent_is_listed_with_its_prompts_and_every_default_loads() -> None:
    assert [a.code for a in catalog.AGENTS] == [f"AG-{n:02d}" for n in range(1, 11)]
    assert len(catalog.BY_KEY) == len(catalog.PROMPTS)
    for a in catalog.AGENTS:
        assert catalog.prompts_for(a.code), a.code
    for p in catalog.PROMPTS:
        assert catalog.default(p.key).strip(), p.key
        assert p.agent in catalog.BY_CODE or p.agent == "shared"


def test_no_default_prompt_holds_another_so_each_swap_is_unambiguous() -> None:
    texts = {p.key: catalog.default(p.key) for p in catalog.PROMPTS}
    for a, x in texts.items():
        for b, y in texts.items():
            assert a == b or x not in y, f"{a} is inside {b}"


def test_an_edited_prompt_replaces_its_default_and_skills_follow() -> None:
    plan = catalog.default("ask.plan")
    system = plan + "\n\nCandidates: measure:open_projects"
    ov = Overrides(prompts={"ask.plan": "  Plan carefully.  "},
                   skills={"AG-03": [("Wording", "Say 'slipping' means schedule risk.")]})
    out, used = compose(system, "AG-03", ov)
    assert plan not in out and out.startswith("Plan carefully.\n\nCandidates: measure:open_projects")
    assert out.endswith(SKILLS_HEAD + "\n\n## Wording\nSay 'slipping' means schedule risk.")
    assert used == {"prompts": ["ask.plan"], "skills": ["Wording"]}
    # Another agent's call keeps its text and gets no AG-03 skills.
    other, used = compose(catalog.default("ask.narrate"), "AG-08", ov)
    assert other == catalog.default("ask.narrate") and used == {"prompts": [], "skills": []}


def test_a_piece_keeps_the_defaults_own_leading_space_and_new_line() -> None:
    from tela_api.content.sql_context import AG10_SYSTEM

    out, used = compose(AG10_SYSTEM, "AG-10", Overrides(prompts={"sql.custom": "Use :page_filters.\n"}))
    assert used["prompts"] == ["sql.custom"]
    assert out == catalog.default("sql.role") + " Use :page_filters."  # the leading space is kept


def test_template_prompts_are_read_only() -> None:
    assert not catalog.BY_KEY["packs.author"].editable
