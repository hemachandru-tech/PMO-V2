import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, X } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import type { ApiError } from "../api/client";
import { api } from "../api/client";
import type { Share, ShareWarning } from "../api/authoring";
import { ShareCheckIn, ShareIn } from "../api/schemas";
import { applyServerError, asApiError } from "../lib/api-errors";
import { cn } from "../lib/utils";
import { PeoplePicker } from "./PeoplePicker";
import { Alert, FormAlert } from "./ui/alert";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Form, FormField, FormItem, FormMessage } from "./ui/form";

const person = z.object({ id: z.string(), name: z.string() });
const shareForm = z.object({ people: z.array(person) }).superRefine((v, ctx) => {
  // The limits live on ShareIn.with_user_ids, which mirrors the API.
  const ids = ShareIn.shape.with_user_ids.safeParse(v.people.map((p) => p.id));
  if (!ids.success) ctx.addIssue({ code: "custom", path: ["people"], message: ids.error.issues[0]!.message });
});
type ShareForm = z.infer<typeof shareForm>;

/**
 * Share view-only with named colleagues (FEAT-041; US-142): a people combobox, and before anything is shared a warning
 * per person who has no data access on a source (E-11) — they would see empty widgets. They see it in their own data
 * scope; Stop sharing is immediate.
 */
export function ShareDialog({ kind, id, title, onClose }: { kind: "report" | "dashboard" | "widget"; id: string; title: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<ShareForm>({ resolver: zodResolver(shareForm), defaultValues: { people: [] } });
  const picked = form.watch("people");
  const shares = useQuery({
    queryKey: ["shares", kind, id],
    queryFn: () => api<{ items: Share[] }>(`/api/v1/shares?object_type=${kind}&object_id=${id}`),
  });
  const ids = picked.map((p) => p.id);
  const check = useQuery({
    queryKey: ["share-check", kind, id, ids.join(",")],
    queryFn: () => {
      const body: z.input<typeof ShareCheckIn> = { object: { kind, id }, with_user_ids: ids };
      return api<{ warnings: ShareWarning[] }>("/api/v1/shares/check", { method: "POST", body });
    },
    enabled: ids.length > 0,
    placeholderData: (prev) => prev,
  });
  const warningsFor = (uid: string) => (check.data?.warnings ?? []).filter((w) => w.user_id === uid);
  const setPeople = (next: ShareForm["people"]) => form.setValue("people", next, { shouldValidate: form.formState.isSubmitted });

  const share = async (v: ShareForm) => {
    setError(null);
    try {
      const body: z.input<typeof ShareIn> = { object: { kind, id }, with_user_ids: v.people.map((p) => p.id), permission: "view" };
      const out = await api<{ shared_with: number }>("/api/v1/shares", { method: "POST", body });
      toast(`Shared with ${out.shared_with} ${out.shared_with === 1 ? "person" : "people"}. They get a notification.`);
      form.reset({ people: [] });
      void qc.invalidateQueries({ queryKey: ["shares", kind, id] });
    } catch (e) {
      setError(applyServerError(form, e, { fields: { with_user_ids: "people" } }));
    }
  };
  const unshare = async (s: Share) => {
    setError(null);
    try {
      await api(`/api/v1/shares/${s.id}`, { method: "DELETE" });
      toast(`Stopped sharing with ${s.user.name}.`);
      void qc.invalidateQueries({ queryKey: ["shares", kind, id] });
    } catch (e) {
      setError(asApiError(e, "Stopping the share failed."));
    }
  };
  const already = (shares.data?.items ?? []).map((s) => s.user.id);
  const warned = picked.filter((p) => warningsFor(p.id).length > 0);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Share “{title}”</DialogTitle>
          <DialogDescription>View only. Each person sees the data their own scope allows.</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form id="share-form" className="grid gap-3" onSubmit={form.handleSubmit(share)} noValidate>
            <PeoplePicker label="People" placeholder="Find a colleague by name" exclude={[...already, ...ids]}
              onPick={(p) => setPeople([...picked, p])} />
            <FormField control={form.control} name="people" render={() => (
              <FormItem>
                {picked.length > 0 && (
                  <ul className="flex flex-wrap gap-2" aria-label="To share with">
                    {picked.map((p) => {
                      const warn = warningsFor(p.id).length > 0;
                      return (
                        <li key={p.id} className={cn("flex items-center gap-1 rounded-full py-0.5 pl-3 pr-1 text-body",
                          warn ? "bg-status-warn-soft text-status-warn" : "bg-accent-soft text-accent")}>
                          {warn && <CircleAlert aria-label="Has a warning" className="size-3.5" />}
                          {p.name}
                          <button type="button" className="rounded-full p-0.5 hover:bg-surface-raised focus-visible:focus-ring" aria-label={`Remove ${p.name}`}
                            onClick={() => setPeople(picked.filter((x) => x.id !== p.id))}>
                            <X aria-hidden className="size-3.5" />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
                <FormMessage />
              </FormItem>
            )} />
            {warned.length > 0 && (
              <Alert variant="warn">
                <ul className="grid gap-1" aria-live="polite">
                  {warned.map((p) => {
                    const ws = warningsFor(p.id);
                    return <li key={p.id}>{p.name} cannot use {ws.map((w) => w.connection).join(" or ")}, so some widgets will be empty for them. Share anyway, or ask an Admin for access.</li>;
                  })}
                </ul>
              </Alert>
            )}
            <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-md bg-surface-sunken p-3 text-small">
              <dt className="font-medium">They can</dt><dd>View only. They cannot edit or share it on.</dd>
              <dt className="font-medium">They see</dt><dd>Figures in their own scope, not yours. Hidden fields stay hidden.</dd>
              <dt className="font-medium">Where</dt><dd>On their Home, under “Shared with you”. They get a notification.</dd>
            </dl>
            <FormAlert error={error} />
          </form>
        </Form>
        <section className="grid gap-2" aria-labelledby="shared-with">
          <h3 id="shared-with" className="text-body-strong">Already shared with</h3>
          {(shares.data?.items ?? []).length === 0 ? <p className="text-small text-ink-muted">Nobody yet.</p> : (
            <ul className="grid gap-1">
              {shares.data!.items.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-2">
                  <span>{s.user.name} <span className="text-small text-ink-muted">· since {new Date(s.created_at).toLocaleDateString([], { day: "numeric", month: "short" })}</span></span>
                  <Button variant="link" size="sm" className="text-status-danger" onClick={() => void unshare(s)}>Stop sharing</Button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>{picked.length ? "Cancel" : "Close"}</Button>
          <Button type="submit" form="share-form" disabled={form.formState.isSubmitting || picked.length === 0}>
            {picked.length ? `Share with ${picked.length} ${picked.length === 1 ? "person" : "people"}` : "Share"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
