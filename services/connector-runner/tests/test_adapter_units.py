"""Adapter pieces that need no server (US-113, US-114): MySQL grant parsing, SQL Server permission names and
connection-string quoting, placeholder binding per driver style, and the SELECT-only backstop."""

from __future__ import annotations

import pytest

from tela_common.errors import ErrorCode, TelaError
from tela_runner.adapters.base import Limits
from tela_runner.adapters.dbapi import collect_rows, jsonable
from tela_runner.adapters.mysql import parse_grants
from tela_runner.adapters.params import bind_positional
from tela_runner.adapters.sqlserver import SqlServerAdapter, is_permission_write, odbc_value, type_name


def test_mysql_grants() -> None:
    ok, roles = parse_grants(["GRANT USAGE ON *.* TO `tela_ro`@`%`", "GRANT SELECT, SHOW VIEW ON `tpg`.* TO `tela_ro`@`%`",
                              "GRANT `reader`@`%` TO `tela_ro`@`%`"])
    assert ok == [] and roles == ["`reader`@`%`"]
    bad, _ = parse_grants(["GRANT SELECT, INSERT ON `tpg`.* TO `u`@`%`", "GRANT SELECT ON `tpg`.* TO `u`@`%` WITH GRANT OPTION",
                           "GRANT PROXY ON ''@'' TO 'u'@'%'", "GRANT SELECT (a, b), UPDATE (c) ON `tpg`.`t` TO `u`@`%`",
                           "REVOKE nonsense"])
    assert "account has INSERT on `tpg`.*" in bad and "account can grant privileges on `tpg`.*" in bad
    assert "account has a PROXY grant" in bad and "account has UPDATE on `tpg`.`t`" in bad
    assert any("could not be read" in r for r in bad)


def test_sqlserver_helpers() -> None:
    assert all(is_permission_write(p) for p in ("INSERT", "ALTER ANY SCHEMA", "CREATE TABLE", "CONTROL", "EXECUTE"))
    assert not any(is_permission_write(p) for p in ("SELECT", "CONNECT", "VIEW DEFINITION", "SHOWPLAN"))
    assert odbc_value("p;w}d") == "{p;w}}d}"
    assert type_name("nvarchar", 128, 0, 0) == "nvarchar(64)" and type_name("decimal", 9, 15, 2) == "decimal(15,2)"
    assert type_name("varchar", -1, 0, 0) == "varchar(max)" and type_name("int", 4, 10, 0) == "int"
    with pytest.raises(TelaError):
        SqlServerAdapter(allow_insecure_tls=False)._base_attrs(type("I", (), {"descriptor": {
            "host": "h", "tls": {"trust_server_certificate": True}}})())  # type: ignore[arg-type]
    backstop = SqlServerAdapter()
    for sql in ("UPDATE dbo.t SET a = 1", "SELECT 1; DROP TABLE dbo.t", "SELECT * INTO x FROM dbo.t", "EXEC sp_who"):
        with pytest.raises(TelaError) as err:
            backstop._assert_select(sql)
        assert err.value.code == ErrorCode.READ_ONLY_VIOLATION
    backstop._assert_select("SELECT TOP 5 a FROM dbo.t WHERE b = :p0")


def test_binding_never_touches_string_literals() -> None:
    sql, values = bind_positional("SELECT a FROM t WHERE b = :p0 AND c = ':p1' AND d LIKE '50%' AND e = :p1",
                                  "mysql", {"p0": 1, "p1": "x"}, "format")
    assert sql == "SELECT a FROM t WHERE b = %s AND c = ':p1' AND d LIKE '50%%' AND e = %s" and values == [1, "x"]
    sql, values = bind_positional("SELECT TOP (10) a FROM t WHERE b = :p0", "tsql", {"p0": "?"}, "qmark")
    assert sql == "SELECT TOP (10) a FROM t WHERE b = ?" and values == ["?"]
    with pytest.raises(TelaError):
        bind_positional("SELECT a FROM t", "mysql", {"p0": 1}, "format")  # a signed value the statement lacks
    with pytest.raises(TelaError):
        bind_positional("SELECT a FROM t WHERE b = ?", "tsql", {}, "qmark")


def test_row_collection_caps() -> None:
    source = [(i,) for i in range(10)]

    def fetchmany(n: int) -> list[tuple[int]]:
        out = source[:n]
        del source[:n]
        return out

    rows = collect_rows(fetchmany, Limits(max_rows=7))
    assert len(rows) == 8  # cap + 1 detects truncation
    with pytest.raises(TelaError):
        collect_rows(lambda n: [("x" * 600,)] * n, Limits(max_bytes=1000))
    from datetime import date
    from decimal import Decimal

    assert jsonable(Decimal("3.0")) == 3 and jsonable(Decimal("2.5")) == 2.5 and jsonable(date(2026, 9, 30)) == "2026-09-30"
    assert jsonable(b"\x00") == "[BINARY]"


def test_empty_strings_can_be_written_as_a_constant() -> None:
    sql, values = bind_positional("SELECT a FROM t WHERE b <> :p0 AND c = :p1 AND d = ':p0'", "tsql",
                                  {"p0": "", "p1": "x"}, "qmark", empty_literal="N''")
    assert sql == "SELECT a FROM t WHERE b <> N'' AND c = ? AND d = ':p0'" and values == ["x"]
    sql, values = bind_positional("SELECT a FROM t WHERE b <> :p0", "tsql", {"p0": ""}, "qmark")
    assert sql == "SELECT a FROM t WHERE b <> ?" and values == [""]  # other engines bind it as usual
    from tela_runner.adapters.dataverse import DataverseAdapter

    assert DataverseAdapter.empty_literal == "N''" and SqlServerAdapter.empty_literal is None


class _LinkDrop(Exception):
    pass


class _Cursor:
    def __init__(self, fail: bool) -> None:
        self.fail, self.description = fail, [("n", int)]

    def execute(self, sql: str, values: list[object]) -> None:
        if self.fail:
            raise _LinkDrop

    def fetchmany(self, n: int) -> list[tuple[int]]:
        rows, self.description = ([(1,)] if self.description else []), None
        return rows


class _Conn:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    def cursor(self) -> _Cursor:
        return _Cursor(self.fail)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


async def test_a_dropped_session_is_retried_once_on_a_new_connection() -> None:
    from tela_runner.adapters.base import ConnectionInfo
    from tela_runner.adapters.dbapi import DbApiAdapter

    class Flaky(DbApiAdapter):
        engine, dialect, param_style = "fake", "tsql", "qmark"
        retry = True

        def __init__(self, drops: int) -> None:
            super().__init__()
            self.drops, self.opened = drops, 0

        def _connect(self, info: ConnectionInfo) -> _Conn:
            self.opened += 1
            return _Conn(self.opened <= self.drops)

        def _open(self, info: ConnectionInfo) -> _Conn:
            return self._connect(info)

        def _begin(self, cur: object, limits: Limits) -> bool:
            return True

        def _link_failed(self, exc: Exception) -> bool:
            return self.retry and isinstance(exc, _LinkDrop)

        def _classify(self, exc: Exception) -> TelaError:
            return TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.")

    info = ConnectionInfo("c", "fake", {}, {})
    one = Flaky(drops=1)
    result = await one.execute(info, "SELECT COUNT(*) AS n FROM t", {}, Limits(), "e1")
    assert result.rows == [[1]] and one.opened == 2
    twice = Flaky(drops=2)  # only one retry
    with pytest.raises(TelaError) as err:
        await twice.execute(info, "SELECT COUNT(*) AS n FROM t", {}, Limits(), "e2")
    assert err.value.code == ErrorCode.SOURCE_UNREACHABLE and twice.opened == 2
    other = Flaky(drops=1)
    other.retry = False  # engines that do not opt in fail at once
    with pytest.raises(TelaError):
        await other.execute(info, "SELECT COUNT(*) AS n FROM t", {}, Limits(), "e3")
    assert other.opened == 1


def test_dataverse_missing_privilege_names_the_table() -> None:
    import pyodbc

    from tela_runner.adapters.dataverse import DataverseAdapter

    text = ('[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]{"Message":"Principal user (Id=92b7a873, '
            "type=8, roleCount=1, ApplicationId/FullName: c154eb1a) is missing prvReadnew_projectlifecycle privilege "
            "(Id=37927874) on OTC=10610 for entity 'new_projectlifecycle' (LocalizedName='Project Life Cycle'). "
            'context.Caller=92b7a873. Consider adding missed privilege to one of the principal (user/team) roles."}')
    err = DataverseAdapter()._classify(pyodbc.ProgrammingError("42000", text))
    assert err.code == ErrorCode.SQL_OBJECT_NOT_ALLOWED and err.details == {"table": "new_projectlifecycle"}
    assert "Project Life Cycle (new_projectlifecycle)" in err.message and "Read privilege" in err.message
    assert "92b7a873" not in err.message and "c154eb1a" not in err.message  # no principal ids
    other = DataverseAdapter()._classify(pyodbc.ProgrammingError("42000", "Incorrect syntax near 'x'."))
    assert other.code == ErrorCode.QUERY_FAILED


async def test_dataverse_marks_tables_whose_rows_the_app_user_may_not_read() -> None:
    from tela_runner.adapters.base import ConnectionInfo
    from tela_runner.adapters.dataverse import DataverseAdapter, _Meta

    class Probe(DataverseAdapter):
        async def _metadata(self, info: ConnectionInfo, logical: str) -> _Meta:
            return _Meta(entity_set=f"{logical}s", primary_id=f"{logical}id", columns={}, fetched=0.0)

        async def _get(self, info: ConnectionInfo, path: str, params: dict[str, str] | None = None, *,
                       url: str | None = None, page_size: int = 5000) -> dict[str, object]:
            assert params == {"$select": path[1:-1] + "id", "$top": "1"} and page_size == 1
            if path == "/new_projectlifecycles":
                raise TelaError(ErrorCode.SQL_OBJECT_NOT_ALLOWED, "Dataverse refused the request.", {"status": 403})
            if path == "/tpg_risks":
                raise TelaError(ErrorCode.SOURCE_UNREACHABLE, "The data source is not reachable right now.")
            return {"value": [{"x": 1}]}

    info = ConnectionInfo("c", "dataverse", {}, {})
    probe = Probe()
    assert await probe._readable(info, "tpg_project") is True
    assert await probe._readable(info, "new_projectlifecycle") is False
    assert await probe._readable(info, "tpg_risk") is None  # the check could not run: not marked unreadable
