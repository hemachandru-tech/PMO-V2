# Ground truth: 12 PMO questions on MySQL `tpg` (Dataverse names)

Data date 2026-10-25 (the dashboards' ASOF). Rules: TelaAdditional2.md (Part A wins over Part B) and TelaGlossary2.md columns. Regenerate with `.venv/bin/python scratchpad/groundtruth_tpg.py out.json`.

## Q1. How many active demand requests currently require resource allocation?

```sql
SELECT COUNT(*) AS active_requests FROM tpg_projectrequests WHERE tpg_requeststatename='Active'
```

| active_requests |
|---|
| 22 |

## Q2. Register: On-Hold projects, reasons and comments

```sql
SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, p.tpg_projectstatusname AS state, COALESCE(p.tpgmea_evwronholdreasonname,'(Blank)') AS hold_reason, DATE(p.tpgmea_projectputonholddate) AS hold_date, DATEDIFF('2026-10-25', DATE(p.tpgmea_projectputonholddate)) AS days_on_hold FROM tpg_project p WHERE p.tpg_projectstatusname='On Hold' ORDER BY p.tpg_subject
```

| project | project_no | state | hold_reason | hold_date | days_on_hold |
|---|---|---|---|---|---|
| Adaptive Workflow Programme 420 | BE-299389 | On Hold | Client Readiness Issues (Client not prepared for next steps) | 2026-10-11 | 14 |
| Bold Network Automation 420 | UR-4424370 | On Hold | Client Readiness Issues (Client not prepared for next steps) | 2026-08-21 | 65 |
| Elastic Cards Retirement | YE-1999153 | On Hold | (Blank) |  |  |
| Guided Identity Integration | UT-8071924 | On Hold | (Blank) |  |  |
| Joint Archive Redesign 178 | TW-7264997 | On Hold | (Blank) |  |  |
| Nimble Compliance Extension | IC-1148490 | On Hold | (Blank) |  |  |
| Resilient Compliance Consolidation 132 | FL-679027 | On Hold | Client Readiness Issues (Client not prepared for next steps) | 2026-10-19 | 6 |
| Trusted Onboarding Migration | TX-8314423 | On Hold | (Blank) |  |  |

## Q3. Count of active projects (Regional Portfolio) - open per Part A

```sql
SELECT COUNT(*) AS open_projects, SUM(p.tpg_projectstatusname='Active') AS strictly_active FROM tpg_project p WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets')
```

| open_projects | strictly_active |
|---|---|
| 95 | 88 |

## Q4. Open projects by CIO

```sql
SELECT COALESCE(p.tpgmea_customcioname,'(not set)') AS cio, COUNT(*) AS projects FROM tpg_project p WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') GROUP BY 1 ORDER BY (cio='(not set)'), projects DESC, cio
```

| cio | projects |
|---|---|
| Giselle Nightingale | 39 |
| Ilsa Abernethy | 20 |
| Philippa Ormsby | 8 |
| Anouk Fairbank | 5 |
| Greta Jarrold | 4 |
| Coralie Jarrold | 3 |
| Florian Sedgwick | 3 |
| Ines Stanmore | 3 |
| Marisol Yardley | 3 |
| Ilsa Stanmore | 2 |
| Katrin Ingleby | 1 |
| Noemi Quennell | 1 |
| Xanthe Bramwell | 1 |
| (not set) | 2 |

## Q5. Register of open projects with RAG (Yellow shown as Amber)

```sql
SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, REPLACE(COALESCE(p.tpg_overallkpiname,'(Blank)'),'Yellow','Amber') AS overall, REPLACE(COALESCE(p.tpg_schedulekpiname,'(Blank)'),'Yellow','Amber') AS schedule, REPLACE(COALESCE(p.tpg_costkpiname,'(Blank)'),'Yellow','Amber') AS cost, REPLACE(COALESCE(p.tpg_issuekpiname,'(Blank)'),'Yellow','Amber') AS issue FROM tpg_project p WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') ORDER BY p.tpg_subject
```

| project | project_no | overall | schedule | cost | issue |
|---|---|---|---|---|---|
| Adaptive Fraud Uplift 790 | WL-1114013 | Amber | Green | Green | Green |
| Adaptive Ledger Rollout | XA-4986764 | Green | Green | Green | Red |
| Adaptive Merchant Redesign 575 | XV-2482379 | Green | Green | Green | Green |
| Adaptive Onboarding Extension 83 | KR-4418280 | Green | Green | Green | Green |
| Adaptive Reconciliation Stabilisation 783 | LQ-4681073 | Green | Green | Green | Green |
| Bold Analytics Extension 200 | RL-4820729 | Green | Green | Green | Green |
| Bold Identity Integration | VB-8270615 | Green | Green | Green | Green |
| Bold Payments Integration 305 | YF-133270 | Green | Green | Green | Green |
| Bold Treasury Uplift | FQ-0069166 | Red | Red | Green | Red |
| Clear Archive Programme 130 | US-9773979 | Amber | Amber | Green | Green |
| Clear Billing Hardening 844 | JH-7502062 | Red | Red | Green | Red |
| Clear Gateway Stabilisation | GF-0764896 | Red | Green | Green | Green |
| Clear Network Track 146 | MZ-0266804 | Green | Green | Green | Green |
| Direct Compliance Migration 746 | SC-1232325 | Green | Amber | Green | Green |
| Direct Compliance Refresh 473 | KZ-542712 | Green | Green | Green | Green |
| Direct Fraud Retirement | SE-9573238 | Green | Green | Green | Green |
| Direct Ledger Rebuild 282 | FR-6204971 | Green | Green | Green | Green |
| Direct Reconciliation Rollout 667 | GY-904333 | Green | Green | Green | Green |
| Direct Reporting Stabilisation 385 | HQ-9756187 | Green | Green | Green | Green |
| Elastic Archive Rebuild | YR-9318756 | Green | Green | Green | Green |
| Elastic Compliance Hardening 950 | PZ-1091682 | Green | Green | Green | Green |
| Elastic Compliance Modernisation | KK-5517548 | Green | Green | Green | Green |
| Focused Billing Upgrade | WA-4410262 | Green | Green | Green | Green |
| Focused Fraud Hardening | ZQ-7070111 | Green | Green | Green | Green |
| Focused Fraud Rebuild 260 | FK-3708000 | Green | Green | Green | Green |
| Focused Fraud Rollout | FB-2887139 | Green | Green | Green | Green |
| Focused Network Uplift 786 | WL-8341593 | Red | Green | Green | Green |
| Focused Workflow Extension | TG-6444024 | Red | Green | Green | Green |
| Guided Compliance Integration | BN-955564 | Red | Red | Red | Red |
| Guided Gateway Automation 918 | ZK-3327519 | Amber | Green | Green | Green |
| Guided Reporting Initiative | TV-097952 | Green | Green | Green | Green |
| Harmonised Archive Initiative | SS-290595 | Green | Green | Green | Green |
| Harmonised Billing Programme 89 | BI-1469186 | Amber | Green | Green | Green |
| Harmonised Compliance Extension 29 | KR-181699 | Green | Green | Green | Green |
| Harmonised Ledger Extension | MF-8457769 | Green | Green | Green | Green |
| Harmonised Reconciliation Retirement 909 | CC-0206175 | Green | Green | Green | Green |
| Harmonised Treasury Integration 298 | TZ-032980 | Green | Green | Green | Green |
| Integrated Identity Refresh 302 | LV-6643226 | Green | Green | Green | Green |
| Joint Compliance Stabilisation 600 | BK-1400827 | Green | Green | Green | Green |
| Joint Data Uplift | KW-1854040 | Amber | Amber | Green | Red |
| Joint Messaging Stabilisation | SV-4654041 | Green | Green | Green | Green |
| Joint Workflow Hardening 193 | LX-3914540 | Green | Green | Green | Green |
| Joint Workflow Retirement | NO-6616714 | Green | Green | Green | Green |
| Joint Workflow Retirement 689 | EF-5383544 | Green | Green | Green | Green |
| Keystone Analytics Retirement 542 | TM-9002276 | Red | Green | Green | Green |
| Keystone Archive Uplift 215 | HG-4237566 | Green | Green | Green | Green |
| Lean Archive Redesign 475 | FQ-2111457 | Green | Green | Green | Green |
| Lean Compliance Track | VE-1080807 | Green | Green | Green | Green |
| Lean Fraud Retirement | WK-7650209 | Red | Green | Green | Green |
| Lean Messaging Retirement 749 | QC-6434218 | Green | Green | Green | Green |
| Lean Payments Hardening 679 | HQ-034043 | Green | Green | Green | Green |
| Modular Cards Retirement 342 | MB-8422446 | Green | Green | Green | Green |
| Modular Ledger Upgrade | YS-2675485 | Amber | Green | Green | Green |
| Modular Reconciliation Integration | XP-3638114 | Red | Red | Red | Red |
| Modular Settlement Stabilisation | ZW-3629489 | Green | Green | Green | Green |
| Nimble Fraud Uplift | IG-0524821 | Green | Green | Green | Green |
| Nimble Gateway Retirement | DR-393847 | Amber | Green | Green | Amber |
| Open Compliance Extension 233 | IA-6727480 | Green | Green | Green | Green |
| Open Gateway Stabilisation | HS-2845886 | Green | Green | Green | Green |
| Open Identity Retirement | CT-149121 | Green | Green | Green | Green |
| Open Reconciliation Refresh 640 | JT-3528191 | Green | Green | Green | Green |
| Precise Archive Extension 911 | CN-868438 | Green | Green | Green | Green |
| Precise Compliance Modernisation 846 | ED-5364416 | Green | Green | Green | Green |
| Precise Data Automation | CX-0886965 | Red | Green | Green | Green |
| Precise Ledger Integration 680 | QU-0064090 | Green | Green | Green | Green |
| Precise Settlement Hardening 508 | TS-7134568 | Green | Green | Green | Green |
| Quick Cards Automation 328 | SN-077064 | Green | Green | Green | Green |
| Quick Merchant Refresh 346 | IW-9264035 | Green | Green | Green | Green |
| Quick Messaging Initiative 475 | EI-051507 | Green | Green | Red | Green |
| Quick Messaging Rebuild 857 | FH-7023154 | Green | Green | Green | Amber |
| Quick Reconciliation Extension 367 | OE-9849175 | Green | Green | Green | Green |
| Resilient Compliance Rollout | MD-6819243 | Green | Green | Green | Green |
| Resilient Fraud Migration | MR-3168946 | Green | Green | Green | Green |
| Resilient Ledger Rollout 441 | VT-9868518 | Amber | Amber | Amber | Amber |
| Resilient Merchant Retirement 187 | VQ-2194876 | Green | Green | Green | Green |
| Resilient Network Automation 639 | UR-483901 | Green | Green | Green | Green |
| Resilient Reconciliation Integration | QU-405794 | Green | Green | Green | Green |
| Resilient Workflow Modernisation 197 | KM-679532 | Green | Green | Green | Green |
| Resilient Workflow Rollout 507 | GF-8954237 | Green | Green | Green | Green |
| Streamlined Billing Track 809 | FK-6310076 | Red | Green | Green | Green |
| Streamlined Fraud Refresh | PA-939829 | Red | Red | Red | Red |
| Streamlined Identity Retirement 738 | QB-1748077 | Green | Green | Green | Green |
| Streamlined Portal Migration 51 | MC-974899 | Green | Green | Green | Green |
| Streamlined Treasury Consolidation 597 | MS-5106562 | Green | Green | Green | Green |
| Trusted Compliance Programme 141 | MN-0194219 | Green | Green | Green | Green |
| Trusted Fraud Retirement 967 | GP-373837 | Green | Green | Green | Green |
| Trusted Ledger Uplift | ZS-301828 | Green | Green | Green | Green |
| Wide Analytics Integration | HQ-6431436 | Red | Green | Green | Green |
| Wide Archive Redesign 332 | YA-3867933 | Amber | Amber | Green | Green |
| Wide Archive Stabilisation 904 | YR-762456 | Green | Green | Green | Green |
| Zonal Archive Upgrade | XI-9419829 | Red | Red | Green | Green |
| Zonal Onboarding Retirement 248 | RJ-4121533 | Green | Green | Green | Green |
| Zonal Portal Uplift 788 | LL-6087878 | Green | Green | Green | Green |
| Zonal Reporting Redesign 115 | RM-2575041 | Red | Red | Green | Green |
| Zonal Settlement Migration | ZY-2172705 | Green | Green | Green | Green |

## Q6. Open projects ranked by change requests awaiting approval

```sql
SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, COUNT(*) AS awaiting_pcrs FROM tpg_change ch JOIN tpg_project p ON ch.tpg_project=p.tpg_projectid WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') AND ch.tpg_approvalname NOT IN ('Fully Approved','Delivery Manager Rejected','Requested By (RM) Rejected') AND COALESCE(ch.tpg_changetypename,'')<>'Initial Baseline' AND COALESCE(ch.tpgmea_reasonforpcrname,'')<>'Initial Baseline' GROUP BY p.tpg_projectid, p.tpg_subject, p.tpgmea_pprojectnumber ORDER BY awaiting_pcrs DESC, project
```

| project | project_no | awaiting_pcrs |
|---|---|---|
| Streamlined Billing Track 809 | FK-6310076 | 4 |
| Zonal Archive Upgrade | XI-9419829 | 3 |
| Bold Identity Integration | VB-8270615 | 1 |
| Bold Treasury Uplift | FQ-0069166 | 1 |
| Joint Compliance Stabilisation 600 | BK-1400827 | 1 |
| Lean Archive Redesign 475 | FQ-2111457 | 1 |
| Resilient Fraud Migration | MR-3168946 | 1 |

## Q7. Active projects by days since last modified

```sql
SELECT CASE WHEN d<=7 THEN '1: 0-7 days' WHEN d<=30 THEN '2: 8-30' WHEN d<=60 THEN '3: 31-60' WHEN d<=120 THEN '4: 61-120' ELSE '5: 120+' END AS bucket, COUNT(*) AS projects FROM (SELECT DATEDIFF('2026-10-25', DATE(p.modifiedon)) AS d FROM tpg_project p WHERE p.tpg_projectstatusname='Active' AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname<>'Timesheets')) t GROUP BY 1 ORDER BY 1
```

| bucket | projects |
|---|---|
| 1: 0-7 days | 6 |
| 2: 8-30 | 35 |
| 3: 31-60 | 22 |
| 4: 61-120 | 22 |
| 5: 120+ | 3 |

## Q8. Journey legs: open projects traced to a request

```sql
SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, r.tpg_title AS request, DATEDIFF(DATE(p.createdon), DATE(r.tpgmea_clientrequestdate)) AS ask_to_project_created_days, DATEDIFF(DATE(p.createdon), DATE(r.tpgmea_darbapprovaltimestamp)) AS darb_to_project_created_days, DATEDIFF(DATE(p.tpgmea_firstbaselinedpdd), DATE(p.createdon)) AS created_to_first_baseline_days FROM tpg_project p JOIN tpg_projectrequests r ON p.tpg_projectrequest=r.tpg_projectrequestsid WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') ORDER BY p.tpg_subject
```

| project | project_no | request | ask_to_project_created_days | darb_to_project_created_days | created_to_first_baseline_days |
|---|---|---|---|---|---|
| Bold Payments Integration 305 | YF-133270 | Precise Fraud Stabilisation 23 | 192 | -11 |  |
| Direct Compliance Refresh 473 | KZ-542712 | Vital Clearing Extension | 107 | 23 | 70 |
| Direct Reconciliation Rollout 667 | GY-904333 | Wide Fraud Retirement 563 | 110 | 52 | 89 |
| Guided Compliance Integration | BN-955564 | Guided Gateway Integration | 295 | 21 | 111 |
| Guided Reporting Initiative | TV-097952 | Trusted Clearing Hardening 407 | 158 | 79 |  |
| Harmonised Archive Initiative | SS-290595 | Guided Gateway Track 968 | 69 | 45 |  |
| Harmonised Compliance Extension 29 | KR-181699 | Precise Fraud Stabilisation 23 | 145 | -58 |  |
| Harmonised Treasury Integration 298 | TZ-032980 | Trusted Clearing Hardening 407 | 160 | 81 |  |
| Lean Payments Hardening 679 | HQ-034043 | Clear Merchant Initiative 669 | 597 | 78 | 101 |
| Nimble Gateway Retirement | DR-393847 | Open Reporting Migration 854 | 93 | -3 |  |
| Open Identity Retirement | CT-149121 | Focused Messaging Programme 620 | -6 | -6 |  |
| Precise Archive Extension 911 | CN-868438 | Zonal Fraud Track 501 | 124 | 95 |  |
| Quick Cards Automation 328 | SN-077064 | Guided Billing Programme 400 | 316 | 33 | 27 |
| Quick Messaging Initiative 475 | EI-051507 | Nimble Fraud Programme | 416 | -11 | 65 |
| Resilient Network Automation 639 | UR-483901 | Precise Fraud Refresh 472 | 56 | 10 |  |
| Resilient Reconciliation Integration | QU-405794 | Harmonised Analytics Redesign 677 | 101 | 8 | 19 |
| Resilient Workflow Modernisation 197 | KM-679532 | Lean Cards Consolidation | 105 | -5 | 103 |
| Streamlined Fraud Refresh | PA-939829 | Keystone Compliance Refresh 647 | 548 | 13 | 124 |
| Streamlined Portal Migration 51 | MC-974899 | Harmonised Analytics Redesign 677 | 101 | 8 | 19 |
| Trusted Fraud Retirement 967 | GP-373837 | Keystone Billing Hardening 584 | 15 | -3 |  |
| Trusted Ledger Uplift | ZS-301828 | Trusted Workflow Consolidation 200 | 261 | 13 | 82 |
| Wide Archive Stabilisation 904 | YR-762456 | Harmonised Clearing Retirement 960 | 17 | 11 |  |

## Q9. Top 20 open requests: Strategic objective or Critical/High priority, then money

```sql
SELECT r.tpg_title AS request, r.tpgmea_projectnumber AS request_no, r.tpg_requeststatename AS state, r.tpg_priorityname AS priority, r.tpgmea_projectobjectivename AS objective, r.tpgmea_recurringrevenueannualbaselineusd AS revenue, r.tpgmea_costtotal_usd AS cost FROM tpg_projectrequests r WHERE (r.tpg_requeststatename IS NULL OR r.tpg_requeststatename NOT IN ('Approved','Dropped','On Hold','Return to Requester')) AND (r.tpgmea_projectobjectivename='Strategic' OR r.tpg_priorityname IN ('Critical','High')) ORDER BY (r.tpgmea_projectobjectivename='Strategic' AND r.tpg_priorityname IN ('Critical','High')) DESC, FIELD(r.tpg_priorityname,'Critical','High') , COALESCE(r.tpgmea_recurringrevenueannualbaselineusd,0) DESC
```

| request | request_no | state | priority | objective | revenue | cost |
|---|---|---|---|---|---|---|
| Streamlined Onboarding Integration 119 | XF-474946 | Active | Critical | Strategic | 0.0000 | 0E-10 |
| Guided Data Rollout | EO-032071 | Active | High | Strategic | 14950.0000 | 107512.0000000000 |
| Bold Identity Redesign 34 | KK-853243 | Active | High | Strategic | 0.0000 | 0E-10 |
| Trusted Billing Refresh 882 | VI-350564 | New | High | Strategic |  | 4770.0000000000 |
| Elastic Settlement Upgrade 920 | NS-592724 | New | Moderate | Strategic | 4607.0000 | 0E-10 |
| Streamlined Ledger Stabilisation | SP-678532 | Active | Moderate | Strategic | 0.0000 | 0E-10 |
| Elastic Data Refresh 585 | WG-282794 | New | Moderate | Strategic |  | 0E-10 |
| Integrated Messaging Track | FP-974241 | New | Moderate | Strategic |  | 77014.0000000000 |
| Modular Compliance Consolidation | LB-092049 | Active | Low | Strategic | 0.0000 | 0E-10 |
| Nimble Reconciliation Extension 800 | ZS-881704 | New | Moderate | Strategic | 0.0000 | 0E-10 |
| Zonal Reporting Integration | KU-297117 | Active | Critical | Revenue Generating | 0.0000 | 1981.5400000000 |
| Direct Settlement Stabilisation | GM-678418 | Active | Critical | Revenue Generating |  | 0E-10 |
| Trusted Gateway Modernisation 235 | GT-474494 | Active | Critical | Revenue Generating |  | 3420.0000000000 |
| Guided Workflow Upgrade 793 | QY-011309 | Active | High | Efficiency | 127137.0000 | 392712.0000000000 |
| Zonal Archive Migration 790 | NK-372718 | New | High | Efficiency | 17295.0000 | 0E-10 |
| Open Treasury Upgrade | BH-696581 | New | High | Revenue Generating | 14827.0000 | 32987.0000000000 |
| Quick Gateway Modernisation 876 | UX-977666 | New | High | Regulatory | 0.0000 | 0E-10 |
| Streamlined Messaging Stabilisation | MS-733972 | Active | High | Regulatory | 0.0000 | 0E-10 |
| Clear Workflow Consolidation 168 | SE-186316 | New | High | Revenue Generating | 0.0000 | 0E-10 |
| Guided Data Redesign | WE-436516 | New | High | Revenue Generating |  | 56094.0000000000 |
| Guided Data Consolidation | LS-600331 | Active | High | Revenue Generating | 0.0000 | 0E-10 |
| Quick Compliance Automation | VS-656417 | New | High | Efficiency | 0.0000 | 12696.0000000000 |
| Streamlined Data Initiative 405 | VY-215737 | New | High | Regulatory | 0.0000 | 0E-10 |
| Zonal Identity Automation | OS-306054 | Active | High | Efficiency | 0.0000 | 36424.0000000000 |
| Zonal Onboarding Rollout | SN-129743 | New | High | Efficiency | 0.0000 | 0E-10 |
| Focused Fraud Redesign 618 | JH-639257 | New | High | Revenue Generating | 0.0000 | 3673.0000000000 |
| Harmonised Messaging Initiative 546 | HG-914774 | Active | High | Revenue Generating | 0.0000 | 0E-10 |

## Q10. Open projects due in next 30/60/90 days (not deployed, planned deploy date in window)

```sql
SELECT SUM(dd<=30) AS due_30, SUM(dd<=60) AS due_60, SUM(dd<=90) AS due_90, COUNT(*) AS open_projects_total FROM (SELECT DATEDIFF(DATE(p.tpgmea_productdeploydate),'2026-10-25') AS dd FROM tpg_project p WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') AND p.tpgmea_projectimpdate IS NULL AND p.tpgmea_productdeploydate IS NOT NULL AND DATE(p.tpgmea_productdeploydate)>='2026-10-25') t RIGHT JOIN (SELECT 1) x ON 1=1
```

| due_30 | due_60 | due_90 | open_projects_total |
|---|---|---|---|
| 5 | 7 | 9 | 9 |

## Q11. Critical path: open projects with a critical task / critical-or-high priority

```sql
SELECT (SELECT COUNT(DISTINCT t.tpg_project) FROM tpg_projecttask t JOIN tpg_project p ON t.tpg_project=p.tpg_projectid WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') AND t.tpg_iscriticalname='Yes') AS open_projects_with_critical_task, (SELECT COUNT(*) FROM tpg_project p WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets') AND p.tpgmea_priorityname IN ('Critical','High')) AS open_critical_or_high_priority, (SELECT COUNT(*) FROM tpg_project p WHERE (p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets')) AS open_projects
```

| open_projects_with_critical_task | open_critical_or_high_priority | open_projects |
|---|---|---|
| 16 | 23 | 95 |

## Q12. Open requests past a planned gate date (Part A: HLE, DOU, SOW; Part B: DOU, DARB, SOW)

```sql
SELECT COUNT(*) AS requests_open, SUM(hle OR dou OR sow) AS partA_requests_with_overdue_gate, SUM(hle)+SUM(dou)+SUM(sow) AS partA_gate_rows, SUM(hle) hle, SUM(dou) dou, SUM(sow) sow, SUM(dou OR darb OR sow) AS partB_requests_with_overdue_gate, SUM(darb) darb FROM (SELECT (r.tpgmea_hlerequiredname='Yes' AND DATE(r.tpgmea_planneddatetoissuehle)<'2026-10-25' AND r.tpgmea_highleveleffortsprovideddate IS NULL) AS hle, (r.tpgmea_dourequiredname='Yes' AND DATE(r.tpgmea_planneddouissuancedate)<'2026-10-25' AND r.tpgmea_dousubmissiondate IS NULL) AS dou, (DATE(r.tpgmea_tentativedateofsowissuance)<'2026-10-25' AND r.tpgmea_sowissueddate IS NULL) AS sow, (COALESCE(r.tpgmea_darbexemptedname,'')<>'Yes' AND DATE(r.tpgmea_darbreviewdate)<'2026-10-25' AND r.tpgmea_darbapprovaltimestamp IS NULL) AS darb FROM tpg_projectrequests r WHERE (r.tpg_requeststatename IS NULL OR r.tpg_requeststatename NOT IN ('Approved','Dropped','On Hold','Return to Requester'))) t
```

| requests_open | partA_requests_with_overdue_gate | partA_gate_rows | hle | dou | sow | partB_requests_with_overdue_gate | darb |
|---|---|---|---|---|---|---|---|
| 60 | 4 | 4 | 0 | 2 | 2 | 4 | 0 |
