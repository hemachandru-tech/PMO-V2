import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PrintExport, PRINT_TABLE_ROWS, type ExportPayload } from "../features/print/PrintExport";

// Print route for the PDF renderer (US-096): no sign-in, no API calls; the injected payload only.

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  registerTheme: vi.fn(),
  init: () => ({ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));
vi.mock("echarts/renderers", () => ({ SVGRenderer: {}, CanvasRenderer: {} }));

const M = { name: "open_projects", kind: "measure", label: "Open projects", ref: "measure:open_projects", data_type: "integer", format: "integer" } as const;
const P = { name: "project", kind: "dimension", label: "Project", ref: "", data_type: "text", format: null } as const;
const box = (x: number, y: number, w: number, h: number) => ({ x, y, w, h });

const PAYLOAD: ExportPayload = {
  dashboard: { id: "d1", title: "PMO — Projects", kind: "starter", layout: "tabs", version: 3 },
  scope: "projects: Own CIO", as_of: "2026-10-21 06:00", filters_text: "Region = KSA", exported_at: "2026-10-21 14:05 +03",
  footer: "Region = KSA · Scope: projects: Own CIO", excluded_widgets: 2,
  tabs: [
    { title: "Overview", sections: [{ title: null, kind: "body", items: [
      { kind: "widget", id: "w1", title: "Open projects", visual: "figure", placement: box(0, 0, 3, 2), badges: ["certified"], columns: [M], rows: [[42]], row_count: 1,
        viz: { visual: "figure", chart_spec: { type: "figure", series: ["open_projects"] } },
        kpi: { value: 42, format: "integer", change: { change: 3, change_pct: 7.7, previous_range: "Q2 2026" } } },
      { kind: "text_note", text: "Approved definitions v3.", placement: box(3, 0, 9, 2) },
    ] }] },
    { title: "Delivery", sections: [{ title: "Late", kind: "body", items: [
      { kind: "widget", id: "w2", title: "Late projects", visual: "table", placement: box(0, 0, 12, 5), badges: ["certified"], columns: [P, M],
        rows: Array.from({ length: 60 }, (_, i) => [`PRJ-${i}`, 1]), row_count: 60, viz: { visual: "table", chart_spec: { type: "table" } as never } },
      { kind: "widget", id: "w3", title: "Broken", visual: "columns", placement: box(0, 5, 6, 4), badges: ["certified"], error: "The data source took too long to answer." },
    ] }] },
  ],
};

afterEach(() => {
  delete window.__TELA_PRINT_READY__;
});

describe("print route (US-096)", () => {
  it("renders every tab with the header, KPI change, notes, capped tables and widget errors, then signals ready", async () => {
    Object.defineProperty(document, "fonts", { value: { ready: Promise.resolve() }, configurable: true });
    render(<PrintExport payload={PAYLOAD} />);
    const tabs = screen.getAllByRole("region");
    expect(tabs.map((t) => t.getAttribute("aria-label"))).toEqual(["Overview", "Delivery"]);
    expect(within(tabs[0]!).getByText("Overview · 1 of 2")).toBeInTheDocument();
    expect(within(tabs[0]!).getByText(/Version 3 · Scope: projects: Own CIO · Data as of 2026-10-21 06:00 · Region = KSA/)).toBeInTheDocument();
    expect(screen.getByText("2 widgets are hidden for your role and not included.")).toBeInTheDocument();
    expect(screen.getByText("+3 (+7.7%) vs Q2 2026")).toBeInTheDocument();
    expect(screen.getByText("Approved definitions v3.")).toBeInTheDocument();
    const table = within(tabs[1]!).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(PRINT_TABLE_ROWS + 1);
    expect(screen.getByText(`First ${PRINT_TABLE_ROWS} of 60 rows — the Excel export has them all.`)).toBeInTheDocument();
    expect(screen.getByText("The data source took too long to answer.")).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("light");
    await waitFor(() => expect(window.__TELA_PRINT_READY__).toBe(true));
  });

  it("says so when no payload was injected", () => {
    render(<PrintExport payload={undefined} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing to print.");
  });
});

describe("brand in the export header (US-151/152)", () => {
  it("shows the tenant's logo at its export height and the app name", () => {
    render(<PrintExport payload={{ ...PAYLOAD, branding: { app_name: "Insights Intelligence", logo: "data:image/svg+xml;base64,PHN2Zy8+", logo_height: 36, palette: null } }} />);
    const brand = document.querySelectorAll<HTMLElement>("[data-print-brand]");
    expect(brand).toHaveLength(2); // one per tab page
    expect(brand[0]).toHaveTextContent("Insights Intelligence");
    const img = brand[0]!.querySelector("img")!;
    expect(img).toHaveAttribute("src", "data:image/svg+xml;base64,PHN2Zy8+");
    expect(img.style.height).toBe("36px");
  });

  it("leaves the header as it was for a payload without branding", () => {
    render(<PrintExport payload={PAYLOAD} />);
    expect(document.querySelector("[data-print-brand]")).toBeNull();
  });
});
