import { dump, load } from "js-yaml";

/** The YAML view of a pack item (US-232): the same text as in `manifest.yaml`, so an item can be copied either way. */
export function toYaml(value: unknown): string {
  return dump(value, { indent: 2, lineWidth: 120, noRefs: true, sortKeys: false });
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

/** Parses YAML typed by the person; a mapping is expected (one item). */
export function fromYaml<T = Record<string, unknown>>(text: string): Parsed<T> {
  try {
    const value = load(text);
    if (value === null || typeof value !== "object" || Array.isArray(value)) return { ok: false, error: "Write one item as a mapping (key: value lines)." };
    return { ok: true, value: value as T };
  } catch (e) {
    const msg = e instanceof Error ? e.message.split("\n")[0]! : "This isn't valid YAML.";
    return { ok: false, error: msg };
  }
}

/** "a, b , c" ⇄ ["a", "b", "c"] for list fields typed on one line. */
export const splitList = (text: string): string[] => text.split(",").map((s) => s.trim()).filter(Boolean);
export const joinList = (items: readonly string[] | null | undefined): string => (items ?? []).join(", ");

/** Drops empty optional values so a saved item reads like a hand-written one. */
export function compact<T extends Record<string, unknown>>(item: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(item)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v) && v.length === 0 && k !== "primary_key" && k !== "fields") continue;
    if (v === false) continue;
    out[k] = v;
  }
  return out as T;
}
