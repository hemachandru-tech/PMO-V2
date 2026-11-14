# Skill: PMO Dashboard & Widget Builder

> **Skill Name**: `pmo-dashboard-widget-builder`  
> **Target Role**: Autonomous Frontend/Full-Stack Agent  
> **Platform**: PMO Intelligence Platform (React, Tailwind CSS, FastAPI, MySQL)  
> **Reference Standards**: `PMO_REPORT_SCREENS_CATALOG.md`, `CANONICAL_METRICS.md`, `report-builder-blueprint.html`  
> **Purpose**: Provides complete instructions, rules, templates, and patterns for generating production-grade reports, dashboards, and analytical widgets that strictly adhere to the PMO Intelligence architectural standard.

---

## 1. Core Principles & Non-Negotiables

When generating or modifying dashboards, reports, or widgets, you **MUST** enforce the following 10 Commandments:

1. **Never Invent or Claim "Actual Spend"**:
   - The database contains **ZERO actual spend, invoice, or monthly cost data**.
   - `cost_total_usd` is **Forecast Cost (EAC)** on projects and **Estimated Cost** on requests.
   - Budget variance (`cost - budget`) is a *forecast comparison*, NOT money already spent. Never label it "Spent", "Burn Rate", or "Overspent to date".
2. **Never Use the Browser Clock**:
   - All aging, tenure, and overdue metrics (`days_past_due`, `days_on_hold`, `days_open`) must be calculated from the centralized `asOf` date provided by `/api/meta` or `useAsOf()`. Never use `new Date()` for domain calculations.
3. **Never Conflate Reported Status with Calculated Health**:
   - **Reported Status** (`overall_kpi`, etc.): The PM's subjective self-reported color (`Green`, `Amber`, `Red`, `Not set`).
   - **Calculated Health** (`health_status`): The algorithmic 4-factor score (0–100) and status.
   - A missing reported status is `"Not set"` (Unknown), never Green.
   - Low evidence (<60%) in calculated health is `"Insufficient data"`, never Green or on track.
4. **Adhere to the 5-Layer Hierarchy**:
   - `Metric` (Certified rule in catalogue) $\to$ `Widget` (Metric + Breakdown + Visual) $\to$ `Report` (12-column grid section) $\to$ `Dashboard` (Tabbed collection) $\to$ `Publication` (Role-scoped view).
5. **Always Dim Instead of Blanking Out on Refresh**:
   - Use the shared cache (`src/lib/api.js`). During filter changes or refetching, keep existing data visible with `opacity-60` (`aria-busy="true"`) rather than unmounting and showing an abrupt full-page spinner.
6. **Support the Global 6 Dimensions**:
   - Every dashboard and report must accept and apply: `cio`, `region`, `portfolio`, `project_manager`, `priority`, `platform`.
   - *Exception*: Requests have no portfolio unless converted to a project. When a portfolio filter is active on a request view, display a `<Callout>` warning.
7. **Adhere to Population Boundaries**:
   - `Open Projects`: TPG status Active (`Project Created`, `Active`, `On Hold`).
   - `In Delivery`: State `Active` only.
   - `Open Requests`: State in `New`, `Active`, `On Hold`, `Return to Requester`.
8. **Consistent Overdue Go-Live Logic**:
   - `current_state == 'Active'` AND `planned_deployment_date < asOf` AND `actual_deployment_date IS NULL`.
   - Planned date equal to `asOf` is **not** overdue. On Hold and Project Created are **never** flagged overdue.
9. **Accessible Colors & Double Signaling**:
   - Never use color as the sole indicator. Always accompany colors with visible text labels (`RagDot`, `RagPill`, or textual counts).
10. **Use Standard Layout Containers**:
    - Wrap entire pages in `<ReportFrame>`, sections in `<Panel>`, key figures in `<KpiCard>`, and tabular details in `<DataTable>`.

---

## 2. Visual Component Catalog & API Reference

All chart and visualization components live in `src/components/dashboard/charts.jsx` and `src/components/dashboard/KpiCard.jsx`.

### 2.1 `KpiCard` (Headline Metric Card)
Used in the top metric strip (`grid grid-cols-2 md:grid-cols-4 (or 5) gap-2`).

```jsx
import KpiCard from '../dashboard/KpiCard';

<KpiCard
  title="Forecast vs approved budget"
  value="$4.2M"
  subtitle="8.4% above approved · EAC $54.1M"
  tone="critical"            // 'good' (green) | 'warning' (amber) | 'critical' (red) | 'neutral' (navy)
  tip="budgetVariance"       // ID from definitions.js (renders MetricTip modal)
  onClick={() => navigate('/projects/finance/capex-budget')} // Optional drilldown
/>
```

### 2.2 `Donut` (Part-to-Whole Ring)
Best for 3–4 discrete categories (e.g. status distributions).

```jsx
import { Donut } from '../dashboard/charts';
import { RAG_COLOR } from '../../lib/rag';

<Donut
  size={140}
  centerLabel="open"
  slices={[
    { name: 'Green', value: 42, color: RAG_COLOR.Green },
    { name: 'Amber', value: 18, color: RAG_COLOR.Amber },
    { name: 'Red', value: 7, color: RAG_COLOR.Red },
    { name: 'Not set', value: 3, color: RAG_COLOR.Unknown, noShare: true }, // noShare excludes from % denominator
  ]}
/>
```

### 2.3 `BarList` (Ranked Horizontal Bars)
Best for ranking categorical dimensions (e.g. Volume by Portfolio, Volume by PM).

```jsx
import { BarList } from '../dashboard/charts';

<BarList
  rows={[
    { name: 'Issuing', value: 24 },
    { name: 'Acquiring', value: 18 },
    { name: 'Digital', value: 11 },
  ]}
  labelWidth="w-40"          // Tailwind width class for label column
  color="#2a78d6"            // Bar fill color
  format={(v) => `${v} projects`}
  onSelect={(name) => setFilters(f => ({ ...f, portfolio: name }))} // Interactive filter
/>
```

### 2.4 `PairedBarList` (Dual Measure Comparison)
Best for comparing two metrics on the exact same scale per row (e.g., Budget vs. Forecast EAC).

```jsx
import { PairedBarList } from '../dashboard/charts';
import { money } from '../../lib/format';

<PairedBarList
  rows={[
    { name: 'Issuing', budget: 12000000, cost: 13500000 },
    { name: 'Acquiring', budget: 8500000, cost: 8100000 },
  ]}
  series={[
    { key: 'budget', label: 'Approved budget', color: '#2a78d6' },
    { key: 'cost', label: 'Forecast cost (EAC)', color: '#eb6834' },
  ]}
  format={(v) => money(v)}
  labelWidth="w-44"
/>
```

### 2.5 `Columns` (Vertical Ordinal Buckets)
Best for ordered time buckets (aging, lateness, variance spreads).

```jsx
import { Columns } from '../dashboard/charts';

<Columns
  rows={[
    { name: '1–30 d', value: 14 },
    { name: '31–60 d', value: 8 },
    { name: '61–90 d', value: 5 },
    { name: '90+ d', value: 3 },
  ]}
  height={140}
  ordinal={true}             // Automatically applies the 5-step ORDINAL blue ramp
  format={(v) => `${v}`}
  onSelect={(bucket) => handleDrill(bucket)}
/>
```

### 2.6 `HealthBars` (Stacked RAG Status Bars)
Best for comparing status distributions across portfolios or CIOs normalized to largest group.

```jsx
import { HealthBars } from '../dashboard/charts';

<HealthBars
  rows={[
    { name: 'Issuing', Green: 12, Amber: 5, Red: 2, Unknown: 1 },
    { name: 'Acquiring', Green: 8, Amber: 7, Red: 3, Unknown: 0 },
  ]}
  labelWidth="w-44"
  showTotal={true}
  onSelect={(row) => setFilters(f => ({ ...f, portfolio: row.name }))}
/>
```

### 2.7 `HeatGrid` (2D Cross-Tabulation Matrix)
Best for 2-dimensional density (Region × Status, Portfolio × Phase).

```jsx
import { HeatGrid } from '../dashboard/charts';
import { RAG_COLOR } from '../../lib/rag';

<HeatGrid
  rows={[{ name: 'GCC - UAE' }, { name: 'Northern Africa' }]}
  columns={['Red', 'Amber', 'Green', 'Not set']}
  rowHeader="Region"
  value={(row, col) => calculateCount(row.name, col)}
  columnColor={(col) => RAG_COLOR[col]}
  columnHealth={(col) => col} // Applies health hue shading intensity
  onSelect={(row, col) => setSelectedCell({ region: row.name, status: col })}
/>
```

### 2.8 `TrendChart` (Time Series Scrubber)
Best for multi-month tracking with interactive hover readout tooltip.

```jsx
import { TrendChart } from '../dashboard/charts';

<TrendChart
  points={[
    { x: '2026-01', requests: 12, projects: 8 },
    { x: '2026-02', requests: 15, projects: 11 },
  ]}
  series={[
    { key: 'requests', label: 'Requests raised', color: '#2a78d6' },
    { key: 'projects', label: 'Projects created', color: '#eb6834' },
  ]}
  height={200}
  xLabel={(x) => x.slice(5)} // e.g. "01", "02"
/>
```

### 2.9 `Meter` (Compliance Gauge)
Best for governance passing/failing percentages.

```jsx
import { Meter } from '../dashboard/charts';

<Meter
  label="Baselining compliance"
  passed={84}
  total={92}
  note="A baseline is recorded once a project passes Planning."
/>
```

### 2.10 `DataTable` (Interactive Tabular Grid)
Best for low-level project or request audit listings.

```jsx
import DataTable from '../ui/DataTable';
import { col } from '../../lib/projectColumns';

<DataTable
  rows={openProjectsList}
  columns={[
    col.project(),
    col.cio,
    col.portfolio,
    col.health,
    col.state,
    col.phase,
    col.goLive,
    col.pm,
  ]}
  initialSort={{ key: 'planned_deployment_date', dir: 'asc' }}
  searchKeys={['name', 'project_number', 'cio', 'portfolio', 'delivery_manager']}
  searchPlaceholder="Search project, PM, CIO..."
  onRowClick={(r) => openProject(r.project_number)} // Opens sliding ProjectDrawer
  exportName="open-projects-report"
  limit={15}
/>
```

---

## 3. Step-by-Step Recipes

### Recipe A: How to Build a New Standard Report Page

Follow this exact pattern when generating a new report component:

```jsx
import React, { useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import { ReportFrame, Footnote } from '../ui/Page';
import Panel from '../ui/Panel';
import DataTable from '../ui/DataTable';
import Callout from '../ui/Callout';
import KpiCard from '../dashboard/KpiCard';
import { Donut, BarList, Columns } from '../dashboard/charts';
import { useReport, combine } from '../../lib/api';
import { useProjects, countBy } from '../../lib/useProjects';
import { useProjectDrawer } from '../../lib/projectDrawer';
import { col } from '../../lib/projectColumns';
import { count, money, daysLabel } from '../../lib/format';

export default function MyNewDeliveryReport() {
  // 1. Consume filters from AppShell outlet
  const { filters, setFilters } = useOutletContext();

  // 2. Fetch necessary API endpoints in parallel
  const apiState = useReport('/api/delivery', filters);
  const projectRowsState = useProjects(filters);

  // 3. Open sliding project drawer on row clicks
  const { openProject } = useProjectDrawer();

  return (
    <ReportFrame
      eyebrow="Delivery"
      title="Milestone & Phase Progress"
      subtitle="Tracking delivery velocity and phase transitions across active portfolio initiatives."
      state={combine(apiState, projectRowsState)}
      isEmpty={([d, p]) => !p.rows.some((r) => r.is_open)}
      filters={filters}
    >
      {([data, projects]) => (
        <ReportBody
          data={data}
          rows={projects.rows}
          setFilters={setFilters}
          openProject={openProject}
        />
      )}
    </ReportFrame>
  );
}

function ReportBody({ data, rows, setFilters, openProject }) {
  // Compute local slice logic memoized
  const open = useMemo(() => rows.filter((r) => r.is_open), [rows]);
  const delayed = useMemo(() => open.filter((r) => r.is_past_due), [open]);
  const byPortfolio = useMemo(() => countBy(delayed, 'portfolio'), [delayed]);

  return (
    <>
      {/* Optional Business Rule Callout */}
      <Callout title="Measurement Scope">
        Only projects currently in delivery phases are evaluated. Projects on hold are excluded from schedule lateness.
      </Callout>

      {/* Top Metric Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <KpiCard
          title="Open projects"
          value={count(open.length)}
          subtitle="Project Created, Active, On Hold"
        />
        <KpiCard
          title="Delayed go-lives"
          tone={delayed.length ? 'critical' : 'good'}
          value={count(delayed.length)}
          subtitle={`of ${open.length} open`}
          tip="overdueGoLive"
        />
        <KpiCard
          title="Average slip"
          value="18 d"
          subtitle="against first baseline"
          tip="slipVsBaseline"
        />
        <KpiCard
          title="Key priorities"
          value={count(open.filter((r) => r.key_priority_project === 'Yes').length)}
          subtitle="requiring executive focus"
        />
      </div>

      {/* Visual Analytics 12-Column Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
        <Panel title="Delayed by portfolio" note="click bar to filter" className="xl:col-span-6">
          <BarList
            rows={byPortfolio}
            labelWidth="w-40"
            color="#d03b3b"
            onSelect={(item) => setFilters((f) => ({ ...f, portfolio: item }))}
          />
        </Panel>

        <Panel title="Schedule variance spread" note="days past planned date" className="xl:col-span-6">
          <Columns
            rows={[
              { name: '1–30 d', value: 12 },
              { name: '31–60 d', value: 6 },
              { name: '61–90 d', value: 4 },
              { name: '90+ d', value: 2 },
            ]}
            ordinal={true}
          />
        </Panel>
      </div>

      {/* Interactive Audit Table */}
      <Panel strip title="Delayed delivery projects" note="most overdue first">
        <DataTable
          rows={delayed}
          columns={[
            col.project(),
            col.pastDue,
            col.goLive,
            col.slip,
            col.kpi('schedule_kpi', 'Schedule'),
            col.health,
            col.phase,
            col.pm,
          ]}
          initialSort={{ key: 'days_past_due', dir: 'desc' }}
          onRowClick={(r) => openProject(r.project_number)}
          exportName="delayed-delivery-projects"
          limit={15}
        />
      </Panel>

      {/* Explanatory Footnote */}
      <Footnote>
        Delayed projects reflect planned deployment dates strictly prior to the reporting date with no actual deployment date recorded.
      </Footnote>
    </>
  );
}
```

---

### Recipe B: How to Build a Custom Dashboard & Add New Widgets

A Custom Dashboard (`src/components/customReports/CustomDashboard.jsx`) arranges widgets on a 12-column grid.

#### Step 1: Define the Widget Specification
A widget is stored in the database with this JSON schema:

```json
{
  "id": "w_overdue_portfolio",
  "title": "Overdue Projects by Portfolio",
  "question": "Which portfolios have the most overdue go-lives?",
  "source": "certified",
  "source_label": "Certified",
  "viz": "bar",
  "data": {
    "metric": "overdue_go_live",
    "breakdown": ["portfolio"],
    "filters": {},
    "sort": "value_desc",
    "limit": 10
  },
  "span": 1
}
```

#### Step 2: Implement the Widget Visual Rendering in `WidgetCard.jsx`
`WidgetCard` fetches `/api/custom-reports/widgets/:id/data` passing the current dashboard filters.
It handles 4 visual representations:
- **`kpi`**: Renders `<KpiCard kpi={data.kpi} />`
- **`bar` / `line`**: Renders `<ResultChart chart={data.chart} rows={data.rows} columns={data.columns} />`
- **`table`**: Renders `<ResultTable msg={{ columns, rows }} />`
- **`auto`**: Adapts based on row count and column types.

#### Step 3: Implement Dashboard Grid Layout
In your custom report template:

```jsx
<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
  {dashboard.items.map((item) => (
    <div
      key={item.widget.id}
      className={item.span === 2 ? 'col-span-1 md:col-span-2' : 'col-span-1'}
    >
      <WidgetCard
        item={item}
        filters={filters}
        edit={isEditing}
        onRemove={handleRemoveWidget}
        onSpan={handleToggleSpan}
        onTitle={handleRenameWidget}
        onView={handleChangeViz}
      />
    </div>
  ))}
</div>
```

---

### Recipe C: Registering Routes and Sidebar Navigation

When creating a new report screen:

1. **Add Route to `src/App.jsx`**:
   ```jsx
   <Route path="projects/delivery/:reportId" element={<DeliveryReport />} />
   ```
2. **Dispatch Component in Dispatcher** (e.g. `src/components/DeliveryReport.jsx`):
   ```jsx
   import MyNewReport from './delivery/MyNewReport';
   // ...
   if (reportId === 'my-new-report') return <MyNewReport />;
   ```
3. **Register in `src/navigation.js`**:
   ```javascript
   {
     name: 'DELIVERY',
     items: [
       { name: 'My New Report', path: '/projects/delivery/my-new-report', icon: CalendarDays },
     ]
   }
   ```
4. **Data Gap Guard**: If the report requires data that does not exist in the database yet:
   - Add entry to `DISABLED_REPORTS` in `src/navigation.js`.
   - Comment out navigation entry in `projectNavigation` or `requestNavigation`.
   - The route remains active and gracefully displays `<ReportUnavailable />`.

---

## 4. Formatters & Utility Standards (`src/lib/format.js`)

Always use the standard formatting helpers to ensure consistency across the application:

| Helper | Input | Output Example | Usage |
| :--- | :--- | :--- | :--- |
| `money(v, opts)` | `4250000` | `"$4.3M"` | Compact currency on cards and charts |
| `moneyExact(v)` | `4250000` | `"$4,250,000"` | Exact currency in tables or subtitles |
| `pct(v, total)` | `4, 10` | `"40.0%"` | Formats share with 1 decimal place |
| `pctValue(v)` | `12.4` | `"+12.4%"` | Signed percentage |
| `count(v)` | `1420` | `"1,420"` | Formatted integer with thousands commas |
| `day(iso)` | `"2026-10-14"` | `"14 Oct 2026"` | Standard business date representation |
| `daysLabel(n)` | `45` | `"45 d"` | Duration or age label |
| `monthLabel(m)` | `"2026-03"` | `"Mar '26"` | Compact month axis tick |
| `reasonLabel(r)`| `"Hold - Vendor"`| `"Vendor delay"` | Clean human-readable stop reason |

---

## 5. Verification & Acceptance Checklist for AI Agents

Before submitting any newly created dashboard, report, or widget, verify each of the following:

- [ ] **No "Actual Spend" Claims**: Does any label say "Spend", "Spent", or "Actual Cost"? (Must change to "Forecast EAC" or "Estimated Cost").
- [ ] **Reporting Date Integrity**: Are all date differences calculated against `asOf` from `/api/meta`? (Never against `new Date()`).
- [ ] **Status Color Integrity**: Does any code assume missing status = Green? (Missing status MUST be "Not set" in grey).
- [ ] **Responsive Grid**: Does the visual container use `grid grid-cols-1 xl:grid-cols-12 gap-3`?
- [ ] **Interactive Drawer**: Do project row clicks call `openProject(r.project_number)`?
- [ ] **CSV Export**: Does the `<DataTable>` provide an `exportName` and render valid CSV values?
- [ ] **Assistive Technology Labels**: Do SVG charts and icon buttons have `aria-label`, `role="img"`, or `title` attributes?
- [ ] **Filter Pass-Through**: Are global filters (`cio`, `region`, `portfolio`, etc.) passed to `useReport`?
- [ ] **Footnote & Caveats**: Is there a `<Footnote>` disclosing population boundaries, definitions, and data extract limitations?
