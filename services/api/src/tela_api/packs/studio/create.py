"""Ways to start a new pack draft (US-232; the Create menu).

Each way is a ``Creator`` registered in ``CREATORS`` under the name the API and the Create menu use. US-232 has
*blank* and *clone* (a plain copy of a pack the tenant can see: "Clone to edit" on a system pack). With
``options.connection_id`` *clone* is re-pointed onto that connection's schema (US-235, ``repoint.py``). US-236 adds
*model* (from an approved model, ``from_model.py``); US-237 adds *ai* (an empty draft for the AI pack author,
``author.py``, which drafts it stage by stage on the chosen connection).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.identity.principal import Principal
from tela_api.packs.studio import store
from tela_common.errors import ErrorCode, TelaError


@dataclass
class Draft:
    head: dict[str, Any]
    documents: dict[str, Any]
    based_on: str | None = None
    # What the method could not take, each with the reason (kind, key, reason), and where it came from.
    left_out: list[dict[str, str]] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreateArgs:
    pack_key: str
    title: str
    version: str
    from_pack_id: str | None
    options: dict[str, Any]


Creator = Callable[[AsyncConnection, Principal, CreateArgs], Awaitable[Draft]]


def blank_head(key: str, title: str, version: str) -> dict[str, Any]:
    return {"pack_key": key, "pack_version": version, "title": title,
            "released_at": datetime.now(UTC).strftime("%Y-%m-%dT00:00:00Z"), "signature": {"min_confidence": 0.5},
            "entities": [], "links": [], "measures": [], "dimensions": []}


async def blank(conn: AsyncConnection, principal: Principal, args: CreateArgs) -> Draft:
    return Draft(blank_head(args.pack_key, args.title, args.version), {})


async def clone(conn: AsyncConnection, principal: Principal, args: CreateArgs) -> Draft:
    """A copy of another pack's manifest and knowledge under a new key, as version ``args.version``; re-pointed onto
    a connection when the options name one (US-235)."""
    if not args.from_pack_id:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the pack to clone.")
    src = await store.load(conn, args.from_pack_id)
    docs = copy.deepcopy(await store.documents(conn, src.id))
    head = store.as_written(copy.deepcopy(src.manifest), docs)
    head.update(pack_key=args.pack_key, pack_version=args.version, title=args.title,
                released_at=datetime.now(UTC).strftime("%Y-%m-%dT00:00:00Z"))
    if args.options.get("connection_id"):
        from tela_api.packs.studio.repoint import repoint  # repoint imports the pack router, which this must not

        head, docs = await repoint(conn, src, head, docs, args.options)
    return Draft(head, docs, based_on=src.id)


async def model(conn: AsyncConnection, principal: Principal, args: CreateArgs) -> Draft:
    """From a connection's approved model (US-236; ``from_model.py``)."""
    from tela_api.packs.studio import from_model

    return await from_model.create(conn, principal, args)


async def ai(conn: AsyncConnection, principal: Principal, args: CreateArgs) -> Draft:
    """An empty draft for the AI pack author (US-237), on a connection whose schema has been read and a template
    pack (PMO by default). ``source`` names both, so the Create menu opens the author on them."""
    from tela_api.packs.studio import author

    connection_id = str(args.options.get("connection_id") or "")
    if len(connection_id) != 36:
        raise TelaError(ErrorCode.VALIDATION_ERROR, "Choose the connection whose schema AI drafts the pack from.",
                        {"field": "connection_id"})
    found = await conn.execute(text("SELECT 1 FROM conn.connections WHERE id = CAST(:c AS uuid) AND deleted_at IS "
                                    "NULL"), {"c": connection_id})
    if found.first() is None:
        raise TelaError(ErrorCode.NOT_FOUND, "Connection not found.")
    await author.catalog(conn, connection_id)  # the schema must have been read
    template = await author.template(conn, args.options.get("template_pack_id") or None)
    return Draft(blank_head(args.pack_key, args.title, args.version), {},
                 source={"connection_id": connection_id, "template_pack_id": template.id if template else None})


CREATORS: dict[str, Creator] = {
    "blank": blank,
    "clone": clone,
    "model": model,
    "ai": ai,
}
# Shown in the Create menu but not built yet: the story that adds each one.
PLANNED: dict[str, str] = {}
