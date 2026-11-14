"""Synthetic TPG extract for the PMO pack (US-040; TEST-060). Synthetic data only — no client data.

Reproduces the S13 reference counts: 100 projects; 65 open (status Active) of which
13 are in delivery (current state Active), 3 on hold and 49 project created; 35
inactive. Also plants the worked examples and rule boundaries from
business_rules.md (overdue at asOf - 1 vs asOf, 15-day RAG contradiction, day 30
vs day 31 horizons, the health example scoring 50.0 → Amber and the cost
override example → Red). Generation is deterministic.

US-156 adds the catalogue's columns (project manager, budget code and type, hold reasons, cost components, TCV;
request creation, hold, gate, effort and budget fields) from a second random stream, so the S13 counts and
every earlier boundary stay as they were, plus boundaries for the new rules: on hold 90 vs 91 days, requests on
hold 180 vs 181 days, deployed 60 vs 61 days late, discovery early / on time / late.

US-155 adds the screens the real TPG source supports beyond projects and requests (third random stream; earlier
values untouched): project stage plan and closure columns, change-request fields on requests, and the activity
tables linked to projects by ``project_id`` — PCRs, status reports, risks, issues, action items, decisions,
timesheets and project tasks — with boundaries on the reporting date (status report exactly 30 vs 31 days old,
action due on the reporting date vs the day before, critical task finishing the day before vs on the date).

    python packs/pmo/fixtures/tpg_synthetic.py --sql deploy/compose/sample-tpg/02-data.sql
    python packs/pmo/fixtures/tpg_synthetic.py --dialect mysql --sql deploy/compose/sample-mysql/02-data.sql
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

AS_OF = date(2026, 9, 30)
CIOS = ["Nomsa Osei", "Karim Haddad", "Aisha Rahman", "Daniel Mwangi"]
REGIONS = ["DPO", "East Africa", "Francophone", "GCC - UAE", "KSA", "Jordan"]
PRIORITIES = ["Critical", "High", "Moderate", "Low"]
PLATFORMS = ["Network One", "Network Flex", "Magnati", "Legacy / Other"]
PHASES = ["Assign PM", "Initiation", "Planning", "Execution", "UAT", "Deployment", "Closure"]
PORTFOLIOS = ["Acquiring", "Issuing", "Digital", "Infrastructure"]
KPIS = ["Green", "Yellow", "Red"]
INACTIVE_STATES = ["Completed", "Completed", "Completed", "Dropped", "Cancelled", "Archived", "Expired"]
REQUEST_STATES = ["New", "Active", "On Hold", "Return to Requester", "Approved", "Rejected", "Dropped"]
MANAGERS = ["Lina Farouk", "Omar Saleh", "Grace Achieng", "Pierre Diallo", "Sara Nasser", "Yusuf Ali"]
HOLD_REASONS = ["Awaiting funding", "Client priority change", "Dependency on vendor", "Resource constraint"]
GATES = ["New", "HLE", "BRD", "DOU", "DARB", "SOW", "Approval"]

DDL = """
DROP TABLE IF EXISTS change_requests;
DROP TABLE IF EXISTS status_reports;
DROP TABLE IF EXISTS risks;
DROP TABLE IF EXISTS issues;
DROP TABLE IF EXISTS action_items;
DROP TABLE IF EXISTS decisions;
DROP TABLE IF EXISTS timesheets;
DROP TABLE IF EXISTS project_tasks;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS project_requests;
CREATE TABLE project_requests (
  project_request_id varchar(64) PRIMARY KEY,
  project_number varchar(64),
  title varchar(255) NOT NULL,
  request_state varchar(64),
  cio varchar(100),
  region varchar(64),
  priority varchar(32),
  stage1_discovery_planned_start_date date,
  stage1_discovery_planned_end_date date,
  stage1_discovery_actual_end_date date,
  dou_required varchar(8),
  dou_planned_date date,
  dou_issued_date date,
  darb_exempted varchar(8),
  darb_review_date date,
  darb_approval_date date,
  sow_planned_date date,
  sow_issued_date date,
  approved_budget_value_usd numeric(15,2),
  cost_total_usd numeric(15,2),
  total_setup_revenue_usd numeric(15,2),
  recurring_revenue_annual_baseline_usd numeric(15,2),
  efforts_internal_mandays integer,
  efforts_external_mandays integer,
  efforts_saba_mandays integer,
  intake_gate varchar(32),
  delivery_manager varchar(100),
  platform varchar(64),
  budget_type varchar(16),
  budget_code varchar(32),
  on_hold_date date,
  on_hold_reason varchar(100),
  created_on timestamp,
  is_change_request varchar(8),
  cr_reason varchar(64),
  master_project_id varchar(64)
);
CREATE TABLE projects (
  project_id varchar(64) PRIMARY KEY,
  project_number varchar(64) UNIQUE,
  name varchar(255) NOT NULL,
  portfolio varchar(100),
  program varchar(100),
  status varchar(32),
  current_state varchar(64),
  lifecycle_phase varchar(64),
  cio varchar(100),
  region varchar(64),
  delivery_manager varchar(100),
  priority varchar(32),
  platform varchar(64),
  key_priority_project varchar(8),
  percent_complete double precision,
  overall_kpi varchar(16),
  schedule_kpi varchar(16),
  cost_kpi varchar(16),
  issue_kpi varchar(16),
  planned_deployment_date date,
  first_baselined_deployment_date date,
  actual_deployment_date date,
  actual_project_start_date date,
  on_hold_date date,
  approved_budget_value_usd numeric(15,2),
  cost_total_usd numeric(15,2),
  cost_internal_usd numeric(15,2),
  cost_external_usd numeric(15,2),
  cost_contingency_usd numeric(15,2),
  cost_hw_usd numeric(15,2),
  cost_sw_usd numeric(15,2),
  estimated_tcv_usd numeric(15,2),
  project_manager varchar(100),
  budget_code varchar(32),
  budget_type varchar(16),
  on_hold_reason varchar(100),
  approved_pcr_counter integer,
  project_request_id varchar(64),
  modified_on timestamp,
  created_on timestamp,
  planning_stage_end_date date,
  design_stage_end_date date,
  build_stage_end_date date,
  test_stage_end_date date,
  implementation_stage_end_date date,
  warranty_stage_end_date date,
  closure_approval_status varchar(32),
  closure_date date,
  CONSTRAINT fk_projects_request FOREIGN KEY (project_request_id) REFERENCES project_requests(project_request_id)
);
CREATE TABLE change_requests (
  change_request_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  pcr_number varchar(32),
  title varchar(255),
  reason varchar(100),
  change_type varchar(32),
  approval_status varchar(64),
  golive_change varchar(8),
  additional_cost_usd numeric(15,2),
  requested_on date
);
CREATE TABLE status_reports (
  status_report_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  title varchar(255),
  report_date date,
  submitted_to varchar(100)
);
CREATE TABLE risks (
  risk_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  title varchar(255),
  category varchar(32),
  state varchar(16),
  likelihood integer,
  consequence integer,
  exposure numeric(10,2),
  cost_exposure_usd numeric(15,2),
  due_date date,
  owner varchar(100)
);
CREATE TABLE issues (
  issue_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  title varchar(255),
  category varchar(32),
  priority varchar(16),
  status varchar(16),
  due_date date,
  owner varchar(100)
);
CREATE TABLE action_items (
  action_item_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  title varchar(255),
  state varchar(32),
  due_date date,
  owner varchar(100)
);
CREATE TABLE decisions (
  decision_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  decision varchar(255),
  status varchar(16),
  target_date date
);
CREATE TABLE timesheets (
  timesheet_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  submitter varchar(100),
  task_name varchar(255),
  status varchar(16),
  total_hours numeric(8,2),
  created_on timestamp
);
CREATE TABLE project_tasks (
  task_id varchar(64) PRIMARY KEY,
  project_id varchar(64),
  project_name varchar(255),
  subject varchar(255),
  is_critical varchar(8),
  is_summary varchar(8),
  progress numeric(6,2),
  start_date date,
  finish_date date
);
"""
DIALECTS = ("postgres", "mysql", "tsql")


def ddl(dialect: str = "postgres") -> str:
    """The extract DDL for one engine (US-113, US-114): the same tables, keys and link everywhere."""
    if dialect not in DIALECTS:
        raise ValueError(dialect)
    if dialect == "tsql":
        return DDL.replace("double precision", "float").replace(" timestamp", " datetime2")
    return DDL

PROJECT_COLUMNS = [
    "project_id", "project_number", "name", "portfolio", "program", "status", "current_state", "lifecycle_phase",
    "cio", "region", "delivery_manager", "priority", "platform", "key_priority_project", "percent_complete",
    "overall_kpi", "schedule_kpi", "cost_kpi", "issue_kpi", "planned_deployment_date",
    "first_baselined_deployment_date", "actual_deployment_date", "actual_project_start_date", "on_hold_date",
    "approved_budget_value_usd", "cost_total_usd", "cost_internal_usd", "cost_external_usd", "cost_contingency_usd",
    "cost_hw_usd", "cost_sw_usd", "estimated_tcv_usd", "project_manager", "budget_code", "budget_type",
    "on_hold_reason", "approved_pcr_counter", "project_request_id", "modified_on", "created_on",
    "planning_stage_end_date", "design_stage_end_date", "build_stage_end_date", "test_stage_end_date",
    "implementation_stage_end_date", "warranty_stage_end_date", "closure_approval_status", "closure_date",
]
REQUEST_COLUMNS = [
    "project_request_id", "project_number", "title", "request_state", "cio", "region", "priority",
    "stage1_discovery_planned_start_date", "stage1_discovery_planned_end_date", "stage1_discovery_actual_end_date",
    "dou_required", "dou_planned_date", "dou_issued_date", "darb_exempted", "darb_review_date", "darb_approval_date",
    "sow_planned_date", "sow_issued_date", "approved_budget_value_usd", "cost_total_usd", "total_setup_revenue_usd",
    "recurring_revenue_annual_baseline_usd", "efforts_internal_mandays", "efforts_external_mandays",
    "efforts_saba_mandays", "intake_gate", "delivery_manager", "platform", "budget_type", "budget_code",
    "on_hold_date", "on_hold_reason", "created_on", "is_change_request", "cr_reason", "master_project_id",
]
# Activity tables (US-155), each linked to projects by project_id; loaded after projects.
ACTIVITY_COLUMNS = {
    "change_requests": ["change_request_id", "project_id", "project_name", "pcr_number", "title", "reason",
                        "change_type", "approval_status", "golive_change", "additional_cost_usd", "requested_on"],
    "status_reports": ["status_report_id", "project_id", "project_name", "title", "report_date", "submitted_to"],
    "risks": ["risk_id", "project_id", "project_name", "title", "category", "state", "likelihood", "consequence",
              "exposure", "cost_exposure_usd", "due_date", "owner"],
    "issues": ["issue_id", "project_id", "project_name", "title", "category", "priority", "status", "due_date",
               "owner"],
    "action_items": ["action_item_id", "project_id", "project_name", "title", "state", "due_date", "owner"],
    "decisions": ["decision_id", "project_id", "project_name", "decision", "status", "target_date"],
    "timesheets": ["timesheet_id", "project_id", "project_name", "submitter", "task_name", "status", "total_hours",
                   "created_on"],
    "project_tasks": ["task_id", "project_id", "project_name", "subject", "is_critical", "is_summary", "progress",
                      "start_date", "finish_date"],
}
PCR_REASONS = ["Initial Baseline", "Schedule Slippage - Client", "Additional Scope Requested by Client",
               "Dependency on Other Project", "Schedule Slippage - Vendor"]
PCR_APPROVALS = ["Fully Approved", "Fully Approved", "Delivery Manager Approved", "Approval In Progress",
                 "Requested By (RM) Rejected", "Not Yet Requested"]
RISK_CATEGORIES = ["Schedule", "Scope", "Vendor", "Cost"]
ACTION_STATES = ["Not Started", "In Progress", "Completed", "Waiting on someone else"]


def _d(days: int) -> date:
    return AS_OF + timedelta(days=days)


def _money(r: random.Random, lo: int, hi: int) -> Decimal:
    return Decimal(r.randrange(lo, hi, 1000))


def generate(seed: int = 42) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    r = random.Random(seed)
    requests: list[dict[str, Any]] = []
    for i in range(1, 61):
        state = REQUEST_STATES[i % len(REQUEST_STATES)]
        dou_required = "Yes" if i % 3 else "No"
        req = {
            "project_request_id": f"REQ-{i:03d}", "project_number": None, "title": f"Demand request {i}",
            "request_state": state, "cio": CIOS[i % len(CIOS)], "region": REGIONS[i % len(REGIONS)],
            "priority": PRIORITIES[i % len(PRIORITIES)],
            "stage1_discovery_planned_start_date": _d(-120 + i), "stage1_discovery_planned_end_date": _d(-60 + i),
            "stage1_discovery_actual_end_date": _d(-55 + i) if i % 2 else None,
            "dou_required": dou_required, "dou_planned_date": _d(r.choice([-20, -10, -1, 0, 5, 30])),
            "dou_issued_date": _d(-3) if i % 4 == 0 else None,
            "darb_exempted": "Yes" if i % 5 == 0 else "No", "darb_review_date": _d(r.choice([-15, -2, 0, 10])),
            "darb_approval_date": _d(-1) if i % 6 == 0 else None,
            "sow_planned_date": _d(r.choice([-30, -5, 0, 20])), "sow_issued_date": _d(-4) if i % 7 == 0 else None,
            "approved_budget_value_usd": _money(r, 100_000, 2_000_000), "cost_total_usd": _money(r, 90_000, 2_200_000),
            "total_setup_revenue_usd": _money(r, 50_000, 900_000),
            "recurring_revenue_annual_baseline_usd": _money(r, 10_000, 400_000),
            "efforts_internal_mandays": r.randrange(10, 400), "efforts_external_mandays": r.randrange(0, 300),
        }
        requests.append(req)
    # Boundary request: DOU planned 2026-09-20, required, not issued, open → overdue by 10 days (RULE-039 example).
    requests[0].update({"request_state": "Active", "dou_required": "Yes", "dou_planned_date": date(2026, 9, 20),
                        "dou_issued_date": None})
    # Boundary request: planned equal to asOf is not overdue.
    requests[1].update({"request_state": "New", "dou_required": "Yes", "dou_planned_date": AS_OF,
                        "dou_issued_date": None})

    # Status / current state plan matching S13: 13 Active, 3 On Hold, 49 Project Created, 35 inactive.
    states = ["Active"] * 13 + ["On Hold"] * 3 + ["Project Created"] * 49
    states += [INACTIVE_STATES[i % len(INACTIVE_STATES)] for i in range(35)]
    projects: list[dict[str, Any]] = []
    for i, state in enumerate(states, start=1):
        open_ = state in ("Active", "On Hold", "Project Created")
        planned = _d(r.randrange(-90, 200))
        baseline = planned - timedelta(days=r.choice([0, 0, 10, 30])) if r.random() < 0.8 else None
        deployed = None
        if not open_ and state == "Completed":
            deployed = planned + timedelta(days=r.randrange(-10, 40))
        budget = _money(r, 50_000, 3_000_000) if r.random() > 0.1 else None
        cost = (budget * Decimal(r.choice(["0.8", "0.95", "1.0", "1.05", "1.2", "1.6"]))).quantize(Decimal("1")) \
            if budget is not None and r.random() > 0.05 else None
        start = _d(-r.randrange(60, 400))
        p = {
            "project_id": f"P{i:04d}", "project_number": f"PRJ-{i:03d}", "name": f"Project {i}",
            "portfolio": PORTFOLIOS[i % len(PORTFOLIOS)], "program": f"Programme {i % 7 + 1}",
            "status": "Active" if open_ else "Inactive", "current_state": state,
            "lifecycle_phase": PHASES[i % len(PHASES)], "cio": CIOS[i % len(CIOS)], "region": REGIONS[i % len(REGIONS)],
            "delivery_manager": MANAGERS[i % len(MANAGERS)], "priority": PRIORITIES[i % len(PRIORITIES)],
            "platform": PLATFORMS[i % len(PLATFORMS)], "key_priority_project": "Yes" if i % 5 == 0 else "No",
            "percent_complete": float(r.randrange(0, 101)),
            "overall_kpi": KPIS[i % 3], "schedule_kpi": KPIS[(i + 1) % 3], "cost_kpi": KPIS[(i + 2) % 3],
            "issue_kpi": KPIS[i % 3],
            "planned_deployment_date": planned, "first_baselined_deployment_date": baseline,
            "actual_deployment_date": deployed, "actual_project_start_date": start,
            "on_hold_date": _d(-r.randrange(5, 120)) if state == "On Hold" else None,
            "approved_budget_value_usd": budget, "cost_total_usd": cost,
            "cost_internal_usd": (cost * Decimal("0.4")).quantize(Decimal("1")) if cost is not None else None,
            "cost_external_usd": (cost * Decimal("0.6")).quantize(Decimal("1")) if cost is not None else None,
            "approved_pcr_counter": r.randrange(0, 5),
            "project_request_id": f"REQ-{i:03d}" if i <= 60 and i % 2 == 0 else None,
            "modified_on": datetime.combine(_d(-r.randrange(0, 80)), datetime.min.time()) + timedelta(hours=10),
            "created_on": datetime.combine(start - timedelta(days=30), datetime.min.time()),
        }
        projects.append(p)

    active = [p for p in projects if p["current_state"] == "Active"]
    # RULE-028 boundaries: planned asOf - 1 (overdue), planned == asOf (not overdue).
    active[0].update({"planned_deployment_date": _d(-1), "actual_deployment_date": None})
    active[1].update({"planned_deployment_date": AS_OF, "actual_deployment_date": None})
    # RULE-030 (A): Green and planned exactly 15 days ago → flagged; 14 days → not flagged.
    active[2].update({"schedule_kpi": "Green", "planned_deployment_date": _d(-15)})
    active[3].update({"schedule_kpi": "Green", "planned_deployment_date": _d(-14)})
    # RULE-032 boundaries: day 30 → 0-30, day 31 → 31-60.
    active[4].update({"planned_deployment_date": _d(30)})
    active[5].update({"planned_deployment_date": _d(31)})
    # RULE-031 boundaries: modified 31 days ago (stale) vs exactly 30 days ago at midnight (not stale).
    active[6].update({"modified_on": datetime.combine(_d(-31), datetime.min.time())})
    active[7].update({"modified_on": datetime.combine(_d(-30), datetime.min.time())})
    # RULE-033 worked example → 50.0 Amber.
    active[8].update({
        "planned_deployment_date": date(2026, 12, 1), "first_baselined_deployment_date": date(2026, 11, 1),
        "actual_deployment_date": None, "approved_budget_value_usd": Decimal("100000"),
        "cost_total_usd": Decimal("110000"), "actual_project_start_date": date(2026, 1, 1),
        "percent_complete": 50.0, "issue_kpi": "Yellow", "schedule_kpi": "Yellow",
    })
    # RULE-035 override example: raw 75 (Green) but cost 60 % over → Red.
    active[9].update({
        "planned_deployment_date": date(2026, 12, 1), "first_baselined_deployment_date": date(2026, 12, 1),
        "actual_deployment_date": None, "approved_budget_value_usd": Decimal("100000"),
        "cost_total_usd": Decimal("160000"), "actual_project_start_date": date(2026, 1, 1),
        "percent_complete": 90.0, "issue_kpi": "Green", "schedule_kpi": "Green",
    })
    # Make sure the boundary projects all belong to the CIO persona used in scope tests.
    for p in active[:10]:
        p["cio"] = "Nomsa Osei"
    # RULE-036: zero budget is never over budget.
    projects[20].update({"approved_budget_value_usd": Decimal("0"), "cost_total_usd": Decimal("5000")})
    _catalogue_columns(projects, requests, random.Random(seed + 156))
    _screen_columns(projects, requests, random.Random(seed + 155))
    return projects, requests


def _catalogue_columns(projects: list[dict[str, Any]], requests: list[dict[str, Any]], r: random.Random) -> None:
    """US-156 columns and boundaries (own random stream; earlier values are untouched)."""
    for i, q in enumerate(requests, start=1):
        q.update({
            "efforts_saba_mandays": r.randrange(5, 60) if i % 3 else None, "intake_gate": GATES[i % len(GATES)],
            "delivery_manager": MANAGERS[i % len(MANAGERS)] if i % 4 else None,
            "platform": PLATFORMS[i % len(PLATFORMS)], "budget_type": "CAPEX" if i % 4 else "OPEX",
            "budget_code": f"BC-{1000 + i}" if i % 5 else None,
            "on_hold_date": None, "on_hold_reason": None,
            "created_on": datetime.combine(_d(-(220 - 3 * i)), datetime.min.time()) + timedelta(hours=9),
        })
        if i % 2:  # discovery finished: late, on time or early in turn
            q["stage1_discovery_actual_end_date"] = q["stage1_discovery_planned_end_date"] + timedelta(
                days=(5, 0, -3)[(i // 2) % 3])
    requests[2]["approved_budget_value_usd"] = None  # no budget yet
    requests[3].update({"efforts_internal_mandays": None, "efforts_external_mandays": None})  # no effort estimate
    on_hold = [q for q in requests if q["request_state"] == "On Hold"]
    for q, days in zip(on_hold, [181, 180, 200, 30, 95, 10, 61, 120, 5], strict=False):
        q["on_hold_date"], q["on_hold_reason"] = _d(-days), HOLD_REASONS[days % len(HOLD_REASONS)]
    completed = [p for p in projects if p["current_state"] == "Completed"]
    for p, req in zip(completed[:4], ["REQ-001", "REQ-003", "REQ-005", "REQ-007"], strict=True):
        p["project_request_id"] = req  # deployed projects with a request: lead time
    for i, p in enumerate(projects, start=1):
        cost = p["cost_total_usd"]
        p.update({
            "cost_contingency_usd": (cost * Decimal("0.05")).quantize(Decimal("1")) if cost is not None else None,
            "cost_hw_usd": Decimal(r.randrange(0, 200_000, 1000)) if cost is not None and i % 2 else None,
            "cost_sw_usd": Decimal(r.randrange(0, 300_000, 1000)) if cost is not None and i % 3 else None,
            "estimated_tcv_usd": Decimal(r.randrange(200_000, 5_000_000, 1000)) if i % 3 else None,
            "project_manager": MANAGERS[(i + 2) % len(MANAGERS)] if i % 11 else None,
            "budget_code": None if i % 9 == 0 else ("" if i == 10 else f"BC-{2000 + i}"),
            "budget_type": None if i % 13 == 0 else ("OPEX" if i % 4 == 0 else "CAPEX"),
            "on_hold_reason": None,
        })
        if p["project_request_id"] is not None:  # converted: the project is created after its request
            req = next(q for q in requests if q["project_request_id"] == p["project_request_id"])
            p["created_on"] = max(p["created_on"], req["created_on"] + timedelta(days=10 + i % 40))
    for p, days in zip([p for p in projects if p["current_state"] == "On Hold"], [91, 90, 20], strict=True):
        p["on_hold_date"], p["on_hold_reason"] = _d(-days), HOLD_REASONS[days % len(HOLD_REASONS)]
    active = [p for p in projects if p["current_state"] == "Active"]
    # Overdue 91 days (more than 90) and exactly 90 days (not more than 90).
    active[10].update({"planned_deployment_date": _d(-91), "actual_deployment_date": None})
    active[11].update({"planned_deployment_date": _d(-90), "actual_deployment_date": None})
    created = [p for p in projects if p["current_state"] == "Project Created"]
    created[5]["overall_kpi"] = None  # reported status not set: shown as Not set, out of RAG % denominators
    # Too little evidence for calculated health: only the schedule factor (35 %) → Insufficient data.
    created[6].update({"approved_budget_value_usd": None, "cost_total_usd": None, "percent_complete": None,
                       "issue_kpi": None, "cost_internal_usd": None, "cost_external_usd": None,
                       "cost_contingency_usd": None, "cost_hw_usd": None, "cost_sw_usd": None})
    # Deployed 61 days late (counted) and exactly 60 days late (not counted); one on time, deployed recently.
    completed[0]["actual_deployment_date"] = completed[0]["planned_deployment_date"] + timedelta(days=61)
    completed[1]["actual_deployment_date"] = completed[1]["planned_deployment_date"] + timedelta(days=60)
    completed[2].update({"planned_deployment_date": _d(-40), "first_baselined_deployment_date": _d(-50),
                         "actual_deployment_date": _d(-40)})


def _screen_columns(projects: list[dict[str, Any]], requests: list[dict[str, Any]], r: random.Random) -> None:
    """US-155 project and request columns: stage plan ends, closure approval, change-request fields."""
    for i, p in enumerate(projects, start=1):
        planned = p["planned_deployment_date"]
        has_plan = i % 6 != 0
        stages = [planned - timedelta(days=d) for d in (150, 120, 75, 35, 0)] if has_plan else [None] * 5
        completed = p["current_state"] == "Completed"
        p.update({
            "planning_stage_end_date": stages[0], "design_stage_end_date": stages[1], "build_stage_end_date": stages[2],
            "test_stage_end_date": stages[3], "implementation_stage_end_date": stages[4],
            "warranty_stage_end_date": planned + timedelta(days=30) if has_plan else None,
            "closure_approval_status": (("Approved", "Approved", "Approval In Progress", None)[i % 4] if completed
                                        else ("Approval In Progress" if i % 17 == 0 else None)),
            "closure_date": None,
        })
        if p["closure_approval_status"] == "Approved":
            p["closure_date"] = (p["actual_deployment_date"] or planned) + timedelta(days=r.randrange(20, 60))
    for i, q in enumerate(requests, start=1):
        cr = i % 7 == 0
        q.update({"is_change_request": "Yes" if cr else "No",
                  "cr_reason": ("Client", "Regulatory", None)[i % 3] if cr else None,
                  "master_project_id": f"P{i:04d}" if cr and i % 2 else None})


def generate_activity(projects: list[dict[str, Any]] | None = None, seed: int = 42) -> dict[str, list[dict[str, Any]]]:
    """The activity tables (US-155) for the generated projects: deterministic, own random stream."""
    if projects is None:
        projects = generate(seed)[0]
    r = random.Random(seed + 1550)
    open_ = [p for p in projects if p["status"] == "Active"]
    active = [p for p in projects if p["current_state"] == "Active"]
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ACTIVITY_COLUMNS}

    def base(p: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": p["project_id"], "project_name": p["name"]}

    for i, p in enumerate(open_[:30], start=1):  # PCRs: up to two per project
        for j in range(1 + i % 2):
            n = len(out["change_requests"]) + 1
            out["change_requests"].append({
                **base(p), "change_request_id": f"PCR-{n:03d}", "pcr_number": f"PCR-{1000 + n}",
                "title": f"Change {n} on {p['name']}", "reason": PCR_REASONS[(i + j) % len(PCR_REASONS)],
                "change_type": ("Initial Baseline" if (i + j) % len(PCR_REASONS) == 0
                                else ("Schedule", "Scope", "Cost")[n % 3]),
                "approval_status": PCR_APPROVALS[n % len(PCR_APPROVALS)],
                "golive_change": "Yes" if n % 3 == 0 else "No",
                "additional_cost_usd": _money(r, 0, 120_000) if n % 4 else None,
                "requested_on": _d(-r.randrange(5, 300))})
    for i, p in enumerate(active, start=1):  # status reports: active projects, some stale or never
        if i % 5 == 0:
            continue
        for k in range(i % 4 + 1):
            n = len(out["status_reports"]) + 1
            out["status_reports"].append({
                **base(p), "status_report_id": f"SR-{n:04d}", "title": f"Status {n}",
                "report_date": _d(-(7 + 21 * k + (i * 11) % 40)), "submitted_to": MANAGERS[(i + k) % len(MANAGERS)]})
    out["status_reports"][0]["report_date"] = _d(-30)  # exactly 30 days old: in the last 30 days
    out["status_reports"][1]["report_date"] = _d(-31)  # 31 days old: not
    for i, p in enumerate(open_[:25], start=1):  # risks, issues, actions, decisions
        for _ in range(i % 3):
            n = len(out["risks"]) + 1
            lik, con = r.randrange(1, 6), r.randrange(1, 6)
            out["risks"].append({
                **base(p), "risk_id": f"RSK-{n:03d}", "title": f"Risk {n}", "category": RISK_CATEGORIES[n % 4],
                "state": "Closed" if n % 6 == 0 else ("Active " if n % 5 == 0 else "Active"),
                "likelihood": lik, "consequence": con, "exposure": Decimal(lik * con),
                "cost_exposure_usd": _money(r, 1_000, 90_000) if n % 2 else None,
                "due_date": _d(r.randrange(-60, 90)) if n % 3 else None, "owner": MANAGERS[n % len(MANAGERS)]})
        if i % 2:
            n = len(out["issues"]) + 1
            out["issues"].append({
                **base(p), "issue_id": f"ISS-{n:03d}", "title": f"Issue {n}", "category": RISK_CATEGORIES[n % 4],
                "priority": ("High", "Medium", "Medium")[n % 3], "status": "Closed" if n % 5 == 0 else "Active",
                "due_date": _d(r.randrange(-40, 60)), "owner": MANAGERS[(n + 1) % len(MANAGERS)]})
        for _ in range(2):
            n = len(out["action_items"]) + 1
            out["action_items"].append({
                **base(p), "action_item_id": f"ACT-{n:03d}", "title": f"Action {n}",
                "state": ACTION_STATES[n % len(ACTION_STATES)],
                "due_date": _d(r.randrange(-50, 50)) if n % 4 else None, "owner": MANAGERS[n % len(MANAGERS)]})
        if i % 4 == 0:
            n = len(out["decisions"]) + 1
            out["decisions"].append({
                **base(p), "decision_id": f"DEC-{n:03d}", "decision": f"Decision {n}",
                "status": ("Active", "On Hold", "Completed", None)[n % 4], "target_date": _d(r.randrange(-20, 40))})
    acts = [a for a in out["action_items"] if a["state"] != "Completed"]
    acts[0]["due_date"] = _d(-1)  # open, due the day before the reporting date: overdue
    acts[1]["due_date"] = AS_OF  # due on the reporting date: not overdue
    for i, p in enumerate(active, start=1):  # timesheets
        for k in range(1 + i % 3):
            n = len(out["timesheets"]) + 1
            out["timesheets"].append({
                **base(p), "timesheet_id": f"TS-{n:04d}", "submitter": MANAGERS[(i + k) % len(MANAGERS)],
                "task_name": f"Task {k + 1}",
                "status": ("Approved", "Approved", "Submitted", "Draft", "Rejected")[n % 5],
                "total_hours": Decimal(r.randrange(4, 41)),
                "created_on": datetime.combine(_d(-r.randrange(1, 120)), datetime.min.time())})
    for i, p in enumerate(active[:9], start=1):  # project plans: a summary row and four tasks
        n0 = len(out["project_tasks"])
        out["project_tasks"].append({**base(p), "task_id": f"T-{n0 + 1:04d}", "subject": f"{p['name']} plan",
                                     "is_critical": "No", "is_summary": "Yes", "progress": Decimal(40),
                                     "start_date": _d(-100), "finish_date": _d(60)})
        for k in range(4):
            n = len(out["project_tasks"]) + 1
            out["project_tasks"].append({
                **base(p), "task_id": f"T-{n:04d}", "subject": f"Task {k + 1}", "is_critical": "Yes" if k % 2 else "No",
                "is_summary": "No", "progress": Decimal((100, 60, 20, 0)[(i + k) % 4]),
                "start_date": _d(-90 + 20 * k), "finish_date": _d(-30 + 25 * k)})
    crit = [t for t in out["project_tasks"] if t["is_critical"] == "Yes" and t["progress"] < 100]
    crit[0]["finish_date"] = _d(-1)  # critical, unfinished, due the day before: late
    crit[1]["finish_date"] = AS_OF  # due on the reporting date: not late
    return out


def _literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float | Decimal):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def tables(seed: int = 42) -> list[tuple[str, list[str], list[dict[str, Any]]]]:
    """(table, columns, rows) of the whole extract in load order."""
    projects, requests = generate(seed)
    activity = generate_activity(projects, seed)
    return [("project_requests", REQUEST_COLUMNS, requests), ("projects", PROJECT_COLUMNS, projects),
            *((t, cols, activity[t]) for t, cols in ACTIVITY_COLUMNS.items())]


def to_sql(dialect: str = "postgres") -> str:
    lines = [ddl(dialect)]
    for table, cols, rows in tables():
        for row in rows:
            lines.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                         f"({', '.join(_literal(row[c]) for c in cols)});")
    return "\n".join(lines) + "\n"


def load(conn: Any) -> None:
    """Load into an open psycopg connection (owner of the target schema)."""
    with conn.cursor() as cur:
        cur.execute(DDL)
        for table, cols, rows in tables():
            placeholders = ", ".join(["%s"] * len(cols))
            cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                            [tuple(row[c] for c in cols) for row in rows])


# ----------------------------------------------------------------------------- Dataverse (US-110, US-111)
# The extract as a Dataverse environment would hold it: logical names from db_scheme §18 (S15 §7) where they are
# documented, the standard statecode / statuscode choices for status, current state and request state (with the
# ``<choice>name`` label columns the TDS endpoint exposes), GUID primary keys, createdon / modifiedon. Fields with
# no documented logical name are left out, as an unknown real environment would leave them for the Admin.
DATAVERSE_TABLES = {
    "projects": ("tpg_project", {
        "project_id": "tpg_projectid", "project_number": "tpgmea_projectnumber", "name": "tpg_title",
        "cio": "tpgmea_customcio", "region": "tpgmea_region", "delivery_manager": "tpgmea_deliverymanager",
        "planned_deployment_date": "tpgmea_planneddeploymentdate",
        "first_baselined_deployment_date": "tpgmea_firstbaselineddate",
        "actual_deployment_date": "tpgmea_actualdeploymentdate",
        "approved_budget_value_usd": "tpgmea_approvedbudgetvaluesusd", "cost_total_usd": "tpgmea_costtotal_usd",
        "modified_on": "modifiedon", "created_on": "createdon"}),
    "project_requests": ("tpg_projectrequest", {
        "project_request_id": "tpg_projectrequestid", "cio": "tpgmea_customcio", "region": "tpgmea_region",
        "dou_required": "tpgmea_dourequired", "dou_planned_date": "tpgmea_planneddouissuancedate",
        "dou_issued_date": "tpgmea_dousubmissiondate", "darb_review_date": "tpgmea_darbreviewdate",
        "darb_approval_date": "tpgmea_darbapprovaltimestamp", "sow_planned_date": "tpgmea_tentativedateofsowissuance",
        "sow_issued_date": "tpgmea_sowissueddate"}),
}
STATE_CODES = {"Active": 0, "Inactive": 1}
STATUS_CODES = {  # synthetic status reasons (statuscode); labels are the extract's current state / request state
    "projects": {"Project Created": 1, "Active": 2, "On Hold": 3, "Completed": 4, "Dropped": 5, "Cancelled": 6,
                 "Archived": 7, "Expired": 8},
    "project_requests": {"New": 1, "Active": 2, "On Hold": 3, "Return to Requester": 4, "Approved": 5,
                         "Rejected": 6, "Dropped": 7},
}
DATAVERSE_TYPES = {"text": "nvarchar(255)", "date": "date", "datetime": "datetime2", "money": "money",
                   "guid": "uniqueidentifier", "int": "int"}


def _guid(value: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tela-synthetic/{value}"))


def dataverse_columns(table: str) -> list[tuple[str, str]]:
    """(logical name, kind) for one table, in TDS column order."""
    _, mapping = DATAVERSE_TABLES[table]
    sample = generate()[0 if table == "projects" else 1][0]
    out: list[tuple[str, str]] = []
    for src, logical in mapping.items():
        if logical.endswith("id") and src.endswith("_id"):
            kind = "guid"
        elif src.endswith(("_usd",)):
            kind = "money"
        elif src in ("modified_on", "created_on"):  # the reference schema types every other date field DATE
            kind = "datetime"
        elif isinstance(sample.get(src), date) or src.endswith("_date"):
            kind = "date"
        else:
            kind = "text"
        out.append((logical, kind))
    out += [("statecode", "int"), ("statecodename", "text"), ("statuscode", "int"), ("statuscodename", "text")]
    return out


def dataverse_rows(table: str) -> list[dict[str, Any]]:
    projects, requests = generate()
    rows = projects if table == "projects" else requests
    _, mapping = DATAVERSE_TABLES[table]
    out = []
    for r in rows:
        d: dict[str, Any] = {}
        for src, logical in mapping.items():
            v = r.get(src)
            d[logical] = _guid(v) if logical in ("tpg_projectid", "tpg_projectrequestid") and v else v
        if table == "projects":
            d["statecodename"] = r["status"]
            d["statuscodename"] = r["current_state"]
        else:
            d["statecodename"] = "Active" if r["request_state"] not in ("Rejected", "Dropped") else "Inactive"
            d["statuscodename"] = r["request_state"]
        d["statecode"] = STATE_CODES[d["statecodename"]]
        d["statuscode"] = STATUS_CODES[table][d["statuscodename"]]
        out.append(d)
    return out


def to_dataverse_sql() -> str:
    """T-SQL for a database standing in for the Dataverse TDS endpoint (dbo.tpg_project, dbo.tpg_projectrequest)."""
    lines = []
    for table in ("projects", "project_requests"):
        name, _ = DATAVERSE_TABLES[table]
        cols = dataverse_columns(table)
        key = cols[0][0]
        lines.append(f"DROP TABLE IF EXISTS dbo.{name};")
        body = ", ".join(f"{c} {DATAVERSE_TYPES[k]}{' NOT NULL PRIMARY KEY' if c == key else ''}" for c, k in cols)
        lines.append(f"CREATE TABLE dbo.{name} ({body});")
        for row in dataverse_rows(table):
            lines.append(f"INSERT INTO dbo.{name} ({', '.join(c for c, _ in cols)}) VALUES "
                         f"({', '.join(_literal(row.get(c)) for c, _ in cols)});")
    return "\n".join(lines) + "\n"


def to_mongosh() -> str:
    """A mongosh script loading the extract as documents (US-112): ISODate dates, NumberDecimal money, NULLs as
    missing fields, plus a ``team`` sub-document and a ``milestones`` array the PMO pack does not use."""
    import json

    def js(v: Any) -> str:
        if isinstance(v, datetime):
            return f'ISODate("{v.isoformat()}Z")'
        if isinstance(v, date):
            return f'ISODate("{v.isoformat()}T00:00:00Z")'
        if isinstance(v, Decimal):
            return f'NumberDecimal("{v}")'
        return json.dumps(v)

    projects, requests = generate()
    out = []
    for name, cols, rows in (("project_requests", REQUEST_COLUMNS, requests), ("projects", PROJECT_COLUMNS, projects)):
        docs = []
        for i, r in enumerate(rows):
            fields = [f"{json.dumps(c)}: {js(r[c])}" for c in cols if r[c] is not None]
            if name == "projects":
                fields.append(f'"team": {{"sponsor": "Sponsor {i % 5}", "size": {3 + i % 7}}}')
                planned = js(r["planned_deployment_date"]) if r["planned_deployment_date"] else "null"
                start = js(r["actual_project_start_date"]) if r["actual_project_start_date"] else "null"
                done = "true" if r["actual_deployment_date"] else "false"
                fields.append(f'"milestones": [{{"name": "Kick-off", "planned": {start}, "done": true}}, '
                              f'{{"name": "Go-live", "planned": {planned}, "done": {done}}}]')
            docs.append("{" + ", ".join(fields) + "}")
        out.append(f"db.{name}.drop();\ndb.{name}.insertMany([\n  " + ",\n  ".join(docs) + "\n]);")
    out.append('db.projects.createIndex({"project_id": 1});')
    out.append('db.project_requests.createIndex({"project_request_id": 1});')
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", required=True, help="write INSERT statements to this file")
    parser.add_argument("--dialect", default="postgres", choices=(*DIALECTS, "mongosh", "dataverse"))
    args = parser.parse_args()
    out = Path(args.sql)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = {"mongosh": to_mongosh, "dataverse": to_dataverse_sql}.get(args.dialect)
    out.write_text(text() if text else to_sql(args.dialect))
    print(f"wrote {out}")
