# API and Integration Catalog

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | L1 – Business only. **Every entry is Proposed** — derived from journeys J1–J11 (S01) and reference patterns (S14, S28). No OpenAPI specification or code was supplied, so nothing here is Specified or Implemented. |
| Sources analyzed | 31 files — see [project_summary.md](project_summary.md#source-inventory-and-coverage) |
| Related documents | [Project summary](project_summary.md) · [Architecture](architecture.md) · [Technical design](technical_document.md) · [Function document](function_document.md) · [DB scheme](db_scheme.md) · [Requirements](requirements.md) · [Risk register](risk_register.md) |

## Table of contents

- [Conventions](#conventions)
- [Evidence status summary](#evidence-status-summary)
- [Endpoint catalog](#endpoint-catalog)
  - [Identity and access](#identity-and-access)
  - [Connections](#connections)
  - [Data model and starter packs](#data-model-and-starter-packs)
  - [Ask](#ask)
  - [Widgets](#widgets)
  - [Reports](#reports)
  - [Personal fields and custom SQL](#personal-fields-and-custom-sql)
  - [Certification](#certification)
  - [Dashboards and publishing](#dashboards-and-publishing)
  - [Reading](#reading)
  - [Starter content](#starter-content)
  - [Knowledge](#knowledge)
  - [Platform](#platform)
- [External integrations](#external-integrations)
- [Error code catalogue](#error-code-catalogue)
- [Open questions](#open-questions-for-the-api)
- [Change log](#change-log)

---

## Conventions

| Topic | Rule (Proposed) |
|---|---|
| Base path | `/api/v1` (internal service APIs of runner and gateway are not public and are listed under API-062…API-069 only as integrations) |
| Auth | `Authorization: Bearer <access token>` issued by Keycloak brokering Entra ID (API-058). All endpoints require auth except `GET /healthz`, `GET /readyz`. SCIM uses a separate provisioning bearer token. |
| Authorization | Permission keys listed per endpoint (catalogue in [technical_document.md](technical_document.md#authentication-and-authorization-implementation)). Data scope (FEAT-003) applies to every endpoint returning source data. |
| Tenancy | Tenant from token; never from request body. Cross-tenant or out-of-scope object IDs return **404** (do not reveal existence). |
| Content type | `application/json; charset=utf-8`; SSE `text/event-stream`; uploads `multipart/form-data` |
| Errors | `{"error": {"code": "sql_not_select", "message": "…", "request_id": "…", "details": {}}}` — codes in [Error code catalogue](#error-code-catalogue) |
| Pagination | Cursor: `?limit=50&cursor=<opaque>` (max 100) → `{"items": [], "next_cursor": "…"}` |
| Idempotency | `Idempotency-Key` header on creating POSTs marked "Idempotent: key"; replay within 24 h returns the first response |
| Concurrency | `ETag` on drafts/layouts/models; `If-Match` required on PUT/PATCH of those → 412 on mismatch |
| Rate limits | Default 120 req/min/user; specific limits listed per endpoint; 429 with `Retry-After` |
| IDs | UUID v7 strings |
| Time | ISO 8601 UTC; `as_of` always present on data responses (RULE-018) |

## Evidence status summary

| Status | Count | Entries |
|---|---|---|
| Specified (from an API spec) | 0 | — |
| Implemented (from code) | 0 | — |
| Proposed (from business documents) | 69 | API-001…API-069 |

---

## Endpoint catalog

### Identity and access

### API-001 Get current user

- **Method/path:** `GET /api/v1/me` · **Permission:** any authenticated · **Features:** FEAT-001, FEAT-017
- **Purpose:** Profile, roles, permissions, scope summary for the greeting ("You see projects in KSA and GCC - UAE").
- **Response 200:**

```json
{
  "user_id": "0192f3b2-7c1e-7a10-9f00-0000000000a1",
  "display_name": "Example Creator",
  "tenant_id": "0192f3b2-0000-7000-8000-000000000001",
  "roles": ["creator"],
  "audience_roles": ["delivery_manager"],
  "permissions": ["ask.use", "widgets.create", "reports.create", "customsql.use"],
  "scope_summary": [{"connection_id": "…", "text": "Region: KSA, GCC - UAE"}],
  "locale": "en-GB",
  "time_zone": "Asia/Dubai"
}
```

- **Errors:** 401 `token_invalid`. **Idempotent:** yes (GET).

### API-002 Logout

- **Method/path:** `POST /api/v1/auth/logout` · **Permission:** authenticated · **Feature:** FEAT-001
- **Purpose:** Revoke refresh token at Keycloak, end server session markers; SPA then redirects to Keycloak end-session endpoint.
- **Response:** 204. **Idempotent:** yes.

### API-003 SCIM users and groups

- **Method/path:** `/scim/v2/ServiceProviderConfig`, `/ResourceTypes`, `/Schemas`, `GET|POST /Users`, `GET|PUT|PATCH|DELETE /Users/{id}`, `GET|POST /Groups`, `GET|PATCH|DELETE /Groups/{id}` · **Auth:** SCIM provisioning token (per tenant) · **Feature:** FEAT-004 · **Source:** S03 §5.3
- **Purpose:** Microsoft Entra provisioning of users and group memberships into DATA-002, DATA-004, DATA-005.
- **Request example (POST /Users):**

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
  "externalId": "entra-object-id",
  "userName": "person@example.com",
  "name": {"givenName": "Alex", "familyName": "Morgan"},
  "active": true
}
```

- **Behaviour:** idempotent by `externalId`; `active:false` → user `SUSPENDED`, sessions revoked; DELETE → `DEPROVISIONED` (records retained for audit — S03 §7). SCIM filter `userName eq` and `externalId eq` only; pagination `startIndex/count`.
- **Errors:** SCIM error schema; 409 uniqueness; 400 invalidFilter. **Rate limit:** 600/min/tenant.

### API-004 Group role mappings

- **Method/path:** `GET /api/v1/admin/group-mappings`, `PUT /api/v1/admin/group-mappings` · **Permission:** `mappings.manage` · **Feature:** FEAT-002 · **Data:** DATA-008
- **Request (PUT, full replace, If-Match):**

```json
{
  "mappings": [
    {"idp_group_id": "entra-group-guid-pmo-admins", "role": "admin", "requires_approval": true},
    {"idp_group_id": "entra-group-guid-cio", "role": "creator", "audience_role": "cio"},
    {"idp_group_id": "entra-group-guid-all-staff", "role": "viewer"}
  ]
}
```

- **Rules:** mapping to `admin` requires a second admin's approval (`requires_approval` → pending until approved) — S03 §9 "privileged mappings need approval". Audit event on every change.
- **Errors:** 400 `unknown_group`, 409 `pending_approval`, 412 precondition.

### API-005 Scope policies

- **Method/path:** `GET|POST /api/v1/admin/scope-policies`, `PATCH|DELETE /api/v1/admin/scope-policies/{id}`, `POST /api/v1/admin/scope-policies/{id}/preview` · **Permission:** `policies.manage` · **Features:** FEAT-002, FEAT-003 · **Data:** DATA-009 · **Rules:** RULE-004, RULE-005
- **Request (POST):**

```json
{
  "connection_id": "…",
  "entity": "projects",
  "applies_to": {"role": "creator", "idp_group_id": "entra-group-guid-ksa"},
  "predicates": [{"field": "region", "op": "in", "values": ["KSA"]}],
  "description": "KSA delivery team sees KSA projects"
}
```

- **Preview response:** row count in scope for a sample principal, to support "Preview as role" (FEAT-027, FEAT-039).
- **Errors:** 400 `field_not_in_model`, 400 `predicate_invalid`.

### API-006 Field policies

- **Method/path:** `GET|PUT /api/v1/admin/field-policies` · **Permission:** `policies.manage` · **Feature:** FEAT-003 · **Data:** DATA-010
- **Purpose:** Field-level hiding per role, e.g. finance fields hidden for CIO audience (S01 §6).

```json
{"connection_id": "…", "rules": [{"field_ref": "field:projects.approved_budget_value_usd", "hidden_for": [{"audience_role": "cio"}]}]}
```

### Connections

### API-007 List and create connections

- **Method/path:** `GET /api/v1/connections`, `POST /api/v1/connections` · **Permission:** list = `connections.manage` (admins) or creators see name/engine only of connections their scope allows; create = `connections.manage` · **Feature:** FEAT-005, FEAT-012, FEAT-013 · **Data:** DATA-011 · **Idempotent:** key
- **Request (POST), examples per engine:**

```json
{"name": "TPG Postgres", "engine": "postgres", "host": "db.internal", "port": 5432, "database": "tpg", "username": "tela_ro", "password": "<write-only>", "tls": {"mode": "verify-full"}, "approved_schemas": ["public"]}
```

```json
{"name": "TPG Dataverse", "engine": "dataverse", "environment_url": "https://<org>.crm.dynamics.com", "tenant_id": "<entra-tenant-guid>", "client_id": "<app-id>", "client_secret": "<write-only>", "mode": "tds_preferred"}
```

```json
{"name": "Ops Mongo", "engine": "mongodb", "uri_without_credentials": "mongodb+srv://cluster.example.net/ops", "username": "tela_read", "password": "<write-only>"}
```

- **Behaviour:** secret fields are written to the secrets store and **never returned**; response contains `secret_ref` presence flag only. Connection created `status: draft` until API-008 passes.
- **Response 201:** `{"connection_id": "…", "status": "draft", "engine": "postgres"}`
- **Errors:** 400 `engine_unsupported`, 400 `validation_error`, 503 `secrets_unavailable`.

### API-008 Test connection

- **Method/path:** `POST /api/v1/connections/{id}/test` · **Permission:** `connections.manage` · **Feature:** FEAT-005 · **Rule:** RULE-001
- **Response 200:**

```json
{"reachable": true, "authenticated": true, "read_only": {"verdict": "safe", "checks": ["no write privileges on approved objects", "session read-only applied"]}, "latency_ms": 42}
```

- **Failure:** `read_only.verdict = "unsafe"` with reasons (e.g. "account has INSERT on projects") → status remains `draft`; execution is impossible until fixed. **Rate limit:** 10/min.

### API-009 Update or disable connection

- **Method/path:** `PATCH /api/v1/connections/{id}` (If-Match), `POST /api/v1/connections/{id}/disable`, `POST /api/v1/connections/{id}/enable` · **Permission:** `connections.manage` · **Feature:** FEAT-005
- **Behaviour:** credential change forces re-test; disable stops all widgets on it (viewers see "source unavailable"); invalidates cache (ADR-010).

### API-010 Introspect schema

- **Method/path:** `POST /api/v1/connections/{id}/introspect` (202 + job), `GET /api/v1/connections/{id}/objects` · **Permission:** `connections.manage` · **Feature:** FEAT-006 · **Data:** DATA-012
- **Response (GET objects):**

```json
{"items": [{"object": "projects", "kind": "table", "columns": [{"name": "region", "type": "varchar", "nullable": true}], "keys": {"primary": ["project_id"], "foreign": [{"columns": ["project_request_id"], "ref": "project_requests.project_request_id"}]}, "row_estimate": 100}], "introspected_at": "2026-10-21T05:00:00Z", "samples_stored": false}
```

### API-011 Upload Excel or CSV file

- **Method/path:** `POST /api/v1/connections/files` (multipart: `file`, `name`), `POST /api/v1/connections/{id}/files` (new version) · **Permission:** `connections.manage` · **Feature:** FEAT-011 · **Data:** DATA-013 · **ADR:** ADR-012 · **Idempotent:** key
- **Limits:** `.xlsx`, `.csv` only; max 50 MB (Proposed); macro-enabled files rejected.
- **Response 202:** `{"connection_id": "…", "file_version": 1, "job_id": "…", "sheets_detected": 3}`
- **Errors:** 413 `file_too_large`, 415 `file_type_not_allowed`, 422 `file_unreadable`.

### Data model and starter packs

### API-012 Data models

- **Method/path:** `GET /api/v1/models?connection_id=`, `GET /api/v1/models/{model_id}`, `GET /api/v1/models/{model_id}/versions`, `GET /api/v1/models/{model_id}/draft`, `PATCH /api/v1/models/{model_id}/draft` (If-Match) · **Permission:** read = creators/admins (fields filtered by field policies); edit = `model.edit` · **Features:** FEAT-007…FEAT-009 · **Data:** DATA-014…DATA-021
- **Draft shape (abridged):**

```json
{"model_id": "…", "draft_etag": "W/\"7\"", "entities": [{"name": "projects", "label": "Projects", "fields": [{"name": "region", "label": "Region", "role": "dimension"}]}],
 "links": [{"from": "projects.project_request_id", "to": "project_requests.project_request_id", "cardinality": "many_to_one", "approved": false}],
 "measures": [{"key": "overdue_go_lives", "label": "Overdue go-lives", "rule_text": "Active projects whose planned deployment date is before the as-of date and not yet deployed", "expression": {"agg": "count", "entity": "projects", "where": "…"}, "format": "integer"}],
 "open_questions": [{"id": "…", "text": "Should cancelled projects count in Open projects?", "options": ["Yes", "No"]}]}
```

### API-013 Model assistant chat

- **Method/path:** `POST /api/v1/models/{model_id}/assistant` (SSE response) · **Permission:** `model.edit` · **Features:** FEAT-007, FEAT-008 · **Agent:** AG-04
- **Request:** `{"message": "Budget is in USD and excludes VAT; rename cio to CIO name"}`
- **SSE events:** `proposal` (ModelPatch with diff), `question`, `complete`, `error`. Patch is **not applied** until `POST /api/v1/models/{model_id}/draft/apply-patch` with `{"patch_id": "…"}`.
- **LLM review (US-245, DEC-28):** with the tenant's LLM review on, the rules propose first (or read the instruction) and one `model_assist` call reviews that patch (catalogue names, types and keys only, DEC-27). The `proposal` gains `review` (`{"verdict": "confirm|correct|ask|fallback", "reason": "…", "rules_left_out": [ops]}`) and `complete` gains `review` (the verdict). A proposal correction replaces the rules' patch after the same validation (`source: "llm"`); a correction to a parsed instruction never does: the rules' and the AI's readings become one clarifying question ("Which change do you mean?", options "As written: …" / "Or: …" with their `choices`). Review off: the LLM-first path as before, no `review` key.

### API-014 Approve model version

- **Method/path:** `POST /api/v1/models/{model_id}/approve` · **Permission:** `model.approve` · **Feature:** FEAT-009 · **Rule:** RULE-015 · **Idempotent:** key
- **Request:** `{"draft_etag": "W/\"7\"", "note": "v2 adds cost breakdown"}`
- **Response 201:** `{"model_version_id": "…", "version": 2, "impact": {"widgets_pinned_to_previous": 14, "published_dashboards": 3, "breaking_changes": [{"measure": "open_projects", "change": "rule_changed"}]}}`
- **Errors:** 409 `open_questions_unresolved`, 409 `unapproved_links_in_use`, 412.

### API-015 Starter packs

- **Method/path:** `GET /api/v1/starter-packs/match?connection_id=`, `POST /api/v1/starter-packs/{pack_id}/install` · **Permission:** `model.approve` · **Features:** FEAT-010, FEAT-051 · **Data:** DATA-022, DATA-023
- **Match response:** `{"matches": [{"pack_id": "pmo", "version": "1.0.0", "confidence": 0.92, "missing_objects": [], "field_mapping": {"projects.cio": "tpgmea_customcio"}}]}`
- **Install response 202:** job ID; creates certified measures and starter reports in a new model version. The pack's context documents are copied to the connection's knowledge, waiting for review (`documents`, US-233; API-073).

### Ask

### API-016 Ask stream

- **Method/path:** `POST /api/v1/ask/turns` → 202 `{"turn_id", "stream_url"}`, then `GET /api/v1/ask/turns/{turn_id}/stream` (SSE) · **Permission:** `ask.use` · **Features:** FEAT-018, FEAT-019, FEAT-022 · **Rate limit:** 30/min/user; concurrency 2/user (429 `rate_limited`)
- **Request:**

```json
{
  "question": "Overdue go-lives by region this quarter",
  "conversation_id": null,
  "connection_id": null,
  "context": {"widget_id": null, "dashboard_id": null, "page_filters": [{"ref": "dimension:priority", "op": "in", "values": ["Critical"]}]}
}
```

- **SSE events (in order; each has `id`, `event`, JSON `data` with `ts` and `stage_ms`):**

| Event | Data (abridged) |
|---|---|
| `intent` | `{"intent": "ask", "connection_id": "…", "model_version": 3}` |
| `clarify` | `{"question": "Quarter by planned or baselined date?", "options": [{"id": "a", "label": "Planned go-live date"}, {"id": "b", "label": "First baselined date"}]}` — stream then pauses; answer via API-018 |
| `retrieval` | `{"knowledge_refs": ["chunk:…"], "measures_considered": ["overdue_go_lives"]}` |
| `plan` | `{"spec": {QuerySpec}, "chips": [{"kind": "measure", "label": "Overdue go-lives"}, {"kind": "breakdown", "label": "Region"}]}` |
| `compile` | `{"engine": "postgres", "statement_sha256": "…"}` (SQL text included only for creators/admins) |
| `validate` | `{"approved": true, "checks": ["allow_list", "scope", "limits"]}` |
| `execute` | `{"row_count": 6, "truncated": false, "duration_ms": 184, "cache": "miss"}` |
| `viz` | `{"visual": "ranked_bar", "alternatives": ["columns", "table"], "chart_spec": {…}}` |
| `narrative` | `{"headline": "KSA has the most overdue go-lives (4).", "facts": [...]}` |
| `complete` | `{"turn_id": "…", "widget_draft_id": "…", "certified": true, "scope_text": "Region: KSA, GCC - UAE", "filters_text": "Priority: Critical", "as_of": "2026-10-21T06:00:00Z"}` |
| `error` | `{"code": "unsupported_question", "message": "There is no timesheet data in this model.", "missing_data": ["timesheets"]}` |

- **Definition explanation (US-245, DEC-28):** with the tenant's LLM review on, a definition answer always gets an AG-08 `explain` call; with no approved knowledge retrieved, the measure's plain rule and blank handling are the only text it may cite, and the kept sentences cite `measure:<key>` (`narrative.grounded_on: "rule"`, `definition.citations` empty). An explanation citing anything else is dropped (`citation_check: "failed"`). `narrate` still needs "send totals to the model" (DEC-01, RULE-019).
- Heartbeat comment every 15 s; reconnect with `Last-Event-ID`. Stream closes after `complete` or `error`.

### API-017 Get turn

- **Method/path:** `GET /api/v1/ask/turns/{turn_id}`, `GET /api/v1/ask/conversations?cursor=` · **Permission:** owner only · **Data:** DATA-039, DATA-040
- **Response:** full result (columns, rows, chart_spec, narrative, spec, as_of). Rows are re-fetched through the cache/engine if older than cache TTL, never stored (ADR-010).

### API-018 Answer clarification

- **Method/path:** `POST /api/v1/ask/turns/{turn_id}/clarification` · **Feature:** FEAT-019
- **Request:** `{"option_id": "a"}` or `{"free_text": "planned date"}` → stream resumes.

### API-019 Refine widget spec

- **Method/path:** `POST /api/v1/ask/turns/{turn_id}/refine` · **Feature:** FEAT-020 · **Agent:** AG-01/AG-03
- **Request:** `{"instruction": "top 5 only, as columns"}` or `{"chip_ops": [{"op": "set_limit", "value": 5}, {"op": "set_visual", "value": "columns"}]}`
- **Response:** new turn ID (SSE as API-016). Spec changes only; rule unchanged (S01 §4.4).
- **`set_measure` (US-219):** `{"op": "set_measure", "ref": "measure:go_lives_0_30"}` answers with another certified measure; filters and breakdowns it can't reach are dropped, extra and derived measures cleared. Offered by the future-tense data note.

### API-020 Undo spec change

- **Method/path:** `POST /api/v1/ask/turns/{turn_id}/undo` · **Feature:** FEAT-020 · **Response:** previous turn's spec restored as a new turn.

### Widgets

### API-021 Widgets CRUD

- **Method/path:** `GET /api/v1/widgets` (library), `POST /api/v1/widgets` (pin), `GET|PATCH|DELETE /api/v1/widgets/{id}` · **Permission:** `widgets.create` (owner) · **Features:** FEAT-021, FEAT-024 · **Data:** DATA-026 · **Idempotent:** key on POST
- **Pin request:**

```json
{"from_turn_id": "…", "title": "Overdue go-lives by region", "visual": "ranked_bar", "target": {"report_id": "…", "section_id": "…"}, "follow_page_filters": true}
```

- **Response 201:** widget with `badges: ["certified"]`, `spec`, `model_version_id`, `placement: {"x": 0, "y": 4, "w": 6, "h": 4}`.
- **Errors:** 422 `visual_not_suitable` with `reason`, 404 target.

### API-022 Run widget

- **Method/path:** `POST /api/v1/widgets/{id}/run` · **Permission:** `dashboards.view` + access to containing object · **Features:** FEAT-024, FEAT-042 · **Rules:** RULE-004, RULE-005, RULE-018
- **Request:** `{"page_filters": [{"ref": "dimension:region", "op": "in", "values": ["KSA"]}], "context": {"dashboard_version_id": "…"}}`
- **Response:** `{"columns": [...], "rows": [...], "chart_spec": {...}, "kpi": null, "truncated": false, "as_of": "…", "scope_text": "…", "badges": ["certified"], "hidden": false}`. If the viewer's field policy hides a field used by the widget: `{"hidden": true, "reason": "Uses fields not available to your role"}` (S01 §4.9).

### API-023 Suitable visuals

- **Method/path:** `POST /api/v1/visuals/suitable` · **Features:** FEAT-021, FEAT-026 · **Rule:** RULE-008
- **Request:** `{"spec": {QuerySpec}}` → **Response:** `{"suitable": ["ranked_bar", "columns", "table"], "disabled": [{"visual": "donut", "reason": "Donut needs parts of a whole"}, {"visual": "trend", "reason": "Trend needs a date breakdown"}]}`

### Reports

### API-024 Reports CRUD

- **Method/path:** `GET|POST /api/v1/reports`, `GET|PATCH|DELETE /api/v1/reports/{id}`, sections `POST|PATCH|DELETE /api/v1/reports/{id}/sections[/{section_id}]` · **Permission:** `reports.create` (owner/editor) · **Features:** FEAT-024, FEAT-027 · **Data:** DATA-027…DATA-029

### API-025 Plan report

- **Method/path:** `POST /api/v1/reports/plan` (SSE) and `POST /api/v1/reports/plans/{plan_id}/edit` · **Feature:** FEAT-023 · **Agent:** AG-05
- **Request:** `{"request": "Delivery health report for my region with overdue go-lives and budget exposure"}`
- **Plan event data:**

```json
{"plan_id": "…", "headline_figures": [{"measure": "open_projects"}, {"measure": "overdue_go_lives"}],
 "sections": [{"title": "Schedule", "widgets": [{"title": "Overdue go-lives by region", "visual": "ranked_bar", "certified": true}]}],
 "page_filters": ["region", "priority"],
 "gaps": [{"request_part": "team workload", "reason": "No workload or timesheet data in this model"}],
 "layout_preview": [{"widget_ref": "w1", "x": 0, "y": 0, "w": 3, "h": 2}],
 "diff": null}
```

- Edit response includes `diff: {"added": [...], "removed": [...]}`.
- With LLM review on (US-243, DEC-28), the plan and each edit carry `review`: `{"verdict": "confirm|correct|ask|fallback", "reason", "changes": ["Added Open projects by region", …], "added", "removed", "dropped": ["Left out “…”: “x” is not a certified measure you can use."], "showing": "ai|rules"}`. Review off: no `review` key, the plan as before.
- `POST /api/v1/reports/plans/{plan_id}/show` `{"show": "rules"|"ai"}` swaps a plan refined by AI with the rules plan (409 when the plan wasn't refined or is built).

### API-026 Build report

- **Method/path:** `POST /api/v1/reports/plans/{plan_id}/build` · **Feature:** FEAT-023 · **Idempotent:** key
- **Behaviour:** runs every planned widget once under the creator's scope; returns `{"report_id", "results": [{"widget_ref": "w1", "status": "ok", "rows": 6}, {"widget_ref": "w4", "status": "empty", "message": "Returns no data in your scope"}]}`.

### API-027 Report layout

- **Method/path:** `PUT /api/v1/reports/{id}/layout` (If-Match) · **Feature:** FEAT-024
- **Request:** `{"items": [{"widget_id": "…", "section_id": "…", "x": 0, "y": 0, "w": 6, "h": 4}]}` — validated: x+w ≤ 12, no overlap after compaction.

### API-028 Chat edit preview and apply

- **Method/path:** `POST /api/v1/reports/{id}/chat-edit` → preview; `POST /api/v1/reports/{id}/chat-edit/{preview_id}/apply`; `DELETE …/{preview_id}` (cancel) · **Features:** FEAT-025, FEAT-026 · **Agent:** AG-06
- **Preview response:** `{"preview_id": "…", "changes": [{"op": "resize", "target": "Overdue go-lives by region", "from": {"w": 6}, "to": {"w": 12}}, {"op": "refused", "request": "show trend as donut", "reason": "Donut cannot show change over time", "alternative": "Columns by month"}]}`
- **Request:** `{"instruction": "…", "answered": false}` — `answered: true` when the instruction answers a clarifying question (read by the rules, not reviewed again).
- **LLM review (US-244, DEC-28):** **off by default since EXEC-169** (`llm_review_off_agents` holds AG-05 and AG-06: the US-246 final gate left one confident-wrong preview), so `review` is null whatever the tenant's switch, unless an Admin turns AG-06 on in Admin → Agents (US-268, API-077). When put back in: with the tenant's LLM review on, one `canvas_edit` call (AG-06) reviews the rules' reading. The response gains `review` (`{"verdict": "confirm|correct|ask|fallback", "reason": "…", "notes": […]}`, null when off); changes the AI added carry `"by": "ai"`; a widget the AI and the rules read differently is the usual `clarification` (with `why`); an AI question's options may carry `instruction`, words the rules read exactly; a change the AI adds is kept only on a widget or filter the instruction's words name, and an added move only with a placement word (the rest listed in `review.notes`).

### API-029 Undo report change

- **Method/path:** `POST /api/v1/reports/{id}/undo`, `POST /api/v1/reports/{id}/undo-all` · **Features:** FEAT-025, FEAT-027 · **Data:** DATA-035

### Personal fields and custom SQL

### API-030 Personal fields

- **Method/path:** `GET|POST /api/v1/fields`, `GET|PATCH|DELETE /api/v1/fields/{id}` · **Permission:** `fields.create` · **Features:** FEAT-028…FEAT-030 · **Data:** DATA-024 · **Rules:** RULE-012…RULE-014
- **Grouping example:**

```json
{"kind": "grouping", "name": "Portfolio tier", "source_ref": "dimension:portfolio", "groups": [{"label": "Core", "values": ["Issuing", "Acquiring"]}, {"label": "Growth", "values": ["Digital"]}], "catch_all_label": "Everything else", "not_set_label": "Not set"}
```

- **Band example:** `{"kind": "band", "name": "Budget band", "source_ref": "field:projects.approved_budget_value_usd", "edges": [0, 1000000, 5000000], "labels": ["< 1M", "1M – 5M", "5M +"], "open_ended_top": true}`
- **Cross-table measure example:** `{"kind": "join_measure", "name": "Requests converted", "path": ["project_requests", "projects"], "agg": "count_distinct", "target": "project_requests.project_request_id"}`

### API-031 Preview personal field

- **Method/path:** `POST /api/v1/fields/preview` · **Response:** `{"buckets": [{"label": "Core", "count": 31}, {"label": "Everything else", "count": 9}, {"label": "Not set", "count": 2}], "edge_rules": "Lower edge included, upper edge excluded", "as_of": "…"}`

### API-032 Check and run custom SQL

- **Method/path:** `POST /api/v1/custom-sql/check`, `POST /api/v1/custom-sql/run`, `POST /api/v1/custom-sql/draft` (AG-10) · **Permission:** `customsql.use` · **Feature:** FEAT-031 · **Rules:** RULE-002, RULE-003, RULE-004, RULE-006, RULE-007 · **Rate limit:** 20/min/user
- **Request:** `{"connection_id": "…", "sql": "SELECT region, COUNT(*) AS n FROM projects WHERE :page_filters GROUP BY region"}`
- **Check response:** `{"ok": true, "checks": [{"name": "single_select", "ok": true}, {"name": "allowed_objects", "ok": true}, {"name": "scope_applied", "ok": true}, {"name": "page_filters_placeholder", "ok": true}, {"name": "limits", "ok": true, "detail": "10,000 rows, 20 s"}], "check_token": "…"}`
- **Run** requires `check_token` (binds statement hash, 5 min) → result as API-022 + `duration_ms`.
- **Errors:** 422 with codes `sql_parse_error`, `sql_multiple_statements`, `sql_not_select`, `sql_forbidden_node`, `sql_object_not_allowed`, `sql_field_hidden`, `scope_rewrite_failed`; 408/422 `query_timeout`; `engine_not_supported` for MongoDB.

### API-033 Request certification

- **Method/path:** `POST /api/v1/certification/requests` · **Permission:** `certification.request` · **Feature:** FEAT-032 · **Rule:** RULE-010 · **Idempotent:** key
- **Request:** `{"item": {"kind": "custom_sql_widget", "id": "…"}, "note": "Needed for Exec dashboard"}` → 201 `{"request_id", "status": "pending", "dependencies": [{"kind": "grouping", "id": "…", "status": "personal"}]}`

### Certification

### API-034 Certification queue

- **Method/path:** `GET /api/v1/certification/queue?kind=&status=pending&cursor=` · **Permission:** `certification.decide` · **Feature:** FEAT-033
- **Response item:** `{"request_id", "kind": "grouping", "name": "Portfolio tier", "author": {...}, "reason": "on dashboard pending publish", "lookalikes": 1, "submitted_at"}`

### API-035 Certification item

- **Method/path:** `GET /api/v1/certification/requests/{id}` · **Features:** FEAT-034…FEAT-036 · **Agent:** AG-09
- **Response:** plain-language rule (incl. blank handling), results across all data (admin scope), usage (reports, widgets, pending publishes), generated SQL/expression, dependencies, `lookalikes: [{"certified_ref": "measure:open_projects", "difference": "includes Cancelled", "result_delta": "+7"}]`, `semantic_checks: [{"check": "excludes_cancelled", "status": "differs", "detail": "Custom SQL counts cancelled invoices; certified measure excludes them"}]`.

### API-036 Certification decision

- **Method/path:** `POST /api/v1/certification/requests/{id}/decision` · **Permission:** `certification.decide` · **Feature:** FEAT-037 · **Data:** DATA-037 · **Idempotent:** key
- **Request:** `{"decision": "certify" | "merge" | "reject", "merge_into": "measure:open_projects", "note": "Use certified Open projects"}`
- **Effects:** certify → badge update everywhere (outbox event); merge → widgets switch to target, author notified (DATA-051); reject → returned with note. Decision snapshot stored (who, when, definition at that moment — S01 §4.8).
- **Errors:** 409 `already_decided`, 409 `dependency_pending`.

### Dashboards and publishing

### API-037 Dashboards CRUD

- **Method/path:** `GET|POST /api/v1/dashboards`, `GET|PATCH|DELETE /api/v1/dashboards/{id}`, `PUT /api/v1/dashboards/{id}/reports` (order) · **Permission:** `dashboards.create` · **Feature:** FEAT-038 · **Data:** DATA-030, DATA-031
- **Request (PATCH):** `{"layout_mode": "tabs" | "grouped", "shared_filters": [{"ref": "dimension:region"}, {"ref": "dimension:priority"}]}`

### API-038 Preview as role

- **Method/path:** `POST /api/v1/dashboards/{id}/preview` · **Features:** FEAT-027, FEAT-039 · **Permission:** `dashboards.publish` (or owner for own share preview)
- **Request:** `{"as": {"audience_role": "cio"}}` or `{"as": {"user_id": "…"}}`
- **Response:** `{"rows_in_scope": {"projects": 25}, "widgets": [{"widget_id": "…", "shown": false, "reason": "Finance fields hidden for CIO"}]}`

### API-039 Pre-publish checks

- **Method/path:** `POST /api/v1/dashboards/{id}/prechecks` · **Feature:** FEAT-039 · **Rule:** RULE-024
- **Request:** `{"target_roles": ["cio", "executive"]}`
- **Response:** `{"passed": false, "checks": [{"check": "all_widgets_certified", "passed": false, "items": [{"widget_id": "…", "fix": {"action": "request_certification"}}]}, {"check": "returns_data_for_each_role", "passed": true}, {"check": "shared_filters_reach_every_widget", "passed": true}]}`

### API-040 Publish dashboard

- **Method/path:** `POST /api/v1/dashboards/{id}/publish` · **Permission:** `dashboards.publish` · **Feature:** FEAT-039 · **Rules:** RULE-009, RULE-016, RULE-024 · **Idempotent:** key
- **Request:** `{"target_roles": ["cio", "executive"], "note": "October review"}`
- **Response 201:** `{"dashboard_version_id": "…", "version": 9, "live": true}` · **Errors:** 409 `precheck_failed` (current live version stays live), 409 `uncertified_widget`.

### API-041 Dashboard versions and restore

- **Method/path:** `GET /api/v1/dashboards/{id}/versions`, `POST /api/v1/dashboards/{id}/versions/{version}/restore` · **Feature:** FEAT-040 · **Rule:** RULE-016
- **Restore** creates a **new** version (e.g. restoring v7 when v9 is live creates v10 = copy of v7) — S01 §4.9.

### API-042 Shares

- **Method/path:** `GET|POST /api/v1/shares`, `DELETE /api/v1/shares/{id}` · **Permission:** `share.create` (owner) · **Feature:** FEAT-041 · **Data:** DATA-034
- **Request:** `{"object": {"kind": "dashboard", "id": "…"}, "with_user_ids": ["…"], "permission": "view"}` — `edit` reserved pending Q-005. Sharing a personal field or custom SQL widget enqueues nothing; publishing to a role does (RULE-010).

### Reading

### API-043 Published dashboards for me

- **Method/path:** `GET /api/v1/published`, `GET /api/v1/published/{dashboard_id}` (current live version for caller's audience roles) · **Permission:** `dashboards.view` · **Feature:** FEAT-042
- **Response:** dashboard version with reports (tabs or groups), widget placements, shared filters, `locked_scope_chip: "Region: KSA"`, `as_of`.

### API-044 Definition lookup

- **Method/path:** `GET /api/v1/definitions/{ref}?widget_id=` · **Permission:** `definitions.view` · **Feature:** FEAT-043
- **Response:** `{"ref": "measure:overdue_go_lives", "label": "Overdue go-lives", "rule_text": "…", "fields_used": ["current_state", "planned_deployment_date", "actual_deployment_date"], "blank_handling": "Projects with no planned date are not counted", "certified": {"by": "…", "at": "…", "model_version": 3}, "in_scope_text": "Within your scope (Region: KSA) and filters (Priority: Critical)"}`

### API-045 Drill records

- **Method/path:** `POST /api/v1/widgets/{id}/drill` · **Permission:** `drill.use` · **Feature:** FEAT-044 · **Rule:** RULE-025
- **Request:** `{"mark": {"region": "KSA"}, "page_filters": [...], "cursor": null, "limit": 100}`
- **Response:** `{"columns": [...], "rows": [...], "record_link_template": "…", "next_cursor": "…", "total_capped": false}` — record link target pending Q-004; hidden fields excluded.

### API-046 Export jobs

- **Method/path:** `POST /api/v1/exports`, `GET /api/v1/exports/{job_id}` · **Permission:** `export.use` · **Feature:** FEAT-045 · **Data:** DATA-049 · **Idempotent:** key · **Rate limit:** 10/hour/user
- **Request:** `{"dashboard_version_id": "…", "format": "pdf" | "xlsx", "page_filters": [...]}`
- **Job response:** `{"status": "queued|running|succeeded|failed", "download_url": "<signed, 15 min>", "expires_at": "…"}` — PDF includes every tab with filters and date in footer; Excel has one sheet per widget + definitions sheet (S01 §4.3). Authorisation re-checked at export time. Limits: Q-008.

### Starter content

### API-047 Starter content admin

- **Method/path:** `GET /api/v1/admin/starter`, `POST /api/v1/admin/starter/{item_id}/visibility` (`{"hide_from_roles": ["viewer"]}` / `{"show_to_all": true}`), `DELETE /api/v1/admin/starter/{item_id}` (returns effects preview first when `?dry_run=true`), `POST /api/v1/admin/starter/{item_id}/restore` · **Permission:** `starter.manage` · **Feature:** FEAT-046 · **Rule:** RULE-017
- **Dry-run response:** `{"effects": {"user_copies_unaffected": 12, "dashboards_getting_new_draft": [{"dashboard_id": "…", "live_version_stays": 9}]}}`

### API-048 Pack updates

- **Method/path:** `GET /api/v1/admin/packs/updates`, `POST /api/v1/admin/packs/{pack_id}/update` · **Permission:** `starter.manage` · **Feature:** FEAT-047
- **Response:** `{"pack_id": "pmo", "installed": "1.0.0", "available": "1.1.0", "changes": [...], "user_copies_affected": 0}`

### API-070 Pack Studio (US-232)

- **Method/path:** `GET /api/v1/packs` (system and tenant packs, one entry per key with every version and where it is installed, plus the Create methods), `POST /api/v1/packs` (`{"method": "blank" | "clone" | "model", "pack_key", "title", "version"?, "from_pack_id"?, "options"?}` → 201 draft, ETag, with `left_out` for what the method could not take; `model` is API-072), `GET /api/v1/packs/{pack_id}` (manifest in file order, document sizes, checks; ETag), `PATCH /api/v1/packs/{pack_id}` (`{"sections": {"entities": [...], "measures": [...], ...}}`, `If-Match`), `DELETE /api/v1/packs/{pack_id}` (a draft, `If-Match`), `POST /api/v1/packs/{pack_id}/validate` (`{"sections"?}`: checks the stored draft, or it with sections replaced, without saving), `GET /api/v1/packs/{pack_id}/versions`, `GET /api/v1/packs/{pack_id}/documents/{kind}`, `GET /api/v1/packs/{pack_id}/export` (zip of `<key>/manifest.yaml` and `<key>/<kind>.yaml`), `POST /api/v1/packs/import` (multipart `file`, optional `pack_key` → 201 draft), `POST /api/v1/packs/{pack_id}/try` (`{"connection_id", "measure"}` → `{"available", "value", "gaps", "confidence"}`) · **Permission:** `packs.author` (Admin) · **Feature:** FEAT-075 · **Decision:** DEC-26 · **Data:** DATA-022, DATA-052
- **Clone onto a source (US-235):** `POST /api/v1/packs/{pack_id}/repoint` (`{"connection_id", "mapping"?}`; without `mapping` the one saved for that pack and connection) → the starter-pack mapping step (`mapping_step`, `checks` with US-199 guards, US-200 value suggestions and `ai_available`, true only for a released pack, whose AI suggestions come from `POST /api/v1/starter-packs/{pack_id}/ai-suggestions`), `candidates` (`[{"ref", "kind", "name"}]`: source names the clone adds) and `unmapped` (`[{"ref": "entity:…" | "field:entity.field" | "measure:…" | "dimension:…" | "report:…", "kind", "key", "title", "reason", "removes_also"?}]`, grouped by kind; reports the pack itself marks unavailable are not listed). Nothing is saved. Create with `POST /api/v1/packs` `{"method": "clone", "from_pack_id", "pack_key", "title", "options": {"connection_id", "mapping", "remove": [refs], "renames": {"entities": {key: {"key"?, "label"?}}, "fields": {"entity.field": {...}}, "measures": {key: {...}}}}}`: confirmed names are added to `object_candidates` / `columns`, removed items go with what reads them, renames rewrite every reference (manifest, blueprints and tones, chart patterns, recipes, glossary, dashboard rules, gold examples). 400 for a mapping that doesn't fit, two pack fields on one column, a rename clash, a field a calculator reads by name, or removing a key field or the primary table.
- **Rules:** the revision (ETag) is the manifest's SHA-256; a write without `If-Match` or on a stale revision answers 412 with `details.current_revision`. A system pack or a released version answers 409 (clone it, or open the next draft, US-238). Schema errors answer 400 with `details.errors` and `details.warnings` (`[{"path": "measures[3].expression.agg", "message": "…"}]`) and nothing is saved; reference warnings (a rule reading a field the pack lacks) are saved and returned in `checks`. A key another pack holds (a system pack's, or the same key and version) answers 409 with `details.field = "pack_key"`. The item models the forms use are in the OpenAPI components as `PackEntity`, `PackField`, `PackLink`, `PackMeasure`, `PackDimension`.

### API-071 Pack report blueprints and starter dashboards (US-234)

- **Method/path:** `GET /api/v1/packs/{pack_id}/reports` (ETag), `PUT|DELETE /api/v1/packs/{pack_id}/reports/blueprints/{id}` (`{"blueprint": {...}, "tones": {measure: {...} | null}}`), `PUT|DELETE /api/v1/packs/{pack_id}/reports/dashboards/{key}` (`{"title", "reports": [blueprint ids, in tab order], "filters": [dimension keys]}`), `POST /api/v1/packs/{pack_id}/reports/preview` (`{"blueprint", "connection_id", "run": true}`), `POST /api/v1/packs/{pack_id}/reports/from-report` (`{"report_id", "dashboard", "id"?, "save"}`), `POST /api/v1/packs/{pack_id}/reports/from-dashboard` (`{"dashboard_id", "key"?, "title"?, "save"}`) · **Permission:** `packs.author` · **Feature:** FEAT-075 · **Decision:** DEC-26
- **Rules:** writes need `If-Match` (412 when stale); a system pack or a released version answers 409 (clone it, or open the next draft); a blueprint or dashboard with problems answers 400 with `details.problems` (`[{"path": "widgets.1", "message": "…"}]`). Preview runs each widget under the caller's scope on that connection's approved model and lists the parts it lacks as `gaps`. Save-as names each widget left out (`left_out: [{"title", "reason"}]`).

### API-072 Pack from an approved model (US-236)

- **Method/path:** `GET /api/v1/packs/from-model/models` (connections with an approved current model: `connection_id`, `name`, `model_version_id`, `version_no`, `counts`), `GET /api/v1/packs/from-model/models/{connection_id}` (what a pack from it would hold: `counts` incl. `links_not_accepted` and `personal_fields`, `measures` with `status`, `knowledge` `[{"kind", "count", "available", "reason"}]`, the `reports` and `dashboards` on that connection the caller can read, `installed_pack`, `reporting_date`), and `POST /api/v1/packs` with `{"method": "model", "pack_key", "title", "options": {"connection_id", "measures"?: [uncertified keys to take], "report_ids"?: [...], "dashboard_ids"?: [...], "knowledge"?: [kinds; default all]}}` → 201 draft with `left_out: [{"kind", "key", "reason", "report"?}]` and `source: {"connection_id", "model_version_id", "version_no", "reporting_date"}` · **Permission:** `packs.author` · **Feature:** FEAT-075 · **Decision:** DEC-26
- **Rules:** the draft takes the entities and fields (source names first in `object_candidates` / `columns`, then the installed pack's; sensitivity and semantic tags kept; a field hidden from queries only when something reads it), accepted links, certified measures (an uncertified one only when listed in `measures`), dimensions, the installed pack's glossary, dashboard rules, chart patterns and analysis recipes whose knowledge document is approved for this model version (cut to the pack's measures and dimensions), approved value synonyms and approved gold examples (spec without connection and model version), and the chosen dashboards (starter dashboards) and reports (blueprints on a dashboard named after the primary entity). A connection without an approved model answers 404; an unknown measure or knowledge kind 400. Audit `pack.created` with `method: model` and `source`.

### API-073 Pack knowledge editors and context documents (US-233)

- **Method/path:** `GET /api/v1/packs/{pack_id}/knowledge` (each kind's count, revision, error and warning counts; the pack's measure, dimension and field names; the attached documents), `GET /api/v1/packs/{pack_id}/knowledge/{kind}` (the document, `checks` with each item's references and problems; ETag), `PUT /api/v1/packs/{pack_id}/knowledge/{kind}` (`{"document": {...}}`, `If-Match`), `POST /api/v1/packs/{pack_id}/knowledge/{kind}/check` (`{"document"}` → checks, nothing saved), `POST /api/v1/packs/{pack_id}/knowledge/attachments` (multipart `file`, `title`? → 201), `DELETE /api/v1/packs/{pack_id}/knowledge/attachments/{id}` (204), `GET /api/v1/packs/{pack_id}/knowledge/attachments/{id}/file` · **Permission:** `packs.author` · **Feature:** FEAT-075 · **Decision:** DEC-26 · **Data:** DATA-052, DATA-053, DATA-042
- **Kinds:** `glossary`, `value_synonyms`, `dashboard_rules` (rules and `wording`), `chart_patterns`, `analysis_recipes`, `gold_examples`; `report_blueprints` is API-071's (404 here). A kind the pack doesn't have yet reads as an empty document (`stored: false`) and is created by the first save.
- **Rules:** the revision (ETag) is the kind's own, `"k<sha>"` of the document body, so editing one kind never clashes with the manifest or another kind; a write without `If-Match` or on a stale revision answers 412 with `details.current_revision`. A system pack or a released version answers 409. Schema errors (the loaders' models in `guidance.py`, `patterns.py`, `values.py`) answer 400 with `details.errors` (`[{"path": "glossary.terms[3].means", "message": "…"}]`) and nothing is saved; a name the pack lacks (a rule's measure, a value's dimension, a term's `entity.field`, a pattern's or recipe's keys, a gold spec's `ref`) is a warning, saved and returned per item: `items: [{"index", "title", "refs": {"measures", "dimensions", "fields"}, "missing": ["measure:burn_rate"], "errors", "warnings"}]`.
- **Context documents:** Markdown, text, PDF or Word, checked like API-049 uploads (type, contents, ≤ `knowledge_max_upload_mb`); the same file twice on one pack answers 409. A clone keeps its source's attachments. **Install** (API-015) copies each to the connection's knowledge as a new `uploaded_doc` in `pending_review` (worker indexes it; US-102 review gate and US-203 definition proposals apply); the install response says so in `documents: {"attached", "copied": [{"document_id", "title"}], "already_there"}`. A document with the same content already on that connection (not retired) is not copied again.

### API-074 AI pack author (US-237)

- **Method/path:** `POST /api/v1/packs` with `{"method": "ai", "pack_key", "title", "options": {"connection_id", "template_pack_id"?}}` → 201 empty draft with `source: {"connection_id", "template_pack_id"}` (the connection's schema must have been read; the template defaults to the first released system pack), `GET /api/v1/packs/{pack_id}/author?connection_id=` (`ai`, `provider`, `samples`, `templates`, `template_id`, `tables` with `in_draft`, `stages`, `knowledge` counts), `POST /api/v1/packs/{pack_id}/author` (SSE; `{"connection_id", "stage": "entities" | "links" | "measures" | "dimensions" | "synonyms" | "knowledge", "kind"? (knowledge: `glossary`, `value_synonyms`, `dashboard_rules`, `chart_patterns`, `analysis_recipes`), "template_pack_id"?, "tables"? (qualified names), "entities"? (keys)}` → events `stage` `{ai, provider, samples, template}`, `proposal` and `dropped` `{"id", "kind", "key", "title", "item", "source": "ai" | "rules", "reason", "checks": {"errors", "warnings", "count", "count_of": "rows" | "value" | "values" | "bands"}}`, `note`, `complete` `{proposed, dropped, sources}`, `error`), `POST /api/v1/packs/{pack_id}/author/check` (`{"connection_id", "kind", "items"}` → `{"valid", "items": [{"index", "item", "errors", "warnings", "count", "count_of"}]}`, nothing saved), `POST /api/v1/packs/{pack_id}/author/accept` (same body plus `sources`; `If-Match` for `entity`, `link`, `measure`, `dimension`, `synonyms`) → the pack as `PATCH` returns it, with `accepted` · **Permission:** `packs.author` · **Feature:** FEAT-075 · **Agent:** AG-04 (gateway task `pack_author`) · **Decision:** DEC-26, DEC-27
- **Rules:** nothing is written by a stage; accept checks every item again (a proposal is never trusted as sent) and answers 400 with `details.items[{index, errors}]` when one fails, 412 on a stale revision, 409 on a system pack or released version. Checks: the item's schema (`PackEntity`, `ModelLink`, `Measure`, `Dimension`, the knowledge kind's item model); references to the draft (a measure may read a linked entity's field); a key, link or knowledge item the draft already has; every literal text filter value, dimension value label and value synonym is a value of the column in Tela's own profile (US-199 trial read; case and spaces are corrected with a warning, any other value is an error, a column whose values are not read is an error); measures bind and compile on the connection and get a trial figure with US-199's sanity warnings (0, the same figure as another measure); entities get a row count; links have the "one" side checked unique by two counts (a warning if not). Synonyms merge into their targets without repeats; the first accepted entities set `signature.primary_entity` (the widest table) when none is set. Audit `pack.ai_drafted` (stage, counts, sources, `samples`) and `pack.ai_accepted` (kind, keys, sources); each stage is an agent run (`model_assist`, `object_ref` `pack:<id>`).
- **Model use:** with a provider set up, each batch (8 tables, one entity's ≤ 20 measures or dimensions, one knowledge kind, all synonyms) is one call with the template's items of the kind as worked examples and the rules draft to keep, correct or extend; a failed or invalid answer keeps the rules draft. Links are rules only. DEC-27: with *send samples* off or a hosted provider, only names and types of the source go out and the rules draft's values are sent as tokens (`[[status:1]]`) that Tela fills back; with it on and a local provider, up to 20 frequent values of each non-personal, non-confidential text column are sent and kept in the run log (`retrieve` step, `values_sent`). Labels of items the Admin accepted are pack names and are sent in the synonyms and knowledge stages.

### API-075 Pack release checks, review and versions (US-238)

- **Method/path:** `POST /api/v1/packs/{pack_id}/release-checks` (`{"connection_id"}` → `{"checks": {"passed", "errors", "warnings", "steps": [{"key", "label", "ran", "errors", "warnings"}], "facts": {"connection", "confidence", "compiled", "figures", "gold_planned"}}, "diff", "revision"}`; issues are `{"id", "level", "check", "path", "message"}`; nothing saved), `GET /api/v1/packs/{pack_id}/diff` (`{"against": {"id", "version"} | null, "changes": [{"kind", "key", "title", "change": "added" | "changed" | "removed", "fields"?, "rule"?: {"before", "after"}}], "summary"}`), `GET /api/v1/packs/{pack_id}/review` (`{"state", "people" (id → name), "review", "you": {"author", "can_submit", "can_withdraw", "can_release", "can_return", "can_open_next"}}`), `POST /api/v1/packs/{pack_id}/submit` (`{"connection_id", "reasons": {issue id: text}, "note"?}`, `If-Match`), `POST /api/v1/packs/{pack_id}/withdraw`, `POST /api/v1/packs/{pack_id}/return` (`{"reason"}`), `POST /api/v1/packs/{pack_id}/release` (→ the version with `diff` and `update_notice: {"connections", "notified"}`), `POST /api/v1/packs/{pack_id}/next-version` (`{"version"?}` → 201 the new draft) · **Permission:** submit, withdraw and next version `packs.author`; checks, diff, review, return and release `packs.author` or `certification.decide` · **Feature:** FEAT-075 · **Decision:** DEC-26 · **Data:** DATA-022, DATA-052, DATA-053
- **Checks:** *schema* (`validate`'s errors), *references* (what editing only warns about blocks here), *knowledge* (every glossary, value synonym, rule, pattern, recipe and gold item names what the pack has), *blueprints* (every starter widget and dashboard report list resolves in the pack), then on the test connection bound as an install binds it (saved field mapping too): *compile* (each servable measure and dimension compiles; one the source can't serve is a warning), *sanity* (one trial run of every measure, aggregates only: can't compute, returns 0, same figure with different rules), *reports* (each servable starter widget compiles; one left out is a warning), *gold* (plan only: each expected spec validates and compiles; no rows). Errors block; each warning needs a reason of 3+ characters at submission.
- **Rules:** submit needs a version above every released one of the key; the draft is then locked (every edit route answers 409 `details.reason: in_review`) until withdrawn or sent back. Release and send back refuse the author and the submitter (403 `details.reason: four_eyes`; the database refuses it too); release refuses a draft whose revision (manifest, documents, attachments) changed since submission (409), runs the checks again on the submitted connection and refuses new errors or warnings without a reason (400). The manifest is stored with its starter reports expanded (`manifest.expand_reports`) and frozen; `model.data_models` installed on an older version of the key get the US-120 `pack.update_available` notice at once (`updates.notify_updates`). Next version: on a released tenant version, the next minor (or the given higher version) as a draft with the manifest as written (`reports_from`), documents and attachments; 409 with `details.pack_id` when a draft of the key exists. Notifications `pack.review_requested` (reviewers), `pack.review_returned` and `pack.released` (author and submitter). Audit `pack.submitted`, `pack.withdrawn`, `pack.returned`, `pack.released` (author, submitter, revision, change summary, connections told), `pack.version_opened`.

### API-076 Pack items as one YAML file (US-247)

- **Method/path:** `GET /api/v1/packs/{pack_id}/bundle` (→ `{"yaml", "file_name"}`, `ETag`), `POST /api/v1/packs/{pack_id}/bundle` (`{"yaml", "mode": "merge" | "replace", "dry_run"}`; `If-Match` on import) · **Permission:** `packs.author` · **Features:** FEAT-075
- **File:** top-level `measures` and `dimensions` (lists, as in `manifest.yaml`, matched by `key`), `reports` (report blueprints as in `report_blueprints.yaml`, matched by `id`), `tones` (mapping) and `dashboards` (mapping key → `{title, reports, filters}`); every part optional, other keys refused. *Merge* adds and replaces by key; *replace* makes each part in the file the whole part (parts left out are kept).
- **Order:** measures and dimensions first, then dashboard shells, blueprints (each checked as API-071's PUT), then each file dashboard's tabs and filters. A data gap (blueprint with `unavailable`) listed as a tab stays in the pack without a tab and is returned as a warning; a blueprint on a dashboard neither the pack nor the file has is an error that says to add it under `dashboards`.
- **Response:** check → `{"valid", "errors", "warnings", "changes": {part: {"added", "updated", "removed", "unchanged"}}}`; import → the pack (as PATCH) with `checks` holding the same. Any error saves nothing (400, `details` as the check, paths in the file: `reports[2].kpis.0`, `dashboards.gulf.filters.0`); a stale revision is 412; system, released and in-review packs 409. Audit `pack.bundle_imported` with counts per part.

### API-077 Agents: prompts and skillsets (ENH-030, US-264 to US-266)

- **Method/path:** `GET /api/v1/admin/agents` (→ `{"items": [{"code", "name", "purpose", "role", "tasks", "review_off", "prompts", "edited", "skills", "skills_on", "stats": {"runs", "llm_calls", "failed", "fallbacks", "avg_ms", "tokens"}}], "shared": [prompt], "stats_days": 7, "limits": {"prompt": 20000, "skill": 8000, "skills_on": 12000}}`), `GET /api/v1/admin/agents/{code}` (adds `inputs`, `output`, `guard`, `knowledge: [{"label", "href"}]`, `prompts`, `shared`, `skills`), `PUT /api/v1/admin/agents/{code}/prompts/{key}` (`{"text", "note"?}`), `DELETE …/prompts/{key}` (back to the default), `GET …/prompts/{key}/versions`, `POST /api/v1/admin/agents/{code}/skills` (`{"name", "body", "enabled"}` → 201), `PUT|DELETE …/skills/{id}`. `code` is `AG-01` … `AG-10`, or `shared` for the LLM review frame · **Permission:** `settings.manage` · **Feature:** FEAT-080 · **Data:** `ops.agent_prompt_versions`, `ops.agent_skills`
- **Prompt:** `{"key", "agent", "task", "title", "used_for", "editable", "note", "edited", "version", "updated_at", "updated_by", "change_note", "default", "text"}`. Keys and defaults come from the code catalogue (`tela_api/agents/catalog.py`); a template prompt (`packs.author`, DEC-27) is read-only (400 on PUT). Every save is a new version; saving the default's own text or the current text again makes none. Empty text is 400 (use DELETE).
- **Run time:** `run_agent` swaps each default prompt found in the system message for the tenant's current version and adds the agent's skills that are on under "Skills from your Admin", before the JSON-schema instruction (cached 30 s per process, cleared on a save there). A failure to read them keeps the code defaults. The run log's `llm_call` step gets `detail.tuned: {"prompts", "skills"}` when any were used.
- **Try it (US-267):** `POST /api/v1/admin/agents/{code}/try` (`{"question", "connection_id"?, "prompt"?: {"key", "text"}, "skill"?: {"id"?, "name", "body", "enabled"}}`, `Accept: text/event-stream`) runs the question with the saved prompts and skills (`before`) and with the draft (`after`), one after the other (two at once timed out on a local model); nothing is saved and the runs are kept out of the agent run log. AG-01, AG-02, AG-03: Ask planning only (no query); AG-10: a Custom SQL draft. Events: `start` (`{"kind": "ask" | "sql", "connection": {"id", "name"}}`), `side` (`{"side", "status", "planner", "confidence"?, "reading"?, "spec"?, "clarify"?: {"question", "options"}, "sql"?, "reason", "calls": [{"agent", "task", "tuned", "ok", "reply" (≤ 4,000 characters), "ms"}], "took_ms"}`), `complete`; a `: ping` comment every 10 s. 400 for another agent, no draft or no model; 404 for a prompt of another agent.
- **LLM review per agent (US-268):** `PUT /api/v1/admin/agents/{code}/review` (`{"on": true | false | null}`; null goes back to the deployment default `TELA_LLM_REVIEW_OFF_AGENTS`) → `{"reviews", "on", "choice", "default_on", "switch_on"}`, also returned as `review` by both GETs (`review_off` = reviews and not on). Only the reviewing agents AG-01–AG-06 and AG-08 (400 otherwise, and 400 before AI settings are saved). Stored in `ops.llm_settings.review_agents`; the tenant's `llm_review` switch must still be on. Audit `agent_review.changed` (before, after, default_on).
- **Rules:** a skill name is unique per agent (409); skills that are on stay within 12,000 characters per agent (400). Audit `agent_prompt.changed` (agent, version, reset, characters, note) and `agent_skill.changed` (added, edited, deleted).

### Knowledge

### API-049 Knowledge documents

- **Method/path:** `GET|POST /api/v1/knowledge/documents` (multipart upload), `POST /api/v1/knowledge/documents/{id}/review` (`{"decision": "approve" | "reject"}`), `DELETE /api/v1/knowledge/documents/{id}` · **Permission:** `knowledge.manage` · **Feature:** FEAT-014 · **Data:** DATA-042, DATA-043
- New documents start `pending_review` (S02); only `approved` are retrievable.

### API-050 Gold examples

- **Method/path:** `GET|POST /api/v1/knowledge/gold-examples`, `PATCH|DELETE /api/v1/knowledge/gold-examples/{id}`, `POST /api/v1/knowledge/gold-examples/evaluate` · **Permission:** `knowledge.manage` · **Feature:** FEAT-015 · **Data:** DATA-044
- **Example:** `{"question": "How many projects are overdue in KSA?", "expected_spec": {QuerySpec}, "connection_id": "…", "tags": ["pmo", "schedule"]}`; evaluate returns pass rate by tag.

### API-051 Knowledge search

- **Method/path:** `POST /api/v1/knowledge/search` · **Permission:** `knowledge.manage` (debug/admin) — agents use the internal service call, not this endpoint · **Features:** FEAT-014, FEAT-016
- **Request:** `{"query": "how is overdue defined", "kinds": ["certified_measure", "glossary"], "k": 8}` → `{"hits": [{"chunk_id", "kind", "title", "snippet", "score"}]}`

### Platform

### API-052 Audit events

- **Method/path:** `GET /api/v1/admin/audit?action=&actor=&object=&from=&to=&cursor=` , `POST /api/v1/admin/audit/export` · **Permission:** `audit.read` · **Feature:** FEAT-048 · **Data:** DATA-045
- **Item:** `{"at", "actor", "action": "certification.decided", "object": {...}, "outcome": "allowed", "request_id", "details": {"decision": "merge"}}`

### API-053 LLM settings

- **Method/path:** `GET|PUT /api/v1/admin/llm-settings` · **Permission:** `settings.manage` · **Feature:** FEAT-049
- **Request:** `{"provider": "azure_openai", "models": {"planner": "<deployment-name>", "narrator": "<deployment-name>"}, "allow_aggregates": false, "monthly_token_budget": 5000000, "api_key": "<write-only>"}` — key stored in secrets store, never returned; change audited.
- **LLM review (US-239, DEC-28):** request `llm_review` (optional boolean): each agent's rules draft goes to the LLM, which confirms, corrects or asks. Left out, it keeps the saved value, or takes the provider's default when the provider changes (on for hosted, off for local). Response adds `llm_review` and `llm_review_default`. Stored in `ops.llm_settings.llm_review` (migration 0050).

### API-054 Usage

- **Method/path:** `GET /api/v1/admin/usage?from=&to=&group_by=model|user|task` · **Permission:** `settings.manage` · **Feature:** FEAT-049 · **Data:** DATA-046
- **Response:** tokens, calls, estimated cost (rates configured, not fetched), latency percentiles.

### API-055 Health and readiness

- **Method/path:** `GET /healthz` (liveness), `GET /readyz` (dependencies) on every service · **Auth:** none (internal network; edge blocks `/readyz` externally)
- **Response:** `{"status": "ok", "checks": {"db": "ok", "redis": "ok", "secrets": "ok"}}`; 503 when not ready.

### API-056 Home shelves

- **Method/path:** `GET /api/v1/home` · **Permission:** `dashboards.view` · **Feature:** FEAT-017
- **Response:** `{"greeting": {"name": "…", "scope_text": "…", "as_of": "…"}, "shelves": {"starter": [...], "published_to_my_role": [...], "mine": [...], "shared_with_me": [...]}}` — tile: `{"id", "type": "report|dashboard", "title", "badges": ["certified","starter"], "version": 9, "count": {"widgets": 8} | {"tabs": 3}, "last_edited_at"}`.

### API-057 Notifications

- **Method/path:** `GET /api/v1/notifications?unread=true`, `POST /api/v1/notifications/{id}/read` · **Data:** DATA-051
- Events: certification decided, field merged, pack update available, export ready, publish blocked.

---

## External integrations

| ID | Integration | Direction / protocol | Auth | Data exchanged | Limits / retries | Failure mode | Ownership | Evidence |
|---|---|---|---|---|---|---|---|---|
| API-058 | Microsoft Entra ID via Keycloak (OIDC) | Browser ↔ Keycloak ↔ Entra; Authorization Code + PKCE | Keycloak client (public SPA) + Entra app registration per tenant | ID/access tokens, `sub`, `oid`, `tid`, `groups` | JWKS cache 10 min | Login unavailable → users cannot sign in; API rejects | STK-006 | Proposed (S01 §2, S03 §5) |
| API-059 | Microsoft Graph (optional) | API/worker → Graph REST | Client credentials, `GroupMember.Read.All` (least privilege; confirm) | Group names/memberships when token groups overage | 429 `Retry-After`, 2 retries | Mapping job warns; SCIM preferred | STK-006 | Proposed (S03 §5) |
| API-060 | LLM providers (Azure OpenAI, Anthropic, Google Gemini, Ollama) via COMP-006 | Gateway → HTTPS | API key / managed identity (Azure) | Masked prompts: schema, definitions, question; aggregates only if allowed (RULE-019) | 30 s timeout, 1 retry; budget | Agents fail closed → user gets "can't answer reliably right now"; certified dashboards keep working (no LLM in render path) | STK-009 | Proposed (S06 §9, S26) |
| API-061 | Embedding provider | Gateway → Ollama HTTP or provider API | none (local) / API key | Text chunks of approved knowledge | 20 s, 2 retries | Indexing paused; retrieval uses last index | STK-007 | Proposed (S31) |
| API-062 | PostgreSQL source | Runner → TCP 5432, TLS | Read-only role | SELECT results | 20 s, 10,000 rows | `source_unreachable`, `query_timeout` | STK-005 | Proposed (mandated engine) |
| API-063 | MySQL source | Runner → TCP 3306, TLS | SELECT-only user | SELECT results | `max_execution_time` 20 s | as above | STK-005 | Proposed (mandated) |
| API-064 | SQL Server source | Runner → TCP 1433, TLS (ODBC 18) | `db_datareader`/SELECT grants | SELECT results | ODBC timeout 20 s | as above | STK-005 | Proposed (S01 §4.1) |
| API-065 | MongoDB source | Runner → TCP 27017 / SRV, TLS | `read` role | Aggregation results | `maxTimeMS` 20000 | as above | STK-005 | Proposed (mandated) |
| API-066 | Dataverse TDS endpoint | Runner → `<org>.crm.dynamics.com:1433` TDS/TLS | Entra service principal token for an application user | SELECT results (T-SQL subset) | endpoint limits + 20 s client | fall back to API-067 when disabled | STK-005 / Power Platform admin | Proposed — **verify against current Microsoft documentation (web lookup unavailable during authoring)** |
| API-067 | Dataverse Web API | Runner → `https://<org>.crm.dynamics.com/api/data/v9.2/` OData v4 / FetchXML | Same service principal (MSAL client credentials) | Metadata (`EntityDefinitions`), aggregates via `$apply`/FetchXML, records for drill | Service protection throttling: honour 429 `Retry-After`, 2 retries | slower but available | STK-005 | Proposed — verify as above |
| API-068 | Object storage (MinIO / S3 / Azure Blob) | API, worker, runner → HTTPS | Access key / managed identity | Uploaded Excel/CSV, export files, knowledge documents | 30 s, 3 retries | uploads/exports fail; dashboards unaffected | STK-007 | Proposed (ADR-012) |
| API-069 | Secrets store (OpenBao / Vault / Azure Key Vault) | Runner, gateway → HTTPS | AppRole / Kubernetes auth / managed identity | Source credentials, LLM keys | 5 s, 2 retries | fail closed: no execution | STK-007 | Proposed (S03, S31) |

Vendor lock-in notes: LLM and embedding providers are swappable behind COMP-006; object storage via S3 API with an Azure Blob adapter; secrets via adapter. Dataverse is inherently Microsoft-specific.

---

## Error code catalogue

| Code | HTTP | Meaning | Where |
|---|---|---|---|
| `token_invalid` | 401 | Missing/expired/invalid token | all |
| `forbidden` | 403 | Permission key missing | all |
| `not_found` | 404 | Unknown or not visible (incl. out of scope / other tenant) | all |
| `validation_error` | 400 | Body schema invalid | all |
| `precondition_failed` | 412 | ETag mismatch | drafts, layouts |
| `rate_limited` | 429 | Too many requests | all |
| `scope_unresolved` | 403 | No scope policy / attribute missing → deny | data endpoints |
| `scope_rewrite_failed` | 422 | Could not apply data rules; not run | custom SQL, compiler |
| `sql_parse_error`, `sql_multiple_statements`, `sql_not_select`, `sql_forbidden_node`, `sql_object_not_allowed`, `sql_field_hidden`, `sql_placeholder_misplaced` | 422 | Custom SQL validation failures | API-032 |
| `engine_not_supported` | 422 | Feature not available for engine (e.g. custom SQL on MongoDB) | API-032 |
| `visual_not_suitable` | 422 | Visual fails RULE-008 | API-021, API-028 |
| `query_timeout` | 504 | Exceeded 20 s | data endpoints |
| `source_unreachable` | 503 | Source down or disabled | data endpoints |
| `read_only_violation` | 503 | Account no longer verifiably read-only | data endpoints |
| `signature_invalid` | 500 (internal) | Runner rejected tampered statement | internal |
| `unsupported_question` | 200 (SSE `error`) | Model lacks data; gaps named | API-016 |
| `planner_unavailable`, `llm_timeout`, `leak_blocked`, `agent_failed` | 503 / SSE | AI path unavailable or blocked | API-013, 016, 025, 028 |
| `uncertified_widget`, `precheck_failed` | 409 | Publish blocked | API-040 |
| `open_questions_unresolved`, `unapproved_links_in_use` | 409 | Model approval blocked | API-014 |
| `already_decided`, `dependency_pending` | 409 | Certification conflicts | API-036 |
| `file_too_large`, `file_type_not_allowed`, `file_unreadable` | 413/415/422 | Upload problems | API-011 |
| `secrets_unavailable` | 503 | Secrets store unreachable | connections |

---

## Open questions for the API

| ID | Question | Affected endpoints |
|---|---|---|
| Q-004 | Where do drill record links point (Tela record view or source system, e.g. Dataverse record URL)? | API-045 |
| Q-005 | Is `edit` sharing allowed? | API-042 |
| Q-006 | Can creators publish to roles? | API-040, permission `dashboards.publish` |
| Q-007 | Do widgets auto-upgrade to a new model version? | API-014, API-022 |
| Q-008 | Export limits and formats | API-046 |
| Q-009 | May aggregates be sent to the LLM for narratives and widget follow-ups? | API-016, API-060 |
| Q-014 | SaaS multi-tenant Keycloak realm per tenant? | API-058, API-003 |
| Q-015 | Dataverse TDS availability in the client environment | API-066, API-067 |

## Change log

| Date | Version | Sources added | Summary |
|---|---|---|---|
| 2026-10-21 | 0.1 | S01–S31 | First proposed API catalogue (API-001…API-069) |
