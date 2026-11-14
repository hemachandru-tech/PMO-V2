import { useRef } from "react";

/**
 * Returns the focus to whatever opened a dialog or sheet when it closes (US-148, found by the keyboard journeys).
 * Radix only restores focus to its own Trigger; most Tela dialogs and sheets are opened from state (a drill, a
 * delete button in a table row), so without this the focus fell back to the page. If the opener is gone (a menu
 * item), Radix's default applies.
 */
export function useReturnFocus(handlers: { onOpenAutoFocus?: (e: Event) => void; onCloseAutoFocus?: (e: Event) => void }) {
  const opener = useRef<HTMLElement | null>(null);
  return {
    onOpenAutoFocus: (e: Event) => {
      const active = document.activeElement;
      opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
      handlers.onOpenAutoFocus?.(e);
    },
    onCloseAutoFocus: (e: Event) => {
      handlers.onCloseAutoFocus?.(e);
      if (e.defaultPrevented) return;
      const el = opener.current;
      opener.current = null;
      if (el?.isConnected) {
        e.preventDefault();
        el.focus();
      }
    },
  };
}
