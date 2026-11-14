import { KnowledgeTab as Knowledge } from "../knowledge/KnowledgeTab";
import { usePack } from "../PackContext";
import type { PackTab } from "./registry";

/** Knowledge (US-233): editors for each kind of business knowledge, with each item's checks, and context documents. */
function KnowledgeTab() {
  const { pack } = usePack();
  return <Knowledge packId={pack.id} />;
}

export const knowledgeTab: PackTab = { key: "knowledge", label: "Knowledge", Component: KnowledgeTab };
