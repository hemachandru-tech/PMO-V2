"""Tela Query Spec contract (US-017; ADR-001)."""

from tela_query_spec.model import ModelSnapshot
from tela_query_spec.spec import SPEC_VERSION, QuerySpec, parse_spec

__all__ = ["SPEC_VERSION", "ModelSnapshot", "QuerySpec", "parse_spec"]
