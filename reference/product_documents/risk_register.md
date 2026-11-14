# PMO Analytics Platform — Risk and Gap Register

| Field | Value |
|---|---|
| Project | PMO Analytics & Conversational Dashboard Platform |
| Document status | Draft for review |
| Version | 0.1 |
| Last updated | 2026-10-19 |
| Evidence level | L1–L2; risks are potential unless stated otherwise |
| Related documents | [Project summary](project_summary.md), [Architecture](architecture.md), [Options and plan](options_and_plan.md), [Requirements](requirements.md) |

Likelihood and impact are preliminary estimates based on supplied notes, not a completed threat model.

| ID | Category | Risk / gap | Status | Likelihood | Impact | Priority | Mitigation / verification | Suggested owner |
|---|---|---|---|---|---|---|---|---|
| RISK-001 | Security | Row-level access is not enforced consistently across chat, filters, drill-down, exports and saved widgets | Potential; implementation unknown | High | Critical | P0 | End-to-end authorization model; adversarial tests for CIO and project-manager scopes | Security + backend |
| RISK-002 | Data | Source platform and supported read-only interface are unclear (TPG/Clarity/API/database) | Confirmed documentation gap | High | High | P0 | Confirm source owner, API/driver, network path and grants | Data owner |
| RISK-003 | Product/technical | PMO talk-to-data is reported not to work properly | Meeting-note assertion | High | High | P0 | Reproduce failure; evaluate Tela test corpus and compare results | Tech lead |
| RISK-004 | Technical | Tela/Insights code, ownership, license and deployment fit are unknown | Confirmed evidence gap | Medium | High | P0 | Obtain repository and legal/ownership approval; inspect build/run and interfaces | Tech lead / sponsor |
| RISK-005 | AI/data | Client policy for Foundry/Claude/Copilot and schema/data transmission is unconfirmed | Confirmed open decision | Medium | Critical | P0 | Written approval from Vijay/client security before real data use | Security / Vijay |
| RISK-006 | Data quality | KPI/measure definitions may differ across dashboards and generated queries | Potential | High | High | P0 | Semantic catalog; business-owned definitions; reconciliation tests | PMO/data owner |
| RISK-007 | Security | LLM-generated SQL may be unsafe, overly broad or expensive | Potential | High | Critical | P0 | SQL parser, allow-list, read-only identity, limits, deny-by-default | Backend/security |
| RISK-008 | Governance | Creator, governor/certifier, publisher and admin duties are not fully defined | Documentation gap | Medium | High | P1 | Permission matrix and separation-of-duty decision | Product owner |
| RISK-009 | UX | Prototype journeys may be mistaken for implemented functionality | Potential | High | Medium | P1 | Feature-to-code evidence matrix; label mock/prototype behavior | Product/QA |
| RISK-010 | Scope | Approximately 30 dashboards and 100 chart types are not prioritized or enumerated | Documentation gap | High | High | P1 | Catalog, rank by business value and verify visualization library capabilities | Product owner |
| RISK-011 | Data privacy | Free-text risk reasons may contain sensitive information | Potential | Medium | High | P1 | Classify fields, restrict row access, minimize LLM exposure and logs | Data protection/security |
| RISK-012 | Integration | Microsoft identity claims may not map cleanly to client data scopes | Potential | Medium | Critical | P0 | Confirm tenant/groups/authoritative attributes and test access boundaries | Identity/security |
| RISK-013 | Operations | Client banking permissions and network approvals may block source connectivity | Meeting-note concern | High | High | P1 | Start access request early; mock connector and synthetic schema in parallel | Delivery/platform |
| RISK-014 | Performance | Generated queries may overload source systems | Potential | Medium | High | P1 | Timeout, row/byte caps, workload limits, query review and source-side monitoring | Data/platform |
| RISK-015 | Audit | Query, export, publish and future mutation activity may lack traceable audit records | Potential | Medium | High | P1 | Define audit event catalog and retention; verify actor/object/correlation IDs | Security/platform |
| RISK-016 | Write-back | Future arbitrary INSERT/UPDATE/DELETE through chat could cause unauthorized or irreversible changes | Potential, future scope | Medium | Critical | P0 before enabling write-back | Separate typed command service, plan/approve, idempotency, transaction and audit | Backend/business/security |
| RISK-017 | Product | Hybrid integration could create split auth, metadata and audit semantics | Potential if Option B selected | Medium | High | P1 | Define API contract and one authoritative policy/audit boundary | Architect |
| RISK-018 | Vendor/commercial | Power BI/Tableau or model licensing/cost may alter build-vs-buy economics | Open | Medium | Medium | P2 | Confirm tenant entitlements, connector/licensing and operating cost | Sponsor/procurement |
| RISK-019 | Reliability | No operational SLOs, backup/restore or rollback evidence supplied | Evidence gap | Medium | High | P1 | Define NFRs and runbook; test restore and rollback | Platform/DevOps |
| RISK-020 | Testing | No source code, schema, ground-truth results or runtime evidence supplied | Confirmed evidence limitation | High | High | P0 | Collect artifacts and establish baseline test suite | Tech lead/QA |
| GAP-001 | Data | Exact initial tables, keys, joins, columns and summary-table definitions are not supplied | Confirmed documentation gap | High | High | P0 | Obtain schema/DDL and business definitions; sign off bounded model | Data owner |
| GAP-002 | Acceptance | Accuracy, latency, freshness and availability thresholds are not measurable yet | Confirmed documentation gap | High | High | P1 | Agree baseline and acceptance thresholds before UAT | Product/QA |
| GAP-003 | Governance | Export/share restrictions and version lifecycle rules need confirmation | Documentation gap | Medium | High | P1 | Approve export matrix and published-version policy | Product/security |
| GAP-004 | Architecture | MCP is mentioned as a possibility but connector protocol is not decided | Open design choice | Medium | Medium | P2 | Compare direct supported API/driver vs MCP based on client platform and governance | Architect |
| GAP-005 | Scope | Write-back appears in future-phase discussion but release commitment is unresolved | Open scope decision | High | High | P1 | Treat as separate gated workstream; exclude production mutations until signed off | Sponsor/product |

## Priority interpretation

- **P0:** Resolve before real client data or production access is enabled.
- **P1:** Resolve before release readiness / UAT sign-off.
- **P2:** Resolve during design or before the relevant optional capability is selected.

## Immediate risk-reduction checklist

- [ ] Obtain PMO and Tela/Insights repositories and establish reuse rights.
- [ ] Confirm source platform, read-only connectivity and initial approved schema.
- [ ] Confirm LLM/provider policy in writing with Vijay/client security.
- [ ] Document identity/group-to-data-scope mapping.
- [ ] Build synthetic sandbox and run negative access tests.
- [ ] Establish ground-truth questions and expected outputs.
- [ ] Define query validation, execution caps and audit events.
- [ ] Decide role and certification/publishing separation.
- [ ] Confirm priority dashboards, chart types and export rules.
- [ ] Keep write-back disabled until its dedicated design is approved.
