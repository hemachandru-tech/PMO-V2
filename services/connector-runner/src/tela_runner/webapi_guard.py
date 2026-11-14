"""Dataverse Web API statement guard (US-111; API-067): the runner's own check of a signed Web API statement.

* JSON with ``entity`` (``dbo.<logical>``, a governed table), ``entity_set``, ``format`` and ``query``;
* query parameters limited to ``$apply``, ``$filter``, ``$select``, ``$orderby``, ``$top`` or ``fetchXml``;
* OData: ``$apply`` starts with ``filter(`` (the scope); a plain read carries a ``$filter`` (the scope);
* FetchXML: one ``<entity>`` named after the governed table, no ``<link-entity>``, a ``<filter>`` first;
* the runner only ever sends GET requests (there is no other code path).
"""

from __future__ import annotations

import json
import re
from typing import Any

from tela_common.errors import ErrorCode, TelaError

ODATA_KEYS = frozenset({"$apply", "$filter", "$select", "$orderby", "$top"})


def _reject(reason: str, code: ErrorCode = ErrorCode.SQL_FORBIDDEN_NODE) -> TelaError:
    return TelaError(code, "This statement is not allowed.", {"reason": reason})


def check_webapi(statement: str, allowed_objects: set[str]) -> dict[str, Any]:
    try:
        st = json.loads(statement)
    except ValueError as exc:
        raise _reject("parse error", ErrorCode.SQL_PARSE_ERROR) from exc
    if not isinstance(st, dict) or not isinstance(st.get("query"), dict):
        raise _reject("not a Web API statement", ErrorCode.SQL_NOT_SELECT)
    entity = str(st.get("entity") or "").lower()
    if entity not in allowed_objects:
        raise _reject(f"table {entity}", ErrorCode.SQL_OBJECT_NOT_ALLOWED)
    if not re.match(r"^[a-z_][a-z0-9_]*$", str(st.get("entity_set") or "")):
        raise _reject("entity set")
    for q in (st["query"], st.get("groups_query")):
        if q is not None:
            _check_query(q, entity.rpartition(".")[2])
    return st


def _check_query(q: dict[str, Any], logical: str) -> None:
    if "fetchXml" in q:
        if set(q) != {"fetchXml"}:
            raise _reject("fetchXml mixed with OData options")
        _check_fetch(str(q["fetchXml"]), logical)
        return
    extra = set(q) - ODATA_KEYS
    if extra:
        raise _reject(f"query option {sorted(extra)[0]}")
    if "$apply" in q:
        if not str(q["$apply"]).startswith("filter("):
            raise _reject("the scope filter must come first")
        if "$filter" in q:
            raise _reject("$filter next to $apply")
    elif not q.get("$filter"):
        raise _reject("missing scope filter")
    text = " ".join(str(v) for v in q.values())
    if re.search(r"\$(expand|batch)|/\$ref|Microsoft\.Dynamics\.CRM\.", text):
        raise _reject("navigation or function call")


def _check_fetch(text: str, logical: str) -> None:
    import xml.etree.ElementTree as ET

    if "<!" in text or "<?" in text:
        raise _reject("DTD or processing instruction in FetchXML")
    try:
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as exc:
        raise _reject("FetchXML parse error", ErrorCode.SQL_PARSE_ERROR) from exc
    entities = root.findall("entity")
    if root.tag != "fetch" or len(entities) != 1 or entities[0].get("name") != logical:
        raise _reject("FetchXML entity", ErrorCode.SQL_OBJECT_NOT_ALLOWED)
    if root.iter("link-entity") and any(True for _ in root.iter("link-entity")):
        raise _reject("link-entity in FetchXML")
    children = [c for c in entities[0] if c.tag != "attribute"]
    if not children or children[0].tag != "filter":
        raise _reject("the scope filter must come first")
