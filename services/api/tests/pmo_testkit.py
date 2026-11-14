"""PMO world for P1 integration tests: tenant, personas, PostgreSQL connection, pack-installed approved model."""

from __future__ import annotations

import argparse
import copy
import functools
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psycopg
import yaml
from api_testkit import ISSUER, OWNER_URL, RESET_TABLES, bootstrap
from psycopg.types.json import Jsonb

AS_OF = "2026-09-30"

GROUPS = {"TELA-Admins": "admin", "TELA-Creators": "creator", "TELA-Viewers": "viewer",
          "TELA-CIO-Nomsa": "cio", "TELA-CIO-Karim": "cio"}


def h(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


# What a release by the API records (US-238); the database refuses a tenant release without it (four eyes).
REVIEWED = "submitted_at = now(), submitted_by = gen_random_uuid(), released_by = gen_random_uuid(), "

@dataclass
class World:
    tenant_id: str
    connection_id: str
    tokens: dict[str, str]
    model_id: str | None = None
    version_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def spec(self, measure: str, **kw: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"model_version_id": self.version_id, "connection_id": self.connection_id,
                               "measure": {"ref": f"measure:{measure}"}}
        for key, value in kw.items():
            out[key] = value
        return out


def load_packs(owner_dsn: str) -> None:
    from tela_api.cli import load_packs as _load

    os.environ["TELA_MIGRATE_DATABASE_URL"] = owner_dsn
    _load(argparse.Namespace(dir=None))


def load_packs_open_by_status(owner_dsn: str) -> None:
    """``load_packs`` with the PMO pack's open = Status Active, as pack 2.1.0 had RULE-026 (DEC-24): the mapping tests
    of US-199 to US-201 need measures that filter Status by its values. Pack tables are reset after each test."""
    from tela_api.cli import load_packs as _load

    os.environ["TELA_MIGRATE_DATABASE_URL"] = owner_dsn
    _load(argparse.Namespace(dir=str(_open_by_status_packs())))


@functools.cache
def _open_by_status_packs() -> Path:
    """A copy of ``packs/`` whose PMO manifest has the 2.1.0 open rule (released packs are frozen in the database)."""
    from pmo_oracle import open_by_status

    from tela_api.packs.manifest import DEFAULT_PACKS_DIR

    root = Path(tempfile.mkdtemp(prefix="tela-packs-"))
    shutil.copytree(DEFAULT_PACKS_DIR, root, dirs_exist_ok=True)
    path = root / "pmo" / "manifest.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["measures"] = open_by_status(raw["measures"])
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return root


def set_scope_attributes(owner: psycopg.Connection, group: str, attrs: str) -> None:
    owner.execute(
        "UPDATE iam.group_role_mappings m SET scope_attributes = %s::jsonb FROM iam.idp_groups g "
        "WHERE g.id = m.group_id AND g.external_group_id = %s", (attrs, group))


async def _no_failures(**_: Any) -> list[dict[str, Any]]:
    return []


async def create_connection(api: httpx.AsyncClient, token: str, src: dict[str, Any], *, user: str = "ro",
                            name: str = "TPG Postgres", schemas: list[str] | None = None) -> str:
    login, password = src[user]
    resp = await api.post("/api/v1/connections", headers=h(token), json={
        "name": name, "engine": "postgres", "host": src["host"], "port": src["port"], "database": src["database"],
        "username": login, "password": password, "tls": {"mode": "disable"},
        "approved_schemas": schemas or ["public"]})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["connection_id"])


def open_by_status_draft(owner: psycopg.Connection) -> Callable[[World], None]:
    """``before_approve``: the installed draft with open = Status Active, as pack 2.1.0 had RULE-026 (DEC-24)."""
    from pmo_oracle import open_by_status

    def change(world: World) -> None:
        row = owner.execute("SELECT id, definition FROM model.model_versions WHERE data_model_id = %s AND "
                            "status = 'draft'", (world.model_id,)).fetchone()
        assert row is not None
        row[1]["measures"] = open_by_status(row[1]["measures"])
        owner.execute("UPDATE model.model_versions SET definition = %s WHERE id = %s", (Jsonb(row[1]), row[0]))
    return change


async def setup_world(api: httpx.AsyncClient, owner: psycopg.Connection, make_token: Callable[..., str],
                      src: dict[str, Any], *, approve: bool = True, policies: bool = True,
                      before_approve: Callable[[World], None] | None = None,
                      override_note: str | None = None) -> World:
    """``before_approve``: changes the installed draft before it is approved (US-205: EXEC-115's mapping), with
    ``override_note`` when that draft fails a blocking approval check (US-199)."""
    key = None
    if before_approve is None and override_note is None:
        key = (OWNER_URL, json.dumps(src, sort_keys=True, default=str), approve, policies)
        if key in _WORLDS:
            return _restore(owner, make_token, _WORLDS[key])
    world = await _build_world(api, owner, make_token, src, approve=approve, policies=policies,
                               before_approve=before_approve, override_note=override_note)
    if key is not None:
        _WORLDS[key] = _capture(owner, world)
    return world


def _tokens(make_token: Callable[..., str]) -> dict[str, str]:
    return {
        "admin": make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin"),
        "creator": make_token("creator-1", groups=["TELA-Creators"], name="Cleo Creator"),
        "viewer": make_token("viewer-1", groups=["TELA-Viewers"], name="Vic Viewer"),
        "nomsa": make_token("nomsa-1", groups=["TELA-Viewers", "TELA-CIO-Nomsa"], name="Nomsa Osei"),
        "karim": make_token("karim-1", groups=["TELA-Viewers", "TELA-CIO-Karim"], name="Karim Haddad"),
    }


# A built world is ~3 s (pack load, install, approve, policies) and ~190 tests ask for the same one. The first build
# in a process is kept as rows (binary COPY of every non-empty reset table plus the tenant) and later tests get those
# rows back after their reset (~0.1 s). Worlds changed by ``before_approve`` / ``override_note`` are always built.
# The connection's credential lives in the file secrets store, which some tests empty: it is kept and written back too.
_SNAPSHOT_TABLES = (*RESET_TABLES, "iam.tenants")
_Snapshot = tuple[World, list[tuple[str, str, bytes]], dict[str, bytes]]
_WORLDS: dict[tuple[Any, ...], _Snapshot] = {}
# Called on every restore. A restored world reuses its connection id, so process-wide caches keyed by that id (the
# session runner's column and read-only caches, 60 s) must not carry a source shape an earlier test left behind.
RESTORE_HOOKS: list[Callable[[], None]] = []


def _columns(owner: psycopg.Connection, table: str) -> str:
    rows = owner.execute("SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
                         "AND NOT attisdropped AND attgenerated = '' ORDER BY attnum", (table,)).fetchall()
    return ", ".join(f'"{r[0]}"' for r in rows)


def _secrets_dir() -> Path:
    return Path(os.environ["TELA_SECRETS_DIR"])


def _capture(owner: psycopg.Connection, world: World) -> _Snapshot:
    tables = []
    for table in _SNAPSHOT_TABLES:
        if owner.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone()[0]:  # type: ignore[index]
            cols = _columns(owner, table)
            with owner.cursor().copy(f"COPY (SELECT {cols} FROM {table}) TO STDOUT (FORMAT binary)") as cp:
                data = b"".join(bytes(chunk) for chunk in cp)
            tables.append((table, cols, data))
    secrets = {p.name: p.read_bytes() for p in _secrets_dir().iterdir() if p.is_file()}
    return copy.deepcopy(world), tables, secrets


def _restore(owner: psycopg.Connection, make_token: Callable[..., str], snapshot: _Snapshot) -> World:
    world, tables, secrets = snapshot
    folder = _secrets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in secrets.items():
        (folder / name).write_bytes(body)
    os.environ["TELA_MIGRATE_DATABASE_URL"] = os.environ["TELA_TEST_OWNER_DATABASE_URL"]  # as load_packs leaves it
    with owner.transaction():
        owner.execute("SET LOCAL session_replication_role = replica")  # rows as they were: no FK order, no triggers
        for table, cols, data in tables:
            with owner.cursor().copy(f"COPY {table} ({cols}) FROM STDIN (FORMAT binary)") as cp:
                cp.write(data)
    for hook in RESTORE_HOOKS:
        hook()
    out = copy.deepcopy(world)
    out.tokens = _tokens(make_token)  # fresh expiry
    return out


async def _build_world(api: httpx.AsyncClient, owner: psycopg.Connection, make_token: Callable[..., str],
                       src: dict[str, Any], *, approve: bool, policies: bool,
                       before_approve: Callable[[World], None] | None, override_note: str | None) -> World:
    tenant = bootstrap(owner, "acme", ISSUER, GROUPS)
    set_scope_attributes(owner, "TELA-CIO-Nomsa", '{"cio": ["Nomsa Osei"]}')
    set_scope_attributes(owner, "TELA-CIO-Karim", '{"cio": ["Karim Haddad"]}')
    tokens = _tokens(make_token)
    admin = tokens["admin"]
    cid = await create_connection(api, admin, src)
    test = await api.post(f"/api/v1/connections/{cid}/test", headers=h(admin))
    assert test.status_code == 200 and test.json()["status"] == "connected", test.text
    intro = await api.post(f"/api/v1/connections/{cid}/introspect", headers=h(admin))
    assert intro.status_code == 202 and intro.json()["status"] == "connected", intro.text
    world = World(tenant, cid, tokens)
    if not approve:
        return world
    load_packs(os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    packs = (await api.get("/api/v1/starter-packs", headers=h(admin))).json()["items"]
    pack_id = next(p["id"] for p in packs if p["pack_key"] == "pmo_tpg")
    install = await api.post(f"/api/v1/starter-packs/{pack_id}/install", headers=h(admin),
                             json={"connection_id": cid, "reporting_date": AS_OF})
    assert install.status_code == 202, install.text
    world.model_id = install.json()["model_id"]
    world.extra["install"] = install.json()
    if before_approve is not None:
        before_approve(world)
    draft = await api.get(f"/api/v1/models/{world.model_id}/draft", headers=h(admin))
    # The approval check runs every pack measure at the source (~7 s of each world); the approval
    # tests (test_connections_model_api, test_pmo_pack_api, ...) cover it, so the shared world skips it.
    service = api.app.state.query_service  # type: ignore[attr-defined]
    service.dry_run = _no_failures
    try:
        approve_resp = await api.post(f"/api/v1/models/{world.model_id}/approve", headers=h(admin),
                                      json={"draft_etag": draft.json()["draft_etag"],
                                            **({"override_note": override_note} if override_note else {})})
    finally:
        del service.dry_run
    assert approve_resp.status_code == 201, approve_resp.text
    world.version_id = approve_resp.json()["model_version_id"]
    if policies:
        for entity in ("projects", "project_requests"):
            for role, body in (
                ("admin", {"all_rows": True, "description": "Admin: all rows"}),
                ("creator", {"predicates": [{"field": "region", "op": "in", "values": ["KSA", "GCC - UAE"]}],
                             "description": "Gulf regions"}),
                ("cio", {"predicates": [{"field": "cio", "op": "=", "attr": "cio"}], "description": "Own CIO"}),
            ):
                resp = await api.post("/api/v1/admin/scope-policies", headers=h(admin), json={
                    "connection_id": cid, "entity": entity, "role": role, **body})
                assert resp.status_code == 201, resp.text
    return world


async def run(api: httpx.AsyncClient, token: str, spec: dict[str, Any], **kw: Any) -> httpx.Response:
    return await api.post("/api/v1/query/run", headers=h(token), json={"spec": spec, **kw})


def value(resp: httpx.Response) -> Any:
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["rows"], body
    return body["rows"][0][-1]


def as_map(resp: httpx.Response) -> dict[Any, Any]:
    assert resp.status_code == 200, resp.text
    return {tuple(r[:-1]) if len(r) > 2 else r[0]: r[-1] for r in resp.json()["rows"]}
