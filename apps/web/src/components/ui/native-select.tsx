import { ChevronDown } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Native `<select>` styled like `Input` (shadcn/ui NativeSelect). Forms use it for short option lists: it works with
 * React Hook Form's `register`/`field` props as is, keeps the platform picker on touch devices, and allows an empty
 * "Choose…" option, which Radix Select does not. Radix `Select` stays for menus that need rich items.
 */
const NativeSelect = forwardRef<HTMLSelectElement, ComponentPropsWithoutRef<"select">>(function NativeSelect({ className, children, ...props }, ref) {
  return (
    <div data-slot="native-select-wrapper" className={cn("relative w-full", className)}>
      <select
        ref={ref}
        data-slot="native-select"
        className={cn(
          "h-control-md w-full min-w-0 appearance-none rounded-md border border-line-strong bg-surface-raised pl-3 pr-8 text-body text-ink",
          "focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-status-danger",
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-muted" />
    </div>
  );
});

export { NativeSelect };
