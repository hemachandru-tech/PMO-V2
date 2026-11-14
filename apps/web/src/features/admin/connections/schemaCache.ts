import type { QueryClient } from "@tanstack/react-query";

/**
 * After the schema is read again, everything built from the old table list is stale (BUG-018): the connection list,
 * its objects, the starter pack match and preview, and the model draft (its "Tables not in the model").
 */
export function schemaRead(qc: QueryClient, id: string) {
  for (const queryKey of [["connections"], ["connection-objects", id], ["pack-match", id], ["pack-preview", id], ["draft"]]) {
    void qc.invalidateQueries({ queryKey });
  }
}
