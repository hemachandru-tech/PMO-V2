import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ShapeGrid as Grid } from "../api/knowledge";
import { ShapeGrid } from "../features/admin/knowledge/GoldExamplesPage";

// US-250: a gold run's outcomes per kind of answer, and per kind × tag.

const c = (total: number, right: number, cw = 0, cl = 0, rf = 0) =>
  ({ total, right, confident_wrong: cw, clarified: cl, refused: rf, wrong: total - right - cl - rf, error: 0 });

const grid: Grid = {
  rows: { number: c(3, 2, 1), list_of_records: c(2, 0, 2) },
  cells: { number: { count: c(1, 1), baseline: c(2, 1, 1) }, list_of_records: { list: c(2, 0, 2), exec186: c(2, 0, 2) } },
  tags: ["baseline", "count", "exec186", "list"],
  total: c(5, 2, 3),
};

describe("Gold run shape grid (US-250)", () => {
  it("shows each kind of answer with its outcomes and the run's totals", () => {
    render(<ShapeGrid grid={grid} />);
    const table = screen.getByRole("table", { name: "By kind of answer" });
    const list = within(table).getByRole("row", { name: /List of records/ });
    expect(within(list).getAllByRole("cell").map((td) => td.textContent)).toEqual(["2", "0", "2", "0", "0"]);
    const all = within(table).getByRole("row", { name: /^All/ });
    expect(within(all).getAllByRole("cell").map((td) => td.textContent)).toEqual(["5", "2", "3", "0", "0"]);
  });

  it("narrows to one tag's examples", () => {
    render(<ShapeGrid grid={grid} />);
    fireEvent.change(screen.getByLabelText("Show one tag's examples"), { target: { value: "count" } });
    const table = screen.getByRole("table", { name: "By kind of answer" });
    expect(within(table).queryByRole("row", { name: /List of records/ })).toBeNull();
    const number = within(table).getByRole("row", { name: /One number/ });
    expect(within(number).getAllByRole("cell")[0]!.textContent).toBe("1");
  });
});
