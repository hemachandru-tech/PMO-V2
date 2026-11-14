"""Synthetic TPG sources for the connector conformance matrix (US-117; TEST-077, TEST-078).

Each ``seed_*`` function creates database ``tpg`` on a throwaway engine container, loads the
synthetic PMO extract (packs/pmo/fixtures/tpg_synthetic.py, never client data) and creates a
read-only login ``tela_ro`` and a writable login ``tela_rw``. Connection URLs come from the
environment; a missing URL skips that engine's suite:

    TELA_TEST_MYSQL_URL=mysql://root:root_pw_local@127.0.0.1:53306
    TELA_TEST_MSSQL_URL=mssql://sa:Sa_pw_local_1!@127.0.0.1:51433
    TELA_TEST_MONGO_URL=mongodb://root:root_pw_local@127.0.0.1:57017

``scripts/conformance-sources.sh up`` starts the containers with these values.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packs" / "pmo" / "fixtures"))
import tpg_synthetic

RO_PASSWORD = "ro_pw_local_only"
RW_PASSWORD = "rw_pw_local_only"


def url(engine: str) -> dict[str, Any] | None:
    raw = os.environ.get(f"TELA_TEST_{engine.upper()}_URL")
    if not raw:
        return None
    u = urlsplit(raw)
    return {"host": u.hostname or "127.0.0.1", "port": u.port, "user": unquote(u.username or ""),
            "password": unquote(u.password or "")}


def _rows() -> list[tuple[str, list[str], list[dict[str, Any]]]]:
    projects, requests = tpg_synthetic.generate()
    return [("project_requests", tpg_synthetic.REQUEST_COLUMNS, requests),
            ("projects", tpg_synthetic.PROJECT_COLUMNS, projects)]


def seed_mysql(admin: dict[str, Any]) -> None:
    import pymysql

    conn = pymysql.connect(host=admin["host"], port=admin["port"], user=admin["user"], password=admin["password"],
                           autocommit=True)
    with conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS tpg")
        cur.execute("USE tpg")
        cur.execute("SELECT count(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'tpg'")
        if cur.fetchone()[0] < 2:
            for stmt in [s for s in tpg_synthetic.ddl("mysql").split(";") if s.strip()]:
                cur.execute(stmt)
            for table, cols, rows in _rows():
                cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                                [tuple(r[c] for c in cols) for r in rows])
        for user, pw in (("tela_ro", RO_PASSWORD), ("tela_rw", RW_PASSWORD), ("tela_role", RO_PASSWORD),
                         ("tela_role_rw", RW_PASSWORD), ("tela_grantor", RO_PASSWORD)):
            cur.execute(f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{pw}'")
        cur.execute("GRANT SELECT, SHOW VIEW ON tpg.* TO 'tela_ro'@'%'")
        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tpg.* TO 'tela_rw'@'%'")
        cur.execute("GRANT SELECT ON tpg.* TO 'tela_grantor'@'%' WITH GRANT OPTION")
        cur.execute("CREATE ROLE IF NOT EXISTS 'tela_reader', 'tela_writer'")
        cur.execute("GRANT SELECT ON tpg.* TO 'tela_reader'")
        cur.execute("GRANT SELECT, INSERT ON tpg.* TO 'tela_writer'")
        cur.execute("GRANT 'tela_reader' TO 'tela_role'@'%'")
        cur.execute("SET DEFAULT ROLE 'tela_reader' TO 'tela_role'@'%'")
        cur.execute("GRANT 'tela_writer' TO 'tela_role_rw'@'%'")
        cur.execute("SET DEFAULT ROLE 'tela_writer' TO 'tela_role_rw'@'%'")
    conn.close()


def seed_postgres(admin: dict[str, Any]) -> None:
    import psycopg
    from psycopg.conninfo import make_conninfo

    base = {"host": admin["host"], "port": admin["port"], "user": admin["user"], "password": admin["password"]}
    with psycopg.connect(make_conninfo(**base, dbname="postgres"), autocommit=True) as c:
        for user, pw in (("tela_ro", RO_PASSWORD), ("tela_rw", RW_PASSWORD)):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone():
                c.execute(f"CREATE ROLE {user} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = 'tpg'").fetchone():
            c.execute("CREATE DATABASE tpg")
    with psycopg.connect(make_conninfo(**base, dbname="tpg"), autocommit=True) as src:
        if not src.execute("SELECT 1 FROM pg_tables WHERE tablename = 'projects'").fetchone():
            tpg_synthetic.load(src)
        src.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        src.execute("GRANT USAGE ON SCHEMA public TO tela_ro, tela_rw")
        src.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO tela_ro, tela_rw")
        src.execute("GRANT INSERT, UPDATE ON projects TO tela_rw")


MSSQL_LOGINS = {
    "tela_ro": (RO_PASSWORD, ["ALTER ROLE db_datareader ADD MEMBER tela_ro"]),
    "tela_rw": (RW_PASSWORD, ["ALTER ROLE db_datareader ADD MEMBER tela_rw",
                              "ALTER ROLE db_datawriter ADD MEMBER tela_rw"]),
    "tela_obj_rw": (RW_PASSWORD, ["GRANT SELECT ON SCHEMA::dbo TO tela_obj_rw",
                                  "GRANT INSERT ON dbo.projects TO tela_obj_rw"]),
    "tela_owner_rw": (RW_PASSWORD, ["ALTER ROLE db_owner ADD MEMBER tela_owner_rw"]),
    "tela_exec_rw": (RW_PASSWORD, ["ALTER ROLE db_datareader ADD MEMBER tela_exec_rw",
                                   "GRANT EXECUTE TO tela_exec_rw"]),
}


def mssql_connect(admin: dict[str, Any], database: str = "master") -> Any:
    import pyodbc

    return pyodbc.connect(f"Driver={{ODBC Driver 18 for SQL Server}};Server={admin['host']},{admin['port']};"
                          f"Database={database};UID={admin['user']};PWD={{{admin['password']}}};Encrypt=yes;"
                          f"TrustServerCertificate=yes", autocommit=True, timeout=30)


def seed_sqlserver(admin: dict[str, Any]) -> None:
    conn = mssql_connect(admin)
    cur = conn.cursor()
    cur.execute("IF DB_ID('tpg') IS NULL CREATE DATABASE tpg")
    for login, (pw, _) in MSSQL_LOGINS.items():
        cur.execute(f"IF SUSER_ID('{login}') IS NULL CREATE LOGIN {login} WITH PASSWORD = '{pw}', CHECK_POLICY = OFF")
    conn.close()
    conn = mssql_connect(admin, "tpg")
    cur = conn.cursor()
    if cur.execute("SELECT count(*) FROM sys.tables WHERE name IN ('projects', 'project_requests')").fetchone()[0] < 2:
        for stmt in [s for s in tpg_synthetic.ddl("tsql").split(";") if s.strip()]:
            cur.execute(stmt)
        cur.fast_executemany = True
        for table, cols, rows in _rows():
            cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                            [tuple(r[c] for c in cols) for r in rows])
    for login, (_, grants) in MSSQL_LOGINS.items():
        cur.execute(f"IF USER_ID('{login}') IS NULL CREATE USER {login} FOR LOGIN {login}")
        for g in grants:
            cur.execute(g)
    conn.close()


def tpg_workbook(*, extras: bool = False) -> bytes:
    """The synthetic extract as an Excel workbook: one sheet per table, header in row 1. ``extras`` adds a
    "Notes" sheet with a title row above the header, a merged title, a mixed-type column and a formula column
    saved without computed values."""
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for table, cols, rows in _rows():
        ws = wb.create_sheet(table)
        ws.append(cols)
        for r in rows:
            ws.append([r[c] for c in cols])
    if extras:
        ws = wb.create_sheet("Notes")
        ws.append(["Project notes export"])
        ws.merge_cells("A1:D1")
        ws.append(["Project id", "Score", "Mixed", "Formula"])
        for i in range(1, 11):
            ws.append([f"P{i:04d}", i * 1.5, i if i % 2 else f"n{i}", f"=B{i + 2}*2"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def tpg_csv(table: str = "projects") -> bytes:
    import csv
    import io

    _, cols, rows = next(t for t in _rows() if t[0] == table)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r[c] is None else r[c] for c in cols])
    return buf.getvalue().encode()


class MemoryStore:
    """Object store stand-in for the runner tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes:
        return self.objects[key]


def file_info(store: MemoryStore, data: bytes, fmt: str = "xlsx", *, sheets: list[dict[str, Any]] | None = None,
              filename: str = "tpg.xlsx", connection_id: str = "01900000-0000-7000-8000-0000000000e1") -> Any:
    import hashlib

    from tela_runner.adapters.base import ConnectionInfo

    digest = hashlib.sha256(data).hexdigest()
    key = f"tenant/t/files/{digest[:8]}/v1"
    store.objects[key] = data
    return ConnectionInfo(connection_id, "excel" if fmt == "xlsx" else "csv",
                          {"file": {"object_key": key, "sha256": digest, "format": fmt, "filename": filename,
                                    "version": 1, "sheets": sheets or []}, "approved_schemas": ["main"]}, {})


def mongo_documents() -> dict[str, list[dict[str, Any]]]:
    """The extract as MongoDB documents: BSON dates (midnight UTC for dates), Decimal128 money, missing fields
    for NULLs (as a document store would hold them), and nested extras the PMO pack does not use — a
    ``team`` sub-document and a ``milestones`` array (a child entity for the sampler)."""
    from datetime import UTC, date, datetime, time
    from decimal import Decimal

    from bson.decimal128 import Decimal128

    def conv(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.replace(tzinfo=UTC)
        if isinstance(v, date):
            return datetime.combine(v, time(), tzinfo=UTC)
        if isinstance(v, Decimal):
            return Decimal128(v)
        return v

    out: dict[str, list[dict[str, Any]]] = {}
    for table, cols, rows in _rows():
        docs = []
        for i, r in enumerate(rows):
            d = {c: conv(r[c]) for c in cols if r[c] is not None}
            if table == "projects":
                d["team"] = {"sponsor": f"Sponsor {i % 5}", "size": 3 + i % 7}
                d["milestones"] = [{"name": "Kick-off", "planned": d.get("actual_project_start_date"), "done": True},
                                   {"name": "Go-live", "planned": d.get("planned_deployment_date"),
                                    "done": "actual_deployment_date" in d}]
            docs.append(d)
        out[table] = docs
    return out


def seed_mongo(admin: dict[str, Any]) -> None:
    from pymongo import MongoClient

    client = MongoClient(host=admin["host"], port=admin["port"], username=admin["user"], password=admin["password"])
    db = client["tpg"]
    if db["projects"].estimated_document_count() < 100:
        for name, docs in mongo_documents().items():
            db[name].drop()
            db[name].insert_many(docs)
            db[name].create_index(name.rstrip("s") + "_id" if name == "projects" else "project_request_id")
    users = {u["user"] for u in db.command("usersInfo")["users"]}
    for user, pw, roles in (("tela_ro", RO_PASSWORD, [{"role": "read", "db": "tpg"}]),
                            ("tela_rw", RW_PASSWORD, [{"role": "readWrite", "db": "tpg"}]),
                            ("tela_dba_rw", RW_PASSWORD, [{"role": "read", "db": "tpg"}, {"role": "dbAdmin", "db": "tpg"}])):
        if user not in users:
            db.command("createUser", user, pwd=pw, roles=roles)
    client.close()


# ----------------------------------------------------------------------------- Dataverse fakes (US-110, US-111)

DV_TENANT = "00000000-0000-4000-8000-000000000001"
DV_READER = ("00000000-0000-4000-8000-0000000000a1", "fake-reader-secret")
DV_WRITER = ("00000000-0000-4000-8000-0000000000a2", "fake-writer-secret")


def seed_dataverse_tds(admin: dict[str, Any]) -> None:
    """Database ``tpg_dataverse`` on the SQL Server container stands in for the TDS endpoint."""
    conn = mssql_connect(admin)
    cur = conn.cursor()
    cur.execute("IF DB_ID('tpg_dataverse') IS NULL CREATE DATABASE tpg_dataverse")
    cur.execute(f"IF SUSER_ID('tela_tds') IS NULL CREATE LOGIN tela_tds WITH PASSWORD = '{RO_PASSWORD}', "
                "CHECK_POLICY = OFF")
    conn.close()
    conn = mssql_connect(admin, "tpg_dataverse")
    cur = conn.cursor()
    if cur.execute("SELECT count(*) FROM sys.tables WHERE name = 'tpg_project'").fetchone()[0] == 0:
        cur.execute(tpg_synthetic.to_dataverse_sql())
    cur.execute("IF USER_ID('tela_tds') IS NULL CREATE USER tela_tds FOR LOGIN tela_tds; "
                "ALTER ROLE db_datareader ADD MEMBER tela_tds")
    conn.close()


class FakeDataverse:
    """The fake Web API and token endpoint on a local port, in a background thread."""

    def __init__(self) -> None:
        import socket
        import threading

        import uvicorn

        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "fakes"))
        import dataverse_fake

        self.module = dataverse_fake
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(dataverse_fake.app, host="127.0.0.1", port=self.port,
                                                    log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        import time

        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)

    def throttle(self, every: int) -> None:
        self.module.STATE["throttle_every"] = every
        self.module.STATE["reads"] = 0

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def dataverse_info(fake: FakeDataverse, *, mode: str = "tds", app: tuple[str, str] = DV_READER,
                   tds: dict[str, Any] | None = None, fmt: str = "odata") -> Any:
    from tela_runner.adapters.base import ConnectionInfo

    descriptor: dict[str, Any] = {"environment_url": fake.url, "host": "127.0.0.1", "tenant_id": DV_TENANT,
                                  "client_id": app[0], "mode": mode, "tables": ["tpg_project", "tpg_projectrequest"],
                                  "approved_schemas": ["dbo"], "webapi_format": fmt}
    creds = {"client_id": app[0], "client_secret": app[1]}
    if tds is not None:
        descriptor["dev_tds"] = f"{tds['host']},{tds['port']}"
        creds.update(tds_username="tela_tds", tds_password=RO_PASSWORD)
    return ConnectionInfo("01900000-0000-7000-8000-0000000000f9", "dataverse", descriptor, creds)
