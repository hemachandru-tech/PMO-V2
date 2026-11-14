import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setConfigForTests } from "../app/config";
import { SuggestionsButton, type ChartSuggestion, type SuggestionsOut } from "../components/Suggestions";

const s = (over: Partial<ChartSuggestion>): ChartSuggestion => ({
  id: "a1", kind: "visual", title: "Show as ranked bars", reason: "9 slices is too many for a donut; ranked bars compare them more clearly.",
  ops: [{ op: "set_visual", value: "ranked_bar" }], visual: "ranked_bar", changes: ["Shown as ranked bar"], source: "rules", pattern: null,
  cites: [], ...over,
});

function serve(out: SuggestionsOut) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } })));
}

function renderButton(onApply = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><SuggestionsButton url="/api/v1/ask/turns/t1/suggestions" label="this answer" onApply={onApply} /></QueryClientProvider>);
  return onApply;
}

beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

describe("Chart suggestions (US-104)", () => {
  it("lists suggestions with reasons; Apply hands over the typed ops and Dismiss hides one", async () => {
    serve({ suggestions: [s({}), s({ id: "b2", kind: "measure", title: "Forecast cost next to approved budget", reason: "The gap is what matters.",
      ops: [{ op: "add_measure", ref: "measure:approved_budget_total" }], source: "pattern", pattern: "forecast_vs_budget" })], message: null, source: "rules" });
    const onApply = renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Suggestions for this answer" }));
    const dialog = await screen.findByRole("dialog", { name: "Suggestions for this answer" });
    expect(await within(dialog).findByText(/too many for a donut/)).toBeInTheDocument();
    expect(within(dialog).getByText("House chart pattern")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss: Forecast cost next to approved budget" }));
    expect(within(dialog).queryByText("The gap is what matters.")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply: Show as ranked bars" }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ ops: [{ op: "set_visual", value: "ranked_bar" }] }));
  });

  it("says when the chart looks good as is", async () => {
    serve({ suggestions: [], message: "Looks good as is.", source: "rules" });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Suggestions for this answer" }));
    expect(await screen.findByText("Looks good as is.")).toBeInTheDocument();
  });
});
