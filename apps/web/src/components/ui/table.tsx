import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

/** Plain table primitives; the shared sortable DataTable (TanStack Table) builds on these in US-136. */
const Table = forwardRef<ElementRef<"table">, ComponentPropsWithoutRef<"table">>(function Table({ className, ...props }, ref) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table ref={ref} data-slot="table" className={cn("w-full caption-bottom text-body", className)} {...props} />
    </div>
  );
});

const TableHeader = forwardRef<ElementRef<"thead">, ComponentPropsWithoutRef<"thead">>(function TableHeader({ className, ...props }, ref) {
  return <thead ref={ref} data-slot="table-header" className={cn("bg-surface-sunken", className)} {...props} />;
});

const TableBody = forwardRef<ElementRef<"tbody">, ComponentPropsWithoutRef<"tbody">>(function TableBody({ className, ...props }, ref) {
  return <tbody ref={ref} data-slot="table-body" className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
});

const TableRow = forwardRef<ElementRef<"tr">, ComponentPropsWithoutRef<"tr">>(function TableRow({ className, ...props }, ref) {
  return <tr ref={ref} data-slot="table-row" className={cn("border-b border-line hover:bg-surface-sunken/60 data-[state=selected]:bg-accent-soft", className)} {...props} />;
});

const TableHead = forwardRef<ElementRef<"th">, ComponentPropsWithoutRef<"th">>(function TableHead({ className, ...props }, ref) {
  return <th ref={ref} data-slot="table-head" className={cn("h-control-md whitespace-nowrap px-3 text-left align-middle text-small font-medium text-ink-muted", className)} {...props} />;
});

/** Numeric cells: right-aligned, mono, tabular figures (NFR-022). */
const TableCell = forwardRef<ElementRef<"td">, ComponentPropsWithoutRef<"td"> & { numeric?: boolean }>(function TableCell({ className, numeric, ...props }, ref) {
  return (
    <td
      ref={ref}
      data-slot="table-cell"
      className={cn("h-control-md px-3 align-middle", numeric && "text-right font-mono text-figure tabular-nums", className)}
      {...props}
    />
  );
});

const TableCaption = forwardRef<ElementRef<"caption">, ComponentPropsWithoutRef<"caption">>(function TableCaption({ className, ...props }, ref) {
  return <caption ref={ref} data-slot="table-caption" className={cn("mt-3 text-small text-ink-muted", className)} {...props} />;
});

export { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow };
