Viewed service.go:265-320
Viewed service.go:190-265
Viewed service.go:100-189
Viewed service.go:320-380
Viewed generated_query.go:220-260
Viewed generated_query.go:500-560
Searched for "production"
Searched for "mask"
Viewed server.py:30-73
Searched for "query_result_mask"
Viewed query_result_mask.go:1-60

In **TelaKnowledge**, guardrails are not an abstract prompt instruction or an external specification file. They are **implemented in the Go microservices, Python workers, and React client code** across the repository.

Here is the concrete breakdown of how this project enforces guardrails directly in its codebase:

---

### 1. Model Gateway & LLM Safety Gates
**Primary Implementation:** [`source/services/model-gateway/internal/application/service.go`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/services/model-gateway/internal/application/service.go)

* **Output Content Safety Check (`safe(result)` at line 344):**
  Scans LLM output text and structured findings. Any response containing secrets or prompt injection patterns is immediately invalidated:
  ```go
  for _, blocked := range []string{"password=", "api_key=", "private key", "ignore previous instructions"} {
      if strings.Contains(combined, blocked) {
          return false
      }
  }
  ```
* **Mandatory Evidence Citation Coverage (`validateCitations` at line 287):**
  - Requires model answers to achieve a minimum citation coverage threshold (typically `≥ 0.70` or 70%).
  - Matches returned citation IDs and URIs strictly against the immutable `input.Evidence` set provided to the model.
* **Deterministic Grounded Fallback (`deterministicFallback` at line 353):**
  - If an LLM response fails validation (safety, structured format, budget, or citation coverage), the raw LLM output is discarded (`withoutAnswer`).
  - The gateway returns a deterministic, grounded fallback constructed solely from verified evidence summaries and citations.
* **Zero Prompt/Answer Data in Logs (`logInvocation` at line 225):**
  - Logs capture only metadata (token usage, latency, provider, status, tenant ID).
  - Prompts, raw evidence, and answer content are strictly barred from the log streams.

---

### 2. Database Driver & SQL AST Guardrails
**Primary Implementation:** [`source/workers/integration-connectors/generated_query.go`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/workers/integration-connectors/generated_query.go)

* **Enforced Read-Only Transactions (Line 512):**
  Every database query execution must begin with a read-only transaction:
  ```go
  tx, err := db.BeginTx(queryContext, &sql.TxOptions{ReadOnly: true})
  if err != nil {
      return failedStage("generated_read_only_sql", "read_only_denied", ...)
  }
  ```
* **Schema Checksum Verification (Line 518):**
  Before executing any plan, the runner reads the live catalog from the target database. If `catalogue.Checksum != task.SchemaChecksum`, execution is halted immediately (`schema_identity_changed`).
* **Typed Plan Compilation (Lines 220–260):**
  Queries are never passed as arbitrary raw SQL strings. Instead, they are compiled from a strongly typed AST plan (`task.Plan`) that validates column existence and projection aliases against the authorized schema.
* **Signed Statement Checksum & Row Limits (Lines 522, 551):**
  - The compiled SQL text must match the cryptographic `ExpectedStatementChecksum` signed by the gateway.
  - Queries are capped to strict row budgets (`profile.MaxRows`, `task.Plan.Limit`); exceeding the limit instantly aborts execution with `result_row_limit`.

---

### 3. PII, Credential & Payment Masking
**Primary Implementations:**
* [`source/workers/integration-connectors/query_result_mask.go`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/workers/integration-connectors/query_result_mask.go)
* [`source/workers/ingestion-normalizer/server.py`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/workers/ingestion-normalizer/server.py)

* **Database Cell Masking (`maskCertifiedCell`):**
  - **Secret-named columns** (`password`, `otp`, `token`, `secret`, `pin`, `cvv`, `apikey`, `credential`, `privatekey`) are unconditionally replaced with `[REDACTED]`.
  - **Email addresses** are masked to `v***@example.com`.
  - **Phone/Contact numbers** retain only the last 4 digits (`+91******3210`).
* **Ingestion Document Masking (`mask_sensitive` in `server.py`):**
  - **Card Data:** Uses the **Luhn algorithm check** (`luhn_valid`) and digit block validation (`payment_like`) to detect credit cards and redact them to `[REDACTED_PAYMENT_DATA]`.
  - **Email Addresses:** Masked to `[REDACTED_EMAIL]`.
  - **Structured Depth Limits:** JSON payload masking enforces a safe recursion depth (`mask_structured`) to prevent stack overflow attacks.

---

### 4. Production Intent Blocking
**Primary Implementation:** [`source/services/triage-api/internal/domain/commands.go`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/services/triage-api/internal/domain/commands.go)

* **Recursive Production Intent Interception (`HasProductionIntent` at line 116):**
  - Triage commands inspect request payloads recursively at any depth.
  - Any command containing `productionAction: true`, `productionExecution: true`, or mutation SQL is rejected with `ErrForbidden`.
* **Bounded Self-Heal Scripts:**
  - Self-heal scripts are restricted to pre-registered, approved recovery scripts (≤40 lines, ≤1,400 bytes).
  - Leases are never auto-renewed and require explicit maker-checker authorization.

---

### 5. Multi-Agent & Knowledge Governance
* **Causal Agreement Rule ([`causal_agreement.go`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/services/agent-orchestrator/internal/projection/causal_agreement.go)):**
  The `agent-orchestrator` will only promote an incident root cause to "Established" when logged errors, source code pointers, and database records independently agree.
* **Knowledge Review Lifecycle ([`knowledge-api`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/services/knowledge-api)):**
  All newly ingested documentation and generated screen descriptions default to `pending_review`. Retrieval queries strictly filter for approved versions.
* **Frontend Guarantees ([`AssistApp.tsx`](file:///Users/sathishkumarnewton/Documents/GitHub/TelaKnowledge/source/apps/assist/src/app/AssistApp.tsx#L2307-L2311)):**
  The user interface explicitly locks user guarantees:
  1. *Sources stay traceable*
  2. *Evidence gaps stay visible*
  3. *Production actions stay blocked*