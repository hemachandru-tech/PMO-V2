import { Copy, Database, FilePlus2, Sparkles, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";

import { AiPackDialog } from "./AiPackDialog";
import { ClonePackDialog } from "./clone/ClonePackDialog";
import { CreatePackDialog } from "./CreatePackDialog";
import { ModelPackDialog } from "./ModelPackDialog";

/**
 * The Create menu (US-232). Each entry is a way to start a pack; `Dialog` is the step that asks for what it needs and
 * creates the draft. An entry without one is shown as coming with its story. A story fills its slot by giving the
 * entry a dialog (and the API a method in `packs/studio/create.py`):
 * - Clone a pack onto a connection, re-pointed to its schema — US-235 (`clone/ClonePackDialog.tsx`)
 * - From an approved model — US-236 (`ModelPackDialog`)
 * - AI draft from the read schema — US-237 (`AiPackDialog`; the draft opens on its AI author tab)
 */
export interface CreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the new draft's id, and the query string the pack page opens with (a tab, a connection …). */
  onCreated: (id: string, search?: string) => void;
}

export interface CreateMethod {
  key: string;
  label: string;
  description: string;
  icon: LucideIcon;
  story?: string;
  Dialog?: ComponentType<CreateDialogProps>;
}

export const CREATE_METHODS: CreateMethod[] = [
  { key: "blank", label: "Blank pack", description: "Start with no entities or measures.", icon: FilePlus2, Dialog: CreatePackDialog },
  { key: "clone", label: "Clone a pack", description: "Start from a template such as PMO, re-pointed to your schema.", icon: Copy, story: "US-235", Dialog: ClonePackDialog },
  { key: "model", label: "From an approved model", description: "Turn a model you built into a pack.", icon: Database, Dialog: ModelPackDialog },
  { key: "ai", label: "AI draft", description: "Let AI draft entities, links and measures from the read schema.", icon: Sparkles, story: "US-237", Dialog: AiPackDialog },
];
