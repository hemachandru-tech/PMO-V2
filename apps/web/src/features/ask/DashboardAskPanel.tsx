import { Bot, ExternalLink, Filter, MessageSquarePlus, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import type { Dashboard, DashboardWidget, PageFilter } from "../../api/app";
import { useAppName } from "../../app/branding";
import { Button } from "../../components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { TurnList, useAskSession } from "./AskChat";
import { Composer, type ComposerHandle } from "./ChatParts";
import type { AskAbout } from "./DashboardAskLauncher";

/** Widgets offered as "Ask about …" before the first question. */
const STARTERS = 3;
/** The question "Ask about this" on a widget fills in, as the old Ask-about dialog did. */
const ABOUT_QUESTION = "Why is this so high?";

/**
 * Ask on a dashboard (US-189; FEAT-022): the launcher (`DashboardAskLauncher`) opens Ask in this side panel beside
 * the dashboard, which stays usable (the panel is not modal). It is the Ask page's conversation (same streams, clarifying
 * questions, answer cards, refine, pin and drill) with the dashboard as context: its connection, its version and
 * the page filters the reader has set; "Ask about this" on a widget opens the panel about that widget. The
 * conversation stays while the reader is on the dashboard, and "Open in Ask" continues it full screen.
 */
export function DashboardAskPanel({ dashboard: d, filters, open, onOpenChange, about, onAboutChange }: {
  dashboard: Dashboard;
  filters: PageFilter[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  about: AskAbout | null;
  onAboutChange: (about: AskAbout | null) => void;
}) {
  const appName = useAppName();
  const session = useAskSession();
  const { turns, conversationId } = session;
  const [draft, setDraft] = useState("");
  const input = useRef<ComposerHandle>(null);
  const end = useRef<HTMLDivElement>(null);
  const widgets = useMemo(() => (d.tabs ?? []).flatMap((t) => t.sections
    ? t.sections.flatMap((s) => (s.items ?? []).filter((i) => i.kind === "widget") as unknown as DashboardWidget[])
    : t.widgets ?? []).filter((w) => !w.hidden && w.title), [d]);

  // "Ask about this" on a widget: the suggested question, ready to send or change.
  useEffect(() => {
    if (about) {
      setDraft(ABOUT_QUESTION);
      input.current?.focus();
    }
  }, [about]);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [turns.length]);

  const ask = (question: string, connectionId?: string) =>
    void session.ask(question, {
      dashboard_id: d.id, dashboard_version_id: d.version_id ?? null, page_filters: filters,
      ...(about ? { widget_id: about.id } : {}),
    }, connectionId ?? d.connection_id);

  const newConversation = () => {
    session.reset();
    onAboutChange(null);
    setDraft("");
    input.current?.focus();
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={false}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-xl"
        onOpenAutoFocus={(e) => { e.preventDefault(); input.current?.focus(); }}
        onInteractOutside={(e) => e.preventDefault()}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2"><Bot aria-hidden className="size-5 text-accent" />Ask about this dashboard</SheetTitle>
          <SheetDescription>{appName} answers with certified measures, in your data scope, using this dashboard's filters.</SheetDescription>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={newConversation} disabled={turns.length === 0}><MessageSquarePlus aria-hidden />New conversation</Button>
            {conversationId && (
              <Button asChild variant="ghost" size="sm"><Link to={`/ask?c=${conversationId}`}><ExternalLink aria-hidden />Open in Ask</Link></Button>
            )}
          </div>
        </SheetHeader>

        <div className="grid min-h-0 flex-1 content-start gap-4 overflow-y-auto p-4">
          {turns.length === 0 && (
            <div className="grid gap-3">
              <p className="text-ink-muted">Ask a question about the figures on this dashboard, for example “Which programmes have the most overdue go-lives?”</p>
              {widgets.length > 0 && !about && (
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Ask about a widget">
                  <span className="text-small text-ink-muted">Or ask about</span>
                  {widgets.slice(0, STARTERS).map((w) => (
                    <Button key={w.id} variant="secondary" size="sm" onClick={() => onAboutChange({ id: w.id, title: w.title })}>{w.title}</Button>
                  ))}
                </div>
              )}
            </div>
          )}
          <TurnList session={session} onAsk={ask} />
          <div ref={end} />
        </div>

        <div className="border-t border-line p-3">
          <Composer ref={input} id="dashboard-ask" label="Your question" value={draft} onChange={setDraft}
            onSubmit={(q) => { ask(q); setDraft(""); }}
            placeholder={turns.length ? "Ask a follow-up, or say how to change the answer" : "Ask a question"}>
            {filters.length > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-small text-ink-muted">
                <Filter aria-hidden className="size-3" />{filters.length === 1 ? "1 dashboard filter" : `${filters.length} dashboard filters`} applied
              </span>
            )}
            {about && (
              <span role="status" className="inline-flex min-w-0 items-center gap-1 rounded-full border border-accent bg-accent-soft py-0.5 pl-2 pr-0.5 text-small">
                <span className="truncate">About the widget “{about.title}”</span>
                <button type="button" className="rounded-full p-1 text-ink-muted hover:text-ink focus-visible:focus-ring" onClick={() => onAboutChange(null)}
                  aria-label="Stop asking about this widget">
                  <X aria-hidden className="size-3" />
                </button>
              </span>
            )}
          </Composer>
        </div>
      </SheetContent>
    </Sheet>
  );
}
