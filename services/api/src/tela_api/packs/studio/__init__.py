"""Pack Studio API (ENH-025, US-232; FEAT-075): ``/api/v1/packs``, one module per area.

* ``catalogue`` — list, get, versions, documents, create (``create.CREATORS``), delete a draft
* ``drafts`` — edit a draft's manifest sections (ETag), validate
* ``transfer`` — export / import as the ``packs/<key>/`` folder, zipped
* ``bulk`` — one YAML file for a draft's measures, dimensions, report blueprints and starter dashboards: export it,
  check it and import it (merged by key or each part replaced)
* ``trial`` — try a measure on a connection
* ``from_model`` — what a pack from an approved model would hold (US-236; the creator is ``create.CREATORS['model']``)
* ``knowledge`` — the knowledge documents of a draft, by kind, with each item's checks (US-233)
* ``attachments`` — context documents attached to a pack, copied to a connection's knowledge on install (US-233)
* ``repoint`` — clone a pack onto a connection's schema (US-235)
* ``author`` — the AI pack author: stages drafted by rules and the model, reviewed item by item (US-237; the creator
  is ``create.CREATORS['ai']``)
* ``release`` — release checks, the diff against the previous version, submit for review, send back, release (four
  eyes) and the next draft version of a released one (US-238; the checks are ``release_checks``)

A later story adds its own module and one line in ``ROUTERS``. Every route needs ``packs.author`` (DEC-26), except
release's review routes, which a certifier (``certification.decide``) may use too.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tela_api.packs.studio import (
    attachments,
    author,
    bulk,
    catalogue,
    drafts,
    from_model,
    knowledge,
    release,
    repoint,
    transfer,
    trial,
)

# Order matters where paths overlap: ``/import`` before ``/{pack_id}``, ``…/knowledge/attachments`` before
# ``…/knowledge/{kind}``.
ROUTERS: list[APIRouter] = [
    from_model.router,
    transfer.router,
    bulk.router,
    drafts.router,
    trial.router,
    attachments.router,
    knowledge.router,
    repoint.router,
    author.router,
    release.router,
    catalogue.router,
]


def item_models() -> dict[str, Any]:
    """The pack item models the Studio's forms are built on (Zod in ``apps/web/src/api/schemas.ts``), by the name
    they take in the OpenAPI document."""
    from tela_api.packs.manifest import PackEntity, PackField
    from tela_query_spec.model import Dimension, Measure, ModelLink

    return {"PackEntity": PackEntity, "PackField": PackField, "PackLink": ModelLink, "PackMeasure": Measure,
            "PackDimension": Dimension}


def add_item_schemas(doc: dict[str, Any]) -> dict[str, Any]:
    """Put the pack item models into an OpenAPI document's components (the routes take sections as plain JSON, so
    the Studio's own checks can report every error with its path). Names already there are left alone."""
    components = doc.setdefault("components", {}).setdefault("schemas", {})
    for name, model in item_models().items():
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for def_name, sub in schema.pop("$defs", {}).items():
            components.setdefault(def_name, sub)
        components.setdefault(name, schema)
    return doc
