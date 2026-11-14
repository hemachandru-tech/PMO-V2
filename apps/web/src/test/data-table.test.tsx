import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "../components/ui/tooltip";
import { DataTable, type DataColumn } from "../components/ui/data-table";

interface Project { id: string; name: string; region: string; budget: number | null; updated: string }

const PROJECTS: Project[] = [
  { id: "p1", name: "Atlas", region: "KSA", budget: 1200, updated: "2026-03-01T00:00:00Z" },
  { id: "p2", name: "Borealis", region: "GCC - UAE", budget: 950000, updated: "2026-01-15T00:00:00Z" },
  { id: "p3", name: "Cygnus", region: "KSA", budget: null, updated: "2026-02-10T00:00:00Z" },
];

const COLUMNS: DataColumn<Project>[] = [
  { id: "name", header: "Project", filter: "text", alwaysVisible: true },
  { id: "region", header: "Region", filter: "select" },
  { id: "budget", header: "Budget", numeric: true, format: "currency" },
  { id: "updated", header: "Updated", date: true, hidden: true },
];

function show(props: Partial<Parameters<typeof DataTable<Project>>[0]> = {}) {
  return render(
    <TooltipProvider>
      <DataTable data={PROJECTS} columns={COLUMNS} label="Projects" getRowId={(p) => p.id} rowLabel={(p) => p.name} {...props} />
    </TooltipProvider>,
  );
}

const body = () => screen.getAllByRole("rowgroup")[1]!;
const bodyNames = () => within(body()).getAllByRole("row").map((r) => within(r).queryAllByRole("cell")[0]?.textContent);

describe("DataTable", () => {
  it("formats numbers right-aligned with tabular figures and shows blanks as a dash", () => {
    show();
    const cell = screen.getByRole("cell", { name: "$1,200" });
    expect(cell.className).toMatch(/text-right/);
    expect(cell.className).toMatch(/tabular-nums/);
    expect(screen.getAllByRole("cell", { name: "—" })).toHaveLength(1);
    expect(screen.queryByRole("columnheader", { name: "Updated" })).not.toBeInTheDocument();
    expect(screen.getByText("3 rows")).toBeInTheDocument();
  });

  it("sorts by a column and says so to assistive tech", async () => {
    show();
    const budget = screen.getByRole("button", { name: "Budget" });
    await userEvent.click(budget);
    expect(screen.getByRole("columnheader", { name: /Budget/ })).toHaveAttribute("aria-sort", "ascending");
    expect(bodyNames()).toEqual(["Atlas", "Borealis", "Cygnus"]);
    await userEvent.click(budget);
    expect(screen.getByRole("columnheader", { name: /Budget/ })).toHaveAttribute("aria-sort", "descending");
    expect(bodyNames()).toEqual(["Borealis", "Atlas", "Cygnus"]); // blanks stay last
  });

  it("searches across columns, including formatted numbers, and offers to clear", async () => {
    show();
    await userEvent.type(screen.getByRole("searchbox", { name: "Search projects" }), "950,000");
    expect(bodyNames()).toEqual(["Borealis"]);
    expect(screen.getByText("1 of 3 rows")).toBeInTheDocument();
    await userEvent.clear(screen.getByRole("searchbox"));
    await userEvent.type(screen.getByRole("searchbox"), "nothing like this");
    expect(screen.getByText("No rows match")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(bodyNames()).toHaveLength(3);
  });

  it("filters by column and shows or hides columns", async () => {
    show({ columnFilters: true });
    await userEvent.click(screen.getByRole("button", { name: "Filters" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter Region" }), "KSA");
    expect(bodyNames()).toEqual(["Atlas", "Cygnus"]);
    await userEvent.type(screen.getByRole("textbox", { name: "Filter Project" }), "cyg");
    expect(bodyNames()).toEqual(["Cygnus"]);

    await userEvent.click(screen.getByRole("button", { name: "Columns" }));
    expect(screen.queryByRole("menuitemcheckbox", { name: "Project" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Updated" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("columnheader", { name: "Updated" })).toBeInTheDocument();
  });

  it("selects rows with checkboxes and the keyboard, and activates with Enter", async () => {
    const onSelection = vi.fn();
    const onActivate = vi.fn();
    show({ onSelectionChange: onSelection, onRowActivate: onActivate });
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Atlas" }));
    await waitFor(() => expect(onSelection).toHaveBeenLastCalledWith([PROJECTS[0]]));
    expect(screen.getByRole("row", { name: "Atlas" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();

    // One row is in the tab order; arrows move between rows, Space selects, Enter opens.
    const atlas = screen.getByRole("row", { name: "Atlas" });
    expect(atlas).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("row", { name: "Borealis" })).toHaveAttribute("tabindex", "-1");
    act(() => atlas.focus());
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("row", { name: "Borealis" })).toHaveFocus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(onSelection).toHaveBeenLastCalledWith([PROJECTS[0], PROJECTS[1]]));
    await userEvent.keyboard("{End}{Enter}");
    expect(onActivate).toHaveBeenCalledWith(PROJECTS[2]);

    await userEvent.click(screen.getByRole("checkbox", { name: "Select all rows" }));
    await waitFor(() => expect(onSelection).toHaveBeenLastCalledWith(PROJECTS));
  });

  it("shows loading, error and empty states", () => {
    const { rerender } = show({ loading: true });
    expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
    rerender(<TooltipProvider><DataTable data={[]} columns={COLUMNS} label="Projects" error={<p role="alert">Could not load.</p>} /></TooltipProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load.");
    rerender(<TooltipProvider><DataTable data={[]} columns={COLUMNS} label="Projects" empty={{ title: "No projects yet", body: "Connect a database first." }} /></TooltipProvider>);
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByText("Connect a database first.")).toBeInTheDocument();
  });

  it("virtualises long lists and keeps the full row count for assistive tech", () => {
    const many = Array.from({ length: 5000 }, (_, i) => ({ id: `p${i}`, name: `Project ${i}`, region: "KSA", budget: i, updated: "" }));
    render(<DataTable data={many} columns={COLUMNS} label="Projects" getRowId={(p) => p.id} />);
    const table = screen.getByRole("table");
    expect(table).toHaveAttribute("aria-rowcount", "5001");
    const rendered = within(body()).getAllByRole("row").length;
    expect(rendered).toBeGreaterThan(5);
    expect(rendered).toBeLessThan(100);
    expect(screen.getByText("5,000 rows")).toBeInTheDocument();
  });

  it("leaves sorting and searching to the server when the API pages", () => {
    show({ serverPaged: true, footer: <button>Load more</button> });
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Budget" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load more" })).toBeInTheDocument();
  });
});
