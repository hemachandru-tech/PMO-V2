# Tela — Phase 1 Scope

*Derived from the "Tela I · clickable journeys" prototype (J0–J11). Sample data in the prototype (names, counts, figures) is illustrative only.*

---

## 1. Product summary

Tela is a chat-first reporting and dashboard tool. Admins connect existing databases read-only; people ask questions in plain English and chat builds widgets, reports and dashboards. Grouping, categorising and joining happen at query level and never change the source data. Every number traces back to a definition anyone can read, and the PMO admin certifies the definitions that everyone relies on.

**Tagline:** Ask. Build. Publish.

### How the pieces fit

```
Connection → Data model → Widget → Report → Dashboard → Published
```

| Object | What it is |
|---|---|
| **Connection** | A read-only link to one database (MySQL, Postgres, SQL Server). |
| **Data model** | What the data means: tables, links between them, measures, dimensions. Chat proposes it; the admin approves it. |
| **Widget** | One live chart, table or figure. Stores the *question* (measure, breakdown, filters, visual), never the numbers. Recomputed for each viewer. |
| **Report** | A page of widgets on a 12-column grid, with sections, headline figures and page filters. |
| **Dashboard** | Several reports shown as tabs or grouped on one page, with one shared filter bar. |
| **Published** | A dashboard version made live for chosen roles, with preview and rollback. |

Chat changes the data model and widget definitions; the engine writes the SQL. Every widget is computed for the person looking at it, inside their role's scope.

---

## 2. Roles

| Role | Can do |
|---|---|
| **Admin (PMO)** | Connect databases, approve the data model, certify definitions and custom SQL, publish to roles, manage starter content. |
| **Creator** (CIO, delivery manager, analyst) | Everything a Viewer can, plus: chat to build widgets, reports and dashboards; create personal groupings, bands, joins and custom SQL widgets; share with named people; request certification. |
| **Viewer** | Open published dashboards, filter, view definitions, drill to records, ask questions — always inside their own scope. Cannot build or pin. |

Role and data scope come from the user's Microsoft sign-in (group membership). Nothing is set up by hand in Tela.

---

## 3. Glossary

| Term | Meaning |
|---|---|
| **Measure** | Something counted or summed (e.g. Overdue go-lives, Approved budget). Its rule is written once and reused everywhere. |
| **Dimension** | Something you group by (e.g. Region, Programme, Lifecycle phase). |
| **Certified** | Approved by the PMO admin. The same question gives the same number in every widget and chat answer. |
| **Personal** | Made by one person. Usable in their own widgets and shareable; must be certified before publishing to a role. |
| **Custom SQL** | A widget written as a SQL query for questions the model can't answer yet. Read-only, inside the viewer's scope, badged until certified. |
| **Scope** | The rows a person may see, set by their role. Filters can narrow it, never widen it. |
| **Starter** | Ready-made, certified reports installed with a database. Can be copied, hidden, deleted and restored from the pack. |
| **Publish** | Make one version of a dashboard live for chosen roles. Earlier versions are kept and restorable. |

**Badges:** ✓ Certified · Personal · Custom SQL · Starter · Deleted

---

## 4. In scope — Phase 1

### 4.1 Connections and data model (J1, J11) — Admin

- Supported databases: **MySQL, Postgres, SQL Server**.
- Connection form: type, host, port, database, user, password; **Test connection**.
- Tela connects with a read-only user; it never writes to, changes or copies tables.
- On connect, Tela reads the schema (tables, columns, views). Column samples may be read to propose labels but are **not stored**.
- **Model assistant** (chat) proposes:
  - Links between tables, with cardinality (one-to-one, many-to-one).
  - Measures (sum, count, average).
  - Dimensions.
  - Explicit questions where it can't infer meaning (e.g. which region, which currency, whether VAT is included, whether cancelled records count).
- Admin corrects the model in plain language (rename fields, set descriptions, set currency formatting, change measure rules).
- **Approve model** → certified version (v1, v2, …), available to all creators on that connection.
- **Starter pack detection:** if the schema matches a known pack (e.g. PMO pack for a TPG database), offer to install it (certified measures + starter reports). If no pack fits, chat proposes a model from scratch.
- Multiple connections supported, each with its own data model. A dashboard may contain reports from different databases, but **each widget reads one database**.

### 4.2 Home (J2) — Everyone

- Persistent "Ask anything about your data" bar on every page.
- Greeting with the user's scope summary and a "data as of" timestamp.
- Four shelves:
  1. **Starter** — PMO starter reports; copy to make your own.
  2. **Published to my role** — official certified dashboards.
  3. **Mine** — own drafts and items.
  4. **Shared with me** — view-only items from colleagues.
- Tiles show type (report/dashboard), badges, version, widget or tab count, last edit.
- Entry points: **New report** (canvas) and **Build with chat**.

### 4.3 Reading a dashboard (J3) — Viewer

- Reports shown as **tabs** (or grouped page, see 4.8).
- **Filter bar** applies to every widget on every tab; a **locked scope chip** shows the role's scope and cannot be removed.
- Headline figures (KPIs) and charts update together when filters change.
- **Definition popover:** click a figure to see its certified rule, fields used, blank handling, and that it is within scope and filters.
- **Drill:** click a bar/mark to list the records behind it, with links to each record and "open as a list".
- **Ask about a widget:** follow-up questions answered in context, stating scope, filters and date.
- **Export:** PDF (every tab, filters and date in the footer) and Excel (one sheet per widget, plus definitions).
- "Data as of" timestamp shown.

### 4.4 Ask, refine and pin (J4) — Creator

- A chat answer is already a live widget: headline sentence, chart, definition, scope, date, and Certified badge when it uses certified measures.
- **Refine in words** (suggested chips or free text): filter, limit (top N), chart type, add columns, trend by date, compare with previous period.
- Refinement changes the widget's settings (spec), not its rule; the spec is shown as chips (measure, breakdown, filter, limit, visual, columns). **Undo** available.
- **Pin widget** dialog:
  - Title.
  - Visual — **only visuals that suit the data** are offered; unsuitable ones are shown disabled with the reason (e.g. donut needs parts of a whole; trend needs a date breakdown).
  - Target report and section.
  - **Follow the page filters** toggle (default on); the widget's own filter persists and shows as a badge.
- Pinned widget is placed in the chosen section at a sensible size.

### 4.5 Build a report from one request (J5) — Creator

- One sentence → a **plan** before anything is built: headline figures, sections, widgets per section (with visual type), page filters.
- Every planned widget uses a certified measure where one exists.
- **Gaps are named, not guessed:** if the data can't support part of the request (e.g. no workload/timesheet data), Tela says so and leaves it out.
- Plan editable in words (add/remove/move widgets, change visuals); diff shown (added / struck out).
- Live **layout preview** on the 12-column grid that updates with the plan.
- **Build report** runs every widget once under the creator's scope to confirm each returns data, then opens on the canvas.

### 4.6 Report canvas (J6) — Creator

- **12-column snap grid**; widgets placed and resized in grid units.
- **Widget library** of everything the user pinned; drop onto the page into free space.
- Selected widget toolbar: Rename, Chart type, Filters, Duplicate, Remove; corner resize handle.
- Add: Text note, Section, "Ask for a new widget" (goes to chat).
- **Edit with chat:** layout and content instructions (resize, move to headline, add filter, remove, limit, filter to a value).
  - Changes are **previewed** as a list before applying; Apply / Cancel; Undo after.
- Tela refuses misleading charts and offers a suitable alternative (e.g. donut for a time trend → columns by month).
- Sections: named groups of widgets; collapsible, renamable, draggable.
- Page filter bar shown on the canvas.
- **Draft model:** edits auto-save as a draft; viewers keep the last published version until **Republish**. **Undo all** and **Preview as role** available.

### 4.7 Grouping, bands, joins and custom SQL (J7) — Creator

All applied **in the query only**; stored data is never changed.

- **Grouping:** map dimension values into named groups (e.g. Core / Growth), with a catch-all for "everything else" (new values fall into it automatically) and a "Not set" bucket for blanks.
- **Bands:** bucket a numeric field into ranges with explicit edge rules shown (e.g. exactly 1M goes into 1–5M); editable in words.
- **Joins / cross-table measures:** only along links **approved in the data model**; distinct-count semantics stated.
- Results preview with counts before saving.
- **Save field** → personal definition, usable in any of the creator's widgets.
- **Custom SQL widget:**
  - Written by hand or drafted by chat.
  - **Check and run** validates before execution:
    - Single read-only `SELECT` only.
    - Only tables/views the connection allows.
    - Creator's scope applied.
    - Page filters injected at a `:page_filters` placeholder.
    - Limits: **10,000 rows**, **20 s timeout**.
  - Save as widget (Custom SQL badge everywhere it appears).
  - Shareable with named people; **cannot be published to a role until certified**.
  - **Request certification** sends it to the admin queue.

### 4.8 Certification (J8) — Admin

- **Queue** of items awaiting decision: groupings, bands, join measures, custom SQL widgets.
- Personal definitions enter the queue only when shared or placed on something going to a role.
- **Detail view** per item:
  - Plain-language rule, including blank handling and edge cases.
  - Results across all data.
  - Where it is used (reports, widgets, pending publishes).
  - The generated SQL / expression.
  - Dependencies on other pending items.
- **Look-alike detection:** flag similar existing certified fields that would give a different answer to the same question.
- **Semantic checks on custom SQL** against certified measures (e.g. includes cancelled invoices where the certified measure excludes them).
- Decisions: **Certify**, **Merge into existing field** (widgets switch over, author notified), **Reject / send back with note**.
- Every decision logged with who, when and the definition at that moment; badges update everywhere.

### 4.9 Compose and publish (J9) — Creator builds, Admin publishes

- Dashboard = ordered list of reports; reorder by drag or by chat.
- Layout choice: **Tabs** (one report at a time) or **Grouped on one page** (collapsible stacked groups, suited to scrolling/printing).
- **Shared filters** apply across every report and widget.
- **Publish dialog:**
  - Choose target roles (e.g. CIO, Executive, Delivery manager, Viewer).
  - **Preview as role / as a specific person:** row count in scope, widgets shown vs hidden and why (e.g. finance fields hidden for CIOs).
  - **Pre-publish checks:** all widgets certified; every widget returns data for every chosen role; shared filters reach every widget.
  - Failing checks block publishing, with inline fixes (request certification, remove widget). The current live version stays live meanwhile.
- **Versions:** each publish is a numbered version; history shown; **Restore** brings an old version back as a new version.
- Creators may share their own dashboards with named people without certification; publishing to a **role** requires every widget certified.

### 4.10 Starter content management (J10) — Admin

- List of starter reports with state, visibility, number of user copies, actions.
- **Hide from a role** / show to all.
- **Delete** with a confirmation that explains effects:
  - User copies stay and keep working.
  - Published dashboards that include it get a new draft without it; the live version stays until republished.
  - **Restore from pack** brings it back at any time.
- **Pack updates:** notify when a newer pack version is available; updating changes starter reports only, never users' copies.

### 4.11 Cross-cutting capabilities

- Chat-built widgets, reports and dashboards.
- Trend (time-series) charts.
- Chart types offered in the prototype: figure (KPI), ranked bars, columns, paired bars, table, trend/line, stacked status bars; donut only for parts of a whole.
- Every chat answer states scope and data date; figures come from the data model, never a guess.
- Click-to-explain on every element (definitions, badges, filters).

---

## 5. Out of scope — Later phases

- File uploads as a data source.
- Joins across two databases within one widget.
- Scheduled e-mail delivery.
- Writing data back to source systems.
- Status-over-time history (snapshots).

---

## 6. Non-functional requirements and guardrails

| Area | Requirement |
|---|---|
| **Data safety** | Read-only database users only. No writes, schema changes or copies of source tables. Column samples used for labelling are not stored. |
| **Security / scope** | Row-level scope derived from Microsoft group membership, enforced in every query: dashboards, chat answers, drill-downs, exports and custom SQL. Filters can narrow, never widen. Field-level hiding per role (e.g. finance fields for CIOs). |
| **Authentication** | Microsoft sign-in (SSO); role and scope from groups. |
| **Query limits** | Custom SQL: single SELECT, allowed tables/views only, 10,000-row cap, 20 s timeout. |
| **Consistency** | One certified definition per question; look-alike detection at certification. |
| **Auditability** | Certification decisions logged (who, when, definition snapshot). Dashboard versions retained and restorable. |
| **Transparency** | Every figure exposes its definition; SQL visible to creators/admins; data freshness ("as of") shown. |
| **Reversibility** | Undo on chat edits; drafts separate from published versions; restore for versions and deleted starters. |
| **Chart integrity** | Only visuals that suit the data are offered; misleading requests get a suitable alternative. |
| **Responsiveness** | Prototype shows report build in ~6 s and custom SQL in ~0.4 s on sample data; targets to be confirmed. |

---

## 7. Journey acceptance summary

| # | Journey | Role | Done when |
|---|---|---|---|
| J1 | Connect a database | Admin | Connection tested read-only; chat model corrected in a sentence; starter pack installed; model approved as certified v1. |
| J2 | First open (Home) | Everyone | Role and scope from SSO; four shelves visible with correct badges; dashboard opens. |
| J3 | Read a dashboard | Viewer | Tabs switch; filter changes every figure with scope locked; definition and drill work; follow-up answered in context; exports work. |
| J4 | Ask, refine, pin | Creator | Answer is a certified widget; word refinements change spec only; pin offers only suitable visuals and places widget in chosen section. |
| J5 | Report from one request | Creator | Plan shown before build; gaps named; plan editable in words with live layout preview; build validates every widget. |
| J6 | Edit canvas | Creator | Library drop snaps to grid; chat edits previewed, applied, undoable; sections added; unsuitable chart refused with alternative; draft until republish. |
| J7 | Groups, bands, joins, SQL | Creator | Personal fields saved and applied at query level; joins only via approved links; custom SQL passes all checks and stays badged. |
| J8 | Certify | Admin | Queue shows only shared/publish-bound items; look-alikes flagged; certify/merge/reject recorded and propagated. |
| J9 | Compose and publish | Creator + Admin | Tabs/grouped choice; preview as role; uncertified widget blocks publish; v9 published; versions restorable. |
| J10 | Manage starter content | Admin | Hide per role; delete with explained effects and restore; pack update leaves copies untouched. |
| J11 | Bring another database | Admin → Creator | Second connection with chat-proposed model approved; creators query it immediately; one database per widget. |

---

## 8. Assumptions and open questions

1. **Data freshness:** "Data as of 06:00" suggests a scheduled refresh or cache rather than fully live queries. What is the refresh model and cadence per connection?
2. **Starter packs:** how many packs ship in phase 1 (only the PMO pack?), and who authors and versions them?
3. **Role catalogue:** are roles fixed (Admin, Creator, Viewer + business roles like CIO, Executive, Delivery manager) or configurable? How are Microsoft groups mapped to roles and scope?
4. **Record links in drill-downs:** where do record links point — a Tela record view or the source system?
5. **Sharing permissions:** can shared items be edit-shared, or are they always view-only?
6. **Publishing rights:** can creators publish to roles, or only admins? (The prototype shows admin publishing.)
7. **Data model versioning:** how do changes to a certified model (v1 → v2) affect existing widgets and published dashboards?
8. **Export limits:** row limits and formatting rules for PDF/Excel exports.
9. **LLM data handling:** what data (schema, samples, query results) is sent to the language model, and what are the retention rules?
10. **Performance targets:** confirm query, build and page-load targets for production data volumes.
