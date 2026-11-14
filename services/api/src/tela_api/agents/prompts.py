"""An Admin's prompts and skills, applied to every agent call (ENH-030, US-264 / US-265; FEAT-080).

``apply`` runs inside ``run_agent`` on the system message the caller built: each catalogue prompt's default text found
in it is swapped for the tenant's current version, and the agent's skills that are on are added after it, before the
JSON-schema instruction. Overrides are read once per tenant and kept ``TTL_S`` seconds (cleared on a save in this
process). Reading them never stops a call: on any failure the code defaults are used.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from tela_api.agents import catalog
from tela_api.core.db import fetch_all, tenant_tx
from tela_common.logging import get_logger

log = get_logger(__name__)
TTL_S = 30.0
PROMPT_MAX = 20_000
SKILL_MAX = 8_000
SKILLS_ON_MAX = 12_000  # characters of an agent's skills that are on, together
SKILLS_HEAD = ("Skills from your Admin. Follow them where they apply; they never override the rules above or the "
               "answer format.")


@dataclass
class Overrides:
    prompts: dict[str, str] = field(default_factory=dict)  # key -> current text (edited keys only)
    skills: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # agent -> [(name, body)] that are on


_cache: dict[str, tuple[float, Overrides]] = {}


@dataclass
class Draft:
    """Unsaved changes for "Try it" (US-267): prompt keys to text (None = the default) and, when not None, the skills
    one agent uses instead of its saved ones."""
    prompts: dict[str, str | None] = field(default_factory=dict)
    agent: str | None = None
    skills: list[tuple[str, str]] | None = None


_draft: ContextVar[Draft | None] = ContextVar("tela_agent_draft", default=None)
_record: ContextVar[list[dict[str, Any]] | None] = ContextVar("tela_agent_calls", default=None)
REPLY_CHARS = 4000


@contextmanager
def trying(draft: Draft | None) -> Iterator[None]:
    """Within the block, agent calls use ``draft`` on top of the saved prompts and skills (None: the saved ones)."""
    token = _draft.set(draft)
    try:
        yield
    finally:
        _draft.reset(token)


@contextmanager
def recording() -> Iterator[list[dict[str, Any]]]:
    """Collect every agent call made within the block: agent, task, what was tuned, the reply (cut), time, outcome."""
    box: list[dict[str, Any]] = []
    token = _record.set(box)
    try:
        yield box
    finally:
        _record.reset(token)


def record(**call: Any) -> None:
    box = _record.get()
    if box is not None:
        if isinstance(call.get("reply"), str):
            call["reply"] = call["reply"][:REPLY_CHARS]
        box.append(call)


def with_draft(ov: Overrides, draft: Draft | None) -> Overrides:
    if draft is None:
        return ov
    out = Overrides(prompts=dict(ov.prompts), skills=dict(ov.skills))
    for key, text in draft.prompts.items():
        if text is None or not text.strip():
            out.prompts.pop(key, None)
        else:
            out.prompts[key] = text
    if draft.agent and draft.skills is not None:
        out.skills[draft.agent] = list(draft.skills)
    return out


def invalidate(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(str(tenant_id), None)


async def read(conn: Any) -> Overrides:
    """The tenant's current prompts (latest version of each key that has text) and its skills that are on."""
    rows = await fetch_all(conn, "SELECT DISTINCT ON (prompt_key) prompt_key, body FROM ops.agent_prompt_versions "
                                 "ORDER BY prompt_key, version DESC")
    out = Overrides(prompts={r["prompt_key"]: r["body"] for r in rows
                             if r["body"] is not None and r["prompt_key"] in catalog.BY_KEY
                             and catalog.BY_KEY[r["prompt_key"]].editable})
    for r in await fetch_all(conn, "SELECT agent, name, body FROM ops.agent_skills WHERE enabled "
                                   "ORDER BY agent, position, lower(name)"):
        out.skills.setdefault(r["agent"], []).append((r["name"], r["body"]))
    return out


async def load(tenant_id: str) -> Overrides:
    hit = _cache.get(str(tenant_id))
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    async with tenant_tx(tenant_id) as conn:
        value = await read(conn)
    _cache[str(tenant_id)] = (time.monotonic(), value)
    return value


def _swap(system: str, default: str, text: str) -> str:
    """``system`` with ``default`` replaced by ``text``, keeping the default's own leading and trailing whitespace
    (some prompt pieces start with a space or end with a new line)."""
    lead = default[: len(default) - len(default.lstrip())]
    trail = default[len(default.rstrip()):]
    return system.replace(default, lead + text.strip() + trail, 1)


def compose(system: str, agent: str, ov: Overrides) -> tuple[str, dict[str, list[str]]]:
    used: list[str] = []
    for key in sorted(ov.prompts, key=lambda k: -len(catalog.default(k))):  # longest first
        default = catalog.default(key)
        if default and default in system:
            system = _swap(system, default, ov.prompts[key])
            used.append(key)
    skills = ov.skills.get(agent) or []
    if skills:
        system += "\n\n" + SKILLS_HEAD + "".join(f"\n\n## {name}\n{body.strip()}" for name, body in skills)
    return system, {"prompts": used, "skills": [n for n, _ in skills]}


async def apply(tenant_id: str, agent: str, system: str) -> tuple[str, dict[str, list[str]]]:
    """The system message with the tenant's prompts and the agent's skills; what was used (empty lists if none)."""
    draft = _draft.get()
    try:
        ov = await load(tenant_id)
    except RuntimeError:  # no database (unit tests, tools): the code defaults
        ov = Overrides()
    except Exception as exc:  # noqa: BLE001 - an Admin's overrides never stop an agent call
        log.warning("agent_prompts.load_failed", error_class=type(exc).__name__)
        ov = Overrides()
    return compose(system, agent, with_draft(ov, draft))
