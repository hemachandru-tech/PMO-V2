# Data Dictionary & Entity Catalog

| Field | Value |
|---|---|
| Project | Tela (NL2SQL Business Intelligence & Reporting Platform) |
| Document status | Approved |
| Version | 2.0 |
| Last updated | 2026-10-20 |
| Evidence level | L5 – Operational (Code Dataclasses, JSON Stores, Database Schema Files, Domain Cheat Sheets) |
| Sources analyzed | `backend/app/api/stores.py`, `backend/app/semantic_layer.py`, `payments.txt`, `backend/data/api/*.json` |
| Related documents | [Architecture](architecture.md) · [Project Summary](project_summary.md) · [Requirements](requirements.md) · [Business Rules](business_rules.md) · [API Catalog](api_catalog.md) · [Risk Register](risk_register.md) |

---

## 1. Overview and Data Governance

This catalog defines the data models, entity relationships, attributes, and storage policies governing Tela. The system maintains two distinct categories of data:
1. **Platform Runtime State:** Application configuration, user accounts, sessions, access grants, saved queries, dashboards, and audit logs persisted as thread-safe JSON files in `backend/data/api/`.
2. **Target Database Domain Entities:** Analytics databases queried by the platform (exemplified by the Payments Core Banking domain and FlexCap Supply Chain Finance domain).

### Data Classification Tiers
- **Public:** UI labels, chart templates, and dialect keywords.
- **Internal:** System metrics, table names, column data types, and widget configurations.
- **Confidential:** Business glossary definitions, few-shot gold SQL examples, and database descriptors.
- **Sensitive / Restricted:** Passwords, connection strings, API keys, user session tokens, and row-level financial transaction records.

---

## 2. Platform Runtime State Entities (`DATA-001` to `DATA-015`)

### DATA-001: User Record (`users.json`)
- **Entity Description:** Represents an authenticated user profile in the system.
- **Storage Location:** `backend/data/api/users.json`
- **Ownership:** `UserStore` (`app/api/stores.py`)
- **Retention:** Indefinite until deleted by admin.
- **Classification:** Confidential / Sensitive (Hashed credentials).

| Attribute | Type | Required? | Constraints / Allowed Values | Description |
|---|---|---|---|---|
| `user_id` | String | Yes | PK, Unique, format: `usr-<uuid>` | Unique identifier for the user. |
| `username` | String | Yes | Unique, alphanumeric, 3–50 chars | Login identifier. |
| `password_hash`| String | Yes | Bcrypt hash (60 chars) | Salted bcrypt hash of user password. |
| `role` | String | Yes | `admin` \| `user` | Security role controlling endpoint access. |
| `persona` | String | No | References `Role.id` (e.g. `leader`) | Job role defining capabilities and UI home order. |
| `tenant_id` | String | Yes | Default: `default` | Multi-tenant organization grouping. |
| `default_connection_id` | String | No | Valid `connection_id` | Default database connection bound to this user. |
| `home_dashboard_id` | String | No | Valid `dashboard_id` | Initial landing dashboard displayed on login. |
| `attributes` | Dict[str, Any]| No | Max 20 keys, string or list[str] | User attributes used for dynamic row-rule substitution. |
| `created_at` | String | Yes | ISO 8601 Timestamp | Record creation timestamp. |
| `is_active` | Boolean | Yes | Default: `true` | Indicates whether account can authenticate. |

---

### DATA-002: Session Token (`SessionStore`)
- **Entity Description:** Ephemeral user session mapped from HTTP-only cookies.
- **Storage Location:** In-memory with optional JSON backup.
- **Ownership:** `SessionStore`
- **Retention:** 24-hour sliding TTL.
- **Classification:** Sensitive / Restricted.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `session_id` | String | Yes | PK, 64-character cryptographically secure token. |
| `user_id` | String | Yes | Foreign Key referencing `User.user_id`. |
| `created_at` | String | Yes | Session initiation timestamp. |
| `expires_at` | String | Yes | Session expiration timestamp. |

---

### DATA-003: Role & Capability Specification (`roles.json`)
- **Entity Description:** Configurable job role definitions providing UI navigation order, grant templates, and attribute schemas.
- **Storage Location:** `backend/data/api/roles.json`
- **Ownership:** `RoleStore`
- **Default Seed Roles:** `Leader`, `Manager`, `Governance`, `Finance`, `Admin`.
- **Classification:** Internal.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `id` | String | Yes | PK, Unique identifier (e.g., `governance`). |
| `name` | String | Yes | Display label (e.g., "Risk & Governance"). |
| `capabilities` | List[str] | Yes | Enabled actions (`ask`, `explore`, `dashboards`, `export`). |
| `grant_template` | Dict[str, Any]| No | Baseline connection/table grants applied to this role. |
| `attribute_names` | List[str] | Yes | Names of allowed dynamic attributes (e.g. `["region", "org_id"]`). |
| `home_order` | Integer | Yes | Sorting order in navigation. |

---

### DATA-004: Per-User Access Grant (`grants.json`)
- **Entity Description:** Defines database connection, table, column, and row-level access permissions for each user.
- **Storage Location:** `backend/data/api/grants.json` (File permissions 0600).
- **Ownership:** `GrantStore`
- **Classification:** Confidential / Sensitive.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `user_id` | String | Yes | PK, references `User.user_id`. |
| `grants` | Dict[str, Any] | Yes | Map of `connection_id` -> `ConnectionGrant`. |
| `ConnectionGrant.tables` | Dict[str, TableGrant] | Yes | Map of table name -> `TableGrant`. |
| `TableGrant.columns` | List[str] | Yes | Explicit allow-list of visible columns. |
| `TableGrant.row_filter` | List[Condition] | No | Structured conditions `{column, op, value}` for AST rewriting. |

---

### DATA-005: Saved Database Connection (`connection.json`)
- **Entity Description:** Multi-engine connection configurations, credentials, and non-secret descriptors.
- **Storage Location:** `backend/data/api/connection.json` (File permissions 0600).
- **Ownership:** `ConnectionStore`
- **Classification:** Sensitive / Restricted (Holds DB passwords and SSH keys).

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `connection_id` | String | Yes | PK, stable hash: `engine:host:port:database`. |
| `name` | String | Yes | Human-readable alias (e.g., "Production ClickHouse"). |
| `engine` | String | Yes | `postgres` \| `mysql` \| `clickhouse` \| `excel`. |
| `descriptor` | Dict[str, Any] | Yes | Non-secret metadata returned to UI clients. |
| `config` | Dict[str, Any] | Yes | Full configuration including credentials (server-only). |
| `is_active` | Boolean | Yes | Flag indicating if this is the active global connection. |
| `needs_reconnect` | Boolean | Yes | Flag indicating connection failed on startup restore. |

---

### DATA-006: Semantic Layer Store (`semantic_layers.json`)
- **Entity Description:** Curated metadata bridge connecting database schemas to the LLM planner.
- **Storage Location:** `backend/data/api/semantic_layers.json`
- **Ownership:** `SemanticLayerStore`
- **Classification:** Confidential.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `connection_id` | String | Yes | PK, references `Connection.connection_id`. |
| `tables` | Dict[str, TableMeta] | Yes | Map of table name -> Table metadata. |
| `TableMeta.columns` | Dict[str, ColMeta] | Yes | Column types, business names, synonyms, descriptions. |
| `TableMeta.approved` | Boolean | Yes | Controls visibility to the LLM planner. |
| `TableMeta.foreign_keys` | List[ForeignKey] | Yes | Verified structural relationships between tables. |
| `business_notes` | List[BusinessNote] | No | Binding domain definitions and instructions. |
| `examples` | List[GoldExample] | No | Verified natural language -> SQL few-shot pairs. |

---

### DATA-007: Widget Specification (`widgets.json`)
- **Entity Description:** Pinned query, visualization settings, and filter bindings.
- **Storage Location:** `backend/data/api/widgets.json`
- **Ownership:** `WidgetStore`
- **Classification:** Internal.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `widget_id` | String | Yes | PK, Unique identifier: `wid-<uuid>`. |
| `title` | String | Yes | Widget display title. |
| `connection_id` | String | Yes | Target database connection identifier. |
| `query_definition.sql` | String | Yes | Original executed SQL statement. |
| `query_definition.question` | String | Yes | Natural language prompt that created the query. |
| `viz_config` | Dict[str, Any] | Yes | Chart type, x/y axis bindings, Vega-Lite spec. |
| `filter_schema` | List[Dict] | Yes | Bound filter parameters (categorical, date range). |
| `cached_columns` | List[Dict] | No | Snapshot of output columns captured at creation. |
| `cached_rows` | List[Dict] | No | Snapshot of output rows captured under author scope. |
| `last_refreshed` | String | Yes | ISO 8601 timestamp of last query execution. |

---

### DATA-008: Dashboard Specification (`dashboards.json`, `custom_dashboards.json`)
- **Entity Description:** Multi-panel dashboard definitions, grid layouts, and shared global filters.
- **Storage Location:** `backend/data/api/dashboards.json` & `custom_dashboards.json`
- **Ownership:** `DashboardStore` & `CustomDashboardStore`
- **Classification:** Internal.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `dashboard_id` | String | Yes | PK, Unique identifier: `dash-<uuid>`. |
| `title` | String | Yes | Dashboard title. |
| `connection_id` | String | Yes | Bound database connection identifier. |
| `layout` | List[GridItem] | Yes | Responsive grid coordinates (`x`, `y`, `w`, `h`, `i`). |
| `panels` | List[Panel] | Yes | Panel definitions containing SQL, chart specs, and filters. |
| `filters` | List[Filter] | Yes | Global filters (date range, categorical dropdowns). |
| `created_by` | String | Yes | `User.user_id` of author. |
| `who_can_view` | List[str] | No | User IDs authorized to view dashboard (share scope). |

---

### DATA-009: Conversation & Turn History (`conversations.json`)
- **Entity Description:** Per-user conversational chat history, questions, generated SQL, and traces.
- **Storage Location:** `backend/data/api/conversations.json`
- **Ownership:** `ConversationStore`
- **Classification:** Confidential (User query text).

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `conversation_id` | String | Yes | PK, Unique conversation UUID. |
| `user_id` | String | Yes | FK referencing `User.user_id`. |
| `connection_id` | String | Yes | Database connection context. |
| `turns` | List[TurnRecord]| Yes | Chronological sequence of turns in the conversation. |
| `TurnRecord.question` | String | Yes | User input query. |
| `TurnRecord.sql` | String | No | Executed SQL query (null in explore mode). |
| `TurnRecord.trace` | List[TraceEvent] | Yes | Stage-by-stage execution trace events. |

---

### DATA-010: LLM Usage & Cost Ledger (`usage.json`)
- **Entity Description:** Durable ledger tracking prompt tokens, completion tokens, call counts, and estimated financial costs per provider and model.
- **Storage Location:** `backend/data/api/usage.json`
- **Ownership:** `UsageStore`
- **Classification:** Internal.

| Attribute | Type | Required? | Description |
|---|---|---|---|
| `total_tokens` | Integer | Yes | Cumulative token volume processed. |
| `prompt_tokens` | Integer | Yes | Cumulative input prompt tokens. |
| `completion_tokens` | Integer | Yes | Cumulative output completion tokens. |
| `estimated_cost_usd` | Float | Yes | Calculated financial expenditure based on model rates. |
| `by_model` | Dict[str, Any] | Yes | Breakdown of token counts and costs by model identifier. |

---

## 3. Payments Domain Data Entities

This section documents the canonical physical entities of the Payments Core Banking domain (`payments.txt`), including cardinalities, keys, and constraints.

```
 [ organization (issuer) ] ◄── [ card_product ] ◄── [ card ] ◄── [ financial_transaction ]
                                                                       │
           ┌───────────────────────────────┬───────────────────────────┼───────────────────────────┐
           ▼                               ▼                           ▼                           ▼
[ authorization_message ]       [ clearing_message ]          [ transaction_fee ]          [ settlement_line ]
       (1:N)                           (1:N)                       (1:N)                       (1:N)
```

### DATA-011: Financial Transaction (`financial_transaction`)
- **Domain Role:** Aggregate Root Fact Table (Grain = 1 row per transaction).
- **Classification:** Confidential / Financial Transaction Record.

| Column | Physical Type | Primary Key? | Foreign Key? | Nullable? | Description |
|---|---|---|---|---|---|
| `financial_txn_id` | VARCHAR(64) | Yes | No | No | Unique transaction identifier. |
| `transaction_reference` | VARCHAR(128)| No | No | No | External reference / scheme RRN. |
| `merchant_id` | VARCHAR(64) | No | Yes (`merchant.merchant_id`) | No | Merchant receiving transaction. |
| `outlet_id` | VARCHAR(64) | No | Yes (`merchant_outlet.outlet_id`) | No | Physical or virtual merchant outlet. |
| `terminal_id` | VARCHAR(64) | No | Yes (`terminal.terminal_id`) | Yes | POS terminal identifier. |
| `card_id` | VARCHAR(64) | No | Yes (`card.card_id`) | No | Card used in transaction. |
| `issuer_org_id` | VARCHAR(64) | No | Yes (`organization.organization_id`)| No | Direct link to issuing bank entity. |
| `amount` | DECIMAL(18,2) | No | No | No | Gross transaction monetary amount. |
| `currency` | VARCHAR(3) | No | No | No | ISO 4217 Currency Code (AED, ZAR, USD). |
| `status` | VARCHAR(32) | No | No | No | `AUTHORIZED`, `CAPTURED`, `SETTLED`, `FAILED`. |
| `txn_timestamp` | TIMESTAMP | No | No | No | Transaction authorization timestamp. |
| `settled_at` | TIMESTAMP | No | No | Yes | Final interbank settlement timestamp. |

---

### DATA-012: Transaction Fee Component (`transaction_fee`)
- **Domain Role:** Fee Component Child Fact Table (Grain = 1:N per `financial_transaction`).
- **Classification:** Confidential / Commercial Fee Split.

| Column | Physical Type | Primary Key? | Foreign Key? | Nullable? | Description |
|---|---|---|---|---|---|
| `fee_id` | VARCHAR(64) | Yes | No | No | Unique fee entry identifier. |
| `financial_txn_id` | VARCHAR(64) | No | Yes (`financial_transaction`) | No | Parent transaction identifier. |
| `fee_type` | VARCHAR(32) | No | No | No | `MDR`, `INTERCHANGE`, `SCHEME_FEE`. |
| `fee_amount` | DECIMAL(18,4) | No | No | No | Computed fee amount in settlement currency. |
| `currency` | VARCHAR(3) | No | No | No | Fee currency code. |

---

### DATA-013: Settlement Line (`settlement_line`)
- **Domain Role:** Batch Settlement Line Item (Grain = 1:N per `financial_transaction`).
- **Classification:** Confidential / Banking Settlement Record.

| Column | Physical Type | Primary Key? | Foreign Key? | Nullable? | Description |
|---|---|---|---|---|---|
| `settlement_line_id` | VARCHAR(64) | Yes | No | No | Unique settlement line identifier. |
| `financial_txn_id` | VARCHAR(64) | No | Yes (`financial_transaction`) | No | Reconciled transaction identifier. |
| `settlement_batch_id`| VARCHAR(64) | No | Yes (`settlement_batch.batch_id`)| No | Settlement batch identifier. |
| `net_amount` | DECIMAL(18,2) | No | No | No | Net payable or receivable amount. |
| `settlement_status` | VARCHAR(32) | No | No | No | `PENDING`, `SETTLED`, `REVERSED`. |

---

### DATA-014: Merchant Master (`merchant`)
- **Domain Role:** Merchant Dimension Table (Grain = 1 row per merchant).
- **Classification:** Confidential / Corporate Merchant Identity.

| Column | Physical Type | Primary Key? | Foreign Key? | Nullable? | Description |
|---|---|---|---|---|---|
| `merchant_id` | VARCHAR(64) | Yes | No | No | Unique merchant entity identifier. |
| `legal_name` | VARCHAR(255) | No | No | No | Registered legal corporate name. |
| `trade_name` | VARCHAR(255) | No | No | No | DBA trade name. |
| `mcc_code` | VARCHAR(4) | No | Yes (`mcc.mcc_code`) | No | Merchant Category Code (ISO 18245). |
| `onboarding_status` | VARCHAR(32) | No | No | No | `ACTIVE`, `SUSPENDED`, `UNDERWRITING`. |

---

### DATA-015: Card and Product Master (`card`, `card_product`)
- **Domain Role:** Cardholder & Product Dimension Tables.
- **Classification:** Sensitive / PCI DSS Governed (Card numbers must be masked/tokenized).

| Column | Physical Type | Primary Key? | Foreign Key? | Nullable? | Description |
|---|---|---|---|---|---|
| `card_id` | VARCHAR(64) | Yes | No | No | Internal card token identifier. |
| `card_product_id` | VARCHAR(64) | No | Yes (`card_product`) | No | Associated product tier (e.g. Platinum). |
| `masked_pan` | VARCHAR(19) | No | No | No | Masked Primary Account Number (e.g. 411111******1111). |
| `bin` | VARCHAR(8) | No | Yes (`bin.bin_number`) | No | Bank Identification Number. |
| `expiry_date` | VARCHAR(5) | No | No | No | Expiration string (MM/YY). |
