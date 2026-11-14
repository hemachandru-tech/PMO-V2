import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { applyChartLibrary, type ChartLibrary } from "../design/chartLibrary";
import { applyTenantLayout, type Layout } from "../design/layout";
import { applyNavColors, type NavColors } from "../design/navColors";
import { applyPalette } from "../design/palette";
import { useTheme, type ResolvedTheme } from "../design/theme";
import defaultLogo from "../design/tela-logo.png";
import { cn } from "../lib/utils";
import { getConfig } from "./config";

export type BrandSlot = "logo_light" | "logo_dark" | "favicon";

export interface BrandAsset {
  url: string;
  content_type: string;
  bytes: number;
  width: number | null;
  height: number | null;
}

export interface PaletteSpec {
  accent: string;
  seeds: string[];
  variant: "natural" | "deep" | "vivid" | "soft";
  source: "logo" | "custom";
}

export interface PaletteCheck {
  id: string;
  label: string;
  theme: ResolvedTheme;
  kind: "contrast" | "delta_e" | "rag";
  value: number;
  min: number;
  ok: boolean;
  tokens?: string[];
}

/** A colour palette (US-150): `--tl-*` token values for each theme. `checks` and `notes` only come back for Admins. */
export interface Palette {
  id: string;
  name: string;
  spec: PaletteSpec;
  tokens: Record<ResolvedTheme, Record<string, string>>;
  brand: string[];
  notes?: string[];
  checks?: PaletteCheck[];
  ok?: boolean;
}

/** How tall the logo is drawn in each place, in px (US-152). */
export interface LogoSizes {
  sidebar: number;
  sign_in: number;
  export: number;
}

export const DEFAULT_LOGO_SIZES: LogoSizes = { sidebar: 28, sign_in: 48, export: 28 };

/** GET /api/v1/branding (ENH-006, US-149). `updated_at` / `updated_by` only come back for people who can change it. */
export interface Branding {
  app_name: string;
  tagline: string | null;
  assets: Record<BrandSlot, BrandAsset | null>;
  /** The chosen palette (US-150); none = the Tela colours. */
  palette?: Palette | null;
  logo_sizes?: LogoSizes;
  /** Which library draws the standard charts (ENH-018, US-206); none = ECharts. */
  chart_library?: ChartLibrary;
  /** How the sidebar and top bar are coloured (ENH-019, US-209); none = standard. */
  nav_colors?: NavColors;
  /** The company's default layout (ENH-021); none = Classic. */
  layout?: Layout;
  version: number;
  is_default: boolean;
  defaults: { app_name: string; tagline: string; logo_sizes?: LogoSizes; logo_size_limits?: Record<keyof LogoSizes, [number, number]>; chart_library?: ChartLibrary; nav_colors?: NavColors; layout?: Layout };
  updated_at?: string | null;
  updated_by?: { id: string; name: string | null } | null;
}

export const DEFAULT_BRANDING: Branding = {
  app_name: "Tela",
  tagline: "PMO analytics",
  assets: { logo_light: null, logo_dark: null, favicon: null },
  palette: null,
  logo_sizes: DEFAULT_LOGO_SIZES,
  chart_library: "echarts",
  nav_colors: { sidebar: "standard", topbar: "standard" },
  version: 0,
  is_default: true,
  defaults: { app_name: "Tela", tagline: "PMO analytics", logo_sizes: DEFAULT_LOGO_SIZES },
};

let current: Branding = DEFAULT_BRANDING;
let publicLoad: Promise<Branding> | null = null;

/** A branding answer the app can show; anything malformed gives the defaults. */
function usable(b: Branding | null | undefined): Branding {
  return b && typeof b.app_name === "string" && b.assets ? b : DEFAULT_BRANDING;
}

/** The app name for code outside React (error messages); components use `useBranding()`. */
export function appName(): string {
  return current.app_name;
}

/**
 * The branding for screens shown before sign-in, fetched once without a token for the configured issuer. Any failure
 * gives the Tela defaults: branding must never stop anyone signing in.
 */
export function loadPublicBranding(fetchImpl: typeof fetch = fetch): Promise<Branding> {
  publicLoad ??= (async () => {
    try {
      const cfg = getConfig();
      const resp = await fetchImpl(`${cfg.apiBaseUrl}/api/v1/branding/public?issuer=${encodeURIComponent(cfg.oidcAuthority)}`);
      if (!resp.ok) return DEFAULT_BRANDING;
      const data = usable((await resp.json()) as Branding);
      if (current === DEFAULT_BRANDING) {
        current = data;
        applyBrand(data);
      }
      return data;
    } catch {
      return DEFAULT_BRANDING;
    }
  })();
  return publicLoad;
}

export function usePublicBranding(): Branding {
  const [branding, setBranding] = useState(current);
  useEffect(() => {
    let active = true;
    void loadPublicBranding().then((b) => active && setBranding(b));
    return () => {
      active = false;
    };
  }, []);
  return branding;
}

/**
 * The signed-in tenant's branding; the defaults (or the pre-sign-in copy) until it loads. Checked again every few
 * minutes and when the tab comes back into focus, so an Admin's change reaches everyone without a reload (US-151);
 * asset URLs carry the branding version, so new logos are never served from cache.
 */
export function useBranding(): Branding {
  const query = useQuery({
    queryKey: ["branding"],
    queryFn: async () => usable(await api<Branding>("/api/v1/branding")),
    staleTime: 60_000,
    refetchInterval: 300_000,
    refetchOnWindowFocus: true,
  });
  const branding = query.data ?? current;
  useEffect(() => {
    if (!query.data) return;
    current = query.data;
    applyBrand(query.data);
  }, [query.data]);
  return branding;
}

/** The app name, kept current in every component that shows it in its copy. */
export function useAppName(): string {
  return useBranding().app_name;
}

/** An asset's full URL (the API may sit on another origin in development). */
export function assetUrl(asset: BrandAsset | null | undefined): string | null {
  return asset ? `${getConfig().apiBaseUrl}${asset.url}` : null;
}

/** The logo for a theme: the dark logo in dark mode when there is one, else the light logo, else Tela's. */
export function logoUrl(branding: Branding, theme: ResolvedTheme): string {
  const asset = theme === "dark" ? branding.assets.logo_dark ?? branding.assets.logo_light : branding.assets.logo_light;
  return assetUrl(asset) ?? defaultLogo;
}

/** A logo size for a place, kept inside its limits (an old or odd answer never draws a huge logo). */
export function logoSize(branding: Branding, place: keyof LogoSizes): number {
  const v = branding.logo_sizes?.[place] ?? DEFAULT_LOGO_SIZES[place];
  const [lo, hi] = branding.defaults.logo_size_limits?.[place] ?? [16, 128];
  return Math.min(hi, Math.max(lo, v));
}

/**
 * The tenant's logo. With `place`, it is drawn at the height the Admin set for that place (US-152), its width
 * following the image's shape up to `maxWidth`; without it, the className sizes it.
 */
export function BrandLogo({ branding, className, place, maxWidth, theme }: {
  branding: Branding; className?: string; place?: keyof LogoSizes; maxWidth?: number;
  /** Which logo to show when the background differs from the page theme (a brand-coloured sidebar, US-209). */
  theme?: ResolvedTheme;
}) {
  const { resolved: pageTheme } = useTheme();
  const resolved = theme ?? pageTheme;
  const style = place ? { height: logoSize(branding, place), width: "auto", maxWidth } : undefined;
  return <img src={logoUrl(branding, resolved)} alt="" data-logo={place} className={cn("object-contain", className)} style={style} />;
}

/** Page title in the browser tab: "<page> · <app name>". */
export function pageTitle(title: string, branding: Branding = current): string {
  return `${title} · ${branding.app_name}`;
}

/** The palette as page-wide colours (US-151), the chart library (US-206) and the bar colours (US-209), plus the tab
 * title and icon. */
function applyBrand(branding: Branding) {
  applyChartLibrary(branding.chart_library);
  applyNavColors(branding.nav_colors);
  applyTenantLayout(branding.layout);
  applyPalette(branding.palette?.tokens ?? null, branding.palette ? `${branding.palette.id}@${branding.version}` : "tela");
  applyHead(branding);
}

function applyHead(branding: Branding) {
  if (typeof document === "undefined") return;
  if (document.title === DEFAULT_BRANDING.app_name || document.title === "") document.title = branding.app_name;
  let link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  const fav = branding.assets.favicon ?? branding.assets.logo_light;
  link.href = assetUrl(fav) ?? defaultLogo;
  if (fav) link.type = fav.content_type;
  else link.removeAttribute("type");
}

/** Test helper: forget what was loaded. */
export function resetBrandingForTests(): void {
  current = DEFAULT_BRANDING;
  publicLoad = null;
  applyPalette(null, "tela");
  applyChartLibrary("echarts");
  applyNavColors(null);
  applyTenantLayout(null);
}
