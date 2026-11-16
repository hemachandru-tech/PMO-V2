import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CircleSlash, ExternalLink, Hammer, Sparkles, Trash2 } from "lucide-react";
import { BrandLoader } from "../../components/BrandLoader";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, type ApiError } from "../../api/client";
import type { Home } from "../../api/app";
import { VISUAL_NAMES } from "../../api/authoring";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import { asApiError } from "../../lib/api-errors";

interface PlanFilter { by: string; op: string; values: string[] }
interface PlanWidget {
  ref: string; title: string; visual: string; measure: string; measure_label?: string; extra_measures: string[];
  group_by: string[]; time: { by: string; grain: string } | null; filters: PlanFilter[]; top: number | null; question: string;
}
interface PlanPage {
  title: string; purpose: string; kpis: PlanWidget[]; widgets: PlanWidget[]; filled_in: number;
  widget_count: number; kpi_count: number; page_filters: { ref: string; label: string }[];
}
interface DashboardPlan {
  title: string; connection: string; model_version: number | null; data_date: string | null; min_widgets: number;
  reports: PlanPage[]; global_filters: { key: string; label: string; reaches_all: boolean; shared: boolean }[];
  not_possible: string[]; dropped: { report: string; widget: string; reason: string }[];
  catalogue: { measures: number; group_bys: number; rules: number };
}
interface BuildRow { widget_ref: string; widget_id: string; title: string; status: string; message?: string; rows?: number; report: string }
interface Job {
  job_id: string; status: "planning" | "planned" | "failed" | "built"; steps: string[]; error?: string;
  plan?: DashboardPlan; dashboard_id?: string; results?: BuildRow[];
}

const describe = (w: PlanWidget) => [
  w.group_by.length ? `by ${w.group_by.join(", ")}` : "",
  w.time ? `over ${w.time.by} (${w.time.grain})` : "",
  w.filters.length ? `where ${w.filters.map((f) => `${f.by} ${f.op} ${f.values.join(", ")}`).join("; ")}` : "",
  w.top ? `top ${w.top}` : "",
].filter(Boolean).join(" · ");

/**
 * Dashboard planned by the LLM from the approved data model: the request (as long as you like: pages, KPIs, widgets,
 * global filters, things to avoid) becomes pages of KPIs and at least N widgets, every key checked against the model.
 * Review the plan, take out what you don't want, then Build creates the draft reports and the dashboard.
 */
export function AiDashboardPage() {
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  const sources = home.data?.greeting.scopes ?? [];
  const [request, setRequest] = useState("");
  const [connection, setConnection] = useState("");
  const [minWidgets, setMinWidgets] = useState(6);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);

  const poll = useQuery({
    queryKey: ["dashboard-ai-job", jobId],
    queryFn: () => api<Job>(`/api/v1/dashboards/ai/jobs/${jobId}`),
    enabled: !!jobId && (!job || job.status === "planning"),
    refetchInterval: 1500, refetchIntervalInBackground: true,
  });
  useEffect(() => {
    if (poll.data && (!job || job.status === "planning")) setJob(poll.data);
  }, [poll.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const current = job;

  const start = async () => {
    setBusy(true); setError(null); setJob(null); setJobId(null);
    try {
      const out = await api<{ job_id: string }>("/api/v1/dashboards/ai/plan", {
        method: "POST", body: { request: request.trim(), min_widgets: minWidgets, ...(connection ? { connection_id: connection } : {}) },
      });
      setJobId(out.job_id);
    } catch (e) { setError(asApiError(e)); } finally { setBusy(false); }
  };
  const remove = async (report: number, widget_ref: string | null) => {
    try {
      setJob(await api<Job>(`/api/v1/dashboards/ai/jobs/${jobId}/remove`, { method: "POST", body: { report, widget_ref } }));
    } catch (e) { setError(asApiError(e)); }
  };
  const build = async () => {
    setBusy(true); setError(null);
    try {
      const out = await api<{ dashboard_id: string; results: BuildRow[] }>(`/api/v1/dashboards/ai/jobs/${jobId}/build`, { method: "POST" });
      setJob((j) => j && { ...j, status: "built", dashboard_id: out.dashboard_id, results: out.results });
    } catch (e) { setError(asApiError(e)); } finally { setBusy(false); }
  };

  const plan = current?.plan;
  const planning = current?.status === "planning" || (!!jobId && !current);
  return (
    <div className="tl grid gap-6">
      <header className="grid gap-1">
        <h1 className="text-title flex items-center gap-2"><Sparkles aria-hidden />Plan a dashboard with AI</h1>
        <p className="text-ink-muted">
          Describe the dashboard: its pages, KPIs, widgets, global filters and anything to avoid, or just its purpose.
          The language model plans it from your approved data model only: every measure, group-by and filter value is
          checked against the model, and anything that can't be checked is left out with the reason.
        </p>
      </header>

      <Card className="gap-3 p-4">
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (request.trim()) void start(); }}>
          <label htmlFor="dash-request" className="text-body-strong">What should the dashboard show?</label>
          <Textarea id="dash-request" value={request} maxLength={8000} rows={8} onChange={(e) => setRequest(e.target.value)}
            placeholder={home.data?.suggested_questions?.length
              ? `For example: one page per area of the data, with KPIs and charts such as “${home.data.suggested_questions[0]}”`
              : "For example: an overview page and one page per main table, with KPIs, trends and breakdowns"} />
          <div className="flex flex-wrap items-end gap-4">
            {sources.length > 1 && (
              <label className="grid gap-1 text-small">Data source
                <select className="h-9 rounded-md border border-line bg-surface px-2" value={connection} onChange={(e) => setConnection(e.target.value)}>
                  <option value="">Pick from the request</option>
                  {sources.map((s) => <option key={s.connection_id} value={s.connection_id}>{s.connection_name}</option>)}
                </select>
              </label>
            )}
            <label className="grid gap-1 text-small">Minimum widgets per page
              <Input type="number" min={1} max={12} value={minWidgets} className="w-24"
                onChange={(e) => setMinWidgets(Math.max(1, Math.min(12, Number(e.target.value) || 6)))} />
            </label>
            <Button type="submit" disabled={busy || planning || !request.trim()}><Sparkles aria-hidden />{planning ? "Planning…" : "Plan dashboard"}</Button>
          </div>
        </form>
        <FormAlert error={error} />
      </Card>

      {current && (
        <Card className="gap-2 p-4" aria-live="polite">
          <div className="flex items-center gap-3">
            {(current.status === "planning" || busy) && <BrandLoader size={36} label="Working" />}
            <h2 className="text-body-strong">{current.status === "planning" ? "Planning your dashboard…" : busy ? "Building…" : "Progress"}</h2>
          </div>
          <ol className="grid gap-1 text-small text-ink-muted">
            {current.steps.map((s, i) => <li key={i}>• {s}</li>)}
          </ol>
          {current.status === "failed" && <p className="text-small text-danger">{current.error}</p>}
        </Card>
      )}

      {plan && (
        <section className="grid gap-4" aria-label="Dashboard plan">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="grid gap-1">
              <h2 className="text-heading">{plan.title}</h2>
              <p className="text-small text-ink-muted">
                Model {plan.connection}{plan.model_version ? ` v${plan.model_version}` : ""} · data date {plan.data_date ?? "not set"} ·
                read {plan.catalogue.measures} measures, {plan.catalogue.group_bys} group-bys, {plan.catalogue.rules} rules
              </p>
              {plan.global_filters.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-small">Global filters:
                  {plan.global_filters.map((f) => (
                    <Badge key={f.key} variant={f.shared && f.reaches_all ? "certified" : "outline"}
                      title={f.shared && f.reaches_all ? "Shared across every page" : "Not every page can be filtered by it, so it is not shared"}>{f.label}</Badge>
                  ))}
                </div>
              )}
            </div>
            {current?.status === "built" && current.dashboard_id ? (
              <div className="flex gap-2">
                <Button asChild><Link to={`/dashboards/${current.dashboard_id}/draft`}><ExternalLink aria-hidden />Open dashboard</Link></Button>
                <Button asChild variant="secondary"><Link to={`/dashboards/${current.dashboard_id}/edit`}>Edit in composer</Link></Button>
              </div>
            ) : (
              <Button onClick={() => void build()} disabled={busy || !plan.reports.length}><Hammer aria-hidden />{busy ? "Building…" : "Build dashboard"}</Button>
            )}
          </div>

          {plan.reports.length === 0 && <EmptyState icon={CircleSlash} title="Nothing to build">Nothing in this request could be planned from the model.</EmptyState>}

          {plan.reports.map((p, ri) => (
            <Card key={ri} className="gap-3 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="grid gap-1">
                  <h3 className="text-body-strong">Page {ri + 1}: {p.title}</h3>
                  {p.purpose && <p className="text-small text-ink-muted">{p.purpose}</p>}
                  <p className="text-small text-ink-muted">
                    {p.kpi_count} KPIs · {p.widget_count} widgets{p.widget_count < plan.min_widgets ? ` (fewer than ${plan.min_widgets}: the model has nothing more that fits)` : ""}
                    {p.filled_in ? ` · ${p.filled_in} filled in from the model` : ""}
                  </p>
                </div>
                {current?.status === "planned" && <Button variant="ghost" size="sm" onClick={() => void remove(ri, null)}><Trash2 aria-hidden />Remove page</Button>}
              </div>
              {p.kpis.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
                  {p.kpis.map((k) => (
                    <div key={k.ref} className="rounded-md border border-line p-2 text-small">
                      <div className="text-body-strong">{k.title}</div>
                      <code className="text-ink-muted">{k.measure}</code>
                      {k.filters.length > 0 && <div className="text-ink-muted">{describe(k)}</div>}
                    </div>
                  ))}
                </div>
              )}
              <table className="w-full text-small">
                <thead><tr className="text-left text-ink-muted"><th className="py-1">#</th><th>Widget</th><th>Chart</th><th>Measure</th><th>Grouped / filtered</th><th /></tr></thead>
                <tbody>
                  {p.widgets.map((w, wi) => (
                    <tr key={w.ref} className="border-t border-line align-top">
                      <td className="py-1 pr-2">W{wi + 1}</td>
                      <td className="pr-2">{w.title}{w.question && <div className="text-ink-muted">{w.question}</div>}</td>
                      <td className="pr-2">{VISUAL_NAMES[w.visual] ?? w.visual}</td>
                      <td className="pr-2"><code>{[w.measure, ...w.extra_measures].join(", ")}</code></td>
                      <td className="pr-2 text-ink-muted">{describe(w) || "—"}</td>
                      <td>{current?.status === "planned" && <Button variant="ghost" size="sm" aria-label={`Remove ${w.title}`} onClick={() => void remove(ri, w.ref)}><Trash2 aria-hidden /></Button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}

          {current?.results && (
            <Card className="gap-2 p-4">
              <h3 className="text-body-strong flex items-center gap-2"><BadgeCheck aria-hidden />Built: every widget was run once</h3>
              <p className="text-small text-ink-muted">
                {current.results.filter((r) => r.status === "ok").length} with data ·{" "}
                {current.results.filter((r) => r.status === "empty").length} empty ·{" "}
                {current.results.filter((r) => r.status === "failed").length} failed
              </p>
              <ul className="grid gap-1 text-small">
                {current.results.filter((r) => r.status !== "ok").map((r) => (
                  <li key={r.widget_ref + r.report}>{r.report} → {r.title}: <span className="text-ink-muted">{r.status}{r.message ? ` (${r.message})` : ""}</span></li>
                ))}
              </ul>
            </Card>
          )}

          {(plan.dropped.length > 0 || plan.not_possible.length > 0) && (
            <Card className="gap-2 p-4" role="region" aria-label="Left out">
              <h3 className="text-body-strong">Left out (not in the model or failed a check)</h3>
              <ul className="grid gap-1 text-small text-ink-muted">
                {plan.not_possible.map((n, i) => <li key={`n${i}`}>• {n}</li>)}
                {plan.dropped.map((d, i) => <li key={`d${i}`}>• {d.report} → {d.widget}: {d.reason}</li>)}
              </ul>
            </Card>
          )}
        </section>
      )}
    </div>
  );
}
