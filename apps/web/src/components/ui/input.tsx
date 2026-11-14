import { forwardRef, type ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

const Input = forwardRef<HTMLInputElement, ComponentPropsWithoutRef<"input">>(function Input({ className, type, ...props }, ref) {
  return (
    <input
      ref={ref}
      type={type}
      data-slot="input"
      className={cn(
        "flex h-control-md w-full min-w-0 rounded-md border border-line-strong bg-surface-raised px-3 text-body text-ink",
        "focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-50",
        "aria-invalid:border-status-danger file:border-0 file:bg-transparent file:text-body-strong",
        className,
      )}
      {...props}
    />
  );
});

export { Input };
