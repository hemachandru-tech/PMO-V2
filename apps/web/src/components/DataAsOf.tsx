import { CalendarClock } from "lucide-react";

import { formatAsOf, formatRelative, formatReportingDate } from "./format";
import { Badge } from "./ui/badge";

/**
 * "Data as of <reporting date>" (US-158): the date every date-relative rule counts from (overdue, aging, horizons),
 * not the clock. The last refresh time follows in the tooltip and the screen-reader label. Without a reporting date
 * (several sources with different dates) it falls back to the refresh time, as before.
 */
export function DataAsOf({ reportingDate, refreshedAt }: { reportingDate?: string | null; refreshedAt?: string | null }) {
  if (!reportingDate) {
    return <Badge title={formatAsOf(refreshedAt)}>Data as of {formatAsOf(refreshedAt)}{refreshedAt ? ` · ${formatRelative(refreshedAt)}` : ""}</Badge>;
  }
  // The canvas passes no refresh time (each widget runs on its own); the viewer passes the dashboard's.
  const refreshed = refreshedAt === undefined ? "" : refreshedAt ? ` Refreshed ${formatAsOf(refreshedAt)} (${formatRelative(refreshedAt)}).` : " Not refreshed yet.";
  const words = `Data as of ${formatReportingDate(reportingDate)}`;
  return (
    <Badge title={`Reporting date: overdue, aging and go-live horizons count from it.${refreshed}`} aria-label={`${words}.${refreshed}`}>
      <CalendarClock aria-hidden />{words}
    </Badge>
  );
}
