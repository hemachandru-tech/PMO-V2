"""Permission key catalogue (technical_document §Authentication and authorization).

This is the single catalogue (PRE-06, inconsistency I-2). Seed grants live in
migration 0002; this module is the reference used by route guards. Per-user grants on one connection
(``iam.permission_grants``, migration 0027) add the ``GRANTABLE`` keys.
"""

from __future__ import annotations

from enum import StrEnum


class Perm(StrEnum):
    DASHBOARDS_VIEW = "dashboards.view"
    DEFINITIONS_VIEW = "definitions.view"
    DRILL_USE = "drill.use"
    ASK_USE = "ask.use"
    EXPORT_USE = "export.use"
    WIDGETS_CREATE = "widgets.create"
    REPORTS_CREATE = "reports.create"
    DASHBOARDS_CREATE = "dashboards.create"
    FIELDS_CREATE = "fields.create"
    SHARE_CREATE = "share.create"
    CERTIFICATION_REQUEST = "certification.request"
    CUSTOMSQL_USE = "customsql.use"
    CONNECTIONS_MANAGE = "connections.manage"
    MODEL_EDIT = "model.edit"
    MODEL_APPROVE = "model.approve"
    STARTER_MANAGE = "starter.manage"
    MAPPINGS_MANAGE = "mappings.manage"
    POLICIES_MANAGE = "policies.manage"
    CERTIFICATION_DECIDE = "certification.decide"
    DASHBOARDS_PUBLISH = "dashboards.publish"
    AUDIT_READ = "audit.read"
    SETTINGS_MANAGE = "settings.manage"
    KNOWLEDGE_MANAGE = "knowledge.manage"
    BRANDING_MANAGE = "branding.manage"
    ASK_FREEHAND = "ask.freehand"
    DASHBOARDS_GROUP = "dashboards.group"
    PACKS_AUTHOR = "packs.author"  # author the tenant's packs in Admin > Packs (DEC-26, US-232; migration 0049)


# Given to one person on one connection by a grant (US-161), never through a role: Admins get them by grant too.
GRANTABLE: frozenset[str] = frozenset({Perm.ASK_FREEHAND})

SYSTEM_LEVELS = ("admin", "creator", "viewer")  # highest first (E-01: highest level wins)
