// js-yaml 4 ships without types; the Pack Studio's YAML view uses these two functions (US-232).
declare module "js-yaml" {
  export function load(text: string, options?: { json?: boolean }): unknown;
  export function dump(value: unknown, options?: { indent?: number; lineWidth?: number; noRefs?: boolean; sortKeys?: boolean; flowLevel?: number }): string;
}
