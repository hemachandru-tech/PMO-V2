import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { createPack } from "../../../api/packs";
import { Button } from "../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Form } from "../../../components/ui/form";
import { asApiError, friendlyMessage } from "../../../lib/api-errors";
import type { CreateDialogProps } from "./createMethods";
import { TextInput } from "./fields";

// CreateIn (API): pack_key ^[a-z][a-z0-9_]{1,40}$, title 1–120.
const NewPack = z.object({
  pack_key: z.string().regex(/^[a-z][a-z0-9_]{1,40}$/, "Use 2 to 41 lower-case letters, digits or _, starting with a letter."),
  title: z.string().min(1, "Enter a title.").max(120, "Keep the title to 120 characters or fewer."),
});
type NewPackValues = z.infer<typeof NewPack>;

export const keyFrom = (title: string) => title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "p_$1").slice(0, 41);

/** Name a new draft: blank, or a copy of `from` ("Clone to edit" on a pack you can't change). */
export function CreatePackDialog({ open, onOpenChange, onCreated, from }: CreateDialogProps & { from?: { id: string; title: string; key: string } }) {
  const form = useForm<NewPackValues>({ resolver: zodResolver(NewPack), defaultValues: { pack_key: "", title: "" } });
  useEffect(() => {
    if (open) form.reset(from ? { title: `${from.title} (copy)`, pack_key: keyFrom(`${from.key}_copy`) } : { title: "", pack_key: "" });
  }, [open, from, form]);
  const create = useMutation({
    mutationFn: (v: NewPackValues) => createPack({ method: from ? "clone" : "blank", ...v, ...(from ? { from_pack_id: from.id } : {}) }),
    onSuccess: (out) => onCreated(out.id),
    onError: (e) => {
      const err = asApiError(e, "The pack could not be created.");
      if ((err.details as { field?: string }).field === "pack_key") form.setError("pack_key", { message: err.message });
      else form.setError("root", { message: friendlyMessage(err) });
    },
  });
  const title = form.watch("title");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{from ? `Clone ${from.title}` : "New blank pack"}</DialogTitle>
          <DialogDescription>{from ? "A draft copy of the pack's entities, measures, reports and knowledge that you can change." : "A draft pack with no entities or measures yet."}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form className="grid gap-3" noValidate onSubmit={form.handleSubmit((v) => create.mutate(v))}>
            <TextInput form={form} name="title" label="Title" />
            <TextInput form={form} name="pack_key" label="Key" description={`Names the pack everywhere and can't change later.${!form.getValues("pack_key") && title ? ` For example ${keyFrom(title)}.` : ""}`} />
            {form.formState.errors.root && <p role="alert" className="text-small text-status-danger">{form.formState.errors.root.message}</p>}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button type="submit" disabled={create.isPending}>{from ? "Clone" : "Create"}</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
