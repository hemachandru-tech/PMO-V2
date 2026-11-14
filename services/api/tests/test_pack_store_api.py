"""Packs and their knowledge stored per tenant (ENH-025, US-230; FEAT-075).

A tenant reads the system packs and its own, never another tenant's; the application role writes its own drafts
only (never a released pack, never a system pack); ``tela-admin load-packs`` stores each pack's knowledge files as
documents, idempotently, and refuses changed content under a released version; migration 0048 goes up and down.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
import yaml
from api_testkit import APP_URL, ISSUER, bootstrap, needs_db, reset_data

from tela_api.cli import load_packs
from tela_api.knowledge.guidance import load_blueprints, load_glossary, load_recipes, load_rules, load_tones
from tela_api.knowledge.patterns import load_patterns
from tela_api.knowledge.values import load_pack_synonyms
from tela_api.packs.manifest import DEFAULT_PACKS_DIR, load_manifest, source_manifest
from tela_api.packs.source import KINDS, DbPackSource, FilePackSource, document_sha, file_source, resolve
from tela_common.ids import new_id

pytestmark = [needs_db, pytest.mark.integration]
REPO = Path(__file__).resolve().parents[3]
PMO = DEFAULT_PACKS_DIR / "pmo"


@pytest.fixture
def clean(owner_conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    reset_data(owner_conn)
    yield owner_conn
    reset_data(owner_conn)


def _load(packs_dir: Path | None = None) -> dict[str, dict[str, object]]:
    os.environ["TELA_MIGRATE_DATABASE_URL"] = os.environ["TELA_TEST_OWNER_DATABASE_URL"]
    from contextlib import redirect_stdout
    from io import StringIO

    out = StringIO()
    with redirect_stdout(out):
        load_packs(argparse.Namespace(dir=str(packs_dir) if packs_dir else None))
    return {p["pack"]: p for p in json.loads(out.getvalue())["packs"]}


def _pack(owner: psycopg.Connection, tenant: str | None, key: str, *, status: str = "draft",
          docs: dict[str, dict[str, object]] | None = None) -> str:
    pid = new_id()
    head = {"pack_key": key, "pack_version": "0.1.0", "title": key.title(), "released_at": "2026-11-05",
            "signature": {}, "entities": [], "measures": []}
    owner.execute("INSERT INTO model.starter_packs (id, tenant_id, pack_key, pack_version, title, detection_signature, "
                  "manifest, manifest_sha256, released_at, status) VALUES (%s, %s, %s, '0.1.0', %s, '{}', %s, %s, "
                  "now(), %s)", (pid, tenant, key, key.title(), json.dumps(head), document_sha(head), status))
    for kind, body in (docs or {}).items():
        owner.execute("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES "
                      "(%s, %s, %s, %s, %s, %s)", (new_id(), pid, tenant, kind, json.dumps(body), document_sha(body)))
    return pid


def _app(tenant: str) -> psycopg.Connection:
    conn = psycopg.connect(APP_URL or "", autocommit=True)
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
    return conn


def test_load_packs_stores_knowledge_documents_idempotently(clean: psycopg.Connection) -> None:
    first = _load()
    assert first["pmo_tpg"]["status"] == "loaded"
    row = clean.execute("SELECT id, tenant_id, status, manifest, manifest_sha256 FROM model.starter_packs "
                        "WHERE pack_key = 'pmo_tpg'").fetchone()
    assert row is not None and row[1] is None and row[2] == "released"
    _, raw, sha = load_manifest(PMO / "manifest.yaml")
    assert row[3] == json.loads(json.dumps(raw, default=str)) and row[4] == sha
    docs = dict(clean.execute("SELECT kind, body FROM model.pack_documents WHERE pack_id = %s", (row[0],)).fetchall())
    files = FilePackSource(PMO).documents()
    assert set(docs) == set(files) == set(KINDS)  # the PMO pack ships every kind
    assert all(docs[k] == json.loads(json.dumps(files[k], default=str)) for k in KINDS)
    # Loading again changes nothing.
    assert _load()["pmo_tpg"]["status"] == "unchanged"
    assert clean.execute("SELECT count(*) FROM model.pack_documents").fetchone()[0] == len(KINDS)
    # A version loaded before migration 0048 (no documents) gets them on the next load.
    clean.execute("DELETE FROM model.pack_documents WHERE kind IN ('glossary', 'gold_examples')")
    again = _load()["pmo_tpg"]
    assert again["status"] == "documents_added" and again["documents"] == ["glossary", "gold_examples"]


def test_released_content_is_frozen(clean: psycopg.Connection, tmp_path: Path) -> None:
    folder = tmp_path / "pmo"
    shutil.copytree(PMO, folder, ignore=shutil.ignore_patterns("fixtures"))
    _load(tmp_path)
    # A knowledge file changed under the same pack version is refused, like a changed manifest.
    rules = yaml.safe_load((folder / "chart_patterns.yaml").read_text())
    rules["patterns"][0]["title"] = "Changed without a new version"
    (folder / "chart_patterns.yaml").write_text(yaml.safe_dump(rules, sort_keys=False))
    with pytest.raises(SystemExit, match="chart_patterns; bump pack_version"):
        _load(tmp_path)
    # Even the owner role cannot rewrite a released pack or its documents.
    with pytest.raises(psycopg.errors.RaiseException, match="frozen"):
        clean.execute("UPDATE model.pack_documents SET body = '{}' WHERE kind = 'chart_patterns'")
    with pytest.raises(psycopg.errors.RaiseException, match="frozen"):
        clean.execute("UPDATE model.starter_packs SET title = 'Other' WHERE pack_key = 'pmo_tpg'")
    # Retiring a released version is the one change allowed.
    clean.execute("UPDATE model.starter_packs SET status = 'retired' WHERE pack_key = 'pmo_tpg'")


def test_a_tenant_reads_system_packs_and_its_own(clean: psycopg.Connection) -> None:
    _load()
    a, b = bootstrap(clean, "acme", ISSUER, {}), bootstrap(clean, "globex", ISSUER + "-globex", {})
    pa = _pack(clean, a, "acme_ops", docs={"glossary": {"version": "0.1.0", "terms": []}})
    pb = _pack(clean, b, "globex_ops", docs={"glossary": {"version": "0.1.0", "terms": []}})
    with _app(a) as app:
        keys = {r[0] for r in app.execute("SELECT pack_key FROM model.starter_packs")}
        assert keys == {"pmo_tpg", "acme_ops"}
        assert app.execute("SELECT count(*) FROM model.starter_packs WHERE id = %s", (pb,)).fetchone()[0] == 0
        assert app.execute("SELECT count(*) FROM model.pack_documents WHERE pack_id = %s", (pb,)).fetchone()[0] == 0
        kinds = app.execute("SELECT count(*) FROM model.pack_documents WHERE pack_id = %s", (pa,)).fetchone()[0]
        assert kinds == 1
        assert app.execute("SELECT count(*) FROM model.pack_documents WHERE tenant_id IS NULL").fetchone()[0] == \
            len(KINDS)
    with _app(b) as app:
        assert {r[0] for r in app.execute("SELECT pack_key FROM model.starter_packs")} == {"pmo_tpg", "globex_ops"}


def test_the_app_role_writes_its_own_drafts_only(clean: psycopg.Connection) -> None:
    _load()
    a, b = bootstrap(clean, "acme", ISSUER, {}), bootstrap(clean, "globex", ISSUER + "-globex", {})
    released = _pack(clean, a, "acme_live", status="released")
    pmo = clean.execute("SELECT id FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()[0]
    head = json.dumps({"pack_key": "acme_ops"})
    insert = ("INSERT INTO model.starter_packs (id, tenant_id, pack_key, pack_version, title, detection_signature, "
              "manifest, manifest_sha256, released_at, status, based_on_pack_id) VALUES (%s, %s, %s, '0.1.0', 'Ops', "
              "'{}', %s, 'x', now(), %s, %s)")
    with _app(a) as app:
        draft = new_id()
        app.execute(insert, (draft, a, "acme_ops", head, "draft", pmo))  # a clone of PMO
        app.execute("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES "
                    "(%s, %s, %s, 'glossary', '{}', %s)", (new_id(), draft, a, "0" * 64))
        app.execute("UPDATE model.pack_documents SET body = '{\"terms\": []}' WHERE pack_id = %s", (draft,))
        app.execute("UPDATE model.starter_packs SET title = 'Operations' WHERE id = %s", (draft,))
        # Not released, not for another tenant, not a system pack: release goes through the API (US-238).
        for status, tenant in (("released", a), ("draft", b), ("draft", None)):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(insert, (new_id(), tenant, f"acme_{status}", head, status, None))
        # Since US-238 the policy lets a draft become released, but only after a review by someone else (trigger).
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute("UPDATE model.starter_packs SET status = 'released' WHERE id = %s", (draft,))
        # Released and system rows are not there to change.
        for pid in (released, pmo):
            assert app.execute("UPDATE model.starter_packs SET title = 'x' WHERE id = %s", (pid,)).rowcount == 0
            assert app.execute("DELETE FROM model.starter_packs WHERE id = %s", (pid,)).rowcount == 0
            assert app.execute("UPDATE model.pack_documents SET body = '{}' WHERE pack_id = %s",
                               (pid,)).rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES "
                        "(%s, %s, %s, 'glossary', '{}', %s)", (new_id(), released, a, "0" * 64))
        # A tenant pack may not take a system pack's key, so a key names one pack.
        with pytest.raises(psycopg.errors.UniqueViolation, match="taken"):
            app.execute(insert, (new_id(), a, "pmo_tpg", head, "draft", None))
        app.execute("DELETE FROM model.starter_packs WHERE id = %s", (draft,))  # a draft can be thrown away
    assert clean.execute("SELECT count(*) FROM model.pack_documents WHERE tenant_id = %s", (a,)).fetchone()[0] == 0


def test_db_source_reads_like_the_folder(clean: psycopg.Connection) -> None:
    """Every loader gives the same from the stored PMO pack as from its folder (US-230 ``PackSource``)."""
    _load()
    pid, raw = clean.execute("SELECT id, manifest FROM model.starter_packs WHERE pack_key = 'pmo_tpg'").fetchone()
    docs = dict(clean.execute("SELECT kind, body FROM model.pack_documents WHERE pack_id = %s", (pid,)).fetchall())
    db = DbPackSource(str(pid), "pmo_tpg", "2.1.0", None, "released", raw, docs)
    folder = file_source("pmo_tpg")
    assert folder is not None and resolve("pmo_tpg") == folder and resolve(db) is db
    assert source_manifest(db)[2] == source_manifest(folder)[2] == load_manifest(PMO / "manifest.yaml")[2]
    for loader in (load_patterns, load_rules, load_tones, load_glossary, load_blueprints, load_recipes,
                   load_pack_synonyms):
        assert loader(db) == loader(folder) == loader("pmo_tpg"), loader.__name__
    assert load_patterns(DbPackSource("x", "empty", "0.1.0", None, "draft", {}, {})) == []


def test_migration_goes_down_and_up() -> None:
    env = {**os.environ, "TELA_MIGRATE_DATABASE_URL": os.environ["TELA_TEST_OWNER_DATABASE_URL"]}
    alembic = [sys.executable, "-m", "alembic"]

    def run(*args: str) -> None:
        subprocess.run([*alembic, *args], cwd=REPO, env=env, check=True, capture_output=True)  # noqa: S603

    owner = os.environ["TELA_TEST_OWNER_DATABASE_URL"]
    run("downgrade", "0047")
    try:
        with psycopg.connect(owner) as conn:
            cols = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE "
                                               "table_schema = 'model' AND table_name = 'starter_packs'")}
            assert "tenant_id" not in cols and "status" not in cols
            assert conn.execute("SELECT to_regclass('model.pack_documents')").fetchone()[0] is None
    finally:
        run("upgrade", "head")
    with psycopg.connect(owner) as conn:
        assert conn.execute("SELECT to_regclass('model.pack_documents')").fetchone()[0] == "model.pack_documents"
