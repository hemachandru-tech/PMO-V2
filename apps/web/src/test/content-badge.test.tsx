import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ContentBadge } from "../components/ContentBadge";
import { TooltipProvider } from "../components/ui/tooltip";

describe("content badges as icons", () => {
  it("shows Certified, Not certified and Custom SQL as icons named in a tooltip", async () => {
    render(
      <TooltipProvider delayDuration={0}>
        <ContentBadge badge="certified" />
        <ContentBadge badge="not_certified" />
        <ContentBadge badge="custom_sql" />
      </TooltipProvider>,
    );
    const certified = screen.getByLabelText("Certified: Every measure used is certified");
    expect(certified.querySelector("svg")).not.toBeNull();
    expect(screen.getByLabelText(/^Not certified · built from your question: /)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Custom SQL: /)).toBeInTheDocument();
    await userEvent.hover(certified);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Every measure used is certified");
  });

  it("keeps the words where nothing can be hovered, and for draft", () => {
    render(
      <TooltipProvider>
        <ContentBadge badge="certified" text />
        <ContentBadge badge="draft" />
      </TooltipProvider>,
    );
    expect(screen.getByText("Certified")).toHaveAttribute("title", "Every measure used is certified");
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });
});
