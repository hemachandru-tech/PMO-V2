import type { ComponentType } from "react";

import type { PackDetail } from "../../../../api/packs";
import { authorTab } from "./AuthorTab";
import { dimensionsTab } from "./DimensionsTab";
import { entitiesTab } from "./EntitiesTab";
import { knowledgeTab } from "./KnowledgeTab";
import { linksTab } from "./LinksTab";
import { measuresTab } from "./MeasuresTab";
import { overviewTab } from "./OverviewTab";
import { reportsTab } from "./ReportsTab";
import { versionsTab } from "./VersionsTab";

/**
 * The tabs a pack opens on (US-232), in order. Each tab is its own file exporting a `PackTab`; a story that adds or
 * replaces a tab (Knowledge US-233, Reports & dashboards US-234, AI author US-237 …) changes its file and, for a new
 * tab, one line here.
 * A tab reads and saves the pack through `usePack()`.
 */
export interface PackTab {
  key: string;
  label: string;
  /** A count shown on the tab (items in it). */
  count?: (pack: PackDetail) => number;
  Component: ComponentType;
}

export const PACK_TABS: PackTab[] = [
  overviewTab,
  authorTab,
  entitiesTab,
  linksTab,
  measuresTab,
  dimensionsTab,
  knowledgeTab,
  reportsTab,
  versionsTab,
];
