# US-059 spike: compiling Query Specs to MongoDB pipelines

| | |
|---|---|
| Story | US-059 (spike), folded into US-112 by the user's choice on 2026-10-22 |
| Decision | **Go**: `MongoCompiler` ships in US-112 (`services/api/src/tela_api/query/mongo.py`) |
| Evidence | `services/api/tests/test_dialect_parity.py` (MongoDB vs PostgreSQL), `services/connector-runner/tests/test_mongo_adapter.py`, `tests/smoke/test_p3_sources.py` |
| Sample | The synthetic TPG extract as documents (MongoDB 7.0): 100 projects, 60 requests, dates as BSON dates, money as Decimal128, NULLs as missing fields, a nested `team` sub-document and a `milestones` array |

## Questions and findings

**1. Do pipelines give the same answers as SQL?** Yes, on everything the PMO pack and the spec language
express. The parity suite compiles 68 shapes (every pack measure alone, by region and by month over a
relative range; filters; an absolute date range; a personal grouping and a band; top N with a tie-breaking
sort; a measure through a fanning-out link) under two scopes, plus a record page, a record count, scoped
filter values and a freshness maximum, and compares each result with PostgreSQL's. All 136 + 4 results
are equal. What it took:

- **Scope first.** The root collection is read through `{$match: <scope>}` then `{$project: <visible
  fields>}`, so hidden fields never travel further. Every `$lookup` carries a nested pipeline that starts the
  same way for the linked collection. The API re-parses the statement and checks this independently of the
  compiler (`assert_mongo_scoped`), and the runner's guard checks the shape again.
- **Grain (RULE-014).** A link that fans out is `$unwind`-ed, then regrouped on the root key with the
  breakdown values (`$first` of the measure inputs and condition flags), then aggregated: the pipeline
  equivalent of the SQL `tela_base`. Counts count distinct roots; sums add each root once per group.
- **SQL NULL semantics** had to be written out: a missing field is NULL (`$ifNull`); conditions use
  three-valued logic (otherwise `x != 'A'` and `NOT (...)` match NULLs); `SUM` over no values is NULL
  (a parallel count); `!=` and `not in` filters exclude NULLs; a figure over no rows returns one row
  (count 0); NULLs sort last ascending and first descending.
- **Values are data.** Every value inside an aggregation expression is wrapped in `$literal`; a filter
  value such as `"$secret"` is never read as a field path.

**2. `$lookup` performance.** With MongoDB ≥ 5.0 the compiler uses `localField`/`foreignField` *and* a
`pipeline` (where the scope lives), so the join can use an index on the foreign field. On the sample the
whole parity run (136 pipelines, two of them with `$lookup`) takes 1.6 s, including the runner's guard.
The sample is too small to say more; the PMO pack needs one many-to-one link. Recommend an index on every
approved link's foreign field, and re-measure on the pilot's real collection sizes (follow-up).

**3. Stage allow-list.** Allowed anywhere: `$match $project $addFields $set $group $sort $limit $skip
$lookup $unwind $count $facet $bucket $bucketAuto $replaceRoot`. Refused anywhere, including nested
pipelines: `$out $merge $function $accumulator $where $planCacheSetFilter`, and stages that read other
collections or server state (`$unionWith $graphLookup $currentOp $collStats $indexStats $documents
$changeStream $search ...`). A `$lookup` without a pipeline, to an ungoverned collection or across
databases is refused; every pipeline and nested pipeline must start with `$match`.

**4. Introspection by sampling.** The first 200 documents by `_id` (deterministic, so a re-read gives the
same draft), in memory only: dotted field paths for sub-documents, BSON types, presence ratio
(`nullable` when below 1), mixed types flagged (`int`+`double` widen to a number instead). An array of
sub-documents becomes a child entity `db.collection#array` keyed by `_parent_id` and `_index`, read with
`$unwind` + `$replaceRoot` after the parent's scope. Arrays of scalars stay a field flagged `array`.

**5. Read-only.** MongoDB has no read-only session switch. The layers are: `connectionStatus` with
privileges must list only the actions of the built-in `read` role (`find`, `listCollections`,
`listIndexes`, `collStats`, `dbStats`, `changeStream`, `dbHash`, `killCursors`, `listSearchIndexes`,
`planCacheRead`, checked against MongoDB 7's `rolesInfo`); the adapter only ever calls `aggregate`; the
guard refuses `$out` and `$merge`. Credentials never sit in the connection string.

## Limits accepted for P3

- Multi-currency measures (a currency breakdown added automatically) are refused on MongoDB for now.
- Links *to* an array entity are refused; an array entity can be the root of a question.
- Schema drift is detected from the sample: a field absent from the first 200 documents is "missing".
- Custom SQL is not offered on MongoDB (MSG-018); there are no custom pipelines.

## Follow-ups

- Measure `$lookup` and `$unwind` timings on pilot-sized collections; add the index advice to the
  connection checklist.
- Let an Admin widen the sample (or pick a filter) for collections whose first documents are unusual.
