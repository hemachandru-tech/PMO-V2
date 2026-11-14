# Data Dictionary — Business Data Terms, Classification and Source of Truth

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | L1 – Business only. Business terms come from S01 (scope glossary and badges) and are **Verified as specified**. Classifications, owners and retention periods are **Proposed**. PMO pack terms are **Reference (secondary)**, taken from S10–S16. |
| Sources analyzed | 31 files — see [project_summary.md](project_summary.md#source-inventory-and-coverage) |
| Related documents | [Project summary](project_summary.md) · [Architecture](architecture.md) · [Database schema](db_scheme.md) · [Business rules](business_rules.md) · [Function document](function_document.md) · [Risk register](risk_register.md) |

**Scope of this document.** This file defines what each business data term *means*, how it is classified, who owns it, how long it is kept and which system is its source of truth. The physical tables and columns live in [db_scheme.md](db_scheme.md#table-catalog). Calculation detail lives in [business_rules.md](business_rules.md).

**Why this file exists.** The data spans several systems: the connected customer sources, Tela's metadata database, the Redis cache, object storage, the secrets store and Keycloak. Formal classification and retention are also required (S01 §6, S09 GAP-003). Together these meet the trigger in master prompt §6.2.

---

## Table of contents

- [Business terms](#business-terms)
- [PMO starter pack terms](#pmo-starter-pack-terms)
- [Classification and retention](#classification-and-retention)
- [Source of truth map](#source-of-truth-map)
- [Data handling rules for LLM and RAG](#data-handling-rules-for-llm-and-rag)
- [Open questions](#open-questions)
- [Change log](#change-log)

---

## Business terms

Platform terms. "Owner" means the business owner who answers for the definition. The physical storage location is in db_scheme.md.

| DATA ref | Term | Business definition | Allowed values or format | Derivation | Classification | Business owner | Retention (Proposed) | Source system |
|---|---|---|---|---|---|---|---|---|
| DATA-011 | **Connection** | A read-only link to one source (S01 §1). | Engine: postgres, mysql, sqlserver, mongodb, excel, csv, dataverse | Created by an Admin. Credentials are held in the secrets store. | Confidential (descriptor), Sensitive (credentials) | STK-005 Data source owner, with STK-001 | Life of connection | Metadata DB plus secrets store |
| DATA-014/015 | **Data model** | What the data means: tables, links, measures and dimensions. Chat proposes it and the Admin approves it (S01 §1). | Versions v1, v2, … | Model assistant draft, then corrections, then approval | Internal | STK-001 Admin (PMO) | Life of tenant | Metadata DB |
| DATA-016 | **Entity** | A source table, view, collection, sheet or Dataverse table included in the model, with a business label and a grain statement. | — | Introspection plus labelling | Internal | STK-001 | With model version | Metadata DB (names only) |
| DATA-017 | **Field** | A column of an entity with its label, format, blank handling and sensitivity. | data_type; sensitivity: public, internal, confidential, personal, sensitive | Introspection plus admin corrections | Internal (metadata). The values it points to carry the field's sensitivity. | STK-001 | With model version | Metadata DB (definition), source (values) |
| DATA-018 | **Link** | An approved relationship between entities with cardinality. It is the only allowed join path (S01 §4.7). | one_to_one, many_to_one, one_to_many | From foreign keys or assistant proposal, then admin approval | Internal | STK-001 | With model version | Metadata DB |
| DATA-019 | **Measure** | Something counted or summed. Its rule is written once and reused everywhere (S01 §3). | Aggregation: count, count_distinct, sum, avg, min, max, ratio, custom | Typed expression over fields (RULE-014, RULE-023) | Internal (definition) | STK-008 PMO business owner (meaning), STK-001 (certification) | With model version | Metadata DB |
| DATA-020 | **Dimension** | Something you group by, for example Region, Programme or Lifecycle phase (S01 §3). | — | A field marked groupable | Internal | STK-008 | With model version | Metadata DB |
| — | **Certified** | Approved by the PMO admin. The same question gives the same number in every widget and chat answer (S01 §3). | Badge ✓ Certified | Certification decision (RULE-009, RULE-010) | Internal | STK-001 | Permanent decision record | Metadata DB (gov) |
| DATA-024 | **Personal definition** | A grouping, band or join measure made by one person. Usable in their own widgets and shareable. It must be certified before it is published to a role (S01 §3). | Badge "Personal". cert_status: personal, pending, certified, rejected, merged | Creator-defined (RULE-012, RULE-013, RULE-014) | Internal | The creator (STK-002) | Life of owner account, plus 90 days after deletion | Metadata DB |
| DATA-025 | **Custom SQL** | A widget written as one read-only SQL query for questions the model cannot answer yet. Scoped and badged until certified (S01 §3, §4.7). | Single SELECT. ≤ 10,000 rows. ≤ 20 s. `:page_filters` placeholder. | Human-written or drafted by AG-10, then Check and run (RULE-002, RULE-006, RULE-007) | Confidential (the SQL text reveals schema and logic) | The creator, with STK-001 at certification | As personal definition | Metadata DB |
| — | **Scope** | The rows a person may see, set by their role. Filters can narrow it, never widen it (S01 §3). | Structured predicates per role and entity | Group membership → role → scope policy (RULE-004, RULE-005) | Confidential | STK-006 Identity and security owner, with STK-008 | Life of policy. Changes are audited. | Entra ID (membership) plus metadata DB (policy) |
| — | **Field hiding** | Fields removed or masked for a role, for example finance fields for CIOs (S01 §6). | hide, mask | field_policies (RULE-020) | Confidential | STK-006 | Life of policy | Metadata DB |
| DATA-026 | **Widget** | One live chart, table or figure. It stores the question (measure, breakdown, filters, visual) and never the numbers. It is recomputed for each viewer (S01 §1). | visual_type: figure, ranked_bar, column, paired_bar, table, line, stacked_status_bar, donut | Ask, then refine, then pin (FEAT-018..FEAT-021) | Internal (spec). Results are Confidential and transient. | Widget owner | Life of owner content | Metadata DB (spec), Redis (transient result) |
| DATA-027 | **Report** | A page of widgets on a 12-column grid, with sections, headline figures and page filters (S01 §1). | — | Canvas or report plan | Internal | Report owner | Life of tenant | Metadata DB |
| DATA-030 | **Dashboard** | Several reports as tabs or grouped on one page, with one shared filter bar (S01 §1). | layout: tabs, grouped | Compose (FEAT-038) | Internal | Dashboard owner | Life of tenant | Metadata DB |
| DATA-032/033 | **Published version** | A dashboard version made live for chosen roles. Earlier versions are kept and restorable (S01 §3). | v1, v2, …; state: live, superseded | Publish (RULE-016, RULE-024) | Internal / Confidential (frozen specs) | Publisher (STK-001; GAP-006) | Life of tenant (audit) | Metadata DB |
| DATA-022/023 | **Starter content** | Ready-made, certified reports installed with a database. They can be copied, hidden, deleted and restored from the pack (S01 §3). | Badge "Starter" | Pack install (FEAT-010) | Internal | Pack author (Q-002), STK-001 per tenant | Life of tenant | Pack catalogue plus metadata DB |
| — | **Badge** | Visual trust marker (S01 §3). | ✓ Certified · Personal · Custom SQL · Starter · Deleted | Derived from the definitions a widget uses | Public (UI label) | Product | — | Derived |
| DATA-048 | **Data as of** | The time at which the shown figures were read from the source. For file sources it is the upload time (S01 §4.2, §4.3; RULE-018). | ISO timestamp in the user's time zone | Execution or cache timestamp | Internal | STK-005 (freshness policy) | With cache entry | Redis plus metadata index |
| DATA-040 | **Question (turn)** | A user's natural-language question and the resolved spec. Result rows are not stored. | Text up to 2,000 characters (Proposed) | User input | Confidential. It may contain personal data typed by users. | The user | 180 days | Metadata DB |
| DATA-036/037 | **Certification decision** | A Certify, Merge or Reject decision with who, when and the definition at that moment (S01 §4.8). | certify, merge, reject | Admin decision | Internal | STK-001 | Permanent | Metadata DB |
| DATA-038 | **Look-alike** | An existing certified field that would give a different answer to the same question (S01 §4.8). | Similarity 0–1 plus a difference summary | RULE-011 | Internal | STK-001 | With request | Metadata DB |
| DATA-042/043 | **Knowledge document** | An approved text used to ground answers and chart suggestions: model definitions, glossary, PMO charter, chart patterns. | review_status: pending_review, approved, rejected, retired | Upload or generation, then review (S02 §5) | Confidential | STK-008 (business documents), STK-001 (review) | Life of document | Object storage plus metadata DB (chunks, vectors) |
| DATA-044 | **Gold example** | A verified question with its expected spec and result fingerprint. Used for few-shot retrieval and accuracy evaluation. | — | Curated by an analyst or admin | Confidential | STK-008, with QA | Life of model version | Metadata DB |
| DATA-045 | **Audit event** | An append-only record of a security- or governance-relevant action. | action key, outcome: allow, deny, success, failure | System generated | Internal. Personal where the IP address is kept. | STK-006 | Open (Q-018). Proposed ≥ 1 year. | Metadata DB |
| DATA-046 | **LLM invocation** | Metadata of a model call: agent, provider, tokens, cost, scanner verdict. No prompt or answer text (S02 §1). | — | Gateway | Internal | STK-007 | 90 days | Metadata DB |
| DATA-050 | **Agent run** | One run of the agents (an Ask turn, a model assistant proposal, a pack mapping suggestion, a certification rephrase, a custom SQL draft, a report plan or a canvas edit): agents that took part, outcome, model calls, tokens, timings (ENH-024). | kind; outcome: running, ok, fallback, clarify, refused, error | System generated | Internal | STK-007 | 90 days | Metadata DB (`ops.agent_runs`) |
| DATA-051 | **Agent step** | An ordered step of an agent run: rule, retrieval, model call, check, retry, fallback, decision, query, error. A model call step keeps the text the gateway sent after masking and the raw reply, by DEC-25 the one place prompt and answer text is stored; never in logs, the ledger (DATA-046), traces or audit details. Result rows are never kept. | kind; status: ok, failed, blocked | System generated; text from the gateway | **Confidential** (masked prompts and replies may hold business words and aggregates) | STK-007 | Text: tenant setting, 14 days by default (1–90); step: 90 days | Metadata DB (`ops.agent_steps`); Admins only, every read of text audited |
| DATA-052 | **Pack document** | One knowledge file of a pack, stored with it: glossary, value synonyms, dashboard rules, chart patterns, analysis recipes, report blueprints or gold examples (ENH-025, US-230). A system pack's documents come from its folder through `tela-admin load-packs`; a tenant's from Pack Studio (US-232 onwards). Names measures, dimensions and values the pack speaks of; never source rows. | kind; SHA-256 | Pack folder (load-packs) or pack author | Internal | Pack author; STK-008 for a tenant pack | Life of the pack version (frozen once released) | Metadata DB (`model.pack_documents`) |
| DATA-053 | **Pack context document** | A Markdown, text, PDF or Word file attached to a pack with the business context behind it (ENH-025, US-233). Copied to each connection the pack is installed on as a knowledge document waiting for review. | content SHA-256 | Pack author | Internal (Confidential if the author's file is) | Pack author; the connection's Admin reviews each copy | Life of the pack version (frozen once released) | Metadata DB (`model.pack_attachments`) + object store |
| DATA-013 | **Uploaded file source** | An Excel or CSV file uploaded as a connection. It is a governed copy of customer data (RISK-006). | xlsx, xls, csv; ≤ 50 MB (Proposed) | Upload | Confidential, or as declared by the uploader | Uploader plus STK-001 | Until replaced, plus 30 days (REC-008) | Object storage |
| DATA-049 | **Export file** | A PDF (every tab, with filters and date in the footer) or an Excel workbook (one sheet per widget, plus definitions) (S01 §4.3). | pdf, xlsx | Rendered under the requester's scope | Confidential | Requester | 24 hours | Object storage |
| — | **Source credential** | A password, key, client secret or certificate for a connection, an LLM provider or a signing key. | — | Admin entry, then written straight to the secrets store | **Sensitive / Restricted** | STK-005 / STK-007 | Until rotated | Secrets store only. Never the metadata DB, logs or prompts. |

---

## PMO starter pack terms

**Reference (secondary).** These definitions are taken from the pmo_dashboard documents (S10, S13, S15). They are **Proposed for the PMO pack** and need sign-off from STK-008 (PMO business owner). Formulas and worked examples are in [business_rules.md](business_rules.md). The source columns are in [db_scheme.md §18](db_scheme.md#18-reference-source-schema-for-the-pmo-starter-pack).

| Term | Definition | Rule | Source columns (reference) | Classification of underlying values |
|---|---|---|---|---|
| Open portfolio | Projects whose current state is Project Created, Active or On Hold (DEC-24). | RULE-026 | projects.current_state | Internal |
| In delivery | Open projects with `current_state = 'Active'`. | RULE-027 | projects.current_state | Internal |
| Overdue go-live | In-delivery project whose planned deployment date is strictly before the as-of date and that has no actual deployment date. | RULE-028 | planned_deployment_date, actual_deployment_date | Internal |
| Schedule risk | Open project whose PM-reported schedule KPI is Red or Yellow. It is subjective and distinct from overdue go-live. | RULE-029 | schedule_kpi | Internal |
| RAG contradiction | Reported schedule KPI contradicts the dates: Green but ≥ 15 days late, or Red but planned ≥ 30 days away. | RULE-030 | schedule_kpi, planned_deployment_date | Internal |
| Stale project | In-delivery project not modified for more than 30 days. The threshold is provisional (S13 RULE-010). | RULE-031 | modified_on | Internal |
| Reported status | The PM's self-reported RAG (`overall_kpi`). It is never overwritten by calculated health. | — | overall_kpi | Internal |
| Calculated health | An objective 0–100 score: Schedule 35%, Cost 25%, Progress 25%, Issues 15%. It needs at least 60% evidence coverage, and severe conditions override it. | RULE-033, RULE-034, RULE-035 | dates, budget, cost, percent_complete, issue_kpi | Internal (derived from Confidential cost data) |
| Forecast cost (EAC) | `cost_total_usd`: the estimate at completion. **It is not actual spend.** The source has no spend data (S13 axiom 3). | RULE-036, RULE-037 | cost_total_usd | Confidential |
| Approved budget | `approved_budget_value_usd`. Zero or null means no budget, and such a project is never "over budget". | RULE-036 | approved_budget_value_usd | Confidential |
| Financial exposure | Total forecast minus total approved, over projects that have both a positive budget and a non-negative forecast. Excluded projects are counted and shown. | RULE-037 | as above | Confidential |
| Open demand request | Request in state New, Active, On Hold or Return to Requester. | RULE-038 | request_state | Internal |
| DOU / DARB / SOW gate | Governance milestones on requests: Document of Understanding, Design Architecture Review Board and Statement of Work. A gate is overdue when its planned date has passed and it has not been issued or approved. | RULE-039 | dou_*, darb_*, sow_* | Internal |
| CIO (scope attribute) | The CIO who owns the project or request. It is the natural row-scope attribute for CIO-role users (S11 DEMO_CIO pin replaced by policy). | RULE-004 | cio | Internal |
| Delivery manager | The assigned project manager. It is a person's name. | — | delivery_manager | **Personal** |
| Revenue estimates | Setup revenue and recurring annual baseline revenue on requests. | — | total_setup_revenue_usd, recurring_revenue_annual_baseline_usd | Confidential (candidate for field hiding) |

---

## Classification and retention

### Classification scheme (Proposed)

| Code | Level | Meaning | Handling rules |
|---|---|---|---|
| PUB | Public | UI labels, chart templates and badges. | No restriction. |
| INT | Internal | Operational metadata such as table names, statuses, specs and counts. | Authenticated users of the tenant, within their permissions. |
| CONF | Confidential | Financial values, commercial revenue, scope predicates, SQL text, question text, knowledge documents, export files, query results. | Role-scoped access. Encrypted at rest. Not sent to an LLM unless RULE-019 allows it. Field hiding applies. |
| PERS | Personal | Names, e-mail addresses, directory object ids, IP addresses, person-named dimension values (for example delivery manager). | Data minimisation. Masked by RULE-020 when sent outside the trust boundary. Subject to access and erasure requests. |
| SENS | Sensitive / Restricted | Credentials, tokens, secrets, and special-category personal data if any is ever found in sources. | Secrets store only. Never logged, never in prompts, never returned by an API. |

Field sensitivity in a model (`model_fields.sensitivity`) maps to these levels. The model assistant proposes a level using name and semantic-type heuristics (S02 §3: secret-named columns, e-mail, phone). The Admin confirms it during approval (FEAT-009).

### Retention table (Proposed — pending Q-018, GAP-014)

| Data | Store | Classification | Retention | Deletion method |
|---|---|---|---|---|
| Source rows | Connected source | per field | Not retained by Tela | — |
| Query results (cache) | Redis | CONF | TTL, default 900 s, per-connection configurable (ADR-010) | Expiry and invalidation on model or permission change |
| Column samples for labelling | Memory only | CONF | Not stored (S01 §4.1) | Discarded after the proposal |
| User profile and identities | Metadata DB | PERS | Life of account. Anonymised 30 days after deprovisioning. | Anonymise job |
| Questions and turns | Metadata DB | CONF / PERS | 180 days | Partition drop or purge job |
| Turn events, query executions, LLM invocations | Metadata DB | INT | 90 days | Partition drop |
| Audit events | Metadata DB | INT / PERS | ≥ 1 year (Q-018); IP pseudonymised after 90 days | Partition archive, then drop |
| Content definitions and versions | Metadata DB | INT / CONF | Life of tenant. Soft-deleted items purged after 90 days. | Purge job |
| Certification decisions | Metadata DB | INT | Life of tenant | Never during the tenant's life |
| Uploaded Excel/CSV | Object storage | CONF | Until replaced, plus 30 days, or admin-configured | Lifecycle rule plus DB marker |
| Exports | Object storage | CONF | 24 hours | Lifecycle rule |
| Knowledge documents and chunks | Object storage plus metadata DB | CONF | Life of document. Retired documents purged after 30 days. | Cascade delete |
| Credentials | Secrets store | SENS | Until rotated. Old versions per the store's policy. | Secret version destroy |
| Keycloak sessions | Keycloak DB | PERS | Session lifetime (Proposed 8 h idle, 24 h max) | Keycloak |
| Backups | Backup store | Inherits | Proposed 30 days (GAP-014) | Rotation |

---

## Source of truth map

| Information | Source of truth | Copies or derivatives held by Tela | Sync direction and timing | Conflict rule |
|---|---|---|---|---|
| Business records (projects, requests, transactions, …) | Connected source (PostgreSQL, MySQL, SQL Server, MongoDB, Dataverse) | None persisted. A transient Redis result cache only. | Read on demand. The cache follows the freshness policy (Q-001). | The source always wins. The cache is never authoritative. |
| Excel/CSV data | The uploaded file in object storage (it becomes the source) | DuckDB in-memory view per query | A new upload replaces the file. As-of = upload time. | The latest upload wins. The previous file is kept until retention ends. |
| Source schema (tables, columns, types) | The connected source catalogue | connection_objects (names and types only) | Introspection on connect, on demand, and when a schema checksum mismatch is detected (S02 §2) | The source wins. A mismatch blocks execution until re-introspection and model review (RULE-015). |
| Meaning of data (labels, measures, dimensions, links) | Tela metadata DB (approved model version) | Knowledge chunks (embedded copies of definitions) | Re-index on approval (outbox event) | The metadata DB wins. The RAG index is rebuilt from it. |
| Who a user is | Microsoft Entra ID (via Keycloak) | users, external_identities | At sign-in, and through SCIM provisioning if enabled (FEAT-004) | Entra wins for identity attributes. Tela never edits them. |
| Group membership | Entra ID | idp_group_members | Token claims at sign-in, plus SCIM or Graph for overage (RISK-007) | Entra wins. A stale membership expires at the next sign-in or sync. |
| Role and scope mapping | Tela metadata DB (group_role_mappings, scope_policies) | effective_roles_cache | Recomputed on group change | The metadata DB wins (C4: configured once by Admin; Q-013). |
| Credentials | Secrets store | None (a `secret_ref` path only) | Admin writes go straight to the store | The store is authoritative. |
| Sessions and tokens | Keycloak | None. The API validates tokens. | — | Keycloak wins. |
| Published content | Metadata DB (dashboard_versions) | Rendered exports in object storage (24 h) | — | The live version wins. Exports are point-in-time. |
| LLM and agent behaviour logs | Metadata DB (llm_invocations, metadata only) | OTel traces | — | — |
| PMO KPI definitions | STK-008 business sign-off, encoded as PMO pack measures | Pack manifest, then installed measures | Pack updates change starter content only, never user copies (S01 §4.10) | Business sign-off wins. Pack code must match the signed definition. |

---

## Data handling rules for LLM and RAG

These rules are a summary. The authoritative statements are RULE-019, RULE-020, RULE-021 and RULE-022 in [business_rules.md](business_rules.md).

- **Default egress.** What is sent to an LLM by default: schema metadata, approved definitions, glossary text, gold examples, the user's question and the conversation's specs. Row values are not sent by default (S02 §2; S26 REQ-007).
- **Optional aggregates.** Aggregated widget results may be sent for "Ask about a widget" and narratives. This is allowed only when the tenant policy permits it (Q-009), the values are CONF or below, PERS dimension labels are masked, and the row count is ≤ 50 (Proposed). The narrative must quote only computed numbers (RULE-022).
- **Never sent.** SENS data, credentials and raw drill-down records.
- **RAG indexing.** Only approved knowledge documents are indexed and retrievable (S02 §5). Chunks are scanned for credentials and personal data before indexing. Retrieved text is treated as untrusted data, never as instructions (RISK-014, REC-014).

---

## Open questions

| ID | Question | Why it matters here |
|---|---|---|
| Q-001 | Refresh model and cadence per connection | The meaning of "Data as of" and cache retention |
| Q-009 | LLM data handling and approved providers | Whether the aggregate exception is allowed at all |
| Q-011 | Confirm Excel/CSV in Phase 1 | Whether uploaded files become a data class held by Tela |
| Q-013 | Where group mappings are configured | The source of truth for role and scope mapping |
| Q-016 | Embedding model hosting | Whether knowledge text leaves the trust boundary for embedding |
| Q-018 | Audit retention period | Retention table |

---

## Change log

| Date | Version | Sources added | Summary |
|---|---|---|---|
| 2026-10-21 | 0.1 | S01–S31 | Initial business terms, PMO pack terms, classification scheme, retention table, source-of-truth map |
