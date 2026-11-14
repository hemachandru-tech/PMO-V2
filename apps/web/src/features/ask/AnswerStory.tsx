import { ArrowRight, Info, Sparkles } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { api, ApiError } from "../../api/client";
import type { CitedSentence, DataNote, FollowUp, InsightFact } from "../../api/app";
import type { QuerySpec } from "../../api/querySpec";
import { formatValue } from "../../components/format";
import { Button } from "../../components/ui/button";

/**
 * The answer card v2 sections (US-186, FEAT-065): the cited insights, What's driving this, data notes, Ask next and
 * How this was worked out. Every number comes from the API's facts (US-185); this file only lays them out.
 */

const TYPE_LABEL: Record<InsightFact["type"], string> = {
  concentration: "Concentration", outlier: "Outlier", change: "Change", trend: "Trend", break: "Break in the series",
  driver: "Driver", distribution: "Distribution", gap: "Against target", data_note: "Data note", summary: "Headline",
};

/** Numbers that are percentages whatever the measure's format. */
const PCT = /(_pct|share)$/;
const SKIP = new Set(["contributions", "direction", "shape", "band", "against", "higher_is_worse", "period", "previous_period"]);

function figure(key: string, value: unknown, format?: string | null): string {
  if (typeof value !== "number") return String(value ?? "—");
  if (PCT.test(key)) return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (["items", "nonzero_items", "top_n", "pareto_items", "periods", "rows", "run", "critical_items", "warning_items"].includes(key) || key === "z" || key === "r2" || key === "skew" || key === "slope_per_period") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return formatValue(value, format);
}

function words(key: string): string {
  const s = key.replace(/_pct$/, " %").replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Why: the fact behind a sentence — its kind, the items it names and the numbers it was computed from. */
function FactDetail({ fact, format }: { fact: InsightFact; format?: string | null }) {
  const rows = Object.entries(fact.numbers).filter(([k, v]) => !SKIP.has(k) && v !== null && typeof v !== "object");
  return (
    <div className="grid gap-1 rounded-md bg-surface-sunken p-2 text-small">
      <p className="font-medium">{TYPE_LABEL[fact.type] ?? fact.type}{fact.members.length > 0 && <span className="font-normal text-ink-muted"> · {fact.members.join(", ")}</span>}</p>
      {rows.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink-muted">{words(k)}</dt>
              <dd className="font-mono tabular-nums">{figure(k, v, format)}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="text-ink-muted">Computed from the result in your scope; the sentence only repeats these numbers.</p>
    </div>
  );
}

export function InsightList({ cited, facts, format, focus, onFocus }: {
  cited: CitedSentence[];
  facts: InsightFact[];
  format?: string | null;
  focus: string | null;
  onFocus: (id: string | null) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const baseId = useId();
  if (cited.length === 0) return null;
  const byId = new Map(facts.map((f) => [f.id, f]));
  return (
    <section aria-label="Insights">
      <ul className="grid gap-1.5">
        {cited.map((c, i) => {
          const cites = c.facts.map((id) => byId.get(id)).filter(Boolean) as InsightFact[];
          const active = c.facts.includes(focus ?? "");
          return (
            <li key={i} className="grid gap-1" onMouseEnter={() => onFocus(c.facts[0] ?? null)}>
              <p className={active ? "text-ink" : "text-ink-muted"}>
                <Sparkles aria-hidden className="mr-1.5 inline size-3.5 align-[-2px] text-accent" />
                {c.text}{" "}
                {cites.length > 0 && (
                  <Button variant="link" size="sm" className="h-auto p-0 align-baseline" aria-expanded={open === i}
                    aria-controls={`${baseId}-${i}`} onFocus={() => onFocus(c.facts[0] ?? null)}
                    onClick={() => { setOpen(open === i ? null : i); onFocus(c.facts[0] ?? null); }}>
                    Why?<span className="sr-only"> {TYPE_LABEL[cites[0]!.type]}</span>
                  </Button>
                )}
              </p>
              {open === i && <div id={`${baseId}-${i}`} className="grid gap-1">{cites.map((f) => <FactDetail key={f.id} fact={f} format={format} />)}</div>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** What's driving this: the driver fact's members ranked by their share of the change, with the breakdown one click away. */
export function DriverPanel({ driver, format, spec, onApply }: {
  driver: InsightFact;
  format?: string | null;
  spec?: QuerySpec;
  onApply: (ops: Record<string, unknown>[], label: string) => void;
}) {
  const parts = (driver.numbers.contributions as { member: string; delta: number; share_pct: number }[] | undefined) ?? [];
  if (parts.length === 0) return null;
  const top = Math.max(...parts.map((p) => Math.abs(p.delta))) || 1;
  const dim = driver.dimension;
  const applied = !!dim && (spec?.breakdowns ?? []).some((b) => b.ref === dim.ref);
  const from = String(driver.numbers.previous_period ?? "the previous period");
  const to = String(driver.numbers.period ?? "this period");
  return (
    <Section title="What's driving this">
      <p className="text-small text-ink-muted">Change in {to} vs {from}{dim ? `, by ${dim.label.toLowerCase()}` : ""}: {Number(driver.numbers.total_delta) > 0 ? "+" : ""}{figure("total_delta", driver.numbers.total_delta, format)} in all.</p>
      <ol className="grid gap-1.5" aria-label={`Contribution to the change${dim ? ` by ${dim.label.toLowerCase()}` : ""}`}>
        {parts.map((p) => (
          <li key={p.member} className="grid grid-cols-[minmax(6rem,10rem)_1fr_auto] items-center gap-2 text-small">
            <span className="truncate" title={p.member}>{p.member}</span>
            <span aria-hidden className="h-3 rounded-sm bg-surface-sunken">
              <span className={p.delta >= 0 ? "block h-3 rounded-sm bg-accent" : "block h-3 rounded-sm bg-ink-muted"} style={{ width: `${Math.max(4, (Math.abs(p.delta) / top) * 100)}%` }} />
            </span>
            <span className="font-mono tabular-nums">{p.delta > 0 ? "+" : p.delta < 0 ? "−" : ""}{formatValue(Math.abs(p.delta), format)} <span className="text-ink-muted">({p.share_pct.toLocaleString(undefined, { maximumFractionDigits: 1 })}%)</span></span>
          </li>
        ))}
      </ol>
      {dim && !applied && (
        <Button variant="secondary" size="sm" className="justify-self-start"
          onClick={() => onApply([{ op: "add_breakdown", ref: dim.ref }], `By ${dim.label.toLowerCase()}`)}>
          Break down by {dim.label.toLowerCase()}
        </Button>
      )}
    </Section>
  );
}

/** Data notes: the insight engine's (Not set, truncated) and the sanity checks' (US-183) — a sanity note may offer a
 * refinement (remove the filter that emptied the answer), which only runs when the person clicks it. */
export function DataNotes({ notes, onApply }: { notes: DataNote[]; onApply?: (ops: Record<string, unknown>[], label: string) => void }) {
  if (notes.length === 0) return null;
  return (
    <section aria-label="Data notes" className="grid gap-1">
      {notes.map((n) => (
        <p key={n.fact} className="flex flex-wrap items-start gap-x-1.5 text-small text-ink-muted">
          <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span className="min-w-0 flex-1">{n.text}</span>
          {n.action && onApply && (
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => onApply(n.action!.ops, n.action!.label)}>{n.action.label}</Button>
          )}
        </p>
      ))}
    </section>
  );
}

export function AskNext({ next, onApply }: { next: FollowUp[]; onApply: (ops: Record<string, unknown>[], label: string) => void }) {
  if (next.length === 0) return null;
  return (
    <Section title="Ask next" plain>
      <div className="flex flex-wrap gap-2">
        {next.map((n) => (
          <button key={n.kind} type="button" data-ask-chip onClick={() => onApply(n.ops, n.label)}
            className="inline-flex h-control-sm items-center gap-1.5 rounded-full border border-line-strong bg-surface-raised px-3 text-small text-ink transition-colors hover:border-accent hover:bg-accent-soft focus-visible:focus-ring">
            {n.label}<ArrowRight aria-hidden className="size-3.5" />
          </button>
        ))}
      </div>
    </Section>
  );
}

/** How this was worked out: the plan in plain words and, for Creators, the query it ran (FEAT-043 "Show query"). */
export function WorkedOut({ label, steps, interpreted, spec, canShowQuery }: {
  label: string;
  steps: string[];
  interpreted: string[];
  spec?: QuerySpec;
  canShowQuery: boolean;
}) {
  const [sql, setSql] = useState<{ sql: string; scope: { entity: string; predicate: string }[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const show = () => {
    setError(null);
    api<typeof sql>("/api/v1/query/explain", { method: "POST", body: { spec } })
      .then(setSql)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "The query could not be shown."));
  };
  return (
    <Section title="How this was worked out">
      <ol aria-label={label} className="grid list-decimal gap-0.5 pl-5 text-small text-ink-muted">
        {steps.map((s) => <li key={s}>{s}</li>)}
        {interpreted.map((s) => <li key={`i-${s}`}>Read “{s}”</li>)}
      </ol>
      {canShowQuery && spec && !sql && <Button variant="link" size="sm" className="justify-self-start px-0" onClick={show}>Show query</Button>}
      {error && <p role="alert" className="text-small text-status-danger">{error}</p>}
      {sql && (
        <div className="grid gap-1">
          <pre className="max-h-48 overflow-auto rounded-sm bg-surface-sunken p-2 font-mono text-code">{sql.sql}</pre>
          <p className="text-small text-ink-muted">Scope part: {sql.scope.map((s) => `${s.entity}: ${s.predicate}`).join("; ")}</p>
        </div>
      )}
    </Section>
  );
}

/** A titled part of the answer: framed (What's driving this) or plain (Ask next). */
function Section({ title, children, plain = false }: { title: string; children: ReactNode; plain?: boolean }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={plain ? "grid gap-2" : "grid gap-2 rounded-[16px] border border-line bg-surface-raised p-4"}>
      <h3 id={id} className="text-small font-medium text-ink-muted">{title}</h3>
      {children}
    </section>
  );
}
