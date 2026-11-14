# PMO starter pack

Versioned manifest of certified PMO measures, dimensions and starter reports (FEAT-051, RULE-026..039). Built in Phase P1, stories US-037 to US-040.

Beside the manifest, drafts for STK-008 review. Indexed as knowledge with each approved model version:
`chart_patterns.yaml` (AG-07 chart conventions, US-104 / US-159), `dashboard_rules.yaml` (the dashboard rules and
the wording check's terms, US-159) and `report_blueprints.yaml` (the report catalogue's screens as layouts for the
report planner, US-159). `gold_examples.yaml` is the gold set, imported as suggestions (US-057 / US-159).

Pack 2.0.0 (US-155) takes its starter reports from `report_blueprints.yaml` (`reports_from` in the manifest): edit a
screen there and both the starter dashboards and the agents' knowledge follow. The activity entities (PCRs, status
reports, RAID, timesheets, tasks) follow their project's row scope through `inherits_scope` links.

Pack 2.1.0 (US-194): a dimension can name the question shapes it answers (`answers: [why | how_long | how_late |
when]`); the rules planner then reads "Why are projects on hold?" as on hold by hold reason.

Pack 2.2.0 (US-202, DEC-24): RULE-026 counts an open project by its current state (Project Created, Active, On
Hold), not by Status, which stays the record state. Every measure scoped to open projects follows.

Pack 2.2.1 (US-250, gold set 0.10.0): every gold example names the kind of answer it expects (`shape`), and 88
held-out `exec186` examples pair a list question ("Which projects are open?") with its count twin.

**Stored in the database (US-230, US-231).** `tela-admin load-packs` stores the manifest and every knowledge file
(`glossary`, `value_synonyms`, `dashboard_rules`, `chart_patterns`, `analysis_recipes`, `report_blueprints`,
`gold_examples`) as `model.pack_documents` of the pack version, and the app reads those stored copies, not this
folder. A released version is frozen: after changing **any** file here, bump `pack_version` in the manifest, or
`load-packs` refuses ("already released with different …; bump pack_version"). A version loaded before migration 0048
gets its documents on the next `load-packs`; until then it reads this folder.
