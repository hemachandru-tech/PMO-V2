import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Column } from "../api/app";
import { formatDate, formatValue } from "../components/format";
import { ResultTable } from "../components/ResultTable";
import { toOption } from "../components/Chart";
import { TooltipProvider } from "../components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../components/ui/dropdown-menu";

describe("BUG-022: a long menu fits the screen and scrolls", () => {
  it("caps the menu at the room Radix reports and scrolls inside it", () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>Chart type</DropdownMenuTrigger>
        <DropdownMenuContent>{Array.from({ length: 27 }, (_, i) => <DropdownMenuItem key={i}>Type {i}</DropdownMenuItem>)}</DropdownMenuContent>
      </DropdownMenu>,
    );
    const menu = screen.getByRole("menu");
    expect(menu.className).toContain("max-h-(--radix-dropdown-menu-content-available-height)");
    expect(menu.className).toContain("overflow-y-auto");
  });
});

describe("BUG-023: dates and date-times read as words", () => {
  it("formats ISO dates without shifting the day", () => {
    expect(formatDate("2025-01-13T00:00:00")).toBe("Jan 13 2025");
    expect(formatDate("2026-11-24")).toBe("Nov 24 2026");
    expect(formatDate("2025-01-13T14:30:00Z")).toBe("Jan 13 2025, 14:30");
    expect(formatDate("2025-12-31T00:00:00.000+05:30")).toBe("Dec 31 2025");
    expect(formatDate("Atlas")).toBeNull();
    expect(formatDate("2025-13-01")).toBeNull();
    expect(formatValue("2025-01-13T00:00:00")).toBe("Jan 13 2025");
    expect(formatValue("PRJ-2025-01")).toBe("PRJ-2025-01");
  });

  it("shows dates in words on chart axes", () => {
    const columns: Column[] = [
      { name: "month", kind: "period", label: "Month", ref: "dimension:month", data_type: "date", format: null },
      { name: "n", kind: "measure", label: "Projects", ref: "measure:n", data_type: "integer", format: "integer" },
    ];
    const option = toOption({ type: "columns", x: "month", series: ["n"] } as never, columns, [["2025-01-01T00:00:00", 3]]) as { xAxis: { data: string[] } };
    expect(option.xAxis.data).toEqual(["Jan 1 2025"]);
  });
});

describe("ENH-020: a table in colour", () => {
  const columns: Column[] = [
    { name: "project", kind: "dimension", label: "Project", ref: "dimension:project", data_type: "text", format: null },
    { name: "status", kind: "dimension", label: "Status", ref: "dimension:status", data_type: "text", format: null },
    { name: "budget", kind: "measure", label: "Budget", ref: "measure:budget", data_type: "integer", format: "integer" },
  ];
  const rows = [["Alpha", "Green", 120], ["Beta", "Amber", 480], ["Gamma", "Red", 910]];
  const show = (tones?: Record<string, "good" | "warning" | "critical">) => render(
    <TooltipProvider><ResultTable columns={columns} rows={rows} tones={tones} /></TooltipProvider>,
  );

  it("badges status words and tints figures by size", () => {
    const { container } = show({ Green: "good", Amber: "warning", Red: "critical" });
    expect([...container.querySelectorAll("[data-tone]")].map((e) => [e.textContent, e.getAttribute("data-tone")]))
      .toEqual([["Green", "good"], ["Amber", "warning"], ["Red", "critical"]]);
    expect([...container.querySelectorAll("[data-step]")].map((e) => e.getAttribute("data-step"))).toEqual(["1", "3", "5"]);
  });

  it("stays plain without colour", () => {
    const { container } = show();
    expect(container.querySelector("[data-tone], [data-step]")).toBeNull();
  });
});

describe("Banded tables like Excel", () => {
  it("tints the header and every other row of a result table", () => {
    const columns: Column[] = [
      { name: "p", kind: "dimension", label: "Project", ref: "dimension:p", data_type: "text", format: null },
      { name: "n", kind: "measure", label: "Budget", ref: "measure:n", data_type: "integer", format: "integer" },
    ];
    const { container } = render(<TooltipProvider><ResultTable columns={columns} rows={[["A", 1], ["B", 2], ["C", 3]]} /></TooltipProvider>);
    expect(container.querySelector("thead")!.className).toContain("bg-accent-soft");
    const rows = [...container.querySelectorAll("tbody tr")].map((r) => r.className.includes("bg-accent-soft/40"));
    expect(rows).toEqual([false, true, false]);
  });
});
