# Research Analysis: Enterprise Identity Integration and Open-Source Analytics

**Prepared:** 20 October 2026\
**Purpose:** Consolidated research and recommendations from this
session\
**Status:** Architecture proposal --- validate provider-specific
capabilities and deployment constraints before implementation

## Executive summary

This session covered two connected product-design areas:

1.  An enterprise identity integration platform using Keycloak, with
    Microsoft Entra ID as the first priority, support for automatic user
    synchronization, application-owned permissions, and hybrid
    SaaS/customer-hosted deployment.
2.  Open-source charting and analytics technologies for a dynamic PMO /
    enterprise reporting application, including Vega-Lite and
    alternatives.

### Main recommendations

-   Use **Keycloak** as the authentication broker for OIDC/SAML-based
    enterprise SSO.
-   Support both **SSO and automated provisioning**. For Microsoft Entra
    ID, implement and test a compatible **SCIM 2.0 provisioning
    endpoint**; use Microsoft Graph only for capabilities not adequately
    covered by SCIM.
-   Keep application users, tenant membership, external identity links,
    dynamic roles, screen permissions, and data-level authorization in
    the application database.
-   Use **hybrid authorization**: Keycloak for authentication and
    selected coarse-grained roles; the application backend for
    tenant-scoped business permissions.
-   Make the platform provider-neutral through adapters, so later
    integrations can include HR systems such as Zoho People. Do not
    assume an HR system automatically provides SSO or SCIM.
-   For hybrid hosting, use the same versioned application package and
    data model, but define explicit SaaS and customer-hosted deployment
    profiles.
-   For dynamic dashboards, start with **Apache ECharts** for charts and
    evaluate **Perspective** for pivoting and interactive data
    exploration. Vega-Lite remains a good choice for declarative
    JSON-driven chart specifications.
-   Build a secure metadata-driven report builder around the chart
    library. A charting library alone does not provide data modeling,
    tenant security, query governance, saved reports, or permissions.

------------------------------------------------------------------------

# Part A --- Enterprise Identity Integration Platform

## 1. Problem statement and objectives

The product needs role-based users and access control. Customers should
be able to connect their existing enterprise identity or HR systems and
avoid manually creating every user. The application must retain control
over its own modules, screens, actions, and data access.

### Functional objectives

-   Corporate login through Microsoft Entra ID, with OIDC or SAML where
    appropriate.
-   Automated user creation, updates, group synchronization, and
    deprovisioning.
-   A mapping between external identities and internal application
    users.
-   Tenant-aware membership and isolation.
-   Application-managed roles and granular permissions.
-   Optional mapping from enterprise groups to application roles.
-   A customer administrator experience for connecting providers and
    viewing synchronization status.
-   Future provider integrations without redesigning the permission
    engine.
-   Shared SaaS and customer-hosted deployment options.

### Non-goals for the initial release

-   Replacing the customer's enterprise identity provider.
-   Treating the HR system as the authoritative authentication provider
    without checking its actual capabilities.
-   Automatically granting privileged application roles merely because a
    user belongs to a similarly named external group.
-   Building a full BI platform if the initial need is only embedded
    dashboards.

## 2. Terminology: authentication, provisioning, authorization

These concerns are distinct and should have separate contracts.

### Authentication / SSO

Proves who the user is. Keycloak brokers the login to an external
identity provider such as Microsoft Entra ID and issues tokens to the
application.

Typical protocols: - OpenID Connect (OIDC) - SAML 2.0

SSO alone does not guarantee that every employee has been
pre-provisioned into the application.

### Provisioning / lifecycle synchronization

Creates and updates user and group records and disables access when
appropriate.

Preferred Microsoft integration: - SCIM 2.0 for user/group provisioning
when supported by the selected Entra configuration. - Microsoft Graph
API for additional directory capabilities or custom synchronization
needs.

### Authorization

Determines what an authenticated user can do in the application. This
includes: - Tenant membership - Roles - Module and screen access -
Action permissions such as create, edit, delete, export, approve -
Resource-level restrictions, such as project or department access

The application must enforce authorization on the backend. Hiding a
button or route in the frontend is not sufficient.

## 3. Target architecture

``` text
Customer enterprise systems
  ├── Microsoft Entra ID
  ├── Other OIDC/SAML identity providers
  └── HR systems (e.g., Zoho People)
           │
           ├── OIDC/SAML: authentication
           └── SCIM/API: provisioning and synchronization
                       │
                    Keycloak
       Identity brokering, sessions, tokens,
       account federation and selected coarse roles
                       │
                  Python/FastAPI
       ├── Identity and tenant API
       ├── SCIM 2.0 provisioning API
       ├── Authorization and permission service
       ├── Provider adapters
       ├── Admin/configuration API
       └── Background sync/reconciliation workers
                       │
       ┌───────────────┼────────────────┐
       │               │                │
   PostgreSQL      Job queue        Secret store
   Identity and     Redis/RabbitMQ   Vault/OpenBao
   authorization
       │
       └── Application frontend and business APIs
           ├── Dashboard / modules
           ├── Screen visibility
           ├── Action permissions
           └── Tenant-scoped business data
```

### Suggested technology stack

  -----------------------------------------------------------------------
  Area                    Proposal                Notes
  ----------------------- ----------------------- -----------------------
  Identity broker         Keycloak                OIDC/SAML identity
                                                  brokering, sessions,
                                                  tokens

  Backend                 FastAPI                 Versioned application
                                                  and SCIM APIs

  Database                PostgreSQL              Tenants, identity
                                                  mapping, roles,
                                                  permissions, audit

  ORM/migrations          SQLAlchemy 2.x +        Typed data access and
                          Alembic                 versioned schema

  Background jobs         Celery or equivalent    Durable sync, retries,
                                                  reconciliation

  Queue/broker            Redis or RabbitMQ       Choose based on
                                                  reliability and
                                                  operational needs

  Secrets                 HashiCorp Vault or      Provider credentials
                          OpenBao                 and sensitive
                                                  configuration

  Frontend                React or Angular        Use existing product
                                                  stack

  Deployment              Docker; Kubernetes      Same images across SaaS
                          where justified         and customer-hosted
                                                  models

  Observability           OpenTelemetry +         Request tracing,
                          centralized             provisioning health,
                          logs/metrics            alerts
  -----------------------------------------------------------------------

Start with a **modular monolith plus workers**, not many microservices.
Separate components only when scale, deployment isolation, or team
ownership justifies it.

## 4. Hybrid hosting model

### Model A: shared SaaS

-   One centrally operated platform serves multiple customer
    organizations.
-   Tenant IDs must be enforced throughout all business data and
    authorization paths.
-   Identity-provider connections and credentials are tenant-specific.
-   Consider a Keycloak realm per customer when each customer needs
    independent federation configuration.
-   A shared realm is possible, but provider routing, account linking,
    and tenant assignment become more complex.
-   Central operations manage upgrades, monitoring, backup, and
    recovery.

### Model B: customer-hosted

-   Ship the same versioned application image and migration process.
-   Customer controls infrastructure, networking, secrets, and
    enterprise directory access.
-   Provide a supported Keycloak deployment or a clearly documented
    external Keycloak integration.
-   Document installation, upgrades, backup, restore, health checks, and
    configuration.
-   Keep provider integrations and permission behavior consistent with
    SaaS.

### Tenant isolation rules

-   Tenant authorization must be enforced in backend queries and service
    methods.
-   Do not rely on a Keycloak realm as the only business-data isolation
    boundary.
-   A user can belong to more than one tenant; tenant membership and
    roles should be scoped accordingly.
-   Never use email domain alone as proof of tenant identity.
-   Consider PostgreSQL row-level security as defense in depth, with
    carefully managed transaction-local tenant context and connection
    pooling.

## 5. Microsoft Entra ID integration

### 5.1 SSO flow

1.  User opens the application.
2.  Frontend starts OIDC Authorization Code flow with PKCE through
    Keycloak.
3.  Keycloak redirects to the customer's Microsoft Entra tenant.
4.  Microsoft authenticates the user.
5.  Keycloak validates the response and maps the external identity.
6.  Keycloak returns an authorization code to the frontend.
7.  The frontend exchanges the code for tokens.
8.  The frontend calls the backend with an access token.
9.  FastAPI validates the token and checks internal tenant membership
    and permissions.

Use the access token for API authorization, not the ID token. Validate
signature, issuer, audience, expiry, and required token claims.

### 5.2 Entra setup checklist

For each customer connection:

1.  Decide the supported account/tenant scope.
2.  Register/configure the relevant Microsoft Entra application.
3.  Configure the redirect URI supplied by Keycloak.
4.  Configure the client ID, tenant-specific issuer/authority, and
    required credential.
5.  Grant only required API permissions and administrator consent where
    needed.
6.  Configure the Keycloak OIDC identity provider and claim mappers.
7.  Preserve stable directory identifiers, including the directory
    tenant ID and object ID.
8.  Define first-login behavior and account-linking rules.
9.  Test login, logout, invalid issuer/audience, disabled users, session
    expiry, and tenant isolation.
10. Document credential rotation and connection-health monitoring.

Do not match an account using email alone. Store the trusted provider
connection and immutable external identifiers.

### 5.3 Automated provisioning with SCIM

Create a SCIM 2.0 API for Microsoft Entra provisioning.

Recommended base path: `/scim/v2`

Minimum resources/capabilities to evaluate: -
`GET /ServiceProviderConfig` - `GET /ResourceTypes` - `GET /Schemas` -
`GET /Users` - `POST /Users` - `GET /Users/{id}` - `PUT /Users/{id}` -
`PATCH /Users/{id}` - `DELETE /Users/{id}` or equivalent configured
deprovisioning behavior - `GET /Groups` - `POST /Groups` -
`GET /Groups/{id}` - `PATCH /Groups/{id}` - `DELETE /Groups/{id}`

The exact operations depend on the required Entra provisioning profile
and whether group provisioning is enabled. Implement SCIM schema,
filters, pagination, response/error formats, PATCH semantics, and stable
resource identifiers---not just endpoints with these names.

### 5.4 Provisioning lifecycle mapping

  -----------------------------------------------------------------------
  Entra event                         Application behavior
  ----------------------------------- -----------------------------------
  User assigned to the enterprise     Create or associate internal user
  application                         and tenant membership

  Attribute changed                   Update approved mapped attributes

  Group membership changed            Update external group membership
                                      and recalculate mapped roles

  User disabled or deprovisioned      Block new application access and
                                      run defined session-revocation
                                      behavior

  Provisioning retry                  Process idempotently; do not create
                                      duplicates

  Sync failure                        Record error, retry safely, alert,
                                      and allow reconciliation
  -----------------------------------------------------------------------

Provision only the intended scope---often users assigned to the
enterprise application or selected groups, not necessarily every user in
the directory.

### 5.5 Keycloak account provisioning decision

Two models are possible:

1.  Create Keycloak accounts for all pre-provisioned users through
    supported administrative APIs or a suitable integration.
2.  Keep application identity records before first login, and associate
    them with Keycloak identities when they authenticate.

Recommended default: keep application identity metadata and
authorization in the application database. Create Keycloak accounts when
the product requires pre-created accounts, or use a documented
federation/broker approach that fits the operational model. Do not
assume a SCIM record must always become a Keycloak user immediately.

## 6. Provider abstraction and Zoho People

Build a provider adapter layer with a normalized identity model. Each
adapter should translate provider-specific identifiers, attributes,
groups, lifecycle events, pagination, and errors into internal
contracts.

Potential adapters: - Microsoft Entra ID: OIDC/SAML for login, SCIM for
provisioning, Graph API for additional directory needs. - Zoho People:
employee API and supported synchronization mechanisms; separately verify
SSO/SCIM support in the customer's actual Zoho products and plan. -
Other OIDC/SAML identity providers. - LDAP/Active Directory for
applicable environments.

Important: Zoho People is primarily an HR system. Employee data access
does not automatically imply that the product is the identity provider
or that it supports the same SSO and provisioning behavior as Entra.
Some customers may need separate authentication and HR-data
integrations.

Suggested provider adapter contract:

``` python
from typing import Protocol

class IdentityProviderAdapter(Protocol):
    async def test_connection(self) -> dict: ...
    async def fetch_users(self, cursor: str | None = None) -> dict: ...
    async def fetch_groups(self, cursor: str | None = None) -> dict: ...
    async def normalize_user(self, provider_record: dict) -> dict: ...
    async def normalize_group(self, provider_record: dict) -> dict: ...
```

Treat this as an illustrative interface. Real adapters need typed
models, pagination, retries, rate limits, credential management, and
provider-specific error handling.

## 7. Database design

Recommended core tables:

  -----------------------------------------------------------------------
  Table                               Purpose and key fields
  ----------------------------------- -----------------------------------
  `tenants`                           Customer organization: `id`,
                                      `slug`, `name`, `status`

  `deployments`                       Deployment metadata: `tenant_id`,
                                      `deployment_type`, `config_version`

  `identity_connections`              Provider configuration:
                                      `tenant_id`, `provider_type`,
                                      `issuer`, `status`, external tenant
                                      ID

  `users`                             Internal profile: `id`,
                                      `display_name`, `email`, `status`

  `external_identities`               Mapping: `connection_id`,
                                      `user_id`, `external_subject`,
                                      `external_object_id`

  `tenant_memberships`                `tenant_id`, `user_id`, `status`

  `external_groups`                   Provider groups: connection,
                                      external group ID, display name

  `external_group_memberships`        Links imported groups to internal
                                      users

  `roles`                             Tenant/system roles: `tenant_id`,
                                      `key`, `name`, `is_system`

  `permissions`                       Stable permission keys and
                                      descriptions

  `role_permissions`                  Role-to-permission assignments

  `user_role_assignments`             Tenant-scoped role assignments,
                                      source, expiry

  `group_role_mappings`               Explicit external group-to-role
                                      mapping

  `provisioning_events`               Idempotency/event status and
                                      processing outcomes

  `sync_runs`                         Reconciliation history and results

  `audit_logs`                        Security and administrative
                                      activity
  -----------------------------------------------------------------------

Add foreign keys, indexes, unique constraints, timestamps, and migration
tests. Secrets should not be stored in ordinary configuration JSON or
returned by APIs.

Example constraints:

``` sql
CREATE UNIQUE INDEX uq_external_identity
ON external_identities (connection_id, external_subject);

CREATE UNIQUE INDEX uq_tenant_membership
ON tenant_memberships (tenant_id, user_id);
```

Role uniqueness depends on whether built-in roles are global or
tenant-specific. Model system roles explicitly rather than forcing all
roles to have a non-null tenant.

### Identity lifecycle states

Use explicit states such as: - `PENDING`: provisioned, but
onboarding/association incomplete. - `ACTIVE`: account and tenant
membership permit access. - `SUSPENDED`: temporarily blocked. -
`DEPROVISIONED`: access revoked; retention and audit rules apply.

Do not automatically delete business records or audit history when a
user is deprovisioned.

## 8. API contracts

### Application APIs

Base path: `/api/v1`

  ------------------------------------------------------------------------------------------------------------
  Method                  Endpoint                                                     Purpose
  ----------------------- ------------------------------------------------------------ -----------------------
  GET                     `/me`                                                        Current user,
                                                                                       memberships, effective
                                                                                       permissions

  GET                     `/tenants/{tenant_id}/members`                               List tenant members

  PATCH                   `/tenants/{tenant_id}/members/{user_id}`                     Update allowed
                                                                                       membership
                                                                                       attributes/status

  GET                     `/tenants/{tenant_id}/roles`                                 List roles

  POST                    `/tenants/{tenant_id}/roles`                                 Create custom role

  PUT                     `/tenants/{tenant_id}/roles/{role_id}/permissions`           Replace role
                                                                                       permissions

  PUT                     `/tenants/{tenant_id}/group-mappings`                        Configure group-to-role
                                                                                       mapping

  GET                     `/tenants/{tenant_id}/identity-connections`                  View safe provider
                                                                                       metadata

  POST                    `/tenants/{tenant_id}/identity-connections`                  Create connection

  POST                    `/tenants/{tenant_id}/identity-connections/{id}/test`        Test connection

  POST                    `/tenants/{tenant_id}/identity-connections/{id}/reconcile`   Start reconciliation

  GET                     `/tenants/{tenant_id}/sync-runs`                             Review sync history

  GET                     `/tenants/{tenant_id}/audit-logs`                            Review authorized audit
                                                                                       records
  ------------------------------------------------------------------------------------------------------------

Every endpoint must verify the caller's tenant scope and permission.
Never rely on a user-supplied tenant ID without validating membership
and access.

### Example SCIM user payload

``` http
POST /scim/v2/Users
Authorization: Bearer <provisioning-token>
Content-Type: application/scim+json
```

``` json
{
  "schemas": [
    "urn:ietf:params:scim:schemas:core:2.0:User"
  ],
  "externalId": "provider-object-id",
  "userName": "employee@example.com",
  "name": {
    "givenName": "Alex",
    "familyName": "Morgan"
  },
  "active": true,
  "emails": [
    {
      "value": "employee@example.com",
      "type": "work",
      "primary": true
    }
  ]
}
```

This is an illustrative payload. Map `externalId` according to the
configured connection policy, and issue a stable opaque SCIM resource ID
of your own.

### Example `/me` response

``` json
{
  "user_id": "user-456",
  "tenant_id": "tenant-123",
  "roles": ["project_manager"],
  "permissions": [
    "dashboard.view",
    "projects.view",
    "projects.create",
    "projects.edit",
    "reports.view"
  ]
}
```

The frontend can use this response to render menus and buttons. The
backend must independently enforce each protected operation.

### Error handling

-   SCIM endpoints must return SCIM-compatible errors and relevant HTTP
    status codes.
-   Application APIs should use a stable error structure and request ID.
-   Never expose stack traces, provider secrets, access tokens, or
    sensitive personal data in errors or logs.

## 9. Hybrid authorization model

### Division of responsibility

**Keycloak** - Authentication and trusted identity claims. - Client
roles and selected coarse-grained roles. - Session and token lifecycle.

**Application database/service** - Tenant membership. - Dynamic
application roles. - Screen/module permissions. - Action permissions. -
Resource/record-level restrictions. - Explicit mappings from external
groups to internal roles.

### Example permission keys

-   `dashboard.view`
-   `projects.view`
-   `projects.create`
-   `projects.edit`
-   `projects.delete`
-   `reports.view`
-   `reports.export`
-   `users.invite`
-   `users.assign_roles`
-   `identity_connections.manage`

### Authorization decision

Require a valid authenticated principal, active tenant membership, and
the specific permission required for the operation. Add resource
ownership or department-level checks where applicable.

Do not require a privileged coarse role for every endpoint. Define which
operations actually need administrator privileges.

External group membership must not automatically grant application
administrator privileges. Privileged role mappings should require
explicit configuration and preferably additional approval.

## 10. FastAPI implementation structure

Suggested repository layout:

``` text
enterprise-identity-platform/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   │   ├── auth.py
│   │   ├── tenants.py
│   │   ├── users.py
│   │   ├── roles.py
│   │   ├── identity_connections.py
│   │   └── scim_v2/
│   ├── identity/
│   │   ├── token_validation.py
│   │   ├── keycloak_admin.py
│   │   └── account_linking.py
│   ├── provisioning/
│   │   ├── service.py
│   │   ├── reconciliation.py
│   │   └── lifecycle.py
│   ├── authorization/
│   │   ├── dependencies.py
│   │   ├── evaluator.py
│   │   └── policy.py
│   ├── providers/
│   │   ├── microsoft_entra/
│   │   └── zoho_people/
│   ├── models/
│   ├── repositories/
│   ├── audit/
│   └── workers/
├── migrations/
├── tests/
├── Dockerfile
├── compose.yaml
└── pyproject.toml
```

### JWT validation considerations

-   Trust only configured Keycloak issuers.
-   Validate signature, issuer, audience, expiry, subject, and token
    type/claims.
-   Restrict allowed signing algorithms.
-   Cache JWKS keys safely and support key rotation.
-   For multiple realms, resolve expected issuer from trusted
    server-side tenant/deployment configuration. Do not blindly use a
    token-provided issuer to fetch arbitrary keys.
-   Reject ID tokens where access tokens are required.
-   Map token subject to internal identity through a trusted server-side
    mapping.

### Reliable background processing

-   Use database transactions for core identity/membership updates.
-   Use idempotent handlers for retries and duplicate provisioning
    requests.
-   Use a transactional outbox when a database update must reliably
    trigger downstream work.
-   Retry transient errors with bounded exponential backoff.
-   Use dead-letter handling and administrator-visible failure records.
-   Reconcile desired and actual identity state periodically.
-   Use request IDs and structured logs; do not log credentials or full
    tokens.

## 11. Security controls

  -----------------------------------------------------------------------
  Risk                                Control
  ----------------------------------- -----------------------------------
  Cross-tenant data access            Tenant-scoped authorization and
                                      queries; optional PostgreSQL RLS
                                      defense in depth

  Duplicate or incorrectly linked     Stable provider identifiers and
  users                               explicit account-linking policy

  Disabled user retains access        Block new requests, revoke sessions
                                      where applicable, define token
                                      expiry/revocation behavior

  Group grants excessive privileges   Explicit group-to-role mappings and
                                      protected privileged roles

  Token forgery/wrong audience        Signature, issuer, audience, expiry
                                      and token-type validation

  Secret leakage                      Vault/OpenBao, restricted access,
                                      credential rotation

  Provisioning retries create         Idempotent operations and unique
  duplicates                          constraints

  Mass accidental deprovisioning      Preview/guardrails, alerting, audit
                                      and recovery procedure

  Sync outage                         Durable queue, bounded retries,
                                      reconciliation and alerts

  Sensitive logs                      Redaction and restricted audit
                                      access
  -----------------------------------------------------------------------

Additional production controls: - HTTPS for all endpoints. - Rate
limiting and request-size limits. - Validate SCIM filters to prevent
expensive queries. - Strong administrative authentication and MFA where
possible. - Backup and restore testing for Keycloak and PostgreSQL. -
Versioned deployment configuration and migrations. - Document incident
response and credential rotation. - Define retention/deletion policies
for personal data and audit logs.

## 12. Implementation roadmap

These are rough planning estimates for an experienced small team;
integration complexity and customer testing can change them.

  ------------------------------------------------------------------------
  Phase                                     Estimate Deliverable
  --------------------- ---------------------------- ---------------------
  1\. Foundation                          1--2 weeks Docker environment,
                                                     Keycloak, PostgreSQL,
                                                     migrations

  2\. SSO                                 1--2 weeks Entra login,
                                                     validated tokens,
                                                     identity/tenant
                                                     mapping

  3\. Identity model                      1--2 weeks External identities,
                                                     memberships,
                                                     lifecycle, audit

  4\. SCIM                                2--4 weeks User lifecycle,
                                                     groups if required,
                                                     filters, pagination,
                                                     Entra tests

  5\. Authorization                       2--3 weeks Coarse roles,
                                                     granular permissions,
                                                     tenant enforcement

  6\. Admin console                       2--3 weeks Connection setup,
                                                     mappings, sync
                                                     status,
                                                     reconciliation

  7\. Hardening                           1--2 weeks Security tests,
                                                     recovery, load tests,
                                                     backups,
                                                     customer-hosted
                                                     validation
  ------------------------------------------------------------------------

### Production acceptance criteria

-   Entra SSO works for a configured tenant.
-   Untrusted issuers and invalid audiences are rejected.
-   Provisioning retries do not create duplicate identities.
-   User disablement blocks access under the defined policy.
-   Group changes only grant roles through configured mappings.
-   Customer A cannot read or modify customer B's data.
-   Frontend visibility matches backend authorization.
-   Failed sync jobs can be retried and reconciled.
-   Customer-hosted installation, upgrade, backup and restore are
    tested.
-   Security and audit tests pass.

### Architecture decisions to finalize

1.  SaaS tenant strategy: realm per customer vs shared realm with
    supported provider routing.
2.  Whether every provisioned user requires a pre-created Keycloak
    account.
3.  Which external groups can map to roles, and whether privileged roles
    require approval.
4.  Whether group synchronization is required in the first release or
    only user provisioning.
5.  Required data residency, retention, availability, and recovery
    targets.
6.  Which customer-hosted deployment environments must be supported.

------------------------------------------------------------------------

# Part B --- Vega-Lite and Open-Source Analytics

## 13. What is Vega-Lite?

Vega-Lite is an open-source, declarative visualization grammar. A chart
is described using JSON: the data source, mark type, field encodings,
scales, axes, tooltips, and interactions.

Instead of manually coding every drawing operation, the application
describes what the visualization should represent. Vega-Lite compiles
specifications into Vega.

Useful for: - JSON-driven chart generation. - Dynamic report builders. -
Bar, line, scatter, heatmap and other common visualizations. - Tooltips
and declarative interaction. - Generating visualizations from metadata
and query results.

Official resources: - https://vega.github.io/vega-lite/ -
https://vega.github.io/editor/

### Example specification

``` json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
  "data": {
    "values": [
      {"project": "Project A", "completion": 80},
      {"project": "Project B", "completion": 55},
      {"project": "Project C", "completion": 95}
    ]
  },
  "mark": "bar",
  "encoding": {
    "x": {
      "field": "project",
      "type": "nominal"
    },
    "y": {
      "field": "completion",
      "type": "quantitative",
      "scale": {"domain": [0, 100]}
    },
    "tooltip": [
      {"field": "project"},
      {"field": "completion"}
    ]
  }
}
```

This is an example only; verify the schema version against the Vega-Lite
version installed in the application.

## 14. Open-source alternatives

### Apache ECharts

-   License: Apache 2.0.
-   Best fit: rich embedded dashboards and broad interactive chart
    types.
-   Strengths: extensive chart catalog, interactions, zooming,
    Canvas/SVG rendering, flexible configuration.
-   Trade-off: the application still needs to build report metadata,
    query logic, saved reports, and permission enforcement.
-   Website: https://echarts.apache.org/
-   GitHub: https://github.com/apache/echarts

### Perspective (FINOS)

-   License: Apache 2.0.
-   Best fit: interactive data exploration, pivot-style views, grouping,
    filtering, grids, and analytical workflows.
-   Strengths: a data-oriented analytical engine and interactive views.
-   Trade-off: it is not a full business intelligence platform;
    integration and report-authoring UX still need design.
-   Website: https://perspective.finos.org/
-   GitHub: https://github.com/finos/perspective

### Recharts

-   License: MIT.
-   Best fit: common charts built as React components.
-   Strengths: familiar component model and straightforward React
    integration.
-   Trade-off: not a native Angular component model; highly dynamic
    chart generation may require more application code.
-   Website: https://recharts.org/
-   GitHub: https://github.com/recharts/recharts

### Plotly.js

-   License: MIT.
-   Best fit: analytical/scientific visualizations, advanced
    interactions, scatter plots, and 3D charts.
-   Strengths: broad analytical chart capabilities.
-   Trade-off: evaluate bundle size, performance, and chart-specific
    needs.
-   Website: https://plotly.com/javascript/
-   GitHub: https://github.com/plotly/plotly.js

### Apache Superset

-   License: Apache 2.0.
-   Best fit: a separate self-service BI platform with dashboards, SQL
    exploration, and visualization.
-   Strengths: provides much more than a chart library.
-   Trade-off: deploying and operating a separate BI platform may be
    excessive if the requirement is to embed charts deeply into an
    existing product.
-   Website: https://superset.apache.org/
-   GitHub: https://github.com/apache/superset

### Altair

-   Python declarative visualization library built around Vega-Lite.
-   Best fit: Python-first chart authoring and analytical notebooks or
    services.
-   Trade-off: browser rendering still relies on Vega-Lite ecosystem
    components; it is not a separate rendering engine that removes
    Vega-Lite from the stack.

## 15. Comparison table

  ----------------------------------------------------------------------------------------------------------------------------
  Capability    ECharts      Perspective   Vega-Lite        Recharts             Plotly.js              Superset
  ------------- ------------ ------------- ---------------- -------------------- ---------------------- ----------------------
  Primary       Charting     Interactive   Declarative      React charts         Analytical charting    Full BI platform
  purpose                    data analysis chart                                                        
                                           specifications                                               

  JSON/spec     Strong       Different     Core approach    Component-oriented   Configuration-driven   Dashboard/metadata
  driven                     data/view                                                                  model
                             model                                                                      

  Pivot/data    Requires     Strong fit    Requires extra   Requires extra logic Requires extra logic   Strong BI workflows
  exploration   extra logic                logic                                                        

  Rich chart    Extensive    Focused       Broad            Common charts        Extensive analytical   Depends on supported
  catalog                    analytical    declarative                           charts                 integrations
                             views         grammar                                                      

  Embedded app  Strong       Strong for    Strong           Strong for React     Strong                 Possible, but separate
  fit                        exploration                                                                platform

  Python        Good         Good          Good             Good                 Good                   Python-based platform
  backend fit                                                                                           

  Angular fit   Available    Available     Available        Not native           Available              Embedded/integration
                                                                                                        approach

  Best use here Main         Interactive   Generated chart  React-specific       Specialized analytics  Separate self-service
                dashboards   exploration   specs            components                                  BI
  ----------------------------------------------------------------------------------------------------------------------------

This is a qualitative architectural comparison, not a controlled
performance benchmark. Confirm current versions, licenses, framework
integrations, accessibility, and chart requirements before selection.

## 16. Recommended analytics architecture

For a PMO / enterprise reporting product, separate the report builder
from the chart renderer.

``` text
Frontend report builder
  ├── Dataset selector
  ├── Dimension selector
  ├── Metric selector
  ├── Filter editor
  ├── Chart-type selector
  └── Saved report configuration
             │
             ▼
Python FastAPI analytics API
  ├── Authenticate and authorize
  ├── Validate against approved data catalog
  ├── Apply tenant and row-level filters
  ├── Validate dimensions and metrics
  ├── Build parameterized query
  ├── Enforce timeouts/row limits
  └── Return aggregate data and safe chart config
             │
             ▼
PostgreSQL / governed data model
             │
             ▼
Chart renderer
  ├── Apache ECharts: dashboards
  └── Perspective: pivot and interactive analysis
```

### Why not expose arbitrary SQL?

A dynamic report builder must not let users submit unrestricted SQL or
arbitrary database columns. Define an approved data catalog that
declares: - Dataset identifiers. - Available dimensions and metrics. -
Data types and aggregation rules. - Relationships and joins. - Sensitive
fields. - Tenant and row-level access rules. - Allowed filters, sort
order, and query limits.

Build queries from validated metadata and parameterized values. Do not
concatenate untrusted user input into SQL identifiers or query text.

### Example report configuration

``` json
{
  "dataset": "projects",
  "visualization": "bar",
  "dimension": "department",
  "metric": "project_count",
  "filters": [
    {
      "field": "status",
      "operator": "equals",
      "value": "active"
    }
  ],
  "sort": {
    "field": "project_count",
    "direction": "desc"
  }
}
```

The backend should validate this configuration, resolve it against an
allowlisted data catalog, apply tenant authorization, and return safe
aggregate results. The client should not be able to override
authorization filters.

## 17. Example PMO analytics use cases

-   Project status and completion by department.
-   Budget versus actual by month.
-   Resource allocation and utilization.
-   Task aging and overdue items.
-   Milestone slippage.
-   Workload by project manager.
-   Portfolio risk heatmap.
-   KPI trends over time.
-   Drill-down from portfolio to project, with authorization checked at
    each step.

Start with a curated set of datasets and metrics. Expand the catalog as
data definitions and business semantics are validated.

## 18. Recommended implementation sequence for analytics

1.  Confirm dashboard requirements and chart types.
2.  Define the reporting data catalog and metric definitions.
3.  Implement tenant-aware aggregate endpoints in FastAPI.
4.  Build a report configuration schema with validation.
5.  Implement ECharts for standard dashboard charts.
6.  Prototype Perspective for pivot-style exploration if users need it.
7.  Store saved reports as validated JSON configurations, with tenant
    ownership and permission rules.
8.  Add export, drill-down, and cross-filtering only after defining
    security and performance boundaries.
9.  Benchmark representative datasets and browsers before committing to
    complex visualizations.

### Analytics security and performance controls

-   Enforce authorization on every data request and drill-down.
-   Use allowlisted metrics/dimensions and parameterized queries.
-   Apply query timeouts, result limits, and pagination where
    appropriate.
-   Avoid returning sensitive row-level data when aggregate values are
    sufficient.
-   Cache only with tenant and permission context included in the cache
    key.
-   Version saved report schemas so older reports can be migrated
    safely.
-   Treat chart specifications as data, not executable code; reject
    arbitrary expressions or unsafe external data sources.

------------------------------------------------------------------------

# Part C --- Consolidated Recommendations and Decision Log

## 19. Recommended decisions

  -----------------------------------------------------------------------
  Area                    Recommendation          Rationale
  ----------------------- ----------------------- -----------------------
  Authentication          Keycloak as broker      Centralizes federation
                                                  and token issuance

  Microsoft login         OIDC Authorization      Standard enterprise SSO
                          Code + PKCE via         pattern
                          Keycloak                

  User lifecycle          SCIM 2.0 plus           Standard provisioning
                          reconciliation          contract and
                                                  recoverable sync

  Extra directory needs   Microsoft Graph adapter Keeps
                                                  Microsoft-specific
                                                  behavior out of the
                                                  core model

  Identity model          Internal users +        Supports multiple
                          external identity links providers and prevents
                                                  email-only matching

  Tenant model            Explicit tenant         Required for safe SaaS
                          memberships and checks  and hybrid hosting

  Authorization           Keycloak coarse roles + Supports both
                          database permissions    platform-level and
                                                  dynamic business rules

  Group mapping           Explicit, auditable     Prevents accidental
                          mappings                privilege escalation

  Backend                 FastAPI modular         Lower initial
                          monolith plus workers   complexity and a clear
                                                  extension path

  Database                PostgreSQL              Relational integrity
                                                  for identity and
                                                  permission models

  Jobs                    Durable queue, retries, Avoids lost or
                          outbox, reconciliation  duplicate lifecycle
                                                  events

  Secrets                 Vault/OpenBao or        Centralized credential
                          equivalent              lifecycle

  Dashboard charting      Apache ECharts          Broad embedded chart
                                                  capabilities

  Interactive analysis    Evaluate Perspective    Better fit for
                                                  pivot-style data
                                                  exploration

  Declarative alternative Keep Vega-Lite as an    Useful when report
                          option                  specs are generated as
                                                  JSON

  Full BI alternative     Apache Superset         Consider if users need
                                                  a separate self-service
                                                  BI product
  -----------------------------------------------------------------------

## 20. Key risks and open questions

### Identity platform

-   Does each SaaS customer require an independent Keycloak realm?
-   What is the minimum supported Entra provisioning profile?
-   Are group provisioning and group-to-role mapping needed in the first
    release?
-   How should access to already-issued tokens be handled after
    suspension?
-   Which identity provider is authoritative for name, email,
    department, and employment status?
-   What is the approved process for linking an existing local user to
    an external identity?
-   Are customer-hosted deployments allowed outbound access to central
    services?
-   What are the requirements for data residency, high availability,
    RTO/RPO, and audit retention?

### Analytics

-   Is the product primarily a predefined dashboard, a self-service
    report builder, or a full BI environment?
-   Are pivot tables, drill-down, cross-filtering, exports, and
    scheduled reports required?
-   How many rows and concurrent users should each report support?
-   Which metrics have agreed business definitions?
-   Must reports work with Angular, React, or both?
-   Are reports allowed to expose individual records, or only
    aggregates?

## 21. Source links for verification

Use official documentation as the source of truth for version-specific
behavior. The architecture and recommendations above are a synthesis,
and provider details must be validated against the selected versions and
customer configuration.

### Identity and provisioning

-   Keycloak Server Administration Guide:
    https://www.keycloak.org/docs/latest/server_admin/
-   Keycloak Authorization Services:
    https://www.keycloak.org/docs/latest/authorization_services/index.html
-   Microsoft Entra SCIM provisioning tutorial:
    https://learn.microsoft.com/en-us/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups
-   How Microsoft Entra provisioning works:
    https://learn.microsoft.com/en-us/entra/identity/app-provisioning/how-provisioning-works
-   Microsoft Entra Authorization Code flow:
    https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
-   Microsoft Graph documentation:
    https://learn.microsoft.com/en-us/graph/overview
-   Zoho People API documentation:
    https://www.zoho.com/people/api/overview.html

### Visualization and analytics

-   Vega-Lite: https://vega.github.io/vega-lite/
-   Vega Editor: https://vega.github.io/editor/
-   Apache ECharts: https://echarts.apache.org/
-   Apache ECharts GitHub: https://github.com/apache/echarts
-   Perspective: https://perspective.finos.org/
-   Perspective GitHub: https://github.com/finos/perspective
-   Recharts: https://recharts.org/
-   Plotly.js: https://plotly.com/javascript/
-   Apache Superset: https://superset.apache.org/
-   Altair: https://altair-viz.github.io/

------------------------------------------------------------------------

## Final conclusion

The identity platform and analytics module should share a common
principle: **keep the core business rules and data governance in your
application, and use external technologies for the capabilities they do
best.**

For identity, Keycloak brokers authentication, SCIM/API adapters
synchronize external users, and your application owns tenant membership
and business permissions.

For analytics, FastAPI governs data access and metrics, Apache ECharts
renders dashboards, and Perspective can be added when interactive
pivoting and exploration are required.

This approach provides a provider-neutral architecture, supports both
SaaS and customer-hosted deployments, and creates a clear path from a
focused first release to a broader enterprise platform.
