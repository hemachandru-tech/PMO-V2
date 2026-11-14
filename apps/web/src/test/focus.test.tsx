import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TestAuthProvider } from "../app/auth";
import { setConfigForTests } from "../app/config";
import { PeoplePicker } from "../components/PeoplePicker";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "../components/ui/sheet";

/** US-148 keyboard findings: focus goes back to what opened a dialog or sheet, and Tab leaves the people picker. */
beforeEach(() => setConfigForTests({ apiBaseUrl: "http://api.test", oidcAuthority: "http://kc", oidcClientId: "tela-web" }));
afterEach(() => vi.unstubAllGlobals());

function Opened({ kind }: { kind: "dialog" | "sheet" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Drill to records</Button>
      {kind === "dialog" ? (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent><DialogTitle>Records</DialogTitle><DialogDescription>Rows</DialogDescription></DialogContent>
        </Dialog>
      ) : (
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent><SheetTitle>Records</SheetTitle><SheetDescription>Rows</SheetDescription></SheetContent>
        </Sheet>
      )}
    </>
  );
}

describe("focus return (US-148)", () => {
  for (const kind of ["dialog", "sheet"] as const) {
    it(`returns the focus to the opener when a ${kind} opened from state closes`, async () => {
      const user = userEvent.setup();
      render(<Opened kind={kind} />);
      const opener = screen.getByRole("button", { name: "Drill to records" });
      opener.focus();
      await user.keyboard("{Enter}");
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(opener).toHaveFocus();
    });
  }
});

describe("people picker (US-148)", () => {
  it("lets Tab move on from the name box while suggestions are open", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [{ id: "v", name: "Vera Viewer" }] }), { headers: { "Content-Type": "application/json" } })));
    const user = userEvent.setup();
    render(
      <TestAuthProvider token="tok">
        <QueryClientProvider client={new QueryClient()}>
          <PeoplePicker label="People" onPick={() => undefined} />
          <Button>Cancel</Button>
        </QueryClientProvider>
      </TestAuthProvider>,
    );
    await user.click(screen.getByRole("combobox", { name: "People" }));
    await user.keyboard("Ve");
    expect(await screen.findByRole("option", { name: /Vera Viewer/ })).toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
