import {
  columnFilteringFeature,
  columnVisibilityFeature,
  constructFilterFn,
  createFilteredRowModel,
  createSortedRowModel,
  globalFilteringFeature,
  rowSelectionFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  sortFn_datetime,
  tableFeatures,
  useTable,
  type ColumnDef,
  type ColumnFiltersState,
  type Row,
  type RowData,
  type RowSelectionState,
  type SortingState,
  type ColumnVisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, Columns3, ListFilter, Search, X } from "lucide-react";
import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { formatValue } from "@/components/format";
import { cn } from "@/lib/utils";

import { Button } from "./button";
import { Checkbox } from "./checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./dropdown-menu";
import { EmptyState } from "./empty-state";
import { Input } from "./input";
import { NativeSelect } from "./native-select";
import { Skeleton } from "./skeleton";

/** Matches the text a person sees: numbers also match in their formatted form (1200 ↔ 1,200). */
const searchText = (v: unknown): string => {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return `${v} ${v.toLocaleString()}`.toLowerCase();
  return String(v).toLowerCase();
};
const contains = constructFilterFn({
  filter: (value: unknown, q: string) => searchText(value).includes(q),
  resolveFilterValue: (q: unknown) => String(q ?? "").trim().toLowerCase(),
  autoRemove: (q: unknown) => !String(q ?? "").trim(),
});
const equalsText = constructFilterFn({
  filter: (value: unknown, q: string) => String(value ?? "") === q,
  autoRemove: (q: unknown) => q === undefined || q === "",
});

// Stable, module-level feature registry (TanStack Table v9 registers only what a table uses).
const features = tableFeatures({
  rowSortingFeature,
  columnFilteringFeature,
  globalFilteringFeature,
  columnVisibilityFeature,
  rowSelectionFeature,
  sortedRowModel: createSortedRowModel(),
  filteredRowModel: createFilteredRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, basic: sortFn_basic, datetime: sortFn_datetime },
  filterFns: { contains, equalsText },
});
type Features = typeof features;
type TRow<T extends RowData> = Row<Features, T>;

export interface DataColumn<T> {
  id: string;
  header: string;
  /** Raw value, used to sort, search and filter. Defaults to `row[id]`. */
  value?: (row: T) => unknown;
  /** Custom cell; defaults to the formatted value. */
  cell?: (row: T) => ReactNode;
  /** Right-aligned, the mono font with tabular figures (NFR-022). */
  numeric?: boolean;
  /** Field format for numbers: "integer", "currency", "percent" (as in query results). */
  format?: string | null;
  /** Sort as dates (ISO strings). */
  date?: boolean;
  sortable?: boolean;
  /** Column filter control shown in the filter row. */
  filter?: "text" | "select";
  /** The column cannot be hidden from the Columns menu. */
  alwaysVisible?: boolean;
  /** Hidden until the person turns it on. */
  hidden?: boolean;
  className?: string;
}

export interface DataTableProps<T extends RowData> {
  data: readonly T[];
  columns: DataColumn<T>[];
  /** Accessible name of the table (also used in "No … yet"). */
  label: string;
  getRowId?: (row: T, index: number) => string;
  loading?: boolean;
  error?: ReactNode;
  /** Shown when there are no rows at all. */
  empty?: { title: string; body?: ReactNode; action?: ReactNode };
  /** Global search box (default on). */
  search?: boolean;
  /** Per-column filter row (only for columns with `filter`). */
  columnFilters?: boolean;
  /** Columns menu (default on when more than three columns). */
  columnMenu?: boolean;
  /** Row checkboxes; the callback gets the selected rows. */
  onSelectionChange?: (rows: T[]) => void;
  /** Accessible name of a row (selection checkbox and keyboard focus). */
  rowLabel?: (row: T) => string;
  /** Enter or click on a row. */
  onRowActivate?: (row: T) => void;
  /** Detail shown under a row (not used when virtualised). */
  renderDetail?: (row: T) => ReactNode;
  rowClassName?: (row: T) => string | undefined;
  toolbar?: ReactNode;
  /** Extra footer content (pager, "Load more", hidden-field note). */
  footer?: ReactNode;
  /** The API pages the data: sorting, search and filters run on the server or not at all. */
  serverPaged?: boolean;
  /** Virtualise above this many rows (TanStack Virtual). */
  virtualizeAbove?: number;
  /** Height of the scroll area when virtualised or when given. */
  maxHeight?: number;
  compact?: boolean;
  /** Banded like an Excel table: a tinted header and every other row tinted. */
  banded?: boolean;
  className?: string;
}

const ROW_HEIGHT = { compact: 32, default: 40 };

/**
 * DataTable (design system: DataTable; US-136): every list and grid in Tela. Sort, search, column filters, column
 * visibility, row selection, sticky header, empty/loading/error states, keyboard navigation, numbers right-aligned in
 * tabular figures. Virtualised above 200 rows; where the API pages, pass `serverPaged` and a pager in `footer`.
 */
export function DataTable<T extends RowData>(props: DataTableProps<T>) {
  const {
    data, columns, label, loading, error, empty, onSelectionChange, onRowActivate, renderDetail, serverPaged,
    compact, banded, virtualizeAbove = 200,
  } = props;
  const search = (props.search ?? true) && !serverPaged;
  const filterable = !!props.columnFilters && !serverPaged && columns.some((c) => c.filter);
  const columnMenu = props.columnMenu ?? columns.length > 3;
  const selectable = !!onSelectionChange;

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [showFilters, setShowFilters] = useState(false);
  const [columnVisibility, setColumnVisibility] = useState<ColumnVisibilityState>(
    () => Object.fromEntries(columns.filter((c) => c.hidden).map((c) => [c.id, false])),
  );
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const defs = useMemo<ColumnDef<Features, T, unknown>[]>(() => {
    const out: ColumnDef<Features, T, unknown>[] = columns.map((c) => ({
      id: c.id,
      header: c.header,
      // Blanks are undefined so they sort last in both directions.
      accessorFn: (row: T) => (c.value ? c.value(row) : (row as Record<string, unknown>)[c.id]) ?? undefined,
      enableSorting: !serverPaged && c.sortable !== false,
      sortFn: c.date ? "datetime" : c.numeric ? "basic" : "alphanumeric",
      sortUndefined: "last",
      enableColumnFilter: !!c.filter,
      filterFn: c.filter === "select" ? "equalsText" : "contains",
      enableHiding: !c.alwaysVisible,
      meta: c,
    }));
    return out;
  }, [columns, serverPaged]);

  const getRowId = props.getRowId;
  const table = useTable({
    features,
    columns: defs,
    data: data as T[],
    getRowId: getRowId ? (row: T, index: number) => getRowId(row, index) : undefined,
    state: { sorting, globalFilter, columnFilters, columnVisibility, rowSelection },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    onRowSelectionChange: setRowSelection,
    globalFilterFn: "contains",
    getColumnCanGlobalFilter: () => true,
    enableRowSelection: selectable,
    sortDescFirst: false,
    manualSorting: serverPaged,
    manualFiltering: serverPaged,
  });

  const rows = table.getRowModel().rows;
  const meta = (id: string) => columns.find((c) => c.id === id);

  // Report selection changes as rows.
  const selectedKey = Object.keys(rowSelection).sort().join("|");
  useEffect(() => {
    if (!onSelectionChange) return;
    onSelectionChange(table.getSelectedRowModel().rows.map((r) => r.original));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- report when the selected ids change, not on every render
  }, [selectedKey]);

  // Virtualisation (above 200 rows): only the visible rows are in the DOM; padding rows keep the scroll height.
  const virtual = rows.length > virtualizeAbove;
  const rowHeight = compact ? ROW_HEIGHT.compact : ROW_HEIGHT.default;
  const maxHeight = props.maxHeight ?? (virtual ? 560 : undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    getItemKey: (i) => rows[i]?.id ?? i,
    overscan: 12,
    enabled: virtual,
    initialRect: { width: 0, height: maxHeight ?? 560 },
  });
  const items = virtual ? virtualizer.getVirtualItems() : [];
  // Until the scroll area is measured, render a first screenful so the table is never blank.
  const firstScreen = Math.min(rows.length, Math.ceil((maxHeight ?? 560) / rowHeight) + 12);
  const visibleIndexes = !virtual ? rows.map((_, i) => i) : items.length ? items.map((i) => i.index) : Array.from({ length: firstScreen }, (_, i) => i);
  const padTop = virtual && items.length ? items[0]!.start : 0;
  const padBottom = !virtual ? 0 : items.length ? virtualizer.getTotalSize() - items[items.length - 1]!.end : (rows.length - firstScreen) * rowHeight;

  // Keyboard navigation: one row is tabbable; arrows, Home/End and Page keys move; Enter activates; Space selects.
  const navigable = !!onRowActivate || selectable;
  const [active, setActive] = useState(0);
  const focusNext = useRef<number | null>(null);
  useEffect(() => {
    if (focusNext.current === null) return;
    const el = scrollRef.current?.querySelector<HTMLElement>(`[data-row-index="${focusNext.current}"]`);
    if (el) {
      el.focus();
      focusNext.current = null;
    }
  });
  const moveTo = (i: number) => {
    const next = Math.max(0, Math.min(rows.length - 1, i));
    setActive(next);
    focusNext.current = next;
    if (virtual) virtualizer.scrollToIndex(next, { align: "auto" });
  };
  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, row: TRow<T>, index: number) => {
    const page = Math.max(1, Math.floor((maxHeight ?? 400) / rowHeight) - 1);
    const keys: Record<string, () => void> = {
      ArrowDown: () => moveTo(index + 1),
      ArrowUp: () => moveTo(index - 1),
      Home: () => moveTo(0),
      End: () => moveTo(rows.length - 1),
      PageDown: () => moveTo(index + page),
      PageUp: () => moveTo(index - page),
      Enter: () => onRowActivate?.(row.original),
      " ": () => selectable && row.toggleSelected(),
    };
    const action = keys[e.key];
    if (action && e.target === e.currentTarget) {
      e.preventDefault();
      action();
    }
  };
  useEffect(() => {
    if (active > rows.length - 1) setActive(Math.max(0, rows.length - 1));
  }, [rows.length, active]);

  const leafColumns = table.getVisibleLeafColumns();
  const colSpan = leafColumns.length + (selectable ? 1 : 0);
  const filtered = !!globalFilter || columnFilters.length > 0;
  const clearFilters = () => {
    setGlobalFilter("");
    setColumnFilters([]);
  };
  const selectedCount = Object.keys(rowSelection).length;
  const rowLabel = (row: T, i: number) => props.rowLabel?.(row) ?? `Row ${i + 1}`;

  const selectOptions = (id: string) => {
    const c = meta(id);
    const values = new Set<string>();
    for (const row of data) {
      const v = c?.value ? c.value(row) : (row as Record<string, unknown>)[id];
      if (v !== null && v !== undefined && v !== "") values.add(String(v));
    }
    return [...values].sort((a, b) => a.localeCompare(b));
  };

  const showToolbar = search || filterable || columnMenu || props.toolbar;
  return (
    <div data-slot="data-table" className={cn("overflow-hidden rounded-lg border border-line bg-surface-raised", props.className)}>
      {showToolbar && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          {search && (
            <div className="relative min-w-48 max-w-xs flex-1">
              <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-muted" />
              <Input
                type="search"
                aria-label={`Search ${label.toLowerCase()}`}
                placeholder="Search"
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                className="h-control-sm pl-8"
              />
            </div>
          )}
          {filterable && (
            <Button variant={showFilters ? "secondary" : "ghost"} size="sm" aria-pressed={showFilters} onClick={() => setShowFilters(!showFilters)}>
              <ListFilter aria-hidden className="size-4" /> Filters{columnFilters.length > 0 ? ` (${columnFilters.length})` : ""}
            </Button>
          )}
          {columnMenu && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm"><Columns3 aria-hidden className="size-4" /> Columns</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuLabel>Show columns</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {table.getAllLeafColumns().filter((c) => c.getCanHide()).map((c) => (
                  <DropdownMenuCheckboxItem key={c.id} checked={c.getIsVisible()} onCheckedChange={(on) => c.toggleVisibility(on === true)}
                    onSelect={(e) => e.preventDefault()}>
                    {meta(c.id)?.header ?? c.id}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {props.toolbar && <div className="ml-auto flex flex-wrap items-center gap-2">{props.toolbar}</div>}
        </div>
      )}
      <div
        ref={scrollRef}
        className="relative overflow-auto"
        style={maxHeight ? { maxHeight } : undefined}
        // A scrollable region must be reachable by keyboard when rows themselves are not focusable.
        tabIndex={!navigable && (virtual || !!props.maxHeight) ? 0 : undefined}
        role={!navigable && (virtual || !!props.maxHeight) ? "region" : undefined}
        aria-label={!navigable && (virtual || !!props.maxHeight) ? label : undefined}
      >
        <table className={cn("w-full border-collapse text-body", compact && "text-small")} aria-label={label}
          aria-rowcount={virtual ? rows.length + 1 : undefined} aria-busy={loading || undefined}>
          <thead className={cn("sticky top-0 z-10", banded ? "bg-accent-soft" : "bg-surface-sunken")}>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id} aria-rowindex={virtual ? 1 : undefined}>
                {selectable && (
                  <th scope="col" className="w-10 border-b border-line px-3">
                    <Checkbox
                      aria-label="Select all rows"
                      checked={table.getIsAllRowsSelected() ? true : table.getIsSomeRowsSelected() ? "indeterminate" : false}
                      onCheckedChange={(on) => table.toggleAllRowsSelected(on === true)}
                    />
                  </th>
                )}
                {group.headers.map((header) => {
                  const c = meta(header.column.id);
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                      className={cn(
                        "h-control-md whitespace-nowrap border-b border-line px-3 text-left align-middle text-small font-semibold",
                        banded ? "text-ink" : "text-ink-muted",
                        c?.numeric && "text-right",
                      )}
                    >
                      {header.column.getCanSort() ? (
                        <button
                          type="button"
                          className={cn("inline-flex items-center gap-1 rounded-sm hover:text-ink focus-visible:focus-ring", c?.numeric && "flex-row-reverse")}
                          onClick={header.column.getToggleSortingHandler()}
                        >
                          {c?.header}
                          {sorted === "asc" && <ArrowUp aria-hidden className="size-3" />}
                          {sorted === "desc" && <ArrowDown aria-hidden className="size-3" />}
                        </button>
                      ) : (
                        c?.header
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
            {filterable && showFilters && (
              <tr>
                {selectable && <th className="border-b border-line" />}
                {leafColumns.map((col) => {
                  const c = meta(col.id);
                  const value = (col.getFilterValue() as string | undefined) ?? "";
                  return (
                    <th key={col.id} className="border-b border-line px-2 py-1.5 font-normal">
                      {c?.filter === "select" ? (
                        <NativeSelect aria-label={`Filter ${c.header}`} value={value} onChange={(e) => col.setFilterValue(e.target.value)}
                          className="[&_select]:h-control-sm">
                          <option value="">All</option>
                          {selectOptions(col.id).map((o) => <option key={o} value={o}>{o}</option>)}
                        </NativeSelect>
                      ) : c?.filter === "text" ? (
                        <Input aria-label={`Filter ${c.header}`} value={value} onChange={(e) => col.setFilterValue(e.target.value)}
                          className="h-control-sm" placeholder="Contains…" />
                      ) : null}
                    </th>
                  );
                })}
              </tr>
            )}
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 4 }, (_, i) => (
                <tr key={i} className="border-b border-line last:border-0">
                  {Array.from({ length: colSpan }, (_, j) => (
                    <td key={j} className="px-3" style={{ height: rowHeight }}><Skeleton className="h-3 w-3/4" /></td>
                  ))}
                </tr>
              ))
            ) : error ? (
              <tr><td colSpan={colSpan} className="p-4">{error}</td></tr>
            ) : data.length === 0 ? (
              <tr>
                <td colSpan={colSpan} className="p-4">
                  <EmptyState title={empty?.title ?? `No ${label.toLowerCase()} yet`} action={empty?.action} className="border-0 py-6">{empty?.body}</EmptyState>
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={colSpan} className="p-4">
                  <EmptyState title="No rows match" className="border-0 py-6"
                    action={filtered && <Button variant="secondary" size="sm" onClick={clearFilters}><X aria-hidden className="size-4" /> Clear search and filters</Button>}>
                    Nothing in this table matches {globalFilter ? `“${globalFilter}”` : "these filters"}.
                  </EmptyState>
                </td>
              </tr>
            ) : (
              <>
                {padTop > 0 && <tr aria-hidden style={{ height: padTop }} />}
                {visibleIndexes.map((index) => {
                  const row = rows[index]!;
                  const detail = !virtual && renderDetail ? renderDetail(row.original) : null;
                  const selected = row.getIsSelected();
                  return (
                    <Fragment key={row.id}>
                      <tr
                        data-row-index={index}
                        aria-rowindex={virtual ? index + 2 : undefined}
                        aria-selected={selectable ? selected : undefined}
                        aria-label={navigable ? rowLabel(row.original, index) : undefined}
                        tabIndex={navigable ? (index === active ? 0 : -1) : undefined}
                        onFocus={navigable ? () => setActive(index) : undefined}
                        onKeyDown={navigable ? (e) => onRowKey(e, row, index) : undefined}
                        onClick={onRowActivate ? (e) => {
                          if ((e.target as HTMLElement).closest("button, a, input, select, textarea, [role=checkbox]")) return;
                          onRowActivate(row.original);
                        } : undefined}
                        className={cn(
                          "border-b border-line last:border-0 hover:bg-surface-sunken/60 focus-visible:focus-ring focus-visible:outline-none",
                          banded && index % 2 === 1 && "bg-accent-soft/40",
                          selected && "bg-accent-soft hover:bg-accent-soft",
                          onRowActivate && "cursor-pointer",
                          detail && "border-b-0",
                          props.rowClassName?.(row.original),
                        )}
                        style={virtual ? { height: rowHeight } : undefined}
                      >
                        {selectable && (
                          <td className="w-10 px-3">
                            <Checkbox aria-label={`Select ${rowLabel(row.original, index)}`} checked={selected} tabIndex={-1}
                              onCheckedChange={(on) => row.toggleSelected(on === true)} />
                          </td>
                        )}
                        {row.getVisibleCells().map((cell) => {
                          const c = meta(cell.column.id);
                          const value = cell.getValue();
                          return (
                            <td
                              key={cell.id}
                              className={cn(
                                "px-3 align-middle",
                                compact ? "py-1.5" : "py-2",
                                c?.numeric && "text-right font-mono text-figure tabular-nums",
                                c?.className,
                              )}
                            >
                              {c?.cell ? c.cell(row.original) : c?.numeric ? formatValue(value, c.format) : value === null || value === undefined || value === "" ? "—" : formatValue(value)}
                            </td>
                          );
                        })}
                      </tr>
                      {detail && (
                        <tr className="border-b border-line last:border-0">
                          <td colSpan={colSpan} className="px-3 pb-3">{detail}</td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {padBottom > 0 && <tr aria-hidden style={{ height: padBottom }} />}
              </>
            )}
          </tbody>
        </table>
      </div>
      {(!loading && !error && data.length > 0) || props.footer ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-2 text-small text-ink-muted">
          <span aria-live="polite">
            {!loading && !error && data.length > 0 && !serverPaged && (
              <>
                {filtered ? `${rows.length.toLocaleString()} of ${data.length.toLocaleString()} rows` : `${data.length.toLocaleString()} ${data.length === 1 ? "row" : "rows"}`}
                {selectable && selectedCount > 0 && ` · ${selectedCount} selected`}
              </>
            )}
          </span>
          {props.footer}
        </div>
      ) : null}
    </div>
  );
}
