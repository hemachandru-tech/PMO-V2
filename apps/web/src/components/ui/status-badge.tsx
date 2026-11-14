import { CircleAlert, CircleCheck, OctagonAlert } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const STATUS = {
  ok: { icon: CircleCheck, className: "bg-status-ok-soft text-status-ok", word: "On track" },
  warn: { icon: CircleAlert, className: "bg-status-warn-soft text-status-warn", word: "At risk" },
  danger: { icon: OctagonAlert, className: "bg-status-danger-soft text-status-danger", word: "Off track" },
} as const;

export type Status = keyof typeof STATUS;

/** RAG status (design system: StatusBadge): always an icon and a word, never colour alone (NFR-021). */
export function StatusBadge({ status, children, className, ...props }: ComponentProps<"span"> & { status: Status }) {
  const s = STATUS[status];
  const Icon = s.icon;
  return (
    <span
      data-slot="status-badge"
      className={cn("inline-flex w-fit items-center gap-1 rounded-sm px-1.5 py-0.5 text-small font-medium", s.className, className)}
      {...props}
    >
      <Icon aria-hidden className="size-3.5" />
      {children ?? s.word}
    </span>
  );
}
