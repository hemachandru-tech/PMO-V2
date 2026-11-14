import { Tabs as TabsPrimitive } from "radix-ui";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

const Tabs = forwardRef<ElementRef<typeof TabsPrimitive.Root>, ComponentPropsWithoutRef<typeof TabsPrimitive.Root>>(function Tabs({ className, ...props }, ref) {
  return <TabsPrimitive.Root ref={ref} data-slot="tabs" className={cn("flex flex-col gap-3", className)} {...props} />;
});

const TabsList = forwardRef<ElementRef<typeof TabsPrimitive.List>, ComponentPropsWithoutRef<typeof TabsPrimitive.List>>(function TabsList({ className, ...props }, ref) {
  return <TabsPrimitive.List ref={ref} data-slot="tabs-list" className={cn("flex items-center gap-4 border-b border-line", className)} {...props} />;
});

const TabsTrigger = forwardRef<ElementRef<typeof TabsPrimitive.Trigger>, ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>>(function TabsTrigger({ className, ...props }, ref) {
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      data-slot="tabs-trigger"
      className={cn(
        "-mb-px inline-flex h-control-md shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent text-body-strong text-ink-muted hover:text-ink",
        "focus-visible:focus-ring disabled:opacity-50 data-[state=active]:border-accent data-[state=active]:text-ink",
        className,
      )}
      {...props}
    />
  );
});

const TabsContent = forwardRef<ElementRef<typeof TabsPrimitive.Content>, ComponentPropsWithoutRef<typeof TabsPrimitive.Content>>(function TabsContent({ className, ...props }, ref) {
  return <TabsPrimitive.Content ref={ref} data-slot="tabs-content" className={cn("outline-none focus-visible:focus-ring", className)} {...props} />;
});

export { Tabs, TabsContent, TabsList, TabsTrigger };
