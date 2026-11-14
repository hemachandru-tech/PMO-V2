import { useEffect, useState } from "react";
import type { FieldValues, Path, UseFormReturn } from "react-hook-form";

import { Checkbox } from "../../../components/ui/checkbox";
import { Field } from "../../../components/ui/form";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { Textarea } from "../../../components/ui/textarea";
import { joinList, splitList } from "./yaml";

/** Form controls of the Pack Studio's side panels (US-232), on the design system's `Field`. */
interface Base<T extends FieldValues> {
  form: UseFormReturn<T>;
  name: Path<T>;
  label: string;
  description?: string;
  className?: string;
}

export function TextInput<T extends FieldValues>({ form, name, label, description, className, multiline }: Base<T> & { multiline?: boolean }) {
  return (
    <Field control={form.control} name={name} label={label} description={description} className={className}
           render={(f) => multiline
             ? <Textarea {...f} value={(f.value as string | null) ?? ""} rows={3} />
             : <Input {...f} value={(f.value as string | null) ?? ""} />} />
  );
}

/** A list typed on one line, comma-separated ("tpg_project, projects"). */
export function ListInput<T extends FieldValues>({ form, name, label, description, className }: Base<T>) {
  return (
    <Field control={form.control} name={name} label={label} description={description ?? "Separate with commas."} className={className}
           render={(f) => <CommaList value={f.value as string[] | undefined} onChange={f.onChange} onBlur={f.onBlur} name={f.name} id={undefined} />} />
  );
}

function CommaList({ value, onChange, ...rest }: { value: string[] | undefined | null; onChange: (v: string[]) => void; onBlur: () => void; name: string; id?: string }) {
  const [text, setText] = useState(joinList(value));
  useEffect(() => { // a reset (the YAML view) replaces the list: show it unless it's what was just typed
    if (joinList(splitList(text)) !== joinList(value)) setText(joinList(value));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Input {...rest} value={text} onChange={(e) => { setText(e.target.value); onChange(splitList(e.target.value)); }} />;
}

export function SelectInput<T extends FieldValues>({ form, name, label, description, className, options, allowEmpty }:
  Base<T> & { options: readonly (string | [string, string])[]; allowEmpty?: string }) {
  return (
    <Field control={form.control} name={name} label={label} description={description} className={className}
           render={(f) => (
             <NativeSelect {...f} value={(f.value as string | null) ?? ""} onChange={(e) => f.onChange(e.target.value || undefined)}>
               {allowEmpty !== undefined && <option value="">{allowEmpty}</option>}
               {options.map((o) => {
                 const [v, l] = Array.isArray(o) ? o : [o, o];
                 return <option key={v} value={v}>{l}</option>;
               })}
             </NativeSelect>
           )} />
  );
}

export function CheckInput<T extends FieldValues>({ form, name, label, description, className }: Base<T>) {
  return (
    <Field control={form.control} name={name} label={label} description={description} className={className}
           render={(f) => <Checkbox checked={!!f.value} onCheckedChange={(c) => f.onChange(c === true)} onBlur={f.onBlur} name={f.name} ref={f.ref} />} />
  );
}

export function NumberInput<T extends FieldValues>({ form, name, label, description, className, step }: Base<T> & { step?: number }) {
  return (
    <Field control={form.control} name={name} label={label} description={description} className={className}
           render={(f) => <Input {...f} type="number" step={step} value={(f.value as number | undefined) ?? ""}
                                 onChange={(e) => f.onChange(e.target.value === "" ? undefined : Number(e.target.value))} />} />
  );
}
