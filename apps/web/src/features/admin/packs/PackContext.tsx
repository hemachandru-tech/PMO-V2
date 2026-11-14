import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { savePack, type PackDetail, type PackSection } from "../../../api/packs";

export interface PackContextValue {
  pack: PackDetail;
  etag: string;
  /** System packs and released versions open read-only (clone to edit). */
  readOnly: boolean;
  saving: boolean;
  /** Replaces whole manifest sections on the revision this screen loaded; throws the API's error (412, 400 …). */
  save: (sections: Partial<Record<PackSection, unknown>>) => Promise<PackDetail>;
}

const Ctx = createContext<PackContextValue | null>(null);

export const packKey = (id: string) => ["pack", id] as const;

/** The pack being edited, shared by every tab (US-232). A save updates the cached pack and its revision. */
export function PackProvider({ pack, etag, children }: { pack: PackDetail; etag: string; children: ReactNode }) {
  const qc = useQueryClient();
  const [saving, setSaving] = useState(false);
  const value = useMemo<PackContextValue>(() => ({
    pack, etag, readOnly: !pack.editable, saving,
    save: async (sections) => {
      setSaving(true);
      try {
        const out = await savePack(pack.id, etag, sections);
        qc.setQueryData(packKey(pack.id), { pack: { ...pack, ...out.pack }, etag: out.etag });
        void qc.invalidateQueries({ queryKey: ["packs"] });
        return out.pack;
      } finally {
        setSaving(false);
      }
    },
  }), [pack, etag, saving, qc]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePack(): PackContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePack must be used inside <PackProvider>.");
  return v;
}
