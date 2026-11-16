# PMO dashboard tab definitions

Each section is one dashboard tab: what it shows, how each widget counts, and its bands. Column names are the Dataverse names. 'Open projects' and 'open requests' follow the rules document.

## Projects by CIO and Business Unit
- Projects view asks: Whose book is it, and where is the trouble?
- Widget 'Act on this: priority requests stuck or stalling': Critical and High priority requests that are stuck or stalling: stuck first, then the longest overdue gate, then Critical, then the longest in the current gate. Query: Consider only open requests with priority Critical or High whose verdict is Stuck or Stalling
- Widget 'Projects per CIO, split by colour': Ranked by size of book. "(not set)" is its own row.
- Widget 'Share of the book against share of the Red': One bar for projects, one for Red: a group that is wider on Red than on projects carries more than its weight.
- Widget 'Key projects per CIO': Projects flagged as key projects in NI's PMO data, against the rest of each book.
- Widget 'Key projects': Highlighted wherever they appear; worst colour first. Query: List open projects with key priority project Yes with CIO, business unit, overall KPI, percent complete and planned go-live
- Widget 'Compliance by CIO: budget code and change control': Projects with no budget code, and projects whose go-live slipped with no change request.
- Widget 'Change requests by reason': Raised, approved, awaiting and changing the go-live date. Query: For open projects count project change requests (excluding change type Initial Baseline) by reason for PCR, with how many are Fully Approved, how many are awaiting approval (not Fully Approved and not rejected) and how many have change type Schedule
- Widget 'Business unit, then CIO': Area is the number of open projects.
- Widget 'Colour split'
- Widget 'Requests per CIO: moving, stalling, stuck or paused': Ranked by size of book. "(not set)" is its own row. Click a cell for the requests behind it.
- Widget 'Share of requests against share of the older-than-90-day tail'
- Widget 'Business unit, then CIO': Area is the number of open requests.
- KPI 'CIOs with projects': How many distinct cio values (excluding blank) appear among open projects?
- KPI 'CIOs with requests': How many distinct cio values (excluding blank) appear among open requests?

## Projects by Status (Green/Amber/Red)
- Projects view asks: Can I believe the colours?
- Widget 'What the colour says, and what the records say': Green split by the evidence verdict; Amber and Red split by whether the records back the colour.
- Widget 'The rule registry': How many open projects hit each rule. Click a row for the projects. Query: For each of these checks count the open projects that hit it: on hold; overdue; a sub-KPI Red; slipped with no change request; cost estimate over 115% of approved budget; an overdue action; an Active High issue; a change request rejected or pending over 28 days; a sub-KPI Amber; no status report in 60 days; no first baselined go-live; no tpg_progress.
- Widget 'Green projects the records contradict or challenge': Ranked by tier, then number of rules, then days. Query: List Green projects whose evidence verdict is Cannot be Green or Challenge it, ranked by verdict then number of rules hit
- Widget 'What sits behind the colour': Switch the dimension; groups under 5 are greyed.
- Widget 'Green, Amber, Red'
- Widget 'Moving, stalling, stuck or paused, by gate reached'
- Widget 'Who holds the stuck or stalling requests': By SD lead; switch to CIO or Owner (SA/BA). "(not set)" is its own row.
- Widget 'Stuck or stalling requests': Stuck first, then the longest overdue gate, then the longest in the current gate. Query: List open requests that are Stuck or Stalling with their gate, any overdue gate, days in the current gate and the reason recorded, Stuck first, then the most days overdue, then the most days in the current gate
- KPI 'Longest overdue gate': Among open requests with an overdue gate, what is the most days any gate is past its planned date?

## Projects by Stage (Plan → Closure)
- Projects view asks: Where is everything in the lifecycle, and where does it pile up?
- Widget 'Act on this: go-lives overdue' Query: Consider only open projects that are overdue
- Widget 'Stage rail': Empty stages are shown at zero. Click a bar for the projects.
- Widget 'How long live projects have been live': Days since actual go-live. Only open projects that went live (stage S7) are counted; projects not yet live are left out. A go-live after today is a data error to correct. Bands: 0-30 days (0 to 30 days); 31-90 (31 to 90 days); 91-180 (91 to 180 days); 181-365 (181 to 365 days); 365+ (366 to 99999 days).
- Widget 'Live dwell histogram'
- Widget 'Gate ladder': Furthest gate reached, split moving, stalling, stuck or paused.
- Widget 'Cleared every gate, not yet approved': Longest in the SOW gate first: what is holding approval. Query: List open requests that have reached gate 6 SOW, with days in the current gate, longest first
- Widget 'How long open demand has sat in its current gate': Days since the last gate date recorded (else the client ask). Over 180 days is Stalling. Bands: 0-30; 31-90; 91-180; 181-365; 365+.
- Lens tab 'Most lenses': Rank open projects by number of lenses cleared, then priority, then revenue baseline, then cost estimate; show the top 20
- Lens tab 'ExCo flag': List open ExCo projects by revenue baseline
- Lens tab 'Strategic objective': List open projects with tpgmea_projectobjectivename Strategic by revenue baseline
- Lens tab 'Revenue': Rank open projects by revenue baseline
- Lens tab 'Cost estimate': Rank open projects by tpgmea_customcosttotalusd (an estimate)

## Top 20 Strategic Initiatives
- Projects view asks: Which twenty matter most, and do the signals agree?
- Widget 'Which lenses each project clears': ExCo flag, Strategic objective, Critical or High priority, Money (top 20 by revenue or cost).
- Widget 'Top 20': Ranked by the number of lenses cleared, then priority. A row opens the dossier.
- Widget 'Health of the lens-leaders against the rest': Projects clearing three or more lenses against everyone else. Query: clear three or more lenses
- Widget 'ExCo lens'
- Widget 'Which lenses each request clears': Strategic objective, Critical or High priority, Money (top 20 by revenue or cost).
- Widget 'Top 20 requests': Ranked by lenses cleared; verdict and overdue gate show whether the important ones are moving.
- KPI 'ExCo share of revenue': What share of the total revenue baseline of open projects sits on ExCo projects?
- Lens tab 'Most lenses': Rank open projects by number of lenses cleared, then priority, then revenue baseline, then cost estimate; show the top 20
- Lens tab 'ExCo flag': List open ExCo projects by revenue baseline
- Lens tab 'Strategic objective': List open projects with tpgmea_projectobjectivename Strategic by revenue baseline
- Lens tab 'Revenue': Rank open projects by revenue baseline
- Lens tab 'Cost estimate': Rank open projects by tpgmea_customcosttotalusd (an estimate)

## ExCo Priority Projects
- Projects view asks: How are the key-priority projects doing, and is anyone looking after them?
- Requests view asks: Is priority demand (Strategic, or Critical or High) moving, and who holds it?
- Widget 'Act on this: ExCo projects' Query: Consider only open projects with tpgmea_keypriorityprojectname Yes
- Widget 'ExCo scorecard': Only Key priority (ExCo) projects; the others are left out. One row per ExCo project in scope, one column per check, worst first. A column total opens the projects failing it.
- Widget 'ExCo against the rest of the book' Query: tpgmea_keypriorityprojectname Yes
- Widget 'Go-live: first baselined against planned': ExCo projects with both dates, biggest slip first. Query: List open ExCo projects with tpgmea_firstbaselinedpdd and tpgmea_productdeploydate and the slip in days
- Widget 'ExCo flag'
- Widget 'Priority demand scorecard'
- Widget 'Priority demand against the rest' Query: tpgmea_projectobjectivename Strategic or priority Critical or High
- Widget 'Where priority demand sits on the gate ladder': Furthest gate reached, split moving, stalling, stuck or paused.
- KPI 'Priority demand stuck or stalling': How many open requests with tpgmea_projectobjectivename Strategic or priority Critical or High are Stuck or Stalling?
- KPI 'Priority demand with an overdue gate': How many open requests with tpgmea_projectobjectivename Strategic or priority Critical or High have an overdue gate?
- KPI 'Priority demand with no SD lead': How many open requests with tpgmea_projectobjectivename Strategic or priority Critical or High have no tpgmea_sdleadname?

## Project Delivery Heatmap
- Projects view asks: Where is the trouble concentrated?
- Widget 'Delivery heatmap': Rows are regional groups, columns the trouble signals. Cells under 5 are greyed.
- Widget 'Where the same projects pile up': Region by platform, switchable. Look for a shared cluster.
- Widget 'Share of each group in trouble': Of each regional group's open projects, the share that is Red or overdue: a small group can carry a large share.
- Widget 'Red and Amber cells'
- Widget 'Where the trouble sits': Rows: any dimension. Columns: stalling, stuck (returned, or the next gate overdue) and paused. Cells under 5 are greyed.
- Widget 'Share of each group's demand stuck or stalling': Of each group's open requests, the share stuck or stalling; compare with the share across all groups in the headline.
- KPI 'Top 3 regions' share of trouble': Group open projects in trouble by region group and give the share held by the three biggest groups

## Regional Portfolio Dashboard
- Projects view asks: How does the portfolio split across regional groups, and does each carry its weight?
- Widget 'Act on this: projects in trouble by region': Who to call and why: stuck first, then the longest overdue gate, then the longest in the current gate. Query: Consider only open projects in trouble (tpg_overallkpiname Red, or overdue), and give their regional group
- Widget 'Share of the portfolio by measure': Each bar is 100% of one measure; segments are the groups.
- Widget 'One card per group': Colour split, overdue, live, cost and revenue.
- Widget 'Region to group mapping': The mapping behind every group on this page. Query: List each region with its region group and the number of open projects
- Widget 'Red share by group'
- Widget 'Share of demand by measure'
- Widget 'One card per group' Query: the number of requests
- Widget 'Region to group mapping' Query: List each region with its region group and the number of open requests
- KPI 'Regional groups': How many distinct region groups appear among open projects?
- KPI 'Africa share of Red': Of the open projects with tpg_overallkpiname Red, how many are in the Africa region group and what share is that?
- KPI 'Acquiring projects': How many open projects have a tpgmea_salestypename that starts with Acquiring?
- KPI 'Regional groups': How many distinct region groups appear among open requests?
- Journey legs: Client ask to project created; DARB approval to project created; Project created to first baselined go-live; Client ask to logged (created); Client ask to DARB approval (approved); Open requests: days waiting since the ask

## Demand-to-Project Conversion
- Projects view asks: What share of demand becomes a project, and how long does it take?
- Widget 'Act on this: past the approved median and stuck or stalling' Query: Consider only open requests that have waited longer than the median days from tpgmea_clientrequestdate to tpgmea_darbapprovaltimestamp of approved requests and whose verdict is Stuck or Stalling, and add the wait as a reason
- Widget 'Where the demand went': All requests, open or closed, into their outcome and next step. Query: Count all requests by outcome (Approved, Open, Dropped or Rejected) and, within each, by next step: for Approved whether the project link resolves to a project in the extract; for Open the gate reached; for Dropped or Rejected whether a tpgmea_dropreasonname is recorded
- Widget 'Journey legs': Median days and n for each leg. Query: Give the median days from tpgmea_clientrequestdate to createdon, and from tpgmea_clientrequestdate to tpgmea_darbapprovaltimestamp for Approved requests, with n
- Widget 'How long open demand has been waiting': Days since the client ask, open requests. Bands: 0-30 (0 to 30 days); 31-90 (31 to 90 days); 91-180 (91 to 180 days); 181-365 (181 to 365 days); 365+ (366 to 99999 days).
- Widget 'Who gets in: conversion by dimension': Approved as a share of decided requests (approved, dropped or rejected). Open requests are not decided yet: switch the measure to Still open to see them.
- Widget 'Month by month': September and October carry no records and are drawn as such, not as zero.
- Widget 'Where projects come from': Traced or untraced, by any dimension.
- Widget 'Journey legs for traced projects' Query: For open projects that name a request, give the median days for: client ask to project created; DARB approval to project created; project created to first baselined go-live, with n
- Widget 'Projects created before their request was approved' Query: List open projects createdon before their request's tpgmea_darbapprovaltimestamp
- Widget 'Projects created per month': Traced to a request or not. A burst of creations in one or two months is a bulk load, not demand.
- KPI 'Traced to a request': How many open projects have tpg_projectrequestsid set?
- KPI 'Created before DARB approval': For open projects that name a request, how many were createdon before the request's tpgmea_darbapprovaltimestamp?
- KPI 'Ask to project, median': For open projects that name a request, what is the median days from the request's tpgmea_clientrequestdate to the project's createdon?

## Portfolio Trend Analysis (Last 12 Months)
- Projects view asks: Which way is the portfolio moving?
- Widget 'Act on this: go-lives slipping' Query: Consider only open projects that are overdue or whose go-live moved and no approved change covers the new date
- Widget 'What the dates support': The last twelve months, and the go-lives planned in the next six.
- Widget 'Today's colours by dimension': The single snapshot we hold, until NI supplies the history.
- Widget 'Demand and gates over time'
- KPI 'Went live by baseline': How many open projects have tpgmea_projectimpdate on or before tpgmea_firstbaselinedpdd?
- KPI 'Raised in the last 90 days': How many requests, open or closed, have tpgmea_clientrequestdate in the last 90 days?
- KPI 'Decided in the last 90 days': How many requests were decided in the last 90 days: tpg_requeststatename Approved with tpgmea_darbapprovaltimestamp, or Dropped or Rejected with tpgmea_dropdate, in the last 90 days?

## Portfolio Health Summary
- Projects view asks: How healthy is the portfolio, KPI by KPI?
- Widget 'Act on this: failing four or more checks' Query: Consider only open requests failing four or more of the nine intake checks, and add the number failed as a reason
- Widget 'Six KPIs by colour': Click any count. Query: For each of tpg_overallkpiname, tpg_costkpiname, tpg_schedulekpiname, tpg_issuekpiname, tpg_resourcekpiname and tpg_workkpiname count open projects Green, Yellow (Amber), Red and blank
- Widget 'Where the Red cells sit': Group by any dimension: Red cells across the six KPIs, summed, against the group's share of the book.
- Widget 'Who or what the colour is attributed to': RAG due to: says who, not why.
- Widget 'Schedule KPI against dates': Schedule KPI Green while the go-live is overdue, or slipped with no approved change covering it, is a contradiction. Query: tpg_schedulekpiname Green
- Widget 'Load and pressure by CIO': Who may need support.
- Widget 'Six-KPI matrix and RAG attribution'
- Widget 'Intake checks by gate reached': Failed checks per stage. Click a count. Query: For each of the nine intake checks count open requests failing it, by gate reached
- Widget 'Failed checks by dimension': Checks failed on each group's open requests, and how many fail four or more. "(not set)" is its own row.
- KPI 'Failing four or more checks': How many open requests fail four or more of the nine intake checks?

## Weekly Status Reporting Compliance
- Projects view asks: Are active projects reporting every week?
- Requests view asks: Which open requests has nobody updated lately, and who holds them?
- Widget 'Days since the last status report': Active projects. Weekly compliance is a report in the last 7 days.
- Widget 'Reporting cadence': Reports filed per month. Query: Count status_reports by month of tpg_reportdate and the number of distinct projects reporting
- Widget 'Reporting load by delivery manager': Who may need support: load against signals.
- Widget 'Active projects not reporting weekly': Longest silence first. Query: List Active projects with no status report in the last 7 days, longest silence first
- Widget 'Status reports'
- Widget 'Days since the request was last updated': Proxy for reporting (modifiedon). Bands: 0-7 days (0 to 7 days); 8-14 (8 to 14 days); 15-30 (15 to 30 days); 31-60 (31 to 60 days); 60+ (61 to 99999 days).
- Widget 'Requests not updated for 30 days, by owner': Owner (SA/BA) is filled on every request; SD lead and CIO are on the switch.
- Widget 'Oldest untouched requests' Query: List open requests with no modifiedon in the last 30 days, longest first
- KPI 'Reported in 7 days': open projects that with tpg_projectstatusname Active have a status report in the last 7 days
- KPI 'Reported in 14 days': open projects that with tpg_projectstatusname Active have a status report in the last 14 days
- KPI 'Reported in 30 days': open projects that with tpg_projectstatusname Active have a status report in the last 30 days
- KPI 'No report in 60 days': open projects that with tpg_projectstatusname Active have no status report in the last 60 days (or ever)
- KPI 'Never reported': open projects that with tpg_projectstatusname Active have no status report at all
- KPI 'Updated in 7 days': open projects that have modifiedon within the last 7 days
- KPI 'Not updated in 30 days, stuck or stalling': open projects that have no modifiedon in the last 30 days and are Stuck or Stalling
- KPI 'Updated in 30 days': open projects that have modifiedon within the last 30 days
- KPI 'Not updated in 90 days': open projects that have modifiedon more than 90 days before 2026-10-25

## Project Baselining Compliance
- Projects view asks: Is every project baselined, and baselined on time?
- Requests view asks: Is each required gate document issued once a request has moved past it?
- Widget 'Baselining funnel': From open projects to a fully approved baseline. Query: Count open projects at each step: all; with tpgmea_firstbaselinedpdd; also with an Initial Baseline change request; also with that change request Fully Approved
- Widget 'Baselining by dimension': Projects with no first-baselined go-live, per delivery manager.
- Widget 'Unbaselined projects': Oldest first. Query: List open projects with no tpgmea_firstbaselinedpdd, oldest first
- Widget 'Project baselines'
- Widget 'Required against issued, per gate' Query: For HLE, DOU, DARB and SOW count open requests that require the document, those that have it, and those that lack it although they are past that stage
- Widget 'Required and not issued' Query: List open requests that need a gate document (HLE, DOU, DARB or SOW) and are past that stage without it
- KPI 'Initial Baseline raised': open projects that have a project_changes row of tpg_changetypename Initial Baseline
- KPI 'Baselined late (30+ days)': open projects that raised their Initial Baseline change request more than 30 days after createdon
- KPI 'Required gate skipped': open projects that are past a gate they require (HLE, DOU, DARB review or SOW) without it being issued, approved or signed off

## Budget Code Compliance
- Projects view asks: Which cost estimates have no budget code or confirmed funding?
- Requests view asks: Which requests reach the funding gates without a budget code or type?
- Widget 'Breach types by dimension': Stacked by the breach combination.
- Widget 'Department against breach': Cells under 5 greyed.
- Widget 'Projects in breach' Query: List open projects with no budget code, unconfirmed funding or CAPEX without an approved budget, with their cost estimate
- Widget 'Requests with no budget code, by CIO and stage'
- Widget 'Requests with no budget code': Furthest gate first: at DARB and SOW a budget code is due. Query: List open requests with no tpgmea_budgetcode, furthest gate first
- KPI 'Any breach': open projects that have no tpgmea_budgetcode, or tpg_fundingisavailablename not Yes, or tpgmea_budgettypename CAPEX with no approved budget
- KPI 'No budget code at DARB or SOW': open projects that have reached gate 5 DARB done or 6 SOW and have no tpgmea_budgetcode
- KPI 'No budget type': open projects that have no tpgmea_budgettypename
- KPI 'No approved budget': open projects that have no tpgmea_approvedbudgetvaluesusd above 0

## PM Assignment Compliance
- Projects view asks: Does every project have an accountable manager, and how heavy is each one's load?
- Requests view asks: Does every open request have an SD lead, and how heavy is each lead's load?
- Widget 'Projects per PM': How many projects each PM (Owner) or delivery manager is handling; people under 5 are greyed.
- Widget 'Load and pressure by delivery manager': Who may need support.
- Widget 'Projects with no delivery manager' Query: List open projects with no tpgmea_pdeliverymanagername, oldest first
- Widget 'Delivery manager'
- Widget 'Load and pressure by SD lead'
- Widget 'Requests with no SD lead' Query: List open requests with no tpgmea_sdleadname, stuck first, then longest in the current gate
- KPI 'No PM assigned date': open projects that have no tpgmea_projectassigneddate
- KPI 'PM assigned 10+ days after creation': open projects that have tpgmea_projectassigneddate more than 10 days after createdon
- KPI 'No SD lead, stuck or stalling': open projects that have no tpgmea_sdleadname and are Stuck or Stalling
- KPI 'No owner (SA/BA)': open projects that have no owner

## Projects Without Approved Schedule
- Projects view asks: Which projects have no approved schedule behind their dates?
- Requests view asks: Which required gates have no planned date, starting with the next gate?
- Widget 'Schedule funnel' Query: Count open projects with all six stage planned end dates, and those that also have an Initial Baseline or Schedule change request that is Fully Approved
- Widget 'Which stage date is missing most' Query: For each of the six stage planned end dates count open projects where it is blank
- Widget 'Projects without an approved schedule' Query: List open projects that lack an approved schedule, oldest first
- Widget 'Requests missing planned gate dates' Query: List open requests with a missing planned date: HLE or DOU where required, DARB review unless exempted, or SOW
- Widget 'Missing planned dates by dimension'
- KPI 'Stage dates out of order': open projects that have stage planned end dates (s2 to s7) that run backwards: a later stage ends before an earlier one
- KPI 'All six stage dates': open projects that have all six stage planned end dates set
- KPI 'Approved schedule': open projects that have all six stage planned end dates and a fully approved Initial Baseline or Schedule change request
- KPI 'No stage dates at all': open projects that have none of the six stage planned end dates
- KPI 'Next gate has no planned date': open projects that have no planned date on their next required gate (the first of HLE, DOU, DARB review, SOW beyond the gate reached)
- KPI 'DOU required, no planned date': open projects that require DOU but have no tpgmea_planneddouissuancedate
- KPI 'DARB review not planned': open projects that are not exempted from DARB but have no tpgmea_darbreviewdate
- KPI 'SOW not planned': open projects that have no tpgmea_tentativedateofsowissuance

## Overdue Governance Actions
- Projects view asks: Which governance actions are overdue, and who holds them?
- Requests view asks: Which committed gate dates have passed, how long ago, and who holds them?
- Widget 'Ageing of the oldest overdue action per project' Bands: 1-30 days (1 to 30 days); 31-60 (31 to 60 days); 61-90 (61 to 90 days); 90+ (91 to 99999 days).
- Widget 'Overdue actions by assignee': Assignees are not necessarily on the project team.
- Widget 'Overdue actions by delivery manager'
- Widget 'Projects with overdue actions' Query: List open projects with overdue actions and how many, oldest first
- Widget 'Ageing of overdue gates' Bands: 1-30 days (1 to 30 days); 31-60 (31 to 60 days); 61-90 (61 to 90 days); 90+ (91 to 99999 days).
- Widget 'Requests with an overdue gate' Query: List open requests with an overdue gate and the days overdue
- KPI 'Overdue actions': How many action_items on open projects are overdue?
- KPI 'Oldest overdue': What is the largest number of days overdue among overdue action_items on open projects?
- KPI 'Next gate overdue': open projects that have their next required gate (the first of HLE, DOU, DARB review, SOW beyond the gate reached) past its planned date and not done
- KPI 'Overdue DOU': open projects that have an overdue DOU gate
- KPI 'Overdue SOW': open projects that have an overdue SOW gate
- KPI 'Overdue DARB': open projects that have an overdue DARB gate

## Project Change Request (PCR) Compliance
- Projects view asks: Are schedule moves going through change control?
- Requests view asks: Which open requests are change requests, and do they name the project they change?
- Widget 'Change requests by reason': Raised, approved, awaiting and changing the go-live date, by reason. Click a row for the change requests. Query: For open projects count project change requests (excluding change type Initial Baseline) by reason for PCR, with how many are Fully Approved, how many are awaiting approval (not Fully Approved and not rejected) and how many have change type Schedule
- Widget 'Change requests by project': Projects with at least one change request, most raised first. Query: List open projects that have project change requests other than the Initial Baseline with how many are raised, approved and awaiting
- Widget 'Change requests by approval status' Query: Count project_changes rows by tpg_approvalname for open projects, with the number of distinct projects
- Widget 'Ageing of unapproved change requests' Bands: 0-28 days; 29-60; 61-120; 120+.
- Widget 'Go-live moved without an approved change' Query: List open projects whose planned go-live is later than first baselined with no fully approved Schedule change request
- Widget 'Change requests per month' Query: Count project_changes by month of tpg_requesteddate and tpg_changetypename
- Widget 'Project change request approval statuses'
- Widget 'Requests that are change requests' Query: List open requests with tpgmea_isthisacrrequestname Yes, with the tpgmea_crreasonname, master project, state and age
- Widget 'Change requests by dimension'
- KPI 'PCRs raised': How many project change requests (project_changes rows, excluding change type Initial Baseline) exist for open projects?
- KPI 'PCRs approved': How many project change requests (excluding change type Initial Baseline) for open projects have approval status Fully Approved?
- KPI 'Awaiting approval': How many project change requests (excluding change type Initial Baseline) for open projects are awaiting approval, meaning the approval status is not Fully Approved and not Rejected?
- KPI 'PCRs changing go-live': How many project change requests for open projects have change type Schedule?
- KPI 'Go-live moved, not covered': open projects that have tpgmea_productdeploydate later than tpgmea_firstbaselinedpdd and no Fully Approved Schedule change request whose tpgmea_productdeploydate is on or after the project's tpgmea_productdeploydate
- KPI 'Change request stuck': open projects that have a change request rejected or pending more than 28 days
- KPI 'Change requests stuck or stalling': open projects that are change requests (tpgmea_isthisacrrequestname Yes) and are Stuck or Stalling
- KPI 'Change requests older than 90 days': open projects that are change requests (tpgmea_isthisacrrequestname Yes) older than 90 days
- KPI 'Change request, no master project': open projects that are change requests with no tpgmea_masterprojectidforcrname

## Projects with Missing Mandatory Fields
- Projects view asks: Which mandatory fields are going unfilled, and where?
- Requests view asks: Which request fields go unfilled, on which requests and under which CIO?
- Widget 'Completeness by mandatory field' Query: For each mandatory field (name, tpg_start, tpg_requestedbyname, tpgmea_clientnamename, tpgmea_businessunitdepartmentname, region, platform, priority, tpgmea_deliverymanagername, cio, tpgmea_budgettypename, tpgmea_budgetcode, tpgmea_productdeploydate, tpgmea_firstbaselinedpdd, tpg_overallkpiname, tpgmea_legacyjira) count open projects where it is blank
- Widget 'Field against dimension': Rows are CIOs, columns the mandatory fields.
- Widget 'Projects with the most gaps' Query: List open projects by number of blank mandatory fields, most first
- Widget 'Completeness by field' Query: For each request field (title, tpgmea_clientnamename, cio, region, platform, priority, tpgmea_projectobjectivename, size, tpgmea_budgetcode, tpgmea_sdleadname, tpgmea_customexecutivponsorname, tpg_valuestatement) count open requests where it is blank
- Widget 'Field against dimension'
- Widget 'Requests with the most gaps' Query: List open requests by number of blank mandatory fields, most first
- KPI 'Missing a mandatory field': open projects that have at least one blank mandatory field
- KPI 'Missing three or more': open projects that have three or more blank mandatory fields
- KPI 'No budget code': open projects that have no tpgmea_budgetcode
- KPI 'No Jira number': open projects that have no tpgmea_jiramigrated
- KPI 'Missing three or more, stuck or stalling': open projects that have three or more blank mandatory request fields and are Stuck or Stalling
- KPI 'Missing a field': open projects that have at least one blank mandatory request field
- KPI 'Missing three or more': open projects that have three or more blank mandatory request fields

## On-Hold Projects Aging
- Projects view asks: How long have projects been parked, and why?
- Requests view asks: How long have requests been on hold or returned, and why?
- Widget 'How long projects have been on hold': Only On Hold projects are counted: days since the hold started. Bands: 0-30 days; 31-90; 91-180; 180+; No hold date.
- Widget 'Reasons': Only On Hold projects are counted; a blank reason is its own row. Query: Count open projects with tpg_projectstatusname On Hold by tpgmea_evwronholdreasonname (blank is its own row)
- Widget 'Projects on hold' Query: List open projects with tpg_projectstatusname On Hold with tpgmea_projectputonholddate and colour
- Widget 'Reasons and comments' Query: Count open requests that are On Hold or Return to Requester by the hold reason or return comment
- Widget 'Requests on hold or returned': Longest held first. Query: List open requests that are On Hold or Return to Requester with the date and reason
- KPI 'On hold over 90 days': How many open projects with tpg_projectstatusname On Hold have tpgmea_projectputonholddate more than 90 days before 2026-10-25?
- KPI 'Held or returned, no date': How many open requests are On Hold or Return to Requester with no tpgmea_onholddate or tpgmea_returntorequestownerdate?

## Closure Governance Compliance
- Projects view asks: Are projects closed properly, and are live projects ever closed?
- Requests view asks: Are requests closed with a reason, and do approved ones lead to a project?
- Widget 'Completed projects against the checklist' Query: List Completed projects with tpgmea_projectclosuredate, tpgmea_projectimpdate and tpgmea_closeapprovalstatus
- Widget 'Live and not closed: days since go-live': Only projects live and not yet closed (stage S7) are counted. Bands: 0-30 days; 31-90; 91-180; 181-365; 365+.
- Widget 'Close approval status'
- Widget 'Live projects past planned closure' Query: List open projects whose tpgmea_warrantyandclosurestagedate is before 2026-10-25 and have no tpgmea_projectclosuredate
- Widget 'Dropped and rejected requests' Query: List requests with tpg_requeststatename Dropped or Rejected with tpgmea_dropdate and tpgmea_dropreasonname
- Widget 'Approved, project outside the extract' Query: List Approved requests whose tpgmea_projectlink points to a project not in the data
- KPI 'Completed projects': How many projects have tpg_projectstatusname Completed?
- KPI 'Pass the closure checklist': How many Completed projects have tpgmea_projectclosuredate, tpgmea_projectimpdate and tpgmea_closeapprovalstatus Approved?
- KPI 'Dropped with a reason': How many Dropped or Rejected requests have a tpgmea_dropreasonname?
- KPI 'Approved, project outside extract': How many Approved requests link to a project that is not in the data?

## Milestone Slippage
- Projects view asks: How far have go-lives slipped, and did anyone agree to it?
- Requests view asks: Which gates were issued after their planned date, by how much, and who holds them?
- Widget 'Act on this: slipped go-lives' Query: Consider only open projects whose tpgmea_productdeploydate is later than tpgmea_firstbaselinedpdd, and add the reason: slipped N days
- Widget 'First baselined against planned go-live': Slipped projects, biggest slip first. Query: List open projects with a positive slip with tpgmea_firstbaselinedpdd and tpgmea_productdeploydate
- Widget 'How big the slips are' Bands: On time or earlier; 1-30 days (1 to 30 days); 31-90 (31 to 90 days); 91-180 (91 to 180 days); 180+ (181 to 99999 days).
- Widget 'Is the slip covered by an approved change?': Covered (an approved Schedule change request dates the new go-live), plan moved past the approved date, change requested but not approved, or silent.
- Widget 'Slip by stage' Query: For each stage S2 to S7 give the median days between the project's current planned end date and the stage end date on its Initial Baseline project_changes row
- Widget 'Go-live slip and stage slip'
- Widget 'Gate slip: planned against issued': All requests, open or closed, with the dates; the cards count open requests only. Query: For the DOU, SOW and HLE gates count requests with a planned date, an issued date, both, issued on time and issued late, with the median days late
- Widget 'Median gate lateness by owner': Days: issued minus planned on the latest gate with both dates; median over the open requests that have both dates (n). Groups under 5 are greyed.
- KPI 'Median slip': What is the median of tpgmea_productdeploydate minus tpgmea_firstbaselinedpdd, in days, over open projects where it is positive?
- KPI 'Slipped 180+ days': open projects that have slipped more than 180 days
- KPI 'DOU issued late': open projects that issued DOU after the planned date
- KPI 'SOW issued late': open projects that issued SOW after the planned date

## Projects Due in Next 30/60/90 Days
- Projects view asks: What is about to go live, and is it ready?
- Requests view asks: Which next gates are due, overdue or not yet planned, and who owns them?
- Widget 'Planned go-lives ahead': Days from today to the go-live (after any approved change request). Only projects not yet deployed with a planned go-live are counted. Bands: 0-30 days (0 to 30 days); 31-60 (31 to 60 days); 61-90 (61 to 90 days); 91-180 (91 to 180 days); 181+ (181 to 99999 days).
- Widget 'Due soonest, with readiness flags' Query: List open projects not yet deployed with tpgmea_productdeploydate in the next 90 days, soonest first, with colour, tpg_progress and overdue actions
- Widget 'Next gate: overdue, no planned date or planned ahead, per owner': The next gate is the first required gate beyond the one reached. Owner (SA/BA) is always filled; SD lead and CIO are on the switch.
- Widget 'Next gates to chase': Overdue first, then the ones with no planned date, then the soonest planned. Query: List open requests whose next required gate (the first of HLE, DOU, DARB review, SOW beyond the gate reached) is overdue, has no planned date, or is planned within 90 days: overdue first (most days), then undated, then the soonest planned
- Widget 'Days to the next planned gate' Bands: 0-30 days (0 to 30 days); 31-60 (31 to 60 days); 61-90 (61 to 90 days); 91-180 (91 to 180 days); 181+ (181 to 99999 days).
- KPI 'Due in 90 days, no recent report': open projects that go live within 90 days of 2026-10-25 and have no status report in the last 60 days
- KPI 'Due in 30 days': open projects that are not deployed and go live (after any approved Schedule change request) between 2026-10-25 and 2026-11-24
- KPI 'Due in 60 days': open projects that are not deployed and go live (after any approved Schedule change request) between 2026-10-25 and 2026-12-24
- KPI 'Due in 90 days': open projects that are not deployed and go live (after any approved Schedule change request) between 2026-10-25 and 2027-01-23
- KPI 'Next gate due in 90 days': open projects that have their next required gate planned within the next 90 days

## Delayed Projects
- Projects view asks: Which projects are already late, and how late?
- Requests view asks: Which requests are stuck or stalling, where do they sit, and who holds them?
- Widget 'Ageing of delay': Only overdue projects (Active, past the go-live) are counted. Bands: 1-30 days (1 to 30 days); 31-60 (31 to 60 days); 61-90 (61 to 90 days); 90+ (91 to 99999 days).
- Widget 'Delay by dimension'
- Widget 'Delayed projects' Query: List overdue open projects, most days overdue first
- Widget 'Delay of go-live'
- Widget 'Where stuck and stalling requests sit': Furthest gate reached, split stuck or stalling.
- Widget 'Stuck and stalling requests': Stuck first, then the longest overdue gate, then the longest in the current gate. Query: List open requests whose verdict is Stuck or Stalling: Stuck first, then the most days a gate is overdue, then days in the current gate
- KPI 'Average days late': What is the average days overdue across overdue open projects?
- KPI 'Delayed but Green': open projects that are overdue with tpg_overallkpiname Green
- KPI 'Both': open projects that have an overdue gate and are older than 180 days

## Critical Path Projects
- Projects view asks: Which projects matter most to protect? (criticality view)
- Requests view asks: Are the Critical and High priority requests moving?
- Widget 'Criticality list': Critical or High priority, ExCo, or holding critical-flagged tasks; the ones in the most trouble first.
- Widget 'How late the critical tasks are': Critical work tasks (summary rows left out) by days past their finish date, with those ending after their project's go-live. Query: For tasks on open projects with tpg_iscriticalname Yes and is_summary No, count the tasks, those finishing after the project go-live and the projects, by days past finish (finish before today and tpg_progress under 100)
- Widget 'Critical tasks that threaten the go-live': Each critical work task with its finish, progress and the project's go-live. Query: List tasks on open projects with tpg_iscriticalname Yes and is_summary No, with the project, finish, tpg_progress and the project go-live; those finishing after go-live first, then the longest past finish
- Widget 'Health by priority': Open projects at each priority, by overall colour: are the Critical and High ones the healthy ones?
- Widget 'Critical-flagged tasks and the ExCo flag'
- Widget 'Critical and High requests on the gate ladder': Furthest gate reached, split moving, stalling, stuck or paused.
- Widget 'Critical and High requests': Stuck first, then the longest overdue gate, then the longest in the current gate. Query: List open requests with priority Critical or High: Stuck first, then the most days a gate is overdue, then days in the current gate
- KPI 'Critical work tasks': How many tasks on open projects have tpg_iscriticalname Yes and is_summary No?
- KPI 'Ending after go-live': How many critical work tasks (tpg_iscriticalname Yes, is_summary No) on open projects finish after the project's go-live (tpgmea_productdeploydate, or a later fully approved Schedule change request)?
- KPI 'Go-live at risk': How many open projects have a critical work task (tpg_iscriticalname Yes, is_summary No) finishing after the project's go-live (tpgmea_productdeploydate, or a later fully approved Schedule change request)?
- KPI 'Past finish, unfinished': How many critical work tasks (tpg_iscriticalname Yes, is_summary No) on open projects have a finish date before today and tpg_progress under 100?
- KPI 'Critical-flagged tasks': How many tasks on open projects have tpg_iscriticalname Yes?
- KPI 'Critical or High, stuck or stalling': open projects that are Critical or High priority and Stuck or Stalling
- KPI 'Critical or High, next gate overdue': open projects that are Critical or High priority with their next required gate overdue
- KPI 'Critical or High, older than 90 days': open projects that are Critical or High priority and older than 90 days
- KPI 'Critical or High with overdue gate': open projects that are Critical or High priority with an overdue gate

## Stage-wise Project Completion (%)
- Projects view asks: How complete are projects at each stage, and can I trust the field?
- Requests view asks: How many of their required gates have open requests passed?
- Widget 'Act on this: % complete to correct' Query: Consider only open projects with tpg_progress above 100, or Active, not yet live, with tpg_progress 0 or blank, and add that as a reason
- Widget 'Average % complete per stage': Averaged over projects that report a % above 0 (zero or blank is left out), shown with how many that is.
- Widget 'Distribution of % complete' Bands: 0% (0 to 0 days); 1-24%; 25-49% (25 to 49 days); 50-74% (50 to 74 days); 75-99% (75 to 99 days); 100% (100 to 100 days); Blank.
- Widget '% complete'
- Widget 'Average gate completion by CIO': Required gates passed, averaged over each group's open requests. Switch to owner, priority or stage.
- Widget 'Requests by gate completion' Query: List open requests with the number of required gates passed over required, most complete first
- KPI 'Average % complete (reporting projects)': What is the average tpg_progress over open projects where tpg_progress is above 0 (projects with 0 or blank are left out)?
- KPI 'Live but under 90% complete': open projects that are live (stage S7) with tpg_progress below 90 or blank
- KPI '% complete above 0': open projects that have tpg_progress above 0
- KPI '0% or blank': open projects that have tpg_progress 0 or blank
- KPI 'No gate passed': open projects that have passed none of their required gates
- KPI 'Half or more of the gates': open projects that have passed half or more of their required gates

## Project Schedule Variance
- Projects view asks: Who is running ahead of or behind their baseline?
- Requests view asks: How late are gates issued against their planned date?
- Widget 'Median variance by delivery manager': Median days over each manager's baselined projects. Groups under 5 are marked n<5, greyed and listed last.
- Widget 'Load and pressure by delivery manager'
- Widget 'Largest variances' Query: List open projects by the size of their variance in days, largest first
- Widget 'Go-live variance'
- Widget 'How late gates are issued': Issued minus planned on the latest gate with both dates, open requests that have both. Bands: Early; On time; 1-30 days late; 31-90; 90+.
- Widget 'Requests with late gates' Query: List open requests with a gate issued late or overdue
- KPI 'Median variance': What is the median variance in days over open projects with a tpgmea_firstbaselinedpdd (actual minus first baselined when deployed, otherwise planned minus first baselined)?
- KPI 'Ahead of baseline': open projects that are ahead of their first baselined go-live
- KPI 'A gate issued late': open projects that have a gate issued after its planned date (DOU, SOW or HLE)

## RAID Summary (Risks, Issues, Dependencies)
- Projects view asks: What is open across risks, issues and actions?
- Requests view asks: What stops the stuck and paused requests, and who can unblock them?
- Widget 'RAID by CIO' Query: For each CIO give, over open projects: active risks, active issues, open actions, overdue actions and pending decisions
- Widget 'Risk exposure bands' Query: Count Active risks on open projects by exposure band: under 10, 10 to 19, 20 and over
- Widget 'Active risks by dimension'
- Widget 'Projects with open RAID items': Any active risk, active issue or overdue action; the most open items first. Query: List open projects with at least one risk with tpg_statename Active, an issue with tpg_statusname Active or an overdue action item, with the number of each, most open items first
- Widget 'Risks, issues, actions, decisions'
- Widget 'Why requests are stuck' Query: Count open requests that are On Hold or Return to Requester by the hold reason or the return comment
- Widget 'Stuck and paused requests': Stuck first, then the longest overdue gate. Query: List open requests whose verdict is Stuck (returned, or the next required gate overdue) or Paused (On Hold), Stuck first
- KPI 'Active risks': How many risks on open projects have tpg_statename Active?
- KPI 'Active issues': How many issues on open projects have tpg_statusname Active?
- KPI 'Open actions': How many action_items on open projects are not Completed or Deferred?
- KPI 'Pending decisions': How many decisions on open projects have tpg_statusname Active?
- KPI 'Overdue actions': How many action_items on open projects are overdue?

## Projects Without Recent Updates
- Projects view asks: Which active projects have gone quiet?
- Requests view asks: Which requests has nobody touched lately, and are they moving?
- Widget 'Days since the project was last edited': Only Active projects are counted. Bands: 0-7 days (0 to 7 days); 8-30 (8 to 30 days); 31-60 (31 to 60 days); 61-120 (61 to 120 days); 120+ (121 to 99999 days).
- Widget 'Quiet projects by delivery manager'
- Widget 'Quietest active projects' Query: List Active projects by days since modifiedon, longest first, with days since the last status report
- Widget 'Days since the request was last updated' Bands: 0-7 days (0 to 7 days); 8-30 (8 to 30 days); 31-60 (31 to 60 days); 61-120 (61 to 120 days); 120+ (121 to 99999 days).
- Widget 'Not updated for 30 days, by owner'
- Widget 'Untouched and unowned requests': No update for 30 days and no SD lead: nobody is moving these. The oldest untouched overall are on G1. Query: List open requests with no SD lead and modifiedon more than 30 days ago, longest untouched first
- KPI 'No edit for 30 days': open projects that with tpg_projectstatusname Active have modifiedon more than 30 days before 2026-10-25
- KPI 'No report for 60 days': open projects that with tpg_projectstatusname Active have no status report in the last 60 days (or ever)
- KPI 'Never reported': open projects that with tpg_projectstatusname Active have no status report at all
- KPI 'Quiet on both': open projects that with tpg_projectstatusname Active have no edit for 30 days and no report for 60 days
- KPI 'Not updated in 30 days, stuck or stalling': open projects that have no modifiedon in the last 30 days and are Stuck or Stalling
- KPI 'Not updated in 30 days, no SD lead': open projects that have no modifiedon in the last 30 days and no tpgmea_sdleadname
- KPI 'Not updated in 90 days': open projects that have modifiedon more than 90 days before 2026-10-25

## CAPEX Budget vs Actual
- Projects view asks: Are CAPEX projects inside their approved budget?
- Requests view asks: Is each request's cost estimate inside its approved budget?
- Widget 'Approved budget against cost estimate': CAPEX projects with a budget, biggest gap first. Query: List open CAPEX projects with tpgmea_approvedbudgetvaluesusd above 0 with tpgmea_customcosttotalusd (an estimate) and the difference
- Widget 'CAPEX projects' Query: List open CAPEX projects with approved budget and cost estimate
- Widget 'Actual spend'
- Widget 'Demand budget against cost estimate' Query: List open requests with tpgmea_approvedbudgetvaluesusd above 0 with tpgmea_costtotal_usd and the difference
- Widget 'Requests with no budget yet' Query: List open requests with no approved budget by cost estimate
- KPI 'Net variance (estimate - budget)': For open CAPEX projects with an tpgmea_approvedbudgetvaluesusd above 0, what is the sum of tpgmea_customcosttotalusd minus tpgmea_approvedbudgetvaluesusd?
- KPI 'Open CAPEX projects': open projects that have tpgmea_budgettypename CAPEX
- KPI 'With an approved budget': open projects that have tpgmea_budgettypename CAPEX and an tpgmea_approvedbudgetvaluesusd above 0
- KPI 'Estimate over budget': open projects that have tpgmea_budgettypename CAPEX, an approved budget and tpgmea_customcosttotalusd (an estimate) above it
- KPI 'Estimate over budget, CAPEX': open projects that have tpgmea_budgettypename CAPEX and tpgmea_customcosttotalusd above tpgmea_approvedbudgetvaluesusd
- KPI 'With an approved budget': open projects that have tpgmea_approvedbudgetvaluesusd above 0
- KPI 'Estimate over budget': open projects that have tpgmea_customcosttotalusd above tpgmea_approvedbudgetvaluesusd
- KPI 'No budget yet': open projects that have no tpgmea_approvedbudgetvaluesusd

## Resource Utilization & Timesheet Compliance
- Projects view asks: Where do the hours go, and how many are approved?
- Requests view asks: Where have the hours shaping requests gone, and how much is sunk in stuck or dropped demand?
- Widget 'Act on this: hours awaiting approval' Query: Consider only open projects with timesheet hours in Draft or Submitted status, and add the reason: N hours unapproved
- Widget 'Act on this: hours on stuck or stalling requests' Query: Consider only open requests with hours booked whose verdict is Stuck or Stalling, and add the hours as a reason
- Widget 'Person-weeks against a standard week': Only weeks with logged hours count, on the projects in view; lines with no owner or date are left out. Query: Group timesheet lines on open projects with tpg_total above 0 by owner and the calendar week (Monday) of createdon; count the person-weeks, people and hours in each band of the summed hours as a share of a 
- Widget 'Who logs the most hours a week': Per person: weeks with hours, average hours a week, and weeks over the standard. Query: Group timesheet lines on open projects with tpg_total above 0 by owner and the calendar week (Monday) of createdon; per owner give the number of weeks, the average hours a week, that average as a share of a 
- Widget 'Hours by timesheet status' Query: Sum timesheet tpg_total by tpgmea_timesheetstatusname for open projects, with the number of projects
- Widget 'Approval backlog by project': Unapproved hours, largest first. Only projects with unapproved hours are shown; the others are left out. Query: Rank open projects by unapproved timesheet hours (Draft or Submitted)
- Widget 'Where the effort sits': Hours logged, largest first, with cumulative share. Query: Rank open projects by total timesheet hours
- Widget 'Hours by dimension'
- Widget 'Person-weeks against a standard week': Only weeks with logged hours count, on the requests in view; lines with no owner or date are left out. Query: Group request timesheet lines on open requests with tpg_total above 0 by owner and the calendar week (Monday) of createdon; count the person-weeks, people and hours in each band of the summed hours as a share of a 
- Widget 'Shaping effort against plan': Hours logged on each request against its planned SA/BA effort. Query: For open requests with tpgmea_baplannedefforts above 0, give the planned man-days, request timesheet hours, those hours as man-days of 
- Widget 'Hours by request state' Query: Sum request timesheet tpg_total by tpg_requeststatename, with the number of requests
- Widget 'Requests absorbing the most hours' Query: Rank open requests by request timesheet hours
- Widget 'Hours by dimension': Owner (SA/BA) is always filled; SD lead and CIO are on the switch.
- KPI 'Median week, of a ': Group timesheet lines on open projects with tpg_total above 0 by owner and the calendar week (Monday) of createdon; sum tpg_total per person-week; what is the median of those sums as a percentage of a 
- KPI 'Weeks over ': Group timesheet lines on open projects with tpg_total above 0 by owner and the calendar week (Monday) of createdon; how many person-weeks sum to more than 
- KPI 'Lines left out': How many timesheet lines on open projects with tpg_total above 0 have no owner or no createdon?
- KPI 'Hours logged': What is the sum of timesheet tpg_total on open projects?
- KPI 'Unapproved hours': What is the sum of timesheet tpg_total with tpgmea_timesheetstatusname Draft or Submitted on open projects?
- KPI 'Unapproved share': What share of timesheet hours on open projects is Draft or Submitted?
- KPI 'Median shaping week, of a ': Group request timesheet lines on open requests with tpg_total above 0 by owner and the calendar week (Monday) of createdon; sum tpg_total per person-week; what is the median of those sums as a percentage of a 
- KPI 'Over planned shaping effort': How many open requests with tpgmea_baplannedefforts above 0 have request timesheet tpg_total divided by 
- KPI 'Hours shaping open requests': What is the sum of request timesheet tpg_total on open requests?
- KPI 'Hours on dropped or rejected': What is the sum of request timesheet tpg_total on requests with tpg_requeststatename Dropped or Rejected?
- KPI 'Projects with unapproved hours': open projects that have timesheet hours in Draft or Submitted
- KPI 'Requests with hours': open projects that have request timesheet hours
