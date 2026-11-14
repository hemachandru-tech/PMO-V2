import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";

import type { Column } from "../../../api/app";
import { api, type ApiError } from "../../../api/client";
import type { LlmSettings, LlmUsage, PlannerHealth } from "../../../api/authoring";
import type { ChartSpec } from "../../../api/querySpec";
import { Chart } from "../../../components/Chart";
import { formatAsOf } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Field, Form, FormControl, FormDescription, FormField, FormItem, FormLabel } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { Skeleton } from "../../../components/ui/skeleton";
import { StatusBadge } from "../../../components/ui/status-badge";
import { Switch } from "../../../components/ui/switch";
import { applyServerError, asApiError } from "../../../lib/api-errors";
import { useAppName } from "../../../app/branding";

const JOBS = [
  { key: "planner", label: "Planning answers", note: "Turns a question into a query", fallback: "Rules planner" },
  { key: "narrator", label: "Writing answers", note: "The sentence above a figure", fallback: "Template sentence" },
  { key: "advisor", label: "Chart suggestions", note: "Clearer charts, useful comparisons", fallback: "Built-in suggestions" },
  { key: "embeddings", label: "Search over knowledge", note: "Embeddings for approved documents", fallback: "Keyword search" },
] as const;
type Job = (typeof JOBS)[number]["key"];

/** Providers approved under DEC-01, with example model names (Azure uses the tenant's own deployment names). */
const PROVIDERS: Record<string, { label: string; examples: Partial<Record<Job, string>> }> = {
  ollama: { label: "Local model (Ollama)", examples: { planner: "qwen3:8b", embeddings: "mxbai-embed-large" } },
  anthropic: { label: "Anthropic Claude", examples: { planner: "claude-opus-5-5", narrator: "claude-sonnet-5-5", advisor: "claude-sonnet-5-5" } },
  openai: { label: "OpenAI", examples: { embeddings: "text-embedding-3-large" } },
  azure_openai: { label: "Azure OpenAI", examples: { planner: "Deployment name", narrator: "Deployment name", advisor: "Deployment name", embeddings: "Embedding deployment" } },
  gemini: { label: "Google Gemini", examples: { embeddings: "gemini-embedding-001" } },
};
const LOCAL = ["ollama", "local"];
const providerLabel = (p: string) => PROVIDERS[p]?.label ?? p;

const number = (what: string, max: number) => z.string().regex(/^\d*(\.\d+)?$/, `Enter ${what} as a number.`).refine((v) => !v || Number(v) <= max, `Enter at most ${max.toLocaleString()}.`);

const settingsForm = z.object({
  provider: z.string(),
  planner: z.string().max(200),
  narrator: z.string().max(200),
  advisor: z.string().max(200),
  embeddings: z.string().max(200),
  allow_aggregates: z.boolean(),
  allow_samples: z.boolean(),
  llm_review: z.boolean(),
  budget: z.string().regex(/^\d*$/, "Enter whole tokens, for example 5000000."),
  // A model a job has just been given has no rate yet: its inputs start undefined.
  rates: z.record(z.string(), z.object({ input: number("the input rate", 1000).optional(), output: number("the output rate", 1000).optional() })),
  api_key: z.string().refine((v) => !v || (v.length >= 8 && v.length <= 500), "A key has 8 to 500 characters."),
  endpoint: z.string().max(200),
});
type SettingsForm = z.infer<typeof settingsForm>;

/** The form rules that depend on the saved settings: a keyed provider needs its own key, Azure its endpoint. */
const schemaFor = (s: LlmSettings) => settingsForm.superRefine((v, ctx) => {
  if (v.provider && !v.planner.trim()) ctx.addIssue({ code: "custom", path: ["planner"], message: "Choose a planner model for the provider." });
  const hasKey = s.key_set && s.provider === v.provider;
  if ((s.keyed_providers ?? []).includes(v.provider) && !hasKey && !v.api_key) ctx.addIssue({ code: "custom", path: ["api_key"], message: `Enter the API key for ${providerLabel(v.provider)}.` });
  if (v.provider === "azure_openai" && !/^https:\/\/[A-Za-z0-9.-]+(:\d+)?\/?$/.test(v.endpoint.trim())) ctx.addIssue({ code: "custom", path: ["endpoint"], message: "Enter the endpoint, for example https://acme.openai.azure.com." });
});

/** Form key for a model's rates: model names such as "llama3.2:3b" contain dots, which form paths would split on. */
const rk = (model: string) => model.replace(/\./g, "%2E");

function toForm(s: LlmSettings): SettingsForm {
  const rates: SettingsForm["rates"] = {};
  for (const [model, r] of Object.entries(s.rates)) rates[rk(model)] = { input: String(r.input_per_mtok), output: String(r.output_per_mtok) };
  return {
    provider: s.provider ?? "", planner: s.models.planner ?? "", narrator: s.models.narrator ?? "", advisor: s.models.advisor ?? "",
    embeddings: s.models.embeddings ?? "", allow_aggregates: s.allow_aggregates, allow_samples: s.allow_samples,
    llm_review: s.llm_review ?? false,
    budget: s.monthly_token_budget ? String(s.monthly_token_budget) : "", rates, api_key: "", endpoint: s.endpoint ?? "",
  };
}

/**
 * AI settings and usage (FEAT-049; API-053, API-054; US-106 built in US-146; board AiSettings): which model does each
 * job, what models may see, the monthly budget and cost rates, a write-only provider key, then usage over the last
 * days — calls, tokens, estimated cost, latency and calls blocked by policy. Saving a provider runs a test call
 * through the gateway; a failing test is not saved. DEC-01 approves Anthropic, OpenAI, Azure OpenAI and Gemini next
 * to the local model; each keyed provider keeps its own key.
 */
export function AiSettingsPage() {
  const appName = useAppName();
  const settings = useQuery({ queryKey: ["llm-settings"], queryFn: () => api<LlmSettings>("/api/v1/admin/llm-settings") });
  return (
    <section className="tl grid gap-6">
      <div className="grid gap-1">
        <h1 className="text-title">AI settings and usage</h1>
        <p className="text-ink-muted">Which models {appName} may use, what they may see, and what they cost.</p>
      </div>
      {settings.isLoading ? <Skeleton className="h-96" /> : settings.error ? <FormAlert error={asApiError(settings.error)} /> : <SettingsCard settings={settings.data!} />}
      <Usage />
    </section>
  );
}

function SettingsCard({ settings }: { settings: LlmSettings }) {
  const appName = useAppName();
  const qc = useQueryClient();
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);
  const schema = useMemo(() => schemaFor(settings), [settings]);
  const form = useForm<SettingsForm>({ resolver: zodResolver(schema), mode: "onTouched", defaultValues: toForm(settings) });
  useEffect(() => form.reset(toForm(settings)), [settings]); // eslint-disable-line react-hooks/exhaustive-deps
  const values = form.watch();
  // DEC-28: a new provider starts with its default (on for hosted, off for local) unless the Admin set the switch.
  const reviewSet = useRef(false);
  useEffect(() => {
    if (reviewSet.current) return;
    const saved = values.provider === (settings.provider ?? "");
    form.setValue("llm_review", saved ? (settings.llm_review ?? false) : !!values.provider && !LOCAL.includes(values.provider));
  }, [values.provider]); // eslint-disable-line react-hooks/exhaustive-deps
  const embeds = (p: string) => (settings.embedding_providers ?? ["ollama"]).includes(p);
  // Cost rates are kept per model name, for every model a job uses.
  const models = useMemo(() => [...new Set(JOBS.filter((j) => j.key !== "embeddings" || embeds(values.provider)).map((j) => values[j.key]?.trim()).filter((m): m is string => !!m))], [values.provider, values.planner, values.narrator, values.advisor, values.embeddings]); // eslint-disable-line react-hooks/exhaustive-deps
  const submit = async (v: SettingsForm) => {
    setError(null);
    setSaved(false);
    const body = {
      provider: v.provider || null,
      // A provider without embeddings uses the deployment's: its embeddings box is not sent.
      models: Object.fromEntries(JOBS.map((j) => [j.key, v[j.key].trim()]).filter(([k, m]) => m && (k !== "embeddings" || embeds(v.provider)))),
      allow_aggregates: v.allow_aggregates, allow_samples: v.allow_samples,
      llm_review: !!v.provider && v.llm_review,
      monthly_token_budget: v.budget ? Number(v.budget) : 0,
      rates: Object.fromEntries(models.map((m) => [m, { input_per_mtok: Number(v.rates[rk(m)]?.input || 0), output_per_mtok: Number(v.rates[rk(m)]?.output || 0) }])),
      ...(v.api_key && keyed.includes(v.provider) ? { api_key: v.api_key } : {}),
      ...(v.provider === "azure_openai" ? { endpoint: v.endpoint.trim().replace(/\/$/, "") } : {}),
    };
    try {
      const out = await api<LlmSettings>("/api/v1/admin/llm-settings", { method: "PUT", body });
      qc.setQueryData(["llm-settings"], out);
      setSaved(true);
    } catch (e) {
      setError(applyServerError(form, e, { fields: { monthly_token_budget: "budget", "models.planner": "planner", endpoint: "endpoint", api_key: "api_key" } }));
      void qc.invalidateQueries({ queryKey: ["llm-settings"] });
    }
  };
  const c = form.control;
  const busy = form.formState.isSubmitting;
  const local = settings.allowed_providers.length > 0 && settings.allowed_providers.every((p) => ["ollama", "local"].includes(p));
  const test = settings.last_test;
  const keyed = settings.keyed_providers ?? [];
  const needsKey = keyed.includes(values.provider);
  const ownEmbeddings = embeds(values.provider);
  const examples = PROVIDERS[values.provider]?.examples ?? {};
  const embeddingChoice = (p: string, m: string) => (embeds(p) && m.trim() ? `${p}:${m.trim()}` : "");
  const reindex = embeddingChoice(values.provider, values.embeddings) !== embeddingChoice(settings.provider ?? "", settings.models.embeddings ?? "");
  return (
    <Form {...form}>
      <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} aria-label="AI settings" noValidate>
        {settings.allowed_providers.length === 0 ? (
          <Alert variant="warn"><p>The AI gateway is not reachable, so providers cannot be listed or tested. {appName} keeps answering with its built-in rules.</p></Alert>
        ) : local && (
          <Alert><p>Only the local model is allowed on this deployment. No client data leaves your servers.</p></Alert>
        )}
        <Card>
          <CardHeader className="block">
            <CardTitle>Model for each job</CardTitle>
            <CardDescription>When a model fails or is not set, {appName} falls back to the built-in way shown.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field control={c} name="provider" label="Provider" description={`Allowed by this deployment: ${settings.allowed_providers.map(providerLabel).join(", ") || "none"}.`} render={(f) => (
                <NativeSelect {...f}>
                  <option value="">None: built-in rules only</option>
                  {settings.allowed_providers.map((p) => <option key={p} value={p}>{providerLabel(p)}</option>)}
                </NativeSelect>
              )} />
              {values.provider === "azure_openai" && (
                <Field control={c} name="endpoint" label="Azure OpenAI endpoint" description="Your resource; it also sets the region." render={(f) => <Input {...f} inputMode="url" placeholder="https://acme.openai.azure.com" />} />
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-body">
                <thead className="text-small text-ink-muted">
                  <tr><th className="py-2 pr-4 font-medium">Job</th><th className="py-2 pr-4 font-medium">Model</th><th className="py-2 font-medium">Fallback when it fails</th></tr>
                </thead>
                <tbody>
                  {JOBS.map((j) => (
                    <tr key={j.key} className="border-t border-line align-top">
                      <td className="py-2 pr-4"><span className="grid"><span className="text-body-strong">{j.label}</span><span className="text-small text-ink-muted">{j.note}</span></span></td>
                      <td className="py-2 pr-4">
                        <FormField control={c} name={j.key as Job} render={({ field, fieldState }) => (
                          <FormItem>
                            <FormControl><Input {...field} aria-label={`${j.label} model`} placeholder={!values.provider ? "Off" : j.key === "embeddings" && !ownEmbeddings ? "Local embeddings" : (examples[j.key] ?? "Model name")} disabled={!values.provider || (j.key === "embeddings" && !ownEmbeddings)} /></FormControl>
                            {j.key === "embeddings" && values.provider && !ownEmbeddings && <p className="text-small text-ink-muted">{providerLabel(values.provider)} has no embeddings service; the deployment's local embeddings are used.</p>}
                            {fieldState.error && <p className="text-small text-status-danger">{fieldState.error.message}</p>}
                          </FormItem>
                        )} />
                      </td>
                      <td className="py-2 text-ink-muted">{j.fallback}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {settings.planner_health && <PlannerFit health={settings.planner_health} />}
            {reindex && <Alert variant="warn"><p>A new embedding model rebuilds the knowledge index after saving. Search uses keywords until it finishes.</p></Alert>}
            {test && (
              <p className="flex flex-wrap items-center gap-2 text-small">
                <StatusBadge status={test.ok ? "ok" : "danger"}>{test.ok ? "Test call passed" : "Test call failed"}</StatusBadge>
                <span className="text-ink-muted">{formatAsOf(test.tested_at)}{test.latency_ms ? ` · ${test.latency_ms} ms` : ""}{test.model ? ` · ${test.model}` : ""}{!test.ok && test.message ? ` · ${test.message}` : ""}</span>
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="block">
            <CardTitle>How agents use the model</CardTitle>
            <CardDescription>The built-in rules always make the first draft.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Toggle control={c} name="llm_review" label="Review every draft with the model" disabled={!values.provider} onUserChange={() => { reviewSet.current = true; }}
              description={!values.provider ? "Choose a provider first." : `The model checks each draft and confirms it, corrects it or asks which reading was meant. A correction is never applied silently. Turn it on or off for each agent in Admin → Agents (report plans and chat edits start off). Adds one or more model calls to every answer${LOCAL.includes(values.provider) ? "; on a local model each can take a minute" : ""}.`} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="block">
            <CardTitle>What models may see</CardTitle>
            <CardDescription>Field names and definitions are always sent: they are needed to understand questions.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Toggle control={c} name="allow_aggregates" label="Totals and counts in answers" disabled={settings.aggregates_allowed === false && !values.allow_aggregates}
              description={settings.aggregates_allowed === false ? "Turned off for this deployment." : "Lets the model write sentences about results. The data policy allows totals and counts, never individual records."} />
            <Toggle control={c} name="allow_samples" label="Sample field values" description="A few example values per field help the model match words like “KSA” to a field. Off until the data policy allows it." />
            <p className="text-small text-ink-muted">Individual records are never sent to a model.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="block">
            <CardTitle>Budget, cost rates and key</CardTitle>
            <CardDescription>Costs are estimated from these rates, not from provider bills.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field control={c} name="budget" label="Monthly token budget" description="Leave blank for no budget." render={(f) => <Input {...f} inputMode="numeric" placeholder="No budget" />} />
              {needsKey && (() => {
                const saved = settings.key_set && settings.provider === values.provider;
                return (
                  <Field control={c} name="api_key" label={`${providerLabel(values.provider)} API key`}
                    description={saved ? "A key is saved. Write-only: it is never shown again; enter a new one to replace it." : "Write-only: a saved key is never shown again. Each provider keeps its own key."}
                    render={(f) => <Input {...f} type="password" autoComplete="new-password" placeholder={saved ? "•••••••• (saved)" : ""} />} />
                );
              })()}
            </div>
            {models.length > 0 && (
              <fieldset className="grid gap-2">
                <legend className="mb-1 text-body-strong">Cost per million tokens (USD)</legend>
                {models.map((m) => (
                  <div key={m} className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_10rem_10rem]">
                    <span className="pb-2 font-mono text-small">{m}</span>
                    <Field control={c} name={`rates.${rk(m)}.input` as `rates.${string}.input`} label="Input" render={(f) => <Input {...f} value={f.value ?? ""} inputMode="decimal" placeholder="0" />} />
                    <Field control={c} name={`rates.${rk(m)}.output` as `rates.${string}.output`} label="Output" render={(f) => <Input {...f} value={f.value ?? ""} inputMode="decimal" placeholder="0" />} />
                  </div>
                ))}
              </fieldset>
            )}
          </CardContent>
        </Card>
        <FormAlert error={error} />
        {saved && <p role="status" className="text-status-ok">Saved{values.provider ? " and tested" : ""}.</p>}
        <div className="flex gap-2">
          <Button type="submit" disabled={busy}>{busy ? (values.provider ? "Testing the provider…" : "Saving…") : "Save changes"}</Button>
          <Button type="button" variant="ghost" onClick={() => form.reset(toForm(settings))} disabled={busy || !form.formState.isDirty}>Undo changes</Button>
        </div>
      </form>
    </Form>
  );
}

/** Planner fit (US-191, DEC-23): planning stays local or uses a hosted provider; each has a recommended model and a
 * minimum. A warning when the current planner model's answers fail the format check too often. */
function PlannerFit({ health: h }: { health: PlannerHealth }) {
  if (!h.option) return null;
  const where = h.option === "local" ? "Planning runs on a local model: questions never leave your servers." : "Planning uses a hosted model: it sees the question, field names and definitions, never individual records.";
  const pct = (r: number) => `${Math.round(r * 100)}%`;
  return (
    <div className="grid gap-2" aria-label="Planner model fit">
      <p className="text-small text-ink-muted">
        {where}
        {h.recommended && ` Recommended planner model: ${h.recommended}`}
        {h.minimum && h.minimum !== h.recommended && ` (at least ${h.minimum})`}
        {h.recommended && "."}
      </p>
      {h.warn && h.failure_rate != null && (
        <Alert variant="warn">
          <p>
            The planner model {h.model} gave answers in the wrong format on {pct(h.failure_rate)} of {h.calls.toLocaleString()} questions in the last {h.days} days, so those questions fell back to the rules planner or went unanswered.
            {h.recommended && h.recommended !== h.model ? ` Choose ${h.recommended} instead.` : ""}
          </p>
        </Alert>
      )}
    </div>
  );
}

function Toggle({ control, name, label, description, disabled, onUserChange }: { control: ReturnType<typeof useForm<SettingsForm>>["control"]; name: "allow_aggregates" | "allow_samples" | "llm_review"; label: string; description: string; disabled?: boolean; onUserChange?: () => void }) {
  return (
    <FormField control={control} name={name} render={({ field }) => (
      <FormItem className="flex items-start justify-between gap-4">
        <div className="grid gap-0.5">
          <FormLabel>{label}</FormLabel>
          <FormDescription>{description}</FormDescription>
        </div>
        <FormControl><Switch checked={field.value} onCheckedChange={(v) => { field.onChange(v); onUserChange?.(); }} onBlur={field.onBlur} disabled={disabled} /></FormControl>
      </FormItem>
    )} />
  );
}

/** UTC days, oldest first, so days without calls show as zero rather than disappearing from the charts. */
function lastDays(n: number): string[] {
  const today = new Date();
  return Array.from({ length: n }, (_, i) => new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - (n - 1 - i))).toISOString().slice(0, 10));
}

const col = (name: string, label: string, kind: Column["kind"], format: string | null = null): Column =>
  ({ name, label, kind, ref: name, data_type: kind === "period" ? "date" : "number", format });

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const money = (v: number) => v.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });

function Usage() {
  const [params, setParams] = useSearchParams();
  const days = [7, 14, 30].includes(Number(params.get("days"))) ? Number(params.get("days")) : 14;
  const group = (params.get("by") === "task" || params.get("by") === "user" ? params.get("by") : "model") as "model" | "task" | "user";
  const set = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); n.set(k, v); return n; }, { replace: true });
  const daily = useQuery({ queryKey: ["llm-usage", "day", days], queryFn: () => api<LlmUsage>(`/api/v1/admin/usage?group_by=day&days=${days}`) });
  const by = useQuery({ queryKey: ["llm-usage", group, days], queryFn: () => api<LlmUsage>(`/api/v1/admin/usage?group_by=${group}&days=${days}`) });

  const series = useMemo(() => {
    const byDay = new Map((daily.data?.items ?? []).map((i) => [i.key, i]));
    return lastDays(days).map((d) => ({ day: d, item: byDay.get(d) }));
  }, [daily.data, days]);
  const totals = (daily.data?.items ?? []).reduce((t, i) => ({
    calls: t.calls + i.calls, tokens: t.tokens + i.prompt_tokens + i.completion_tokens, cost: t.cost + i.estimated_cost, blocked: t.blocked + i.blocked, failed: t.failed + i.failed,
  }), { calls: 0, tokens: 0, cost: 0, blocked: 0, failed: 0 });
  const label = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
  const day = col("day", "Day", "period");
  const charts: { title: string; columns: Column[]; rows: unknown[][]; spec: ChartSpec }[] = [
    { title: "Calls per day", columns: [day, col("calls", "Calls", "measure", "integer")], rows: series.map((s) => [label(s.day), s.item?.calls ?? 0]), spec: { type: "columns", x: "day", series: ["calls"] } },
    { title: "Tokens per day", columns: [day, col("tokens", "Tokens", "measure", "integer")], rows: series.map((s) => [label(s.day), s.item ? s.item.prompt_tokens + s.item.completion_tokens : 0]), spec: { type: "columns", x: "day", series: ["tokens"] } },
    { title: "Latency per day (ms)", columns: [day, col("p50", "Median", "measure", "integer"), col("p95", "95th percentile", "measure", "integer")], rows: series.map((s) => [label(s.day), s.item?.p50_ms != null ? Math.round(s.item.p50_ms) : null, s.item?.p95_ms != null ? Math.round(s.item.p95_ms) : null]), spec: { type: "columns", x: "day", series: ["p50", "p95"] } }, // bars: a day on its own still shows
    { title: "Calls blocked by policy per day", columns: [day, col("blocked", "Blocked", "measure", "integer")], rows: series.map((s) => [label(s.day), s.item?.blocked ?? 0]), spec: { type: "columns", x: "day", series: ["blocked"] } },
  ];
  const tableColumns = useMemo<DataColumn<LlmUsage["items"][number]>[]>(() => [
    { id: "key", header: group === "model" ? "Model" : group === "task" ? "Task" : "Person", alwaysVisible: true, cell: (i) => <span className="font-mono text-small">{i.key}</span> },
    { id: "calls", header: "Calls", numeric: true, format: "integer" },
    { id: "tokens", header: "Tokens", numeric: true, format: "integer", value: (i) => i.prompt_tokens + i.completion_tokens },
    { id: "p95_ms", header: "p95 latency (ms)", numeric: true, value: (i) => (i.p95_ms != null ? Math.round(i.p95_ms) : null) },
    { id: "failed", header: "Failed", numeric: true, format: "integer" },
    { id: "blocked", header: "Blocked", numeric: true, format: "integer" },
    { id: "estimated_cost", header: "Estimated cost", numeric: true, value: (i) => i.estimated_cost, cell: (i) => <span className="font-mono tabular-nums">{money(i.estimated_cost)}</span> },
  ], [group]);

  return (
    <section className="grid gap-4" aria-labelledby="usage-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h2 id="usage-title" className="text-heading">Usage</h2>
          <p className="text-small text-ink-muted">{daily.data?.cost_note ?? "Estimated from the configured rates, not from provider bills."} Days are in UTC.</p>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="usage-days">Period</Label>
          <NativeSelect id="usage-days" value={String(days)} onChange={(e) => set("days", e.target.value)}>
            <option value="7">Last 7 days</option><option value="14">Last 14 days</option><option value="30">Last 30 days</option>
          </NativeSelect>
        </div>
      </div>
      <FormAlert error={daily.error ? asApiError(daily.error) : by.error ? asApiError(by.error) : null} />
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[["Calls", totals.calls.toLocaleString()], ["Tokens", compact.format(totals.tokens)], ["Estimated cost", money(totals.cost)], ["Blocked by policy", totals.blocked.toLocaleString()]].map(([k, v]) => (
          <Card key={k} className="gap-1 p-4">
            <dt className="text-label uppercase text-ink-muted">{k}</dt>
            <dd className="font-mono text-figure-xl tabular-nums">{daily.isLoading ? "…" : v}</dd>
          </Card>
        ))}
      </dl>
      <div className="grid gap-4 lg:grid-cols-2">
        {charts.map((ch) => (
          <Card key={ch.title} className="gap-2 p-4">
            <h3 className="text-body-strong">{ch.title}</h3>
            {daily.isLoading ? <Skeleton className="h-[280px]" /> : <Chart spec={ch.spec} columns={ch.columns} rows={ch.rows} title={ch.title} />}
          </Card>
        ))}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h3 className="text-body-strong">By {group === "user" ? "person" : group}</h3>
        <div className="grid gap-1">
          <Label htmlFor="usage-by">Group by</Label>
          <NativeSelect id="usage-by" value={group} onChange={(e) => set("by", e.target.value)}>
            <option value="model">Model</option><option value="task">Task</option><option value="user">Person</option>
          </NativeSelect>
        </div>
      </div>
      <DataTable data={by.data?.items ?? []} columns={tableColumns} label={`Usage by ${group}`} getRowId={(i) => i.key} loading={by.isLoading} compact
        empty={{ title: "No calls in this period", body: "Calls appear here once Ask, the planner or the model assistant use a model." }} />
    </section>
  );
}
