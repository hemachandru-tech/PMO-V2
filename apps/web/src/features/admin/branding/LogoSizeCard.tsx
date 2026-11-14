import { useEffect, useState } from "react";
import { toast } from "sonner";

import { api, type ApiError } from "../../../api/client";
import { DEFAULT_LOGO_SIZES, logoUrl, type Branding, type LogoSizes } from "../../../app/branding";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { Input } from "../../../components/ui/input";
import { ThemeScope } from "../../../design/theme";
import { asApiError } from "../../../lib/api-errors";

const PLACES: { place: keyof LogoSizes; label: string; note: string }[] = [
  { place: "sidebar", label: "Sidebar", note: "Next to the name at the top of the sidebar. The collapsed sidebar keeps a small square." },
  { place: "sign_in", label: "Sign-in screens", note: "On the start-up, no-access and sign-in error screens." },
  { place: "export", label: "PDF exports", note: "In the header of every exported page." },
];
const FALLBACK_LIMITS: Record<keyof LogoSizes, [number, number]> = { sidebar: [20, 56], sign_in: [32, 128], export: [16, 64] };

/**
 * Logo size (ENH-006, US-152): how tall the logo is drawn in the sidebar, on the sign-in screens and in PDF exports.
 * Wide logos keep their shape: the height is set and the width follows, up to what the place has room for.
 */
export function LogoSizeCard({ branding, onSaved }: { branding: Branding; onSaved: (b: Branding) => void }) {
  const stored = branding.logo_sizes ?? DEFAULT_LOGO_SIZES;
  const defaults = branding.defaults.logo_sizes ?? DEFAULT_LOGO_SIZES;
  const limits = branding.defaults.logo_size_limits ?? FALLBACK_LIMITS;
  const [sizes, setSizes] = useState<LogoSizes>(stored);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setSizes(stored), [branding.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = PLACES.some(({ place }) => sizes[place] !== stored[place]);
  const atDefaults = PLACES.every(({ place }) => sizes[place] === defaults[place]);
  const valid = PLACES.every(({ place }) => Number.isInteger(sizes[place]) && sizes[place] >= limits[place][0] && sizes[place] <= limits[place][1]);

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      const out = await api<Branding>("/api/v1/admin/branding/logo-size", { method: "PUT", body: { ...sizes, expected_version: branding.version } });
      onSaved(out);
      toast.success("Logo size saved — everyone sees it now");
    } catch (e) {
      setError(asApiError(e, "The logo size could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Logo size</CardTitle>
        <CardDescription>How tall the logo is shown in each place. Its width follows the logo’s shape.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <form className="grid content-start gap-4" aria-label="Logo size" noValidate onSubmit={(e) => { e.preventDefault(); if (valid && dirty) void save(); }}>
          {PLACES.map(({ place, label, note }) => {
            const [lo, hi] = limits[place];
            const id = `logo-size-${place}`;
            return (
              <div key={place} className="grid gap-1" role="group" aria-labelledby={`${id}-label`}>
                <span id={`${id}-label`} className="font-medium">{label}</span>
                <div className="flex items-center gap-3">
                  <input type="range" min={lo} max={hi} step={1} value={sizes[place]} aria-label={`${label} logo height`}
                    className="h-2 min-w-0 flex-1 cursor-pointer accent-accent"
                    onChange={(e) => setSizes({ ...sizes, [place]: Number(e.target.value) })} />
                  <div className="flex items-center gap-1">
                    <Input id={id} type="number" inputMode="numeric" min={lo} max={hi} value={Number.isNaN(sizes[place]) ? "" : sizes[place]}
                      aria-label={`${label} logo height in pixels`} aria-describedby={`${id}-note`} className="w-20"
                      onChange={(e) => setSizes({ ...sizes, [place]: e.target.value === "" ? Number.NaN : Math.round(Number(e.target.value)) })} />
                    <span className="text-small text-ink-muted">px</span>
                  </div>
                </div>
                <span id={`${id}-note`} className="text-small text-ink-muted">{note} {lo} to {hi} px, default {defaults[place]}.</span>
              </div>
            );
          })}
          {!valid && <p role="alert" className="text-small text-status-danger">Each height must be a whole number within its range.</p>}
          {error && <FormAlert error={error} />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !dirty || !valid}>{busy ? "Saving…" : "Save sizes"}</Button>
            {dirty && <Button type="button" variant="ghost" onClick={() => setSizes(stored)}>Discard</Button>}
            {!atDefaults && <Button type="button" variant="ghost" onClick={() => setSizes(defaults)}>Restore defaults</Button>}
          </div>
        </form>
        <SizePreview branding={branding} sizes={sizes} />
      </CardContent>
    </Card>
  );
}

function px(n: number, [lo, hi]: [number, number]): number {
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : lo;
}

/** The three places at the chosen sizes, before saving: sidebar in both themes, a sign-in screen and a PDF header. */
function SizePreview({ branding, sizes }: { branding: Branding; sizes: LogoSizes }) {
  const limits = branding.defaults.logo_size_limits ?? FALLBACK_LIMITS;
  const h = (place: keyof LogoSizes) => ({ height: px(sizes[place], limits[place]), width: "auto" });
  return (
    <div className="grid content-start gap-3" aria-label="Logo size preview">
      <span className="text-label uppercase tracking-wider text-ink-muted">Preview</span>
      <div className="grid gap-2 sm:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <ThemeScope key={theme} theme={theme} className="rounded-md border border-line bg-surface-sunken p-3">
            <div className="flex min-w-0 items-center gap-2 text-ink">
              <img src={logoUrl(branding, theme)} alt="" data-preview="sidebar" className="shrink-0 object-contain" style={{ ...h("sidebar"), maxWidth: 160 }} />
              <span className="break-words text-[18px] font-semibold leading-tight tracking-tight">{branding.app_name}</span>
            </div>
            <span className="mt-1 block text-small text-ink-muted">Sidebar · {theme === "light" ? "light" : "dark"} theme</span>
          </ThemeScope>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <ThemeScope theme="light" className="grid justify-items-center gap-2 rounded-md border border-line bg-surface p-4 text-center">
          <img src={logoUrl(branding, "light")} alt="" data-preview="sign_in" className="object-contain" style={{ ...h("sign_in"), maxWidth: "100%" }} />
          <span className="text-heading">{branding.app_name}</span>
          <span className="text-small text-ink-muted">Sign-in screen</span>
        </ThemeScope>
        <ThemeScope theme="light" className="grid content-start gap-1 rounded-md border border-line bg-surface-raised p-3">
          <div className="flex items-center gap-2 text-small text-ink-muted">
            <img src={logoUrl(branding, "light")} alt="" data-preview="export" className="object-contain" style={{ ...h("export"), maxWidth: 200 }} />
            <span className="font-semibold text-ink">{branding.app_name}</span>
          </div>
          <span className="border-b border-line pb-1 font-semibold text-ink">Portfolio overview</span>
          <span className="text-small text-ink-muted">PDF export header</span>
        </ThemeScope>
      </div>
    </div>
  );
}
