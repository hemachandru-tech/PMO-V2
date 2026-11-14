import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Alert } from "../../../../components/ui/alert";
import { Button } from "../../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../../components/ui/card";
import { Form } from "../../../../components/ui/form";
import { asApiError, friendlyMessage } from "../../../../lib/api-errors";
import { NumberInput, SelectInput, TextInput } from "../fields";
import { usePack } from "../PackContext";
import type { PackTab } from "./registry";

const Overview = z.object({
  title: z.string().min(1, "Enter a title.").max(120, "Keep the title to 120 characters or fewer."),
  pack_version: z.string().regex(/^[0-9]+\.[0-9]+\.[0-9]+$/, "Use a version like 1.0.0."),
  primary_entity: z.string().optional(),
  min_confidence: z.number().min(0, "Use a share between 0 and 1.").max(1, "Use a share between 0 and 1.").optional(),
});
type OverviewValues = z.infer<typeof Overview>;

/** Overview (US-232): key, title, version, and the signature that detects the pack on a source. */
function OverviewTab() {
  const { pack, readOnly, save, saving } = usePack();
  const m = pack.manifest;
  const form = useForm<OverviewValues>({
    resolver: zodResolver(Overview),
    defaultValues: { title: m.title, pack_version: m.pack_version, primary_entity: m.signature.primary_entity ?? "",
                     min_confidence: m.signature.min_confidence },
  });
  const onSubmit = form.handleSubmit(async (v) => {
    try {
      await save({ title: v.title, pack_version: v.pack_version,
                   signature: { ...m.signature, primary_entity: v.primary_entity || undefined, min_confidence: v.min_confidence } });
      toast.success("Overview saved");
    } catch (e) {
      toast.error(friendlyMessage(asApiError(e, "The overview could not be saved.")));
    }
  });
  const { errors, warnings } = pack.checks;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <CardTitle>Pack</CardTitle>
          <CardDescription>Key <span className="font-mono">{pack.pack_key}</span> names the pack everywhere and can't change.</CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void onSubmit(); }} noValidate>
              <fieldset disabled={readOnly} className="contents">
                <TextInput form={form} name="title" label="Title" className="sm:col-span-2" />
                <TextInput form={form} name="pack_version" label="Version" description="Semantic version: major.minor.patch." />
                <SelectInput form={form} name="primary_entity" label="Primary entity" allowEmpty="Not set"
                             options={m.entities.map((e) => [e.key, e.label] as [string, string])}
                             description="A source matches when this entity's required fields are found." />
                <NumberInput form={form} name="min_confidence" label="Lowest match confidence" step={0.01}
                             description="Share of the pack's fields a source must have (0 to 1)." />
              </fieldset>
              {!readOnly && <div className="sm:col-span-2"><Button type="submit" disabled={saving}>Save overview</Button></div>}
            </form>
          </Form>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Checks</CardTitle><CardDescription>Run on every save; errors block saving.</CardDescription></CardHeader>
        <CardContent className="grid gap-2">
          {errors.length === 0 && warnings.length === 0 && <p className="text-ink-muted">No problems found.</p>}
          {errors.length > 0 && <Alert>{errors.length} {errors.length === 1 ? "error" : "errors"}</Alert>}
          <ul aria-label="Check results" className="grid gap-1 text-small">
            {[...errors.map((c) => ({ ...c, kind: "Error" })), ...warnings.map((c) => ({ ...c, kind: "Warning" }))].slice(0, 30).map((c, i) => (
              <li key={i}><span className="font-medium">{c.kind}</span> <span className="font-mono">{c.path}</span>: {c.message}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

export const overviewTab: PackTab = { key: "overview", label: "Overview", Component: OverviewTab };
