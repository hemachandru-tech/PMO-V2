import { Toaster as Sonner, type ToasterProps } from "sonner";

import { useTheme } from "@/design/theme";

/** Toasts (design system: Toast), on sonner. Undo lives in the toast action; errors carry the request id. */
function Toaster(props: ToasterProps) {
  const { resolved } = useTheme();
  return (
    <Sonner
      theme={resolved}
      position="bottom-right"
      toastOptions={{
        classNames: {
          toast: "rounded-md! border! border-line! bg-surface-raised! text-ink! shadow-overlay! font-sans!",
          description: "text-ink-muted!",
          actionButton: "bg-accent! text-on-accent!",
          cancelButton: "bg-surface-sunken! text-ink!",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };
