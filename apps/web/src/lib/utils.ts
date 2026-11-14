import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge taught the token type scale, so `text-small` and `text-ink` are not treated as one group, and the
 * control sizes, so `h-control-lg` overrides a component's `h-control-md`.
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: { spacing: ["control-sm", "control-md", "control-lg"] },
    classGroups: {
      "font-size": [{ text: ["display", "title", "heading", "body", "body-strong", "small", "label", "figure-xl", "figure", "code"] }],
      shadow: [{ shadow: ["overlay", "drag", "focus-ring"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
