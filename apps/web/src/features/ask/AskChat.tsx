import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Award, BookOpen, CircleAlert, FileText, Pencil, Pin, Rows3, Shapes, Undo2, Workflow } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { api, ApiError } from "../../api/client";
import type { Chip, Clarify, Column, Narrative, TurnComplete, Viz } from "../../api/app";
import type { FieldDraft } from "../../api/govern";
import { streamSse, type SseEvent } from "../../api/sse";
import { can, useMe } from "../../app/me";
import { Chart } from "../../components/Chart";
import { ContentBadge } from "../../components/ContentBadge";
import { DefinitionButton } from "../../components/Definition";
import { DrillSheet, type DrillTarget } from "../../components/DrillSheet";
import { formatAsOf, formatValue } from "../../components/format";
import { PinDialog } from "../../components/PinDialog";
import { SuggestionsButton } from "../../components/Suggestions";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "../../components/ui/dropdown-menu";
import { Input } from "../../components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { AnswerFeedback } from "./AnswerFeedback";
import { ActionBar, ActionDivider, AnswerMeta, AssistantMessage, CopyAction, FollowChip, IconAction, Thinking, UserMessage, ViewSwitch } from "./ChatParts";
import { AskNext, DataNotes, DriverPanel, InsightList } from "./AnswerStory";
import { BesidePanel, RequestAccessButton, RuleCard } from "./FreehandRule";
import { DirectSqlAnswer, RefusedSql } from "./DirectSqlAnswer";

const QUICK: { label: string; ops: Record<string, unknown>[] }[] = [
  { label: "Top 5", ops: [{ op: "set_limit", value: 5 }] },
  { label: "As table", ops: [{ op: "set_visual", value: "table" }] },
  { label: "By month", ops: [{ op: "set_time", grain: "month" }] },
  { label: "Compare with previous period", ops: [{ op: "compare_previous" }] },
];

export interface Turn {
  id: string;
  question: string;
  stages: string[];
  chips: Chip[];
  viz?: Viz;
  headline?: string;
  sentences?: { text: string }[];
  /** The narrative event in full: cited insights, data notes, driver and Ask next (US-186). */
  story?: Narrative;
  complete?: TurnComplete;
  clarify?: Clarify;
  error?: { code: string; message: string; missing_data?: string[]; examples?: string[];
    did_you_mean?: { label: string; measure: string } | null; understood?: string[]; unrecognised?: string[];
    choices?: { connection_id: string; name: string; engine: string }[];
    freehand_access?: { freehand: "granted" | "can_request"; connection_id: string; connection: string };
    /** A Direct SQL refusal (US-257b): the SQL that failed the check, when there was one. */
    sql?: string | null };
  notice?: string;
  lastEventId?: string | null;
}

/** What a turn is asked about (`context` on `POST /ask/turns`): a dashboard, one of its widgets, its page filters. */
export interface AskContext {
  dashboard_id?: string;
  dashboard_version_id?: string | null;
  widget_id?: string;
  page_filters?: unknown[];
}

type Started = { turn_id: string | null; conversation_id?: string; message?: string };

/**
 * One Ask conversation (US-189: shared by the Ask page and the dashboard's Ask panel): starts turns, follows their
 * streams, refines, answers clarifying questions and undoes. `open` replays a saved conversation's turns.
 */
export function useAskSession() {
  const qc = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);

  const update = (id: string, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  const follow = async (turnId: string, lastEventId?: string | null) => {
    await streamSse(`/api/v1/ask/turns/${turnId}/stream`, (e: SseEvent) => {
      const data = e.data as Record<string, unknown>;
      update(turnId, (t) => {
        const next: Turn = { ...t, stages: [...t.stages, e.event], lastEventId: e.id ?? t.lastEventId };
        if (e.event === "plan") next.chips = (data.chips as Chip[]) ?? [];
        if (e.event === "viz") next.viz = data as unknown as Viz;
        if (e.event === "narrative") {
          next.headline = data.headline as string;
          next.sentences = (data.sentences as { text: string }[]) ?? [];
          next.story = data as unknown as Narrative;
        }
        if (e.event === "complete") next.complete = data as unknown as TurnComplete;
        // A clarify followed by more events was answered (a reopened conversation replays both): no buttons then.
        next.clarify = e.event === "clarify" ? data as unknown as Clarify : undefined;
        if (e.event === "error") next.error = data as Turn["error"];
        return next;
      });
    }, { lastEventId });
  };

  const start = async (question: string, call: () => Promise<Started>, from?: Turn): Promise<string | null> => {
    try {
      const resp = await call();
      if (!resp.turn_id) {
        if (from) update(from.id, (t) => ({ ...t, notice: resp.message }));
        return null;
      }
      if (resp.conversation_id && resp.conversation_id !== conversationId) {
        setConversationId(resp.conversation_id);
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
      setTurns((ts) => [...ts, { id: resp.turn_id!, question, stages: [], chips: [] }]);
      await follow(resp.turn_id);
      return resp.turn_id;
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Chat is temporarily unavailable. Certified figures still work.";
      if (from) update(from.id, (t) => ({ ...t, notice: message }));
      else setTurns((ts) => [...ts, { id: `local-${Date.now()}`, question, stages: [], chips: [], error: { code: "error", message } }]);
      return null;
    }
  };

  const ask = (question: string, context: AskContext = {}, connectionId?: string | null) =>
    start(question, () => api<Started>("/api/v1/ask/turns", {
      method: "POST",
      body: { question, conversation_id: conversationId, ...(connectionId ? { connection_id: connectionId } : {}), context },
    }));

  const undo = (turnId: string, question: string) =>
    start(`Undo: ${question}`, () => api<Started>(`/api/v1/ask/turns/${turnId}/undo`, { method: "POST" }));

  const refine = async (turn: Turn, body: { instruction?: string; chip_ops?: Record<string, unknown>[] }, label: string) => {
    const id = await start(label, () => api<Started>(`/api/v1/ask/turns/${turn.id}/refine`, { method: "POST", body }), turn);
    if (id) toast(`Refined: ${label}`, { action: { label: "Undo", onClick: () => void undo(id, label) } });
  };

  const answer = async (turn: Turn, optionId: string) => {
    update(turn.id, (t) => ({ ...t, clarify: undefined }));
    try {
      await api(`/api/v1/ask/turns/${turn.id}/clarification`, { method: "POST", body: { option_id: optionId } });
    } catch (e) {
      const message = e instanceof ApiError ? `${e.message} Ask it again to get an answer.` :
        "Chat is temporarily unavailable. Certified figures still work.";
      update(turn.id, (t) => ({ ...t, notice: message }));
      return;
    }
    await follow(turn.id, turn.lastEventId);
  };

  const reset = () => {
    setTurns([]);
    setConversationId(null);
  };

  const open = async (id: string) => {
    try {
      const conv = await api<{ id: string; turns: { id: string; question: string }[] }>(`/api/v1/ask/conversations/${id}`);
      setConversationId(conv.id);
      setTurns(conv.turns.map((t) => ({ id: t.id, question: t.question, stages: [], chips: [] })));
      for (const t of conv.turns) await follow(t.id);
    } catch (e) {
      setTurns([{ id: "local-open", question: "Open conversation", stages: [], chips: [], error: { code: "error", message: (e as Error).message } }]);
    }
  };

  return { turns, conversationId, ask, undo, refine, answer, reset, open };
}

export type AskSession = ReturnType<typeof useAskSession>;

/**
 * The conversation (US-261: laid out like a chat): each question as a bubble, then the assistant's reply beside its
 * avatar — the thinking line while it streams, a clarifying question, an error with its ways forward (another
 * source, did you mean, closest measures, request free-hand access) or the answer. `onAsk` asks again in the caller's
 * context. Quick refinements show under the latest answer only.
 */
export function TurnList({ session, onAsk, pinTarget = null }: {
  session: AskSession;
  onAsk: (question: string, connectionId?: string) => void;
  pinTarget?: string | null;
}) {
  const { turns, answer, refine, undo } = session;
  const latest = turns[turns.length - 1]?.id;
  return (
    <ol className="grid gap-8" aria-label="Conversation">
      {turns.map((t) => (
        <li key={t.id} className="grid min-w-0 gap-4">
          <UserMessage>{t.question}</UserMessage>
          {!t.complete && !t.error && !t.clarify && <Thinking stages={t.stages} />}
          {t.clarify && (
            <AssistantMessage>
              <div role="group" aria-label="Clarifying question" className="grid gap-3">
                <p>{t.clarify.question}</p>
                {t.clarify.why && t.clarify.why.length > 0 && <p className="text-small text-ink-muted">Why I'm asking: {t.clarify.why.join("; ")}.</p>}
                <div className="flex flex-wrap gap-2">
                  {t.clarify.options.map((o) => <FollowChip key={o.id} onClick={() => void answer(t, o.id)}>{o.label}</FollowChip>)}
                </div>
              </div>
            </AssistantMessage>
          )}
          {t.error && <AssistantMessage><TurnError turn={t} onAsk={onAsk} /></AssistantMessage>}
          {t.complete && (
            <AssistantMessage>
              <AnswerCard turn={t} pinTarget={pinTarget} latest={t.id === latest} onRefine={(b, l) => void refine(t, b, l)}
                onUndo={() => void undo(t.id, t.question)} onFollowUp={(q) => onAsk(q, t.complete?.connection_id ?? undefined)} />
            </AssistantMessage>
          )}
          {t.notice && <AssistantMessage><Alert variant="warn"><p>{t.notice}</p></Alert></AssistantMessage>}
        </li>
      ))}
    </ol>
  );
}

/** Why a turn has no answer, said plainly, with the ways forward as chips. */
function TurnError({ turn: t, onAsk }: { turn: Turn; onAsk: (question: string, connectionId?: string) => void }) {
  const e = t.error!;
  return (
    <div role="alert" data-ask-error className="grid gap-3 rounded-[16px] border border-status-warn/30 bg-status-warn-soft/60 px-4 py-3">
      <p className="flex items-start gap-2 text-ink">
        <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-status-warn" />
        <span className="min-w-0">{e.message}</span>
      </p>
      {e.sql && <RefusedSql sql={e.sql} />}
      {((e.understood?.length ?? 0) > 0 || (e.unrecognised?.length ?? 0) > 0) && (
        <div className="grid gap-0.5 text-small text-ink-muted">
          {e.understood && e.understood.length > 0 && <p>Understood: {e.understood.join(", ")}</p>}
          {e.unrecognised && e.unrecognised.length > 0 && <p>Not recognised: {e.unrecognised.join(", ")}</p>}
        </div>
      )}
      {e.choices && e.choices.length > 0 && (
        <ChipRow label="Answer from" group="Choose a source">
          {e.choices.map((c) => <FollowChip key={c.connection_id} onClick={() => onAsk(t.question, c.connection_id)}>{c.name}</FollowChip>)}
        </ChipRow>
      )}
      {e.did_you_mean && (
        <ChipRow label="Did you mean">
          <FollowChip onClick={() => onAsk(e.did_you_mean!.label)}>{e.did_you_mean.label}</FollowChip>
        </ChipRow>
      )}
      {e.examples && e.examples.length > 0 && (
        <ChipRow label="Try asking about" group="Closest measures">
          {e.examples.map((x) => <FollowChip key={x} onClick={() => onAsk(x)}>{x}</FollowChip>)}
        </ChipRow>
      )}
      {e.freehand_access?.freehand === "can_request" && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-small text-ink-muted">No certified measure fits? An Admin can let you ask free-hand questions on {e.freehand_access.connection}.</span>
          <RequestAccessButton connectionId={e.freehand_access.connection_id} connection={e.freehand_access.connection} question={t.question} />
        </div>
      )}
    </div>
  );
}

function ChipRow({ label, group, children }: { label: string; group?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2" role={group ? "group" : undefined} aria-label={group}>
      <span className="text-small text-ink-muted">{label}</span>
      {children}
    </div>
  );
}

/** No highlight: one array, so the chart is not redrawn on every render. */
const NONE: string[] = [];

/** "Save as gold example" (US-056): an Admin's is approved at once; a Creator's becomes a suggestion for an Admin. */
function SaveGoldButton({ turn }: { turn: Turn }) {
  const [saved, setSaved] = useState(false);
  const spec = turn.complete!.spec!;
  const save = async () => {
    try {
      const out = await api<{ status: string }>("/api/v1/knowledge/gold-examples", {
        method: "POST", body: { question: turn.question.replace(/^Undo: /, ""), expected_spec: spec, connection_id: spec.connection_id, turn_id: turn.id },
      });
      setSaved(true);
      toast.success(out.status === "approved" ? "Saved as a gold example" : "Suggested as a gold example — an Admin will review it");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "The gold example could not be saved.");
    }
  };
  return (
    <IconAction label={saved ? "Saved as gold example" : "Save as gold example"} icon={<Award aria-hidden />}
      disabled={saved} onClick={() => void save()} />
  );
}

/** "New field" on an answer: group the values of a dimension it is broken down by (FEAT-028 dimension menu; US-075). */
function NewFieldMenu({ connectionId, columns }: { connectionId: string; columns: Column[] }) {
  const navigate = useNavigate();
  const dims = columns.filter((col) => col.kind === "dimension" && col.ref.startsWith("dimension:"));
  if (dims.length === 0) return null;
  const open = (col: Column) => {
    const draft: FieldDraft = { draft: { kind: "grouping", name: `${col.label} groups`, connection_id: connectionId, source_ref: col.ref }, source_label: col.label };
    navigate("/fields/new", { state: { draft } });
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconAction label="New field" icon={<Shapes aria-hidden />} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Personal field from this answer</DropdownMenuLabel>
        {dims.map((col) => <DropdownMenuItem key={col.ref} onSelect={() => open(col)}>Group {col.label} values…</DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AnswerCard({ turn, pinTarget, latest, onRefine, onUndo, onFollowUp }: {
  turn: Turn;
  pinTarget: string | null;
  latest: boolean;
  onRefine: (body: { instruction?: string; chip_ops?: Record<string, unknown>[] }, label: string) => void;
  onUndo: () => void;
  /** A follow-up question in this conversation, on this answer's source (Direct SQL's drill and refine, US-260). */
  onFollowUp: (question: string) => void;
}) {
  const me = useMe().data;
  const c = turn.complete!;
  const [view, setView] = useState<"chart" | "table">("chart");
  const [drill, setDrill] = useState<DrillTarget | null>(null);
  const [pinning, setPinning] = useState(false);
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const story = turn.story;
  const facts = story?.insights ?? [];
  const lead = story?.cited?.flatMap((x) => x.facts).find((id) => (facts.find((f) => f.id === id)?.members.length ?? 0) > 0) ?? null;
  const [focus, setFocus] = useState<string | null>(null);
  const focused = focus ?? lead;
  const highlight = useMemo(() => story?.insights?.find((f) => f.id === focused)?.members ?? NONE, [story, focused]);
  if (c.action?.kind === "plan_report") {
    return (
      <article className="grid gap-3" aria-label="Answer">
        <h2 className="text-heading">{turn.headline}</h2>
        <Button asChild className="justify-self-start rounded-full"><Link to={`/reports/new?request=${encodeURIComponent(c.action.request)}`}><FileText aria-hidden />Open the report planner</Link></Button>
      </article>
    );
  }
  if (c.action?.kind === "build_field") {
    const draft: FieldDraft = c.action;
    return (
      <article className="grid gap-3" aria-label="Answer">
        <h2 className="text-heading">{turn.headline}</h2>
        {turn.sentences?.map((s, i) => <p key={i} className="text-ink-muted">{s.text}</p>)}
        <p className="text-small text-ink-muted">Nothing is saved until you save it in the builder.</p>
        <Button asChild className="justify-self-start rounded-full"><Link to="/fields/new" state={{ draft }}><Shapes aria-hidden />Open in the field builder</Link></Button>
      </article>
    );
  }
  if (c.definition) {
    const explained = (turn.sentences ?? []).filter((s) => s.text !== c.definition!.blank_handling);
    const citations = c.definition.citations ?? [];
    return (
      <article className="grid gap-2" aria-label="Answer">
        <div className="flex flex-wrap items-center gap-2"><h2 className="text-heading">{c.definition.label}</h2><ContentBadge badge="certified" /></div>
        <p>{c.definition.rule_text}</p>
        {explained.map((s, i) => <p key={i} className="text-ink-muted">{s.text}</p>)}
        <p className="text-small text-ink-muted">{c.definition.blank_handling} {c.definition.rule_ref ? `(${c.definition.rule_ref})` : ""}</p>
        {citations.length > 0 && (
          <section className="grid gap-1.5 rounded-[16px] border border-line bg-surface-raised p-4" aria-label="From approved knowledge">
            <h3 className="flex items-center gap-1.5 text-small font-medium text-ink-muted"><BookOpen aria-hidden className="size-3.5" />From approved knowledge</h3>
            <ul className="grid gap-1.5">
              {citations.map((k) => (
                <li key={k.chunk_id} className="text-small"><span className="font-medium">{k.title}</span> — <q>{k.snippet}</q></li>
              ))}
            </ul>
          </section>
        )}
        {c.notes?.map((n) => <p key={n} className="text-small text-ink-muted">{n}</p>)}
      </article>
    );
  }
  if (c.ask_mode === "direct_sql") return <DirectSqlAnswer turn={turn} pinTarget={pinTarget} latest={latest} onFollowUp={onFollowUp} />;
  const columns = (c.columns ?? []) as Column[];
  const rows = c.rows ?? [];
  const measureRef = c.spec?.measure.ref;
  const measureIdx = columns.findIndex((col) => col.kind === "measure");
  const single = rows.length === 1 && columns.length === 1 && measureIdx === 0;
  const chartable = !c.hidden && !single && turn.viz?.chart_spec && !["table", "figure"].includes(turn.viz.chart_spec.type);
  const measureLabel = c.definitions?.[0]?.label;
  const rule = c.rule ?? null;
  const definition = measureRef && c.spec && !rule
    ? { refId: measureRef, connectionId: c.spec.connection_id, modelVersionId: c.spec.model_version_id, filtersText: c.filters_text, spec: c.spec }
    : undefined;
  const drillable = can(me, "drill.use") && !!c.spec && !c.hidden;
  const figure = single ? formatValue(rows[0]![0], columns[0]!.format) : null;
  const badge = c.certified ? <ContentBadge badge="certified" />
    : c.counted_by || c.badge === "definition_conflict" || c.badge === "filter_left_out" ? <ContentBadge badge={c.badge ?? "your_definition"} />
    : rule ? <ContentBadge badge="not_certified" /> : undefined;
  const meta = [
    { text: `Data as of ${formatAsOf(c.as_of)}` },
    ...(c.connection ? [{ text: `${c.connection} · model v${c.model_version}` }] : []),
    ...(c.interpreted ?? []).map((text) => ({ text })),
    ...(c.confidence && c.confidence.score < 1
      ? [{ text: `Confidence ${Math.round(c.confidence.score * 100)}%${c.confidence.score < c.confidence.threshold ? " · low" : ""}`, title: c.confidence.why.join("; ") || undefined }]
      : []),
    ...turn.chips.filter((ch) => ch.kind !== "rule").map((ch) => ({ text: ch.label })),
  ];
  const copyText = [turn.headline, figure && `${measureLabel ?? "Value"}: ${figure}`, ...(turn.sentences ?? []).map((s) => s.text)].filter(Boolean).join("\n");
  const sendInstruction = () => {
    if (!instruction.trim()) return;
    onRefine({ instruction: instruction.trim() }, instruction.trim());
    setInstruction("");
    setEditing(false);
  };
  const tools = (
    <>
      {c.can_pin && can(me, "widgets.create") && c.spec && <IconAction label={rule ? "Add as widget" : "Pin"} icon={<Pin aria-hidden />} onClick={() => setPinning(true)} />}
      {drillable && <IconAction label="Drill to records" icon={<Rows3 aria-hidden />} onClick={() => setDrill({ title: turn.headline ?? turn.question, mark: {}, spec: c.spec, definition })} />}
      {definition && (
        <DefinitionButton title={measureLabel ?? "this figure"} {...definition}>
          <IconAction label="Why this number?" icon={<BookOpen aria-hidden />} />
        </DefinitionButton>
      )}
      {c.spec && !c.hidden && can(me, "widgets.create") && (
        <SuggestionsButton url={`/api/v1/ask/turns/${turn.id}/suggestions`} label="this answer" compact
          onApply={(s) => onRefine({ chip_ops: s.ops }, s.title)} />
      )}
      {c.spec && c.certified && can(me, "widgets.create") && <SaveGoldButton turn={turn} />}
      {c.spec && !c.hidden && can(me, "fields.create") && <NewFieldMenu connectionId={c.spec.connection_id} columns={columns} />}
      {c.refined_from && <IconAction label="Undo" icon={<Undo2 aria-hidden />} onClick={onUndo} />}
      {can(me, "settings.manage") && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button asChild variant="ghost" size="icon" className="size-8 rounded-lg text-ink-muted hover:text-ink">
              <Link to={`/admin/agent-runs?turn_id=${turn.id}`} aria-label="Agent runs"><Workflow aria-hidden /></Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Agent runs</TooltipContent>
        </Tooltip>
      )}
    </>
  );
  return (
    <article data-ask-answer className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4" aria-label="Answer">
      {c.widget && <p className="text-small text-ink-muted">About “{c.widget.title}”</p>}
      <div className="grid min-w-0 gap-1.5">
        <h2 className="text-heading">{turn.headline}</h2>
        {turn.sentences?.map((s, i) => <p key={i} className="text-ink-muted">{s.text}</p>)}
        {c.counted_by?.definition && (
          <p className="text-small text-ink-muted">“{c.counted_by.definition.term}” means: {c.counted_by.definition.meaning} · from “{c.counted_by.definition.document.title ?? "an approved document"}”</p>
        )}
      </div>
      {single && (
        <div data-ask-frame className="grid w-fit min-w-56 max-w-full gap-0.5 rounded-[16px] border border-line bg-surface-raised px-5 py-4">
          <p className="text-small font-medium text-ink-muted">{measureLabel ?? "Value"}</p>
          <p className="font-mono text-figure-xl tabular-nums text-ink" aria-label={`${measureLabel ?? "Value"}: ${figure}`}>{figure}</p>
          {c.filters_text && <p className="text-small text-ink-muted">{c.filters_text}</p>}
        </div>
      )}
      <InsightList cited={story?.cited ?? []} facts={facts} format={columns[measureIdx]?.format} focus={focused} onFocus={(id) => id && setFocus(id)} />
      {rule && c.spec && (
        <RuleCard rule={rule} connectionId={c.spec.connection_id}
          onChange={(chip, change, label) => onRefine({ chip_ops: [{ op: "set_condition", path: chip.path, ...change }] }, label)} />
      )}
      {c.hidden ? <p className="text-ink-muted">{c.message}</p> : !single && (
        <figure data-ask-frame className="m-0 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2 rounded-[16px] border border-line bg-surface-raised p-4">
          {chartable && <div className="flex justify-end"><ViewSwitch view={view} onChange={setView} /></div>}
          <Chart spec={turn.viz?.chart_spec ?? null} columns={columns} rows={rows} title={turn.headline} view={chartable ? view : undefined} highlight={highlight}
            onMark={drillable ? (mark) => setDrill({ title: turn.headline ?? turn.question, mark, spec: c.spec, definition }) : undefined} />
          {turn.viz?.note && <figcaption className="text-small text-ink-muted">{turn.viz.note}</figcaption>}
        </figure>
      )}
      {c.hidden && turn.viz?.note && <p className="text-small text-ink-muted">{turn.viz.note}</p>}
      {c.beside && (
        <BesidePanel beside={c.beside} main={{
          label: rule?.label ?? (c.counted_by ? c.counted_by.definition?.term ?? "Your definition" : measureLabel) ?? "This answer",
          plain_rule: rule?.plain_rule ?? c.counted_by?.rule ?? c.definitions?.[0]?.plain_rule ?? "",
          value: single ? rows[0]![0] : undefined, format: columns[measureIdx]?.format, certified: !!c.certified,
          tag: c.counted_by ? (c.counted_by.source === "question" ? "Counted by your definition" : "Approved definition") : undefined }} />
      )}
      {story?.driver && !c.hidden && (
        <DriverPanel driver={story.driver} format={columns[measureIdx]?.format} spec={c.spec} onApply={(ops, label) => onRefine({ chip_ops: ops }, label)} />
      )}
      {c.notes?.map((n) => <p key={n} className="text-small text-ink-muted">{n}</p>)}
      <DataNotes notes={c.hidden ? [] : story?.notes ?? []} onApply={c.spec ? (ops, label) => onRefine({ chip_ops: ops }, label) : undefined} />
      <AnswerMeta badge={badge} items={meta} />
      {c.spec ? (
        <AnswerFeedback turnId={turn.id} before={<CopyAction text={copyText} />}>{tools}</AnswerFeedback>
      ) : (
        <ActionBar><CopyAction text={copyText} /><ActionDivider />{tools}</ActionBar>
      )}
      {c.spec && !c.hidden && <AskNext next={story?.next ?? []} onApply={(ops, label) => onRefine({ chip_ops: ops }, label)} />}
      {c.spec && latest && (
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Refine">
            {QUICK.map((q) => <FollowChip key={q.label} onClick={() => onRefine({ chip_ops: q.ops }, q.label)}>{q.label}</FollowChip>)}
            <FollowChip aria-expanded={editing} onClick={() => setEditing(!editing)}><Pencil aria-hidden />Change this answer</FollowChip>
          </div>
          {editing && (
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); sendInstruction(); }}>
              <label htmlFor={`refine-${turn.id}`} className="sr-only">Refine this answer</label>
              <Input id={`refine-${turn.id}`} autoFocus value={instruction} maxLength={500} onChange={(e) => setInstruction(e.target.value)}
                placeholder="Only KSA, top 5, as columns, by month…" className="rounded-full" />
              <Button type="submit" variant="secondary" className="rounded-full" disabled={!instruction.trim()}>Refine<ArrowRight aria-hidden /></Button>
            </form>
          )}
        </div>
      )}
      <DrillSheet target={drill} onClose={() => setDrill(null)} />
      {pinning && c.spec && (
        <PinDialog turnId={turn.id} spec={c.spec} defaultTitle={turn.question.replace(/^Undo: /, "")} defaultVisual={turn.viz?.visual}
          defaultReportId={pinTarget} onClose={() => setPinning(false)} />
      )}
    </article>
  );
}
