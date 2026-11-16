"""Ground truth for the 12 questions against MySQL `tpg` (Dataverse names). Data date 2026-10-25."""
import json
import sys

import pymysql

D = "2026-10-25"
c = pymysql.connect(host="127.0.0.1", port=3306, user="root", password="1234", database="tpg")
cur = c.cursor()

# open project (Part A): not Completed/Dropped/On Hold; blank counts as open. Scope: not Timesheets portfolio.
OPEN_P = ("(p.tpg_projectstatusname IS NULL OR p.tpg_projectstatusname NOT IN ('Completed','Dropped','On Hold')) "
          "AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname <> 'Timesheets')")
OPEN_R = "(r.tpg_requeststatename IS NULL OR r.tpg_requeststatename NOT IN ('Approved','Dropped','On Hold','Return to Requester'))"

Q = {}

Q[1] = ("How many active demand requests currently require resource allocation?",
        "SELECT COUNT(*) AS active_requests FROM tpg_projectrequests WHERE tpg_requeststatename='Active'")

Q[2] = ("Register: On-Hold projects, reasons and comments",
        """SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, p.tpg_projectstatusname AS state,
                  COALESCE(p.tpgmea_evwronholdreasonname,'(Blank)') AS hold_reason,
                  DATE(p.tpgmea_projectputonholddate) AS hold_date,
                  DATEDIFF('2026-10-25', DATE(p.tpgmea_projectputonholddate)) AS days_on_hold
           FROM tpg_project p WHERE p.tpg_projectstatusname='On Hold' ORDER BY p.tpg_subject""")

Q[3] = ("Count of active projects (Regional Portfolio) - open per Part A",
        f"SELECT COUNT(*) AS open_projects, SUM(p.tpg_projectstatusname='Active') AS strictly_active FROM tpg_project p WHERE {OPEN_P}")

Q[4] = ("Open projects by CIO",
        f"""SELECT COALESCE(p.tpgmea_customcioname,'(not set)') AS cio, COUNT(*) AS projects
            FROM tpg_project p WHERE {OPEN_P} GROUP BY 1 ORDER BY (cio='(not set)'), projects DESC, cio""")

Q[5] = ("Register of open projects with RAG (Yellow shown as Amber)",
        f"""SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no,
                   REPLACE(COALESCE(p.tpg_overallkpiname,'(Blank)'),'Yellow','Amber') AS overall,
                   REPLACE(COALESCE(p.tpg_schedulekpiname,'(Blank)'),'Yellow','Amber') AS schedule,
                   REPLACE(COALESCE(p.tpg_costkpiname,'(Blank)'),'Yellow','Amber') AS cost,
                   REPLACE(COALESCE(p.tpg_issuekpiname,'(Blank)'),'Yellow','Amber') AS issue
            FROM tpg_project p WHERE {OPEN_P} ORDER BY p.tpg_subject""")

Q[6] = ("Open projects ranked by change requests awaiting approval",
        f"""SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, COUNT(*) AS awaiting_pcrs
            FROM tpg_change ch JOIN tpg_project p ON ch.tpg_project=p.tpg_projectid
            WHERE {OPEN_P}
              AND ch.tpg_approvalname NOT IN ('Fully Approved','Delivery Manager Rejected','Requested By (RM) Rejected')
              AND COALESCE(ch.tpg_changetypename,'')<>'Initial Baseline' AND COALESCE(ch.tpgmea_reasonforpcrname,'')<>'Initial Baseline'
            GROUP BY p.tpg_projectid, p.tpg_subject, p.tpgmea_pprojectnumber ORDER BY awaiting_pcrs DESC, project""")

Q[7] = ("Active projects by days since last modified",
        f"""SELECT CASE WHEN d<=7 THEN '1: 0-7 days' WHEN d<=30 THEN '2: 8-30' WHEN d<=60 THEN '3: 31-60'
                        WHEN d<=120 THEN '4: 61-120' ELSE '5: 120+' END AS bucket, COUNT(*) AS projects
            FROM (SELECT DATEDIFF('{D}', DATE(p.modifiedon)) AS d FROM tpg_project p
                  WHERE p.tpg_projectstatusname='Active' AND (p.tpg_portfolioname IS NULL OR p.tpg_portfolioname<>'Timesheets')) t
            GROUP BY 1 ORDER BY 1""")

Q[8] = ("Journey legs: open projects traced to a request",
        f"""SELECT p.tpg_subject AS project, p.tpgmea_pprojectnumber AS project_no, r.tpg_title AS request,
                   DATEDIFF(DATE(p.createdon), DATE(r.tpgmea_clientrequestdate)) AS ask_to_project_created_days,
                   DATEDIFF(DATE(p.createdon), DATE(r.tpgmea_darbapprovaltimestamp)) AS darb_to_project_created_days,
                   DATEDIFF(DATE(p.tpgmea_firstbaselinedpdd), DATE(p.createdon)) AS created_to_first_baseline_days
            FROM tpg_project p JOIN tpg_projectrequests r ON p.tpg_projectrequest=r.tpg_projectrequestsid
            WHERE {OPEN_P} ORDER BY p.tpg_subject""")

Q[9] = ("Top 20 open requests: Strategic objective or Critical/High priority, then money",
        f"""SELECT r.tpg_title AS request, r.tpgmea_projectnumber AS request_no, r.tpg_requeststatename AS state,
                   r.tpg_priorityname AS priority, r.tpgmea_projectobjectivename AS objective,
                   r.tpgmea_recurringrevenueannualbaselineusd AS revenue, r.tpgmea_costtotal_usd AS cost
            FROM tpg_projectrequests r WHERE {OPEN_R}
              AND (r.tpgmea_projectobjectivename='Strategic' OR r.tpg_priorityname IN ('Critical','High'))
            ORDER BY (r.tpgmea_projectobjectivename='Strategic' AND r.tpg_priorityname IN ('Critical','High')) DESC,
                     FIELD(r.tpg_priorityname,'Critical','High') , COALESCE(r.tpgmea_recurringrevenueannualbaselineusd,0) DESC""")

Q[10] = ("Open projects due in next 30/60/90 days (not deployed, planned deploy date in window)",
         f"""SELECT SUM(dd<=30) AS due_30, SUM(dd<=60) AS due_60, SUM(dd<=90) AS due_90, COUNT(*) AS open_projects_total
             FROM (SELECT DATEDIFF(DATE(p.tpgmea_productdeploydate),'{D}') AS dd FROM tpg_project p
                   WHERE {OPEN_P} AND p.tpgmea_projectimpdate IS NULL AND p.tpgmea_productdeploydate IS NOT NULL
                     AND DATE(p.tpgmea_productdeploydate)>='{D}') t
             RIGHT JOIN (SELECT 1) x ON 1=1""")

Q[11] = ("Critical path: open projects with a critical task / critical-or-high priority",
         f"""SELECT (SELECT COUNT(DISTINCT t.tpg_project) FROM tpg_projecttask t JOIN tpg_project p ON t.tpg_project=p.tpg_projectid
                     WHERE {OPEN_P} AND t.tpg_iscriticalname='Yes') AS open_projects_with_critical_task,
                    (SELECT COUNT(*) FROM tpg_project p WHERE {OPEN_P} AND p.tpgmea_priorityname IN ('Critical','High')) AS open_critical_or_high_priority,
                    (SELECT COUNT(*) FROM tpg_project p WHERE {OPEN_P}) AS open_projects""")

Q[12] = ("Open requests past a planned gate date (Part A: HLE, DOU, SOW; Part B: DOU, DARB, SOW)",
         f"""SELECT COUNT(*) AS requests_open,
                    SUM(hle OR dou OR sow) AS partA_requests_with_overdue_gate,
                    SUM(hle)+SUM(dou)+SUM(sow) AS partA_gate_rows, SUM(hle) hle, SUM(dou) dou, SUM(sow) sow,
                    SUM(dou OR darb OR sow) AS partB_requests_with_overdue_gate, SUM(darb) darb
             FROM (SELECT
                 (r.tpgmea_hlerequiredname='Yes' AND DATE(r.tpgmea_planneddatetoissuehle)<'{D}' AND r.tpgmea_highleveleffortsprovideddate IS NULL) AS hle,
                 (r.tpgmea_dourequiredname='Yes' AND DATE(r.tpgmea_planneddouissuancedate)<'{D}' AND r.tpgmea_dousubmissiondate IS NULL) AS dou,
                 (DATE(r.tpgmea_tentativedateofsowissuance)<'{D}' AND r.tpgmea_sowissueddate IS NULL) AS sow,
                 (COALESCE(r.tpgmea_darbexemptedname,'')<>'Yes' AND DATE(r.tpgmea_darbreviewdate)<'{D}' AND r.tpgmea_darbapprovaltimestamp IS NULL) AS darb
               FROM tpg_projectrequests r WHERE {OPEN_R}) t""")

out = {}
for k, (label, sql) in Q.items():
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [[(str(v) if v is not None else None) if not isinstance(v, (int,)) else v for v in r] for r in cur.fetchall()]
    out[k] = {"label": label, "sql": " ".join(sql.split()), "cols": cols, "rows": rows}
json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "gt.json", "w"), indent=1, default=str)
for k, v in out.items():
    print(f"\n=== Q{k}: {v['label']}  ({len(v['rows'])} rows)")
    print(" | ".join(v["cols"]))
    for r in v["rows"][:25]:
        print(" | ".join("" if x is None else str(x) for x in r))
    if len(v["rows"]) > 25:
        print(f"... +{len(v['rows'])-25} more")
