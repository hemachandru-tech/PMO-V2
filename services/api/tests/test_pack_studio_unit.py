"""Pack Studio checks without a database (US-232): error paths, reference warnings, the manifest as written, zips."""

from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest

from tela_api.packs.manifest import DEFAULT_PACKS_DIR, source_manifest
from tela_api.packs.source import DbPackSource, FilePackSource
from tela_api.packs.studio import store
from tela_api.packs.studio.transfer import dump, folder_zip, read_zip
from tela_api.packs.studio.validate import path_of, validate
from tela_common.errors import TelaError

PMO = FilePackSource(DEFAULT_PACKS_DIR / "pmo")


def _blank(**kw: object) -> dict[str, object]:
    return {"pack_key": "acme_ops", "pack_version": "0.1.0", "title": "Acme", "released_at": "2026-11-06",
            "signature": {"primary_entity": "t"}, "entities": [
                {"key": "t", "label": "T", "object_candidates": ["t"], "primary_key": ["id"],
                 "fields": [{"key": "id", "label": "Id", "data_type": "text", "columns": ["id"]}]}],
            "measures": [], **kw}


def test_path_of_reads_like_yaml() -> None:
    assert path_of("", ("measures", 3, "expression", "agg")) == "measures[3].expression.agg"
    assert path_of("glossary", ("terms", 0, "means")) == "glossary.terms[0].means"


def test_the_pmo_pack_passes_clean() -> None:
    report = validate(PMO.head(), PMO.documents())
    assert report.errors == [] and report.warnings == []


def test_errors_name_one_shape_and_their_path() -> None:
    bad = {"key": "m", "label": "M", "entity": "t", "plain_rule": "r", "expression": {"agg": "tally"}}
    report = validate(_blank(measures=[bad], pack_key="Bad Key"), {"glossary": {"terms": [{"term": "x"}]}})
    assert {(e["path"], e["message"][:20]) for e in report.errors} == {
        ("measures[0].expression.agg", "Input should be 'cou"),  # the aggregate's error, not arithmetic's too
        ("glossary.version", "Field required"), ("glossary.terms[0].means", "Field required"),
        ("pack_key", "Use 2 to 41 lower-ca")}


def test_references_that_do_not_resolve_are_warnings() -> None:
    head = _blank(
        signature={"primary_entity": "nope"},
        links=[{"from_entity": "t", "to_entity": "u", "from_fields": ["id"], "to_fields": ["id"],
               "cardinality": "many_to_one"}],
        measures=[{"key": "m", "label": "M", "entity": "t", "plain_rule": "r",
                   "expression": {"agg": "sum", "field": "cost"}}],
        dimensions=[{"key": "d", "label": "D", "entity": "t", "field": "region"}])
    report = validate(head, {})
    assert report.errors == []
    assert [w["path"] for w in report.warnings] == ["signature.primary_entity", "links[0].to_entity",
                                                    "measures[0].expression", "dimensions[0].field"]


def test_duplicate_keys_are_errors() -> None:
    m = {"key": "m", "label": "M", "entity": "t", "plain_rule": "r", "expression": {"agg": "count"}}
    report = validate(_blank(measures=[m, m]), {})
    assert report.errors == [{"path": "measures[1].key", "message": "Another measure already has the key 'm'."}]


def test_as_written_puts_reports_from_back_with_the_same_reports() -> None:
    """``load-packs`` stores the expanded reports; a clone takes them from the blueprints again, unchanged."""
    _, stored, _ = source_manifest(PMO)  # as the CLI stores it
    stored = json.loads(json.dumps(stored, default=str))
    docs = json.loads(json.dumps(PMO.documents(), default=str))
    written = store.as_written(copy.deepcopy(stored), docs)
    assert written["reports_from"] == "report_blueprints.yaml" and "reports" not in written
    again, _, _ = source_manifest(DbPackSource("x", "pmo_tpg", "2.1.0", None, "draft", written, docs))
    assert again.reports == stored["reports"] and again.unavailable_reports == stored["unavailable_reports"]
    # A manifest whose own reports differ keeps them.
    mine = {**stored, "reports": {**stored["reports"], "mine": {"title": "Mine", "widgets": []}}}
    assert store.as_written(mine, docs)["reports"] == {"mine": {"title": "Mine", "widgets": []}}


def test_zip_round_trip_and_refusals() -> None:
    data = folder_zip("pmo_tpg", PMO.head(), PMO.documents())
    head, docs, skipped = read_zip(data)
    assert head == PMO.head() and docs == PMO.documents() and skipped == []
    assert validate(head, docs).errors == []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", dump(_blank()))  # files at the root are read too
        z.writestr("README.md", "skipped")
    head, docs, skipped = read_zip(buf.getvalue())
    assert head["pack_key"] == "acme_ops" and docs == {} and skipped == ["README.md"]
    for bad in (b"plain text", _zip({"a/x.yaml": "x: 1"}), _zip({"a/manifest.yaml": "- a list"}),
                _zip({"a/manifest.yaml": "k: [unclosed"})):
        with pytest.raises(TelaError):
            read_zip(bad)


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()
