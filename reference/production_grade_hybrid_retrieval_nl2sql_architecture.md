# Production-Grade Hybrid Retrieval & NL2SQL Architecture

## Target Architecture Score: 9.7/10

## Application Context

The application is an **Enterprise Analytics Copilot** used for:

- Dynamic dashboards
- Reports
- Widgets
- Information generation
- Natural Language to SQL (NL2SQL)

### Core flow

```text
User Question
    ↓
Retrieve Business Context
    ↓
LLM Generates SQL
    ↓
Validate SQL
    ↓
Fetch Records from Source
    ↓
Analyze Results
    ↓
Generate Answer / Dashboard / Report / Widget / Chart
```

The existing retrieval architecture is already a strong foundation:

- Vector search
- Keyword / BM25 search
- Reciprocal Rank Fusion (RRF)
- Permission filtering
- Cross-encoder reranking
- MMR diversity selection
- Context budgets for Models, Glossary, Documents, Previous Answers, and Patterns

The goal is to enhance **relevance, search quality, LLM context, SQL accuracy, and output quality**.

---

# Recommended Production Architecture

```text
                         USER QUESTION
                               │
                               ▼
                  ┌────────────────────────┐
                  │ 1. QUERY UNDERSTANDING │
                  │ Intent                 │
                  │ Entities / Metrics     │
                  │ Dates / Filters        │
                  │ Output Type            │
                  └───────────┬────────────┘
                              │
                              ▼
                  ┌────────────────────────┐
                  │ 2. SECURITY & SCOPE    │
                  │ Tenant / RLS           │
                  │ Approved Content       │
                  │ Model & Connection     │
                  │ User Permissions       │
                  └───────────┬────────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        SEMANTIC SEARCH   KEYWORD SEARCH   METADATA SEARCH
        Meaning           BM25 / FTS       Entity / Metric /
        Embeddings                          Date / Source
              │               │                │
              └───────────────┼────────────────┘
                              ▼
                    HYBRID RANK FUSION
                     RRF + Weighted Score
                              │
                              ▼
                  PERMISSION & VERSION CHECK
                              │
                              ▼
                    CROSS-ENCODER RERANKING
                              │
                              ▼
                 DIVERSITY & CONTEXT BUDGET
                        MMR / Top-K
                              │
                              ▼
                 ┌────────────────────────┐
                 │ CONTEXT ASSEMBLY       │
                 │ Model                  │
                 │ Glossary               │
                 │ Documents              │
                 │ Previous Answers       │
                 │ Patterns               │
                 └───────────┬────────────┘
                              │
                              ▼
                      QUERY PLANNING
                              │
                              ▼
                     SQL GENERATION
                              │
                              ▼
                    SQL VALIDATION
              Schema + Permission + Safety
                              │
                              ▼
                     EXECUTE SQL
                              │
                              ▼
                   RESULT VALIDATION
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
            DATA / TABLE              CHART / REPORT
                 │                         │
                 └────────────┬────────────┘
                              ▼
                       FINAL RESPONSE
                Answer + Insight + Citation
```

---

# Key Recommendations

## 1. Query Understanding

Before retrieval, extract the business intent and query components.

Example:

**User:** `Show revenue by region for Q3 2026`

```text
Intent: Analytics
Metric: Revenue
Dimension: Region
Period: Q3 2026
Output: Chart / Table
```

Benefits:

- Improves retrieval relevance
- Improves SQL generation
- Helps select the correct output format

---

## 2. Metadata-Aware Retrieval

Do not rely only on semantic similarity.

Use extracted metadata such as:

- Entities
- Metrics
- Dimensions
- Dates
- Source
- Version

### Recommended relevance signals

```text
Final Relevance Score =
    Semantic Relevance
  + Keyword/BM25 Relevance
  + Entity/Metric Match
  + Metadata Match
  + Source Authority
  + Freshness/Version
```

This is important for NL2SQL because retrieving the wrong metric definition can produce valid SQL but the wrong business answer.

---

## 3. Field and Metric-Level Retrieval

In addition to retrieving models, retrieve:

- Fields
- Measures
- Dimensions
- Relationships
- Business definitions

Example:

```text
Model: Project

Fields:
- Project Name
- Status
- Planned Finish Date

Measures:
- Project Count
- Red Project Count

Relationships:
- Project → Risks
- Project → Issues
```

For the question `How many projects are overdue?`, retrieve:

- Project model
- Planned Finish Date
- Project Status
- Definition of Overdue

---

## 4. Dynamic Context Budgets

The current fixed budget should become intent-aware.

### Metric definition question

`What is Net Revenue?`

Priority:

```text
Glossary → Model → Documents
```

### Data / SQL question

`Show monthly revenue by region.`

Priority:

```text
Model → Relationships → Pattern → Glossary
```

### Business information question

`Why did revenue decline?`

Priority:

```text
Documents → Previous Answers → Models → Patterns
```

Use a query intent classifier to allocate context dynamically.

---

## 5. Query Planning Before SQL Generation

Avoid:

```text
Question → Context → LLM → SQL
```

Use:

```text
Question
    ↓
Query Plan
    ↓
SQL
```

Example:

**Question:** `Compare revenue for Chennai and Mumbai this quarter.`

```text
Intent: Comparison
Metric: Revenue
Dimension: City
Filters: Chennai, Mumbai
Period: Current Quarter
Aggregation: SUM
Output: Comparison Chart
```

Benefits:

- Reduces SQL hallucination
- Improves debugging
- Improves chart selection
- Enables stronger validation

---

## 6. SQL Validation Layer

Before execution, validate generated SQL.

```text
LLM Generated SQL
       │
       ▼
Schema Validation
       │
       ▼
Permission Validation
       │
       ▼
Business Rule Validation
       │
       ▼
Read-only / Safety Validation
       │
       ▼
Execute
```

Validate:

- Tables are approved
- Fields exist
- Joins are valid
- User has access
- SQL is read-only
- Query cost is acceptable

---

## 7. Controlled SQL Repair

If SQL execution fails:

```text
Generate SQL
    ↓
Validate / Execute
    ↓
Error?
    ├── No → Continue
    └── Yes
          ↓
     Retrieve Missing Context
          ↓
     Repair SQL
          ↓
     Retry (Maximum 1–2 times)
```

Avoid unlimited retries.

---

## 8. Result-Aware Visualization

Select charts after SQL execution based on the actual result shape.

| Result Shape | Recommended Output |
|---|---|
| Single number | KPI card |
| Time series | Line chart |
| Category comparison | Bar chart |
| Part-to-whole | Pie/Donut |
| Multiple dimensions | Table/Heatmap |
| Detailed records | Data table |

Use:

- Number of rows
- Data types
- Time dimensions
- Cardinality
- Aggregations

---

## 9. Answered-Question Feedback Loop

Only high-quality answers should become future retrieval examples.

```text
User Question
      ↓
Generated SQL + Result
      ↓
User Feedback / Success Signal
      ↓
Verified Good Answer
      ↓
Promote to Gold Example / Pattern
```

Promote answers only when they are:

- Successfully executed
- Validated
- High-confidence
- Optionally user-approved

---

## 10. Retrieval Confidence

Calculate confidence before generating SQL.

```text
High Confidence
→ Generate SQL

Medium Confidence
→ Generate SQL with stricter validation

Low Confidence
→ Ask for clarification or state that data is unavailable
```

Confidence signals can include:

- Top retrieval score
- Score gap between results
- Agreement between vector and keyword search
- Reranker score
- Required model/metric coverage

---

# Recommended LLM Context Structure

Do not send retrieved chunks as unstructured text.

Use structured context:

```text
USER QUESTION
"Show revenue by region for Q3 2026"

QUERY UNDERSTANDING
- Intent: Analytics
- Metric: Revenue
- Dimension: Region
- Period: Q3 2026

AUTHORIZED DATA MODELS
- Sales
- Region

METRIC DEFINITIONS
- Revenue = ...

AVAILABLE FIELDS
- transaction_date
- revenue
- region_name

JOIN RULES
- Sales.region_id → Region.id

BUSINESS RULES
- ...

RELEVANT SQL PATTERNS
- ...

GOLD EXAMPLES
- ...
```

Structured context improves SQL generation and reduces ambiguity.

---

# Architecture Intelligence Layers

The application should be viewed as three intelligence layers.

## 1. Retrieval Intelligence

Find the correct:

- Models
- Definitions
- Documents
- Previous answers
- Patterns
- Fields and metrics

## 2. Data Intelligence

Understand the business question and:

- Create a query plan
- Generate SQL
- Validate SQL
- Execute queries
- Validate results

## 3. Presentation Intelligence

Use the query results to generate:

- Reports
- Dashboards
- Widgets
- Charts
- Tables
- Business insights

---

# Final Recommendation

The most important enhancements to the existing architecture are:

> **Query Understanding → Field/Metric Retrieval → Dynamic Context Budget → Query Planning → SQL Validation → Result-Aware Visualization**

Combined with the existing:

> **Vector Search + BM25 + RRF + Permission Filter + Cross-Encoder + MMR**

this provides a strong **production-grade architecture for dynamic dashboards, reports, widgets, information generation, and NL2SQL**.

## Target Score: 9.7/10

The key principle is:

> **Retrieve the right business context, understand the user's analytical intent, generate verified SQL, validate the results, and present the information in the most useful format.**
