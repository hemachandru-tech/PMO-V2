"""Independent Python implementations of RULE-026 - RULE-039 over the synthetic fixture (TEST-060 oracle).

Written from business_rules.md, separately from the pack manifest, so the query engine's results are
checked against a second implementation rather than against themselves.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import tpg_synthetic

AS_OF: date = tpg_synthetic.AS_OF
PROJECTS, REQUESTS = tpg_synthetic.generate()
ACTIVITY = tpg_synthetic.generate_activity(PROJECTS)
OPEN_REQUEST = {"New", "Active", "On Hold", "Return to Requester"}


OPEN_STATES = {"Project Created", "Active", "On Hold"}  # RULE-026, DEC-24


def _open(p: dict[str, Any]) -> bool:
    return p["current_state"] in OPEN_STATES


def open_by_status(node: Any) -> Any:
    """Measure definitions with RULE-026 as pack 2.1.0 had it, open = Status Active (DEC-24 moved it to the current
    state): a rule that reads another field than the states a question lists (US-203 to US-205 tests)."""
    if isinstance(node, dict):
        if node.get("field") == "current_state" and node.get("values") == ["Project Created", "Active", "On Hold"]:
            return {"op": "=", "field": "status", "value": "Active" if node["op"] == "in" else "Inactive"}
        return {k: open_by_status(v) for k, v in node.items()}
    return [open_by_status(v) for v in node] if isinstance(node, list) else node


def _comparable(p: dict[str, Any]) -> bool:
    b, c = p["approved_budget_value_usd"], p["cost_total_usd"]
    return _open(p) and b is not None and b > 0 and c is not None and c >= 0


def _days_since(ts: datetime) -> float:
    return (datetime.combine(AS_OF, datetime.min.time()) - ts).total_seconds() / 86400


RULES: dict[str, Any] = {
    "total_projects": lambda p: True,
    "open_projects": _open,
    "inactive_projects": lambda p: p["current_state"] is not None and not _open(p),
    "in_delivery": lambda p: p["current_state"] == "Active",
    "on_hold": lambda p: _open(p) and p["current_state"] == "On Hold",
    "project_created": lambda p: _open(p) and p["current_state"] == "Project Created",
    "overdue_go_lives": lambda p: p["current_state"] == "Active" and p["planned_deployment_date"] < AS_OF
    and p["actual_deployment_date"] is None,
    "schedule_risk": lambda p: _open(p) and p["schedule_kpi"] in ("Red", "Yellow"),
    "rag_contradictions": lambda p: _open(p) and p["actual_deployment_date"] is None and p[
        "planned_deployment_date"] is not None and (
        (p["schedule_kpi"] == "Green" and (AS_OF - p["planned_deployment_date"]).days >= 15)
        or (p["schedule_kpi"] == "Red" and (p["planned_deployment_date"] - AS_OF).days >= 30)),
    "stale_projects": lambda p: p["current_state"] == "Active" and _days_since(p["modified_on"]) > 30,
    "go_lives_0_30": lambda p: _open(p) and p["actual_deployment_date"] is None
    and AS_OF <= p["planned_deployment_date"] <= AS_OF + timedelta(days=30),
    "go_lives_31_60": lambda p: _open(p) and p["actual_deployment_date"] is None
    and AS_OF + timedelta(days=30) < p["planned_deployment_date"] <= AS_OF + timedelta(days=60),
    "go_lives_61_90": lambda p: _open(p) and p["actual_deployment_date"] is None
    and AS_OF + timedelta(days=60) < p["planned_deployment_date"] <= AS_OF + timedelta(days=90),
    "over_budget": lambda p: p["approved_budget_value_usd"] is not None and p["approved_budget_value_usd"] > 0
    and p["cost_total_usd"] is not None and p["cost_total_usd"] > p["approved_budget_value_usd"],
}

REQUEST_RULES: dict[str, Any] = {
    "total_requests": lambda r: True,
    "open_demand": lambda r: r["request_state"] in OPEN_REQUEST,
    "overdue_dou": lambda r: r["request_state"] in OPEN_REQUEST and r["dou_required"] == "Yes"
    and r["dou_planned_date"] < AS_OF and r["dou_issued_date"] is None,
    "overdue_darb": lambda r: r["request_state"] in OPEN_REQUEST and r["darb_exempted"] == "No"
    and r["darb_review_date"] < AS_OF and r["darb_approval_date"] is None,
    "overdue_sow": lambda r: r["request_state"] in OPEN_REQUEST and r["sow_planned_date"] < AS_OF
    and r["sow_issued_date"] is None,
}


def count(measure: str, *, cio: str | None = None, regions: set[str] | None = None) -> int:
    rows, rule = (PROJECTS, RULES.get(measure)) if measure in RULES else (REQUESTS, REQUEST_RULES[measure])
    return sum(1 for r in rows if rule(r) and (cio is None or r["cio"] == cio)
               and (regions is None or r["region"] in regions))


def by(measure: str, field: str, *, cio: str | None = None) -> dict[Any, int]:
    rows, rule = (PROJECTS, RULES.get(measure)) if measure in RULES else (REQUESTS, REQUEST_RULES[measure])
    out: dict[Any, int] = {}
    for r in rows:
        if (cio is None or r["cio"] == cio):
            out.setdefault(r[field], 0)
            if rule(r):
                out[r[field]] += 1
    return {k: v for k, v in out.items() if v or True}


def finance() -> dict[str, Any]:
    k = [p for p in PROJECTS if _comparable(p)]
    approved = sum((p["approved_budget_value_usd"] for p in k), Decimal(0))
    forecast = sum((p["cost_total_usd"] for p in k), Decimal(0))
    open_n = sum(1 for p in PROJECTS if _open(p))
    return {"approved": approved, "forecast": forecast, "variance": forecast - approved,
            "variance_pct": (forecast - approved) / approved * 100 if approved else None,
            "excluded": open_n - len(k)}


# ----------------------------------------------------------------------------- US-156 catalogue measures
# Written from the report catalogue (§2, §3), independently of the manifest's expressions. Calculated health
# comes from the pack's Python calculator (pmo_health.calculate), so the SQL calculator is checked against it.

PHASES_BEFORE_BASELINE = {"Assign PM", "TRB Review", "Initiation", "Planning"}


def _pctl(values: list[float], q: float) -> float | None:
    """Continuous percentile (linear interpolation), as SQL PERCENTILE_CONT."""
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(a: float, b: float) -> float | None:
    return a / b * 100 if b else None


def health(p: dict[str, Any]) -> Any:
    from tela_api.packs.pmo_health import calculate

    return calculate({k: (float(v) if isinstance(v, Decimal) else v) for k, v in p.items()}, AS_OF)


def _catalogue() -> dict[str, Any]:
    P, R = PROJECTS, REQUESTS
    open_ = [p for p in P if _open(p)]
    req_by_id = {r["project_request_id"]: r for r in R}
    kpi_known = [p for p in open_ if p["overall_kpi"] in ("Green", "Yellow", "Amber", "Red")]
    statuses = [(p, health(p)) for p in open_]
    slips = [float((p["planned_deployment_date"] - p["first_baselined_deployment_date"]).days) for p in open_
             if p["planned_deployment_date"] and p["first_baselined_deployment_date"]]
    overdue = [p for p in P if RULES["overdue_go_lives"](p)]
    deployed = [p for p in P if p["actual_deployment_date"] is not None]
    variance = [float((p["actual_deployment_date"] - p["planned_deployment_date"]).days) for p in deployed]
    held = [p for p in open_ if p["current_state"] == "On Hold"]
    hold_days = [float((AS_OF - p["on_hold_date"]).days) for p in held if p["on_hold_date"]]
    past_planning = [p for p in open_ if p["lifecycle_phase"] and p["lifecycle_phase"] not in PHASES_BEFORE_BASELINE]
    in_delivery = [p for p in P if p["current_state"] == "Active"]
    funded = [p for p in P if (p["approved_budget_value_usd"] or 0) > 0 and p["cost_total_usd"] is not None]

    def in_12m(d: Any) -> bool:
        return d is not None and AS_OF - timedelta(days=365) < d <= AS_OF

    d12 = [p for p in P if in_12m(p["actual_deployment_date"])]
    d12b = [p for p in d12 if p["first_baselined_deployment_date"] is not None]
    on_time_tcv = sum((p["estimated_tcv_usd"] or 0) for p in d12b
                      if (p["actual_deployment_date"] - p["first_baselined_deployment_date"]).days <= 30)
    all_tcv = sum((p["estimated_tcv_usd"] or 0) for p in d12b if p["estimated_tcv_usd"] is not None)
    lead = [float((p["actual_deployment_date"] - req_by_id[p["project_request_id"]]["created_on"].date()).days)
            for p in deployed if p["project_request_id"]]
    open_r = [r for r in R if r["request_state"] in OPEN_REQUEST]
    held_r = [r for r in R if r["request_state"] == "On Hold"]
    converted = {p["project_request_id"] for p in P if p["project_request_id"]}
    convert_days = [float((p["created_on"].date() - req_by_id[p["project_request_id"]]["created_on"].date()).days)
                    for p in P if p["project_request_id"]]
    disc = [r for r in R if r["stage1_discovery_actual_end_date"] and r["stage1_discovery_planned_end_date"]]

    def total(rows: list[dict[str, Any]], key: str) -> Any:
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) if vals else None

    return {
        "reported_green_pct": _ratio(sum(p["overall_kpi"] == "Green" for p in kpi_known), len(kpi_known)),
        "reported_amber_pct": _ratio(sum(p["overall_kpi"] in ("Yellow", "Amber") for p in kpi_known), len(kpi_known)),
        "reported_red_pct": _ratio(sum(p["overall_kpi"] == "Red" for p in kpi_known), len(kpi_known)),
        "status_not_set": len(open_) - len(kpi_known),
        "avg_health_score": _mean([h.score for _, h in statuses if h.score is not None]),
        "health_green": sum(h.status == "Green" for _, h in statuses),
        "health_amber": sum(h.status == "Amber" for _, h in statuses),
        "health_red": sum(h.status == "Red" for _, h in statuses),
        "health_insufficient": sum(h.status == "Insufficient data" for _, h in statuses),
        "key_priorities_attention": sum(p["key_priority_project"] == "Yes" and h.status in ("Red", "Amber")
                                        for p, h in statuses),
        "slip_vs_baseline_median": _pctl(slips, 0.5),
        "slip_vs_baseline_avg": _mean(slips),
        "slip_vs_baseline_p85": _pctl(slips, 0.85),
        "overdue_over_90": sum((AS_OF - p["planned_deployment_date"]).days > 90 for p in overdue),
        "most_overdue_days": max((AS_OF - p["planned_deployment_date"]).days for p in overdue),
        "deployed_projects": len(deployed),
        "deployed_on_time": sum(p["actual_deployment_date"] <= p["planned_deployment_date"] for p in deployed),
        "median_schedule_variance": _pctl(variance, 0.5),
        "deployed_over_60_late": sum(v > 60 for v in variance),
        "on_hold_over_90": sum(d > 90 for d in hold_days),
        "longest_on_hold_days": max(hold_days),
        "avg_on_hold_days": _mean(hold_days),
        "baselining_in_scope": len(past_planning),
        "baselined": sum(p["first_baselined_deployment_date"] is not None for p in past_planning),
        "baselining_compliance_pct": _ratio(sum(p["first_baselined_deployment_date"] is not None
                                                for p in past_planning), len(past_planning)),
        "budget_code_set": sum(bool(p["budget_code"]) for p in P),
        "budget_code_compliance_pct": _ratio(sum(bool(p["budget_code"]) for p in P), len(P)),
        "pm_assigned": sum(p["delivery_manager"] is not None for p in in_delivery),
        "pm_assignment_compliance_pct": _ratio(sum(p["delivery_manager"] is not None for p in in_delivery),
                                               len(in_delivery)),
        "forecast_over_budget_15": sum(p["cost_total_usd"] / p["approved_budget_value_usd"] > Decimal("1.15")
                                       for p in funded),
        "net_forecast_variance": sum(p["cost_total_usd"] - p["approved_budget_value_usd"] for p in funded),
        "cost_internal_total": total(P, "cost_internal_usd"),
        "cost_external_total": total(P, "cost_external_usd"),
        "cost_contingency_total": total(P, "cost_contingency_usd"),
        "cost_hw_total": total(P, "cost_hw_usd"),
        "cost_sw_total": total(P, "cost_sw_usd"),
        "north_star_on_time_tcv_pct": _ratio(on_time_tcv, all_tcv),
        "lead_time_median": _pctl(lead, 0.5),
        "lead_time_p85": _pctl(lead, 0.85),
        "throughput_go_lives_month": len(d12) / 12,
        "project_starts_month": sum(in_12m(p["actual_project_start_date"]) for p in P) / 12,
        "months_to_clear_wip": len(open_) * 12 / len(d12) if d12 else None,
        "overdue_gates_total": count("overdue_dou") + count("overdue_darb") + count("overdue_sow"),
        "requests_on_hold_over_180": sum(r["on_hold_date"] is not None and (AS_OF - r["on_hold_date"]).days > 180
                                         for r in held_r),
        "request_longest_on_hold_days": max((AS_OF - r["on_hold_date"]).days for r in held_r if r["on_hold_date"]),
        "oldest_open_request_days": max((AS_OF - r["created_on"].date()).days for r in open_r),
        "estimated_cost_total": total(R, "cost_total_usd"),
        "request_budget_total": total(R, "approved_budget_value_usd"),
        "requests_no_budget": sum((r["approved_budget_value_usd"] or 0) <= 0 for r in R),
        "approved_for_delivery": sum(r["request_state"] == "Approved" for r in R),
        "rejected_or_dropped": sum(r["request_state"] in ("Rejected", "Dropped") for r in R),
        "converted_requests": len(converted),
        "conversion_rate": _ratio(len(converted), len(R)),
        "avg_days_to_convert": _mean(convert_days),
        "discovery_completed": len(disc),
        "discovery_late": sum(r["stage1_discovery_actual_end_date"] > r["stage1_discovery_planned_end_date"]
                              for r in disc),
        "discovery_early": sum(r["stage1_discovery_actual_end_date"] < r["stage1_discovery_planned_end_date"]
                               for r in disc),
        "discovery_on_time": sum(r["stage1_discovery_actual_end_date"] == r["stage1_discovery_planned_end_date"]
                                 for r in disc),
        "effort_internal_md": total(R, "efforts_internal_mandays"),
        "effort_external_md": total(R, "efforts_external_mandays"),
        "effort_saba_md": total(R, "efforts_saba_mandays"),
        "requests_no_effort_estimate": sum(r["efforts_internal_mandays"] is None
                                           and r["efforts_external_mandays"] is None for r in R),
    }


def _screens() -> dict[str, Any]:
    """US-155: the measures of the screens beyond projects and requests, written from the rules in task.md."""
    P, R, A = PROJECTS, REQUESTS, ACTIVITY
    open_ = [p for p in P if _open(p)]
    stages = ("planning", "design", "build", "test", "implementation", "warranty")

    def passed(stage: str) -> int:
        d = f"{stage}_stage_end_date"
        return sum(p[d] is not None and p[d] < AS_OF for p in open_)

    completed = [p for p in P if p["current_state"] == "Completed"]
    closed_ok = [p for p in completed if p["closure_approval_status"] == "Approved"]
    active_ids = {p["project_id"] for p in P if p["current_state"] == "Active"}
    reported = {s["project_id"] for s in A["status_reports"] if s["project_id"] in active_ids
                and AS_OF - timedelta(days=30) <= s["report_date"] <= AS_OF}
    srs = A["status_reports"]
    pcrs = A["change_requests"]
    pending = {"Not Yet Requested", "Approval In Progress", "Requested By (RM) Approved", "Delivery Manager Approved"}
    risks = [x for x in A["risks"] if x["state"] != "Closed"]
    issues = [x for x in A["issues"] if x["status"] != "Closed"]
    actions = [x for x in A["action_items"] if x["state"] != "Completed"]
    logged = [t for t in A["timesheets"] if t["status"] in ("Approved", "Submitted")]
    tasks = [t for t in A["project_tasks"] if t["is_summary"] != "Yes"]
    crit = [t for t in tasks if t["is_critical"] == "Yes" and (t["progress"] is None or t["progress"] < 100)]
    open_r = [r for r in R if r["request_state"] in OPEN_REQUEST]
    with_code = [r for r in open_r if r["budget_code"]]
    with_dm = [r for r in open_r if r["delivery_manager"] is not None]
    crs = [r for r in R if r["is_change_request"] == "Yes"]

    def hours(rows: list[dict[str, Any]]) -> Any:
        return sum(t["total_hours"] for t in rows) if rows else None

    return {
        "tcv_open_total": sum(p["estimated_tcv_usd"] for p in open_ if p["estimated_tcv_usd"] is not None),
        "staged_open_projects": sum(any(p[f"{s}_stage_end_date"] is not None for s in stages) for p in open_),
        **{f"{s}_end_passed": passed(s) for s in stages},
        "completed_projects": len(completed),
        "closure_approved": len(closed_ok),
        "closure_compliance_pct": _ratio(len(closed_ok), len(completed)),
        "closure_in_approval": sum(p["closure_approval_status"] == "Approval In Progress" for p in P),
        "closure_overdue": sum(p["closure_approval_status"] != "Approved" and p["warranty_stage_end_date"] is not None
                               and p["warranty_stage_end_date"] < AS_OF for p in completed),
        "status_reporting_scope": len(active_ids),
        "reported_last_30d": len(reported),
        "status_reporting_compliance_pct": _ratio(len(reported), len(active_ids)),
        "status_reports_30d": sum(AS_OF - timedelta(days=30) <= s["report_date"] <= AS_OF for s in srs),
        "status_reports_total": len(srs),
        "days_since_latest_report": min((AS_OF - s["report_date"]).days for s in srs if s["report_date"] <= AS_OF),
        "total_pcrs": len(pcrs),
        "pcrs_approved": sum(c["approval_status"] == "Fully Approved" for c in pcrs),
        "pcrs_pending": sum(c["approval_status"] in pending for c in pcrs),
        "pcrs_rejected": sum(c["approval_status"] == "Requested By (RM) Rejected" for c in pcrs),
        "pcr_approval_pct": _ratio(sum(c["approval_status"] == "Fully Approved" for c in pcrs), len(pcrs)),
        "pcrs_golive_change": sum(c["golive_change"] == "Yes" for c in pcrs),
        "pcr_additional_cost": sum(c["additional_cost_usd"] for c in pcrs if c["additional_cost_usd"] is not None),
        "projects_with_pcr": len({c["project_id"] for c in pcrs}),
        "open_risks": len(risks),
        "high_exposure_risks": sum(x["exposure"] >= 15 for x in risks),
        "risk_cost_exposure": sum(x["cost_exposure_usd"] for x in risks if x["cost_exposure_usd"] is not None),
        "overdue_risks": sum(x["due_date"] is not None and x["due_date"] < AS_OF for x in risks),
        "open_issues": len(issues),
        "high_priority_issues": sum(x["priority"] in ("High", "Critical") for x in issues),
        "overdue_issues": sum(x["due_date"] is not None and x["due_date"] < AS_OF for x in issues),
        "open_actions": len(actions),
        "overdue_actions": sum(x["due_date"] is not None and x["due_date"] < AS_OF for x in actions),
        "open_decisions": sum(x["status"] != "Completed" for x in A["decisions"]),
        "hours_logged": hours(logged),
        "hours_approved": hours([t for t in logged if t["status"] == "Approved"]),
        "hours_awaiting_approval": hours([t for t in logged if t["status"] == "Submitted"]),
        "hours_all": hours(A["timesheets"]),
        "people_logging": len({t["submitter"] for t in logged}),
        "projects_with_time": len({t["project_id"] for t in logged}),
        "projects_with_tasks": len({t["project_id"] for t in A["project_tasks"]}),
        "plan_tasks": len(tasks),
        "critical_open_tasks": len(crit),
        "critical_tasks_late": sum(t["finish_date"] < AS_OF for t in crit),
        "open_requests_with_code": len(with_code),
        "request_budget_code_pct": _ratio(len(with_code), len(open_r)),
        "open_requests_with_dm": len(with_dm),
        "request_pm_assignment_pct": _ratio(len(with_dm), len(open_r)),
        "change_request_count": len(crs),
        "crs_with_master_project": sum(r["master_project_id"] is not None for r in crs),
        "cr_share_pct": _ratio(len(crs), len(R)),
    }


CATALOGUE: dict[str, Any] = {**_catalogue(), **_screens()}
