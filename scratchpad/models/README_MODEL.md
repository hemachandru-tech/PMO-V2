# Tela data model — how to read the raw JSON

File: `tpg-mysql-model-v8.json` — the **approved v8** model of the `tpg-mysql` connection (MySQL database `tpg`),
exported from Tela's own Postgres (`model.model_versions.definition`).

A model is Tela's "recipe book" for one database: which tables and columns exist, what they mean, how tables join,
the certified measures, the breakdowns, and the business rules. Every Ask answer is written from it.

## Get it again

```bash
# tpg-mysql, approved version
docker exec tela-postgres-1 psql -U tela_owner -d tela -t -A -c "select v.definition from model.model_versions v join model.data_models m on m.current_version_id = v.id where m.id = '01a1da62-e4f9-7242-89f6-01b97632cb8d'" | python3 -m json.tool > tpg-mysql-model.json

# ball-report, approved version
docker exec tela-postgres-1 psql -U tela_owner -d tela -t -A -c "select v.definition from model.model_versions v join model.data_models m on m.current_version_id = v.id where m.id = '01a1df90-f39f-7345-8ac2-a72455ed0a90'" | python3 -m json.tool > ball-report-model.json

# list every model, its connection and version numbers
docker exec tela-postgres-1 psql -U tela_owner -d tela -c "select m.id, c.name, v.version_no, v.status from model.data_models m join conn.connections c on c.id = m.connection_id join model.model_versions v on v.data_model_id = m.id where c.deleted_at is null order by c.name, v.version_no"
```

In the app: **Admin → Data → Connections →** click **v8** in the *Model* column (Model studio).

## What is in v8 (summary)

| Part | Count | Notes |
|---|---|---|
| `entities` (tables) | 10 | projects, project_requests, change_requests, status_reports, risks, issues, action_items, decisions, timesheets, project_tasks |
| fields (columns) | 887 | all visible; 399 have a description |
| `links` (joins) | 9 | every child table → projects; projects → project_requests |
| `measures` | 124 | certified counts / sums from the PMO starter pack |
| `dimensions` | 50 | breakdowns (region, CIO, status …) |
| `rules` | 121 | 108 checked (`ok`), 13 held for review — 61 condition, 31 expression, 9 display, 7 population |
| `settings` | `reporting_date: 2026-10-25` | the data date ("today" for overdue, ageing, next 30 days) |

> v8 was generated **before** the data profiler existed, so its fields have no `values` (vocabularies) and the empty
> or one-value columns are not hidden. Enabling `tpg-mysql` and running **Generate model** again would add them.

## Top level

```json
{
  "entities":   [ ... tables ... ],
  "links":      [ ... joins ... ],
  "measures":   [ ... certified figures ... ],
  "dimensions": [ ... breakdowns ... ],
  "rules":      [ ... business rules ... ],
  "settings":   { "reporting_date": "2026-10-25" }
}
```

## `entities` — one per table

```json
{
  "key": "projects",                      // name Tela uses inside the model
  "label": "Projects",                    // name people see
  "source_object": "tpg.tpg_project",     // the real table: schema.table
  "primary_key": ["project_id"],          // field key(s) that identify one row
  "display_fields": ["tpg_subject", ...], // record columns; the FIRST is the record's name
  "description": "One project record.",
  "grain": "One row per project",
  "fields": [ ... columns ... ]
}
```

### `fields` — one per column

| Key | Meaning |
|---|---|
| `key` | name inside the model (e.g. `project_number`) |
| `source_column` | the real column (e.g. `tpgmea_pprojectnumber`) — what the SQL uses |
| `label` | name people see |
| `data_type` | `text`, `integer`, `decimal`, `date`, `datetime`, `boolean`, `json` |
| `source_type` | how the database stores it |
| `description` | meaning; also the profiler's notes ("Identifier: …", "Always empty …", "Date stored as text in DD/MM/YYYY: …") |
| `semantic_type` | kind: `identifier`, `category`, `flag`, `json`, `text_date`, `currency`, … |
| `values` | the column's stored values when it has few (vocabulary) — e.g. `["Active","On Hold","Completed"]` |
| `is_queryable` | `false` = hidden from the LLM and the planners (empty, constant, secret or not picked) |
| `sensitivity` | `internal` or `personal` (personal data is never sampled) |

## `links` — joins between tables

```json
{
  "from_entity": "risks", "from_fields": ["project_id"],
  "to_entity": "projects", "to_fields": ["project_id"],
  "cardinality": "many_to_one",          // many risks per project
  "origin": "pack",                      // foreign_key | proposed (proven by the data) | admin | pack
  "accepted": true                       // only accepted links are used in SQL
}
```

## `measures` — certified figures

```json
{
  "key": "total_projects", "label": "Projects", "entity": "projects",
  "expression": { "agg": "count", "field": null, "where": null },   // the calculation
  "plain_rule": "Every project record.",                           // the rule in words
  "definition": "Every project record in any state.",
  "format": "integer", "status": "certified"
}
```

`expression.where` holds the filter of a measure that counts only some records (e.g. open projects).

## `dimensions` — breakdowns

```json
{ "key": "region", "label": "Region", "entity": "projects", "field": "region",
  "is_status": false, "is_temporal": false, "sort_order": null,
  "value_labels": {}, "bands": null, "not_set": null }
```

`value_labels` relabels values (e.g. `Yellow → Amber`); `bands` groups numbers into ranges; `not_set` labels blanks.

## `rules` — business rules (from your documents, through the model builder)

```json
{
  "key": "projects_open_project",
  "term": "Open project",                       // words people use
  "synonyms": ["Open projects", "All Open", "Projects"],
  "meaning": "Projects whose tpg_projectstatusname is not Completed, Dropped or On Hold ...",
  "entity": "projects",                         // table it applies to
  "sql": "`tpg_projectstatusname` NOT IN ('Completed','Dropped','On Hold') OR `tpg_projectstatusname` IS NULL",
  "kind": "population",                         // see below
  "always": false,                              // true = applied to every query on that table
  "origin": "document",                         // document | inferred | admin
  "cite": "3. What 'open' means",               // section of the source document
  "status": "ok",                               // ok = checked and used; needs_review = never used in answers
  "issue": null                                 // why it needs review (unknown column, value not in data ...)
}
```

| `kind` | How Ask uses it |
|---|---|
| `condition` | a filter that defines a term ("Critical path" → `tpg_iscriticalname = 'Yes'`) |
| `expression` | a calculation or the column a term stands for ("Slip" → `DATEDIFF(...)`) |
| `population` | the default records when a question names none ("projects" → open projects) |
| `display` | how a column's values are shown ("Amber" → `CASE WHEN ... = 'Yellow' THEN 'Amber' ...`) |

`{data_date}` inside a rule's `sql` is replaced by `settings.reporting_date` when the SQL is written.

## Useful one-liners

```bash
# every checked rule: table, kind, term, condition
python3 -c "import json;d=json.load(open('tpg-mysql-model-v8.json'));[print(r['entity'],'|',r.get('kind'),'|',r['term'],'|',r['sql']) for r in d['rules'] if r['status']=='ok']"

# the columns of one table with their descriptions
python3 -c "import json;d=json.load(open('tpg-mysql-model-v8.json'));e=[x for x in d['entities'] if x['key']=='projects'][0];[print(f['source_column'],'|',f.get('description')) for f in e['fields'] if f.get('is_queryable',True)]"

# rules held for review and why
python3 -c "import json;d=json.load(open('tpg-mysql-model-v8.json'));[print(r['term'],'|',r['issue']) for r in d['rules'] if r['status']!='ok']"
```
