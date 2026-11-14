import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TurnComplete } from "../api/app";
import { AnswerPanel } from "../features/ask/AnswerPanel";
import type { Turn } from "../features/ask/AskChat";

// ENH-027: "How this was answered" beside the Ask conversation in the Aurora layout.

const complete = {
  spec: { measure: { ref: "open_projects" } },
  columns: [{ name: "region", kind: "dimension", label: "Region" }, { name: "n", kind: "measure", label: "Open projects" }],
  definitions: [{ ref: "open_projects", label: "Open projects", plain_rule: "Projects whose current state is Project Created, Active or On Hold.",
    blank_handling: "Projects with a blank current state are not counted.", rule_ref: "RULE-026", format: "integer" }],
  filters_text: "",
  connection: "Dataverse",
  model_version: 5,
} as unknown as TurnComplete;

describe("Ask side panel (ENH-027)", () => {
  it("shows the latest answer's measure, grouping, filters, source and definition", () => {
    const turns: Turn[] = [{ id: "t1", question: "Open projects for each region.", stages: [], chips: [], complete }];
    render(<AnswerPanel turns={turns} />);
    const how = screen.getByRole("region", { name: "How this was answered" });
    expect(within(how).getAllByText("Open projects")).toHaveLength(1);
    expect(within(how).getByText("Region")).toBeInTheDocument();
    expect(within(how).getByText("None")).toBeInTheDocument();
    expect(within(how).getByText("Dataverse · model v5")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Definition" })).toHaveTextContent("Project Created, Active or On Hold");
  });

  it("lists the conditions a Fields + definitions answer used and where each came from (US-254)", () => {
    const fields = {
      certified: false, certified_by: "measure:total_projects", not_certified_because: "“UAE” was matched by partial, not named",
      conditions_used: [
        { phrase: "open", term: "Open projects", rule: "Projects where Current state is one of Project Created, Active, On Hold",
          source: "pack_measure", source_label: "certified measure", how: "definition", score: 1 },
        { phrase: "in uae", rule: "Projects where Region is GCC - UAE", source: "value", source_label: "stored value", how: "partial", score: 0.8 },
      ],
    };
    const turns: Turn[] = [{ id: "t1", question: "How many open projects in UAE", stages: [], chips: [], complete: { ...complete, fields } as TurnComplete }];
    render(<AnswerPanel turns={turns} />);
    const used = screen.getByRole("region", { name: "Conditions used" });
    expect(within(used).getByText("“open” · Open projects")).toBeInTheDocument();
    expect(within(used).getByText("From: certified measure")).toBeInTheDocument();
    expect(within(used).getByText("Projects where Region is GCC - UAE")).toBeInTheDocument();
    expect(within(used).getByText(/Not certified: “UAE” was matched by partial/)).toBeInTheDocument();
  });

  it("waits for a first answer", () => {
    render(<AnswerPanel turns={[]} />);
    expect(screen.getByText(/the measure, grouping and filters behind the answer show here/)).toBeInTheDocument();
  });
});
