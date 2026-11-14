import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { MessageSquareText, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api, ApiError } from "../../../api/client";
import type { AccessRequest, GrantConnection, PermissionGrant } from "../../../api/types";
import { PeoplePicker } from "../../../components/PeoplePicker";
import { formatRelative } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Field, Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { StatusBadge } from "../../../components/ui/status-badge";
import { Textarea } from "../../../components/ui/textarea";
import { applyServerError, asApiError } from "../../../lib/api-errors";

const GRANTS = "/api/v1/admin/permission-grants";
const REQUESTS = "/api/v1/admin/permission-requests";
const DEFAULT_DAYS = 90;
const MAX_DAYS = 365;
const CAN_DO = "ask questions in their own words when no certified measure fits";

/** A date `days` from today as YYYY-MM-DD (local), for date inputs. */
export function dayFromNow(days: number, from: Date = new Date()): string {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The end of that day (local) as an ISO timestamp: access lasts through the chosen date. */
const endOfDay = (date: string) => new Date(`${date}T23:59:00`).toISOString();
const longDate = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

const endDate = z.string().min(1, "Choose an end date.")
  .refine((v) => v >= dayFromNow(1), "Choose a date after today.")
  .refine((v) => v <= dayFromNow(MAX_DAYS), "Access can last at most a year.");
const reason = z.string().trim().min(1, "Say why this person needs it.").max(500, "Keep it under 500 characters.");

/**
 * Admin → Access → Free-hand query (ENH-008, US-162): who may ask free-hand questions, on which connection and until
 * when; give, extend or remove that access; approve or decline requests sent from Ask. Grants are per person and per
 * connection (US-161) and never come with a role.
 */
export function FreehandPage() {
  const qc = useQueryClient();
  const [showEnded, setShowEnded] = useState(false);
  const [giving, setGiving] = useState(false);
  const [extending, setExtending] = useState<PermissionGrant | null>(null);
  const [approving, setApproving] = useState<AccessRequest | null>(null);
  const [declining, setDeclining] = useState<AccessRequest | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const grants = useQuery({
    queryKey: ["admin", "permission-grants", { showEnded }],
    queryFn: () => api<{ items: PermissionGrant[] }>(`${GRANTS}?permission=ask.freehand${showEnded ? "&include_ended=true" : ""}`),
  });
  const requests = useQuery({
    queryKey: ["admin", "permission-requests"],
    queryFn: () => api<{ items: AccessRequest[] }>(REQUESTS),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin"] });

  const remove = useMutation({
    mutationFn: (g: PermissionGrant) => api(`${GRANTS}/${g.id}`, { method: "DELETE" }),
    onSuccess: () => { setError(null); void refresh(); },
    onError: (e) => setError(asApiError(e)),
  });
  const askRemove = (g: PermissionGrant) => setConfirm({
    title: `Remove ${g.user.display_name}'s free-hand access on ${g.connection.name}?`,
    body: <p>It stops at once: {g.user.display_name} can only ask questions that certified measures answer. Their earlier answers stay as they are.</p>,
    confirm: "Remove access", danger: true,
    onConfirm: () => remove.mutate(g),
  });

  const rows = grants.data?.items ?? [];
  const pending = (requests.data?.items ?? []).filter((r) => r.status === "pending");

  const columns: DataColumn<PermissionGrant>[] = [
    { id: "person", header: "Person", alwaysVisible: true, value: (g) => `${g.user.display_name} ${g.user.email ?? ""}`, cell: (g) => (
      <span className="grid"><span>{g.user.display_name}</span>{g.user.email && <span className="text-small text-ink-muted">{g.user.email}</span>}</span>
    ) },
    { id: "connection", header: "Connection", filter: "select", value: (g) => g.connection.name },
    { id: "expires", header: "Expires", date: true, value: (g) => g.expires_at, cell: (g) => (
      <span className="grid"><span>{longDate(g.expires_at)}</span>
        {g.status === "active" && <span className="text-small text-ink-muted">{formatRelative(g.expires_at)}</span>}</span>
    ) },
    { id: "granted_by", header: "Granted by", filter: "select", value: (g) => g.granted_by?.display_name ?? "—" },
    { id: "reason", header: "Reason", sortable: false, value: (g) => g.reason },
    { id: "last_used", header: "Last used", date: true, value: (g) => g.last_used_at ?? null,
      cell: (g) => (g.last_used_at ? formatRelative(g.last_used_at) : <span className="text-ink-muted">Never</span>) },
    { id: "status", header: "Status", filter: "select", value: (g) => STATUS_WORD[g.status], cell: (g) => (
      g.status === "active" ? <StatusBadge status="ok">Active</StatusBadge> : <Badge variant="neutral">{STATUS_WORD[g.status]}</Badge>
    ) },
    { id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "", cell: (g) => (g.status === "active" ? (
      <span className="flex gap-3">
        <Button variant="link" size="sm" onClick={() => setExtending(g)} aria-label={`Extend ${g.user.display_name} on ${g.connection.name}`}>Extend</Button>
        <Button variant="link" size="sm" className="text-status-danger" onClick={() => askRemove(g)} disabled={remove.isPending}
          aria-label={`Remove ${g.user.display_name} on ${g.connection.name}`}>Remove</Button>
      </span>
    ) : null) },
  ];

  const requestColumns: DataColumn<AccessRequest>[] = [
    { id: "person", header: "Person", alwaysVisible: true, value: (r) => r.user.display_name },
    { id: "connection", header: "Connection", value: (r) => r.connection.name },
    { id: "note", header: "Their note", sortable: false, value: (r) => r.note ?? "", cell: (r) => r.note ?? <span className="text-ink-muted">No note</span> },
    { id: "asked", header: "Asked", date: true, value: (r) => r.requested_at, cell: (r) => formatRelative(r.requested_at) },
    { id: "actions", header: "Actions", sortable: false, alwaysVisible: true, value: () => "", cell: (r) => (
      <span className="flex gap-3">
        <Button variant="secondary" size="sm" onClick={() => setApproving(r)} aria-label={`Approve ${r.user.display_name} on ${r.connection.name}`}>Approve</Button>
        <Button variant="link" size="sm" className="text-status-danger" onClick={() => setDeclining(r)} aria-label={`Decline ${r.user.display_name} on ${r.connection.name}`}>Decline</Button>
      </span>
    ) },
  ];

  return (
    <div className="grid gap-6">
      <p className="text-ink-muted">
        Free-hand access lets a person {CAN_DO}. It is given per person and per connection, never with a role, and always
        ends on a date you choose. Their answers are labelled <em>Not certified</em>, use only the rows and fields they
        already see, and stay off dashboards and exports until an Admin certifies them.
      </p>
      <FormAlert error={error ?? (grants.error ? asApiError(grants.error) : null)} />

      {pending.length > 0 && (
        <section className="grid gap-2" aria-labelledby="freehand-requests">
          <h2 id="freehand-requests" className="text-heading">Requests <span className="font-mono text-small tabular-nums text-ink-muted">{pending.length}</span></h2>
          <DataTable data={pending} columns={requestColumns} label="Free-hand access requests" getRowId={(r) => r.id} compact />
        </section>
      )}

      <section className="grid gap-2" aria-labelledby="freehand-people">
        <h2 id="freehand-people" className="text-heading">People with access</h2>
        <DataTable data={rows} columns={columns} label="People with free-hand access" getRowId={(g) => g.id}
          loading={grants.isLoading} search columnFilters
          rowClassName={(g) => (g.status === "active" ? undefined : "text-ink-muted")}
          toolbar={(
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-body">
                <Checkbox checked={showEnded} onCheckedChange={(v) => setShowEnded(v === true)} />Show ended
              </label>
              <Button onClick={() => setGiving(true)}><Plus aria-hidden />Give access</Button>
            </div>
          )}
          footer={rows.length > 0 && <span>{new Set(rows.filter((g) => g.status === "active").map((g) => g.user.id)).size} people · {rows.filter((g) => g.status === "active").length} active grants</span>}
          empty={{
            title: "Nobody can ask free-hand questions yet",
            body: `Everyone answers through certified measures. Give access to a person who needs to ${CAN_DO}; requests from Ask also appear here.`,
            action: <Button variant="secondary" onClick={() => setGiving(true)}><MessageSquareText aria-hidden />Give access</Button>,
          }} />
      </section>

      <GiveAccessDialog open={giving} onClose={() => setGiving(false)} onDone={refresh} />
      <ExtendDialog grant={extending} onClose={() => setExtending(null)} onDone={refresh} />
      <ApproveDialog request={approving} onClose={() => setApproving(null)} onDone={refresh} />
      <DeclineDialog request={declining} onClose={() => setDeclining(null)} onDone={refresh} />
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </div>
  );
}

const STATUS_WORD: Record<PermissionGrant["status"], string> = { active: "Active", expired: "Expired", removed: "Removed" };

// --------------------------------------------------------------------------- Give access

const giveForm = z.object({
  userId: z.string().min(1, "Choose a person."),
  userName: z.string(),
  connections: z.array(z.string()).min(1, "Choose at least one connection."),
  expires: endDate,
  reason,
});
type GiveForm = z.infer<typeof giveForm>;

/** Person, connections they can see, end date and reason; then a confirmation that says what they will be able to do. */
function GiveAccessDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [step, setStep] = useState<"form" | "confirm">("form");
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<GiveForm>({
    resolver: zodResolver(giveForm), mode: "onTouched",
    defaultValues: { userId: "", userName: "", connections: [], expires: dayFromNow(DEFAULT_DAYS), reason: "" },
  });
  const userId = form.watch("userId");
  const userName = form.watch("userName");
  const chosen = form.watch("connections");
  const connections = useQuery({
    queryKey: ["admin", "permission-grants", "connections", userId],
    queryFn: () => api<{ items: GrantConnection[] }>(`${GRANTS}/connections?user_id=${userId}`),
    enabled: open && !!userId,
  });
  const close = () => { form.reset(); setStep("form"); setError(null); onClose(); };
  const names = (connections.data?.items ?? []).filter((c) => chosen.includes(c.id)).map((c) => c.name);

  const give = async (v: GiveForm) => {
    setError(null);
    const done: string[] = [];
    try {
      for (const id of v.connections) {
        await api(GRANTS, { method: "POST", body: { user_id: v.userId, connection_id: id, permission: "ask.freehand", reason: v.reason, expires_at: endOfDay(v.expires) } });
        done.push(id);
      }
      onDone();
      close();
    } catch (e) {
      // Some may have gone through: keep only the rest chosen, and go back to the form with the message.
      if (done.length) { form.setValue("connections", v.connections.filter((c) => !done.includes(c))); onDone(); }
      setStep("form");
      setError(applyServerError(form, e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-lg">
        <Form {...form}>
          <form noValidate onSubmit={form.handleSubmit(step === "form" ? async () => setStep("confirm") : give)} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>{step === "form" ? "Give free-hand access" : `Give ${userName} free-hand access?`}</DialogTitle>
              <DialogDescription>
                {step === "form" ? "One person, on the connections they can already see." : "Check what this allows before you confirm."}
              </DialogDescription>
            </DialogHeader>
            {step === "form" ? (
              <>
                <FormField control={form.control} name="userId" render={({ field }) => (
                  <FormItem>
                    <PeoplePicker label="Person" admin single placeholder="Type a name"
                      onPick={(p) => { field.onChange(p.id); form.setValue("userName", p.name); form.setValue("connections", []); }} pickHint="Enter to choose" />
                    <FormMessage />
                  </FormItem>
                )} />
                <FormField control={form.control} name="connections" render={({ field }) => (
                  <FormItem>
                    <fieldset className="grid gap-2">
                      <legend className="mb-1 text-body-strong">Connections</legend>
                      {!userId && <p className="text-small text-ink-muted">Choose a person first.</p>}
                      {userId && connections.isLoading && <p className="text-small text-ink-muted">Loading…</p>}
                      {(userId ? connections.data?.items ?? [] : []).map((c) => {
                        const disabled = !c.visible || !!c.grant;
                        return (
                          <label key={c.id} className="flex items-start gap-2 text-body">
                            <Checkbox className="mt-0.5" disabled={disabled} checked={field.value.includes(c.id)}
                              onCheckedChange={(on) => field.onChange(on ? [...field.value, c.id] : field.value.filter((x) => x !== c.id))} />
                            <span className="grid">
                              <span className={disabled ? "text-ink-muted" : undefined}>{c.name}</span>
                              {!c.visible && <span className="text-small text-ink-muted">{userName} can't see this connection: give one of their roles access first.</span>}
                              {c.grant && <span className="text-small text-ink-muted">Already has access until {longDate(c.grant.expires_at)}.</span>}
                            </span>
                          </label>
                        );
                      })}
                    </fieldset>
                    <FormMessage />
                  </FormItem>
                )} />
                <Field control={form.control} name="expires" label="Ends on" description="At most a year from today."
                  render={(f) => <Input type="date" min={dayFromNow(1)} max={dayFromNow(MAX_DAYS)} {...f} />} />
                <Field control={form.control} name="reason" label="Reason" description="Shown to other Admins and kept in the audit log."
                  render={(f) => <Textarea rows={3} maxLength={500} {...f} />} />
              </>
            ) : (
              <div className="grid gap-2 text-body">
                <p>
                  Until {longDate(endOfDay(form.getValues("expires")))}, {userName} will be able to {CAN_DO} on <strong>{names.join(", ")}</strong>.
                </p>
                <ul className="list-disc pl-5 text-ink-muted">
                  <li>Answers are labelled <em>Not certified · built from your question</em>, with the rule in plain words.</li>
                  <li>They see only the rows and fields their roles already allow.</li>
                  <li>Free-hand answers stay off dashboards, reports and exports until an Admin certifies them.</li>
                </ul>
              </div>
            )}
            <FormAlert error={error} />
            <DialogFooter>
              {step === "form"
                ? <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
                : <Button type="button" variant="ghost" onClick={() => setStep("form")}>Back</Button>}
              <Button type="submit" disabled={form.formState.isSubmitting}>{step === "form" ? "Continue" : "Give access"}</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------- Extend

const extendForm = z.object({ expires: endDate });

function ExtendDialog({ grant, onClose, onDone }: { grant: PermissionGrant | null; onClose: () => void; onDone: () => void }) {
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<z.infer<typeof extendForm>>({ resolver: zodResolver(extendForm), mode: "onTouched", values: { expires: dayFromNow(DEFAULT_DAYS) } });
  const close = () => { setError(null); onClose(); };
  const submit = async (v: z.infer<typeof extendForm>) => {
    try {
      await api(`${GRANTS}/${grant!.id}`, { method: "PATCH", body: { expires_at: endOfDay(v.expires) } });
      onDone();
      close();
    } catch (e) {
      setError(applyServerError(form, e));
    }
  };
  return (
    <Dialog open={!!grant} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent>
        <Form {...form}>
          <form noValidate onSubmit={form.handleSubmit(submit)} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Extend {grant?.user.display_name}'s access</DialogTitle>
              <DialogDescription>{grant ? `On ${grant.connection.name}, now until ${longDate(grant.expires_at)}.` : null}</DialogDescription>
            </DialogHeader>
            <Field control={form.control} name="expires" label="New end date" description="At most a year from today."
              render={(f) => <Input type="date" min={dayFromNow(1)} max={dayFromNow(MAX_DAYS)} {...f} />} />
            <FormAlert error={error} />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>Save end date</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------- Requests

const approveForm = z.object({ expires: endDate, reason });

function ApproveDialog({ request, onClose, onDone }: { request: AccessRequest | null; onClose: () => void; onDone: () => void }) {
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<z.infer<typeof approveForm>>({
    resolver: zodResolver(approveForm), mode: "onTouched",
    values: { expires: dayFromNow(DEFAULT_DAYS), reason: request?.note ?? "" },
  });
  const close = () => { setError(null); onClose(); };
  const submit = async (v: z.infer<typeof approveForm>) => {
    try {
      await api(`${REQUESTS}/${request!.id}/approve`, { method: "POST", body: { expires_at: endOfDay(v.expires), reason: v.reason } });
      onDone();
      close();
    } catch (e) {
      setError(applyServerError(form, e));
    }
  };
  return (
    <Dialog open={!!request} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent>
        <Form {...form}>
          <form noValidate onSubmit={form.handleSubmit(submit)} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Approve {request?.user.display_name}'s request?</DialogTitle>
              <DialogDescription>
                {request ? `${request.user.display_name} will be able to ${CAN_DO} on ${request.connection.name}, seeing only the rows and fields they already see. They are notified.` : null}
              </DialogDescription>
            </DialogHeader>
            <Field control={form.control} name="expires" label="Ends on" description="At most a year from today."
              render={(f) => <Input type="date" min={dayFromNow(1)} max={dayFromNow(MAX_DAYS)} {...f} />} />
            <Field control={form.control} name="reason" label="Reason" render={(f) => <Textarea rows={3} maxLength={500} {...f} />} />
            <FormAlert error={error} />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>Approve</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

const declineForm = z.object({ reason: z.string().trim().min(1, "Say why, so they know what to do next.").max(500, "Keep it under 500 characters.") });

function DeclineDialog({ request, onClose, onDone }: { request: AccessRequest | null; onClose: () => void; onDone: () => void }) {
  const [error, setError] = useState<ApiError | null>(null);
  const form = useForm<z.infer<typeof declineForm>>({ resolver: zodResolver(declineForm), mode: "onTouched", defaultValues: { reason: "" } });
  const close = () => { form.reset(); setError(null); onClose(); };
  const submit = async (v: z.infer<typeof declineForm>) => {
    try {
      await api(`${REQUESTS}/${request!.id}/decline`, { method: "POST", body: v });
      onDone();
      close();
    } catch (e) {
      setError(applyServerError(form, e));
    }
  };
  return (
    <Dialog open={!!request} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent>
        <Form {...form}>
          <form noValidate onSubmit={form.handleSubmit(submit)} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Decline {request?.user.display_name}'s request?</DialogTitle>
              <DialogDescription>{request ? `For ${request.connection.name}. They are notified with your reason.` : null}</DialogDescription>
            </DialogHeader>
            <FormField control={form.control} name="reason" render={({ field }) => (
              <FormItem>
                <FormLabel>Reason</FormLabel>
                <FormControl><Textarea rows={3} maxLength={500} placeholder="e.g. The Past planning measure already answers this." {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )} />
            <FormAlert error={error} />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
              <Button type="submit" variant="danger" disabled={form.formState.isSubmitting}>Decline</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
