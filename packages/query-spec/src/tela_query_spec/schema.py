"""JSON Schema export for the web app's generated TypeScript types (US-017).

    uv run python -m tela_query_spec.schema > packages/query-spec/query-spec.schema.json
"""

from __future__ import annotations

import json
from typing import Any

from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import QuerySpec


def schemas() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Tela Query Spec contract",
        "QuerySpec": QuerySpec.model_json_schema(by_alias=True),
        "ModelSnapshot": ModelSnapshot.model_json_schema(by_alias=True),
    }


if __name__ == "__main__":
    print(json.dumps(schemas(), indent=2, sort_keys=True))
