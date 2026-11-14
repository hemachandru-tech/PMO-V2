import type { AskMode } from "../../api/app";

/** Ask answer modes (ENH-028, US-256): the names the Connections screen and "How this was answered" show. */
export const ASK_MODE_LABELS: Record<AskMode, string> = {
  certified: "Certified measures",
  fields: "Fields + definitions",
  hybrid: "Hybrid",
  direct_sql: "Direct SQL",
};
