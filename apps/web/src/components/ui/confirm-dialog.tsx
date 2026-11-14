import type { ReactNode } from "react";

import { Button } from "./button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./dialog";

export interface ConfirmRequest {
  title: string;
  /** What happens and to whom, in plain words. */
  body: ReactNode;
  confirm: string;
  /** Red confirm button for changes that take something away. */
  danger?: boolean;
  onConfirm: () => void;
}

/**
 * Confirmation before a destructive change (US-145): says what will happen, the confirm button names the action, and
 * Cancel has the focus so Enter does not destroy anything by accident. Pass `null` to close.
 */
export function ConfirmDialog({ request, onClose, busy }: { request: ConfirmRequest | null; onClose: () => void; busy?: boolean }) {
  return (
    <Dialog open={!!request} onOpenChange={(open) => !open && onClose()}>
      <DialogContent onOpenAutoFocus={(e) => { e.preventDefault(); (e.currentTarget as HTMLElement).querySelector<HTMLButtonElement>("[data-cancel]")?.focus(); }}>
        {request && (
          <>
            <DialogHeader>
              <DialogTitle>{request.title}</DialogTitle>
              <DialogDescription asChild><div>{request.body}</div></DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="ghost" data-cancel onClick={onClose}>Cancel</Button>
              <Button variant={request.danger ? "danger" : "primary"} disabled={busy} onClick={() => { request.onConfirm(); onClose(); }}>{request.confirm}</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
