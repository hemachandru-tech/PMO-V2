import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { DEFAULT_BRANDING, loadPublicBranding, type Branding, type Palette } from "../app/branding";
import { setConfigForTests } from "../app/config";
import { visibleNav } from "../app/shell/nav";
import { extractColours, logoPixels } from "../features/admin/branding/extract";
import { echartsTheme } from "../design/echartsTheme";
import { activeChartLibrary } from "../design/chartLibrary";
import { applyUserLayout } from "../design/layout";
import { activePaletteKey, applyPalette, paletteCss } from "../design/palette";

vi.mock("../features/admin/branding/extract", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../features/admin/branding/extract")>()),
  logoPixels: vi.fn(async () => { throw new Error("no canvas in jsdom"); }),
}));

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

const admin: Me = {
  user_id: "u1", display_name: "Ada Admin", email: null, tenant_id: "t1", roles: ["admin"], level: "admin", audience_roles: [],
  permissions: ["dashboards.view", "ask.use", "mappings.manage", "policies.manage", "branding.manage"], scope_summary: [], locale: null, time_zone: null,
};
const RENAMED: Branding = {
  ...DEFAULT_BRANDING, app_name: "Insights Intelligence", tagline: "Portfolio insights", version: 3, is_default: false,
  assets: { logo_light: { url: "/api/v1/branding/assets/t1/logo_light?v=3", content_type: "image/svg+xml", bytes: 2048, width: 120, height: 40 }, logo_dark: null, favicon: null },
  updated_at: "2026-10-27T09:00:00Z", updated_by: { id: "u1", name: "Ada Admin" },
};

type Call = { url: string; method: string; body: unknown };

function serve(me: Me, branding: Branding, answers: Record<string, (body: unknown) => Response> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body instanceof FormData ? init.body : init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });
    const key = `${method} ${url.replace("http://api.test", "")}`;
    if (answers[key]) return answers[key]!(body);
    if (url.endsWith("/api/v1/me")) return json(me);
    if (url.endsWith("/api/v1/branding")) return json(branding);
    if (url.includes("/api/v1/notifications")) return json({ items: [], unread_count: 0 });
    return json({ items: [] });
  }));
  return calls;
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(
    <TestAuthProvider token="tok">
      <Providers client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppRoutes />
      </Providers>
    </TestAuthProvider>,
  );
}

const json = (b: unknown, status = 200) => () => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc/realms/tela", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("branding across the app (ENH-006, US-149)", () => {
  it("shows the tenant's name, tagline and logo in the sidebar and the tab title", async () => {
    serve(admin, RENAMED);
    renderAt("/admin/access/scope");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(await within(nav).findByText("Insights Intelligence")).toBeInTheDocument();
    expect(within(nav).getByText("Portfolio insights")).toBeInTheDocument();
    expect(within(nav).queryByText("Tela")).not.toBeInTheDocument();
    expect(nav.querySelector("img")).toHaveAttribute("src", "http://api.test/api/v1/branding/assets/t1/logo_light?v=3");
    await waitFor(() => expect(document.title).toBe("Scope policies · Insights Intelligence"));
    await waitFor(() => expect(document.querySelector("link[rel='icon']")).toHaveAttribute("href", "http://api.test/api/v1/branding/assets/t1/logo_light?v=3"));
  });

  it("lists Branding under Administer only for people who can change it", () => {
    expect(visibleNav(admin).map((i) => i.label)).toContain("Branding");
    expect(visibleNav({ ...admin, permissions: admin.permissions.filter((p) => p !== "branding.manage") }).map((i) => i.label)).not.toContain("Branding");
  });

  it("gives the defaults before sign-in when the public branding can't be read", async () => {
    const failing = vi.fn(async () => { throw new TypeError("offline"); }) as unknown as typeof fetch;
    expect((await loadPublicBranding(failing)).app_name).toBe("Tela");
  });

  it("asks for the configured issuer's branding before sign-in", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(RENAMED), { status: 200 })) as unknown as typeof fetch;
    expect((await loadPublicBranding(fetcher)).app_name).toBe("Insights Intelligence");
    expect(fetcher).toHaveBeenCalledWith("http://api.test/api/v1/branding/public?issuer=http%3A%2F%2Fkc%2Frealms%2Ftela");
  });
});

describe("Branding screen", () => {
  it("previews a new name, saves it with the version it was based on and renames the app", async () => {
    const saved = { ...DEFAULT_BRANDING, app_name: "Insights Intelligence", tagline: "Portfolio insights", version: 1, is_default: false, updated_by: { id: "u1", name: "Ada Admin" }, updated_at: "2026-10-27T09:00:00Z" };
    const calls = serve(admin, DEFAULT_BRANDING, { "PUT /api/v1/admin/branding": json(saved) });
    renderAt("/admin/branding");
    const name = await screen.findByRole("textbox", { name: "App name" });
    expect(name).toHaveValue("Tela");
    await userEvent.clear(name);
    await userEvent.type(name, "Insights Intelligence");
    const tagline = screen.getByRole("textbox", { name: /Tagline/ });
    await userEvent.clear(tagline);
    await userEvent.type(tagline, "Portfolio insights");
    const preview = screen.getByLabelText("Preview");
    expect(within(preview).getByText("Home · Insights Intelligence")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ app_name: "Insights Intelligence", tagline: "Portfolio insights", expected_version: 0 }));
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(await within(nav).findByText("Insights Intelligence")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Branding · Insights Intelligence"));
    expect(screen.getByText(/Last changed on .* by Ada Admin\./)).toBeInTheDocument();
  });

  it("checks the name before sending it", async () => {
    const calls = serve(admin, DEFAULT_BRANDING);
    renderAt("/admin/branding");
    const name = await screen.findByRole("textbox", { name: "App name" });
    await userEvent.clear(name);
    await userEvent.type(name, "X");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Enter 2 to 40 characters.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("uploads a logo and shows why one is refused", async () => {
    const refused = { error: { code: "validation_error", message: "The SVG contains a <script> element, which isn't allowed in a logo.", request_id: "r1", details: {} } };
    const calls = serve(admin, DEFAULT_BRANDING, {
      "POST /api/v1/admin/branding/assets/logo_light": json(RENAMED),
      "POST /api/v1/admin/branding/assets/logo_dark": json(refused, 400),
    });
    const { container } = renderAt("/admin/branding");
    await screen.findByRole("heading", { name: "Branding" });
    const light = container.querySelector<HTMLInputElement>("#brand-logo_light")!;
    await userEvent.upload(light, new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/assets/logo_light"))?.body).toBeInstanceOf(FormData));
    expect((calls.find((c) => c.url.endsWith("/assets/logo_light"))!.body as FormData).get("file")).toBeInstanceOf(File);
    expect(await screen.findByText("SVG · 120 × 40 px · 2 KB")).toBeInTheDocument();

    const dark = container.querySelector<HTMLInputElement>("#brand-logo_dark")!;
    await userEvent.upload(dark, new File(["<svg/>"], "evil.svg", { type: "image/svg+xml" }));
    expect(await screen.findByText("The SVG contains a <script> element, which isn't allowed in a logo.")).toBeInTheDocument();
  });

  it("resets to the defaults after confirming", async () => {
    const calls = serve(admin, RENAMED, { "POST /api/v1/admin/branding/reset": json({ ...DEFAULT_BRANDING, version: 4 }) });
    renderAt("/admin/branding");
    await userEvent.click(await screen.findByRole("button", { name: "Reset to defaults" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("The name goes back to “Tela”");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reset branding" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/reset"))).toBe(true));
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(await within(nav).findByText("Tela")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- palettes (US-150)

/** RGBA pixels: `n` of each colour, in order. */
function pixels(...runs: [string, number, number?][]): Uint8ClampedArray {
  const out: number[] = [];
  for (const [hex, n, alpha = 255] of runs) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    for (let i = 0; i < n; i++) out.push(r!, g!, b!, alpha);
  }
  return new Uint8ClampedArray(out);
}

describe("logo colours (US-150)", () => {
  it("finds the main colours, most common first, skipping background, outlines and see-through pixels", () => {
    const found = extractColours(pixels(
      ["#ffffff", 500], ["#000000", 80], ["#d62828", 300, 0], // background, outline, transparent
      ["#287a9a", 400], ["#2a7c9c", 200], // two shades of one blue: merged
      ["#e46e22", 300], ["#f7bd3c", 5], // the yellow is under 1 %
    ));
    expect(found.map((c) => c.hex)).toHaveLength(2);
    expect(found[0]!.hex).toMatch(/^#2[89]7[ab]9[ab]$/);
    expect(found[0]!.share).toBeCloseTo(600 / 905, 2);
    expect(found[1]).toEqual({ hex: "#e46e22", share: 300 / 905 });
  });

  it("returns nothing for an empty or all-white logo", () => {
    expect(extractColours(pixels(["#ffffff", 100], ["#fafafa", 50]))).toEqual([]);
    expect(extractColours(new Uint8ClampedArray())).toEqual([]);
  });

  it("caps the number of colours", () => {
    const runs: [string, number][] = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#00ffff", "#ff00ff", "#808000", "#008080", "#800080", "#804000"].map((h) => [h, 10]);
    expect(extractColours(pixels(...runs), 4)).toHaveLength(4);
  });
});

const KEYS = ["accent", "accent-strong", "accent-soft", "on-accent", "focus", ...Array.from({ length: 8 }, (_, i) => `chart-${i + 1}`),
  "seq-1", "seq-2", "seq-3", "seq-4", "seq-5", "div-neg-2", "div-neg-1", "div-mid", "div-pos-1", "div-pos-2"];

function palette(id: string, name: string, light: string, dark: string, source: "logo" | "custom" = "logo"): Palette {
  const tokens = (accent: string) => Object.fromEntries(KEYS.map((k) => [k, k === "accent" ? accent : "#7d8c96"]));
  return {
    id, name, brand: [light], tokens: { light: tokens(light), dark: tokens(dark) },
    spec: { accent: light, seeds: ["#287a9a", "#e46e22"], variant: "natural", source },
    notes: source === "custom" ? [`${light} is adjusted so text on it can be read.`] : [],
    checks: [
      { id: "light:accent/surface-raised", label: "accent text on surface-raised", theme: "light", kind: "contrast", value: 5.2, min: 4.5, ok: true },
      { id: "dark:chart/adjacent-cvd", label: "neighbouring chart colours apart for colour-blind readers", theme: "dark", kind: "delta_e", value: 16.2, min: 15, ok: true },
    ],
    ok: true,
  };
}
const BLUE = palette("p-blue", "Blue", "#227695", "#4f9dbe");
const ORANGE = palette("p-orange", "Orange", "#b44f00", "#e66700");

describe("Branding screen: colours (US-150)", () => {
  beforeEach(() => {
    vi.mocked(logoPixels).mockResolvedValue(pixels(["#ffffff", 50], ["#287a9a", 60], ["#e46e22", 40]));
  });
  afterEach(() => vi.mocked(logoPixels).mockReset());

  it("asks for palettes from the logo's colours, previews one in both themes and saves its spec", async () => {
    const saved = { ...DEFAULT_BRANDING, palette: { ...ORANGE, checks: undefined }, version: 1, is_default: false };
    const calls = serve(admin, DEFAULT_BRANDING, {
      "POST /api/v1/admin/branding/palettes": json({ candidates: [BLUE, ORANGE], dropped: 0, greys_only: false }),
      "PUT /api/v1/admin/branding/palette": json(saved),
    });
    renderAt("/admin/branding");
    const group = await screen.findByRole("radiogroup", { name: "Palette" });
    await within(group).findByText("Orange");
    expect(calls.find((c) => c.url.endsWith("/branding/palettes"))?.body).toEqual({ seeds: ["#287a9a", "#e46e22"] });
    expect(within(screen.getByLabelText("Colours found in the logo")).getAllByTitle(/^#/).map((e) => e.title)).toEqual(["#287a9a", "#e46e22"]);
    expect(within(group).getByRole("radio", { name: /Tela/ })).toBeChecked();
    expect(within(group).getAllByText("2 of 2 checks pass")).toHaveLength(2);

    await userEvent.click(within(group).getByRole("radio", { name: /Orange/ }));
    const preview = screen.getByLabelText("Palette preview");
    expect(within(preview).getByText("Preview · Orange")).toBeInTheDocument();
    const [light, dark] = [...preview.querySelectorAll<HTMLElement>("[data-theme]")];
    expect(light!.style.getPropertyValue("--tl-accent")).toBe("#b44f00");
    expect(dark!.style.getPropertyValue("--tl-accent")).toBe("#e66700");
    expect(within(light!).getByText("Open risks")).toBeInTheDocument();
    expect(within(light!).getByRole("img", { name: /eight series colours/ })).toBeInTheDocument();
    expect(within(preview).getByText("All 2 checks pass")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Use Orange" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ spec: ORANGE.spec, expected_version: 0 }));
    expect(await within(group).findByText("Current")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use Orange" })).toBeDisabled();
  });

  it("tries the Admin's own accent, and shows why red is refused", async () => {
    const custom = palette("p-gold", "Gold", "#856a00", "#ffcc00", "custom");
    const refused = { error: { code: "validation_error", message: "#d62828 reads as red, which means Off track in badges and charts, so it can't be the accent. Pick another colour.", request_id: "r1", details: {} } };
    let n = 0;
    const calls = serve(admin, DEFAULT_BRANDING, {
      "POST /api/v1/admin/branding/palettes": (body) => {
        const accent = (body as { accent?: string }).accent;
        if (!accent) return json({ candidates: [BLUE], dropped: 0, greys_only: false })();
        n++;
        return accent === "#ffcc00" ? json({ candidates: [custom], dropped: 0, greys_only: false })() : json(refused, 400)();
      },
    });
    renderAt("/admin/branding");
    await screen.findByRole("radio", { name: /Blue/ });
    const input = screen.getByLabelText("Or start from your own accent colour");
    fireEvent.change(input, { target: { value: "#ffcc00" } });
    await userEvent.click(screen.getByRole("button", { name: "Try this colour" }));
    expect(await screen.findByRole("radio", { name: /Gold/ })).toBeChecked();
    expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ accent: "#ffcc00", seeds: ["#287a9a", "#e46e22"] });
    expect(screen.getByText("#856a00 is adjusted so text on it can be read.")).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "#d62828" } });
    await userEvent.click(screen.getByRole("button", { name: "Try this colour" }));
    expect(await screen.findByText(/reads as red, which means Off track/)).toBeInTheDocument();
    expect(n).toBe(2);
  });

  it("says so when the logo has no colours to use, and still offers the Tela colours", async () => {
    vi.mocked(logoPixels).mockResolvedValue(pixels(["#777777", 50]));
    serve(admin, DEFAULT_BRANDING, { "POST /api/v1/admin/branding/palettes": json({ candidates: [], dropped: 0, greys_only: true }) });
    renderAt("/admin/branding");
    expect(await screen.findByText(/only greys, black or white/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Tela/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Use the Tela colours" })).toBeDisabled();
  });

  it("goes back to the Tela colours from a saved palette", async () => {
    const calls = serve(admin, { ...DEFAULT_BRANDING, palette: BLUE, version: 2, is_default: false }, {
      "POST /api/v1/admin/branding/palettes": json({ candidates: [BLUE], dropped: 0, greys_only: false }),
      "PUT /api/v1/admin/branding/palette": json({ ...DEFAULT_BRANDING, version: 3 }),
    });
    renderAt("/admin/branding");
    expect(await screen.findByRole("radio", { name: /Blue/ })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /Tela/ }));
    await userEvent.click(screen.getByRole("button", { name: "Use the Tela colours" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ spec: null, expected_version: 2 }));
  });
});

// --------------------------------------------------------------------------- runtime palette, logo size, history (US-151/152)

describe("the palette across the app (US-151)", () => {
  afterEach(() => applyPalette(null, "tela"));

  it("writes the palette for light, dark and system-dark, and keeps anything but token names and hex colours out", () => {
    const css = paletteCss({ light: { accent: "#227695", "x;}body{": "#000000", focus: "red" }, dark: { accent: "#4f9dbe" } });
    expect(css).toContain(':root, [data-theme="light"] { --tl-accent: #227695; }');
    expect(css).toContain('[data-theme="dark"] { --tl-accent: #4f9dbe; }');
    expect(css).toContain("@media (prefers-color-scheme: dark) { :root:not([data-theme]) { --tl-accent: #4f9dbe; } }");
    expect(css).toContain('[data-theme="dark"] { --tl-accent: #4f9dbe; }');
    expect(paletteCss({ light: { focus: "#227695" }, dark: { focus: "#4f9dbe" } })).toContain("--tl-focus-ring: 0 0 0 2px #161e23, 0 0 0 4px #4f9dbe;");
    expect(css).not.toContain("body");
    expect(css).not.toContain("red");
  });

  it("gives charts the palette's series colours, and the Tela ones again without it", () => {
    applyPalette(BLUE.tokens, "p-blue@1");
    expect((echartsTheme("light").color as string[])[0]).toBe("#7d8c96");
    expect(activePaletteKey()).toBe("p-blue@1");
    applyPalette(null, "tela");
    expect((echartsTheme("light").color as string[])[0]).toBe("#0893bf");
  });

  it("applies the tenant's palette and logo size when the app loads", async () => {
    serve(admin, { ...RENAMED, palette: BLUE, logo_sizes: { sidebar: 44, sign_in: 96, export: 28 } });
    renderAt("/admin/branding");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() => expect(activePaletteKey()).toBe("p-blue@3"));
    const logo = await waitFor(() => {
      const img = nav.querySelector<HTMLImageElement>("img[data-logo='sidebar']");
      expect(img).not.toBeNull();
      return img!;
    });
    await waitFor(() => expect(logo.style.height).toBe("44px"));
    expect(logo.style.maxWidth).toBe("160px");
  });
});

describe("Branding screen: logo size (US-152)", () => {
  it("previews the new heights and saves them with the version", async () => {
    const calls = serve(admin, RENAMED, {
      "PUT /api/v1/admin/branding/logo-size": json({ ...RENAMED, version: 4, logo_sizes: { sidebar: 40, sign_in: 48, export: 28 } }),
    });
    renderAt("/admin/branding");
    const form = await screen.findByRole("form", { name: "Logo size" });
    const save = within(form).getByRole("button", { name: "Save sizes" });
    expect(save).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("Sidebar logo height in pixels"), { target: { value: "40" } });
    const preview = screen.getByLabelText("Logo size preview");
    expect(preview.querySelector<HTMLImageElement>("[data-preview='sidebar']")!.style.height).toBe("40px");

    fireEvent.change(within(form).getByLabelText("Sign-in screens logo height in pixels"), { target: { value: "500" } });
    expect(within(form).getByRole("alert")).toHaveTextContent("within its range");
    expect(save).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("Sign-in screens logo height"), { target: { value: "48" } });

    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ sidebar: 40, sign_in: 48, export: 28, expected_version: 3 }));
    const nav = screen.getByRole("navigation", { name: "Main" });
    await waitFor(() => expect(nav.querySelector<HTMLImageElement>("img[data-logo='sidebar']")!.style.height).toBe("40px"));
  });

  it("offers the defaults back", async () => {
    serve(admin, { ...RENAMED, logo_sizes: { sidebar: 50, sign_in: 48, export: 28 } });
    renderAt("/admin/branding");
    const form = await screen.findByRole("form", { name: "Logo size" });
    await userEvent.click(within(form).getByRole("button", { name: "Restore defaults" }));
    expect(within(form).getByLabelText("Sidebar logo height in pixels")).toHaveValue(28);
    expect(within(form).getByRole("button", { name: "Save sizes" })).toBeEnabled();
  });
});

describe("Branding screen: chart library (ENH-018, US-206)", () => {
  it("previews each library and saves the choice with the version, and the app's charts follow it", async () => {
    const calls = serve(admin, RENAMED, {
      "PUT /api/v1/admin/branding/chart-library": json({ ...RENAMED, version: 4, chart_library: "plot" }),
    });
    renderAt("/admin/branding");
    const form = await screen.findByRole("form", { name: "Chart library" });
    const save = within(form).getByRole("button", { name: "Save chart library" });
    expect(save).toBeDisabled();
    const preview = screen.getByLabelText("Chart library preview");
    expect(preview).toHaveAttribute("data-preview-library", "echarts");
    await userEvent.click(within(form).getByRole("radio", { name: /Observable Plot/ }));
    expect(preview).toHaveAttribute("data-preview-library", "plot");
    await waitFor(() => expect(preview.querySelectorAll("[data-library='plot']")).toHaveLength(2));
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ library: "plot", expected_version: 3 }));
    await waitFor(() => expect(activeChartLibrary()).toBe("plot"));
  });

  it("applies the tenant's chart library when the app loads", async () => {
    serve(admin, { ...RENAMED, chart_library: "recharts" });
    renderAt("/admin/branding");
    await waitFor(() => expect(activeChartLibrary()).toBe("recharts"));
  });
});

describe("Branding screen: bar colours (ENH-019, US-209)", () => {
  it("previews the sidebar and top bar styles and saves them with the version, and the shell follows", async () => {
    const calls = serve(admin, RENAMED, {
      "PUT /api/v1/admin/branding/nav-colors": json({ ...RENAMED, version: 4, nav_colors: { sidebar: "brand", topbar: "tinted" } }),
    });
    renderAt("/admin/branding");
    const form = await screen.findByRole("form", { name: "Bar colours" });
    const save = within(form).getByRole("button", { name: "Save bar colours" });
    expect(save).toBeDisabled();
    const preview = screen.getByLabelText("Bar colours preview");
    expect(preview).toHaveAttribute("data-preview-sidebar", "standard");
    await userEvent.click(within(within(form).getByRole("radiogroup", { name: "Sidebar" })).getByRole("radio", { name: /Brand/ }));
    await userEvent.click(within(within(form).getByRole("radiogroup", { name: "Top bar" })).getByRole("radio", { name: /Tinted/ }));
    expect(preview).toHaveAttribute("data-preview-sidebar", "brand");
    expect(preview.querySelectorAll("[data-nav-place='sidebar'][data-nav='brand']")).toHaveLength(2);
    expect(preview.querySelectorAll("[data-nav-place='topbar'][data-nav='tinted']")).toHaveLength(2);
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ sidebar: "brand", topbar: "tinted", expected_version: 3 }));
    const nav = screen.getByRole("navigation", { name: "Main" });
    await waitFor(() => expect(nav).toHaveAttribute("data-nav", "brand"));
    expect(document.querySelector("header[data-nav-place='topbar']")).toHaveAttribute("data-nav", "tinted");
  });

  it("draws a dark sidebar in the dark theme, and an unknown style as standard", async () => {
    serve(admin, { ...RENAMED, nav_colors: { sidebar: "dark", topbar: "neon" } as unknown as Branding["nav_colors"] });
    renderAt("/admin/branding");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() => expect(nav).toHaveAttribute("data-nav", "dark"));
    expect(nav.closest("[data-theme='dark']")).not.toBeNull();
    expect(document.querySelector("header[data-nav-place='topbar']")).toHaveAttribute("data-nav", "standard");
  });
});

describe("Layouts (ENH-021)", () => {
  afterEach(() => applyUserLayout("default"));

  it("saves the company's default layout, and the frame follows it", async () => {
    const calls = serve(admin, RENAMED, {
      "PUT /api/v1/admin/branding/layout": json({ ...RENAMED, version: 4, layout: "executive" }),
    });
    renderAt("/admin/branding");
    const form = await screen.findByRole("form", { name: "Default layout" });
    const save = within(form).getByRole("button", { name: "Save default layout" });
    expect(save).toBeDisabled();
    expect(document.querySelector("aside nav[aria-label='Main']")).not.toBeNull();
    await userEvent.click(within(form).getByRole("radio", { name: /Executive/ }));
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ layout: "executive", expected_version: 3 }));
    await waitFor(() => expect(document.documentElement.dataset.layout).toBe("executive"));
    // Executive: no sidebar; the navigation sits in a brand-coloured top bar.
    expect(document.querySelector("aside nav[aria-label='Main']")).toBeNull();
    expect(document.querySelector("header[data-nav-place='topbar']")).toHaveAttribute("data-nav", "brand");
    expect(within(document.querySelector("header")!).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("lets a person pick their own layout over the company's", async () => {
    const calls = serve(admin, { ...RENAMED, layout: "executive" }, {
      "PUT /api/v1/me/preferences": json({ theme: "system", ask_verbosity: "balanced", layout: "command" }),
    });
    renderAt("/admin/branding");
    await waitFor(() => expect(document.documentElement.dataset.layout).toBe("executive"));
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Command centre" }));
    expect(document.documentElement.dataset.layout).toBe("command");
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/me/preferences"))?.body).toEqual({ layout: "command" }));
    // Command centre: a dark icon rail.
    const rail = document.querySelector("aside nav[aria-label='Main']");
    expect(rail).toHaveAttribute("data-nav", "dark");
    expect(rail?.className).toContain("w-14");
  });

  it("offers Aurora: a floating sidebar with a colour chip for each screen (ENH-027)", async () => {
    const calls = serve(admin, RENAMED, {
      "PUT /api/v1/me/preferences": json({ theme: "system", ask_verbosity: "balanced", layout: "aurora" }),
    });
    renderAt("/admin/branding");
    const form = await screen.findByRole("form", { name: "Default layout" });
    expect(within(form).getByRole("radio", { name: /Aurora/ })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Aurora" }));
    expect(document.documentElement.dataset.layout).toBe("aurora");
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/me/preferences"))?.body).toEqual({ layout: "aurora" }));
    const aside = document.querySelector("aside[data-shell-aside]");
    expect(aside?.className).not.toContain("border-r");
    const nav = aside?.querySelector("nav[aria-label='Main']");
    expect(nav?.className).not.toContain("w-14");
    const chips = [...(nav?.querySelectorAll("[data-nav-icon]") ?? [])].map((c) => c.getAttribute("data-nav-icon"));
    expect(chips.slice(0, 3)).toEqual(["1", "2", "3"]);
    // The canvas's frame: a white sidebar card and a light top bar when the company keeps the standard colours.
    expect(nav).toHaveAttribute("data-nav", "standard");
    expect(document.querySelector("header[data-nav-place='topbar']")).toHaveAttribute("data-nav", "standard");
  });

  it("colours Aurora's sidebar as the company chose, but keeps its own top bar", async () => {
    serve(admin, { ...RENAMED, nav_colors: { sidebar: "brand", topbar: "tinted" } }, {
      "PUT /api/v1/me/preferences": json({ theme: "system", ask_verbosity: "balanced", layout: "aurora" }),
    });
    renderAt("/admin/branding");
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Aurora" }));
    expect(document.documentElement.dataset.layout).toBe("aurora");
    const nav = document.querySelector("aside[data-shell-aside] nav[aria-label='Main']");
    await waitFor(() => expect(nav).toHaveAttribute("data-nav", "brand"));
    expect(document.querySelector("header[data-nav-place='topbar']")).toHaveAttribute("data-nav", "standard");
  });
});

describe("Branding screen: history (US-151)", () => {
  const version = (v: number, change: string, current = false) => ({
    version: v, change, changed_at: "2026-10-27T09:00:00Z", changed_by: { id: "u1", name: "Ada Admin" }, current,
    app_name: v === 3 ? "Insights Intelligence" : "Tela", tagline: null, logo_sizes: { sidebar: 28, sign_in: 48, export: 28 },
    palette: v === 2 ? { name: "Blue", accent: "#227695" } : null, assets: { logo_light: v === 3, logo_dark: false, favicon: false },
  });

  it("lists the versions and rolls back to one after confirming", async () => {
    const calls = serve(admin, RENAMED, {
      "GET /api/v1/admin/branding/history": json({ items: [version(3, "names", true), version(2, "palette"), version(1, "logo_size")], version: 3, keep: 50 }),
      "POST /api/v1/admin/branding/rollback": json({ ...DEFAULT_BRANDING, palette: BLUE, version: 4, is_default: false }),
    });
    renderAt("/admin/branding");
    const list = await screen.findByRole("list", { name: "Branding versions" });
    const current = within(list).getByRole("listitem", { name: "Version 3" });
    expect(within(current).getByText("Current")).toBeInTheDocument();
    expect(within(current).queryByRole("button", { name: /Roll back/ })).not.toBeInTheDocument();
    const v2 = within(list).getByRole("listitem", { name: "Version 2" });
    expect(within(v2).getByText("Colours changed")).toBeInTheDocument();
    expect(within(v2).getByText("Blue")).toBeInTheDocument();

    await userEvent.click(within(v2).getByRole("button", { name: "Roll back to version 2" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("the Blue colours");
    await userEvent.click(within(dialog).getByRole("button", { name: "Roll back" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/rollback"))?.body).toEqual({ version: 2, expected_version: 3 }));
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(await within(nav).findByText("Tela")).toBeInTheDocument();
  });
});
