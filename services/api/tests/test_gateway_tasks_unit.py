"""BUG-024: every task the api sends to the LLM gateway is one the gateway allows.

AG-09 sent ``explain_rule`` and AG-10 ``draft_sql``; the gateway refused both and the callers fell back silently.
"""

from __future__ import annotations

import re
from pathlib import Path

from tela_gateway.settings import GatewaySettings
from tela_gateway.tenant_settings import ROLE_BY_TASK

API_SRC = Path(__file__).resolve().parents[1] / "src" / "tela_api"
LITERAL = re.compile(r"""\btask\s*=\s*["']([a-z_]+)["']""")
CONSTANT = re.compile(r"""^\s*AGENT,\s*TASK\s*=\s*["']AG-\d+["'],\s*["']([a-z_]+)["']""", re.M)


def sent_tasks() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in API_SRC.rglob("*.py"):
        source = path.read_text()
        for task in LITERAL.findall(source) + CONSTANT.findall(source):
            found.setdefault(task, []).append(path.relative_to(API_SRC).as_posix())
    return found


def test_every_task_the_api_sends_is_allowed_by_the_gateway() -> None:
    allowed = set(GatewaySettings().llm_allowed_tasks)
    sent = sent_tasks()
    assert {"plan", "plan_review", "narrate", "cert_review", "sql_draft", "map_suggest", "pack_author"} <= set(sent)
    refused = {task: files for task, files in sent.items() if task not in allowed}
    assert not refused, f"tasks the gateway refuses: {refused}"


def test_every_allowed_task_has_a_model_role() -> None:
    assert set(GatewaySettings().llm_allowed_tasks) <= set(ROLE_BY_TASK)
