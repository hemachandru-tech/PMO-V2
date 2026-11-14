import { describe, expect, it } from "vitest";

import type { Report } from "../api/authoring";
import { canMap } from "../features/admin/starter/PackMappingStep";
import { changedWidgets } from "../features/reports/ReportCanvasPage";

const report = (items: Record<string, unknown>[]) =>
  ({ id: "r", sections: [{ id: "s", items: items.map((i, n) => ({ id: `i${n}`, kind: "widget", ...i })) }] }) as unknown as Report;

describe("BUG-019: a changed chart type refetches only that widget", () => {
  it("lists widgets whose chart type, query or title changed", () => {
    const before = report([{ widget_id: "w1", visual: "columns" }, { widget_id: "w2", visual: "table" }]);
    expect(changedWidgets(before, report([{ widget_id: "w1", visual: "donut" }, { widget_id: "w2", visual: "table" }]))).toEqual(["w1"]);
    expect(changedWidgets(before, report([{ widget_id: "w1", visual: "columns", spec: { limit: 5 } }, { widget_id: "w2", visual: "table" }]))).toEqual(["w1"]);
    expect(changedWidgets(before, before)).toEqual([]);
    expect(changedWidgets(null, before)).toEqual([]); // a new widget has no cached run to refresh
  });
});

describe("BUG-021: any column can be mapped onto a text pack field", () => {
  it("takes yes/no, number and date columns for text, and a fitting column otherwise", () => {
    const text = { data_type: "text", semantic_type: null };
    for (const t of ["bit", "boolean", "int", "decimal(18,2)", "datetime2", "nvarchar"]) expect(canMap(text, t)).toBe(true);
    expect(canMap({ data_type: "integer", semantic_type: null }, "nvarchar")).toBe(false);
    expect(canMap({ data_type: "integer", semantic_type: null }, "bigint")).toBe(true);
  });
});
