import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import { logoUrl, type Branding, type Palette } from "../../../app/branding";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { KpiCard } from "../../../components/ui/kpi-card";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { StatusBadge } from "../../../components/ui/status-badge";
import { tokenColor } from "../../../design/echartsTheme";
import { ThemeScope, type ResolvedTheme } from "../../../design/theme";
import { asApiError } from "../../../lib/api-errors";
import { extractColours, logoPixels } from "./extract";

interface Candidates {
  candidates: Palette[];
  dropped: number;
  greys_only: boolean;
}

const CHART = ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6", "chart-7", "chart-8"];
const SEQ = ["seq-1", "seq-2", "seq-3", "seq-4", "seq-5"];
const DIV = ["div-neg-2", "div-neg-1", "div-mid", "div-pos-1", "div-pos-2"];
const KEYS = ["accent", "accent-strong", "accent-soft", "on-accent", "focus", ...CHART, ...SEQ, ...DIV];
const DEFAULT_ID = "default";

/** The Tela colours as a palette, so they preview like any other choice. */
const TELA: Palette = {
  id: DEFAULT_ID,
  name: "Tela",
  spec: { accent: tokenColor("accent", "light"), seeds: [], variant: "natural", source: "logo" },
  tokens: {
    light: Object.fromEntries(KEYS.map((k) => [k, tokenColor(k, "light")])),
    dark: Object.fromEntries(KEYS.map((k) => [k, tokenColor(k, "dark")])),
  },
  brand: [],
};

/** CSS variable overrides that make everything inside look as it will with this palette. */
function paletteStyle(palette: Palette, theme: ResolvedTheme): CSSProperties {
  return Object.fromEntries(Object.entries(palette.tokens[theme]).map(([k, v]) => [`--tl-${k}`, v])) as CSSProperties;
}

/**
 * Colours (ENH-006, US-150): palettes made from the logo's main colours, each checked on the server for readable
 * text and colour-blind-safe charts in both themes. The Admin previews one, can try their own accent colour, and saves
 * it — or keeps the Tela colours.
 */
export function PaletteCard({ branding, onSaved }: { branding: Branding; onSaved: (b: Branding) => void }) {
  const logo = logoUrl(branding, "light");
  const colours = useQuery({
    queryKey: ["logo-colours", logo],
    queryFn: async () => extractColours(await logoPixels(logo)),
    staleTime: Infinity,
    retry: false,
  });
  const seeds = useMemo(() => colours.data?.map((c) => c.hex) ?? [], [colours.data]);
  const offered = useQuery({
    queryKey: ["branding-palettes", seeds],
    queryFn: () => api<Candidates>("/api/v1/admin/branding/palettes", { method: "POST", body: { seeds } }),
    enabled: colours.isSuccess,
    staleTime: Infinity,
  });
  const [custom, setCustom] = useState<Palette | null>(null);
  const [accent, setAccent] = useState(branding.palette?.spec.accent ?? TELA.spec.accent);
  const current = branding.palette ?? null;
  const currentId = current?.id ?? DEFAULT_ID;
  const [selected, setSelected] = useState(currentId);
  useEffect(() => setSelected(currentId), [currentId]);

  const options = useMemo(() => {
    const list = [TELA, ...(offered.data?.candidates ?? [])];
    for (const p of [current, custom]) if (p && !list.some((o) => o.id === p.id)) list.push(p);
    return list;
  }, [offered.data, current, custom]);
  const chosen = options.find((o) => o.id === selected) ?? TELA;

  const tryAccent = useMutation({
    mutationFn: () => api<Candidates & { failed?: Palette | null }>("/api/v1/admin/branding/palettes", { method: "POST", body: { accent, seeds } }),
    onSuccess: (out) => {
      const p = out.candidates[0];
      if (!p) return;
      setCustom(p);
      setSelected(p.id);
    },
  });
  const save = useMutation({
    mutationFn: () => api<Branding>("/api/v1/admin/branding/palette", {
      method: "PUT", body: { spec: chosen.id === DEFAULT_ID ? null : chosen.spec, expected_version: branding.version },
    }),
    onSuccess: (b) => {
      onSaved(b);
      toast.success(b.palette ? `Palette “${b.palette.name}” saved` : "Back to the Tela colours");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Colours</CardTitle>
        <CardDescription>
          Palettes made from your logo’s colours. Each one is checked for readable text and for charts colour-blind
          readers can tell apart, in light and dark themes. Red and green are kept for On track and Off track.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <div className="grid content-start gap-4">
          {colours.data && colours.data.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-small text-ink-muted" aria-label="Colours found in the logo">
              <span>From the logo:</span>
              {colours.data.map((c) => <Swatch key={c.hex} hex={c.hex} />)}
            </div>
          )}
          {colours.isError && <p className="text-small text-ink-muted">The logo’s colours couldn’t be read. Keep the Tela colours or pick an accent below.</p>}
          {offered.data?.greys_only && <p className="text-small text-ink-muted">The logo has only greys, black or white, so no palettes can be made from it. Keep the Tela colours or pick an accent below.</p>}
          {(colours.isPending || offered.isFetching) && <p className="text-small text-ink-muted" role="status">Making palettes from the logo…</p>}
          {offered.error && <FormAlert error={asApiError(offered.error)} />}
          <RadioGroup value={selected} onValueChange={setSelected} aria-label="Palette">
            {options.map((p) => (
              <label key={p.id} className="flex cursor-pointer items-start gap-3 rounded-md border border-line p-3 has-[[data-state=checked]]:border-accent has-[[data-state=checked]]:bg-accent-soft">
                <RadioGroupItem value={p.id} className="mt-0.5" />
                <span className="grid min-w-0 gap-1.5">
                  <span className="flex flex-wrap items-center gap-2 font-medium">
                    {p.name}
                    {p.id === DEFAULT_ID && <span className="text-small font-normal text-ink-muted">default</span>}
                    {p.spec.source === "custom" && p.id !== DEFAULT_ID && <span className="text-small font-normal text-ink-muted">your colour</span>}
                    {p.id === currentId && <Badge variant="certified">Current</Badge>}
                  </span>
                  <span className="flex flex-wrap items-center gap-1" aria-hidden>
                    {(["accent", "accent-strong", "accent-soft"] as const).map((k) => <Swatch key={k} hex={p.tokens.light[k]!} />)}
                    <span className="mx-1 h-4 w-px bg-line" />
                    {CHART.map((k) => <Swatch key={k} hex={p.tokens.light[k]!} small />)}
                  </span>
                  <ChecksLine palette={p} />
                </span>
              </label>
            ))}
          </RadioGroup>
          <div className="grid gap-2">
            <span className="font-medium" id="custom-accent">Or start from your own accent colour</span>
            <div className="flex flex-wrap items-center gap-2">
              <input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} aria-labelledby="custom-accent"
                className="h-8 w-12 cursor-pointer rounded-sm border border-line-strong bg-surface-raised p-0.5" />
              <span className="font-mono text-small text-ink-muted">{accent}</span>
              <Button variant="secondary" size="sm" disabled={tryAccent.isPending} onClick={() => tryAccent.mutate()}>
                {tryAccent.isPending ? "Checking…" : "Try this colour"}
              </Button>
            </div>
            {tryAccent.error && <FormAlert error={asApiError(tryAccent.error)} />}
            {tryAccent.data && !tryAccent.data.candidates.length && (
              <p className="text-small text-status-danger">No palette that passes every check can be made from {accent}. Try a nearby colour.</p>
            )}
          </div>
          {save.error && <FormAlert error={asApiError(save.error)} />}
          <div className="flex flex-wrap gap-2">
            <Button disabled={save.isPending || selected === currentId} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : chosen.id === DEFAULT_ID ? "Use the Tela colours" : `Use ${chosen.name}`}
            </Button>
            {selected !== currentId && <Button variant="ghost" onClick={() => setSelected(currentId)}>Discard</Button>}
          </div>
        </div>
        <PalettePreview palette={chosen} />
      </CardContent>
    </Card>
  );
}

function Swatch({ hex, small }: { hex: string; small?: boolean }) {
  return <span title={hex} className={small ? "size-3.5 rounded-sm border border-line" : "size-5 rounded-sm border border-line"} style={{ backgroundColor: hex }} />;
}

function ChecksLine({ palette }: { palette: Palette }) {
  if (!palette.checks) {
    return <span className="text-small text-ink-muted">{palette.id === DEFAULT_ID ? "Checked in the Tela design system" : "Checked when it was saved"}</span>;
  }
  const passed = palette.checks.filter((c) => c.ok).length;
  return <span className="text-small text-ink-muted">{passed} of {palette.checks.length} checks pass</span>;
}

/** Buttons, badges, a KPI card and a chart in light and dark with the palette, plus its checks and notes. */
function PalettePreview({ palette }: { palette: Palette }) {
  return (
    <div className="grid content-start gap-3" aria-label="Palette preview">
      <span className="text-label uppercase tracking-wider text-ink-muted">Preview · {palette.name}</span>
      <div className="grid gap-3 xl:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <ThemeScope key={theme} theme={theme} style={paletteStyle(palette, theme)} className="grid content-start gap-3 rounded-md border border-line bg-surface p-3 text-ink">
            <span className="text-small text-ink-muted">{theme === "light" ? "Light theme" : "Dark theme"}</span>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" tabIndex={-1}>Primary</Button>
              <Button size="sm" variant="secondary" tabIndex={-1}>Secondary</Button>
              <span className="text-small text-link underline">A link</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="certified">Certified</Badge>
              <StatusBadge status="ok" />
              <StatusBadge status="danger" />
            </div>
            <span className="w-fit rounded-md bg-accent-soft px-2 py-1 text-small font-medium text-accent">Active menu item</span>
            <KpiCard label="Open risks" value="42" delta={{ text: "+4 vs last month", direction: "up", good: false }} />
            <MiniChart tokens={palette.tokens[theme]!} />
          </ThemeScope>
        ))}
      </div>
      {palette.notes && palette.notes.length > 0 && (
        <ul className="grid gap-1 text-small text-ink-muted">
          {palette.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      {palette.checks && <ChecksTable palette={palette} />}
    </div>
  );
}

function MiniChart({ tokens }: { tokens: Record<string, string> }) {
  const heights = [62, 48, 70, 35, 55, 42, 66, 30];
  return (
    <div className="grid gap-2 rounded-lg border border-line bg-surface-raised p-3">
      <span className="text-small font-medium">Spend by programme</span>
      <svg viewBox="0 0 168 76" role="img" aria-label="Sample chart in the palette's eight series colours" className="h-20 w-full">
        {heights.map((h, i) => <rect key={i} x={4 + i * 20} y={74 - h} width={14} height={h} rx={2} style={{ fill: tokens[CHART[i]!] }} />)}
        <line x1="0" x2="168" y1="74.5" y2="74.5" style={{ stroke: "var(--tl-chart-axis)" }} />
      </svg>
      <span className="flex h-3 overflow-hidden rounded-sm" aria-hidden>
        {SEQ.map((k) => <span key={k} className="flex-1" style={{ backgroundColor: tokens[k] }} />)}
      </span>
      <span className="flex h-3 overflow-hidden rounded-sm" aria-hidden>
        {DIV.map((k) => <span key={k} className="flex-1" style={{ backgroundColor: tokens[k] }} />)}
      </span>
    </div>
  );
}

function ChecksTable({ palette }: { palette: Palette }) {
  const checks = palette.checks ?? [];
  const failed = checks.filter((c) => !c.ok).length;
  return (
    <details className="text-small" open={failed > 0}>
      <summary className="cursor-pointer font-medium">{failed ? `${failed} checks fail` : `All ${checks.length} checks pass`}</summary>
      <table className="mt-2 w-full">
        <thead>
          <tr className="text-left text-ink-muted"><th className="py-1 font-medium">Check</th><th className="font-medium">Theme</th><th className="text-right font-medium">Measured</th><th className="text-right font-medium">Needs</th><th><span className="sr-only">Result</span></th></tr>
        </thead>
        <tbody>
          {checks.map((c) => (
            <tr key={c.id} className="border-t border-line">
              <td className="py-1 pr-2">{c.label}</td>
              <td className="pr-2">{c.theme === "light" ? "Light" : "Dark"}</td>
              <td className="text-right font-mono tabular-nums">{fmt(c.kind, c.value)}</td>
              <td className="text-right font-mono tabular-nums">{c.kind === "rag" ? "none" : `≥ ${fmt(c.kind, c.min)}`}</td>
              <td className="pl-2">{c.ok ? <Check aria-label="passes" className="size-4 text-status-ok" /> : <X aria-label="fails" className="size-4 text-status-danger" />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function fmt(kind: string, v: number): string {
  if (kind === "contrast") return `${v.toFixed(1)}:1`;
  if (kind === "delta_e") return `ΔE ${v.toFixed(1)}`;
  return String(v);
}
