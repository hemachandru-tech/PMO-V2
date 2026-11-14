import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

/** Button (design system: Button). One primary action per view; danger only for irreversible actions. */
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-body-strong transition-colors focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent hover:bg-accent-strong",
        secondary: "border border-line-strong bg-surface-raised text-ink hover:bg-surface-sunken",
        ghost: "text-ink hover:bg-surface-sunken",
        danger: "bg-status-danger text-on-danger hover:opacity-90",
        link: "h-auto px-0 text-link underline-offset-4 hover:text-accent-strong hover:underline",
      },
      size: {
        sm: "h-control-sm px-3",
        md: "h-control-md px-4",
        lg: "h-control-lg px-5",
        icon: "size-control-md",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

// React 18: leaf components forward refs so Radix triggers (asChild) and forms can reach the element.
const Button = forwardRef<
  HTMLButtonElement,
  ComponentPropsWithoutRef<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }
>(function Button({ className, variant, size, asChild = false, ...props }, ref) {
  const Comp = asChild ? Slot.Root : "button";
  return <Comp ref={ref} data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />;
});

export { Button, buttonVariants };
