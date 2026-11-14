import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

/** Badge (design system: Badge). Certified, personal, stale and as-of badges stay visible next to figures (NFR-021). */
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center gap-1 whitespace-nowrap rounded-sm px-1.5 py-0.5 text-small font-medium [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "bg-surface-sunken text-ink-muted",
        certified: "bg-accent-soft text-accent",
        personal: "bg-personal-soft text-personal",
        stale: "bg-status-warn-soft text-status-warn",
        outline: "border border-line text-ink-muted",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

const Badge = forwardRef<HTMLSpanElement, ComponentPropsWithoutRef<"span"> & VariantProps<typeof badgeVariants>>(
  function Badge({ className, variant, ...props }, ref) {
    return <span ref={ref} data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
  },
);

export { Badge, badgeVariants };
