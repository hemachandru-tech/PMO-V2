"""The agent catalogue (ENH-030, US-264; FEAT-080).

One place that lists every agent (AG-01 to AG-10), what it does, the knowledge it reads and each system prompt it
sends. A prompt's default text is read from the constant in the agent's own module, so the code stays the single
source of the defaults; an Admin's edited version replaces that text at run time (``prompts.apply``).

A prompt built from a template whose text depends on a setting (AG-04 pack author: the send-samples switch, DEC-27)
is listed read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache


@dataclass(frozen=True)
class Knowledge:
    label: str
    href: str | None = None


@dataclass(frozen=True)
class Agent:
    code: str
    name: str
    purpose: str
    inputs: str
    output: str
    guard: str
    role: str  # model role in AI settings: planner, narrator or advisor
    tasks: tuple[str, ...]
    knowledge: tuple[Knowledge, ...] = ()


@dataclass(frozen=True)
class PromptDef:
    key: str
    agent: str  # AG-01 … AG-10, or "shared"
    task: str
    title: str
    used_for: str
    loader: Callable[[], str] = field(repr=False)
    editable: bool = True
    note: str = ""


GLOSSARY = Knowledge("Glossary and business notes", "/admin/knowledge")
DOCS = Knowledge("Uploaded documents", "/admin/knowledge")
GOLD = Knowledge("Gold examples", "/admin/knowledge/gold")
VALUES = Knowledge("Value synonyms", "/admin/knowledge/values")
DEFINITIONS = Knowledge("Definitions", "/admin/knowledge/definitions")
CONDITIONS = Knowledge("Named conditions", "/admin/knowledge/conditions")
CERTIFIED = Knowledge("Certified measures and fields", "/govern/certification")
MODEL = Knowledge("Approved model and pack", "/admin/packs")
PATTERNS = Knowledge("Chart patterns", "/admin/knowledge")

AGENTS: tuple[Agent, ...] = (
    Agent("AG-01", "Intent router",
          "Reads each Ask turn and decides what it is (a data question, a refinement, a definition, a report request, "
          "a canvas edit, model help or out of scope) and which source answers it. Also frames the question's shape.",
          "The question, the conversation so far and the page it was asked on.",
          "The intent and the source, with a confidence.",
          "Unknown intents go to the Clarifier; a write request is refused, never sent on.",
          "planner", ("intent", "frame")),
    Agent("AG-02", "Clarifier",
          "Asks one short question when the meaning would change the answer (period, region, cancelled records), "
          "and checks that the planned answer really answers the question.",
          "The question and the readings the planner considered.",
          "A question with 2 to 4 options, or a yes/no back-check.",
          "At most one clarifying question a turn; the readings come from the rules, the model only words them.",
          "planner", ("clarify",), (GLOSSARY, DOCS)),
    Agent("AG-03", "Query planner",
          "Turns a question into a Query Spec over the approved model: measure, breakdowns, filters, sort, limit, "
          "time grain and comparison. Also rewrites follow-ups and links words to fields.",
          "The question, 5 to 10 linked candidates, gold examples and standing notes.",
          "A Query Spec, or a refusal with the reason and the missing data.",
          "The spec validator and compiler check every ID, filter and value; at most two repairs.",
          "planner", ("plan", "plan_review"), (MODEL, CERTIFIED, GOLD, VALUES, CONDITIONS, GLOSSARY, DOCS)),
    Agent("AG-04", "Model assistant",
          "Proposes links, measures, dimensions and questions after a source is read, applies an Admin's "
          "corrections, suggests pack mappings and drafts packs in Pack Studio.",
          "The source's catalogue (names and types; values only when allowed) and the Admin's message.",
          "A model change set the Admin reviews.",
          "Links must use real columns; cardinality is counted on the source; nothing applies without approval.",
          "planner", ("model_assist", "map_suggest", "pack_author"), (MODEL,)),
    Agent("AG-05", "Report planner",
          "Turns one sentence into a report plan: headline figures, sections, widgets, filters and named gaps.",
          "The request, the model and the certified measures.",
          "A report plan.",
          "Every widget spec is validated; gaps are listed, never guessed.",
          "planner", ("report_plan",), (MODEL, CERTIFIED, DOCS)),
    Agent("AG-06", "Canvas editor",
          "Turns a layout instruction (move, resize, remove, add a filter, change a visual) into a change list.",
          "The instruction and the report's layout.",
          "A list of layout changes.",
          "Changes are applied to a copy and previewed; the grid stays 12 columns; undo is kept.",
          "planner", ("canvas_edit",)),
    Agent("AG-07", "Visual advisor",
          "Recommends the visual and enhancements (reference lines, comparisons, sort, top N) for an answer.",
          "The result's shape (column kinds, cardinality), the question and chart patterns.",
          "A visual, alternatives and the reason.",
          "RULE-008 decides what is suitable; the agent only chooses among suitable visuals.",
          "advisor", ("visual_advice",), (PATTERNS,)),
    Agent("AG-08", "Narrator",
          "Writes the headline sentence and explanation of an answer, and explains what a measure means.",
          "Summary figures computed in code, and the measure's definition.",
          "A headline and sentences that cite the figures.",
          "Every number must match a computed figure (RULE-022); otherwise a template sentence is used.",
          "narrator", ("narrate", "explain"), (MODEL, DEFINITIONS)),
    Agent("AG-09", "Certification reviewer",
          "Rephrases a measure's rule in plain words for the reviewer and explains look-alikes.",
          "The candidate definition and similar certified definitions.",
          "A plain-language note.",
          "Advisory only: the decision stays with the Admin; a rephrasing with new words is not shown.",
          "narrator", ("cert_review",), (CERTIFIED,)),
    Agent("AG-10", "SQL drafter",
          "Drafts read-only SQL for Custom SQL and answers Direct SQL questions.",
          "The question and the tables and columns the person may use.",
          "One SELECT statement.",
          "Never runs on its own in Custom SQL; Direct SQL runs only after the Custom SQL checks pass.",
          "planner", ("sql_draft",), (CONDITIONS,)),
)
BY_CODE = {a.code: a for a in AGENTS}


def _route() -> str:
    from tela_api.ask.routing import ROUTE_SYSTEM
    return ROUTE_SYSTEM


def _frame() -> str:
    from tela_api.ask.frame import FRAME_SYSTEM
    return FRAME_SYSTEM


def _backcheck() -> str:
    from tela_api.ask.confidence import BACKCHECK_SYSTEM
    return BACKCHECK_SYSTEM


def _clarify() -> str:
    from tela_api.ask.confidence import CLARIFY_SYSTEM
    return CLARIFY_SYSTEM


def _plan() -> str:
    from tela_api.ask.planner import AG03_SYSTEM
    return AG03_SYSTEM


def _freehand() -> str:
    from tela_api.ask.freehand import AG03_FREEHAND
    return AG03_FREEHAND


def _link() -> str:
    from tela_api.ask.linking import LINK_SYSTEM
    return LINK_SYSTEM


def _rewrite() -> str:
    from tela_api.ask.rewrite import REWRITE_SYSTEM
    return REWRITE_SYSTEM


def _ag04() -> str:
    from tela_api.model.assistant import AG04_SYSTEM
    return AG04_SYSTEM


def _ag04_correct() -> str:
    from tela_api.model.assistant import AG04_CORRECT
    return AG04_CORRECT


def _ag04_review() -> str:
    from tela_api.model.assistant import AG04_REVIEW
    return AG04_REVIEW


def _map_suggest() -> str:
    from tela_api.packs.ai_suggest import SYSTEM
    return SYSTEM


def _pack_author() -> str:
    from tela_api.packs.studio.author_llm import SYSTEM
    return SYSTEM


def _ag05() -> str:
    from tela_api.content.plan_review import AG05_SYSTEM
    return AG05_SYSTEM


def _ag06() -> str:
    from tela_api.content.chat_review import SYSTEM
    return SYSTEM


def _ag07() -> str:
    from tela_api.ask.advisor import AG07_SYSTEM
    return AG07_SYSTEM


def _narrate() -> str:
    from tela_api.ask.narrator import AG08_V2_SYSTEM
    return AG08_V2_SYSTEM


def _explain() -> str:
    from tela_api.ask.orchestrator import EXPLAIN_SYSTEM
    return EXPLAIN_SYSTEM


def _ag09() -> str:
    from tela_api.governance.certification import AG09_SYSTEM
    return AG09_SYSTEM


def _ag10() -> str:
    from tela_api.content.sql_context import AG10_ROLE
    return AG10_ROLE


def _ag10_custom() -> str:
    from tela_api.content.sql_context import AG10_AGGREGATE, AG10_PAGE_FILTERS
    return AG10_PAGE_FILTERS + AG10_AGGREGATE


def _review_frame() -> str:
    from tela_api.llm.review import FRAME
    return FRAME


PROMPTS: tuple[PromptDef, ...] = (
    PromptDef("ask.route", "AG-01", "intent", "Routing review",
              "Ask, with LLM review on: checks the rules' intent and source for the question.", _route),
    PromptDef("ask.frame", "AG-01", "frame", "Question frame",
              "Ask: reads the question's shape (figure, list, breakdown, trend …) and whether it follows on.", _frame),
    PromptDef("ask.backcheck", "AG-02", "clarify", "Answer back-check",
              "Ask: checks that the planned answer answers the question; a mismatch lowers confidence.", _backcheck),
    PromptDef("ask.clarify", "AG-02", "clarify", "Clarifying question",
              "Ask: words the clarifying question and its option labels.", _clarify),
    PromptDef("ask.plan", "AG-03", "plan", "Query planner",
              "Ask: plans the Query Spec, and reviews the rules' plan when LLM review is on.", _plan),
    PromptDef("ask.freehand", "AG-03", "plan", "Free-hand planning",
              "Ask: added to the planner prompt when free-hand answers are allowed.", _freehand),
    PromptDef("ask.link", "AG-03", "plan", "Linking words to fields",
              "Ask: picks measures and fields from the catalogue when the rules found no match.", _link),
    PromptDef("ask.rewrite", "AG-03", "plan", "Follow-up rewrite",
              "Ask: rewrites a follow-up into a complete question.", _rewrite),
    PromptDef("model.propose", "AG-04", "model_assist", "Model proposal",
              "Model studio: proposes links, measures and dimensions from the catalogue.", _ag04),
    PromptDef("model.correct", "AG-04", "model_assist", "Model correction",
              "Model studio: applies an Admin's instruction to the draft.", _ag04_correct),
    PromptDef("model.review", "AG-04", "model_assist", "Model review",
              "Model studio, with LLM review on: reviews the rules' proposal.", _ag04_review),
    PromptDef("packs.map_suggest", "AG-04", "map_suggest", "Pack mapping suggestions",
              "Packs: suggests which columns a pack's fields map to.", _map_suggest),
    PromptDef("packs.author", "AG-04", "pack_author", "Pack author",
              "Pack Studio: drafts a pack stage by stage.", _pack_author, editable=False,
              note="Built from a template that depends on the send-samples setting (DEC-27), so it is read-only. "
                   "Skills still apply."),
    PromptDef("reports.plan_review", "AG-05", "report_plan", "Report plan review",
              "Report planner, with LLM review on: reviews the rules' report plan.", _ag05),
    PromptDef("canvas.review", "AG-06", "canvas_edit", "Canvas edit review",
              "Report canvas, with LLM review on: reviews the rules' reading of a layout instruction.", _ag06),
    PromptDef("ask.visual", "AG-07", "visual_advice", "Visual advice",
              "Ask: chooses the visual among the suitable ones.", _ag07),
    PromptDef("ask.narrate", "AG-08", "narrate", "Answer narration",
              "Ask: writes the headline and sentences from the computed figures.", _narrate),
    PromptDef("ask.explain", "AG-08", "explain", "Measure explanation",
              "Ask: explains what a measure means (\"Why this number?\").", _explain),
    PromptDef("certification.rephrase", "AG-09", "cert_review", "Rule rephrasing",
              "Certification: rephrases a measure's rule for the reviewer.", _ag09),
    PromptDef("sql.role", "AG-10", "sql_draft", "SQL drafter role",
              "Custom SQL and Direct SQL: the drafter's instructions (the schema and question are added after).",
              _ag10),
    PromptDef("sql.custom", "AG-10", "sql_draft", "Custom SQL drafting rules",
              "Custom SQL: follows the role; asks for an aggregate query with :page_filters.", _ag10_custom),
    PromptDef("review.frame", "shared", "*", "LLM review frame",
              "Every LLM review (AG-01, AG-03, AG-04, AG-05, AG-06): added after the agent's review prompt.",
              _review_frame),
)
BY_KEY = {p.key: p for p in PROMPTS}


@cache
def default(key: str) -> str:
    return BY_KEY[key].loader()


def prompts_for(code: str) -> list[PromptDef]:
    return [p for p in PROMPTS if p.agent == code]
