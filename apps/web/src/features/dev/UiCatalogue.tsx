import { BarChart3, Bell, FileText, Inbox, LayoutDashboard, Search, Settings, Share2, Trash2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import type { Column, InsightFact } from "../../api/app";
import { AnswerFeedback } from "../ask/AnswerFeedback";
import { AskNext, DataNotes, DriverPanel, InsightList, WorkedOut } from "../ask/AnswerStory";
import { ApiError } from "../../api/client";
import { FormAlert } from "../../components/ui/alert";
import { Field, Form } from "../../components/ui/form";
import { NativeSelect } from "../../components/ui/native-select";
import { applyServerError } from "../../lib/api-errors";
import { Chart } from "../../components/Chart";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from "../../components/ui/command";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "../../components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "../../components/ui/dropdown-menu";
import { EmptyState } from "../../components/ui/empty-state";
import { FilterChip } from "../../components/ui/filter-chip";
import { Input } from "../../components/ui/input";
import { KpiCard } from "../../components/ui/kpi-card";
import { Label } from "../../components/ui/label";
import { MotionList } from "../../components/ui/motion";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Separator } from "../../components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "../../components/ui/sheet";
import { Skeleton } from "../../components/ui/skeleton";
import { StatusBadge } from "../../components/ui/status-badge";
import { Switch } from "../../components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Textarea } from "../../components/ui/textarea";
import { ThemeToggle } from "../../components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import tokens from "../../design/tokens.json";
import { ThemeScope, type ResolvedTheme } from "../../design/theme";
import logo from "../../design/tela-logo.png";

/** Sample figures for the catalogue only; real screens show what the API returns (COMP-001). */
const COLUMNS: Column[] = [
  { name: "programme", kind: "dimension", label: "Programme", ref: "p", data_type: "text", format: null },
  { name: "open", kind: "measure", label: "Open risks", ref: "o", data_type: "integer", format: "integer" },
  { name: "closed", kind: "measure", label: "Closed risks", ref: "c", data_type: "integer", format: "integer" },
];
const GRID_COLUMNS: Column[] = [COLUMNS[0]!, { name: "status", kind: "dimension", label: "Status", ref: "s", data_type: "text", format: null }, COLUMNS[1]!];
const GRID_ROWS = ["Core banking", "Data platform", "CRM"].flatMap((p, i) => [[p, "Green", 6 + i], [p, "Amber", 3 - i], [p, "Red", i]]);
const ROWS = [["Core banking", 14, 9], ["Data platform", 11, 12], ["Branch refresh", 7, 4], ["CRM", 5, 8], ["Payments", 3, 6]];

/**
 * Component catalogue (US-134), at /dev/ui in dev builds only. Every component is shown in the light and the dark
 * theme side by side, from the same tokens as the Claude Design system (US-132).
 */
export function UiCatalogue() {
  return (
    <div className="tl min-h-screen bg-surface">
      <header className="sticky top-0 z-10 flex h-topbar-height items-center gap-3 border-b border-line bg-surface-raised px-6">
        <img src={logo} alt="" className="size-7" />
        <h1 className="text-title">Tela components</h1>
        <span className="text-small text-ink-muted">US-134 · tokens v{tokens.version} · dev builds only</span>
        <span className="ml-auto">
          <ThemeToggle />
        </span>
      </header>
      <main className="flex flex-col gap-8 p-6">
        <Section title="Colour tokens"><Swatches /></Section>
        <Section title="Type"><TypeScale /></Section>
        <Section title="Buttons"><Buttons /></Section>
        <Section title="Badges and chips"><BadgesAndChips /></Section>
        <Section title="Form controls"><FormControls /></Section>
        <Section title="Forms (React Hook Form + Zod)"><FormDemo /></Section>
        <Section title="Tabs"><TabsDemo /></Section>
        <Section title="KPI and widget cards"><Cards /></Section>
        <Section title="Chart (ECharts, token theme)"><ChartDemo /></Section>
        <Section title="Visual catalogue wave 1 (US-187)"><CatalogueDemo /></Section>
        <Section title="Visual catalogue wave 2 (US-188)"><CatalogueWave2Demo /></Section>
        <Section title="Answer card v2 (US-186, US-183)"><AnswerStoryDemo /></Section>
        <Section title="Table"><TableDemo /></Section>
        <Section title="DataTable (TanStack Table + Virtual)"><DataTableDemo /></Section>
        <Section title="Overlays: dialog, sheet, popover, menu, tooltip, toast"><Overlays /></Section>
        <Section title="Command palette"><CommandDemo /></Section>
        <Section title="Empty, loading and list motion"><States /></Section>
      </main>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-3">
      <h2 className="text-heading">{title}</h2>
      <div className="grid gap-4 xl:grid-cols-2">
        {(["light", "dark"] as ResolvedTheme[]).map((theme) => (
          <ThemeScope key={theme} theme={theme} className="tl rounded-lg border border-line bg-surface p-5 text-ink">
            <p className="mb-4 text-label uppercase text-ink-muted">{theme}</p>
            {children}
          </ThemeScope>
        ))}
      </div>
    </section>
  );
}

function Swatches() {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-2">
      {tokens.color.tokens.map((t) => (
        <div key={t.name} className="flex flex-col gap-1">
          <span className="h-8 rounded-sm border border-line" style={{ background: `var(--tl-${t.name})` }} />
          <span className="truncate font-mono text-small text-ink-muted" title={t.usage}>{t.name}</span>
        </div>
      ))}
    </div>
  );
}

function TypeScale() {
  return (
    <div className="flex flex-col gap-2">
      {tokens.type.groups.flatMap((g) =>
        g.styles.map((s) => (
          <div key={s.name} className="flex items-baseline gap-4">
            <span className="w-24 shrink-0 font-mono text-small text-ink-muted">{s.name}</span>
            <span className={g.family === "mono" ? "font-mono" : undefined} style={{ fontSize: `var(--text-${s.name})`, lineHeight: `var(--text-${s.name}--line-height)`, fontWeight: s.fontWeight, letterSpacing: s.letterSpacing }}>
              {s.name === "label" ? s.sample.toUpperCase() : s.sample}
            </span>
          </div>
        )),
      )}
    </div>
  );
}

function Buttons() {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button>Publish</Button>
        <Button variant="secondary">Save draft</Button>
        <Button variant="ghost">Cancel</Button>
        <Button variant="danger"><Trash2 /> Delete</Button>
        <Button variant="link">View definition</Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm">Small</Button>
        <Button size="lg">Large</Button>
        <Button disabled>Disabled</Button>
        <Button variant="secondary" size="icon" aria-label="Share"><Share2 /></Button>
      </div>
    </div>
  );
}

function BadgesAndChips() {
  const [applied, setApplied] = useState(true);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Badge variant="certified">Certified</Badge>
        <Badge variant="personal">Personal</Badge>
        <Badge variant="stale">Stale · 3 h</Badge>
        <Badge>As of 09:15</Badge>
        <Badge variant="outline">Draft</Badge>
      </div>
      <div className="flex flex-wrap gap-2">
        <StatusBadge status="ok" />
        <StatusBadge status="warn" />
        <StatusBadge status="danger" />
      </div>
      <div className="flex flex-wrap gap-2">
        <FilterChip field="Programme" value={applied ? "Core banking" : undefined} applied={applied} onClick={() => setApplied(true)} onClear={() => setApplied(false)} />
        <FilterChip field="Quarter" value="Q3 2026" applied onClear={() => undefined} />
        <FilterChip field="Owner" />
      </div>
    </div>
  );
}

function FormControls() {
  const id = useId();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-cat-name`}>Report name</Label>
        <Input id={`${id}-cat-name`} placeholder="Delivery risks" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-cat-err`}>Connection</Label>
        <Input id={`${id}-cat-err`} aria-invalid defaultValue="tpg-prod" aria-describedby={`${id}-cat-err-msg`} />
        <p id={`${id}-cat-err-msg`} className="text-small text-status-danger">This connection cannot be reached.</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Audience</Label>
        <Select defaultValue="cio">
          <SelectTrigger aria-label="Audience"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="cio">CIO</SelectItem>
            <SelectItem value="pmo">PMO office</SelectItem>
            <SelectItem value="all">Everyone</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-cat-notes`}>Notes</Label>
        <Textarea id={`${id}-cat-notes`} placeholder="What this report is for" />
      </div>
      <Label className="font-normal"><Checkbox defaultChecked /> Include closed risks</Label>
      <Label className="font-normal"><Switch defaultChecked /> Email me when it changes</Label>
    </div>
  );
}

const demoForm = z.object({
  name: z.string().min(1, "Enter a name.").max(80, "Keep a name to 80 characters or fewer."),
  port: z.string().regex(/^\d+$/, "Enter the port as a number, for example 5432."),
  tls: z.enum(["verify-full", "require"]),
});

function FormDemo() {
  const form = useForm<z.infer<typeof demoForm>>({ resolver: zodResolver(demoForm), mode: "onTouched", defaultValues: { name: "", port: "54x", tls: "verify-full" } });
  const [error, setError] = useState<ApiError | null>(null);
  const submit = () => setError(applyServerError(form, new ApiError(409, "conflict", "A connection with this name already exists.", "req-demo")));
  return (
    <Form {...form}>
      <form className="grid max-w-md gap-3" onSubmit={form.handleSubmit(submit)} noValidate>
        <Field control={form.control} name="name" label="Name" description="Shown to Creators when they pick data." render={(f) => <Input {...f} />} />
        <Field control={form.control} name="port" label="Port" render={(f) => <Input {...f} inputMode="numeric" />} />
        <Field control={form.control} name="tls" label="TLS" render={(f) => (
          <NativeSelect {...f}><option value="verify-full">verify-full</option><option value="require">require</option></NativeSelect>
        )} />
        <FormAlert error={error} />
        <div><Button type="submit">Save</Button></div>
      </form>
    </Form>
  );
}

interface DemoProject { id: string; name: string; region: string; status: "ok" | "warn" | "danger"; budget: number | null }
const REGIONS = ["KSA", "GCC - UAE", "India", "Africa"];
const DEMO_ROWS: DemoProject[] = Array.from({ length: 1000 }, (_, i) => ({
  id: `p${i}`, name: `Project ${String(i + 1).padStart(4, "0")}`, region: REGIONS[i % 4]!,
  status: (["ok", "warn", "danger"] as const)[i % 7 === 0 ? 2 : i % 3 === 0 ? 1 : 0], budget: i % 11 === 0 ? null : (i * 7919) % 2_000_000,
}));
const DEMO_COLUMNS: DataColumn<DemoProject>[] = [
  { id: "name", header: "Project", alwaysVisible: true, filter: "text" },
  { id: "region", header: "Region", filter: "select" },
  { id: "status", header: "Health", filter: "select", cell: (p) => <StatusBadge status={p.status} /> },
  { id: "budget", header: "Budget", numeric: true, format: "currency" },
];

function DataTableDemo() {
  const [selected, setSelected] = useState(0);
  return (
    <DataTable data={DEMO_ROWS} columns={DEMO_COLUMNS} label="Projects" getRowId={(p) => p.id} rowLabel={(p) => p.name} columnFilters
      onSelectionChange={(rows) => setSelected(rows.length)} maxHeight={360} toolbar={<span className="text-small text-ink-muted">{selected} selected</span>} />
  );
}

function TabsDemo() {
  return (
    <Tabs defaultValue="overview">
      <TabsList>
        <TabsTrigger value="overview">Overview</TabsTrigger>
        <TabsTrigger value="risks">Risks</TabsTrigger>
        <TabsTrigger value="finance">Finance</TabsTrigger>
      </TabsList>
      <TabsContent value="overview" className="text-ink-muted">Overview content.</TabsContent>
      <TabsContent value="risks" className="text-ink-muted">Risks content.</TabsContent>
      <TabsContent value="finance" className="text-ink-muted">Finance content.</TabsContent>
    </Tabs>
  );
}

function Cards() {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <KpiCard label="Open risks" value="40" delta={{ text: "+4 vs last month", direction: "up", good: false }} badges={<Badge variant="certified">Certified</Badge>} onValueClick={() => toast("Definition: risks with status Open")} />
      <KpiCard label="Budget spent" value="$ 4,821,500" delta={{ text: "−2.1% vs plan", direction: "down", good: true }} badges={<Badge variant="stale">Stale</Badge>} footer="Data as of 21 Oct 2026, 09:15 IST" />
      <Card className="sm:col-span-2">
        <CardHeader>
          <div>
            <CardTitle>Open risks by programme</CardTitle>
            <CardDescription>Projects you can see · this quarter</CardDescription>
          </div>
          <Badge variant="personal">Personal</Badge>
        </CardHeader>
        <CardContent className="text-ink-muted">Widget body.</CardContent>
        <CardFooter>As of 09:15 IST</CardFooter>
      </Card>
    </div>
  );
}

// US-187 demo data, with the figures the API would work out for each visual (query/viz.py).
const money = (name: string, label: string): Column => ({ name, kind: "measure", label, ref: name, data_type: "decimal", format: "currency" });
const BUDGET_COLUMNS: Column[] = [COLUMNS[0]!, money("budget", "Approved budget"), money("forecast", "Forecast cost")];
const BUDGET_ROWS = [["Core banking", 1200000, 1380000], ["Data platform", 800000, 720000], ["CRM", 450000, 510000]];
const MONTH: Column = { name: "period", kind: "period", label: "Month", ref: "p", data_type: "date", format: null };
const XMR_VALUES = [12, 14, 11, 13, 15, 12, 13, 24];
const STAGES = [["Requests", 120], ["DOU issued", 84], ["DARB approved", 51], ["Approved for delivery", 30]] as const;
const PROJECT: Column = { name: "project", kind: "dimension", label: "Project", ref: "pr", data_type: "text", format: null };
const day = (name: string, label: string): Column => ({ name, kind: "period", label, ref: name, data_type: "date", format: null });

function CatalogueDemo() {
  const steps = [
    { label: "Approved budget (total)", kind: "start", value: 2450000, low: 0, high: 2450000 },
    { label: "Core banking", kind: "up", value: 180000, low: 2450000, high: 2630000 },
    { label: "Data platform", kind: "down", value: -80000, low: 2550000, high: 2630000 },
    { label: "CRM", kind: "up", value: 60000, low: 2550000, high: 2610000 },
    { label: "Forecast cost (total)", kind: "total", value: 2610000, low: 0, high: 2610000 },
  ];
  const pctCol: Column = { name: "pct", kind: "measure", label: "Baselining compliance", ref: "pct", data_type: "decimal", format: "percent" };
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Card className="p-3"><Chart spec={{ type: "dumbbell", x: "programme", series: ["budget", "forecast"], figures: { gaps: [180000, -80000, 60000], order: [0, 1, 2] } }} columns={BUDGET_COLUMNS} rows={BUDGET_ROWS} title="Budget and forecast by programme" /></Card>
      <Card className="p-3"><Chart spec={{ type: "waterfall", x: "programme", series: ["budget", "forecast"], figures: { mode: "bridge", steps, format: "currency" } }} columns={BUDGET_COLUMNS} rows={BUDGET_ROWS} title="Budget to forecast" /></Card>
      <Card className="p-3"><Chart spec={{ type: "bullet", x: "programme", series: ["pct"], figures: { rows: [{ label: "Core banking", value: 96, band: "good" }, { label: "Data platform", value: 84, band: "warning" }, { label: "CRM", value: 62, band: "critical" }], warning: 90, critical: 75, higher_is_worse: false, max: 100, target: 90 } }} columns={[COLUMNS[0]!, pctCol]} rows={[["Core banking", 96], ["Data platform", 84], ["CRM", 62]]} title="Baselining compliance against target" /></Card>
      <Card className="p-3"><Chart spec={{ type: "pareto", x: "programme", series: ["open"], figures: { labels: ROWS.map((r) => r[0]), values: ROWS.map((r) => r[1]), cumulative_pct: [35, 62.5, 80, 92.5, 100], vital_few: 3 } }} columns={COLUMNS} rows={ROWS} title="Open risks, Pareto" /></Card>
      <Card className="p-3"><Chart spec={{ type: "xmr", x: "period", series: ["open"], figures: { mean: 14.25, upper: 24.3, lower: 4.2, mr_bar: 3.78, beyond: [] } }} columns={[MONTH, COLUMNS[1]!]} rows={XMR_VALUES.map((v, i) => [`2026-${String(i + 1).padStart(2, "0")}`, v])} title="Open risks per month, control chart" /></Card>
      <Card className="p-3"><Chart spec={{ type: "funnel", series: ["n"], figures: { stages: STAGES.map(([label, value], i) => ({ label, value, pct_of_first: Math.round((value / 120) * 1000) / 10, drop_pct: i ? Math.round(((STAGES[i - 1]![1] - value) / STAGES[i - 1]![1]) * 1000) / 10 : null })) } }} columns={[COLUMNS[1]!]} rows={[[120]]} title="Request pipeline" /></Card>
      <Card className="p-3 sm:col-span-2"><Chart spec={{ type: "gantt", x: "project", series: ["n"], figures: { start: "start", end: "go_live", unplanned: 0 } }} columns={[PROJECT, day("start", "Planned start"), day("go_live", "Planned go-live"), COLUMNS[1]!]} rows={[["Core banking upgrade", "2026-01-12", "2026-09-30", 1], ["Data platform", "2026-03-01", "2026-12-15", 1], ["CRM rollout", "2026-05-04", "2026-08-31", 1]]} title="Delivery timeline" /></Card>
      <Card className="p-3 sm:col-span-2"><Chart spec={{ type: "small_multiples", x: "period", y: "programme", series: ["open"], figures: { panels: ["Core banking", "Data platform", "CRM"], max: 16, min: 0 } }} columns={[MONTH, COLUMNS[0]!, COLUMNS[1]!]} rows={["Core banking", "Data platform", "CRM"].flatMap((p, k) => [1, 2, 3, 4, 5, 6].map((m) => [`2026-0${m}`, p, 4 + k * 3 + (m % 3) * 2]))} title="Open risks per month by programme" /></Card>
    </div>
  );
}

// US-188 demo data, with the figures the API would work out for each visual (query/viz.py).
const COUNT: Column = { name: "n", kind: "measure", label: "Projects", ref: "n", data_type: "integer", format: "integer" };
const DAY: Column = { name: "day", kind: "period", label: "Day", ref: "day", data_type: "date", format: null };
const DAYS = Array.from({ length: 120 }, (_, i) => [new Date(Date.UTC(2026, 3, 1 + i)).toISOString().slice(0, 10), (i * 7) % 5]);
const SPREAD = ["Digital", "Infra", "Data"].map((label, k) => ({ label, n: 8, min: 2 + k * 6, q1: 6 + k * 6, median: 9 + k * 6, q3: 12 + k * 6, max: 20 + k * 6, low: 2 + k * 6, high: 20 + k * 6 }));
const SPREAD_ROWS = SPREAD.flatMap((g, k) => Array.from({ length: 8 }, (_, i) => [`${g.label} ${i + 1}`, g.label, 2 + k * 6 + ((i * 5) % 18)]));
const SPREAD_COLUMNS: Column[] = [PROJECT, { ...COLUMNS[0]!, name: "portfolio", label: "Portfolio" }, { name: "slip", kind: "measure", label: "Slip (days)", ref: "slip", data_type: "integer", format: "integer" }];
const bell = (shift: number) => Array.from({ length: 48 }, (_, i) => Math.round(Math.exp(-(((i - 14 - shift * 6) / 7) ** 2)) * 1000) / 1000);

function CatalogueWave2Demo() {
  const flows = { levels: ["Kind", "Gate", "Outcome"], nodes: [{ label: "New", level: 0, value: 20 }, { label: "Change", level: 0, value: 5 }, { label: "DOU", level: 1, value: 17 }, { label: "DARB", level: 1, value: 8 }, { label: "Converted", level: 2, value: 18 }, { label: "Rejected", level: 2, value: 4 }, { label: "On hold", level: 2, value: 3 }],
    links: [{ source: 0, target: 2, value: 14 }, { source: 0, target: 3, value: 6 }, { source: 1, target: 2, value: 3 }, { source: 1, target: 3, value: 2 }, { source: 2, target: 4, value: 10 }, { source: 2, target: 5, value: 4 }, { source: 2, target: 6, value: 3 }, { source: 3, target: 4, value: 8 }] };
  const upset = { sets: [{ label: "No budget code", size: 35 }, { label: "No PM", size: 22 }, { label: "Not baselined", size: 10 }], intersections: [{ members: [false, false, false], value: 50, degree: 0 }, { members: [true, false, false], value: 20, degree: 1 }, { members: [true, true, false], value: 12, degree: 2 }, { members: [false, true, true], value: 7, degree: 2 }, { members: [true, true, true], value: 3, degree: 3 }], more: 0, total: 92 };
  const curve = (label: string, k: number) => ({ label, n: 10, events: 8 - k, median: 60 + k * 30, points: [0, 30, 60, 90, 120, 150].map((t, i) => ({ t, s: Math.max(0.1, 1 - i * (0.18 - k * 0.04)), at_risk: 10 - i, events: i ? 1 : 0, censored: i === 5 ? 2 : 0 })) });
  const bump = { periods: ["2026-05", "2026-06", "2026-07", "2026-08"], items: [{ label: "KSA", ranks: [1, 2, 3, 3], values: [9, 7, 5, 4] }, { label: "UAE", ranks: [3, 1, 1, 1], values: [5, 9, 10, 11] }, { label: "Egypt", ranks: [2, 3, 2, 2], values: [7, 6, 7, 8] }] };
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Card className="p-3 sm:col-span-2"><Chart spec={{ type: "calendar", x: "day", series: ["n"], figures: { start: DAYS[0]![0], end: DAYS[DAYS.length - 1]![0], years: [2026], edges: [1, 2, 3], max: 4 } }} columns={[DAY, COUNT]} rows={DAYS} title="Status reports filed per day" /></Card>
      <Card className="p-3"><Chart spec={{ type: "sankey", series: ["n"], figures: flows }} columns={[COUNT]} rows={[[25]]} title="Requests through their gates" /></Card>
      <Card className="p-3"><Chart spec={{ type: "upset", series: ["n"], figures: upset }} columns={[COUNT]} rows={[[92]]} title="Compliance gaps that occur together" /></Card>
      <Card className="p-3"><Chart spec={{ type: "beeswarm", x: "project", y: "portfolio", series: ["slip"], figures: { groups: SPREAD, min: 0, max: 34 } }} columns={SPREAD_COLUMNS} rows={SPREAD_ROWS} title="Slip per project" /></Card>
      <Card className="p-3"><Chart spec={{ type: "ridgeline", x: "project", y: "portfolio", series: ["slip"], figures: { groups: SPREAD.map((g, k) => ({ ...g, density: bell(k) })), min: 0, max: 34, points: 48 } }} columns={SPREAD_COLUMNS} rows={SPREAD_ROWS} title="Slip by portfolio" /></Card>
      <Card className="p-3"><Chart spec={{ type: "survival", x: "project", y: "portfolio", series: [], figures: { event: "Go-live", as_of: "2026-10-29", left_out: 1, max_t: 150, curves: [curve("Digital", 0), curve("Infra", 1)] } }} columns={[PROJECT]} rows={[["A"]]} title="Time from start to go-live" /></Card>
      <Card className="p-3"><Chart spec={{ type: "bump", x: "period", y: "region", series: ["n"], figures: bump }} columns={[MONTH, COLUMNS[0]!, COUNT]} rows={[["2026-05", "KSA", 9]]} title="Overdue go-lives, rank by month" highlight={["UAE"]} /></Card>
      <Card className="p-3"><Chart spec={{ type: "treemap", x: "programme", series: ["budget"], figures: { total: 2450000, nodes: [{ label: "Core banking", value: 1200000, share: 49, children: [] }, { label: "Data platform", value: 800000, share: 32.7, children: [] }, { label: "CRM", value: 450000, share: 18.4, children: [] }] } }} columns={BUDGET_COLUMNS} rows={BUDGET_ROWS} title="Approved budget by programme" /></Card>
      <Card className="p-3"><Chart spec={{ type: "waffle", series: ["open", "closed"], figures: { total: 111, parts: [{ label: "With a budget code", value: 31, pct: 27.9, cells: 28 }, { label: "Rest of open projects", value: 80, pct: 72.1, cells: 72 }] } }} columns={COLUMNS.slice(1)} rows={[[31, 111]]} title="Budget code compliance" /></Card>
    </div>
  );
}

function ChartDemo() {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Card className="p-3"><Chart spec={{ type: "columns", x: "programme", series: ["open", "closed"] }} columns={COLUMNS} rows={ROWS} title="Open and closed risks" /></Card>
      <Card className="p-3"><Chart spec={{ type: "donut", x: "programme", series: ["open"] }} columns={COLUMNS} rows={ROWS} title="Open risks share" /></Card>
      <Card className="p-3"><Chart spec={{ type: "heat_grid", x: "programme", y: "status", series: ["open"], ramp: "rag", tones: { Green: "good", Amber: "warning", Red: "critical" } }} columns={GRID_COLUMNS} rows={GRID_ROWS} title="Open risks by status" /></Card>
      <Card className="p-3"><Chart spec={{ type: "heat_grid", x: "programme", y: "priority", series: ["open"], ramp: "sequential" }} columns={GRID_COLUMNS.map((c) => (c.name === "status" ? { ...c, name: "priority", label: "Priority" } : c))} rows={GRID_ROWS.map(([p, s, n]) => [p, s === "Green" ? "P1" : s === "Amber" ? "P2" : "P3", n])} title="Open risks by priority" /></Card>
      <Card className="p-3"><Chart spec={{ type: "meter", series: ["open", "closed"], meter: { part: 31, whole: 111, percent: 27.9, note: "Open projects with a budget code" } }} columns={COLUMNS.slice(1)} rows={[[31, 111]]} title="Budget code compliance" /></Card>
      <Card className="p-3"><Chart spec={{ type: "columns", x: "programme", series: ["open"], ramp: "ordinal" }} columns={COLUMNS} rows={ROWS} title="Open risks by age band" /></Card>
    </div>
  );
}

function TableDemo() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Programme</TableHead>
          <TableHead>Health</TableHead>
          <TableHead className="text-right">Open risks</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ROWS.slice(0, 3).map(([p, o], i) => (
          <TableRow key={String(p)} data-state={i === 1 ? "selected" : undefined}>
            <TableCell>{p}</TableCell>
            <TableCell><StatusBadge status={i === 0 ? "danger" : i === 1 ? "warn" : "ok"} /></TableCell>
            <TableCell numeric>{o}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Overlays() {
  return (
    <div className="flex flex-wrap gap-2">
      <Dialog>
        <DialogTrigger asChild><Button variant="secondary">Dialog</Button></DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Publish dashboard</DialogTitle>
            <DialogDescription>Viewers in CIO will see version 4.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
            <DialogClose asChild><Button onClick={() => toast.success("Published version 4", { action: { label: "Undo", onClick: () => undefined } })}>Publish</Button></DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Sheet>
        <SheetTrigger asChild><Button variant="secondary">Sheet</Button></SheetTrigger>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Open risks</SheetTitle>
            <SheetDescription>Counts risks with status Open in projects you can see.</SheetDescription>
          </SheetHeader>
          <div className="px-4 text-ink-muted">Drill-to-records goes here.</div>
        </SheetContent>
      </Sheet>
      <Popover>
        <PopoverTrigger asChild><Button variant="secondary">Popover</Button></PopoverTrigger>
        <PopoverContent>
          <p className="text-heading">Open risks</p>
          <p className="mt-1 text-body text-ink-muted">Risks with status Open. Certified by PMO Office.</p>
        </PopoverContent>
      </Popover>
      <DropdownMenu>
        <DropdownMenuTrigger asChild><Button variant="secondary">Menu</Button></DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>Widget</DropdownMenuLabel>
          <DropdownMenuItem><Share2 /> Share</DropdownMenuItem>
          <DropdownMenuItem><Settings /> Settings</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="danger"><Trash2 /> Remove</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild><Button variant="secondary" size="icon" aria-label="Notifications"><Bell /></Button></TooltipTrigger>
        <TooltipContent>Notifications</TooltipContent>
      </Tooltip>
      <Button variant="secondary" onClick={() => toast("Widget removed", { action: { label: "Undo", onClick: () => undefined } })}>Toast</Button>
      <Button variant="secondary" onClick={() => toast.error("The query timed out", { description: "Request id: 7f3a…" })}>Error toast</Button>
    </div>
  );
}

const STORY_FACTS: InsightFact[] = [
  { id: "f1", type: "concentration", priority: 0.6, text: "The top 3 of 5 account for 80% of open risks (40); 3 of 5 make up 80%.",
    numbers: { items: 5, top_n: 3, top_share_pct: 80, total: 40 }, members: ["Core banking", "Data platform", "Branch refresh"] },
  { id: "f2", type: "driver", priority: 0.5, text: "Core banking drove most of the change in this period vs Q2: +5 of the +6 total (83.3%).",
    numbers: { total_delta: 6, contributions: [{ member: "Core banking", delta: 5, share_pct: 83.3 }, { member: "CRM", delta: 2, share_pct: 33.3 }, { member: "Payments", delta: -1, share_pct: -16.7 }], period: "this period", previous_period: "Q2" },
    members: ["Core banking", "CRM", "Payments"], dimension: { ref: "dimension:programme", label: "Programme" } },
];

function AnswerStoryDemo() {
  const [focus, setFocus] = useState<string | null>("f1");
  const highlight = STORY_FACTS.find((f) => f.id === focus)?.members ?? [];
  const noop = () => undefined;
  return (
    <div className="grid gap-3">
      <h3 className="text-heading">Core banking has the most open risks (14 of 40).</h3>
      <InsightList cited={STORY_FACTS.map((f) => ({ text: f.text, facts: [f.id], source: "template" }))} facts={STORY_FACTS} format="integer" focus={focus} onFocus={setFocus} />
      <Chart spec={{ type: "ranked_bar", x: "programme", series: ["open"] }} columns={COLUMNS} rows={ROWS} title="Open risks by programme" highlight={highlight} />
      <DriverPanel driver={{ ...STORY_FACTS[1]!, dimension: { ref: "dimension:portfolio", label: "Portfolio" } }} format="integer" onApply={noop} />
      <DataNotes onApply={noop} notes={[{ text: "3 of 40 (7.5%) has no programme (Not set).", fact: "f3" },
        { text: "Your region filter leaves one region (KSA), so the breakdown has nothing to compare.", fact: "s1", kind: "single_row",
          action: { label: "Remove the region filter", ops: [] } }]} />
      <AskNext next={[{ kind: "driver", label: "Break down by portfolio", ops: [] }, { kind: "compare", label: "Compare with the previous period", ops: [] }, { kind: "drill", label: "Drill into Core banking", ops: [] }]} onApply={noop} />
      <WorkedOut label="How Tela answered" steps={["Understood the question", "Used certified measure “Open risks”", "Applied your scope: risks: Gulf regions"]} interpreted={["this quarter"]} canShowQuery={false} />
      <AnswerFeedback turnId="demo" />
    </div>
  );
}

function CommandDemo() {
  const [open, setOpen] = useState(false);
  const items = (
    <>
      <CommandInput placeholder="Search pages, reports and actions" />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        <CommandGroup heading="Go to">
          <CommandItem><LayoutDashboard /> Portfolio health <CommandShortcut>G D</CommandShortcut></CommandItem>
          <CommandItem><FileText /> Delivery risks report</CommandItem>
          <CommandItem><BarChart3 /> Ask a question <CommandShortcut>/</CommandShortcut></CommandItem>
        </CommandGroup>
      </CommandList>
    </>
  );
  return (
    <div className="flex flex-col gap-3">
      <Command className="border border-line">{items}</Command>
      <Button variant="secondary" className="w-fit" onClick={() => setOpen(true)}><Search /> Open palette</Button>
      <CommandDialog open={open} onOpenChange={setOpen}>{items}</CommandDialog>
    </div>
  );
}

function States() {
  const [items, setItems] = useState(["Risk 112 is overdue", "Budget report published"]);
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <EmptyState icon={Inbox} title="No dashboards yet" action={<Button size="sm">New dashboard</Button>}>
        Dashboards published to your roles appear here.
      </EmptyState>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Separator className="my-2" />
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={() => setItems((l) => [`Notification ${l.length + 1}`, ...l])}>Add</Button>
          <Button size="sm" variant="ghost" onClick={() => setItems((l) => l.slice(1))}>Remove</Button>
        </div>
        <MotionList className="flex flex-col gap-1" items={items.map((t) => ({ key: t, content: <span className="block rounded-sm bg-surface-sunken px-2 py-1">{t}</span> }))} />
      </div>
    </div>
  );
}
