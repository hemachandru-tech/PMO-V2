/** A measure's expression as a readable tree (US-232): the typed AST the query engine compiles, in words. */

type Node = { text: string; children: Node[] };
type Json = Record<string, unknown>;

const AGG: Record<string, string> = {
  count: "Count", count_distinct: "Count of distinct", sum: "Sum", avg: "Average", min: "Lowest", max: "Highest",
  median: "Median", percentile: "Percentile",
};
const OP: Record<string, string> = {
  "=": "is", "!=": "is not", "<": "is before or below", "<=": "is at most", ">": "is after or above", ">=": "is at least",
  in: "is one of", not_in: "is none of", is_null: "is blank", is_not_null: "is not blank", contains: "contains",
};
const CALC: Record<string, string> = { add: "plus", sub: "minus", mul: "times", div: "divided by" };

const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

function days(n: unknown): string {
  const d = Number(n ?? 0);
  return d === 0 ? "" : d > 0 ? ` + ${d} days` : ` − ${-d} days`;
}

export function valueText(v: unknown): string {
  if (v === null || v === undefined) return "blank";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(valueText).join(", ");
  if (!isObj(v)) return String(v);
  if ("asof_offset_days" in v) return `the reporting date${days(v.asof_offset_days)}`;
  if ("today" in v) return `today${days(v.offset_days)}`;
  if ("days_from" in v) return `days from ${valueText(v.days_from)} to ${valueText(v.days_to)}`;
  if ("calc" in v) return `(${valueText(v.left)} ${CALC[String(v.calc)] ?? v.calc} ${valueText(v.right)})`;
  if ("calculator" in v) return `${String(v.calculator)} → ${String(v.output)}`;
  if ("field" in v) return String(v.field);
  return JSON.stringify(v);
}

export function conditionNode(c: unknown): Node {
  if (!isObj(c)) return { text: valueText(c), children: [] };
  if (Array.isArray(c.all)) return { text: "All of", children: c.all.map(conditionNode) };
  if (Array.isArray(c.any)) return { text: "Any of", children: c.any.map(conditionNode) };
  if ("not" in c) return { text: "Not", children: [conditionNode(c.not)] };
  const op = String(c.op ?? "");
  const word = OP[op] ?? op;
  if (Array.isArray(c.values)) return { text: `${String(c.field)} ${word} ${c.values.map(valueText).join(", ")}`, children: [] };
  if (op === "is_null" || op === "is_not_null") return { text: `${String(c.field)} ${word}`, children: [] };
  const other = c.other_field ? String(c.other_field) : valueText(c.value);
  return { text: `${valueText(c.field)} ${word} ${other}`, children: [] };
}

export function expressionNode(e: unknown): Node {
  if (!isObj(e)) return { text: valueText(e), children: [] };
  if ("agg" in e) {
    const agg = AGG[String(e.agg)] ?? String(e.agg);
    const of = e.field ? ` of ${String(e.field)}` : e.value !== undefined ? ` of ${valueText(e.value)}` : e.agg === "count" ? " of records" : "";
    const p = e.p ? ` (p${Math.round(Number(e.p) * 100)})` : "";
    return { text: `${agg}${of}${p}`, children: e.where ? [{ text: "where", children: [conditionNode(e.where)] }] : [] };
  }
  if ("op" in e && "left" in e) {
    const scale = e.scale ? `, times ${String(e.scale)}` : "";
    return { text: `${CALC[String(e.op)] ?? String(e.op)}${scale}`, children: [expressionNode(e.left), expressionNode(e.right)] };
  }
  return { text: JSON.stringify(e), children: [] };
}

function Branch({ node }: { node: Node }) {
  return (
    <li>
      <span>{node.text}</span>
      {node.children.length > 0 && <ul className="ml-4 list-disc pl-3">{node.children.map((c, i) => <Branch key={i} node={c} />)}</ul>}
    </li>
  );
}

export function ExpressionTree({ expression }: { expression: unknown }) {
  return (
    <ul aria-label="The rule as a tree" className="grid gap-0.5 font-mono text-small">
      <Branch node={expressionNode(expression)} />
    </ul>
  );
}
