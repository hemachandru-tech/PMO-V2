import { ChartColumn, LayoutDashboard, Search, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { api, type ApiError } from "../../../api/client";
import { BrandLogo, type Branding } from "../../../app/branding";
import { navAttrs, NavTheme } from "../../../app/shell/NavScope";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { NAV_PLACES, NAV_STYLES, navColors, type NavColors, type NavPlace, type NavStyle } from "../../../design/navColors";
import { ThemeScope, type ResolvedTheme } from "../../../design/theme";
import { asApiError } from "../../../lib/api-errors";
import { cn } from "../../../lib/utils";

/**
 * Bar colours (ENH-019, US-209): how the sidebar and the top bar take the brand's colours — standard, tinted, brand
 * or dark — chosen apart. The preview draws both bars with the palette in use, light and dark, before saving.
 */
export function NavColorsCard({ branding, onSaved }: { branding: Branding; onSaved: (b: Branding) => void }) {
  const stored = navColors(branding.nav_colors);
  const [colors, setColors] = useState<NavColors>(stored);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setColors(navColors(branding.nav_colors)), [branding.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const changed = colors.sidebar !== stored.sidebar || colors.topbar !== stored.topbar;

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      const out = await api<Branding>("/api/v1/admin/branding/nav-colors", { method: "PUT", body: { ...colors, expected_version: branding.version } });
      onSaved(out);
      toast.success("Bar colours saved — everyone sees them now");
    } catch (e) {
      setError(asApiError(e, "The bar colours could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Bar colours</CardTitle>
        <CardDescription>How the sidebar and the top bar use your brand colours. They follow the colour palette above, in light and dark mode. The Aurora layout uses the sidebar colour only; its top bar keeps the Aurora look.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <form className="grid content-start gap-5" aria-label="Bar colours" onSubmit={(e) => { e.preventDefault(); if (changed) void save(); }}>
          {NAV_PLACES.map((place) => (
            <fieldset key={place.id} className="grid gap-2">
              <legend className="pb-2 font-medium">{place.label}</legend>
              <RadioGroup value={colors[place.id]} onValueChange={(v) => setColors({ ...colors, [place.id]: v as NavStyle })}
                aria-label={place.label} className="grid gap-2 sm:grid-cols-2">
                {NAV_STYLES.map((s) => (
                  <label key={s.id} htmlFor={`nav-${place.id}-${s.id}`}
                    className="flex cursor-pointer items-start gap-3 rounded-md border border-line p-3 has-[[data-state=checked]]:border-accent">
                    <RadioGroupItem id={`nav-${place.id}-${s.id}`} value={s.id} className="mt-0.5" />
                    <span className="grid gap-0.5">
                      <span className="font-medium">{s.label}{s.id === "standard" && <span className="font-normal text-ink-muted"> · default</span>}</span>
                      <span className="text-small text-ink-muted">{s.note}</span>
                    </span>
                  </label>
                ))}
              </RadioGroup>
            </fieldset>
          ))}
          {error && <FormAlert error={error} />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !changed}>{busy ? "Saving…" : "Save bar colours"}</Button>
            {changed && <Button type="button" variant="ghost" onClick={() => setColors(stored)}>Discard</Button>}
          </div>
        </form>
        <div className="grid content-start gap-3" aria-label="Bar colours preview" data-preview-sidebar={colors.sidebar} data-preview-topbar={colors.topbar}>
          <span className="text-label uppercase tracking-wider text-ink-muted">Preview</span>
          <div className="grid gap-3 xl:grid-cols-2">
            {(["light", "dark"] as ResolvedTheme[]).map((theme) => (
              <ThemeScope key={theme} theme={theme} className="overflow-hidden rounded-md border border-line bg-surface">
                <PreviewShell branding={branding} colors={colors} />
              </ThemeScope>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

const ITEMS = [
  { label: "Home", icon: LayoutDashboard },
  { label: "Ask", icon: Sparkles, current: true },
  { label: "Reports", icon: ChartColumn },
];

/** A small app frame drawn with the same bar colours as the real shell (`data-nav`, index.css). */
function PreviewShell({ branding, colors }: { branding: Branding; colors: NavColors }) {
  return (
    <div className="flex h-56 text-small" aria-hidden>
      <PreviewBar place="sidebar" style={colors.sidebar}>
        <div className="flex w-32 flex-col gap-2 px-2 py-3">
          <span className="flex items-center gap-1.5 px-1 font-semibold text-ink">
            <BrandLogo branding={branding} theme={colors.sidebar === "brand" ? "dark" : undefined} className="size-5 shrink-0" />
            <span className="truncate">{branding.app_name}</span>
          </span>
          <span className="px-1 pt-1 text-[10px] uppercase tracking-wider text-ink-muted">Explore</span>
          {ITEMS.map((item) => (
            <span key={item.label} className={cn("flex h-7 items-center gap-1.5 rounded-md px-1.5 text-ink-muted",
              item.current && "bg-(--nav-current-bg) font-medium text-(--nav-current-ink)")}>
              <item.icon className="size-3.5 shrink-0" />{item.label}
            </span>
          ))}
          <span className="mt-auto truncate border-t border-line px-1 pt-2 text-ink">Signed-in user</span>
        </div>
      </PreviewBar>
      <div className="flex min-w-0 flex-1 flex-col border-l border-line">
        <PreviewBar place="topbar" style={colors.topbar}>
          <div className="flex h-11 items-center gap-2 border-b border-line px-3">
            <span className="hidden truncate text-ink-muted sm:inline">Ask</span>
            <span className="flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2 text-ink-disabled">
              <Search className="size-3.5 shrink-0" /><span className="truncate">Ask anything</span>
            </span>
            <span className="grid size-6 shrink-0 place-items-center rounded-full bg-(--nav-current-bg) text-[10px] font-semibold text-(--nav-current-ink)">SK</span>
          </div>
        </PreviewBar>
        <div className="grid flex-1 content-start gap-2 p-3">
          <span className="h-3 w-2/3 rounded-sm bg-surface-sunken" />
          <span className="h-16 rounded-md border border-line bg-surface-raised" />
        </div>
      </div>
    </div>
  );
}

function PreviewBar({ place, style, children }: { place: NavPlace; style: NavStyle; children: React.ReactNode }) {
  return (
    <NavTheme style={style}>
      <div {...navAttrs(place, style)} className="flex bg-(--nav-bg) [&>*]:flex-1">{children}</div>
    </NavTheme>
  );
}
