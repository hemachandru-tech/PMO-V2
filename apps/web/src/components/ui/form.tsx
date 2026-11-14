import { OctagonAlert } from "lucide-react";
import { Slot } from "radix-ui";
import {
  createContext,
  forwardRef,
  useContext,
  useId,
  type ComponentPropsWithoutRef,
  type ElementRef,
  type ReactNode,
} from "react";
import {
  Controller,
  FormProvider,
  useFormContext,
  useFormState,
  type ControllerProps,
  type ControllerRenderProps,
  type FieldPath,
  type FieldValues,
} from "react-hook-form";

import { cn } from "@/lib/utils";

import { Label } from "./label";

/**
 * Forms (design system: Field; US-135): React Hook Form + Zod, shadcn/ui `Form` pattern. Each control gets its label,
 * help text and error wired through ids and `aria-describedby`/`aria-invalid`, so screen readers announce all three.
 */
const Form = FormProvider;

type FieldContextValue = { name: string };
const FormFieldContext = createContext<FieldContextValue | null>(null);
const FormItemContext = createContext<{ id: string } | null>(null);

function FormField<TValues extends FieldValues, TName extends FieldPath<TValues>>(props: ControllerProps<TValues, TName>) {
  return (
    <FormFieldContext.Provider value={{ name: props.name }}>
      <Controller {...props} />
    </FormFieldContext.Provider>
  );
}

function useFormField() {
  const field = useContext(FormFieldContext);
  const item = useContext(FormItemContext);
  const { getFieldState } = useFormContext();
  const formState = useFormState({ name: field?.name });
  if (!field || !item) throw new Error("useFormField must be used inside <FormField> and <FormItem>.");
  const state = getFieldState(field.name, formState);
  return {
    name: field.name,
    formItemId: `${item.id}-control`,
    descriptionId: `${item.id}-description`,
    messageId: `${item.id}-message`,
    ...state,
  };
}

const FormItem = forwardRef<HTMLDivElement, ComponentPropsWithoutRef<"div">>(function FormItem({ className, ...props }, ref) {
  const id = useId();
  return (
    <FormItemContext.Provider value={{ id }}>
      <div ref={ref} data-slot="form-item" className={cn("grid content-start gap-1", className)} {...props} />
    </FormItemContext.Provider>
  );
});

const FormLabel = forwardRef<ElementRef<typeof Label>, ComponentPropsWithoutRef<typeof Label>>(function FormLabel({ className, ...props }, ref) {
  const { formItemId, error } = useFormField();
  return <Label ref={ref} data-slot="form-label" data-error={!!error} htmlFor={formItemId} className={className} {...props} />;
});

const FormControl = forwardRef<ElementRef<typeof Slot.Root>, ComponentPropsWithoutRef<typeof Slot.Root>>(function FormControl(props, ref) {
  const { formItemId, descriptionId, messageId, error } = useFormField();
  return (
    <Slot.Root
      ref={ref}
      data-slot="form-control"
      id={formItemId}
      aria-describedby={error ? `${descriptionId} ${messageId}` : descriptionId}
      aria-invalid={!!error}
      {...props}
    />
  );
});

const FormDescription = forwardRef<HTMLParagraphElement, ComponentPropsWithoutRef<"p">>(function FormDescription({ className, ...props }, ref) {
  const { descriptionId } = useFormField();
  return <p ref={ref} data-slot="form-description" id={descriptionId} className={cn("text-small text-ink-muted", className)} {...props} />;
});

/** The field's error: an octagon icon and a sentence that says how to fix it. Always rendered, so the id resolves. */
const FormMessage = forwardRef<HTMLParagraphElement, ComponentPropsWithoutRef<"p">>(function FormMessage({ className, children, ...props }, ref) {
  const { error, messageId } = useFormField();
  const body = error?.message ?? children;
  return (
    <p ref={ref} data-slot="form-message" id={messageId} aria-live="polite"
      className={cn("flex items-start gap-1 text-small text-status-danger empty:hidden", className)} {...props}>
      {body ? <><OctagonAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />{body}</> : null}
    </p>
  );
});

/**
 * One labelled field: label, control, help text and error. `render` receives React Hook Form's field props;
 * spread them on an `Input`, `Textarea` or `NativeSelect`.
 */
function Field<TValues extends FieldValues, TName extends FieldPath<TValues>>({
  name, control, label, description, className, render,
}: {
  name: TName;
  control: ControllerProps<TValues, TName>["control"];
  label: ReactNode;
  description?: ReactNode;
  className?: string;
  render: (field: ControllerRenderProps<TValues, TName>) => React.ReactElement;
}) {
  return (
    <FormField
      name={name}
      control={control}
      render={({ field }) => (
        <FormItem className={className}>
          <FormLabel>{label}</FormLabel>
          <FormControl>{render(field)}</FormControl>
          {description && <FormDescription>{description}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

export { Field, Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage, useFormField };
