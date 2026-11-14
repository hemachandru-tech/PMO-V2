import { BookOpen, Database, Filter, LayoutGrid, LineChart, Route } from "lucide-react";
import type { ReactNode } from "react";

import type { Turn } from "./AskChat";
import { ASK_MODE_LABELS } from "./askModes";

/**
 * "How this was answered" beside the conversation (Aurora layout, ENH-027): the latest answer's measure, what it is
 * grouped by, its filters and source, and the measure's definition. A Fields + definitions answer (Plan B, US-254)
 * also lists the conditions it used and where each came from; a Direct SQL answer (US-257b) its agreed terms. Read from the answer the API returned; nothing is
 * worked out here.
 */
export function AnswerPanel({ turns }: { turns: Turn[] }) {
  const turn = [...turns].reverse().find((t) => t.complete?.spec || t.complete?.ask_mode === "direct_sql");
  const c = turn?.complete;
  if (!turn || !c) {
    return (
      <section data-ask-panel className="grid gap-2 p-5">
        <h2 className="text-heading">How this was answered</h2>
        <p className="text-small text-ink-muted">Ask a question: the measure, grouping and filters behind the answer show here.</p>
      </section>
    );
  }
  if (c.ask_mode === "direct_sql") {
    const terms = (c.conditions ?? []).map((h) => h.term);
    return (
      <section data-ask-panel aria-label="How this was answered" className="grid gap-3 p-5">
        <h2 className="text-heading">How this was answered</h2>
        <Row tone={1} icon={<LineChart aria-hidden className="size-3.5" />} kind="Measure" value="SQL written for this question" />
        <Row tone={3} icon={<Filter aria-hidden className="size-3.5" />} kind="Agreed terms used" value={terms.length ? terms.join(", ") : "None"} />
        {c.connection && <Row tone={4} icon={<Database aria-hidden className="size-3.5" />} kind="Source" value={`${c.connection} · model v${c.model_version}`} />}
        <Row tone={5} icon={<Route aria-hidden className="size-3.5" />} kind="Answered with" value={ASK_MODE_LABELS.direct_sql} />
        <p className="text-small text-ink-muted">Not certified: the model wrote the SQL; Tela checked and scoped it.</p>
      </section>
    );
  }
  const def = c.definitions?.[0];
  const groups = (c.columns ?? []).filter((col) => col.kind === "dimension" || col.kind === "period").map((col) => col.label || col.name);
  return (
    <div className="grid gap-4">
      <section data-ask-panel aria-label="How this was answered" className="grid gap-3 p-5">
        <h2 className="text-heading">How this was answered</h2>
        <Row tone={1} icon={<LineChart aria-hidden className="size-3.5" />} kind="Measure" value={def?.label ?? c.rule?.label ?? "Your question"} />
        <Row tone={2} icon={<LayoutGrid aria-hidden className="size-3.5" />} kind="Grouped by" value={groups.length ? groups.join(", ") : "Not grouped"} />
        <Row tone={3} icon={<Filter aria-hidden className="size-3.5" />} kind="Filters" value={c.filters_text || "None"} />
        {c.connection && <Row tone={4} icon={<Database aria-hidden className="size-3.5" />} kind="Source" value={`${c.connection} · model v${c.model_version}`} />}
        {c.ask_mode && <Row tone={5} icon={<Route aria-hidden className="size-3.5" />} kind="Answered with" value={ASK_MODE_LABELS[c.ask_mode]} />}
      </section>
      {c.fields && (
        <section data-ask-panel aria-label="Conditions used" className="grid gap-2 p-5">
          <h2 className="text-heading">Conditions used</h2>
          {c.fields.conditions_used.length === 0 ? (
            <p className="text-small text-ink-muted">None: every record counts.</p>
          ) : (
            <ul className="grid gap-2">
              {c.fields.conditions_used.map((u, i) => (
                <li key={i} className="grid">
                  <span className="font-medium">“{u.phrase}”{u.term && u.term.toLowerCase() !== u.phrase.toLowerCase() ? ` · ${u.term}` : ""}</span>
                  {u.rule && <span className="text-small text-ink-muted">{u.rule}</span>}
                  <span className="text-small text-ink-muted">From: {u.source_label}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-small text-ink-muted">
            {c.fields.certified ? "Certified: every term used is approved." : `Not certified: ${c.fields.not_certified_because ?? "built from your question"}.`}
          </p>
        </section>
      )}
      {(def?.plain_rule || c.rule?.plain_rule) && (
        <section data-ask-panel aria-label="Definition" className="grid gap-2 p-5">
          <h2 className="flex items-center gap-2 text-heading"><BookOpen aria-hidden className="size-4 text-ink-muted" />Definition</h2>
          <p className="text-ink-muted">{def?.plain_rule ?? c.rule?.plain_rule}</p>
          {def?.blank_handling && <p className="text-small text-ink-muted">{def.blank_handling}</p>}
        </section>
      )}
    </div>
  );
}

function Row({ tone, icon, kind, value }: { tone: number; icon: ReactNode; kind: string; value: string }) {
  return (
    <div className="flex items-center gap-3">
      <span data-nav-icon={tone} className="inline-flex shrink-0 items-center justify-center">{icon}</span>
      <span className="grid min-w-0">
        <span className="text-small text-ink-muted">{kind}</span>
        <span className="break-words font-medium">{value}</span>
      </span>
    </div>
  );
}
