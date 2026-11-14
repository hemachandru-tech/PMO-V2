import { usePack } from "../PackContext";
import { ReportsTab as Reports } from "../reports/ReportsTab";
import type { PackTab } from "./registry";

/** Reports & dashboards (US-234): report blueprints with a live preview, and the pack's starter dashboards. */
function ReportsTab() {
  const { pack } = usePack();
  return <Reports packId={pack.id} />;
}

export const reportsTab: PackTab = { key: "reports", label: "Reports & dashboards", Component: ReportsTab };
