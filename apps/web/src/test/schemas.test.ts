import { describe, expect, it } from "vitest";
import { z } from "zod";

import openapi from "../api/openapi.json";
import { PACK_SCHEMAS } from "../api/packSchemas";
import { REQUEST_SCHEMAS } from "../api/schemas";

type Json = { [k: string]: unknown };
const components = (openapi as unknown as { components: { schemas: Record<string, Json> } }).components.schemas;

/** Follows `$ref`s and drops the `null` arm of optional values, so both sides compare as plain shapes. */
function resolve(node: Json | undefined): Json | undefined {
  if (!node) return node;
  if (typeof node.$ref === "string") return resolve(components[node.$ref.split("/").pop()!]);
  if (Array.isArray(node.anyOf)) {
    const arms = (node.anyOf as Json[]).filter((a) => a.type !== "null");
    if (arms.length === 1) return resolve(arms[0]);
  }
  if (Array.isArray(node.type)) {
    const types = (node.type as string[]).filter((t) => t !== "null");
    if (types.length === 1) return { ...node, type: types[0] };
  }
  return node;
}

const LIMITS = ["type", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"] as const;

/** Lists every place where the Zod side disagrees with the API's request model. */
function compare(api: Json | undefined, zod: Json | undefined, path: string, out: string[]): void {
  const a = resolve(api);
  const s = resolve(zod);
  if (!a || !s) return;
  if (Array.isArray(a.anyOf) || Array.isArray(s.anyOf)) return; // unions of scalars: shape only
  for (const k of LIMITS) {
    const av = k === "type" && a.type === "integer" ? "integer" : a[k];
    const sv = s[k];
    if (av !== undefined && av !== sv) out.push(`${path}: ${k} is ${String(sv)} in Zod but ${String(av)} in the API`);
  }
  if (typeof a.pattern === "string") {
    const re = new RegExp(a.pattern);
    if (Array.isArray(s.enum)) {
      for (const v of s.enum) if (!re.test(String(v))) out.push(`${path}: enum value ${String(v)} fails the API pattern`);
    } else if (s.pattern !== a.pattern) out.push(`${path}: pattern ${String(s.pattern)} differs from ${a.pattern}`);
  }
  if (Array.isArray(a.enum) && JSON.stringify([...a.enum].sort()) !== JSON.stringify([...((s.enum as unknown[]) ?? [])].sort())) {
    out.push(`${path}: enum differs`);
  }
  if (a.type === "array") compare(a.items as Json, s.items as Json, `${path}[]`, out);
  if (a.type === "object" && a.properties) {
    const ap = a.properties as Record<string, Json>;
    const sp = (s.properties ?? {}) as Record<string, Json>;
    const ar = new Set((a.required as string[]) ?? []);
    const sr = new Set((s.required as string[]) ?? []);
    for (const key of Object.keys(sp)) if (!(key in ap)) out.push(`${path}.${key}: not in the API model`);
    for (const key of ar) if (!sr.has(key)) out.push(`${path}.${key}: required by the API but optional or missing in Zod`);
    for (const key of sr) if (!ar.has(key)) out.push(`${path}.${key}: required in Zod but optional in the API`);
    for (const key of Object.keys(sp)) compare(ap[key], sp[key], `${path}.${key}`, out);
  }
}

describe("Zod request schemas match the OpenAPI document", () => {
  for (const [name, schema] of Object.entries({ ...REQUEST_SCHEMAS, ...PACK_SCHEMAS })) {
    it(name, () => {
      expect(components[name], `${name} is not in openapi.json; run pnpm openapi`).toBeDefined();
      const problems: string[] = [];
      compare(components[name], z.toJSONSchema(schema, { io: "input" }) as Json, name, problems);
      expect(problems).toEqual([]);
    });
  }

  it("catches drift", () => {
    const problems: string[] = [];
    const drifted = z.object({ name: z.string().max(81), host: z.string(), extra: z.string() });
    compare(components.ConnectionIn, z.toJSONSchema(drifted, { io: "input" }) as Json, "ConnectionIn", problems);
    expect(problems).toEqual(expect.arrayContaining([
      "ConnectionIn.extra: not in the API model",
      "ConnectionIn.engine: required by the API but optional or missing in Zod",
      "ConnectionIn.host: required in Zod but optional in the API",
      "ConnectionIn.name: maxLength is 81 in Zod but 80 in the API",
    ]));
  });
});
