import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, BarChart3, History, Lightbulb, MessageSquarePlus, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../api/client";
import type { ConversationItem, Home } from "../../api/app";
import type { AskVerbosity, Me } from "../../api/types";
import { can, useMe } from "../../app/me";
import { formatRelative } from "../../components/format";
import { Button } from "../../components/ui/button";
import { NativeSelect } from "../../components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/utils";
import { type AskContext, TurnList, useAskSession } from "./AskChat";
import { useLayout } from "../../design/layout";
import { AnswerPanel } from "./AnswerPanel";
import { Composer, type ComposerHandle } from "./ChatParts";

interface WidgetContext {
  widget_id: string;
  dashboard_version_id: string | null;
  page_filters: unknown[];
  title: string | null;
}

const contextOf = (w: WidgetContext | null): AskContext =>
  w ? { widget_id: w.widget_id, dashboard_version_id: w.dashboard_version_id, page_filters: w.page_filters } : {};

/**
 * Ask (FEAT-018 – 022; SCR-02; US-140): conversation history beside the chat, progress by stage, clarifying questions
 * and refine suggestions as chips, and answer cards that explain themselves (definition, scope, as-of) with Pin,
 * Drill and chart ↔ table. Card v2 (US-186): headline, cited insights with their facts, the chart with the item an
 * insight names highlighted, What's driving this, data notes, Ask next and How this was worked out. Refinements can be undone from a toast. `?c=` reopens a conversation by replaying its
 * turns' streams (rows are re-read in the reader's scope, never stored).
 */
export function AskPage() {
  const [params, setParams] = useSearchParams();
  const session = useAskSession();
  const { turns, conversationId } = session;
  const [draft, setDraft] = useState("");
  const [widget, setWidget] = useState<WidgetContext | null>(null);
  const [pinTarget] = useState<string | null>(params.get("report"));
  const [history, setHistory] = useState(false);
  const asked = useRef<string | null>(null);
  const opened = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);

  const ask = (question: string, ctx: WidgetContext | null = widget, connectionId?: string) =>
    session.ask(question, contextOf(ctx), connectionId);

  const newConversation = () => {
    session.reset();
    setWidget(null);
    opened.current = null;
    setParams({}, { replace: true });
    setHistory(false);
  };

  // A question handed over from Home, the ask bar, the palette or a dashboard widget.
  useEffect(() => {
    const q = params.get("q");
    if (q && asked.current !== q) {
      asked.current = q;
      let ctx: WidgetContext | null = null;
      const w = params.get("widget");
      if (w) {
        let pf: unknown[] = [];
        try { pf = JSON.parse(params.get("pf") ?? "[]") as unknown[]; } catch { pf = []; }
        ctx = { widget_id: w, dashboard_version_id: params.get("dv"), page_filters: pf, title: params.get("wt") };
        setWidget(ctx);
      }
      setParams(pinTarget ? { report: pinTarget } : {}, { replace: true });
      void ask(q, ctx);
    }
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reopen a conversation from the history (?c=).
  useEffect(() => {
    const c = params.get("c");
    if (!c || opened.current === c) return;
    opened.current = c;
    setHistory(false);
    setWidget(null);
    void session.open(c);
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [turns.length]);

  const send = (question: string) => {
    void ask(question);
    setDraft("");
  };

  const aurora = useLayout() === "aurora";
  const historyList = <ConversationHistory current={conversationId} onNew={newConversation} />;
  const empty = turns.length === 0;
  return (
    <div className={cn("tl grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]", aurora && "xl:grid-cols-[15rem_minmax(0,1fr)_18rem]")}>
      <aside aria-label="Conversations" className="hidden min-w-0 lg:block">
        <div className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto pr-1">{historyList}</div>
      </aside>
      <Sheet open={history} onOpenChange={setHistory}>
        <SheetContent side="left" className="overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Conversations</SheetTitle>
            <SheetDescription>Your recent questions. Answers are re-run in your current data scope.</SheetDescription>
          </SheetHeader>
          <div className="px-4">{historyList}</div>
        </SheetContent>
      </Sheet>

      {/* The chat (US-261): one centred reading column, the composer pinned at the bottom. */}
      <div className="mx-auto grid min-h-[calc(100vh-9rem)] w-full min-w-0 max-w-3xl grid-rows-[auto_1fr_auto] gap-4">
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-heading">Ask</h1>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" className="lg:hidden" onClick={() => setHistory(true)}><History aria-hidden />History</Button>
            {!empty && <Button variant="ghost" size="sm" onClick={newConversation}><MessageSquarePlus aria-hidden />New chat</Button>}
          </div>
        </div>
        <div className="grid min-w-0 content-start gap-4">
          {empty ? <Welcome onPick={(q) => void ask(q)} /> : (
            <TurnList session={session} pinTarget={pinTarget} onAsk={(q, connectionId) => void ask(q, widget, connectionId)} />
          )}
          <div ref={end} />
        </div>
        <div className="sticky bottom-0 z-20 -mx-2 bg-gradient-to-t from-surface from-70% to-transparent px-2 pb-3 pt-6">
          <Composer ref={composer} id="followup" label={empty ? "Ask a question" : "Ask a follow-up"} value={draft} onChange={setDraft} onSubmit={send} submitLabel="Send"
            placeholder={empty ? "Ask anything about your PMO data…" : "Ask a follow-up, or say how to change the answer"}
            footer="Answers use certified measures, in your data scope.">
            {widget && (
              <span role="status" className="inline-flex max-w-full items-center gap-1 rounded-full border border-accent bg-accent-soft py-0.5 pl-2.5 pr-0.5 text-small">
                <span className="truncate">About the widget “{widget.title ?? "widget"}” with the dashboard's filters</span>
                <button type="button" className="rounded-full p-1 text-ink-muted hover:text-ink focus-visible:focus-ring" onClick={() => setWidget(null)} aria-label="Stop asking about this widget">
                  <X aria-hidden className="size-3" />
                </button>
              </span>
            )}
            <VerbosityPicker />
          </Composer>
        </div>
      </div>
      {/* Aurora (ENH-027): how the latest answer was worked out, beside the conversation. */}
      {aurora && <aside aria-label="About this answer" className="hidden min-w-0 xl:block"><div className="sticky top-20"><AnswerPanel turns={turns} /></div></aside>}
    </div>
  );
}

const SUGGESTION_ICONS = [BarChart3, Lightbulb, ArrowUpRight, Sparkles];

/** A new chat: a greeting and up to four questions to start from (Home's suggestions, built from certified measures). */
function Welcome({ onPick }: { onPick: (question: string) => void }) {
  const me = useMe().data;
  // Its own cache key: only the greeting and the suggestions are read here.
  const home = useQuery({ queryKey: ["ask-welcome"], queryFn: () => api<Home>("/api/v1/home"), enabled: can(me, "ask.use"), staleTime: 300_000 });
  const name = home.data?.greeting?.name ?? me?.display_name?.split(" ")[0];
  const offered = home.data?.suggested_questions;
  const questions = (Array.isArray(offered) ? offered : []).slice(0, 4);
  return (
    <section aria-label="Start a conversation" className="grid justify-items-center gap-6 py-10 text-center sm:py-16">
      <span aria-hidden className="inline-flex size-12 items-center justify-center rounded-[16px] bg-accent text-on-accent shadow-md">
        <Sparkles className="size-6" />
      </span>
      <div className="grid gap-1.5">
        {name && <p className="text-body text-ink-muted">Hello, {name}</p>}
        <h2 className="text-title">Ask about your PMO data</h2>
        <p className="max-w-lg text-ink-muted">Get a number, a chart or a breakdown in plain words. Answers use certified measures, in your data scope.</p>
      </div>
      {questions.length > 0 && <ul aria-label="Suggested questions" className="grid w-full gap-2 sm:grid-cols-2">
        {questions.map((q, i) => {
          const Icon = SUGGESTION_ICONS[i % SUGGESTION_ICONS.length]!;
          return (
            <li key={q}>
              <button type="button" onClick={() => onPick(q)}
                className="group flex h-full w-full items-start gap-3 rounded-[16px] border border-line bg-surface-raised p-4 text-left transition-colors hover:border-accent hover:bg-accent-soft focus-visible:focus-ring">
                <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-accent" />
                <span className="text-body text-ink">{q}</span>
              </button>
            </li>
          );
        })}
      </ul>}
    </section>
  );
}

/** Conversations listed before "Show all" (the API returns the latest 50). */
const HISTORY_LIMIT = 12;

function ConversationHistory({ current, onNew }: { current: string | null; onNew: () => void }) {
  const list = useQuery({ queryKey: ["conversations"], queryFn: () => api<{ items: ConversationItem[] }>("/api/v1/ask/conversations") });
  const [all, setAll] = useState(false);
  const today = new Date().toDateString();
  const every = list.data?.items ?? [];
  const items = all ? every : every.slice(0, HISTORY_LIMIT);
  const groups: [string, ConversationItem[]][] = [
    ["Today", items.filter((c) => new Date(c.updated_at).toDateString() === today)],
    ["Earlier", items.filter((c) => new Date(c.updated_at).toDateString() !== today)],
  ];
  return (
    <nav aria-label="Conversation history" className="grid min-w-0 gap-3">
      <Button variant="secondary" onClick={onNew}><MessageSquarePlus aria-hidden />New conversation</Button>
      {list.isLoading && <Skeleton className="h-24" />}
      {!list.isLoading && items.length === 0 && <p className="text-small text-ink-muted">Your conversations will appear here.</p>}
      {groups.filter(([, cs]) => cs.length > 0).map(([label, cs]) => (
        <div key={label} className="grid min-w-0 gap-1">
          <p className="px-2 text-label uppercase text-ink-muted">{label}</p>
          <ul className="grid min-w-0 gap-0.5">
            {cs.map((c) => (
              <li key={c.id} className="min-w-0">
                <Link to={`/ask?c=${c.id}`} aria-current={c.id === current ? "page" : undefined} title={formatRelative(c.updated_at)}
                  className={cn("block truncate rounded-md px-2 py-1.5 text-body hover:bg-surface-sunken focus-visible:focus-ring",
                    c.id === current ? "bg-accent-soft font-medium text-ink" : "text-ink-muted")}>
                  {c.title ?? "Untitled conversation"}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {every.length > HISTORY_LIMIT && (
        <Button variant="link" size="sm" className="justify-self-start px-2" aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${every.length}`}
        </Button>
      )}
    </nav>
  );
}

const VERBOSITY: { value: AskVerbosity; label: string }[] = [
  { value: "assume_first", label: "Assume first" },
  { value: "balanced", label: "Balanced" },
  { value: "ask_freely", label: "Ask freely" },
];

/** How readily Ask asks a clarifying question when it is unsure (US-182), saved as the person's preference. */
function VerbosityPicker() {
  const me = useMe().data;
  const qc = useQueryClient();
  const value = me?.preferences?.ask_verbosity ?? "balanced";
  const save = (ask_verbosity: AskVerbosity) =>
    api<Me["preferences"]>("/api/v1/me/preferences", { method: "PUT", body: { ask_verbosity } })
      .then((prefs) => qc.setQueryData<Me>(["me"], (old) => (old ? { ...old, preferences: prefs } : old)))
      .catch((err: Error) => toast.error("Your setting could not be saved", { description: err.message }));
  if (!me) return null;
  return (
    <label className="flex items-center gap-1.5 text-small text-ink-muted" title="How readily Ask checks with you when a question is unclear">
      Clarifying questions
      <NativeSelect className="w-32 [&_select]:h-7 [&_select]:rounded-full [&_select]:border-transparent [&_select]:bg-surface-sunken [&_select]:text-small" value={value} onChange={(e) => void save(e.target.value as AskVerbosity)}>
        {VERBOSITY.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </NativeSelect>
    </label>
  );
}
