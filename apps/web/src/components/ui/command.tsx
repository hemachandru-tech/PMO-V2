import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";
import { forwardRef, type ComponentProps, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./dialog";

/** Command palette (design system: CommandPalette), on cmdk. */
const Command = forwardRef<ElementRef<typeof CommandPrimitive>, ComponentPropsWithoutRef<typeof CommandPrimitive>>(function Command({ className, ...props }, ref) {
  return (
    <CommandPrimitive
      ref={ref}
      data-slot="command"
      className={cn("flex h-full w-full flex-col overflow-hidden rounded-md bg-surface-raised text-ink", className)}
      {...props}
    />
  );
});

function CommandDialog({ title = "Command palette", description = "Search for a page, report or action.", children, ...props }: ComponentProps<typeof Dialog> & { title?: string; description?: string }) {
  return (
    <Dialog {...props}>
      <DialogContent className="overflow-hidden p-0 sm:max-w-xl">
        <DialogTitle className="sr-only">{title}</DialogTitle>
        <DialogDescription className="sr-only">{description}</DialogDescription>
        <Command>{children}</Command>
      </DialogContent>
    </Dialog>
  );
}

const CommandInput = forwardRef<ElementRef<typeof CommandPrimitive.Input>, ComponentPropsWithoutRef<typeof CommandPrimitive.Input>>(function CommandInput({ className, ...props }, ref) {
  return (
    <div data-slot="command-input-wrapper" className="flex h-control-lg items-center gap-2 border-b border-line px-3">
      <Search className="size-4 shrink-0 text-ink-muted" aria-hidden />
      <CommandPrimitive.Input
        ref={ref}
        data-slot="command-input"
        className={cn("flex h-full w-full bg-transparent text-body outline-none disabled:cursor-not-allowed disabled:opacity-50", className)}
        {...props}
      />
    </div>
  );
});

const CommandList = forwardRef<ElementRef<typeof CommandPrimitive.List>, ComponentPropsWithoutRef<typeof CommandPrimitive.List>>(function CommandList({ className, ...props }, ref) {
  return <CommandPrimitive.List ref={ref} data-slot="command-list" className={cn("max-h-80 overflow-y-auto overflow-x-hidden p-1", className)} {...props} />;
});

const CommandEmpty = forwardRef<ElementRef<typeof CommandPrimitive.Empty>, ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>>(function CommandEmpty(props, ref) {
  return <CommandPrimitive.Empty ref={ref} data-slot="command-empty" className="py-6 text-center text-body text-ink-muted" {...props} />;
});

const CommandGroup = forwardRef<ElementRef<typeof CommandPrimitive.Group>, ComponentPropsWithoutRef<typeof CommandPrimitive.Group>>(function CommandGroup({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Group
      ref={ref}
      data-slot="command-group"
      className={cn("text-ink [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-label [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:text-ink-muted", className)}
      {...props}
    />
  );
});

const CommandItem = forwardRef<ElementRef<typeof CommandPrimitive.Item>, ComponentPropsWithoutRef<typeof CommandPrimitive.Item>>(function CommandItem({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Item
      ref={ref}
      data-slot="command-item"
      className={cn(
        "relative flex cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-body outline-none",
        "data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-accent-soft [&_svg]:size-4 [&_svg]:text-ink-muted",
        className,
      )}
      {...props}
    />
  );
});

const CommandShortcut = forwardRef<ElementRef<"span">, ComponentPropsWithoutRef<"span">>(function CommandShortcut({ className, ...props }, ref) {
  return <span ref={ref} data-slot="command-shortcut" className={cn("ml-auto font-mono text-small text-ink-muted", className)} {...props} />;
});

export { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut };
