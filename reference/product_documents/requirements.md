# PMO Analytics Platform — Requirements and Acceptance Catalog

| Field | Value |
|---|---|
| Project | PMO Analytics & Conversational Dashboard Platform |
| Document status | Draft for review |
| Version | 0.1 |
| Last updated | 2026-10-19 |
| Evidence level | L1–L2; requirements derived from meeting scope and journey prototype |
| Related documents | [Project summary](project_summary.md), [Architecture](architecture.md), [Options and plan](options_and_plan.md), [Risk register](risk_register.md) |

Priorities are provisional: **Must** means necessary for a safe, useful read-only product; **Should** means important but can follow after core proof; **Could** means optional; **Later** means explicitly outside initial read-only acceptance unless separately approved.

## Functional requirements

| ID | Requirement | Priority | Source/status | Acceptance evidence |
|---|---|---|---|---|
| REQ-001 | Authenticate users using the client's approved Microsoft SSO configuration | Must | Explicit in meeting notes; details missing | Authenticated/unauthenticated integration tests |
| REQ-002 | Resolve application role and data scope from trusted identity/policy sources | Must | Explicit priority; mapping missing | Role matrix and negative access tests |
| REQ-003 | Configure a read-only connection to the approved client data platform | Must | Explicit; connector/source uncertain | Connection test and least-privilege review |
| REQ-004 | Expose only approved tables/views/columns to query generation | Must | Inferred security control | Attempted access to unapproved objects is denied |
| REQ-005 | Show default/published dashboard content appropriate to the user | Must | Explicit; exact dashboard list missing | Role-specific home acceptance tests |
| REQ-006 | Support dashboard filters and show relevant definitions | Must | Explicit in journey prototype | Filter and KPI-definition tests |
| REQ-007 | Allow natural-language questions over the approved semantic model | Must | Explicit | Ground-truth question suite |
| REQ-008 | Ask clarification when ambiguity materially changes the result | Must | Explicit | Ambiguous question tests |
| REQ-009 | Validate query proposals before execution | Must | Explicit pipeline concept | SQL parser/policy negative tests |
| REQ-010 | Execute read-only queries with time, row and resource limits | Must | Inferred safety requirement | Timeout/row-cap/abuse tests |
| REQ-011 | Return suitable chart, table or card based on result and user intent | Must | Explicit | Visual-result acceptance set |
| REQ-012 | Explain applied filters, period and measure definitions | Should | Inferred trust requirement | Result explanation review |
| REQ-013 | Pin a conversational result as a reusable widget | Must | Explicit J4 | Widget persists and can be reopened |
| REQ-014 | Create a report from a natural-language request after showing a plan | Must | Explicit J5 | Plan preview and apply/cancel tests |
| REQ-015 | Edit a report by chat and by direct manipulation | Should | Explicit J6 | Layout/field/filter edit tests |
| REQ-016 | Support grouped analytics, joins and approved measures | Must for agreed use cases | Explicit; exact joins missing | Reconciled result tests |
| REQ-017 | Permit custom SQL only under explicit policy and authorization | Should / conditional | J7 shows custom SQL; permissions unclear | Denial tests and review workflow |
| REQ-018 | Save drafts and allow creators to request certification | Must | Explicit J8/J9 | Draft and request workflow tests |
| REQ-019 | Certify or reject with a recorded decision and note | Must | Explicit prototype | Audit and state-transition tests |
| REQ-020 | Publish approved versions to selected roles/audiences | Must | Explicit | Viewer access and version tests |
| REQ-021 | Preview content as a target role | Should | Explicit prototype | Preview matches that role's permissions |
| REQ-022 | Manage starter content: show, hide, delete and restore | Should | Explicit J10 | Permission/state tests |
| REQ-023 | Add another database/source under admin control | Should | Explicit J11; connector details missing | Second-source isolation test |
| REQ-024 | Export reports to PDF and/or Excel subject to policy | Must if confirmed in MVP | Meeting notes and prototype labels | Export authorization and rendering tests |
| REQ-025 | Record audit events for connection changes, query policy decisions, certification, publishing and exports | Must | Inferred security/governance need | Audit completeness review |
| REQ-026 | Support future project-request creation via typed commands and approval | Later | Meeting notes | Separate write-back approval gate |
| REQ-027 | Support future updates to project manager, timeline, budget and risk via typed commands | Later | Meeting notes | Separate command/authorization tests |
| REQ-028 | Support delete operations only after explicit business, security and recovery approval | Later / conditional | Meeting notes mention delete; policy unspecified | Signed delete policy and recovery test |

## Non-functional requirements

| ID | Requirement | Priority | Verification |
|---|---|---|---|
| NFR-001 | Data access must be enforced server-side and/or at the source, not solely in UI or prompts | Must | Adversarial row/column-scope tests |
| NFR-002 | Use least-privilege read-only credentials for analytics | Must | Database grants review |
| NFR-003 | Keep secrets out of browser code, prompts and logs | Must | Static/config/log review |
| NFR-004 | Confirm LLM provider, retention, training, residency and data-handling policy before client data is sent | Must | Written policy approval |
| NFR-005 | Use synthetic data in sandbox unless real data is specifically approved | Must | Environment/data review |
| NFR-006 | Provide traceable query/result metadata suitable for debugging and audit | Must | Correlation-ID test |
| NFR-007 | Protect source platform from expensive or unbounded generated queries | Must | Load/abuse test |
| NFR-008 | Dashboard measures must be consistent and governed across reports | Must | KPI reconciliation |
| NFR-009 | Published artifacts must be versioned and auditable | Must | Version lifecycle tests |
| NFR-010 | UI should support keyboard navigation, visible focus and accessible chart/table alternatives | Should | Accessibility review |
| NFR-011 | Define response-time, availability and concurrency targets before performance sign-off | Must | Approved NFR baseline and performance test |
| NFR-012 | Define backup, restore, rollback and incident ownership for production | Must | Runbook and restore exercise |
| NFR-013 | Separate dev, sandbox/UAT and production identities/secrets/configuration | Must | Environment review |
| NFR-014 | Store only the minimum prompt/result data needed for operation and audit | Must | Data-retention and logging review |

## Journey traceability

| Journey | Related requirements |
|---|---|
| J1 Connect a database | REQ-003, REQ-004, REQ-023, NFR-002, NFR-005 |
| J2 First open / Home | REQ-005, REQ-020 |
| J3 Read a dashboard | REQ-005, REQ-006, NFR-001, NFR-008 |
| J4 Ask, refine and pin | REQ-007–REQ-013, NFR-006, NFR-007 |
| J5 Build a report from one request | REQ-014, REQ-018 |
| J6 Edit the report canvas | REQ-015, REQ-018 |
| J7 Grouping, joins, custom SQL | REQ-016, REQ-017, NFR-007, NFR-008 |
| J8 Certify | REQ-018, REQ-019, REQ-025 |
| J9 Compose and publish | REQ-020, REQ-021, NFR-009 |
| J10 Manage starter content | REQ-022 |
| J11 Bring another database | REQ-023, NFR-002 |

## Ambiguities to resolve

- The source platform is variously described as TPG, possibly Clarity, or an API. Confirm authoritative source(s) and interface.
- The count of default dashboards (~30) and chart types (~100) is approximate and needs a prioritized catalog.
- The HTML prototype's illustrative data and controls do not prove implementation.
- “Admin”, “creator”, “governor/certifier”, “publisher” and “viewer” boundaries need a permission matrix.
- “Data-level access” must be specified in terms of row/column/object policy and trusted source attributes.
- PDF export, Excel export, sharing and publishing may have different data-loss-prevention rules.
- Custom SQL needs an explicit user eligibility and review policy.
- Write-back scope and release approval are not fixed.
- Accuracy, latency, freshness and availability thresholds are not defined.
