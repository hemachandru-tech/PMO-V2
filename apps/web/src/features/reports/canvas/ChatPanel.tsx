import { Check, MessageSquareText, Sparkles, Undo2, X } from "lucide-react";
import { useState } from "react";

import { api, type ApiError } from "../../../api/client";
import type { ChatChange, ChatClarifyOption, ChatPreview, Report } from "../../../api/authoring";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";

interface Applied { instruction: string; changes: ChatChange[] }

const VERDICT: Record<NonNullable<ChatPreview["review"]>["verdict"], string> = {
  confirm: "Checked by AI: the rules' reading confirmed",
  correct: "Checked by AI: changes marked AI were added by the AI",
  ask: "Checked by AI",
  fallback: "AI check unavailable: the rules' reading stands",
};

/**
 * Edit with chat (FEAT-027; US-071 – 073; US-141) as a side panel: describe a change, see the change list with
 * refused changes and their reasons (the affected widgets are highlighted on the canvas), then Apply. Applied edits
 * stay listed with "Undo these changes" while they are the last change on the report. With LLM review on (US-244) the
 * panel says how the AI checked the reading and marks the changes the AI added; nothing changes until Apply.
 */
export function ChatPanel({ report, onHighlight, onApplied, onUndo, onAlternative, onClose }: {
  report: Report;
  /** A refused chart change's one-click alternative: switch that widget to the suggested chart instead. */
  onAlternative: (itemId: string, visual: string, label: string) => void;
  onHighlight: (ids: string[]) => void;
  onApplied: (r: Report, summary: string) => void;
  onUndo: () => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [sent, setSent] = useState("");
  const [preview, setPreview] = useState<ChatPreview | null>(null);
  const [applied, setApplied] = useState<Applied[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const url = `/api/v1/reports/${report.id}/chat-edit`;

  const send = async (instruction: string, answered = false) => {
    setError(null);
    setBusy(true);
    try {
      const p = await api<ChatPreview>(url, { method: "POST", body: answered ? { instruction, answered } : { instruction } });
      setPreview(p);
      setSent(instruction);
      onHighlight(p.highlights);
    } catch (e) {
      setError(asApiError(e));
    } finally {
      setBusy(false);
    }
  };
  const close = () => { setPreview(null); onHighlight([]); };
  const apply = async () => {
    setBusy(true);
    try {
      const r = await api<Report>(`${url}/${preview!.preview_id}/apply`, { method: "POST" });
      setApplied([{ instruction: sent, changes: preview!.changes }, ...applied].slice(0, 5));
      close();
      setText("");
      onApplied(r, `Chat edit: ${sent}`);
    } catch (e) {
      setError(asApiError(e));
    } finally {
      setBusy(false);
    }
  };
  const discard = async () => {
    if (preview?.preview_id) await api(`${url}/${preview.preview_id}`, { method: "DELETE" }).catch(() => undefined);
    close();
  };
  const lastIsChat = report.undo_summary?.startsWith("Chat edit") ?? false;

  return (
    <aside aria-label="Edit with chat" className="grid max-h-[calc(100dvh-8rem)] content-start gap-3 overflow-y-auto rounded-lg border border-line bg-surface-raised p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-heading"><MessageSquareText aria-hidden className="size-4" />Edit with chat</h2>
        <Button variant="ghost" size="icon" className="size-control-sm" aria-label="Close edit with chat" onClick={() => { void discard(); onClose(); }}><X aria-hidden /></Button>
      </div>

      {preview?.clarification && (
        <div role="group" aria-label="Clarifying question" className="grid gap-2 rounded-md bg-surface-sunken p-3">
          <p>{preview.clarification.question}</p>
          {preview.clarification.why?.map((w) => <p key={w} className="text-small text-ink-muted">{w}</p>)}
          <div className="flex flex-wrap gap-1.5">
            {preview.clarification.options.map((o, i) => (
              <Button key={`${o.item_id ?? ""}-${i}`} variant="secondary" size="sm" onClick={() => {
                const next = answer(o, preview.clarification!.question, sent);
                setText(next);
                void send(next, true);
              }}>{o.title}</Button>
            ))}
          </div>
        </div>
      )}

      {preview && !preview.clarification && (
        <section aria-label="Proposed changes" className="grid gap-2 rounded-md border border-accent bg-accent-soft/40 p-3">
          <p className="text-small text-ink-muted">“{sent}”</p>
          {preview.review && (
            <p className="flex gap-1.5 text-small text-ink-muted"><Sparkles aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              <span>{VERDICT[preview.review.verdict]}{preview.review.reason ? ` — ${preview.review.reason}` : ""}</span></p>
          )}
          {preview.review?.notes.map((n) => <p key={n} className="text-small text-ink-muted">{n}</p>)}
          <ChangeList changes={preview.changes} onAlternative={onAlternative} />
          <div className="flex gap-2">
            <Button size="sm" disabled={!preview.preview_id || busy} onClick={() => void apply()}>Apply</Button>
            <Button size="sm" variant="ghost" onClick={() => void discard()}>Discard</Button>
          </div>
          {!preview.preview_id && <p className="text-small text-ink-muted">Nothing here can be changed as asked. Try different words.</p>}
        </section>
      )}

      {applied.map((a, i) => (
        <section key={i} aria-label="Changes made" className="grid gap-2 rounded-md bg-surface-sunken p-3">
          <p className="text-small text-ink-muted">“{a.instruction}”</p>
          <ChangeList changes={a.changes} />
          {i === 0 && lastIsChat && (
            <Button size="sm" variant="secondary" className="justify-self-start" onClick={onUndo}><Undo2 aria-hidden />Undo these changes</Button>
          )}
        </section>
      ))}

      <FormAlert error={error} />
      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void send(text.trim()); }}>
        <label htmlFor="chat-edit" className="text-body-strong">Describe a change</label>
        <Textarea id="chat-edit" value={text} maxLength={1000} rows={3} aria-label="Describe a change to this report"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && text.trim()) { e.preventDefault(); void send(text.trim()); } }}
          placeholder="Make the platform chart a top 5 and move it next to the delay band" />
        <Button type="submit" variant="secondary" disabled={busy || !text.trim()} className="justify-self-end">Preview</Button>
      </form>
      <p className="text-small text-ink-muted">Changes show here before anything is saved. Widgets that change are outlined on the canvas.</p>
    </aside>
  );
}

/** The instruction for a chosen option: the AI's exact words, else the asked-about phrase swapped for the title. */
function answer(o: ChatClarifyOption, question: string, sent: string): string {
  if (o.instruction) return o.instruction;
  const phrase = /“(.+)”/.exec(question)?.[1];
  return phrase ? sent.replace(new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), o.title) : sent;
}

function AiMark({ change }: { change: ChatChange }) {
  return change.by === "ai" ? <span className="ml-1.5 rounded bg-accent-soft px-1 text-small text-ink-muted" title="Added by the AI review">AI</span> : null;
}

function ChangeList({ changes, onAlternative }: { changes: ChatChange[]; onAlternative?: (itemId: string, visual: string, label: string) => void }) {
  return (
    <ul className="grid gap-1.5">
      {changes.map((c, i) => c.op === "refused" ? (
        <li key={i} className="grid gap-1">
          <span className="flex gap-2"><X aria-hidden className="mt-0.5 size-4 shrink-0 text-status-danger" />
            <span><span className="sr-only">Not changed: </span><span className="text-body-strong">{c.request}</span> — {c.reason}<AiMark change={c} /></span>
          </span>
          {c.alternative && c.alternative_label && c.item_id && onAlternative && (
            <Button size="sm" variant="link" className="ml-6 justify-self-start" onClick={() => onAlternative(c.item_id!, c.alternative!, c.alternative_label!)}>{c.alternative_label}</Button>
          )}
        </li>
      ) : (
        <li key={i} className="flex gap-2"><Check aria-hidden className="mt-0.5 size-4 shrink-0 text-status-ok" /><span>{c.summary}<AiMark change={c} /></span></li>
      ))}
    </ul>
  );
}
