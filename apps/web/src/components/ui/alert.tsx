import { cva, type VariantProps } from "class-variance-authority";
import { CircleAlert, Info, OctagonAlert } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

import type { ApiError } from "@/api/client";
import { friendlyMessage } from "@/lib/api-errors";
import { cn } from "@/lib/utils";

const alertVariants = cva("grid grid-cols-[auto_minmax(0,1fr)] items-start break-words gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-body", {
  variants: {
    variant: {
      info: "border-line bg-surface-sunken text-ink",
      warn: "border-status-warn/40 bg-status-warn-soft text-ink",
      danger: "border-status-danger/40 bg-status-danger-soft text-ink",
    },
  },
  defaultVariants: { variant: "info" },
});

const ICONS = { info: Info, warn: CircleAlert, danger: OctagonAlert };

/** Alert: a message about the whole form or page, with an icon and words (never colour alone). */
const Alert = forwardRef<HTMLDivElement, ComponentPropsWithoutRef<"div"> & VariantProps<typeof alertVariants>>(function Alert(
  { className, variant, children, ...props }, ref,
) {
  const Icon = ICONS[variant ?? "info"];
  return (
    <div ref={ref} data-slot="alert" role={variant === "danger" ? "alert" : "status"} className={cn(alertVariants({ variant }), className)} {...props}>
      <Icon aria-hidden className={cn("mt-0.5 size-4", variant === "danger" ? "text-status-danger" : variant === "warn" ? "text-status-warn" : "text-ink-muted")} />
      <div className="grid gap-1">{children}</div>
    </div>
  );
});

/** The form-level alert for an API error: plain message, and the request id to quote to support. */
function FormAlert({ error, className, children }: { error: ApiError | null | undefined; className?: string; children?: React.ReactNode }) {
  if (!error) return null;
  return (
    <Alert variant="danger" className={className}>
      <p className="text-body-strong">{friendlyMessage(error)}</p>
      {children}
      {error.requestId && <p className="text-small break-all text-ink-muted">Request id {error.requestId}</p>}
    </Alert>
  );
}

export { Alert, FormAlert };
