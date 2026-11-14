import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Award, ChevronDown, Code2, Lightbulb, Pencil, Pin, Rows3, Workflow } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import type { Column } from "../../api/app";
import type { ChartSpec } from "../../api/querySpec";
import { VISUAL_NAMES, type ReportListItem } from "../../api/authoring";
import { api, ApiError } from "../../api/client";
import { can, useMe } from "../../app/me";
import { Chart } from "../../components/Chart";
import { ContentBadge } from "../../components/ContentBadge";
import { formatAsOf, formatValue } from "../../components/format";
import { ResultTable } from "../../components/ResultTable";
import { FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "../../components/ui/dropdown-menu";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { NativeSelect } from "../../components/ui/native-select";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { AnswerFeedback } from "./AnswerFeedback";
import type { Turn } from "./AskChat";
import { AnswerMeta, CopyAction, FollowChip, IconAction, ViewSwitch } from "./ChatParts";

/** The chart a Direct SQL answer is drawn as, the suitable others and their charts (US-260, from the `viz` event). */
interface DirectViz {
  visual: string | null;
  chart_spec: ChartSpec | null;
  visuals?: { suitable: string[]; disabled: { visual: string; label: string; reason: string }[]; recommended: string } | null;
  charts?: Record<string, ChartSpec>;
}

/** Refinements as follow-up questions: Direct SQL reads them with the earlier question (US-257b), and writes new SQL. */
const QUICK = ["Top 5", "By month", "Compare with the previous period"];

/**
 * A Direct SQL answer (ENH-028 US-257b, US-260; DEC-30): the model wrote SQL for this question and Tela checked, scoped
 * and ran it. The card always shows the Custom SQL badge, says it is not certified and shows the SQL. It has the same
 * actions as any answer: a chart and chart suggestions, Pin (a Custom SQL widget, which a published dashboard takes only
 * once certified, RULE-009), Drill to records and Refine (follow-up questions, answered with new SQL), Save as gold
 * example (to the Admin's curation queue, where the spec is written) and Helpful / Not helpful / This isn't what I asked.
 */
export function DirectSqlAnswer({ turn, pinTarget = null, latest = true, onFollowUp }: {
  turn: Turn;
  pinTarget?: string | null;
  /** Quick refinements show under the latest answer only (US-261). */
  latest?: boolean;
  onFollowUp?: (question: string) => void;
}) {
  const { data: me } = useMe();
  const c = turn.complete!;
  const viz = turn.viz as unknown as DirectViz | undefined;
  const columns = (c.columns ?? []) as Column[];
  const rows = c.rows ?? [];
  const single = rows.length === 1 && columns.length === 1;
  const terms = (c.conditions ?? []).map((h) => h.term);
  const question = turn.question.replace(/^Undo: /, "");
  const [visual, setVisual] = useState<string | null>(viz?.visual ?? null);
  const [view, setView] = useState<"chart" | "table">("chart");
  const [pinning, setPinning] = useState(false);
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const chosen = visual ?? viz?.visual ?? null;
  const spec = (chosen && viz?.charts?.[chosen]) || viz?.chart_spec || null;
  const chartable = !single && rows.length > 0 && !!spec && !["table", "figure"].includes(spec.type);
  const suggestions = (viz?.visuals?.suitable ?? []).filter((v) => v !== chosen);
  const canPin = !!c.can_pin && !!c.sql && !!c.connection_id && can(me, "widgets.create") && can(me, "customsql.use");
  const figure = single ? formatValue(rows[0]![0], columns[0]!.format) : null;
  const headline = turn.headline ?? "Answered with SQL written for this question.";
  const drill = (mark?: Record<string, unknown>) => {
    const where = mark ? Object.entries(mark).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
      .map(([k, v]) => `${columns.find((col) => col.name === k)?.label ?? k} is ${String(v)}`) : [];
    onFollowUp?.(`List the records behind: ${question}${where.length ? ` — where ${where.join(" and ")}` : ""}`);
  };
  const meta = [
    { text: `Data as of ${formatAsOf(c.as_of ?? null)}` },
    ...(c.connection ? [{ text: `${c.connection} · model v${c.model_version}` }] : []),
    ...(c.repaired ? [{ text: "SQL repaired after a failed check" }] : []),
    ...(terms.length > 0 ? [{ text: `Agreed terms used: ${terms.join(", ")}` }] : []),
  ];
  const sendInstruction = () => {
    if (!instruction.trim() || !onFollowUp) return;
    onFollowUp(instruction.trim());
    setInstruction("");
    setEditing(false);
  };
  return (
    <article data-ask-answer data-direct-sql className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4" aria-label="Answer">
      <div className="flex min-w-0 items-start gap-2">
        <h2 className="text-heading">{headline}</h2>
        <ContentBadge badge="direct_sql" />
      </div>
      {single && (
        <div data-ask-frame className="grid w-fit min-w-56 max-w-full gap-0.5 rounded-[16px] border border-line bg-surface-raised px-5 py-4">
          <p className="text-small font-medium text-ink-muted">{columns[0]!.label || "Value"}</p>
          <p className="font-mono text-figure-xl tabular-nums text-ink">{figure}</p>
        </div>
      )}
      {!single && (
        rows.length === 0
          ? <p className="text-ink-muted">No rows match.</p>
          : (
            <figure data-ask-frame className="m-0 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2 rounded-[16px] border border-line bg-surface-raised p-4">
              {chartable && <div className="flex justify-end"><ViewSwitch view={view} onChange={setView} /></div>}
              {spec
                ? <Chart spec={spec} columns={columns} rows={rows} title={turn.headline ?? question} view={chartable ? view : undefined}
                    onMark={onFollowUp ? (mark) => drill(mark) : undefined} />
                : <ResultTable columns={columns} rows={rows} caption="Direct SQL result" />}
              {c.truncated && <figcaption className="text-small text-ink-muted">Showing the first {rows.length} of {c.row_count} rows.</figcaption>}
            </figure>
          )
      )}
      {rows.length === 0 && (c.value_notes ?? []).length > 0 && (
        <div role="note" aria-label="Filter values not in the data" className="grid gap-1 rounded-[16px] border border-status-warn/30 bg-status-warn-soft/60 p-4 text-small">
          <p className="font-medium">The query filters on values the data doesn&apos;t hold, so the empty result may be wrong:</p>
          <ul className="list-disc pl-5">
            {c.value_notes!.map((n) => <li key={n}>{n}</li>)}
          </ul>
        </div>
      )}
      <details className="group rounded-[12px] border border-line bg-surface-raised px-4 py-2.5">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-small font-medium text-ink-muted [&::-webkit-details-marker]:hidden">
          <Code2 aria-hidden className="size-4" />The SQL
          <ChevronDown aria-hidden className="ml-auto size-4 transition-transform group-open:rotate-180" />
        </summary>
        <pre aria-label="The SQL" className="mt-2 overflow-x-auto whitespace-pre-wrap break-words font-mono text-small">{c.sql}</pre>
      </details>
      <div className="grid gap-1">
        <AnswerMeta items={meta} />
        {c.scope_text && <p className="text-small text-ink-muted">{c.scope_text}</p>}
        <p className="text-small text-ink-muted">{c.publish_note ?? "Direct SQL answers can't go on a published dashboard until certified."}</p>
      </div>
      <AnswerFeedback turnId={turn.id} before={<CopyAction text={[headline, figure, c.sql].filter(Boolean).join("\n")} />}>
        {canPin && <IconAction label="Pin" icon={<Pin aria-hidden />} onClick={() => setPinning(true)} />}
        {onFollowUp && rows.length > 0 && <IconAction label="Drill to records" icon={<Rows3 aria-hidden />} onClick={() => drill()} />}
        {can(me, "widgets.create") && suggestions.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconAction label="Suggestions" icon={<Lightbulb aria-hidden />} />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuLabel>Other charts that suit this answer</DropdownMenuLabel>
              {suggestions.map((v) => (
                <DropdownMenuItem key={v} onSelect={() => { setVisual(v); setView("chart"); toast(`Showing as ${VISUAL_NAMES[v] ?? v}`); }}>
                  {VISUAL_NAMES[v] ?? v}{v === viz?.visuals?.recommended ? " (recommended)" : ""}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {can(me, "widgets.create") && <SuggestGoldButton turnId={turn.id} />}
        {can(me, "customsql.use") && c.sql && (
          <IconLink label="Open in Custom SQL" icon={<Code2 aria-hidden />} to="/sql/new" state={{ sql: c.sql, connection_id: c.connection_id, drafted_by_ai: true }} />
        )}
        {can(me, "settings.manage") && <IconLink label="Agent runs" icon={<Workflow aria-hidden />} to={`/admin/agent-runs?turn_id=${turn.id}`} />}
      </AnswerFeedback>
      {onFollowUp && latest && (
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Refine">
            {QUICK.map((q) => <FollowChip key={q} onClick={() => onFollowUp(q)}>{q}</FollowChip>)}
            {chartable && <FollowChip onClick={() => setView("table")}>As table</FollowChip>}
            <FollowChip aria-expanded={editing} onClick={() => setEditing(!editing)}><Pencil aria-hidden />Change this answer</FollowChip>
          </div>
          {editing && (
            <form className="grid gap-1.5" onSubmit={(e) => { e.preventDefault(); sendInstruction(); }}>
              <div className="flex gap-2">
                <label htmlFor={`refine-${turn.id}`} className="sr-only">Refine this answer</label>
                <Input id={`refine-${turn.id}`} autoFocus value={instruction} maxLength={500} onChange={(e) => setInstruction(e.target.value)}
                  placeholder="Only KSA, top 5, by month…" className="rounded-full" />
                <Button type="submit" variant="secondary" className="rounded-full" disabled={!instruction.trim()}>Refine<ArrowRight aria-hidden /></Button>
              </div>
              <p className="text-small text-ink-muted">A refinement is a follow-up question: Direct SQL writes new SQL for it.</p>
            </form>
          )}
        </div>
      )}
      {pinning && c.sql && c.connection_id && (
        <PinSqlDialog sql={c.sql} connectionId={c.connection_id} defaultTitle={question} visual={chosen ?? "table"}
          visuals={viz?.visuals?.suitable ?? []} defaultReportId={pinTarget} onClose={() => setPinning(false)} />
      )}
    </article>
  );
}

/** An icon-only link in the answer's action row, named by its tooltip. */
function IconLink({ label, icon, to, state }: { label: string; icon: ReactNode; to: string; state?: unknown }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button asChild variant="ghost" size="icon" className="size-8 rounded-lg text-ink-muted hover:text-ink">
          <Link to={to} state={state} aria-label={label}>{icon}</Link>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * "Save as gold example" on a Direct SQL answer (US-260): there is no Query Spec to save, so the question and its SQL go
 * to Admin → Knowledge → From misses, where an Admin writes the spec and approves it through the gold gate.
 */
function SuggestGoldButton({ turnId }: { turnId: string }) {
  const [saved, setSaved] = useState(false);
  const save = async () => {
    try {
      await api("/api/v1/knowledge/curation/from-answer", { method: "POST", body: { turn_id: turnId } });
      setSaved(true);
      toast.success("Suggested as a gold example — an Admin will write its spec and review it");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "The gold example could not be suggested.");
    }
  };
  return (
    <IconAction label={saved ? "Suggested as gold example" : "Save as gold example"} icon={<Award aria-hidden />}
      disabled={saved} onClick={() => void save()} />
  );
}

/**
 * Pin a Direct SQL answer (US-260): the SQL is checked again and saved as a Custom SQL widget, in the library or on one
 * of your reports. It keeps the Custom SQL badge; a published dashboard takes it only once certified (RULE-009).
 */
function PinSqlDialog({ sql, connectionId, defaultTitle, visual: initial, visuals, defaultReportId, onClose }: {
  sql: string;
  connectionId: string;
  defaultTitle: string;
  visual: string;
  visuals: string[];
  defaultReportId: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ items: ReportListItem[] }>("/api/v1/reports") });
  const mine = (reports.data?.items ?? []).filter((r) => r.mine);
  const [title, setTitle] = useState(defaultTitle.slice(0, 200));
  const [visual, setVisual] = useState(visuals.includes(initial) ? initial : "table");
  const [reportId, setReportId] = useState(defaultReportId ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<{ reportId: string | null } | null>(null);
  const choices = Array.from(new Set(["table", ...visuals]));
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const checked = await api<{ check_token: string }>("/api/v1/custom-sql/check", { method: "POST", body: { connection_id: connectionId, sql } });
      await api("/api/v1/widgets/custom-sql", {
        method: "POST",
        body: { connection_id: connectionId, sql, check_token: checked.check_token, title: title.trim(), visual, drafted_by_ai: true, report_id: reportId || null },
      });
      setDone({ reportId: reportId || null });
      void qc.invalidateQueries({ queryKey: ["reports"] });
      if (reportId) void qc.invalidateQueries({ queryKey: ["report", reportId] });
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "error", "The widget could not be saved.", null));
    } finally {
      setBusy(false);
    }
  };
  const report = mine.find((r) => r.id === done?.reportId);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Pin to a report</DialogTitle>
          <DialogDescription>
            The widget keeps this SQL, not today&apos;s numbers, and shows the Custom SQL badge. It can go on a published dashboard once certified.
          </DialogDescription>
        </DialogHeader>
        {done ? (
          <>
            <p>
              Pinned as a live Custom SQL widget.
              {done.reportId && <> It is on <Link className="text-link underline-offset-4 hover:underline" to={`/reports/${done.reportId}`} onClick={onClose}>{report?.title ?? "your report"}</Link>.</>}
            </p>
            <DialogFooter><Button onClick={onClose}>Done</Button></DialogFooter>
          </>
        ) : (
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (title.trim()) void submit(); }}>
            <div className="grid gap-1.5">
              <Label htmlFor="pin-sql-title">Title</Label>
              <Input id="pin-sql-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor="pin-sql-visual">Visual</Label>
                <NativeSelect id="pin-sql-visual" value={visual} onChange={(e) => setVisual(e.target.value)}>
                  {choices.map((v) => <option key={v} value={v}>{VISUAL_NAMES[v] ?? v}</option>)}
                </NativeSelect>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="pin-sql-report">Report</Label>
                <NativeSelect id="pin-sql-report" value={reportId} onChange={(e) => setReportId(e.target.value)}>
                  <option value="">Library only</option>
                  {mine.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
                </NativeSelect>
              </div>
            </div>
            <FormAlert error={error} />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={busy || !title.trim()}>{busy ? "Pinning…" : "Pin"}</Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** The SQL a Direct SQL refusal was given for, so the person can see what failed the check. */
export function RefusedSql({ sql }: { sql: string }) {
  return (
    <details className="text-small">
      <summary className="cursor-pointer">The SQL that was refused</summary>
      <pre aria-label="The SQL that was refused" className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono">{sql}</pre>
    </details>
  );
}
