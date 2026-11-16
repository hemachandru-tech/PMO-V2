import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

/** Button (design system: Button). One primary action per view; danger only for irreversible actions. */
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl text-body-strong transition-all focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 active:scale-[0.98]",
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent hover:bg-accent-strong shadow-sm shadow-accent/25",
        secondary: "border border-line bg-surface-raised text-ink hover:bg-surface-sunken hover:border-line-strong",
        ghost: "text-ink hover:bg-surface-sunken",
        danger: "bg-status-danger text-on-danger hover:opacity-90 shadow-sm shadow-status-danger/25",
        link: "h-auto px-0 text-link underline-offset-4 hover:text-accent-strong hover:underline",
      },
      size: {
        sm: "h-control-sm px-3 text-xs rounded-lg",
        md: "h-control-md px-4 rounded-xl",
        lg: "h-control-lg px-5 rounded-xl",
        icon: "size-control-md rounded-xl",
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
