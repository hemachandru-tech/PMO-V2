"""Every outbox topic the API emits has a worker handler, so no event dead-letters for want of one (US-009)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "services" / "api" / "src" / "tela_api"
SQL_TOPIC = re.compile(r"INSERT INTO ops\.outbox_events[^']*'([a-z_]+(?:\.[a-z_]+)+)'")


def emitted_topics() -> dict[str, str]:
    """topic → first file that emits it: ``outbox.emit(..., topic="…")`` calls and literal SQL inserts."""
    found: dict[str, str] = {}
    for path in sorted(API_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "emit":
                for kw in node.keywords:
                    if kw.arg == "topic" and isinstance(kw.value, ast.Constant):
                        found.setdefault(kw.value.value, path.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):  # adjacent literals are one node
                for topic in SQL_TOPIC.findall(node.value):
                    found.setdefault(topic, path.name)
    return found


def test_scan_finds_the_known_emitters() -> None:
    topics = emitted_topics()
    # Guards the scan itself: these come from emit() calls spread over several modules, and the CLI's SQL.
    assert {"dashboard.published", "model.version_approved", "certification.decided",
            "access.mappings_changed", "export.requested"} <= set(topics)
    assert topics["model.version_approved"] in {"router.py", "documents.py", "cli.py"}


def test_every_emitted_topic_has_a_handler() -> None:
    from tela_worker.tasks.outbox import HANDLERS

    missing = {t: f for t, f in emitted_topics().items() if t not in HANDLERS}
    assert not missing, f"outbox topics without a worker handler (they would dead-letter): {missing}"
