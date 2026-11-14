import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";

import { api, type ApiError } from "../../../api/client";
import type { SshKeyItem } from "../../../api/app";
import { SSH_KEY_MAX } from "../../../api/schemas";
import { formatAsOf } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card } from "../../../components/ui/card";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { Field, Form } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { Skeleton } from "../../../components/ui/skeleton";
import { applyServerError, asApiError } from "../../../lib/api-errors";
import { Fingerprint, certificateNote, keyFileText, keyLabel } from "./engines";
import { useAppName } from "../../../app/branding";

const KEYS = ["ssh-keys"];
const tooLarge = "This file is too large to be a key or certificate. Choose the right file.";
const keyForm = z.object({
  name: z.string().trim().min(1, "Name the key, for example Ops bastion.").max(80, "Keep the name to 80 characters or fewer."),
  private_key: z.string().max(SSH_KEY_MAX, tooLarge),
  passphrase: z.string().max(1024, "Keep the passphrase to 1024 characters or fewer."),
  certificate: z.string().max(SSH_KEY_MAX, tooLarge),
});
type KeyForm = z.infer<typeof keyForm>;

/**
 * SSH key library (ENH-004): keys uploaded once and picked on many connections and jump hosts. The private key and
 * its certificate go to the vault and are never shown again; the page shows type, fingerprint, certificate validity
 * and which connections use each key. Replacing a key sends those connections back to draft until they pass a test.
 */
export function SshKeysPage() {
  const appName = useAppName();
  const list = useQuery({ queryKey: KEYS, queryFn: () => api<{ items: SshKeyItem[] }>("/api/v1/admin/ssh-keys") });
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [replacing, setReplacing] = useState<string | null>(null);
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/v1/admin/ssh-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEYS }),
  });
  const items = list.data?.items ?? [];
  return (
    <section className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">SSH keys</h1>
        <p className="text-ink-muted">Keys for SSH tunnels, kept in the vault and shared by connections and jump hosts. {appName} shows only each key's fingerprint.</p>
      </div>
      <KeyEditor key="new" onDone={() => void qc.invalidateQueries({ queryKey: KEYS })} />
      <FormAlert error={list.error ? asApiError(list.error) : remove.error ? asApiError(remove.error) : null} />
      {list.isLoading ? <Skeleton className="h-24" /> : items.length === 0 ? (
        <p className="text-ink-muted">No keys yet. Connections can still use a key uploaded just for them.</p>
      ) : (
        <ul className="grid gap-3" aria-label="SSH keys">
          {items.map((k) => (
            <li key={k.id}>
              <Card className="grid gap-2 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="grid gap-1">
                    <h2 className="text-heading">{k.name}</h2>
                    <p className="text-small text-ink-muted">{keyLabel({ key_type: k.key_type, key_bits: k.key_bits })} <Fingerprint value={k.fingerprint} /></p>
                    {k.certificate && <p className="text-small">{certificateNote(k.certificate)}</p>}
                    <p className="text-small text-ink-muted">Updated {formatAsOf(k.updated_at)}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" size="sm" onClick={() => setReplacing(replacing === k.id ? null : k.id)}>
                      {replacing === k.id ? "Cancel" : "Replace or add certificate"}
                    </Button>
                    <Button variant="ghost" size="sm" disabled={k.used_by.length > 0 || remove.isPending}
                      title={k.used_by.length ? "Choose another key on the connections that use this one first." : undefined}
                      onClick={() => setConfirm({ title: `Delete ${k.name}?`, danger: true, confirm: "Delete key",
                        body: <p>The key is removed from the vault. This cannot be undone.</p>, onConfirm: () => remove.mutate(k.id) })}>
                      Delete
                    </Button>
                  </div>
                </div>
                <p className="text-small">
                  {k.used_by.length === 0 ? "Not used by any connection." : <>Used by {k.used_by.map((c, i) => (
                    <span key={c.id}>{i > 0 && ", "}<Link className="text-link underline-offset-4 hover:underline" to={`/admin/connections/new?id=${c.id}&step=details`}>{c.name}</Link>
                      {c.status === "draft" && <Badge variant="neutral" className="ml-1">needs a test</Badge>}</span>
                  ))}</>}
                </p>
                {replacing === k.id && (
                  <KeyEditor key={k.id} existing={k} onDone={() => { setReplacing(null); void qc.invalidateQueries({ queryKey: KEYS }); }} />
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={remove.isPending} />
    </section>
  );
}

/** Add a key, or (with `existing`) replace it or set its certificate. */
function KeyEditor({ existing, onDone }: { existing?: SshKeyItem; onDone: () => void }) {
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const schema = existing ? keyForm : keyForm.refine((v) => !!v.private_key, { path: ["private_key"], message: "Choose the private key file (.pem, .ppk or OpenSSH)." });
  const form = useForm<KeyForm>({ resolver: zodResolver(schema), mode: "onTouched",
    defaultValues: { name: existing?.name ?? "", private_key: "", passphrase: "", certificate: "" } });
  const submit = async (v: KeyForm) => {
    setError(null);
    setSaved(null);
    const body = { name: v.name.trim(), ...(v.private_key ? { private_key: v.private_key } : {}), ...(v.passphrase ? { passphrase: v.passphrase } : {}),
      ...(v.certificate ? { certificate: v.certificate } : {}) };
    try {
      const out = await api<SshKeyItem>(existing ? `/api/v1/admin/ssh-keys/${existing.id}` : "/api/v1/admin/ssh-keys",
        { method: existing ? "PATCH" : "POST", body });
      form.reset({ name: existing ? out.name : "", private_key: "", passphrase: "", certificate: "" });
      setSaved(existing && v.private_key && out.used_by.length
        ? `Saved. ${out.used_by.length} connection(s) need a passing test before they are used again.` : `Saved ${out.name}.`);
      onDone();
    } catch (err) {
      setError(applyServerError(form, err));
    }
  };
  const c = form.control;
  const fileField = (name: "private_key" | "certificate", label: string, description: string) => (
    <Field control={c} name={name} label={label} description={description} render={(f) => (
      <Input name={f.name} ref={f.ref} onBlur={f.onBlur} type="file" onChange={async (e) => f.onChange(await keyFileText(e.target.files?.[0]))} />
    )} />
  );
  return (
    <Form {...form}>
      <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} aria-label={existing ? `Replace ${existing.name}` : "Add an SSH key"} noValidate>
        {!existing && <h2 className="text-heading">Add a key</h2>}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field control={c} name="name" label="Name" render={(f) => <Input {...f} maxLength={80} />} />
          {fileField("private_key", existing ? "New private key file" : "Private key file",
            existing ? "Leave empty to keep the key. A new key replaces it on every connection that uses it." : "PEM (.pem), PuTTY (.ppk) or OpenSSH.")}
          <Field control={c} name="passphrase" label="Key passphrase" description="Only if the key has one. Not stored."
            render={(f) => <Input {...f} type="password" autoComplete="off" />} />
          {fileField("certificate", "Certificate (optional)", "OpenSSH user certificate for this key, the file ending in -cert.pub.")}
        </div>
        <FormAlert error={error} />
        {saved && <Alert><p>{saved}</p></Alert>}
        <div><Button type="submit" disabled={form.formState.isSubmitting}>{form.formState.isSubmitting ? "Saving…" : existing ? "Save" : "Add key"}</Button></div>
      </form>
    </Form>
  );
}
