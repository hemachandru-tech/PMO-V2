import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ImageUp, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { api, type ApiError } from "../../../api/client";
import { assetUrl, logoUrl, useBranding, type BrandAsset, type Branding, type BrandSlot } from "../../../app/branding";
import { formatAsOf } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { ConfirmDialog, type ConfirmRequest } from "../../../components/ui/confirm-dialog";
import { Field, Form } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { ThemeScope, type ResolvedTheme } from "../../../design/theme";
import { applyServerError, asApiError } from "../../../lib/api-errors";
import { ChartLibraryCard } from "./ChartLibraryCard";
import { HistoryCard } from "./HistoryCard";
import { LogoSizeCard } from "./LogoSizeCard";
import { LayoutCard } from "./LayoutCard";
import { NavColorsCard } from "./NavColorsCard";
import { PaletteCard } from "./PaletteCard";

const namesForm = z.object({
  app_name: z.string().trim().min(2, "Enter 2 to 40 characters.").max(40, "Enter 2 to 40 characters."),
  tagline: z.string().trim().max(60, "Use at most 60 characters."),
});
type NamesForm = z.infer<typeof namesForm>;

const SLOTS: { slot: BrandSlot; label: string; note: string; theme: ResolvedTheme }[] = [
  { slot: "logo_light", label: "Logo", note: "Shown in the sidebar and on sign-in screens. A square or wide mark, at least 64 pixels high.", theme: "light" },
  { slot: "logo_dark", label: "Logo for dark theme", note: "Optional. Without it, the logo above is used in dark theme too.", theme: "dark" },
  { slot: "favicon", label: "Browser tab icon", note: "Optional. A square image of 32 to 512 pixels. Without it, the logo is used.", theme: "light" },
];
const ACCEPT = ".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml";
const KIND: Record<string, string> = { "image/png": "PNG", "image/jpeg": "JPEG", "image/svg+xml": "SVG" };

function describe(a: BrandAsset): string {
  const size = a.width && a.height ? ` · ${a.width} × ${a.height} px` : "";
  return `${KIND[a.content_type] ?? a.content_type}${size} · ${Math.max(1, Math.round(a.bytes / 1024))} KB`;
}

/**
 * Branding (ENH-006, US-149): the app name and tagline, logos for light and dark themes and the browser tab icon;
 * the colour palette made from the logo (US-150), applied across the app (US-151); the logo size per place (US-152);
 * the chart library (US-206); the history with roll back (US-151). Changes apply for everyone as soon as they are saved; a preview shows the sidebar, tab and sign-in screen first.
 */
export function BrandingPage() {
  const branding = useBranding();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const saved = (b: Branding) => qc.setQueryData(["branding"], b);
  const reset = useMutation({
    mutationFn: () => api<Branding>("/api/v1/admin/branding/reset", { method: "POST" }),
    onSuccess: (b) => {
      saved(b);
      toast.success(`Branding reset to ${b.app_name}`);
    },
    onError: (e) => toast.error("The branding could not be reset", { description: asApiError(e).message }),
  });
  return (
    <section className="tl grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Branding</h1>
          <p className="text-ink-muted">Your name and logo on every screen, the browser tab and the sign-in screens, for everyone.</p>
        </div>
        {!branding.is_default && (
          <Button variant="secondary" disabled={reset.isPending} onClick={() => setConfirm({
            title: `Reset to ${branding.defaults.app_name}?`,
            body: `The name goes back to “${branding.defaults.app_name}”, the colours go back to ${branding.defaults.app_name}’s and the uploaded logos and tab icon are removed, and the logo sizes, bar colours and chart library go back to the defaults. Everyone sees the change straight away; the history keeps the current branding, so you can roll back.`,
            confirm: "Reset branding", danger: true, onConfirm: () => reset.mutate(),
          })}>Reset to defaults</Button>
        )}
      </div>
      <NamesCard branding={branding} onSaved={saved} />
      <Card>
        <CardHeader>
          <CardTitle>Logos</CardTitle>
          <CardDescription>PNG, JPEG or SVG. Logos up to 1 MB, the tab icon up to 256 KB. SVGs with scripts or links to other files are refused.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          {SLOTS.map((s) => (
            <AssetTile key={s.slot} {...s} branding={branding} onSaved={saved} onConfirm={setConfirm} />
          ))}
        </CardContent>
      </Card>
      <LogoSizeCard branding={branding} onSaved={saved} />
      <PaletteCard branding={branding} onSaved={saved} />
      <LayoutCard branding={branding} onSaved={saved} />
      <NavColorsCard branding={branding} onSaved={saved} />
      <ChartLibraryCard branding={branding} onSaved={saved} />
      <HistoryCard branding={branding} onSaved={saved} onConfirm={setConfirm} />
      {branding.updated_at && (
        <p className="text-small text-ink-muted">Last changed on {formatAsOf(branding.updated_at)}{branding.updated_by?.name ? ` by ${branding.updated_by.name}` : ""}.</p>
      )}
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} busy={reset.isPending} />
    </section>
  );
}

function NamesCard({ branding, onSaved }: { branding: Branding; onSaved: (b: Branding) => void }) {
  const [error, setError] = useState<ApiError | null>(null);
  const toForm = (b: Branding): NamesForm => ({ app_name: b.app_name, tagline: b.tagline ?? "" });
  const form = useForm<NamesForm>({ resolver: zodResolver(namesForm), mode: "onTouched", defaultValues: toForm(branding) });
  // A save here, elsewhere or the first load: show what is stored now.
  useEffect(() => form.reset(toForm(branding)), [branding.version, branding.app_name, branding.tagline]); // eslint-disable-line react-hooks/exhaustive-deps
  const values = form.watch();
  const preview: Branding = { ...branding, app_name: values.app_name?.trim() || branding.app_name, tagline: values.tagline?.trim() || null };
  const submit = async (v: NamesForm) => {
    setError(null);
    try {
      const out = await api<Branding>("/api/v1/admin/branding", {
        method: "PUT", body: { app_name: v.app_name, tagline: v.tagline || null, expected_version: branding.version },
      });
      onSaved(out);
      toast.success(`Saved — everyone now sees ${out.app_name}`);
    } catch (e) {
      setError(applyServerError(form, e, { fields: { app_name: "app_name", tagline: "tagline" } }));
    }
  };
  const busy = form.formState.isSubmitting;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Name</CardTitle>
        <CardDescription>Shown in the sidebar, the browser tab and on the sign-in screens, and in messages that name the product.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <Form {...form}>
          <form className="grid content-start gap-4" onSubmit={form.handleSubmit(submit)} aria-label="App name" noValidate>
            <Field control={form.control} name="app_name" label="App name" description={`2 to 40 characters. Default: ${branding.defaults.app_name}.`}
              render={(f) => <Input {...f} maxLength={40} autoComplete="off" />} />
            <Field control={form.control} name="tagline" label={<>Tagline <span className="text-ink-muted">(optional)</span></>}
              description={`Next to the name in the sidebar. Default: ${branding.defaults.tagline}.`}
              render={(f) => <Input {...f} maxLength={60} autoComplete="off" />} />
            {error && <FormAlert error={error} />}
            <div className="flex gap-2">
              <Button type="submit" disabled={busy || !form.formState.isDirty}>{busy ? "Saving…" : "Save"}</Button>
              {form.formState.isDirty && <Button type="button" variant="ghost" onClick={() => form.reset(toForm(branding))}>Discard</Button>}
            </div>
          </form>
        </Form>
        <Preview branding={preview} />
      </CardContent>
    </Card>
  );
}

/** How the brand will look in the sidebar and the browser tab, in both themes, before it is saved. */
function Preview({ branding }: { branding: Branding }) {
  const tab = assetUrl(branding.assets.favicon) ?? logoUrl(branding, "light");
  return (
    <div className="grid content-start gap-2" aria-label="Preview">
      <span className="text-label uppercase tracking-wider text-ink-muted">Preview</span>
      <div className="flex min-w-0 items-center gap-2 rounded-md border border-line bg-surface-sunken px-3 py-1.5 text-small">
        <img src={tab} alt="" className="size-4 shrink-0 object-contain" />
        <span className="truncate">Home · {branding.app_name}</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <ThemeScope key={theme} theme={theme} className="max-w-[240px] rounded-md border border-line bg-surface-sunken p-3">
            <div className="flex min-w-0 items-center gap-2 text-ink">
              <img src={logoUrl(branding, theme)} alt="" className="size-7 shrink-0 object-contain" />
              <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 leading-tight">
                <span className="break-words text-[18px] font-semibold tracking-tight">{branding.app_name}</span>
                {branding.tagline && <span className="break-words text-small text-ink-muted">{branding.tagline}</span>}
              </span>
            </div>
            <span className="mt-1 block text-small text-ink-muted">{theme === "light" ? "Light theme" : "Dark theme"}</span>
          </ThemeScope>
        ))}
      </div>
    </div>
  );
}

function AssetTile({ slot, label, note, theme, branding, onSaved, onConfirm }: {
  slot: BrandSlot; label: string; note: string; theme: ResolvedTheme; branding: Branding;
  onSaved: (b: Branding) => void; onConfirm: (r: ConfirmRequest) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const asset = branding.assets[slot];
  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return api<Branding>(`/api/v1/admin/branding/assets/${slot}`, { method: "POST", body });
    },
    onSuccess: (b) => {
      onSaved(b);
      toast.success(`${label} uploaded`);
    },
  });
  const remove = useMutation({
    mutationFn: () => api<Branding>(`/api/v1/admin/branding/assets/${slot}`, { method: "DELETE" }),
    onSuccess: (b) => {
      onSaved(b);
      toast.success(`${label} removed`);
    },
  });
  const shown = slot === "favicon" ? assetUrl(asset) : assetUrl(asset) ?? (slot === "logo_dark" ? logoUrl(branding, "dark") : logoUrl(branding, "light"));
  const id = `brand-${slot}`;
  return (
    <div className="grid content-start gap-2" role="group" aria-labelledby={`${id}-label`}>
      <span id={`${id}-label`} className="font-medium">{label}</span>
      <ThemeScope theme={theme} className="grid h-28 place-items-center rounded-md border border-line bg-surface-sunken p-3">
        {shown ? <img src={shown} alt={`${label} preview`} className="max-h-20 max-w-full object-contain" />
          : <span className="text-small text-ink-muted">The logo is used</span>}
      </ThemeScope>
      <span className="text-small text-ink-muted">{asset ? describe(asset) : slot === "logo_light" ? "Default logo" : "Not set"}</span>
      <span className="text-small text-ink-muted">{note}</span>
      <input ref={input} id={id} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload.mutate(f); }} />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={upload.isPending} onClick={() => input.current?.click()}>
          <ImageUp aria-hidden className="size-4" />{upload.isPending ? "Uploading…" : asset ? "Replace" : "Upload"}
          <span className="sr-only"> {label.toLowerCase()}</span>
        </Button>
        {asset && (
          <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={() => onConfirm({
            title: `Remove the ${label.toLowerCase()}?`,
            body: slot === "logo_light" ? `Everyone sees the ${branding.defaults.app_name} logo instead.` : "The logo is used instead.",
            confirm: "Remove", danger: true, onConfirm: () => remove.mutate(),
          })}>
            <Trash2 aria-hidden className="size-4" />Remove<span className="sr-only"> {label.toLowerCase()}</span>
          </Button>
        )}
      </div>
      {upload.error && <FormAlert error={asApiError(upload.error, "The image could not be uploaded.")} />}
      {remove.error && <FormAlert error={asApiError(remove.error)} />}
    </div>
  );
}
