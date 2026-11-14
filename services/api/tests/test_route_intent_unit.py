"""Which questions ask for a definition and which ask for the figure (BUG-015): rules only, no database."""

from __future__ import annotations

import pytest

from tela_api.ask.planner import route_intent


@pytest.mark.parametrize("question", [
    "What is the overall health of the project portfolio?",
    "What is the approved budget for open projects?",
    "What is the average calculated health of open projects?",
    "what are overdue go-lives by region",
    "What is total forecast cost this year?",
])
def test_what_is_the_figure_is_a_data_question(question: str) -> None:
    assert route_intent(question).kind == "data"


@pytest.mark.parametrize(("question", "subject", "loose"), [
    ("What does overdue go-lives mean?", "overdue go-lives", False),
    ("What is overdue go-lives?", "overdue go-lives", True),
    ("What is the definition of open projects?", "open projects", False),
    ("How is calculated health calculated?", "calculated health", False),
    ("Define forecast variance", "forecast variance", False),
])
def test_definition_questions(question: str, subject: str, loose: bool) -> None:
    intent = route_intent(question)
    assert (intent.kind, intent.subject, intent.loose) == ("definition", subject, loose)
