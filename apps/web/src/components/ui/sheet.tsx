import { X } from "lucide-react";
import { Dialog as SheetPrimitive } from "radix-ui";
import { forwardRef, type ComponentProps, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

import { useReturnFocus } from "./return-focus";

function Sheet(props: ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />;
}

const SheetTrigger = forwardRef<ElementRef<typeof SheetPrimitive.Trigger>, ComponentPropsWithoutRef<typeof SheetPrimitive.Trigger>>(function SheetTrigger(props, ref) {
  return <SheetPrimitive.Trigger ref={ref} data-slot="sheet-trigger" {...props} />;
});

const SheetClose = forwardRef<ElementRef<typeof SheetPrimitive.Close>, ComponentPropsWithoutRef<typeof SheetPrimitive.Close>>(function SheetClose(props, ref) {
  return <SheetPrimitive.Close ref={ref} data-slot="sheet-close" {...props} />;
});

/** Sheet: a side panel for definitions, drill-to-records and notifications. */
const SheetContent = forwardRef<ElementRef<typeof SheetPrimitive.Content>, ComponentPropsWithoutRef<typeof SheetPrimitive.Content> & { side?: "right" | "left" }>(function SheetContent({ className, children, side = "right", onOpenAutoFocus, onCloseAutoFocus, ...props }, ref) {
  const focus = useReturnFocus({ onOpenAutoFocus, onCloseAutoFocus });
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay
        data-slot="sheet-overlay"
        className="fixed inset-0 z-50 bg-scrim motion-base data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0"
      />
      <SheetPrimitive.Content
        ref={ref}
        data-slot="sheet-content"
        {...focus}
        className={cn(
          "fixed inset-y-0 z-50 flex h-full w-3/4 flex-col gap-4 border-line bg-surface-raised text-ink shadow-overlay sm:max-w-md",
          "motion-panel data-[state=open]:animate-in data-[state=closed]:animate-out",
          side === "right" ? "right-0 border-l data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right" : "left-0 border-r data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left",
          className,
        )}
        {...props}
      >
        {children}
        <SheetPrimitive.Close className="absolute right-4 top-4 rounded-sm text-ink-muted hover:text-ink focus-visible:focus-ring" aria-label="Close">
          <X className="size-4" />
        </SheetPrimitive.Close>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
});

const SheetHeader = forwardRef<ElementRef<"div">, ComponentPropsWithoutRef<"div">>(function SheetHeader({ className, ...props }, ref) {
  return <div ref={ref} data-slot="sheet-header" className={cn("flex flex-col gap-1.5 border-b border-line p-4 pr-10", className)} {...props} />;
});

const SheetTitle = forwardRef<ElementRef<typeof SheetPrimitive.Title>, ComponentPropsWithoutRef<typeof SheetPrimitive.Title>>(function SheetTitle({ className, ...props }, ref) {
  return <SheetPrimitive.Title ref={ref} data-slot="sheet-title" className={cn("text-title", className)} {...props} />;
});

const SheetDescription = forwardRef<ElementRef<typeof SheetPrimitive.Description>, ComponentPropsWithoutRef<typeof SheetPrimitive.Description>>(function SheetDescription({ className, ...props }, ref) {
  return <SheetPrimitive.Description ref={ref} data-slot="sheet-description" className={cn("text-small text-ink-muted", className)} {...props} />;
});

export { Sheet, SheetClose, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger };
