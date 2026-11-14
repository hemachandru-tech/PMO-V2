import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

/** Card: the frame of widgets and KPI cards. Hairline edge, no shadow (shadows are for things that float). */
const Card = forwardRef<HTMLDivElement, ComponentPropsWithoutRef<"div">>(function Card({ className, ...props }, ref) {
  return <div ref={ref} data-slot="card" className={cn("flex flex-col rounded-lg border border-line bg-surface-raised text-ink", className)} {...props} />;
});

const CardHeader = forwardRef<ElementRef<"div">, ComponentPropsWithoutRef<"div">>(function CardHeader({ className, ...props }, ref) {
  return <div ref={ref} data-slot="card-header" className={cn("flex items-start justify-between gap-3 px-4 pt-4", className)} {...props} />;
});

const CardTitle = forwardRef<HTMLHeadingElement, ComponentPropsWithoutRef<"h3">>(function CardTitle({ className, ...props }, ref) {
  return <h3 ref={ref} data-slot="card-title" className={cn("text-heading text-ink", className)} {...props} />;
});

const CardDescription = forwardRef<ElementRef<"p">, ComponentPropsWithoutRef<"p">>(function CardDescription({ className, ...props }, ref) {
  return <p ref={ref} data-slot="card-description" className={cn("text-small text-ink-muted", className)} {...props} />;
});

const CardContent = forwardRef<ElementRef<"div">, ComponentPropsWithoutRef<"div">>(function CardContent({ className, ...props }, ref) {
  return <div ref={ref} data-slot="card-content" className={cn("px-4 py-3", className)} {...props} />;
});

const CardFooter = forwardRef<ElementRef<"div">, ComponentPropsWithoutRef<"div">>(function CardFooter({ className, ...props }, ref) {
  return <div ref={ref} data-slot="card-footer" className={cn("flex items-center gap-2 border-t border-line px-4 py-2 text-small text-ink-muted", className)} {...props} />;
});

export { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle };
