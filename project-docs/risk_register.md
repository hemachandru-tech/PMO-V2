# Risk and Gap Register

| Field | Value |
|---|---|
| Project | Tela — Conversational PMO Analytics & Dashboard Platform |
| Document status | Draft |
| Version | 0.1 |
| Last updated | 2026-10-21 |
| Evidence level | L1 – Business only (product); reference designs are secondary evidence (S10–S31); no source code, DDL, API spec or deployment files supplied |
| Sources analyzed | 31 files — see [project_summary.md](project_summary.md#source-inventory-and-coverage) |
| Related documents | [Project summary](project_summary.md) · [Architecture](architecture.md) · [Function document](function_document.md) · [Technical document](technical_document.md) · [DB scheme](db_scheme.md) · [Requirements](requirements.md) · [Business rules](business_rules.md) · [API catalog](api_catalog.md) · [Data dictionary](data_dictionary.md) · [Diagrams](diagrams/README.md) |

---

## Table of contents

- [How to read this register](#how-to-read-this-register)
- [Summary heat map](#summary-heat-map)
- [Risk register](#risk-register)
- [Gap register](#gap-register)
- [Recommendations](#recommendations)
- [Status tracking](#status-tracking)
- [Lessons carried over from reference systems](#lessons-carried-over-from-reference-systems)
- [Change log](#change-log)

---

## How to read this register

This register owns the full detail for every `RISK`, `GAP` and `REC` identifier. Other documents summarise and link here.

**Evidence level matters.** The product has not been built. At L1, nothing in this register is a *confirmed implementation defect*. Items are classified as follows:

| Status | Meaning in this register |
|---|---|
| **Potential risk** | A condition that could cause harm if the design or build does not address it. |
| **Documentation gap** | Information is absent from or contradictory across the supplied sources. Implementation status is unknown because nothing is implemented yet. |
| **Unverified concern** | A concern reported by a reference document about a *different* system (S10–S31). It is a lesson, not our defect. |
| **Confirmed gap** | Used only for contradictions that are demonstrably present in the supplied documents (for example scope.md vs. stakeholder instruction). |

**Scales.** Likelihood: Low / Medium / High / Unknown. Impact: Low / Medium / High / Critical. Priority: P0 = resolve before real client data is connected; P1 = resolve before UAT / release readiness; P2 = resolve during design or before the related optional capability is enabled; P3 = backlog.

**Source codes** (S01–S31) are defined in [project_summary.md](project_summary.md#source-inventory-and-coverage).

---

## Summary heat map

### Counts by category and impact

| Category | Total | Critical | High | Medium | Low |
|---|---:|---:|---:|---:|---:|
| Security and privacy | 9 | 5 | 4 | 0 | 0 |
| Data and reporting | 5 | 0 | 4 | 1 | 0 |
| Technical / integration | 6 | 0 | 5 | 1 | 0 |
| Operational | 4 | 0 | 3 | 1 | 0 |
| Product and commercial | 5 | 0 | 1 | 4 | 0 |
| Business / governance | 5 | 0 | 3 | 2 | 0 |
| Evidence | 3 | 0 | 3 | 0 | 0 |
| **Total (22 RISK + 15 GAP)** | **37** | **5** | **23** | **9** | **0** |

Category assignment: Security and privacy = RISK-001, 003, 009, 010, 014, 015, 016, 021, GAP-013. Data and reporting = RISK-002, 011, 012, GAP-001, GAP-015. Technical / integration = RISK-004, 005, 006, 007, 020, GAP-012. Operational = RISK-008, 019, GAP-007, GAP-014. Product and commercial = RISK-013, 017, 022, GAP-004, GAP-005. Business / governance = GAP-002, 003, 006, 010, GAP-011. Evidence = RISK-018, GAP-008, GAP-009.

### Likelihood × impact grid (risks only)

| Likelihood \ Impact | Medium | High | Critical |
|---|---|---|---|
| **High** | RISK-017 | RISK-002, RISK-004, RISK-008, RISK-012, RISK-013, RISK-019 | RISK-001 |
| **Medium** | RISK-020, RISK-022 | RISK-005, RISK-006, RISK-007, RISK-011, RISK-014, RISK-015, RISK-016, RISK-018 | RISK-003, RISK-010, RISK-021 |
| **Low** | — | — | RISK-009 |

---

## Risk register

### Overview

| ID | Title | Category | Status | Likelihood | Impact | Priority | REC | Owner |
|---|---|---|---|---|---|---|---|---|
| RISK-001 | Scope leakage across surfaces | Security | Potential risk | High | Critical | P0 | REC-004 | Security architect + backend lead |
| RISK-002 | Confidently wrong answers | Data | Potential risk | High | High | P0 | REC-003, REC-010 | AI/NLP lead |
| RISK-003 | Unapproved LLM data handling | Security / privacy | Potential risk | Medium | Critical | P0 | REC-002 | STK-009 model-policy approver |
| RISK-004 | Dataverse endpoint constraints | Integration | Potential risk | High | High | P1 | REC-006 | Integration engineer |
| RISK-005 | MongoDB semantic mismatch | Integration | Potential risk | Medium | High | P1 | REC-007 | Integration engineer |
| RISK-006 | Excel uploads copy and expose data | Integration / privacy | Potential risk | Medium | High | P1 | REC-008 | Product owner + security |
| RISK-007 | Entra group mapping errors | Integration / security | Potential risk | Medium | High | P0 | REC-009 | STK-006 identity owner |
| RISK-008 | Source system load | Operational | Potential risk | High | High | P1 | REC-005, REC-001 | STK-005 data source owner |
| RISK-009 | Credential compromise | Security | Potential risk | Low | Critical | P0 | REC-005 | Platform operator |
| RISK-010 | Custom SQL validation bypass | Security | Potential risk | Medium | Critical | P0 | REC-003 | Backend lead |
| RISK-011 | Look-alike detection misses | Data | Potential risk | Medium | High | P1 | REC-010 | Product owner + AI lead |
| RISK-012 | Model version changes break widgets | Data | Potential risk | High | High | P1 | REC-011 | Backend lead |
| RISK-013 | Misleading visuals | Product | Potential risk | High | High | P1 | REC-013 | UX lead |
| RISK-014 | Prompt injection through metadata and documents | Security | Potential risk | Medium | High | P0 | REC-014 | Security architect |
| RISK-015 | Stale or cross-scope cache | Security / data | Potential risk | Medium | High | P0 | REC-004, REC-001 | Backend lead |
| RISK-016 | Export exfiltration | Security | Potential risk | Medium | High | P1 | REC-015 | Security architect |
| RISK-017 | Scope creep | Product | Potential risk | High | Medium | P1 | REC-013 | Product owner |
| RISK-018 | Reference code reuse rights unknown | Evidence / commercial | Documentation gap | Medium | High | P1 | REC-016 | Sponsor |
| RISK-019 | Operational resilience gaps | Operational | Unverified concern (lesson) | High | High | P1 | REC-012 | Platform operator |
| RISK-020 | RAG retrieval quality drift | Technical | Potential risk | Medium | Medium | P2 | REC-010, REC-014 | AI/NLP lead |
| RISK-021 | Tenant isolation in SaaS | Security | Potential risk | Medium | Critical | P0 (if SaaS) | REC-020 | Security architect |
| RISK-022 | Accessibility not specified | Product | Documentation gap | Medium | Medium | P2 | REC-017 | UX lead |

### RISK-001 Scope leakage across surfaces

- **Category:** Security and privacy.
- **Description:** Row scope and field hiding must hold on every surface that touches source data: dashboards, chat answers, refinements, drill-downs, exports, custom SQL, filter-option lists, caches and LLM context. Each extra code path is a chance to forget the scope. The Tela reference reports exactly this kind of defect: a widget refresh endpoint that did not resolve the viewer's scope (S30 RISK-004).
- **Evidence:** S01 §6 "enforced in every query: dashboards, chat answers, drill-downs, exports and custom SQL"; S09 RISK-001; S30 RISK-004 (lesson); S16 RISK-001 (PMO reference has no per-user auth at all).
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / Critical / P0.
- **Consequence:** A user sees rows or fields outside their role (for example finance fields shown to a CIO, or another region's projects). Breach of client data policy; loss of trust in the product.
- **Recommendation:** [REC-004](#rec-004-enforce-scope-in-one-shared-query-path-for-all-surfaces); supporting [REC-009](#rec-009-define-role-catalogue-and-group-mappings).
- **Owner:** Security architect with backend lead.
- **Verification:** Scope-isolation test matrix (two personas × every surface × adversarial prompts) passes with zero leaks; see technical tests in [technical_document.md](technical_document.md). Code review confirms a single scoped execution function is the only path to the connector runner.

### RISK-002 Confidently wrong answers

- **Category:** Data and reporting.
- **Description:** A query can run cleanly yet answer the wrong question: invented status values ("Success" instead of "SETTLED"), dropped entity filters on follow-ups, creation timestamp used instead of business event date, fan-out double counting on joins. Reference evidence shows all four (S30 GAP-001..003, S27 RULE-007..009).
- **Evidence:** S30 GAP-001, GAP-002, GAP-003; S27 RULE-007, RULE-008, RULE-015; S08 REQ-007/008; S05 "PMO talk-to-data is not considered reliable".
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / High / P0.
- **Consequence:** Executives act on wrong numbers; the "one certified definition" promise (BUS-002) fails.
- **Recommendation:** [REC-003](#rec-003-build-spec-first-query-engine-with-ast-validator) (typed spec bound to approved measures and grounded values), [REC-010](#rec-010-create-ground-truth-evaluation-suite-before-uat).
- **Owner:** AI/NLP lead.
- **Verification:** Ground-truth suite pass rate meets the threshold agreed under Q-017; regression tests for enum grounding, multi-turn filter persistence, date-field selection and join fan-out.

### RISK-003 Unapproved LLM data handling

- **Category:** Security and privacy.
- **Description:** Schema names, column samples, user questions and (for "ask about a widget") aggregated results could be sent to an external model provider before the client has approved the provider, region, retention and training terms.
- **Evidence:** S01 §8 Q9; S05/S06 "confirm with Vijay"; S09 RISK-005; S02 model-gateway safety gate.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / Critical / P0.
- **Consequence:** Contractual or regulatory breach; programme halt.
- **Recommendation:** [REC-002](#rec-002-confirm-llm-policy-in-writing-before-real-data).
- **Owner:** STK-009 model-policy approver with security.
- **Verification:** Signed policy on file; LLM gateway configuration allow-lists only approved providers; egress tests show no raw rows leave the platform.

### RISK-004 Dataverse endpoint constraints

- **Category:** Technical / integration.
- **Description:** Dataverse can be read through the TDS (SQL) endpoint or the Web API (OData / FetchXML). Based on general knowledge (not verified in supplied sources), the TDS endpoint must be enabled per environment, supports only a subset of T-SQL, is read-only, and has query time and result-size limits; the Web API has service-protection throttling. Authentication needs an Entra app registration and a Dataverse application user. These limits may block some measures or the 20-second budget.
- **Evidence:** Stakeholder instruction 2026-10-21 (Dataverse required); S15 Dynamics CRM field mapping (`tpgmea_` logical names) — Inferred that the PMO source lives in Dataverse. No Dataverse documentation supplied.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / High / P1.
- **Consequence:** The PMO pilot on live TPG data cannot run, or runs slowly; measures need rewriting.
- **Recommendation:** [REC-006](#rec-006-spike-dataverse-tds-and-web-api).
- **Owner:** Integration engineer.
- **Verification:** Spike report covering enablement, auth, supported SQL for every PMO pack measure, timings on realistic volumes, and throttling behaviour.

### RISK-005 MongoDB semantic mismatch

- **Category:** Technical / integration.
- **Description:** MongoDB is schemaless and has no SQL. Joins need `$lookup`, nested arrays change grain, fields may vary across documents. Scope injection must prepend a `$match` stage that cannot be bypassed, and the "single SELECT" rule has no direct equivalent.
- **Evidence:** Stakeholder instruction 2026-10-21; S01 §1 ("the engine writes the SQL") assumes SQL sources.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P1.
- **Consequence:** Wrong counts on nested data; custom SQL feature unavailable for Mongo connections; scope bypass if pipelines are not guarded.
- **Recommendation:** [REC-007](#rec-007-spike-mongodb-spec-compilation).
- **Owner:** Integration engineer.
- **Verification:** Compiled pipelines pass a stage allow-list (no `$out`, `$merge`, `$function`, `$where`); scope `$match` is always first; grain tests on nested arrays.

### RISK-006 Excel uploads copy and expose data

- **Category:** Technical / integration and privacy.
- **Description:** Unlike database connections, an uploaded Excel or CSV file *is* a copy of data held by the platform. It may contain personal data, hidden sheets or formulas, and has no row-level owner.
- **Evidence:** S01 §5 lists file uploads as out of scope; stakeholder instruction requires Excel (conflict C1, see GAP-011); S01 §6 "no copies of source tables".
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P1.
- **Consequence:** Data retained longer than allowed; sensitive columns visible to creators; contradiction with the "never copies" promise.
- **Recommendation:** [REC-008](#rec-008-treat-excel-as-governed-upload-with-retention).
- **Owner:** Product owner with security.
- **Verification:** Upload flow shows retention and classification; files encrypted at rest in object storage; deletion test removes file and derived metadata.

### RISK-007 Entra group mapping errors

- **Category:** Technical / integration and security.
- **Description:** Role and scope come from Microsoft group membership (S01 §2). Errors include: similarly named groups mapped to privileged roles, token group overage when a user is in many groups, nested groups, stale membership after a user moves teams.
- **Evidence:** S01 §2, §8 Q3; S03 §5, §9 ("external group membership must not automatically grant administrator privileges"); S09 RISK-012.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P0.
- **Consequence:** Over-privileged or locked-out users.
- **Recommendation:** [REC-009](#rec-009-define-role-catalogue-and-group-mappings).
- **Owner:** STK-006 identity and security owner.
- **Verification:** Mapping table reviewed and signed; tests for overage, nested groups, deprovisioning and privileged-role approval.

### RISK-008 Source system load

- **Category:** Operational.
- **Description:** Every widget is recomputed per viewer (S01 §1). A published dashboard opened by many people fires many queries at the client's production database.
- **Evidence:** S01 §1, §8 Q1 ("data as of 06:00" suggests a cache); S06 §12; S09 RISK-014; S11 §15.2 (unindexed queries).
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / High / P1.
- **Consequence:** Slow dashboards; source DBA revokes access.
- **Recommendation:** [REC-001](#rec-001-decide-data-freshness-model-per-connection), [REC-005](#rec-005-provision-dedicated-read-only-source-identities).
- **Owner:** STK-005 data source owner with platform operator.
- **Verification:** Load test against a representative replica; per-connection concurrency limit enforced; cache hit rate measured.

### RISK-009 Credential compromise

- **Category:** Security.
- **Description:** Source credentials, LLM API keys and Keycloak secrets could leak through config files, logs, prompts or the browser. The Tela reference stores DB passwords in plaintext JSON (S30 RISK-003); TelaKnowledge had a placeholder all-zero signing key (S31 §6.1).
- **Evidence:** S06 §9; S30 RISK-003 (lesson); S31 §6.1 (lesson); S03 §11.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Low / Critical / P0.
- **Consequence:** Attacker reads client databases directly.
- **Recommendation:** [REC-005](#rec-005-provision-dedicated-read-only-source-identities) (least privilege) plus secrets in OpenBao/Vault (COMP-013).
- **Owner:** Platform operator.
- **Verification:** Secret-scanning in CI; log redaction tests; DB stores only `secret_ref`; startup refuses default or placeholder keys.

### RISK-010 Custom SQL validation bypass

- **Category:** Security.
- **Description:** Custom SQL is the one place a person writes free SQL. Parser gaps per dialect (SQL Server, Dataverse T-SQL subset), comments, CTE shadowing of governed tables, functions with side effects or timing attacks could bypass the single read-only `SELECT` rule or scope injection.
- **Evidence:** S01 §4.7; S13 RULE-017; S27 RULE-001, RULE-004; S02 §2.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / Critical / P0.
- **Consequence:** Data modification attempts (stopped only by DB read-only role) or out-of-scope reads.
- **Recommendation:** [REC-003](#rec-003-build-spec-first-query-engine-with-ast-validator), [REC-005](#rec-005-provision-dedicated-read-only-source-identities).
- **Owner:** Backend lead.
- **Verification:** Validator fuzzing and adversarial corpus per dialect; DB-level read-only proven by attempting writes with the runtime identity.

### RISK-011 Look-alike detection misses

- **Category:** Data and reporting.
- **Description:** Two fields that answer the same question differently (for example invoices including vs excluding cancelled) may both get certified if detection relies on names only.
- **Evidence:** S01 §4.8 look-alike and semantic checks; S09 RISK-006.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P1.
- **Consequence:** Inconsistent KPIs undermine BUS-002.
- **Recommendation:** [REC-010](#rec-010-create-ground-truth-evaluation-suite-before-uat) (look-alike test pairs); detection on base table, aggregation, filters and results, not only names (see business_rules.md RULE-011).
- **Owner:** Product owner with AI lead.
- **Verification:** Seeded look-alike pairs are flagged at ≥ agreed recall.

### RISK-012 Model version changes break widgets

- **Category:** Data and reporting.
- **Description:** When an admin approves model v2 (renamed field, changed measure rule), existing widgets and published dashboards may silently change meaning or fail.
- **Evidence:** S01 §8 Q7.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / High / P1.
- **Consequence:** Published numbers change without review; broken dashboards.
- **Recommendation:** [REC-011](#rec-011-version-pin-widgets-to-model-versions).
- **Owner:** Backend lead.
- **Verification:** Impact analysis lists affected widgets before approval; published versions keep running on their pinned model version.

### RISK-013 Misleading visuals

- **Category:** Product.
- **Description:** Auto-selected or requested charts can mislead (donut for a trend, truncated axes, mixed currencies on one axis).
- **Evidence:** S01 §4.4, §4.6, §6 "Chart integrity"; S27 RULE-006.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / High / P1.
- **Consequence:** Wrong decisions; credibility loss.
- **Recommendation:** [REC-013](#rec-013-prioritise-chart-catalogue).
- **Owner:** UX lead.
- **Verification:** Suitability decision table tests (RULE-008) for every visual × data shape.

### RISK-014 Prompt injection through metadata and documents

- **Category:** Security.
- **Description:** Column descriptions, sample values, uploaded knowledge documents and even cell text in Excel can contain instructions aimed at the model ("ignore previous instructions"). RAG retrieval feeds this text into prompts.
- **Evidence:** S02 §1 (blocked phrases), §5 (knowledge review lifecycle: pending_review by default); S31 §4.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P0.
- **Consequence:** Model produces specs outside intent; with typed specs and code validation the blast radius is limited to wrong (not unsafe) answers.
- **Recommendation:** [REC-014](#rec-014-defend-rag-against-prompt-injection).
- **Owner:** Security architect.
- **Verification:** Injection corpus tests; review gate prevents unapproved documents from being retrieved.

### RISK-015 Stale or cross-scope cache

- **Category:** Security / data.
- **Description:** A result cache that is not keyed by scope can serve one role's numbers to another; a long TTL shows stale figures with the wrong "as of".
- **Evidence:** S06 §12 ("include scope in cache keys"); S03 §18.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P0.
- **Consequence:** Data leak or stale decisions.
- **Recommendation:** [REC-004](#rec-004-enforce-scope-in-one-shared-query-path-for-all-surfaces), [REC-001](#rec-001-decide-data-freshness-model-per-connection).
- **Owner:** Backend lead.
- **Verification:** Cache key includes tenant, connection, model version, compiled query hash and scope hash; test two scopes never share an entry.

### RISK-016 Export exfiltration

- **Category:** Security.
- **Description:** PDF and Excel exports (S01 §4.3) move scoped data outside the platform with no further control.
- **Evidence:** S01 §4.3, §8 Q8; S08 REQ-024; S09 GAP-003.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / High / P1.
- **Consequence:** Bulk data leaves the organisation.
- **Recommendation:** [REC-015](#rec-015-define-export-policy).
- **Owner:** Security architect.
- **Verification:** Export re-checks permission at render time; audit event per export; per-role export permission; row caps.

### RISK-017 Scope creep

- **Category:** Product.
- **Description:** Meeting notes mention about 30 predefined dashboards and about 100 chart types (S05); stakeholder adds more connectors. Unbounded expectations threaten Phase 1.
- **Evidence:** S05, S09 RISK-010; S01 §4.11 lists a small chart set.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** High / Medium / P1.
- **Consequence:** Delays; shallow quality across too many features.
- **Recommendation:** [REC-013](#rec-013-prioritise-chart-catalogue); connector priority order from Q-012.
- **Owner:** Product owner.
- **Verification:** Signed Phase 1 catalogue of charts, connectors and starter reports.

### RISK-018 Reference code reuse rights unknown

- **Category:** Evidence / commercial.
- **Description:** The PMO dashboard, Tela NL2SQL and TelaKnowledge references describe code we may want to reuse, but ownership, licence and access are not established.
- **Evidence:** S05 Q-002; S09 RISK-004; S07 Option B.
- **Status:** Documentation gap.
- **Likelihood / Impact / Priority:** Medium / High / P1.
- **Consequence:** Rework, or legal exposure if code is reused without rights.
- **Recommendation:** [REC-016](#rec-016-confirm-reuse-rights-for-reference-code).
- **Owner:** Sponsor.
- **Verification:** Written confirmation of rights per repository.

### RISK-019 Operational resilience gaps

- **Category:** Operational.
- **Description:** TelaKnowledge scored 35/100 on operational resilience: missing health checks and restart policies, fatal errors exiting with code 0, undrained outboxes, `sslmode=disable`, no CI (S31 §5–§6). The same failure modes are easy to repeat when building many Docker services.
- **Evidence:** S31 §5, §6 (lesson from reference); S16 GAP-003.
- **Status:** Unverified concern (lesson from reference).
- **Likelihood / Impact / Priority:** High / High / P1.
- **Consequence:** Silent outages, data loss in queues, insecure internal traffic.
- **Recommendation:** [REC-012](#rec-012-ship-health-checks-restart-policies-and-ci-from-day-one).
- **Owner:** Platform operator.
- **Verification:** Every container has a health check, restart policy and non-zero exit on fatal error; outbox drain monitored; TLS on internal DB connections; CI gate.

### RISK-020 RAG retrieval quality drift

- **Category:** Technical.
- **Description:** Retrieval of definitions, gold examples and chart patterns may degrade as the knowledge index grows or the embedding model changes.
- **Evidence:** S31 §3.1 (pgvector HNSW, 1024-dim); S02 citation coverage ≥0.70.
- **Status:** Potential risk.
- **Likelihood / Impact / Priority:** Medium / Medium / P2.
- **Consequence:** Planner picks wrong measures; lower answer accuracy.
- **Recommendation:** [REC-010](#rec-010-create-ground-truth-evaluation-suite-before-uat), [REC-014](#rec-014-defend-rag-against-prompt-injection).
- **Owner:** AI/NLP lead.
- **Verification:** Retrieval recall@k tracked in the evaluation suite; re-index on embedding model change with versioned embeddings.

### RISK-021 Tenant isolation in SaaS

- **Category:** Security.
- **Description:** If the product is offered as shared SaaS (S03 §4), one tenant's metadata, model or cached results could reach another tenant.
- **Evidence:** S03 §4 tenant isolation rules; S29 DATA-001 `tenant_id`.
- **Status:** Potential risk (only if SaaS is chosen, Q-014).
- **Likelihood / Impact / Priority:** Medium / Critical / P0 if SaaS.
- **Consequence:** Cross-customer data breach.
- **Recommendation:** [REC-020](#rec-020-tenant-isolation-tests).
- **Owner:** Security architect.
- **Verification:** Tenant isolation test suite; PostgreSQL RLS enabled on tenant-scoped tables as defence in depth.

### RISK-022 Accessibility not specified

- **Category:** Product.
- **Description:** No accessibility requirement exists in the Phase 1 scope; charts and canvas drag-and-drop are hard for keyboard and screen-reader users.
- **Evidence:** S01 (absent); S08 NFR-010.
- **Status:** Documentation gap.
- **Likelihood / Impact / Priority:** Medium / Medium / P2.
- **Consequence:** Exclusion of users; procurement failure in regulated clients.
- **Recommendation:** [REC-017](#rec-017-accessibility-baseline-wcag-22-aa).
- **Owner:** UX lead.
- **Verification:** Accessibility audit against WCAG 2.2 AA on key journeys.

---

## Gap register

### Overview

| ID | Title | Category | Status | Likelihood | Impact | Priority | Linked Q | REC | Owner |
|---|---|---|---|---|---|---|---|---|---|
| GAP-001 | Data freshness model undefined | Data | Documentation gap | High | High | P0 | Q-001 | REC-001 | Product owner + STK-005 |
| GAP-002 | Role catalogue undefined | Governance | Documentation gap | High | High | P0 | Q-003 | REC-009 | Product owner |
| GAP-003 | Starter pack authoring undefined | Governance | Documentation gap | Medium | Medium | P1 | Q-002 | REC-019 | STK-008 PMO owner |
| GAP-004 | Drill record link target undefined | Product | Documentation gap | Medium | Medium | P1 | Q-004 | — (decision) | Product owner |
| GAP-005 | Share permission level undefined | Product | Documentation gap | Medium | Medium | P1 | Q-005 | — (decision) | Product owner |
| GAP-006 | Publishing rights undefined | Governance | Confirmed gap (sources differ) | Medium | High | P0 | Q-006 | REC-009 | Product owner |
| GAP-007 | Export limits undefined | Operational | Documentation gap | Medium | Medium | P1 | Q-008 | REC-015 | Security architect |
| GAP-008 | Performance targets unconfirmed | Evidence | Documentation gap | High | High | P1 | Q-010 | REC-012 | Product owner |
| GAP-009 | Production PMO source schema not supplied | Evidence | Documentation gap | High | High | P0 | Q-015 | REC-019 | STK-005 |
| GAP-010 | Answer accuracy thresholds undefined | Governance | Documentation gap | High | High | P1 | Q-017 | REC-010 | Product owner + STK-008 |
| GAP-011 | File source scope conflict | Governance | Confirmed gap (conflict C1) | High | Medium | P0 | Q-011 | REC-008 | Sponsor |
| GAP-012 | Connector list differs between sources | Integration | Confirmed gap (conflict C2) | High | High | P0 | Q-012 | REC-006, REC-007 | Product owner |
| GAP-013 | Group mapping setup conflict | Security | Confirmed gap (conflict C4) | Medium | High | P0 | Q-013 | REC-009 | STK-006 |
| GAP-014 | Backup, retention and recovery targets undefined | Operational | Documentation gap | Medium | High | P1 | Q-018 | REC-018 | Platform operator |
| GAP-015 | Localisation, time zone and currency formatting undefined | Data | Documentation gap | Medium | Medium | P2 | — | — (decision) | Product owner |

### GAP-001 Data freshness model undefined

- **Description:** The prototype shows "Data as of 06:00", which implies scheduled refresh or caching, yet widgets are "recomputed for each viewer". The refresh model and cadence per connection are not defined.
- **Evidence:** S01 §4.2, §4.3, §8 Q1.
- **Consequence:** Cannot design caching (ADR-010), "as of" labels (RULE-018) or source load protection.
- **Recommendation:** [REC-001](#rec-001-decide-data-freshness-model-per-connection).
- **Verification:** Freshness mode recorded per connection and shown in every answer.

### GAP-002 Role catalogue undefined

- **Description:** Product roles are Admin, Creator, Viewer; publish targets are business roles (CIO, Executive, Delivery manager). Whether roles are fixed or configurable is open.
- **Evidence:** S01 §2, §4.9, §8 Q3; S05 users and governance table.
- **Consequence:** Permission matrix and publish targets cannot be finalised.
- **Recommendation:** [REC-009](#rec-009-define-role-catalogue-and-group-mappings).
- **Verification:** Signed role catalogue and permission matrix ([function_document.md](function_document.md#roles-and-permission-matrix)).

### GAP-003 Starter pack authoring undefined

- **Description:** How many packs ship in Phase 1 (only PMO?), who authors and versions them, and how schema matching works are open.
- **Evidence:** S01 §4.1, §4.10, §8 Q2.
- **Consequence:** FEAT-010, FEAT-047, FEAT-051 cannot be fully specified.
- **Recommendation:** [REC-019](#rec-019-pilot-with-pmo-pack-on-dataverse-or-tpg-extract).
- **Verification:** Pack manifest format and owner agreed.

### GAP-004 Drill record link target undefined

- **Description:** Drill lists link to "each record". Target could be a Tela record view or the source system (for example a Dynamics 365 form URL).
- **Evidence:** S01 §4.3, §8 Q4.
- **Consequence:** FEAT-044 UI and link-building rules incomplete.
- **Recommendation:** Product decision; until then show a read-only Tela record panel (Proposed).
- **Verification:** Decision recorded in project_summary.md decisions.

### GAP-005 Share permission level undefined

- **Description:** Sharing with named people is described as view-only in Home ("Shared with me — view-only items") but edit-sharing is an open question.
- **Evidence:** S01 §4.2, §8 Q5.
- **Consequence:** FEAT-041 permissions incomplete.
- **Recommendation:** Product decision; default view-only (Proposed).
- **Verification:** Decision recorded.

### GAP-006 Publishing rights undefined

- **Description:** Prototype shows Admin publishing; the decision pack suggests a separate governor/publisher role with separation of duties (conflict C7).
- **Evidence:** S01 §4.9, §8 Q6; S05; S06 §10; S09 RISK-008.
- **Consequence:** Publish workflow and audit cannot be finalised.
- **Recommendation:** [REC-009](#rec-009-define-role-catalogue-and-group-mappings).
- **Verification:** Permission matrix shows who may publish; separation-of-duty test.

### GAP-007 Export limits undefined

- **Description:** Row limits and formatting rules for PDF and Excel exports are not defined.
- **Evidence:** S01 §8 Q8.
- **Consequence:** Export may exceed memory or policy.
- **Recommendation:** [REC-015](#rec-015-define-export-policy).
- **Verification:** Limits configured and tested.

### GAP-008 Performance targets unconfirmed

- **Description:** Prototype timings (report build about 6 s, custom SQL about 0.4 s) are on sample data; production targets are to be confirmed.
- **Evidence:** S01 §6 Responsiveness, §8 Q10; S06 §12.
- **Consequence:** Sizing and NFR-011 cannot be verified.
- **Recommendation:** Agree targets after a load test on representative data; see [REC-012](#rec-012-ship-health-checks-restart-policies-and-ci-from-day-one) for the measurement baseline.
- **Verification:** Signed NFR baseline and performance test report.

### GAP-009 Production PMO source schema not supplied

- **Description:** Only the reference's synthetic two-table extract (`projects`, `project_requests`) is described. The real TPG schema (about 115 tables per S06) and its Dataverse form are unknown.
- **Evidence:** S06 §7; S10 §13; S15.
- **Consequence:** PMO pack measures (RULE-026..039) may not map.
- **Recommendation:** [REC-019](#rec-019-pilot-with-pmo-pack-on-dataverse-or-tpg-extract).
- **Verification:** Schema/DDL or Dataverse metadata export obtained; pack mapping completed.

### GAP-010 Answer accuracy thresholds undefined

- **Description:** No measurable accuracy threshold exists for chat answers or report plans.
- **Evidence:** S05 Q-012; S09 GAP-002.
- **Consequence:** UAT cannot pass or fail objectively.
- **Recommendation:** [REC-010](#rec-010-create-ground-truth-evaluation-suite-before-uat).
- **Verification:** Threshold signed by STK-008.

### GAP-011 File source scope conflict

- **Description:** scope.md §5 puts file uploads out of Phase 1; the stakeholder instruction of 2026-10-21 requires Excel. This register treats Excel/CSV as in scope per the latest instruction (conflict C1).
- **Evidence:** S01 §5; stakeholder instruction.
- **Consequence:** Plan and estimates differ depending on the answer.
- **Recommendation:** [REC-008](#rec-008-treat-excel-as-governed-upload-with-retention); confirm via Q-011.
- **Verification:** Decision recorded; scope.md updated.

### GAP-012 Connector list differs between sources

- **Description:** scope.md lists MySQL, Postgres, SQL Server; the stakeholder adds MongoDB, Excel, Dataverse "etc." (conflict C2). Priority order and the meaning of "etc." are open.
- **Evidence:** S01 §4.1; stakeholder instruction.
- **Consequence:** Connector effort cannot be sequenced.
- **Recommendation:** [REC-006](#rec-006-spike-dataverse-tds-and-web-api), [REC-007](#rec-007-spike-mongodb-spec-compilation); Q-012.
- **Verification:** Ordered connector list signed.

### GAP-013 Group mapping setup conflict

- **Description:** scope.md says "Nothing is set up by hand in Tela", yet some mapping from Microsoft groups to Tela roles and data scopes must exist (conflict C4).
- **Evidence:** S01 §2; S03 §9.
- **Consequence:** Either mappings live in Entra (app roles / claims) or an Admin configures them once in Tela.
- **Recommendation:** [REC-009](#rec-009-define-role-catalogue-and-group-mappings).
- **Verification:** Decision recorded with chosen mechanism.

### GAP-014 Backup, retention and recovery targets undefined

- **Description:** No RPO, RTO, audit retention period or backup policy is supplied.
- **Evidence:** S01 (absent); S06 §12; S03 §20.
- **Consequence:** NFR-018 and NFR-027 unverifiable.
- **Recommendation:** [REC-018](#rec-018-define-backup-and-dr-targets).
- **Verification:** Restore drill meets agreed RPO/RTO.

### GAP-015 Localisation, time zone and currency formatting undefined

- **Description:** The PMO reference uses USD and regions across Africa and the Middle East; the payments reference has multiple currencies. Display time zone for "as of", number formats and currency rules are not specified (S01 mentions "set currency formatting" only).
- **Evidence:** S01 §4.1; S13 RULE-012; S27 RULE-010.
- **Consequence:** Ambiguous dates and amounts.
- **Recommendation:** Product decision; default tenant time zone and per-measure currency (Proposed); RULE-023.
- **Verification:** Decision recorded; formatting tests.

---

## Recommendations

Recommendations are grouped by when they must happen. Effort uses T-shirt sizes (S ≈ days, M ≈ 1–2 weeks, L ≈ 3–6 weeks, XL > 6 weeks) and is a rough, provisional estimate for a small experienced team.

### Immediate clarification or remediation

#### REC-001 Decide data freshness model per connection

- **Problem addressed:** GAP-001, RISK-008, RISK-015.
- **Proposed change:** For each connection choose one mode: live (query per view, cache TTL ≤ 15 min), scheduled (cache warmed on a schedule; "as of" = warm time), or source-reported (as-of from a source field such as an extract timestamp). Show the mode and timestamp on every answer (RULE-018).
- **Business benefit:** Honest "as of" labels; predictable numbers in meetings.
- **Technical benefit:** Defines cache design and source load.
- **Priority:** P0. **Dependencies:** Q-001, STK-005 input. **Effort:** S (decision) + M (build).
- **Risks of the recommendation:** Scheduled mode can show stale data; must label clearly.
- **Verification:** Mode configured per connection; test that the label matches cache timestamps.

#### REC-002 Confirm LLM policy in writing before real data

- **Problem addressed:** RISK-003.
- **Proposed change:** Obtain written approval from STK-009 covering providers, regions, retention, training use and what may be sent (schema, descriptions, questions, aggregates). Configure the LLM gateway allow-list accordingly; default to a local model (Ollama) or the client's tenant-hosted endpoint until approved.
- **Business benefit:** Unblocks use of client data.
- **Technical benefit:** Clear egress contract for RULE-019.
- **Priority:** P0. **Dependencies:** Q-009. **Effort:** S.
- **Risks:** Approved provider may be weaker; affects accuracy.
- **Verification:** Signed policy; gateway config review; egress test.

#### REC-005 Provision dedicated read-only source identities

- **Problem addressed:** RISK-008, RISK-009, RISK-010.
- **Proposed change:** For every connection use a dedicated identity with SELECT-only (or Dataverse read-only security role / MongoDB `read` role) on approved objects, never an admin or shared account. Verify grants at connect time and periodically (RULE-001). Store secrets only in COMP-013.
- **Business benefit:** DBA confidence; faster access approval.
- **Technical benefit:** Database-level backstop independent of code validation.
- **Priority:** P0. **Dependencies:** STK-005. **Effort:** S per connection.
- **Risks:** Over-narrow grants break measures; needs a grant checklist.
- **Verification:** Write attempts with the runtime identity are refused by the source.

#### REC-009 Define role catalogue and group mappings

- **Problem addressed:** GAP-002, GAP-006, GAP-013, RISK-007, RISK-001.
- **Proposed change:** Fix product roles (Admin, Creator, Viewer) and a configurable list of audience roles. Decide whether mappings come from Entra app roles or an Admin-maintained table (FEAT-002). Require approval for any mapping that grants Admin. Decide who may publish (Admin only, or a separate Publisher).
- **Business benefit:** Clear accountability for publishing and data access.
- **Technical benefit:** Finalises permission checks and scope policies.
- **Priority:** P0. **Dependencies:** Q-003, Q-006, Q-013, STK-006. **Effort:** S (decision) + M (build).
- **Risks:** Too many roles increase test burden.
- **Verification:** Signed matrix; tests per role.

#### REC-016 Confirm reuse rights for reference code

- **Problem addressed:** RISK-018.
- **Proposed change:** Confirm ownership and licence of the PMO dashboard, Tela NL2SQL and TelaKnowledge repositories; request access to the parts worth reusing (validator, scope rewriter, health model, gateway).
- **Business benefit:** Avoids legal exposure; may save effort.
- **Technical benefit:** Lets us raise evidence from reference to verified.
- **Priority:** P1. **Dependencies:** Q-019. **Effort:** S.
- **Risks:** Reused code brings its own debt (S16, S30, S31).
- **Verification:** Written confirmation.

### Must-have before implementation or release

#### REC-003 Build spec-first query engine with AST validator

- **Problem addressed:** RISK-002, RISK-010.
- **Proposed change:** The LLM produces a typed query spec referencing approved measures, dimensions and filters (ADR-001). Code compiles it per engine. Custom SQL goes through an AST validator per dialect (single read-only statement, approved objects, no side-effect functions, limits) before scope injection.
- **Business benefit:** Same question gives the same number (BUS-002).
- **Technical benefit:** LLM cannot emit arbitrary SQL; validation is deterministic and testable.
- **Priority:** P0. **Dependencies:** model schema (FEAT-009). **Effort:** L.
- **Risks:** Specs may not express every question; custom SQL remains the escape hatch.
- **Verification:** Validator fuzz suite; spec round-trip tests.

#### REC-004 Enforce scope in one shared query path for all surfaces

- **Problem addressed:** RISK-001, RISK-015.
- **Proposed change:** All source access (widgets, chat, drill, export, filter options, custom SQL, pre-publish checks, preview as role) passes through one scoped execution function in the query engine that injects scope (RULE-004), applies field hiding and computes the cache key including the scope hash. Fail closed on any error.
- **Business benefit:** Data access guaranteed by design.
- **Technical benefit:** One place to test and audit.
- **Priority:** P0. **Dependencies:** REC-009. **Effort:** M.
- **Risks:** Bottleneck if poorly designed; keep it a library plus the connector runner.
- **Verification:** Static check that connector runner is only called from the scoped path; isolation test matrix.

#### REC-006 Spike Dataverse TDS and Web API

- **Problem addressed:** RISK-004, GAP-012.
- **Proposed change:** Time-boxed spike on a client-like Dataverse environment: enablement, Entra app registration and application user, supported SQL for PMO pack measures, timings, throttling, choice of TDS vs Web API per query shape (ADR-013).
- **Business benefit:** De-risks the PMO pilot on live TPG data.
- **Technical benefit:** Confirms compiler targets.
- **Priority:** P0. **Dependencies:** Q-015. **Effort:** M.
- **Risks:** Environment access may take time; start the access request early.
- **Verification:** Spike report with go/no-go per measure.

#### REC-008 Treat Excel as governed upload with retention

- **Problem addressed:** RISK-006, GAP-011.
- **Proposed change:** Upload only by Admin (or permitted Creator), virus/macro check, sheet and header detection, classification step, encrypted storage in object storage, explicit retention and delete, queries via DuckDB read-only views (ADR-012), versioned re-upload.
- **Business benefit:** Meets the Excel requirement without uncontrolled copies.
- **Technical benefit:** Same spec engine as databases.
- **Priority:** P0 (decision) / P1 (build). **Dependencies:** Q-011. **Effort:** M.
- **Risks:** Users treat uploads as a data lake; limit size and count.
- **Verification:** Retention and deletion tests; classification required before use.

#### REC-010 Create ground-truth evaluation suite before UAT

- **Problem addressed:** RISK-002, RISK-011, RISK-020, GAP-010.
- **Proposed change:** Curate gold questions with expected spec, SQL and result per pack (FEAT-015); include look-alike pairs, multi-turn, date-field, enum and fan-out cases; run in CI and before model/provider changes.
- **Business benefit:** Objective acceptance.
- **Technical benefit:** Regression safety for prompts, models and retrieval.
- **Priority:** P1. **Dependencies:** pack content, Q-017. **Effort:** M.
- **Risks:** Suite overfits; refresh regularly.
- **Verification:** Pass rate ≥ agreed threshold.

#### REC-011 Version-pin widgets to model versions

- **Problem addressed:** RISK-012.
- **Proposed change:** Each widget stores the model version it was built on. Approving a new model version runs an impact analysis; published dashboards keep their pinned version until republished; drafts get an upgrade prompt (RULE-015).
- **Business benefit:** No silent change in published numbers.
- **Technical benefit:** Deterministic re-execution.
- **Priority:** P1. **Dependencies:** FEAT-009. **Effort:** M.
- **Risks:** Old versions accumulate; define retirement.
- **Verification:** Test that v2 approval does not change v1-pinned published widgets.

#### REC-012 Ship health checks, restart policies and CI from day one

- **Problem addressed:** RISK-019, GAP-008.
- **Proposed change:** Every image exposes `/healthz` and `/readyz`, has a restart policy, exits non-zero on fatal start-up errors, uses TLS to PostgreSQL and authenticated Redis, drains the outbox with monitoring; CI runs unit, contract, validator fuzz and container health tests; baseline performance test on representative data.
- **Business benefit:** Fewer outages; faster releases.
- **Technical benefit:** Avoids reference failure modes (S31).
- **Priority:** P1. **Dependencies:** none. **Effort:** M.
- **Risks:** Slightly slower initial delivery.
- **Verification:** CI gate; chaos test (kill a container, it recovers).

#### REC-014 Defend RAG against prompt injection

- **Problem addressed:** RISK-014, RISK-020.
- **Proposed change:** Knowledge documents default to `pending_review` and are retrievable only after approval (S02 §5); strip or quote instruction-like text; delimit retrieved content as data; pre-send leak and injection scan (RULE-021); require citations to retrieved items for definitions.
- **Business benefit:** Trustworthy assistant behaviour.
- **Technical benefit:** Limits model manipulation.
- **Priority:** P0. **Dependencies:** FEAT-014. **Effort:** M.
- **Risks:** Review gate slows onboarding of documents.
- **Verification:** Injection corpus tests.

#### REC-015 Define export policy

- **Problem addressed:** RISK-016, GAP-007.
- **Proposed change:** Per-role export permission; row caps per widget; watermark with user, scope, filters and as-of; re-check permission at render; audit every export; optional disable for Viewer.
- **Business benefit:** Controlled distribution.
- **Technical benefit:** Bounded memory for renderer.
- **Priority:** P1. **Dependencies:** Q-008. **Effort:** S.
- **Risks:** Over-restriction frustrates users.
- **Verification:** Export tests per role.

#### REC-020 Tenant isolation tests

- **Problem addressed:** RISK-021.
- **Proposed change:** `tenant_id` on every tenant-scoped table, PostgreSQL RLS as defence in depth, tenant in every cache key and object-storage prefix, per-tenant Keycloak realm or provider routing (S03 §4), automated cross-tenant tests.
- **Business benefit:** Enables SaaS safely.
- **Technical benefit:** Defence in depth.
- **Priority:** P0 if SaaS. **Dependencies:** Q-014. **Effort:** M.
- **Risks:** RLS with connection pooling needs transaction-local tenant context.
- **Verification:** Cross-tenant test suite passes.

### Near-term improvements

#### REC-007 Spike MongoDB spec compilation

- **Problem addressed:** RISK-005, GAP-012.
- **Proposed change:** Compile specs into aggregation pipelines (ADR-014) with a stage allow-list, mandatory first `$match` for scope, `$lookup` only along approved links, grain handling for arrays (`$unwind` declared in the model). Custom SQL disabled for MongoDB in Phase 1.
- **Business benefit:** Supports MongoDB sources.
- **Technical benefit:** Same governance as SQL engines.
- **Priority:** P1. **Dependencies:** ADR-001. **Effort:** M.
- **Risks:** Performance of `$lookup` on large collections.
- **Verification:** Pipeline tests; read-only role check.

#### REC-013 Prioritise chart catalogue

- **Problem addressed:** RISK-013, RISK-017.
- **Proposed change:** Phase 1 ships the S01 §4.11 set (figure, ranked bars, columns, paired bars, table, trend line, stacked status bars, donut for parts of a whole) with RULE-008 suitability; further types added by demand.
- **Business benefit:** Focus and quality.
- **Technical benefit:** Smaller test surface.
- **Priority:** P1. **Dependencies:** product sign-off. **Effort:** S.
- **Risks:** Stakeholders expecting about 100 types.
- **Verification:** Signed catalogue.

#### REC-018 Define backup and DR targets

- **Problem addressed:** GAP-014.
- **Proposed change:** Agree RPO/RTO; daily PostgreSQL backups with point-in-time recovery, Keycloak realm export, object storage versioning, OpenBao snapshots; quarterly restore drill.
- **Business benefit:** Business continuity.
- **Technical benefit:** Tested recovery.
- **Priority:** P1. **Dependencies:** Q-018. **Effort:** S–M.
- **Risks:** Storage cost.
- **Verification:** Restore drill report.

#### REC-019 Pilot with PMO pack on Dataverse or TPG extract

- **Problem addressed:** GAP-003, GAP-009.
- **Proposed change:** Build the PMO starter pack (FEAT-051) against the real TPG schema, via Dataverse if available, otherwise an approved read-only extract; get STK-008 sign-off on measures RULE-026..039.
- **Business benefit:** Early proof of value with the first customer domain.
- **Technical benefit:** Validates connector, spec and pack format end to end.
- **Priority:** P1. **Dependencies:** REC-006, Q-015. **Effort:** L.
- **Risks:** Real data quality issues; synthetic sandbox needed in parallel.
- **Verification:** Pack measures reconcile with the client's current reports.

### Long-term architectural or product improvements

#### REC-017 Accessibility baseline WCAG 2.2 AA

- **Problem addressed:** RISK-022.
- **Proposed change:** Adopt WCAG 2.2 AA; keyboard alternatives for canvas drag-and-drop; table view for every chart; colour-blind-safe palette.
- **Business benefit:** Inclusive, procurement-ready.
- **Technical benefit:** Component-level standards.
- **Priority:** P2. **Dependencies:** design system. **Effort:** M.
- **Risks:** Additional UI effort.
- **Verification:** Audit on key journeys.

---

## Status tracking

| ID | Status | Last reviewed | Next action |
|---|---|---|---|
| RISK-001 … RISK-022 | Open | 2026-10-21 | Assign owners at kick-off |
| GAP-001 … GAP-015 | Open | 2026-10-21 | Raise linked Q-### with stakeholders |
| REC-001 … REC-020 | Proposed (not started) | 2026-10-21 | Prioritise in Phase 1 plan |

Allowed status values: **Open**, **Mitigating**, **Accepted**, **Resolved**. Update the row and the item's verification evidence when status changes.

---

## Lessons carried over from reference systems

These are defects or weaknesses reported by documents about *other* systems. They are not defects in Tela; they informed the risks above.

| Reference | Reported issue | Informs |
|---|---|---|
| S16 RISK-001 (PMO dashboard) | No user authentication; tenancy pinned to one CIO | RISK-001, RISK-007 |
| S16 RISK-004 (PMO dashboard) | SQLite single-file store blocks scaling | ADR-004 |
| S16 RISK-005 (PMO dashboard) | No actual spend data; forecast mislabelled risk | RULE-036, RULE-037 |
| S30 RISK-001 (Tela NL2SQL) | Superuser DB profile removes DB-level backstop | RISK-009, REC-005 |
| S30 RISK-003 (Tela NL2SQL) | Plaintext stored DB passwords | RISK-009 |
| S30 RISK-004 (Tela NL2SQL) | Widget refresh ignored viewer scope | RISK-001, REC-004 |
| S30 RISK-005 (Tela NL2SQL) | JSON flat files force single worker | ADR-004 |
| S30 GAP-001..005 (Tela NL2SQL) | Enum fabrication, entity drop, date confusion, relative dates, entity ambiguity | RISK-002, REC-010 |
| S31 §6 (TelaKnowledge) | Missing auth checks, silent exits, no health checks, undrained outbox, no CI, ports on 0.0.0.0 | RISK-019, REC-012 |

---

## Change log

| Date | Version | Sources added | Summary |
|---|---|---|---|
| 2026-10-21 | 0.1 | S01–S31, stakeholder instruction 2026-10-21 | Initial register: 22 risks, 15 gaps, 20 recommendations. |
