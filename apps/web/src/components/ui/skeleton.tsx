import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

const Skeleton = forwardRef<ElementRef<"div">, ComponentPropsWithoutRef<"div">>(function Skeleton({ className, ...props }, ref) {
  return <div ref={ref} data-slot="skeleton" aria-hidden className={cn("animate-pulse rounded-md bg-surface-sunken", className)} {...props} />;
});

export { Skeleton };
