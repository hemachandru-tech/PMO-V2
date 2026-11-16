# TPG dashboard rules (upload document)

Purpose: the definitions behind the PMO dashboard tabs, written as rules for the `tpg_dataverse` database (Dataverse
names, same shape as TelaAdditional2.md). Read after TelaAdditional2.md; where they overlap, TelaAdditional2.md wins.
"Open project" and "open request" are as defined there. "Today" is the data date.

## 1. Days since the project was last edited
- **Days since last edited** = the data date − DATE(`tpg_project.modifiedon`).
- This measure counts **Active projects only**: `tpg_projectstatusname` = 'Active'. People ask "how are projects
  distributed by days since the project was last edited" and mean Active projects.
- **Last-edited bands** (in this order): '0-7 days' (0 to 7), '8-30' (8 to 30), '31-60' (31 to 60), '61-120'
  (61 to 120), '120+' (121 or more). A band is counted even when it holds no project.

## 2. Journey legs (Demand-to-Project Conversion)
- A **traced project** is an open project that names the request it came from:
  `tpg_project.tpg_projectrequest` = `tpg_projectrequests.tpg_projectrequestsid`.
- The **journey legs** of a traced project, in days:
  - **Client ask to project created** = DATE(`tpg_project.createdon`) − DATE(`tpg_projectrequests.tpgmea_clientrequestdate`).
  - **DARB approval to project created** = DATE(`tpg_project.createdon`) − DATE(`tpg_projectrequests.tpgmea_darbapprovaltimestamp`).
    A negative value means the project was created before the request was approved.
  - **Project created to first baselined go-live** = DATE(`tpg_project.tpgmea_firstbaselinedpdd`) − DATE(`tpg_project.createdon`).
- The **register of projects for journey legs** lists one row per traced project: the project (`tpg_subject`), its
  number (`tpgmea_pprojectnumber`), the request (`tpg_projectrequests.tpg_title`) and the three legs. A blank date
  leaves that leg blank; the project stays in the list.
- Request journey legs: **client ask to logged** = DATE(`tpg_projectrequests.createdon`) −
  DATE(`tpgmea_clientrequestdate`); **client ask to DARB approval** = DATE(`tpgmea_darbapprovaltimestamp`) −
  DATE(`tpgmea_clientrequestdate`), for Approved requests only.

## 3. Top 20 Strategic Initiatives
- The tab ranks **open records** by how many **lenses** they clear.
- **Request lenses** (table `tpg_projectrequests`):
  - **Strategic objective**: `tpgmea_projectobjectivename` = 'Strategic'.
  - **Critical or High priority**: `tpg_priorityname` IN ('Critical', 'High').
  - **Money**: among the top 20 open requests by `tpgmea_recurringrevenueannualbaselineusd` or among the top 20 open
    requests by `tpgmea_costtotal_usd`.
- **Project lenses** (table `tpg_project`): **ExCo flag** `tpgmea_keypriorityprojectname` = 'Yes'; **Strategic
  objective** `tpgmea_projectobjectivename` = 'Strategic'; **Critical or High priority** `tpgmea_priorityname` IN
  ('Critical', 'High'); **Money** as for requests, on `tpgmea_recurringrevenueannualbaselineusd` and
  `tpgmea_customcosttotalusd`.
- **Top 20 requests** (and **Top 20 projects**): sort by the number of lenses cleared (most first), then priority
  (Critical, High, Moderate, Low, blank last), then revenue (`tpgmea_recurringrevenueannualbaselineusd`, blank as 0,
  highest first), then cost (highest first), and keep the first 20. Only open records are ranked.
- The **register of demand requests for the Top 20** shows the request (`tpg_title`), its number
  (`tpgmea_projectnumber`), state, priority, objective, revenue, cost and the number of lenses cleared.

## 4. On-hold reasons and comments
- The **register of on-hold projects** lists projects with `tpg_projectstatusname` = 'On Hold' and shows the reason
  (`tpgmea_evwronholdreasonname`, a blank reason shown as '(Blank)'), the comment (`tpgmea_projectputonholdreason`),
  the hold date (`tpgmea_projectputonholddate`) and the days on hold (the data date − DATE(`tpgmea_projectputonholddate`)).

## 5. Change requests awaiting approval
- A change request (`tpg_change`) is **awaiting approval** when it is open (TelaAdditional2.md §3) and is not an Initial
  Baseline (`tpg_changetypename` and `tpgmea_reasonforpcrname` are not 'Initial Baseline').
- "Which projects have the most change requests awaiting approval" counts those change requests per open project,
  joined on `tpg_change.tpg_project` = `tpg_project.tpg_projectid`, most first. "PCR in progress" (the last 28 days) is
  a different, narrower measure.
