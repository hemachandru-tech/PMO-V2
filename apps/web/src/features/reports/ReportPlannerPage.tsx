import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CircleSlash, LayoutGrid, Lock, RotateCw, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, type ApiError } from "../../api/client";
import type { Home } from "../../api/app";
import { VISUAL_NAMES, type BuildResult, type PlanReview, type ReportPlan } from "../../api/authoring";
import { ContentBadge } from "../../components/ContentBadge";
import { TextBlock } from "../../components/TextBlock";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { Input } from "../../components/ui/input";
import { Skeleton } from "../../components/ui/skeleton";
import { Textarea } from "../../components/ui/textarea";
import { asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";
import { NewReportDialog } from "./NewReportDialog";
import { useAppName } from "../../app/branding";

export interface BuiltState { built: BuildResult["results"] }

/**
 * Report planner (FEAT-023; US-066 – US-068; SCR-03; US-141): one sentence becomes a plan of headline figures,
 * sections, page filters and what was left out, previewed on a 12-column mini-grid. Change it in words, then Build
 * creates a draft and opens it on the canvas; the build results travel with the navigation.
 */
export function ReportPlannerPage() {
  const appName = useAppName();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [request, setRequest] = useState(params.get("request") ?? "");
  const [plan, setPlan] = useState<ReportPlan | null>(null);
  const [took, setTook] = useState<number | null>(null);
  const [edit, setEdit] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<"plan" | "edit" | "build" | "show" | null>(null);
  const started = useRef(false);
  const [blank, setBlank] = useState(false);
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });

  const run = async (kind: "plan" | "edit" | "build" | "show", fn: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try { await fn(); } catch (e) { setError(asApiError(e)); } finally { setBusy(null); }
  };
  const makePlan = () => run("plan", async () => {
    const t0 = performance.now();
    setPlan(await api<ReportPlan>("/api/v1/reports/plan", { method: "POST", body: { request: request.trim() } }));
    setTook((performance.now() - t0) / 1000);
  });
  const editPlan = () => run("edit", async () => {
    setPlan(await api<ReportPlan>(`/api/v1/reports/plans/${plan!.plan_id}/edit`, { method: "POST", body: { instruction: edit.trim() } }));
    setEdit("");
  });
  const show = (which: PlanReview["showing"]) => run("show", async () => {
    setPlan(await api<ReportPlan>(`/api/v1/reports/plans/${plan!.plan_id}/show`, { method: "POST", body: { show: which } }));
  });
  const build = () => run("build", async () => {
    const out = await api<BuildResult>(`/api/v1/reports/plans/${plan!.plan_id}/build`, { method: "POST" });
    navigate(`/reports/${out.report_id}`, { state: { built: out.results } satisfies BuiltState });
  });

  useEffect(() => {
    if (!started.current && params.get("request")) {
      started.current = true;
      void makePlan();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const scope = plan && home.data?.greeting.scopes.find((s) => s.connection_id === plan.connection_id || s.connection_name === plan.connection);
  const widgets = plan?.sections.flatMap((s) => s.widgets) ?? [];
  const measures = [...new Map(widgets.map((w) => [w.measure_label, w.certified])).entries()];
  const buildable = !!plan && plan.sections.length > 0;

  return (
    <div className="tl grid gap-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">New report</h1>
          <p className="text-ink-muted">Describe the report. {appName} plans it from certified measures; you change the plan in words, then build.</p>
        </div>
        <Button variant="secondary" onClick={() => setBlank(true)}><LayoutGrid aria-hidden />Start from a blank canvas</Button>
      </header>

      <Card className="gap-3 p-4">
        <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (request.trim()) void makePlan(); }}>
          <label htmlFor="plan-request" className="text-body-strong">What should the report show?</label>
          <Textarea id="plan-request" value={request} maxLength={2000} rows={2} aria-label="Describe the report"
            onChange={(e) => setRequest(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && request.trim()) { e.preventDefault(); void makePlan(); } }}
            placeholder={home.data?.suggested_questions?.[0] ? `For example: ${home.data.suggested_questions[0]}` : "Describe the figures and breakdowns you want, in your own words"} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-small text-ink-muted" aria-live="polite">
              {plan && took !== null && busy === null ? `Planned in ${took.toFixed(1)} s · uses model ${plan.connection}${plan.model_version ? ` v${plan.model_version}` : ""}` : "Enter to plan, Shift + Enter for a new line"}
            </span>
            <Button type="submit" disabled={busy !== null || !request.trim()}>
              {plan ? <><RotateCw aria-hidden />Plan again</> : <><Sparkles aria-hidden />Plan</>}
            </Button>
          </div>
        </form>
      </Card>

      <FormAlert error={error} />
      {busy === "plan" && <PlanSkeleton />}

      {plan && busy !== "plan" && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Card className="gap-4 p-4" aria-label="Report plan" role="region">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-heading">Plan · {plan.title}</h2>
              <span className="text-small text-ink-muted">
                {plan.sections.length} {plan.sections.length === 1 ? "section" : "sections"} · {widgets.length} {widgets.length === 1 ? "widget" : "widgets"} · 12-column layout
              </span>
            </div>
            {plan.notes?.map((n) => <p key={n} className="text-small text-ink-muted">{n}</p>)}
            {plan.review && <PlanReviewNote review={plan.review} busy={busy !== null} onShow={(w) => void show(w)} />}
            {plan.sections.length === 0 ? (
              <EmptyState icon={CircleSlash} title="Nothing to build yet">Nothing in this request can be built from the connected data. Try naming one of the measures in your data model.</EmptyState>
            ) : plan.sections.map((s) => (
              <section key={s.key} aria-label={s.title} className="grid gap-2">
                <h3 className="text-small font-semibold uppercase tracking-wide text-ink-muted">{s.title}</h3>
                {s.blueprint && (
                  <p className="text-small text-ink-muted">
                    Report catalogue layout{s.blueprint.question ? ` · ${s.blueprint.question}` : ""}
                    {s.blueprint.population && <> Counts: {s.blueprint.population}</>}
                  </p>
                )}
                {s.callouts?.map((c) => <TextBlock key={c.text} kind="callout" title={c.title} level={c.level} text={c.text} />)}
                <PlanGrid plan={plan} sectionKey={s.key} />
                {s.footnote && <TextBlock kind="footnote" text={s.footnote} />}
              </section>
            ))}
            {plan.page_filters.length > 0 && (
              <p className="text-small text-ink-muted">Page filters: {plan.page_filters.map((f) => f.label).join(", ")}</p>
            )}
            {buildable && (
              <form className="grid gap-2 border-t border-line pt-3" onSubmit={(e) => { e.preventDefault(); if (edit.trim()) void editPlan(); }}>
                <label htmlFor="plan-edit" className="text-body-strong">Change the plan in words</label>
                <div className="flex gap-2">
                  <Input id="plan-edit" value={edit} maxLength={1000} onChange={(e) => setEdit(e.target.value)}
                    placeholder="Remove …, add …, add a priority filter, rename … to …" />
                  <Button type="submit" variant="secondary" disabled={busy !== null || !edit.trim()}>Apply</Button>
                </div>
                <PlanDiff plan={plan} />
              </form>
            )}
          </Card>

          <aside className="grid content-start gap-4">
            {measures.length > 0 && (
              <Card className="gap-2 p-4">
                <h2 className="text-heading">Measures in this plan</h2>
                <ul className="grid gap-1.5">
                  {measures.map(([label, certified]) => (
                    <li key={label} className="flex items-center justify-between gap-2">
                      <span className="truncate">{label}</span>
                      <ContentBadge badge={certified ? "certified" : "personal"} />
                    </li>
                  ))}
                </ul>
                {scope && <p className="flex gap-1.5 text-small text-ink-muted"><Lock aria-hidden className="mt-0.5 size-3.5 shrink-0" />Every widget uses your scope: {scope.text}</p>}
              </Card>
            )}
            {plan.gaps.length > 0 && (
              <Card className="gap-2 p-4" role="region" aria-label="Not included">
                <h2 className="text-heading">Left out</h2>
                <ul className="grid gap-1.5 text-small">
                  {plan.gaps.map((g, i) => <li key={i}><span className="text-body-strong">“{g.request_part}”</span> — {g.reason}</li>)}
                </ul>
              </Card>
            )}
            {buildable && (
              <div className="grid gap-1.5">
                <Button size="lg" onClick={() => void build()} disabled={busy !== null}>{busy === "build" ? "Building…" : "Build report"}</Button>
                <p className="text-small text-ink-muted">Builds a draft only you can see. You can change anything after.</p>
              </div>
            )}
          </aside>
        </div>
      )}
      <NewReportDialog open={blank} onOpenChange={setBlank} />
    </div>
  );
}

/** US-243: what the AI changed against the rules plan, with a way back to the rules plan (and forth again). */
function PlanReviewNote({ review, busy, onShow }: { review: PlanReview; busy: boolean; onShow: (which: PlanReview["showing"]) => void }) {
  const refined = review.changes.length > 0;
  if (!refined && review.verdict !== "confirm" && review.dropped.length === 0) return null;
  return (
    <div role="status" aria-label="AI review" className="grid gap-1.5 rounded-md border border-line bg-surface-sunken p-3 text-small">
      {refined ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-body-strong">
            <Sparkles aria-hidden className="size-4" />{review.showing === "ai" ? "Refined by AI" : "Showing the rules plan"}
          </span>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => onShow(review.showing === "ai" ? "rules" : "ai")}>
            {review.showing === "ai" ? "Show the rules plan" : "Show the AI plan"}
          </Button>
        </div>
      ) : review.verdict === "confirm" && (
        <span className="flex items-center gap-1.5 text-ink-muted"><BadgeCheck aria-hidden className="size-4" />Checked by AI: the plan fits your request</span>
      )}
      {refined && review.reason && <p className="text-ink-muted">{review.reason}</p>}
      {refined && review.changes.length > 0 && (
        <ul className="grid list-disc gap-0.5 pl-5">{review.changes.map((c) => <li key={c}>{c}</li>)}</ul>
      )}
      {review.dropped.length > 0 && (
        <details className="text-ink-muted">
          <summary className="cursor-pointer">{review.dropped.length} AI {review.dropped.length === 1 ? "suggestion" : "suggestions"} left out</summary>
          <ul className="grid list-disc gap-0.5 pl-5">{review.dropped.map((d) => <li key={d}>{d}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

function PlanDiff({ plan }: { plan: ReportPlan }) {
  const d = plan.diff;
  if (!d || (d.added.length === 0 && d.removed.length === 0)) return null;
  return (
    <p aria-label="Changes" role="status" className="flex flex-wrap gap-1.5 text-small">
      {d.added.map((a) => <Badge key={`+${a}`} variant="certified">Added {a}</Badge>)}
      {d.removed.map((r) => <Badge key={`-${r}`} variant="outline"><del>Removed {r}</del></Badge>)}
    </p>
  );
}

/** The plan's layout on a 12-column mini-grid; each box names the widget and its chart. */
function PlanGrid({ plan, sectionKey }: { plan: ReportPlan; sectionKey: string }) {
  const section = plan.sections.find((s) => s.key === sectionKey)!;
  // The section's callouts are shown above the grid, so the rows they hold in the layout are not drawn.
  const top = (section.callouts?.length ?? 0) * 2;
  const boxes = plan.layout_preview.filter((b) => b.section === sectionKey).map((b) => ({ ...b, y: b.y - top }));
  const rows = Math.max(1, ...boxes.map((b) => b.y + b.h));
  const added = new Set(plan.diff?.added ?? []);
  const byAi = new Set(plan.review?.showing === "ai" ? plan.review.added : []);
  return (
    <ul className="grid grid-cols-12 gap-2" style={{ gridTemplateRows: `repeat(${rows}, 1.5rem)` }}>
      {boxes.map((b) => {
        const w = section.widgets.find((x) => x.ref === b.widget_ref)!;
        const fresh = added.has(w.title) || byAi.has(w.title);
        return (
          <li key={b.widget_ref} style={{ gridColumn: `${b.x + 1} / span ${b.w}`, gridRow: `${b.y + 1} / span ${b.h}` }}
            className={cn("grid min-w-0 content-start gap-0.5 overflow-hidden rounded-md border p-2",
              fresh ? "border-accent bg-accent-soft" : "border-line bg-surface-sunken")}>
            <span className="truncate text-body-strong" title={w.title}>{w.title}</span>
            <span className="flex items-center gap-1 truncate text-small text-ink-muted">
              {VISUAL_NAMES[w.visual] ?? w.visual}
              {fresh ? (byAi.has(w.title) && !added.has(w.title) ? " · added by AI" : " · added from your words") : w.certified && <><span aria-hidden>·</span><BadgeCheck aria-label="certified" className="size-3.5 text-certified" /></>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function PlanSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]" aria-busy="true" aria-label="Planning">
      <Skeleton className="h-72" />
      <div className="grid content-start gap-4"><Skeleton className="h-40" /><Skeleton className="h-12" /></div>
    </div>
  );
}
