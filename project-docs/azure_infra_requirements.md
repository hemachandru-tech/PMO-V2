# Tela — Azure Infrastructure Requirements

| Field | Value |
|---|---|
| Version | 0.1 (draft for client review) |
| Date | 2026-11-03 |
| Target cloud | Microsoft Azure (client subscription) |
| LLM | Azure AI Foundry (Azure OpenAI models); see §4 on Copilot |
| Sizing basis | NFR-016: 200 concurrent viewers and 20 concurrent chat sessions per tenant |
| Related | [architecture.md](architecture.md) · [technical_document.md](technical_document.md) · [requirements.md](requirements.md) (NFR-004 – NFR-027) · task.md EPIC-HD US-121 – US-126 (US-122 Azure profile) |

---

## 1. Summary

Tela is a set of Docker services: web (React SPA on nginx), API (FastAPI), LLM gateway, connector runner, Celery
worker and beat, Keycloak (sign-in broker to Entra ID), OpenBao (secrets), PostgreSQL 16 with pgvector, Redis and
an S3-compatible object store. On Azure we run the stateless services on compute we manage and move state to
Azure PaaS where Tela already supports it.

**Recommended go-live pattern (Option A).** One or two Linux VMs running the tested Docker Compose stack, behind
Azure Application Gateway (WAF v2), with Azure Database for PostgreSQL Flexible Server, Azure Cache for Redis,
Azure Key Vault (to auto-unseal OpenBao and hold the bootstrap secrets) and Azure AI Foundry for models. This is the
same topology we run today (`deploy/compose/compose.prod.yaml`), so it carries the least delivery risk.

**Scale-out pattern (Option B, later).** AKS with the Helm chart (US-121, not built yet) once load or availability
targets need more than two VMs.

| Item | What the client provides (§2) | What Prodian delivers |
|---|---|---|
| Azure subscription, network, DNS, certificates | ✔ | Bicep/Terraform templates and runbook |
| Entra ID app registration and groups | ✔ (admin consent) | Keycloak realm, group-to-role mapping |
| Azure AI Foundry resource, model deployments, quota | ✔ (or Prodian deploys with Contributor) | Model choice, gateway config, accuracy run |
| Read-only access to source systems (Dataverse, SQL, etc.) | ✔ | Connections, data model, scope policies |
| Application images, deployment, monitoring | — | ✔ |

---

## 2. What we need from the client (application point of view)

### 2.1 Azure subscription and access

| # | Requirement | Notes |
|---|---|---|
| A-01 | A dedicated subscription, or a resource group per environment: `rg-tela-prod`, `rg-tela-uat` (dev optional) | NFR-020: separate identities, secrets and config per environment |
| A-02 | Azure region | Pick the region that meets the client's data-residency policy and has the Foundry models in §4.3 available (e.g. Central India / South India, UAE North, West Europe, East US 2). Foundry model region must match the residency rule too. |
| A-03 | Prodian deployment identity: **Contributor** + **User Access Administrator** (or Role Based Access Control Administrator restricted to the RG) on each Tela resource group | Needed to create managed identities and assign roles (Key Vault, ACR pull, Foundry). Can be time-boxed with PIM. |
| A-04 | Azure Policy exceptions, if the landing zone blocks: public IP on App Gateway, `Microsoft.CognitiveServices` resources, VM extensions | Tell us the policies up front |
| A-05 | Cost-centre tags and budget alerts the client wants applied | |
| A-06 | Azure Container Registry: client-owned ACR or permission to create one in the RG | Prodian pushes signed, versioned images (`tela-api`, `tela-web`, `tela-worker`, `tela-llm-gateway`, `tela-connector-runner`, `tela-migrate`) |

### 2.2 Networking

| # | Requirement | Notes |
|---|---|---|
| N-01 | A VNet (or spoke in the client hub) with an address space of at least /24 | Subnets: `snet-appgw` (/27), `snet-app` (/27), `snet-data` (/28, PostgreSQL delegated), `snet-pe` (/27 private endpoints) |
| N-02 | Private endpoints / VNet integration for PostgreSQL, Redis, Key Vault, Storage, ACR and Foundry; private DNS zones linked to the VNet | If the client runs central private DNS in the hub, they create the A records or grant us rights |
| N-03 | **Connectivity to every data source** Tela will read: peering, VPN or ExpressRoute to on-prem/other VNets; SQL/MySQL/PostgreSQL/MongoDB ports open from `snet-app` only | Connector runner is the only component that talks to sources |
| N-04 | **Static egress IP** (NAT Gateway on `snet-app`) and the client allow-lists that IP on: source DB firewalls, Dataverse/Power Platform IP firewall (if enabled), Foundry network rules (if public) | Required for Dataverse TDS (port 1433 to `*.crm*.dynamics.com`) and any SaaS source |
| N-05 | Outbound HTTPS from `snet-app` to: `login.microsoftonline.com`, `graph.microsoft.com`, the Dataverse environment URL, the Foundry endpoint, ACR, Azure Monitor | Through the client firewall if forced tunnelling is used — we supply the FQDN list |
| N-06 | Inbound: HTTPS 443 from the client's user networks (internet or corporate-only) to Application Gateway | Decide public vs internal-only front end |
| N-07 | Optional: SSH tunnel targets — if a source is only reachable through a bastion host, the bastion host name, port and a key pair | Tela supports SSH tunnels per connection (ENH-003/004) |

### 2.3 DNS and TLS

| # | Requirement | Notes |
|---|---|---|
| D-01 | Two host names, e.g. `tela.<client-domain>` (application) and `tela-auth.<client-domain>` (Keycloak sign-in) | Same pattern as today's `telainsight` / `telaauth` |
| D-02 | TLS certificates for both names (client CA or public CA), uploaded to Key Vault, or permission to use App Gateway managed certificates | TLS 1.2+ (NFR-014) |
| D-03 | DNS records pointing both names at Application Gateway | |

### 2.4 Identity — Microsoft Entra ID

| # | Requirement | Notes |
|---|---|---|
| I-01 | An **app registration** (confidential client) for Tela sign-in: redirect URI `https://tela-auth.<domain>/realms/tela/broker/entra/endpoint`; a client secret or certificate; **groups claim** in the ID token (security groups, object IDs) | Keycloak brokers Entra (NFR-004; REQ-001). Secret goes into Key Vault/OpenBao, never email |
| I-02 | **Tenant admin consent** for `openid`, `profile`, `email`, `User.Read`; `GroupMember.Read.All` only if group overage (> 200 groups per user) applies | |
| I-03 | Four Entra security groups and their **object IDs**: `TELA-Admins`, `TELA-Creators`, `TELA-Viewers`, `TELA-CIO` (plus any business-unit groups used for row scope) | Passed to `tela-admin bootstrap-tenant` |
| I-04 | Optional: SCIM provisioning from an Entra Enterprise Application (US-118/119) | Removes access on leaver within the provisioning cycle |
| I-05 | Conditional Access: confirm MFA / compliant-device policies apply to the Tela app | |
| I-06 | Named client contacts: Entra admin, network admin, DBA per source, data owner per source | |

### 2.5 Data sources (read-only)

Tela never writes to, copies or changes a source (REQ-012). For each source:

| Source | What we need |
|---|---|
| **Microsoft Dataverse** | Environment URL; **TDS endpoint enabled** in Power Platform admin; an **application user** (Entra app registration + secret/cert) with a **read-only custom security role** on the tables Tela models; Power Platform IP firewall allow-list of the Tela egress IP (if enabled) |
| SQL Server / Azure SQL | Host, port, database; SQL login or Entra principal with `db_datareader` on the needed schemas only; TLS on |
| MySQL / PostgreSQL | Host, port, database; a user with `SELECT` only on the needed schemas; TLS on (`sslmode=require`) |
| MongoDB | Connection string; a user with `read` role on the needed database |
| Excel / CSV | Nothing up front — uploaded in the app (≤ 50 MB per file) |
| All | A **read replica** where the source is a busy OLTP system (NFR-026: Tela caps at 4 concurrent statements per connection, 20 s timeout, 10,000 rows) |

### 2.6 LLM — Azure AI Foundry

| # | Requirement | Notes |
|---|---|---|
| L-01 | A Foundry resource (Azure OpenAI / AI Services kind) in the agreed region, in the Tela subscription or a shared AI subscription | |
| L-02 | Model **deployments** and **quota (TPM)** per §4.3 | Quota requests can take days — raise early |
| L-03 | Endpoint URL and API key, put into Key Vault by the client (Tela copies it into OpenBao `llm/*`, readable only by the LLM gateway) | Managed-identity auth is a planned gateway change (§6, G-02) |
| L-04 | Content-filter policy: default is fine; confirm it does not block PMO terms (we test this during UAT) | |
| L-05 | Data-handling sign-off: by default **no source rows** reach the model — only schema, approved metadata and the question; aggregates only if the tenant toggle allows (NFR-012, RULE-019) | Client security / DPO to approve DEC-01 for the hosted model |
| L-06 | Optional: approval to opt out of abuse-monitoring data retention (Microsoft Limited Access form) if policy requires zero retention at Microsoft | |

### 2.7 Operations and governance decisions

| # | Decision | Our proposal |
|---|---|---|
| G-01 | Availability target (NFR-015) | 99.5 % monthly |
| G-02 | Backup RPO / RTO (NFR-018) | RPO ≤ 15 min (PITR), RTO ≤ 4 h; quarterly restore drill |
| G-03 | Retention for audit events, conversations, LLM metadata, uploaded files (NFR-027, Q-018) | Audit 7 y, conversations 1 y, LLM metadata 1 y, uploads 1 y |
| G-04 | Log destination | Client Log Analytics workspace / Sentinel |
| G-05 | Patch windows and change process | Monthly window; images rebuilt on every release |
| G-06 | Security testing | Client pen test before go-live; we fix findings |
| G-07 | Support model | Who is L1; Prodian L2/L3 access route (Bastion + PIM) |

---

## 3. Target architecture on Azure

```text
Users (browser) ──HTTPS──► Application Gateway WAF v2 (public or internal IP)
                                │  tela.<domain>        tela-auth.<domain>
                                ▼
                     snet-app (VM(s) with Docker, or AKS later)
   ┌───────────┬────────┬──────────────┬──────────────────┬──────────┬──────────┬──────────┐
   │ proxy+web │  api   │ llm-gateway  │ connector-runner │ worker   │ keycloak │ openbao  │
   └─────┬─────┴───┬────┴──────┬───────┴────────┬─────────┴────┬─────┴────┬─────┴────┬─────┘
         │         │           │                │              │          │          │ auto-unseal
         │         ▼           ▼                ▼              ▼          ▼          ▼
         │   PostgreSQL Flex  Azure AI       NAT GW (static    Redis   PostgreSQL  Key Vault
         │   (tela + keycloak  Foundry        egress IP) ──►   Cache    (same server)
         │   DBs, pgvector)   (private EP)   Dataverse / SQL /
         │                                   MySQL / Mongo sources
         ▼
   Object store (SeaweedFS on Premium SSD now; Azure Blob after US-122)
   Azure Monitor / Log Analytics ◄── OpenTelemetry collector (traces, metrics, logs)
```

| Tela component | Azure service | Status in code |
|---|---|---|
| web, proxy, api, llm-gateway, connector-runner, worker, beat | Docker on Linux VM (Option A) → AKS (Option B) | Ready (Compose); Helm chart pending (US-121) |
| Metadata DB + pgvector + Keycloak DB | **Azure Database for PostgreSQL Flexible Server 16**, extensions `vector`, `pgcrypto`, `pg_trgm` allow-listed | Ready — plain `postgresql://` DSN, `sslmode=require` |
| Cache / Celery broker | **Azure Cache for Redis** (Standard C1+) or Azure Managed Redis; TLS 6380 | Ready (Redis 7 protocol) |
| Secrets | **OpenBao** container (Tela's secrets API) with **Azure Key Vault auto-unseal**; Key Vault also holds bootstrap secrets and TLS certs | OpenBao ready; sealed/production mode with US-122/US-124. Native Key Vault adapter is US-122 (not built) |
| Object store (exports, uploads, knowledge files) | SeaweedFS container on a Premium SSD data disk, backed up by Azure Backup; switch to **Blob Storage** when the adapter lands | S3 adapter ready; Blob adapter US-122 (not built) |
| Sign-in | Keycloak container brokering **Entra ID** | Ready (prod realm template) |
| LLM | **Azure AI Foundry** — Azure OpenAI deployments | Ready (`azure_openai` provider: endpoint + key + deployment names) |
| Edge | **Application Gateway WAF v2** (TLS termination) in front of the nginx proxy | Ready |
| Egress | **NAT Gateway** with a static public IP | — |
| Images | **Azure Container Registry** (Premium if private endpoint needed) | Ready |
| Observability | OTel collector → **Azure Monitor / Application Insights / Log Analytics**; or keep the bundled Grafana stack | Collector ready; Azure exporter config with US-122 |
| Admin access | **Azure Bastion** + PIM; no public SSH | — |

---

## 4. LLM: Azure AI Foundry vs Copilot

### 4.1 Recommendation

Use **Azure AI Foundry (Azure OpenAI models)** for every LLM call. Tela's LLM gateway already speaks Azure OpenAI,
keeps the call in the client's tenant and region, applies masking and leak scanning, and records tokens and cost
in the usage ledger (REQ-117).

### 4.2 Why not Copilot as the LLM

Microsoft 365 Copilot and Copilot Studio are **end-user assistants, not a model API**: there is no supported
chat-completions/embeddings endpoint an application can call with its own prompts and JSON schemas. Tela's agents
(AG-01 – AG-10) need structured output against a typed Query Spec, embeddings for knowledge retrieval and
per-call metering — that is what Foundry provides.

If the client wants Tela **inside Copilot**, the right pattern is the reverse: a Copilot Studio agent (or M365
Copilot declarative agent / API plugin) that calls **Tela's Ask API** as an action, so Copilot users get governed,
scoped Tela answers. That is a separate integration story (not in the current backlog) and still needs Foundry
behind Tela.

### 4.3 Model deployments and quota

| Tela role (AI settings key) | Agents | Suggested Foundry deployment | Deployment type | Start TPM (Medium tier) |
|---|---|---|---|---|
| `planner` | AG-01 router, AG-02 clarifier, AG-03 Query-spec planner, AG-04 model assistant, AG-05 report planner, AG-06 canvas editor, AG-10 SQL drafter | GPT-4.1 (or newer GPT-4.1/GPT-5-class model available in region) | Data Zone Standard or Regional Standard (per residency) | 250 K |
| `narrator` / `advisor` | AG-07 visual advisor, AG-08 narrator, AG-09 certification reviewer | GPT-4.1-mini | Same as above | 100 K |
| `embeddings` | Knowledge (RAG), value index, semantic linking | **text-embedding-3-large** with `dimensions = 1024` (Tela index size) | Standard | 120 K (bursts during re-embed) |

Notes:
- Model choice is **not final until measured**: before go-live we run the gold suite (`tela-admin gold-run`) against
  the client's deployments and pick the cheapest model that meets accuracy. Hosted models are unmeasured so far
  (US-191 open item); the local reference is qwen3:8b.
- Changing the embedding model later clears vectors and the 20-minute re-embed job rebuilds them — pick it once.
- Every agent has a deterministic fallback, so a Foundry outage degrades answers but does not stop dashboards.
- Claude models offered through Foundry would need a small gateway change (Anthropic provider with a Foundry base
  URL) — G-03 in §6; not required for go-live.
- Provisioned Throughput (PTU) is only worth it above roughly 1 M TPM sustained; start pay-as-you-go.

### 4.4 Token estimate (to be confirmed from the usage ledger in UAT)

| Activity | Calls | Input tokens | Output tokens |
|---|---|---|---|
| One Ask question (router + planner + narrator + retrieval embedding) | 3 – 5 | 6 K – 12 K | 0.5 K – 1.5 K |
| One "build report with chat" | 4 – 8 | 15 K – 30 K | 2 K – 5 K |
| Model assistant on a new connection (one-off) | 10 – 40 | 100 K – 400 K | 20 K – 60 K |

Peak at the Medium tier: 20 concurrent chat sessions × ~1 question/min × ~12 K ≈ **240 K TPM** on the planner.
Monthly at ~100 active chat users × 15 questions/day × 22 days ≈ 33 K questions ≈ 300 – 450 M input tokens. Cost
depends on the model picked in §4.3 and current Azure price list — we produce the figure after the gold run.

---

## 5. Sizing and capacity

### 5.1 Tiers

| Tier | Users (named) | Concurrent viewers | Concurrent chat sessions | Use |
|---|---|---|---|---|
| **Small** | ≤ 100 | 50 | 5 | Pilot, UAT |
| **Medium** (NFR-016 baseline) | ≤ 1,000 | 200 | 20 | Production, one business |
| **Large** | ≤ 5,000 | 800 | 60 | Multi-BU / SaaS |

### 5.2 Compute — Option A (Docker Compose on VMs)

| Tier | VMs | VM size (each) | OS disk | Data disk (object store, logs) |
|---|---|---|---|---|
| Small / UAT | 1 | D4s_v5 (4 vCPU, 16 GiB) | 128 GiB Premium SSD | 256 GiB Premium SSD |
| Medium | 2 (active–active behind App Gateway; worker/beat on one only) | D8s_v5 (8 vCPU, 32 GiB) | 128 GiB Premium SSD | 512 GiB Premium SSD |
| Large | Move to Option B (AKS) | — | — | — |

Per-service container budget on one Medium VM:

| Service | Replicas per VM | CPU | Memory |
|---|---|---|---|
| proxy + web (nginx) | 1 | 0.25 | 256 MiB |
| api (uvicorn workers) | 1 × 4 workers | 2.0 | 4 GiB |
| llm-gateway | 1 | 0.5 | 1 GiB |
| connector-runner | 1 | 1.5 | 3 GiB (DuckDB for Excel/CSV, ODBC drivers) |
| worker (Celery) | 1 × 4 concurrency | 1.5 | 3 GiB (exports, re-embed, introspection) |
| beat | 1 (one VM only) | 0.1 | 256 MiB |
| keycloak | 1 | 1.0 | 2 GiB |
| openbao | 1 (one VM; HA via Raft on 3 nodes in Option B) | 0.25 | 512 MiB |
| objectstore (SeaweedFS) until Blob | 1 (one VM) | 0.25 | 512 MiB |
| otel-collector | 1 | 0.25 | 512 MiB |
| **Total** | | **≈ 7.6 vCPU** | **≈ 15 GiB** (headroom to 32 GiB for spikes and OS cache) |

### 5.3 Compute — Option B (AKS)

| Tier | System pool | User pool | Notes |
|---|---|---|---|
| Medium | 2 × D2s_v5 | 3 × D4s_v5 (autoscale 3 – 6) | 3 zones; HPA on api/runner at 70 % CPU |
| Large | 3 × D2s_v5 | 4 × D8s_v5 (autoscale 4 – 10) | OpenBao 3-node Raft; Keycloak 2 replicas |

Replicas (Large): api 4 – 8, connector-runner 3 – 6, llm-gateway 2 – 4, worker 2 – 4, web 2, keycloak 2.

### 5.4 Data services

| Service | Small | Medium | Large |
|---|---|---|---|
| PostgreSQL Flexible Server 16 | Burstable B2ms (2 vCPU, 8 GiB), 128 GiB | General Purpose **D4ds_v5** (4 vCPU, 16 GiB), 256 GiB P15, **zone-redundant HA** | General Purpose D8ds_v5 (8 vCPU, 32 GiB), 512 GiB, zone-redundant HA, 1 read replica |
| — backup | 7 days PITR, LRS | **35 days PITR, geo-redundant** | 35 days PITR, geo-redundant |
| Azure Cache for Redis | Basic C0 (UAT only) | **Standard C1** (1 GB, replicated) | Premium P1 (6 GB, zone redundant) |
| Key Vault | Standard | Standard (soft delete + purge protection) | Premium (HSM-backed unseal key) |
| Object store | 256 GiB disk | 512 GiB disk → Blob (Hot, ZRS) after US-122 | Blob (Hot, ZRS) with lifecycle rules per class |
| ACR | Basic | Standard | Premium (private endpoint, geo-replication) |
| Log Analytics | Pay-as-you-go, 30-day retention | 5 – 15 GB/day ingest, 90 days | 20 – 50 GB/day, 90 days + archive |

Why these sizes:
- **PostgreSQL** holds the metadata DB (models, dashboards, audit, conversations, LLM ledger, outbox) and the
  pgvector index. The value index and knowledge chunks use 1024-dim vectors: ≈ 4 KiB per row, so 1 M rows ≈ 4 –
  6 GiB with the HNSW index. 16 GiB RAM keeps the working set and the vector index in memory at Medium. Expected
  growth at Medium: 20 – 40 GiB per year, driven by audit, conversations and the LLM ledger (partitioned, swept
  by retention). Connection budget: api 4 workers × pool 10 + worker + beat + keycloak ≈ 120 per VM; enable the
  built-in **PgBouncer** on the Flexible Server.
- **Source queries run on the client's sources**, not on Tela — Tela stores no source rows, so Tela's DB size does
  not grow with source data.
- **Connector runner** is the heaviest service for Excel/CSV (DuckDB loads the file in memory: allow ≈ 10× file
  size, so 50 MB files need ~0.5 GiB each at peak).
- **Concurrency to sources** is capped per connection (default 4), so 200 viewers do not mean 200 queries on a
  source; dashboard tiles are cached in Redis by as-of.

### 5.5 Performance targets to verify in UAT (NFR-011)

| Measure | Target (p90) |
|---|---|
| Chat answer first token | ≤ 2 s (warm cache) |
| Chat answer complete | ≤ 8 s |
| Dashboard tab render (≤ 12 widgets) | ≤ 3 s |
| Custom SQL overhead | ≤ 0.5 s + source time |

These depend on the Foundry model latency and on source response time; we run a load test (US-125) on the
UAT environment at the Medium tier before go-live.

### 5.6 Environments

| Environment | Tier | HA | Notes |
|---|---|---|---|
| Dev (optional, can stay on Prodian side) | Small, 1 VM, Postgres in container | No | Synthetic data only (NFR-020) |
| UAT | Small | No | Real sources read-only, masked if required; separate Entra app registration and groups |
| Production | Medium | Yes (2 VMs, zone-redundant DB) | |
| DR (optional) | Cold: geo-restored DB + IaC redeploy in paired region | — | RTO ≤ 4 h achievable with IaC |

### 5.7 Indicative monthly cost (Medium, pay-as-you-go, before LLM tokens)

Order-of-magnitude only; confirm with the Azure Pricing Calculator for the chosen region.

| Item | Approx. USD / month |
|---|---|
| 2 × D8s_v5 VMs + disks | 650 – 750 |
| PostgreSQL Flex D4ds_v5 zone-redundant HA + 256 GiB + backup | 650 – 800 |
| Redis Standard C1 | 100 – 120 |
| Application Gateway WAF v2 | 350 – 450 |
| NAT Gateway + public IPs | 40 – 60 |
| Key Vault, ACR Standard, private endpoints, Bastion Basic | 250 – 300 |
| Log Analytics (≈ 10 GB/day) | 700 – 900 |
| **Platform total** | **≈ 2,700 – 3,400** |
| Foundry tokens | per §4.4 after the gold run |

UAT on the Small tier is roughly 25 – 30 % of production.

---

## 6. Gaps on the Tela side before Azure go-live

| # | Gap | Story | Needed for |
|---|---|---|---|
| G-01 | OpenBao production (sealed) mode with Azure Key Vault auto-unseal, Raft storage on a persistent disk | US-122 / US-124 | Go-live (today OpenBao is dev mode: data lost on restart) |
| G-02 | LLM gateway: Entra **managed identity** auth to Foundry (no API key) | US-122 (Azure profile) | Recommended; key-based works now |
| G-03 | Native Azure Blob object-store adapter and Azure Key Vault secrets adapter | US-122 | Optional (SeaweedFS + OpenBao work meanwhile) |
| G-04 | IaC (Bicep or Terraform) for §3 and the deployment runbook | US-122 | Go-live |
| G-05 | OTel exporter config for Azure Monitor / App Insights | US-122 | Go-live (or keep bundled Grafana stack) |
| G-06 | Helm chart for AKS | US-121 | Option B only |
| G-07 | Gold-suite accuracy run on the client's Foundry deployments; pick models | US-191 follow-up | Go-live |
| G-08 | Load test at the Medium tier; backup restore drill; transport/container security review | US-125, US-126, US-124 | Go-live |
| G-09 | Copilot Studio action calling Tela Ask (only if the client wants Tela in Copilot) | new | Optional |

---

## 7. Client checklist (one page)

- [ ] Subscription / resource groups for UAT and Prod; region agreed (A-01, A-02)
- [ ] Prodian deployment identity with Contributor + RBAC admin on the RGs (A-03)
- [ ] VNet/subnets, private DNS, NAT Gateway static IP (N-01 – N-04)
- [ ] Firewall rules: inbound 443, outbound FQDN list, source DB ports (N-05, N-06)
- [ ] Two DNS names and TLS certificates (D-01 – D-03)
- [ ] Entra app registration, admin consent, groups claim (I-01, I-02)
- [ ] Four Entra groups with object IDs (I-03)
- [ ] Dataverse: TDS on, application user, read-only role (§2.5)
- [ ] Other sources: read-only accounts, replicas, allow-list egress IP (§2.5)
- [ ] Foundry resource, deployments and TPM quota; key into Key Vault (L-01 – L-03)
- [ ] Security/DPO sign-off on LLM data handling (L-05)
- [ ] Decisions: availability, RPO/RTO, retention, log destination, support model (G-01 – G-07 in §2.7)
