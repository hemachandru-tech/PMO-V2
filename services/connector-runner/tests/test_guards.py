"""Runner guard, envelope and masking unit tests (US-021, US-023, US-025, US-027; RULE-002, RULE-020; TEST-071, TEST-072)."""

from __future__ import annotations

import time

import pytest
import sqlglot
from security_corpus import malicious_statements

from tela_common import envelope as env
from tela_common.errors import ErrorCode, TelaError
from tela_runner.guards import luhn_ok, mask_email, mask_phone, mask_rows
from tela_runner.sql_guard import check_statement

ALLOWED = {"public.projects", "public.project_requests"}
CORPUS = malicious_statements()


def test_corpus_is_large_enough() -> None:
    assert len(CORPUS) >= 500


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql", "duckdb"])
def test_every_malicious_statement_is_refused(dialect: str) -> None:
    accepted = []
    for sql in CORPUS:
        try:
            check_statement(sql, dialect, ALLOWED)
        except TelaError as exc:
            assert exc.code.startswith("sql_"), (sql, exc.code)
        else:
            accepted.append(sql)
    assert accepted == []


def test_compiled_shapes_are_accepted() -> None:
    sql = ('WITH tela_base AS (SELECT DISTINCT "t0"."project_id" AS __pk0 FROM (SELECT "project_id", "region" '
           'FROM public.projects WHERE "cio" = %(p0)s) AS t0) SELECT count(tela_base.__pk0) AS n FROM tela_base '
           'LIMIT 10001')
    check_statement(sql, "postgres", ALLOWED)
    check_statement("SELECT 1 FROM public.projects;", "postgres", ALLOWED)  # one trailing semicolon allowed


def test_cte_may_not_shadow_a_governed_table() -> None:
    with pytest.raises(TelaError) as err:
        check_statement("WITH projects AS (SELECT 1 AS x) SELECT x FROM projects", "postgres", ALLOWED)
    assert err.value.code == ErrorCode.SQL_OBJECT_NOT_ALLOWED


def test_envelope_signature_hash_and_expiry() -> None:
    private, public = env.generate_keypair()
    sk, pk = env.load_private_key(private), env.load_public_key(public)
    statement = "SELECT 1 FROM public.projects"
    signed = env.sign(sk, {"op": "execute", "statement_sha256": env.statement_sha256(statement)})
    assert env.verify(pk, signed, statement=statement)["op"] == "execute"
    for tamper in (statement + " ", statement.replace("1", "2")):
        with pytest.raises(TelaError) as err:
            env.verify(pk, signed, statement=tamper)
        assert err.value.code == ErrorCode.SIGNATURE_INVALID
    forged = {"payload": {**signed["payload"], "op": "introspect"}, "signature": signed["signature"]}
    with pytest.raises(TelaError):
        env.verify(pk, forged)
    other_sk = env.load_private_key(env.generate_keypair()[0])
    with pytest.raises(TelaError):
        env.verify(pk, env.sign(other_sk, {"op": "execute"}))
    expired = env.sign(sk, {"op": "execute"}, ttl_s=-5)
    with pytest.raises(TelaError):
        env.verify(pk, expired)
    assert int(signed["payload"]["exp"]) <= int(time.time()) + env.ENVELOPE_TTL_S


def test_masking_rule_020() -> None:
    assert mask_email("nomsa.osei@example.com") == "n***@example.com"
    # RULE-020's example shows nine stars, but +971501234567 has 12 digits: keeping the last 4 leaves 8
    # (recorded as a source-pack inconsistency in task.md).
    assert mask_phone("+971501234567") == "+********4567"
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    cols = ["password", "email_address", "phone", "card", "region", "project_number", "token_count"]
    rows = [["hunter2", "nomsa.osei@example.com", "+971501234567", "4111 1111 1111 1111", "KSA", "PRJ-001", 5]]
    masked = mask_rows(cols, rows, {"project_number": {"semantic_type": "identifier"},
                                    "token_count": {"sensitivity": "public"}})
    assert rows[0] == ["[REDACTED]", "n***@example.com", "+********4567", "[REDACTED_PAYMENT_DATA]", "KSA",
                       "PRJ-001", 5]
    assert masked == 4
    # Dates and plain numbers are never mistaken for phone numbers.
    rows = [["2026-09-30", "12345", None]]
    mask_rows(["d", "n", "x"], rows, {})
    assert rows == [["2026-09-30", "12345", None]]


def test_guard_parses_what_the_compiler_emits_for_each_dialect() -> None:
    for dialect in ("postgres", "mysql", "tsql", "duckdb"):
        sql = sqlglot.transpile("SELECT region, COUNT(*) AS n FROM public.projects GROUP BY region LIMIT 10",
                                read="postgres", write=dialect)[0]
        check_statement(sql, dialect, ALLOWED)
