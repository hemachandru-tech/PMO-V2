import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import type { z } from "zod";

import { api } from "../../api/client";
import type { CertKind } from "../../api/govern";
import type { RequestIn } from "../../api/schemas";
import { FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { asApiError } from "../../lib/api-errors";

/**
 * Request certification (US-081) for a personal field or a custom SQL definition: an optional note for the reviewer.
 * The API only accepts items that are shared or on a dashboard (MSG-022) and reuses a pending request.
 */
export function RequestCertification({ item, name, open, onOpenChange }: {
  item: { kind: CertKind; id: string } | null;
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [key] = useState(() => crypto.randomUUID());
  const send = useMutation({
    mutationFn: () => api<{ request_id: string; reused: boolean }>("/api/v1/certification/requests", {
      method: "POST", idempotencyKey: `${key}-${item?.id}`,
      body: { item: item!, note: note.trim() || null } satisfies z.input<typeof RequestIn>,
    }),
    onSuccess: (out) => {
      toast.success(out.reused ? "Your request was already waiting; the note is updated." : `${name} was sent for certification`, {
        description: "An Admin reviews it. You'll get a notification with the decision.",
      });
      void qc.invalidateQueries({ queryKey: ["cert-mine"] });
      onOpenChange(false);
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Request certification</DialogTitle>
          <DialogDescription>
            An Admin reviews the rule, checks it doesn't duplicate a certified field, and may merge it. Until then,
            dashboards that use “{name}” can't be published to a role.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor="cert-note">Note for the reviewer (optional)</Label>
          <Textarea id="cert-note" rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Why it's needed, who asked for it" />
          <span className="text-small text-ink-muted">{note.length} / 1,000</span>
        </div>
        <FormAlert error={send.error ? asApiError(send.error) : null} />
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => send.mutate()} disabled={!item || send.isPending}>Send request</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
