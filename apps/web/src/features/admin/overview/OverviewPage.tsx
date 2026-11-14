import { useQuery } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { api } from "../../../api/client";
import type { AdminOverview } from "../../../api/app";
import { formatRelative } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card } from "../../../components/ui/card";
import { Skeleton } from "../../../components/ui/skeleton";
import { StatusBadge, type Status } from "../../../components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../../components/ui/tooltip";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";

type Jobs = NonNullable<AdminOverview["jobs"]>;

/** What each security event means to an Admin; unknown actions show their key. */
const SECURITY_LABELS: Record<string, string> = {
  "query.scope_refused": "queries refused by scope",
  "custom_sql.rejected": "custom SQL refused as unsafe",
  "auth.login": "sign-ins refused",
};
const STATUS_WORDS: Record<string, [Status, string]> = { error: ["danger", "Error"], incomplete: ["warn", "Incomplete"] };

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "");
const time = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "");

/** A datetime-local value, as the audit log's filters keep them in its URL. */
function localInput(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
const auditLink = (filters: Record<string, string>) => `/admin/audit?${new URLSearchParams(filters).toString()}`;
const since = (hours: number) => localInput(new Date(Date.now() - hours * 3_600_000).toISOString());

function jobLink(key: Jobs["items"][number]["key"], failed: boolean): string | undefined {
  if (key === "introspection") return auditLink({ action: "connection.introspected", ...(failed ? { outcome: "failure" } : {}), from: since(24) });
  if (key === "cache_refresh") return auditLink({ action: "connection.cache_refreshed", from: since(24) });
  return undefined; // exports and event delivery have no screen yet (EPIC-10 part 2)
}

/** `to` is left out when the problem has no screen yet (exports, event delivery). */
interface Attention { status: Status; word: string; text: string; action?: string; to?: string }

/** "Needs your attention": problems first, then things waiting for a decision. */
function attentionItems(o: AdminOverview): Attention[] {
  const out: Attention[] = [];
  for (const c of o.connections?.attention ?? []) {
    const [status, word] = STATUS_WORDS[c.status] ?? ["warn", c.status];
    out.push({ status, word, action: "Fix connection", to: "/admin/connections?status=attention",
      text: c.status === "error" ? `${c.name} cannot connect or failed its read-only check.` : `${c.name}: the schema was only partly read. Read it again.` });
  }
  for (const m of o.models?.items ?? []) {
    if (m.broken) out.push({ status: "danger", word: "Broken", action: "Open model", to: `/admin/models/${m.id}`,
      text: `The ${m.connection} model uses columns that changed or disappeared.` });
  }
  const failed = o.jobs?.items.filter((j) => j.failed > 0) ?? [];
  for (const j of failed) {
    const to = jobLink(j.key, true);
    out.push({ status: "danger", word: "Failed", ...(to ? { action: "See details", to } : {}),
      text: `${j.label}: ${count(j.failed, "run")} failed in the last ${o.jobs!.hours} hours${j.last_failure?.label ? `, latest ${j.last_failure.label}` : ""}.` });
  }
  if (o.publish_requests?.open) {
    const p = o.publish_requests;
    out.push({ status: "warn", word: "Waiting", action: "Review", to: "/govern/publish-requests?status=open",
      text: `${count(p.open, "publish request")}, the oldest from ${p.oldest_by ?? "a Creator"} ${formatRelative(p.oldest_at)}.` });
  }
  if (o.certification?.blocks_publish) {
    out.push({ status: "warn", word: "Waiting", action: "Review", to: "/govern/certification?status=pending&blocks=1",
      text: `${count(o.certification.blocks_publish, "certification request")} ${o.certification.blocks_publish === 1 ? "holds" : "hold"} up a dashboard publish.` });
  }
  for (const c of o.value_synonyms?.connections ?? []) {
    if (c.pack_pending > 0) out.push({ status: "warn", word: "Waiting", action: "Review", to: `/admin/knowledge/values?connection=${c.id}`,
      text: `${count(c.pack_pending, "pack synonym")} for ${c.name} ${c.pack_pending === 1 ? "waits" : "wait"} for review; until approved, words like “Saudi” or “Dubai” don't filter in Ask.` });
  }
  for (const m of o.models?.items ?? []) {
    const waiting = m.pending_changes + m.open_questions;
    if (m.has_draft && waiting > 0) {
      out.push({ status: "warn", word: "Waiting", action: "Open model", to: `/admin/models/${m.id}`,
        text: `The ${m.connection} draft model has ${[m.pending_changes && count(m.pending_changes, "suggested change"), m.open_questions && count(m.open_questions, "open question")].filter(Boolean).join(" and ")} waiting.` });
    }
  }
  return out;
}

/**
 * Admin console overview (US-143; FEAT-056; design board AdminOverview): what needs an Admin today across data,
 * governance and the platform, from one `GET /admin/overview`. Each card opens its screen with the filter applied.
 * Sections the person cannot open are not returned, so they are not shown.
 */
export function OverviewPage() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const q = useQuery({
    queryKey: ["admin-overview", tz],
    queryFn: () => api<AdminOverview>(`/api/v1/admin/overview?tz=${encodeURIComponent(tz)}`),
    refetchInterval: 60_000,
  });
  return (
    <div className="tl grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="grid gap-1">
          <h1 className="text-title">Admin overview</h1>
          <p className="text-ink-muted">What needs you today, across data, governance and the platform.</p>
        </div>
        {q.data && (
          <span className="flex items-center gap-2 text-small text-ink-muted">
            Updated {time(q.data.generated_at)}
            <Button variant="ghost" size="sm" onClick={() => void q.refetch()} disabled={q.isFetching}>{q.isFetching ? "Refreshing…" : "Refresh"}</Button>
          </span>
        )}
      </div>
      {q.isLoading ? <OverviewSkeleton /> : q.error ? <FormAlert error={asApiError(q.error)} /> : <Overview o={q.data!} />}
    </div>
  );
}

function Overview({ o }: { o: AdminOverview }) {
  const items = attentionItems(o);
  return (
    <>
      <Card className="px-4 py-2" role="region" aria-labelledby="attention-title">
        <h2 id="attention-title" className="py-2 text-heading">Needs your attention</h2>
        {items.length === 0 ? (
          <p className="flex items-center gap-2 border-t border-line py-3 text-ink-muted">
            <CircleCheck aria-hidden className="size-4 text-status-ok" /> Nothing needs you right now.
          </p>
        ) : (
          <ul>
            {items.map((a, i) => (
              <li key={i} className="flex flex-wrap items-center gap-3 border-t border-line py-2.5">
                <StatusBadge status={a.status}>{a.word}</StatusBadge>
                <span className="min-w-0 flex-[1_1_18rem]">{a.text}</span>
                {a.to && <Button asChild variant="secondary" size="sm"><Link to={a.to}>{a.action}</Link></Button>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {o.connections && <ConnectionsCard c={o.connections} />}
        {o.models && <ModelCard m={o.models} />}
        {o.certification && (
          <StatCard label="Certification queue" to="/govern/certification?status=pending" value={o.certification.pending.toLocaleString()}
            footer={o.certification.pending === 0 ? "Nothing waiting" : [
              `Oldest waiting ${formatRelative(o.certification.oldest_at)}`,
              o.certification.blocks_publish ? `${o.certification.blocks_publish} ${o.certification.blocks_publish === 1 ? "holds" : "hold"} up a publish` : null,
              o.certification.with_lookalikes ? `${o.certification.with_lookalikes} look like existing items` : null,
            ].filter(Boolean).join(" · ")} />
        )}
        {o.publish_requests && (
          <StatCard label="Publish requests" to="/govern/publish-requests?status=open" value={o.publish_requests.open.toLocaleString()}
            footer={o.publish_requests.open === 0 ? "Nothing waiting" : `Oldest from ${o.publish_requests.oldest_by ?? "a Creator"}, ${formatRelative(o.publish_requests.oldest_at)}`} />
        )}
      </div>

      {(o.ai || o.security) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {o.ai && <AiCard ai={o.ai} />}
          {o.security && <SecurityCard s={o.security} />}
        </div>
      )}

      {o.jobs && <JobsCard jobs={o.jobs} />}
    </>
  );
}

/** A count card; the whole card is the link to its screen. */
function StatCard({ label, value, footer, to, aside, children, className }: {
  label: string; value?: string; footer?: ReactNode; to?: string; aside?: ReactNode; children?: ReactNode; className?: string;
}) {
  const body = (
    <>
      <span className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-label uppercase text-ink-muted">{label}</span>
        {aside && <span className="text-small text-ink-muted">{aside}</span>}
      </span>
      {value !== undefined && <span className="font-mono text-figure-xl tabular-nums text-ink">{value}</span>}
      {children}
      {footer && <span className="text-small text-ink-muted">{footer}</span>}
    </>
  );
  const frame = "flex flex-col gap-2 rounded-lg border border-line bg-surface-raised p-4 text-ink";
  if (!to) return <div className={cn(frame, className)}>{body}</div>;
  return (
    <Link to={to} className={cn(frame, "no-underline transition-colors hover:border-accent focus-visible:focus-ring", className)}>{body}</Link>
  );
}

function ConnectionsCard({ c }: { c: NonNullable<AdminOverview["connections"]> }) {
  const others = c.total - c.healthy - c.attention.length;
  return (
    <StatCard label="Connections" to={c.attention.length ? "/admin/connections?status=attention" : "/admin/connections"}
      footer={c.total === 0 ? "No connections yet" : [
        c.attention.map((a) => `${a.name} ${STATUS_WORDS[a.status]?.[1].toLowerCase() ?? a.status}`).join(" · "),
        others > 0 ? `${others} not set up or turned off` : null,
      ].filter(Boolean).join(" · ") || "All working"}>
      <span className="font-mono text-figure-xl tabular-nums text-ink">
        {c.healthy}<span className="ml-2 font-sans text-small text-ink-muted">of {c.total} working</span>
      </span>
    </StatCard>
  );
}

function ModelCard({ m }: { m: NonNullable<AdminOverview["models"]> }) {
  if (m.total === 0) return <StatCard label="Data model" to="/admin/connections" value="None" footer="Build a model from a connection, or install a starter pack." />;
  if (m.total > 1) {
    return <StatCard label="Data models" to="/admin/connections" value={m.total.toLocaleString()}
      footer={[`${m.drafts} with a draft`, m.broken ? `${m.broken} need attention` : null].filter(Boolean).join(" · ")} />;
  }
  const one = m.items[0]!;
  const waiting = one.pending_changes + one.open_questions;
  return (
    <StatCard label="Data model" to={`/admin/models/${one.id}`} value={one.version_no ? `v${one.version_no}` : "Draft"} aside={one.connection}
      footer={[one.approved_at ? `Approved ${day(one.approved_at)}` : "Not approved yet",
        one.has_draft ? (waiting ? `draft with ${count(waiting, "item")} to review` : "draft open") : null].filter(Boolean).join(" · ")} />
  );
}

function AiCard({ ai }: { ai: NonNullable<AdminOverview["ai"]> }) {
  const t = ai.today;
  return (
    // The bars carry their own tooltips, so the card is not one big link; the footer links to AI settings (US-146).
    <StatCard label="AI usage today" aside={ai.provider ? `Provider: ${ai.provider}` : "No provider set"}
      footer={<Link className="text-link underline-offset-4 hover:underline" to="/admin/ai">AI settings and usage</Link>}>
      <dl className="flex flex-wrap gap-x-6 gap-y-2">
        <Figure term="calls" value={t.calls.toLocaleString()} />
        <Figure term="tokens" value={compact.format(t.tokens)} />
        <Figure term="estimated cost" value={t.estimated_cost.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 })} />
        <Figure term="blocked by policy" value={t.blocked.toLocaleString()} />
        <Figure term="failed" value={t.failed.toLocaleString()} />
      </dl>
      <CallsBars days={ai.daily_calls} />
    </StatCard>
  );
}

function Figure({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex flex-col-reverse">
      <dt className="text-small text-ink-muted">{term}</dt>
      <dd className="font-mono text-figure tabular-nums text-ink">{value}</dd>
    </div>
  );
}

/** Calls per day, oldest first: one series in the first chart colour, a tooltip per bar, a table for screen readers. */
function CallsBars({ days }: { days: { day: string; calls: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.calls));
  const label = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return (
    <figure className="grid gap-1">
      <div className="flex h-16 items-end gap-0.5 border-b border-chart-axis" aria-hidden>
        {days.map((d) => (
          <Tooltip key={d.day}>
            <TooltipTrigger asChild>
              <span className="flex h-full flex-1 items-end">
                <span className={cn("w-full rounded-t-[4px]", d.calls ? "bg-chart-1" : "bg-chart-grid")}
                  style={{ height: d.calls ? `${Math.max(4, (d.calls / max) * 100)}%` : "2px" }} />
              </span>
            </TooltipTrigger>
            <TooltipContent>{label(d.day)}: {count(d.calls, "call")}</TooltipContent>
          </Tooltip>
        ))}
      </div>
      <figcaption className="text-small text-ink-muted">Calls per day, last {days.length} days</figcaption>
      <table className="sr-only">
        <caption>AI calls per day</caption>
        <tbody>{days.map((d) => <tr key={d.day}><th scope="row">{label(d.day)}</th><td>{d.calls}</td></tr>)}</tbody>
      </table>
    </figure>
  );
}

function SecurityCard({ s }: { s: NonNullable<AdminOverview["security"]> }) {
  const actions = Object.entries(s.by_action).slice(0, 3);
  return (
    <StatCard label={`Security, last ${s.days} days`} aside="From the audit log" to={auditLink({ security: "true", from: localInput(s.since) })}
      footer={s.latest ? `Latest: ${SECURITY_LABELS[s.latest.action] ?? s.latest.action}${s.latest.actor ? ` for ${s.latest.actor}` : ""}, ${day(s.latest.at)} ${time(s.latest.at)}` : undefined}>
      {s.total === 0 ? (
        <span className="flex items-center gap-2 text-ink-muted"><CircleCheck aria-hidden className="size-4 text-status-ok" /> No security events.</span>
      ) : (
        <dl className="flex flex-wrap gap-x-6 gap-y-2">
          {actions.map(([action, n]) => <Figure key={action} term={SECURITY_LABELS[action] ?? action} value={n.toLocaleString()} />)}
        </dl>
      )}
    </StatCard>
  );
}

function JobsCard({ jobs }: { jobs: Jobs }) {
  return (
    <Card className="gap-2 p-4" role="region" aria-labelledby="jobs-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="jobs-title" className="text-heading">Background jobs</h2>
        <span className="text-small text-ink-muted">Last {jobs.hours} hours</span>
      </div>
      <Table>
        <TableHeader>
          <TableRow><TableHead>Job</TableHead><TableHead className="text-right">Runs</TableHead><TableHead>Last run</TableHead><TableHead>Result</TableHead></TableRow>
        </TableHeader>
        <TableBody>
          {jobs.items.map((j) => {
            const to = jobLink(j.key, j.failed > 0);
            return (
              <TableRow key={j.key}>
                <TableCell>{to ? <Link className="text-link underline-offset-4 hover:underline" to={to}>{j.label}</Link> : j.label}</TableCell>
                <TableCell numeric>{j.runs.toLocaleString()}</TableCell>
                <TableCell>{j.last_run_at ? `${day(j.last_run_at)} ${time(j.last_run_at)}` : "—"}</TableCell>
                <TableCell>
                  {j.failed > 0 ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusBadge status="danger">{count(j.failed, "failure")}</StatusBadge>
                      {j.last_failure?.label && <span className="text-small text-ink-muted">latest {j.last_failure.label}</span>}
                    </span>
                  ) : j.runs > 0 ? <StatusBadge status="ok">Done</StatusBadge> : <span className="text-ink-muted">No runs</span>}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Card>
  );
}

function OverviewSkeleton() {
  return (
    <div className="grid gap-4" aria-busy="true" aria-label="Loading the overview">
      <Skeleton className="h-28" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32" />)}</div>
      <div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-48" /><Skeleton className="h-48" /></div>
    </div>
  );
}
