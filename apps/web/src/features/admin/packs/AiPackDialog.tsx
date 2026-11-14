import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "../../../api/client";
import { createPack, listPacks } from "../../../api/packs";
import { Button } from "../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Form } from "../../../components/ui/form";
import { asApiError, friendlyMessage } from "../../../lib/api-errors";
import { keyFrom } from "./CreatePackDialog";
import type { CreateDialogProps } from "./createMethods";
import { SelectInput, TextInput } from "./fields";

const NewPack = z.object({
  connection_id: z.string().min(1, "Choose the connection whose schema AI drafts the pack from."),
  template_pack_id: z.string().optional(),
  pack_key: z.string().regex(/^[a-z][a-z0-9_]{1,40}$/, "Use 2 to 41 lower-case letters, digits or _, starting with a letter."),
  title: z.string().min(1, "Enter a title.").max(120, "Keep the title to 120 characters or fewer."),
});
type NewPackValues = z.infer<typeof NewPack>;

/** Create → AI draft (US-237): an empty draft on a connection, then the AI author tab drafts it stage by stage. */
export function AiPackDialog({ open, onOpenChange, onCreated }: CreateDialogProps) {
  const form = useForm<NewPackValues>({ resolver: zodResolver(NewPack), defaultValues: { connection_id: "", template_pack_id: "", pack_key: "", title: "" } });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: { id: string; name: string }[] }>("/api/v1/connections"), enabled: open });
  const packs = useQuery({ queryKey: ["packs"], queryFn: listPacks, enabled: open });
  const released = (packs.data?.items ?? []).flatMap((p) => p.versions.filter((v) => v.status === "released").slice(-1));

  useEffect(() => {
    if (open) form.reset({ connection_id: "", template_pack_id: "", pack_key: "", title: "" });
  }, [open, form]);
  const title = form.watch("title");

  const create = useMutation({
    mutationFn: (v: NewPackValues) => createPack({
      method: "ai", pack_key: v.pack_key, title: v.title,
      options: { connection_id: v.connection_id, ...(v.template_pack_id ? { template_pack_id: v.template_pack_id } : {}) },
    }),
    onSuccess: (out) => {
      const src = out.source as { connection_id?: string; template_pack_id?: string | null } | undefined;
      const q = new URLSearchParams({ tab: "author" });
      if (src?.connection_id) q.set("connection", src.connection_id);
      if (src?.template_pack_id) q.set("template", src.template_pack_id);
      onCreated(out.id, `?${q.toString()}`);
    },
    onError: (e) => {
      const err = asApiError(e, "The pack could not be created.");
      const fieldName = (err.details as { field?: string }).field;
      if (fieldName === "pack_key" || fieldName === "connection_id") form.setError(fieldName, { message: err.message });
      else form.setError("root", { message: friendlyMessage(err) });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New pack drafted by AI</DialogTitle>
          <DialogDescription>
            AI drafts entities, links, measures, dimensions, synonyms and knowledge from the connection's read schema, with the template's items as worked examples. You review every item; nothing goes into the pack until you accept it.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form className="grid gap-3" noValidate onSubmit={form.handleSubmit((v) => create.mutate(v))}>
            <SelectInput form={form} name="connection_id" label="Connection" allowEmpty="Choose a connection"
                         options={(connections.data?.items ?? []).map((c) => [c.id, c.name] as [string, string])} />
            <SelectInput form={form} name="template_pack_id" label="Template" allowEmpty="The PMO pack (default)"
                         description="Its items are the worked examples the AI follows."
                         options={released.map((v) => [v.id, `${v.title} ${v.version}`] as [string, string])} />
            <TextInput form={form} name="title" label="Title" />
            <TextInput form={form} name="pack_key" label="Key"
                       description={`Names the pack everywhere and can't change later.${!form.getValues("pack_key") && title ? ` For example ${keyFrom(title)}.` : ""}`} />
            {form.formState.errors.root && <p role="alert" className="text-small text-status-danger">{form.formState.errors.root.message}</p>}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button type="submit" disabled={create.isPending}>Create and draft</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
