"""Static guarantees for the single scoped execution path (US-022; REC-004, RISK-001; TEST-075).

The build fails if any module other than the query service can reach the runner's execute operation,
or if anything outside the transport signs execution envelopes.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tela_api"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def _module(path: Path) -> str:
    return "tela_api." + ".".join(path.relative_to(SRC).with_suffix("").parts)


def test_only_the_query_service_imports_the_execution_client() -> None:
    users = {_module(p) for p in SRC.rglob("*.py") if any(
        n == "tela_api.query.runner" or n.startswith("tela_api.query.runner.") or n == "tela_api.query.runner"
        for n in _imports(p)) or "from tela_api.query import runner" in p.read_text()}
    assert users == {"tela_api.query.service"}


def test_execute_operation_is_named_in_one_place_only() -> None:
    import re

    pattern = re.compile(r"""\.post\(\s*["']execute["']""")
    hits = sorted(_module(p) for p in SRC.rglob("*.py") if pattern.search(p.read_text()))
    assert hits == ["tela_api.query.runner"]


def test_envelopes_are_signed_only_by_the_transport() -> None:
    signers = sorted(_module(p) for p in SRC.rglob("*.py") if "envelope.sign(" in p.read_text()
                     or "env.sign(" in p.read_text())
    assert signers == ["tela_api.runner_transport"]


def test_no_source_driver_in_the_api() -> None:
    """The API never opens a source connection: no psycopg pool or source driver use outside tests."""
    for p in SRC.rglob("*.py"):
        if _module(p) == "tela_api.cli":  # the operator CLI talks to the metadata DB only
            continue
        assert "psycopg_pool" not in p.read_text() and "pymongo" not in p.read_text(), p
