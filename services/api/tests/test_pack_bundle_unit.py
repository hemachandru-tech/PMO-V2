"""One YAML file for a draft's measures, dimensions, report blueprints and starter dashboards (Pack Studio import),
without a database.

The PMO pack's own parts export and import back unchanged; ``merge`` adds and replaces by key while ``replace``
makes each part in the file the whole part; a file's blueprints may use the file's new measures and sit on its new
dashboard; every error names its path in the file and the YAML is refused before anything is applied.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from tela_api.packs.manifest import DEFAULT_PACKS_DIR
from tela_api.packs.studio import bulk
from tela_api.packs.studio.transfer import dump
from tela_api.packs.studio.validate import validate
from tela_common.errors import TelaError

PMO = DEFAULT_PACKS_DIR / "pmo"


@pytest.fixture(scope="module")
def pmo() -> tuple[dict[str, Any], dict[str, Any]]:
    head = yaml.safe_load((PMO / "manifest.yaml").read_text())
    doc = yaml.safe_load((PMO / "report_blueprints.yaml").read_text())
    return head, doc


def _bp(doc: dict[str, Any], bid: str) -> dict[str, Any]:
    return copy.deepcopy(next(b for b in doc["blueprints"] if b["id"] == bid))


def test_the_pack_exports_and_imports_back_unchanged(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    text = dump(bulk.bundle_of(head, doc))
    bundle = bulk.parse(text)
    assert list(bundle) == ["measures", "dimensions", "reports", "tones", "dashboards"]
    for replace in (False, True):
        out = bulk.apply(head, doc, bundle, replace=replace)
        assert out.errors == []
        assert out.head["measures"] == head["measures"] and out.head["dashboards"] == head["dashboards"]
        assert out.doc["blueprints"] == doc["blueprints"]
        assert all(not c["added"] and not c["updated"] and not c["removed"] for c in out.changes.values())
        assert out.changes["measures"]["unchanged"] == len(head["measures"])


def test_merge_adds_and_replaces_by_key(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    new_measure = {"key": "gulf_projects", "label": "Gulf projects", "entity": "projects",
                   "plain_rule": "Projects in the Gulf.", "expression": {"agg": "count"}}
    relabelled = {**head["measures"][1], "label": "Live projects"}
    new_dim = {"key": "region_again", "label": "Region again", "entity": "projects", "field": "region"}
    bp = {**_bp(doc, "projects_overview"), "id": "gulf_overview", "title": "Gulf overview", "tab": "Gulf",
          "dashboard": "gulf", "kpis": ["gulf_projects"], "filters": [{"ref": "region_again", "op": "in",
                                                                        "values": ["KSA"]}]}
    out = bulk.apply(head, doc, {"measures": [relabelled, new_measure], "dimensions": [new_dim], "reports": [bp],
                                 "dashboards": {"gulf": {"title": "Gulf", "filters": ["region_again"]}}})
    assert out.errors == []
    keys = [m["key"] for m in out.head["measures"]]
    assert keys == [m["key"] for m in head["measures"]] + ["gulf_projects"]
    assert out.head["measures"][1]["label"] == "Live projects"
    assert out.changes["measures"] == {"added": ["gulf_projects"], "updated": [head["measures"][1]["key"]],
                                       "removed": [], "unchanged": 0}
    # The new blueprint sits on the file's new dashboard, which keeps the pack's others.
    assert out.head["dashboards"]["gulf"] == {"title": "Gulf", "reports": ["gulf_overview"], "filters": ["region_again"]}
    assert set(out.head["dashboards"]) == {*head["dashboards"], "gulf"}
    assert out.changes["reports"]["added"] == ["gulf_overview"] and out.changes["dashboards"]["added"] == ["gulf"]
    assert len(out.doc["blueprints"]) == len(doc["blueprints"]) + 1
    report = validate(out.head, {"report_blueprints": out.doc})
    assert report.errors == []


def test_replace_makes_each_part_in_the_file_the_whole_part(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    keep = _bp(doc, "projects_overview")
    out = bulk.apply(head, doc, {"reports": [keep], "dashboards": {"projects": {"title": "Projects"}}}, replace=True)
    assert out.errors == []
    assert [b["id"] for b in out.doc["blueprints"]] == ["projects_overview"]
    assert out.head["dashboards"] == {"projects": {"title": "Projects", "reports": ["projects_overview"],
                                                   "filters": head["dashboards"]["projects"]["filters"]}}
    assert out.head["measures"] == head["measures"]  # a part left out of the file is kept
    assert "requests" in out.changes["dashboards"]["removed"]
    assert len(out.changes["reports"]["removed"]) == len(doc["blueprints"]) - 1


def test_tabs_order_comes_from_the_dashboard(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    tabs = list(reversed(head["dashboards"]["projects"]["reports"]))
    out = bulk.apply(head, doc, {"dashboards": {"projects": {"title": "PMO — Projects", "reports": tabs}}})
    assert out.errors == [] and out.head["dashboards"]["projects"]["reports"] == tabs
    assert out.changes["dashboards"] == {"added": [], "updated": ["projects"], "removed": [], "unchanged": 0}


def test_errors_name_their_path_in_the_file(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    bad_bp = {**_bp(doc, "lifecycle"), "kpis": ["no_such_measure"]}
    out = bulk.apply(head, doc, {
        "measures": [{"label": "No key"}, head["measures"][0], head["measures"][0]],
        "reports": [_bp(doc, "projects_overview"), bad_bp],
        "dashboards": {"projects": {"title": "P", "filters": ["no_such_dimension"]}, "Bad Key": {"title": "x"},
                       "nameless": {}},
    })
    paths = {e["path"] for e in out.errors}
    assert {"measures[0]", "measures[2].key", "reports[1].kpis.0", "dashboards.projects.filters.0",
            "dashboards.Bad Key", "dashboards.nameless"} <= paths


def test_a_data_gap_listed_as_a_tab_stays_without_one(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    gap = {"id": "data_trust", "title": "Data trust", "dashboard": "owners", "triggers": ["data trust"],
           "unavailable": ["a trust score"]}
    live = {**_bp(doc, "projects_overview"), "id": "owner_overview", "dashboard": "owners"}
    out = bulk.apply(head, doc, {"reports": [gap, live],
                                 "dashboards": {"owners": {"title": "Owners", "reports": ["data_trust", "owner_overview"]}}})
    assert out.errors == []
    assert out.head["dashboards"]["owners"]["reports"] == ["owner_overview"]
    assert "data_trust" in [b["id"] for b in out.doc["blueprints"]]
    assert out.warnings == [{"path": "dashboards.owners.reports.0",
                             "message": "data_trust is a data gap: it stays in the pack without a tab."}]


def test_a_missing_dashboard_says_where_to_add_it(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    out = bulk.apply(head, doc, {"reports": [{**_bp(doc, "projects_overview"), "dashboard": "nowhere"}]})
    assert out.errors == [{"path": "reports[0].dashboard",
                           "message": "the pack has no dashboard nowhere: add nowhere under dashboards in the file"}]


def test_replacing_dashboards_keeps_no_blueprint_homeless(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    out = bulk.apply(head, doc, {"dashboards": {"projects": {"title": "Projects"}}}, replace=True)
    assert any(e["path"] == "dashboards" and "requests_overview" in e["message"] for e in out.errors)


@pytest.mark.parametrize(("text", "says"), [
    ("measures: [a: b", "valid YAML"),
    ("- just\n- a list\n", "mapping"),
    ("entities: []\n", "can't be imported"),
])
def test_a_file_that_isnt_a_bundle_is_refused(text: str, says: str) -> None:
    with pytest.raises(TelaError) as err:
        bulk.parse(text)
    assert says in err.value.message


def test_an_empty_file_changes_nothing(pmo: tuple[dict[str, Any], dict[str, Any]]) -> None:
    head, doc = pmo
    out = bulk.apply(head, doc, bulk.parse(""))
    assert out.errors == [] and out.changes == {} and out.head == head
