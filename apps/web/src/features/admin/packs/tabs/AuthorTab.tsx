import { AuthorTab as Author } from "../author/AuthorTab";
import type { PackTab } from "./registry";

/** AI author (US-237): AI drafts the pack stage by stage from a connection's read schema; every item is reviewed. */
export const authorTab: PackTab = { key: "author", label: "AI author", Component: Author };
