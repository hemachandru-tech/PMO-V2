"""Generate TypeScript types for the web app from the Query Spec and chart spec schemas (US-017, US-050).

    uv run python packages/query-spec/scripts/gen_ts.py > apps/web/src/api/querySpec.ts

CI regenerates and diffs this file, so the web types can never drift from the Python contract.
"""

from __future__ import annotations

import json
from typing import Any

from tela_chart_spec import ChartSpec, ResultShape
from tela_query_spec.schema import schemas


def ts(node: dict[str, Any]) -> str:
    if "$ref" in node:
        return node["$ref"].rsplit("/", 1)[-1]
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " | ".join(json.dumps(v) for v in node["enum"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            return " | ".join(dict.fromkeys(ts(n) for n in node[key]))
    t = node.get("type")
    if t == "array":
        inner = ts(node.get("items", {}))
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if t == "object":
        if "properties" in node:
            return obj(node)
        extra = node.get("additionalProperties")
        return f"Record<string, {ts(extra) if isinstance(extra, dict) else 'unknown'}>"
    return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean",
            "null": "null"}.get(t or "", "unknown")


def obj(node: dict[str, Any], indent: str = "  ") -> str:
    required = set(node.get("required", []))
    lines = ["{"]
    for name, prop in node.get("properties", {}).items():
        opt = "" if name in required else "?"
        lines.append(f"{indent}{name}{opt}: {ts(prop)};")
    lines.append("}")
    return "\n".join(lines)


def main() -> None:
    out = ["// Generated from packages/query-spec (tela_query_spec). Do not edit by hand.",
           "// Regenerate: uv run python packages/query-spec/scripts/gen_ts.py > apps/web/src/api/querySpec.ts", ""]
    defs: dict[str, Any] = {}
    roots = {}
    sources = {**schemas(), "ChartSpec": ChartSpec.model_json_schema(), "ResultShape": ResultShape.model_json_schema()}
    for name in ("QuerySpec", "ModelSnapshot", "ChartSpec", "ResultShape"):
        schema = sources[name]
        defs.update(schema.get("$defs", {}))
        roots[name] = schema
    for name in sorted(defs):
        node = defs[name]
        if node.get("type") == "object":
            out.append(f"export interface {name} {obj(node)}")
        else:
            out.append(f"export type {name} = {ts(node)};")
        out.append("")
    for name, node in roots.items():
        out.append(f"export interface {name} {obj(node)}")
        out.append("")
    print("\n".join(out).rstrip() + "\n", end="")


if __name__ == "__main__":
    main()
