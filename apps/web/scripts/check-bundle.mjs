// Bundle budget per route (US-148). Run with `pnpm bundle:check` (builds with a manifest, then checks).
//
// For each route in bundle-budget.json this adds up the gzip size of the JavaScript and CSS a first visit downloads:
// the entry (the shell and Home) plus the route's lazy chunk and every chunk it imports statically. Chunks loaded
// later on demand (CodeMirror, the canvas grid) are not counted against the route. The Viewer's routes have the
// tightest budgets, so building tools never makes reading slower.
//
// `--update` rewrites each budget to the current size plus 10 % headroom (round up to the next kB); review the diff.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const budgetFile = join(root, "bundle-budget.json");
const manifest = JSON.parse(readFileSync(join(dist, ".vite/manifest.json"), "utf8"));
const budget = JSON.parse(readFileSync(budgetFile, "utf8"));

const gz = new Map();
const size = (file) => {
  if (!gz.has(file)) gz.set(file, gzipSync(readFileSync(join(dist, file))).length);
  return gz.get(file);
};

/** Files a chunk needs before it runs: itself, its CSS and its static imports, recursively. */
function closure(key, seen = new Set()) {
  const chunk = manifest[key];
  if (!chunk || seen.has(key)) return seen;
  seen.add(key);
  for (const imp of chunk.imports ?? []) closure(imp, seen);
  return seen;
}
const files = (keys) => {
  const out = new Set();
  for (const k of keys) {
    const c = manifest[k];
    out.add(c.file);
    for (const css of c.css ?? []) out.add(css);
  }
  return out;
};

const entryKeys = closure("index.html");
const entryFiles = files(entryKeys);
const kb = (bytes) => Math.round((bytes / 1024) * 10) / 10;
const total = (set) => [...set].reduce((n, f) => n + size(f), 0);

const rows = [];
let failed = false;
for (const r of budget.routes) {
  let routeFiles = new Set(entryFiles);
  if (r.module) {
    // A page another lazy page also imports lands in a shared chunk, keyed by its chunk name instead of its path.
    const name = r.module.split("/").pop().replace(/\.tsx?$/, "");
    const key = manifest[r.module] ? r.module : Object.keys(manifest).find((k) => manifest[k].isDynamicEntry && manifest[k].name === name);
    if (!key) throw new Error(`${r.module} is not a chunk in the manifest; is it still lazy-loaded?`);
    routeFiles = new Set([...entryFiles, ...files(closure(key))]);
  }
  const bytes = total(routeFiles);
  const over = bytes / 1024 > r.budget_kb;
  failed ||= over;
  rows.push({ route: r.route, kb: kb(bytes), budget: r.budget_kb, over });
  if (process.argv.includes("--update")) r.budget_kb = Math.ceil((bytes / 1024) * 1.1);
}

const width = Math.max(...rows.map((r) => r.route.length));
console.log(`${"Route".padEnd(width)}  gzip kB  budget`);
for (const r of rows) console.log(`${r.route.padEnd(width)}  ${String(r.kb).padStart(7)}  ${String(r.budget).padStart(6)}${r.over ? "  OVER" : ""}`);

if (process.argv.includes("--update")) {
  writeFileSync(budgetFile, JSON.stringify(budget, null, 2) + "\n");
  console.log("Budgets updated.");
} else if (failed) {
  console.error("\nA route is over its bundle budget. Split the new code into a lazy chunk, or raise the budget in review.");
  process.exit(1);
}
