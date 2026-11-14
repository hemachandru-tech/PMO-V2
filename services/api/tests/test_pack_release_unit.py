"""Pack release checks and the diff between versions (ENH-025, US-238), no database.

The PMO pack as written passes every check that reads the pack alone; a starter widget on a missing measure, a
dashboard listing an unknown report, a glossary term reading a missing measure and a measure reading a missing field
each block with their path. Issue ids are stable. The diff lists a changed rule with its text before and after, and
added and removed items of the manifest and the knowledge; the content revision follows the documents.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from tela_api.packs.source import KINDS
from tela_api.packs.studio import release_checks as rc
from tela_api.packs.studio.store import PackRow

PACK = Path(__file__).resolve().parents[3] / "packs" / "pmo"


@pytest.fixture(scope="module")
def pmo() -> tuple[dict[str, Any], dict[str, Any]]:
    head = yaml.safe_load((PACK / "manifest.yaml").read_text())
    docs = {k: yaml.safe_load((PACK / f"{k}.yaml").read_text()) for k in KINDS if (PACK / f"{k}.yaml").exists()}
    return head, docs


def _copy(pmo: tuple[dict[str, Any], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    return copy.deepcopy(pmo[0]), copy.deepcopy(pmo[1])


def test_pmo_passes_the_pack_checks(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = pmo
    out = rc.static_checks(head, docs)
    assert out.errors == [], out.errors[:3]
    steps = {s["key"]: s for s in out.out()["steps"]}
    assert all(steps[k]["ran"] for k in ("schema", "references", "knowledge", "blueprints"))
    assert not steps["compile"]["ran"]  # needs a test connection
    full = rc.expanded(head, docs)
    assert "reports_from" not in full and len(full["reports"]) >= len(docs["report_blueprints"]["blueprints"]) - \
        sum(1 for b in docs["report_blueprints"]["blueprints"] if b.get("unavailable"))


def test_a_broken_widget_blocks(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = _copy(pmo)
    bp = docs["report_blueprints"]["blueprints"][0]
    bp.setdefault("widgets", []).append({"title": "Lost", "measure": "no_such_measure", "visual": "ranked_bar",
                                         "breakdowns": ["region"]})
    out = rc.static_checks(head, docs)
    broken = [e for e in out.errors if e["check"] == "blueprints"]
    assert len(broken) == 1 and "measure no_such_measure" in broken[0]["message"]
    assert broken[0]["path"].startswith(f"reports.{bp['id']}.widgets[")
    # The same problem has the same id, so a reason given at submission matches at release.
    assert rc.static_checks(head, docs).errors[0]["id"] == out.errors[0]["id"]


def test_unknown_report_on_a_dashboard_and_broken_knowledge_block(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = _copy(pmo)
    dash = next(iter(head["dashboards"]))
    head["dashboards"][dash].setdefault("reports", []).append("no_such_report")
    docs["glossary"]["terms"][0]["reading"] = "no_such_measure"
    head["measures"][0]["expression"] = {"agg": "count", "where": {"field": "no_such_field", "op": "=",
                                                                    "value": "x"}}
    out = rc.static_checks(head, docs)
    by_check = {e["check"] for e in out.errors}
    assert by_check == {"blueprints", "knowledge", "references"}, out.errors
    assert any(e["path"] == "glossary.terms[0].reading" for e in out.errors)
    assert any(e["path"].startswith(f"dashboards.{dash}.reports[") for e in out.errors)


def test_schema_errors_stop_before_references(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, docs = _copy(pmo)
    head["measures"][0]["expression"] = {"agg": "nope"}
    out = rc.static_checks(head, docs)
    assert out.errors and {e["check"] for e in out.errors} == {"schema"}


def test_diff_lists_a_changed_rule(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    old = rc.expanded(*pmo), pmo[1]
    head, docs = _copy(pmo)
    m = head["measures"][0]
    m["plain_rule"] = "A new rule text."
    m["expression"] = {"agg": "count", "where": {"field": "project_id", "op": "is_not_null"}}
    head["measures"].pop()
    head["measures"].append({"key": "brand_new", "label": "Brand new", "entity": m["entity"],
                             "plain_rule": "Every row.", "expression": {"agg": "count"}})
    docs["glossary"]["terms"].append({"term": "Widget", "meaning": "A thing on a report."})
    changes = rc.diff(old, (rc.expanded(head, docs), docs))
    by = {(c["kind"], c["key"]): c for c in changes}
    changed = by[("measure", m["key"])]
    assert changed["change"] == "changed" and set(changed["fields"]) == {"expression", "plain_rule"}
    assert changed["rule"] == {"before": pmo[0]["measures"][0]["plain_rule"], "after": "A new rule text."}
    assert by[("measure", "brand_new")]["change"] == "added"
    assert by[("measure", pmo[0]["measures"][-1]["key"])]["change"] == "removed"
    assert by[("glossary", "Widget")]["change"] == "added"
    summary = rc.diff_summary(changes)
    assert summary["measure"] == {"added": 1, "changed": 1, "removed": 1}
    # Nothing changed, nothing listed; a first release lists everything as added.
    assert rc.diff(old, old) == []
    first = rc.diff(None, old)
    assert first and {c["change"] for c in first} == {"added"}


def test_content_revision_follows_documents_and_attachments(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    row = PackRow("p", "t", "acme", "1.0.0", "Acme", "draft", pmo[0], "a" * 64, None, None, None, None, None)
    docs = copy.deepcopy(pmo[1])
    base = rc.content_revision(row, docs, [])
    assert rc.content_revision(row, copy.deepcopy(docs), []) == base
    docs["glossary"]["terms"][0]["meaning"] = "Changed."
    assert rc.content_revision(row, docs, []) != base
    assert rc.content_revision(row, pmo[1], ["b" * 64]) != base
