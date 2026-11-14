"""Bind the API's named placeholders to a driver's parameter style (US-113, US-114, US-109).

The API renders bound values as sqlglot placeholders: ``:p0`` for MySQL and T-SQL,
``$p0`` for DuckDB, ``%(p0)s`` for PostgreSQL. Drivers want ``%s`` (PyMySQL) or
``?`` (pyodbc). The statement is tokenised, so a placeholder-like text inside a
string literal is never touched; only a marker token immediately followed by the
name of a signed parameter is rewritten. Values stay out of the SQL text.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlglot.dialects.dialect import Dialect
from sqlglot.tokens import TokenType

from tela_common.errors import ErrorCode, TelaError

_MARKERS = {TokenType.COLON, TokenType.PARAMETER}


def bind_positional(sql: str, dialect: str, params: dict[str, Any],
                    style: Literal["format", "qmark"], *, empty_literal: str | None = None) -> tuple[str, list[Any]]:
    """Rewrite named placeholders to positional ones. ``format`` doubles every other ``%`` (PyMySQL
    interpolates with ``%``); ``qmark`` leaves the text alone. With ``empty_literal`` an empty-string value is
    written as that constant instead of a marker (the Dataverse TDS endpoint drops empty parameters)."""
    tokens = Dialect.get_or_raise(dialect).tokenize(sql)
    out: list[str] = []
    values: list[Any] = []
    used: set[str] = set()
    pos = 0
    i = 0
    marker = "%s" if style == "format" else "?"

    def keep(upto: int) -> None:
        chunk = sql[pos:upto]
        out.append(chunk.replace("%", "%%") if style == "format" else chunk)

    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if (tok.token_type in _MARKERS and nxt is not None and nxt.token_type == TokenType.VAR
                and nxt.start == tok.end + 1 and nxt.text in params):
            keep(tok.start)
            if empty_literal is not None and params[nxt.text] == "":
                out.append(empty_literal)
            else:
                out.append(marker)
                values.append(params[nxt.text])
            used.add(nxt.text)
            pos = nxt.end + 1
            i += 2
            continue
        if style == "qmark" and tok.token_type == TokenType.PLACEHOLDER:
            raise TelaError(ErrorCode.SIGNATURE_INVALID, "unexpected positional parameter")
        i += 1
    keep(len(sql))
    if set(params) - used:  # fail closed: the signed values and the statement disagree
        raise TelaError(ErrorCode.SIGNATURE_INVALID, "a signed parameter is not used by the statement")
    return "".join(out), values
