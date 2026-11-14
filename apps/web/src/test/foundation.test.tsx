import { QueryClient } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error -- plain ES module script without type declarations
import { generate } from "../../scripts/gen-tokens.mjs";
import type { Me } from "../api/types";
import { AppRoutes, Providers } from "../app/App";
import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "../components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../components/ui/dropdown-menu";
import { KpiCard } from "../components/ui/kpi-card";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "../components/ui/sheet";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../components/ui/tooltip";
import { StatusBadge } from "../components/ui/status-badge";
import { CHART_SERIES, echartsTheme, tokenColor } from "../design/echartsTheme";
import tokens from "../design/tokens.json";
import { cn } from "../lib/utils";
import { createRef } from "react";

const me: Me = {
  user_id: "u1", display_name: "Nomsa Osei", email: null, tenant_id: "t1", roles: ["viewer"], level: "viewer",
  audience_roles: [], permissions: ["dashboards.view", "ask.use"], scope_summary: [], locale: null, time_zone: null,
  preferences: { theme: "dark" },
};

function mockFetch(route: (url: string, init?: RequestInit) => { status: number; body?: unknown }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = route(url, init);
    return new Response(r.body === undefined ? null : JSON.stringify(r.body), { status: r.status, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}

function renderApp(path = "/") {
  window.history.pushState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <TestAuthProvider token="tok">
      <Providers client={client}>
        <AppRoutes />
      </Providers>
    </TestAuthProvider>,
  );
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
});

describe("design tokens (US-134)", () => {
  it("tokens.css is generated from the current tokens.json", () => {
    const css = readFileSync(join(__dirname, "../design/tokens.css"), "utf8");
    expect(css).toBe(generate(tokens));
  });

  it("defines every colour token for both themes and maps it into Tailwind", () => {
    const css: string = generate(tokens);
    for (const t of tokens.color.tokens) {
      expect(css).toContain(`--tl-${t.name}:`);
      expect(css).toContain(`--color-${t.name}: var(--tl-${t.name});`);
    }
    expect(css).toContain("--color-*: initial;"); // Tailwind's default palette is not available
    expect(css).toContain("--color-primary: var(--tl-accent);");
    expect(css).toMatch(/\[data-theme="dark"\] \{\n {2}color-scheme: dark;\n {2}--tl-surface: #0e1418;/);
  });

  it("keeps type sizes and colours apart when merging classes", () => {
    expect(cn("text-small text-ink", "text-ink-muted")).toBe("text-small text-ink-muted");
    expect(cn("text-body", "text-heading")).toBe("text-heading");
  });
});

describe("ECharts theme (US-134)", () => {
  it("uses the validated chart palette in order, per theme", () => {
    expect((echartsTheme("light").color as string[])[0]).toBe("#0893bf");
    expect(echartsTheme("dark").color).toEqual(CHART_SERIES.map((n) => tokenColor(n, "dark")));
    expect(tokenColor("link", "dark")).toBe(tokenColor("accent", "dark"));
    const tooltip = echartsTheme("dark").tooltip as { backgroundColor: string };
    expect(tooltip.backgroundColor).toBe("#161e23");
  });
});

describe("patterns", () => {
  it("shows a status as an icon and a word, never colour alone", () => {
    render(<StatusBadge status="warn" />);
    expect(screen.getByText("At risk")).toBeInTheDocument();
  });

  it("shows KPI figures exactly as given by the server", () => {
    render(<KpiCard label="Open risks" value="1,284" delta={{ text: "+4 vs last month", direction: "up", good: false }} />);
    expect(screen.getByText("1,284")).toBeInTheDocument();
    expect(screen.getByText("+4 vs last month")).toHaveClass("text-status-danger");
  });
});

describe("React 18 refs (US-134)", () => {
  it("forwards refs, so Radix triggers, portals and presence work without warnings", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref}>Go</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
    render(
      <TooltipProvider>
        <Dialog defaultOpen>
          <DialogTrigger asChild><Button>Open</Button></DialogTrigger>
          <DialogContent><DialogTitle>T</DialogTitle><DialogDescription>D</DialogDescription></DialogContent>
        </Dialog>
        <Sheet defaultOpen>
          <SheetTrigger asChild><Button>Sheet</Button></SheetTrigger>
          <SheetContent><SheetTitle>S</SheetTitle><SheetDescription>D</SheetDescription></SheetContent>
        </Sheet>
        <Popover defaultOpen><PopoverTrigger asChild><Button>P</Button></PopoverTrigger><PopoverContent>Body</PopoverContent></Popover>
        <DropdownMenu defaultOpen modal={false}><DropdownMenuTrigger asChild><Button>M</Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuItem>Item</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
        <Tooltip defaultOpen><TooltipTrigger asChild><Button>Tip</Button></TooltipTrigger><TooltipContent>Hint</TooltipContent></Tooltip>
        <Select defaultOpen defaultValue="a"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="a">A</SelectItem></SelectContent></Select>
      </TooltipProvider>,
    );
    const refWarnings = errors.mock.calls.filter((c) => String(c[0]).includes("cannot be given refs"));
    errors.mockRestore();
    expect(refWarnings).toEqual([]);
  });
});

describe("theme switch (US-134)", () => {
  it("applies the saved theme and saves a new choice for the user", async () => {
    const calls = mockFetch((url, init) => {
      if (url.endsWith("/api/v1/me/preferences") && init?.method === "PUT") return { status: 200, body: JSON.parse(String(init.body)) };
      if (url.endsWith("/api/v1/me")) return { status: 200, body: me };
      return { status: 200, body: { items: [], dashboards: [], unread: 0 } };
    });
    renderApp("/no-such-page");
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(await screen.findByRole("menuitemradio", { name: "Dark" })).toBeChecked();
    await user.click(screen.getByRole("menuitemradio", { name: "Light" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/v1/me/preferences"))).toBe(true));
    const put = calls.find((c) => c.url.endsWith("/api/v1/me/preferences"))!;
    expect(put.init?.method).toBe("PUT");
    expect(JSON.parse(String(put.init?.body))).toEqual({ theme: "light" });
  });
});

describe("route-level code splitting (US-134)", () => {
  it("loads a lazily split route", async () => {
    mockFetch((url) => (url.endsWith("/api/v1/me") ? { status: 200, body: me } : { status: 200, body: { items: [] } }));
    renderApp("/no-such-page");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
