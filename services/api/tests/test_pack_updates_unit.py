"""Starter pack updates without a database (US-120): the three-way merge, change lists and summaries."""

from __future__ import annotations

from typing import Any

from tela_api.packs.updates import item_changes, merge_definition, merge_keyed, summarise, version_key


def m(key: str, label: str, agg: str = "count") -> dict[str, Any]:
    return {"key": key, "label": label, "entity": "projects", "expression": {"agg": agg}}


def definition(measures: list[dict[str, Any]], fields: list[str], **extra: Any) -> dict[str, Any]:
    return {"entities": [{"key": "projects", "label": "Projects", "source_object": "public.projects",
                          "fields": [{"key": f, "label": f.title(), "source_column": f} for f in fields]}],
            "links": [], "measures": measures, "dimensions": [], "settings": {}, **extra}


def test_version_key_orders_numerically() -> None:
    assert version_key("1.10.0") > version_key("1.9.3") > version_key("1.2.0")


def test_merge_keyed_applies_only_what_the_pack_changed() -> None:
    old = [m("a", "A"), m("b", "B"), m("c", "C"), m("gone", "Gone")]
    new = [m("a", "A"), m("b", "B2"), m("c", "C"), m("added", "Added")]
    base = [m("a", "A edited by the Admin"), m("b", "B"), m("gone", "Gone"), m("mine", "Admin's own")]
    out = {i["key"]: i["label"] for i in merge_keyed(base, old, new)}
    # Edit kept (the pack did not change "a"), pack change applied, drop applied, addition added, own item kept;
    # "c" was removed by the Admin and the pack did not change it, so it stays removed.
    assert out == {"a": "A edited by the Admin", "b": "B2", "mine": "Admin's own", "added": "Added"}
    # An item the Admin removed comes back when the pack changes it.
    assert [i["label"] for i in merge_keyed([], [m("c", "C")], [m("c", "C2")])] == ["C2"]


def test_merge_definition_fields_entities_and_settings() -> None:
    old = definition([m("open", "Open")], ["id", "status"])
    new = definition([m("open", "Open"), m("all", "All")], ["id", "status", "region"])
    new["entities"].append({"key": "requests", "label": "Requests", "source_object": "public.requests",
                            "fields": [{"key": "id", "label": "Id", "source_column": "id"}]})
    base = definition([m("open", "Open"), m("mine", "Mine")], ["id", "status", "personal"],
                      settings={"reporting_date": "2026-09-30"})
    base["entities"][0]["source_checksum"] = "abc"
    merged = merge_definition(base, old, new)
    assert [x["key"] for x in merged["measures"]] == ["open", "mine", "all"]
    projects = merged["entities"][0]
    assert [f["key"] for f in projects["fields"]] == ["id", "status", "personal", "region"]
    assert projects["source_checksum"] == "abc"  # attributes the pack did not change stay
    assert [e["key"] for e in merged["entities"]] == ["projects", "requests"]
    assert merged["settings"] == {"reporting_date": "2026-09-30"}
    assert base["measures"][-1]["key"] == "mine" and len(base["measures"]) == 2  # the input is not mutated
    # Nothing changed in the pack: the model is left exactly as it was.
    assert merge_definition(base, old, old) == base
    # The pack drops an entity: it leaves the model.
    assert merge_definition(base, new, old)["entities"][0]["key"] == "projects"
    assert len(merge_definition(base | {"entities": base["entities"] + new["entities"][1:]}, new, old)
               ["entities"]) == 1


def test_item_changes_and_summary() -> None:
    old = definition([m("open", "Open"), m("gone", "Gone")], ["id", "status"])
    new = definition([m("open", "Open", "sum"), m("all", "All")], ["id", "region"])
    changes = {(c["kind"], c["key"]): c["change"] for c in item_changes(old, new)}
    assert changes == {("measure", "open"): "changed", ("measure", "gone"): "removed",
                       ("measure", "all"): "added", ("field", "projects.status"): "removed",
                       ("field", "projects.region"): "added"}
    reports = [{"kind": "report", "key": "r", "title": "R", "change": "changed"}]
    text = summarise(reports + item_changes(old, new))
    assert text == ("starter reports: 1 changed; measures: 1 added, 1 changed, 1 removed; "
                    "fields: 1 added, 1 removed.")
    assert summarise([]) == "No changes for this connection."
