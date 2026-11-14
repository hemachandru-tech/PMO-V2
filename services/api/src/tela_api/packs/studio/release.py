"""Release a tenant pack version with checks and four eyes (ENH-025, US-238; FEAT-075; DEC-26 points 2 and 5).

* ``POST /packs/{id}/release-checks`` ``{connection_id}`` — run the release checks (``release_checks``) on a chosen
  test connection, with the diff against the previous released version. Nothing is saved.
* ``GET /packs/{id}/diff`` — what this version adds, changes and removes against the previous released one.
* ``POST /packs/{id}/submit`` ``{connection_id, reasons, note}`` (``If-Match``) — the author submits a draft for review:
  the checks run, errors refuse it, each warning needs a reason (``reasons`` by issue id). The draft is then locked
  (edits answer 409 until it is withdrawn or sent back) and the reviewers are told.
* ``POST /packs/{id}/withdraw`` — take a draft back out of review to edit it.
* ``POST /packs/{id}/return`` ``{reason}`` — a reviewer sends it back with a reason; the author is told.
* ``POST /packs/{id}/release`` — a reviewer releases it: an Admin (``packs.author``) or a certifier
  (``certification.decide``) who is neither its author nor the one who submitted it (the database refuses it too).
  The submitted revision must be the current one; the checks run again on the same connection and must still pass
  with no warning left without a reason. The manifest is stored with its starter reports expanded (the form
  ``load-packs`` stores and install, match and updates read) and the version is frozen; the connections with an older
  version installed get the US-120 update notice.
* ``POST /packs/{id}/next-version`` ``{version?}`` — editing a released version opens the next draft version (the next
  minor by default) with its manifest as written, knowledge and attachments; the released one keeps serving installs
  until the new one is released.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_api.audit import service as audit
from tela_api.content.common import notify, users_with_permission
from tela_api.core.db import fetch_all, tenant_tx
from tela_api.identity.deps import get_principal
from tela_api.identity.permissions import Perm
from tela_api.identity.principal import Principal
from tela_api.packs.source import document_sha
from tela_api.packs.studio import attachments, store
from tela_api.packs.studio import release_checks as rc
from tela_api.packs.studio.deps import AUTHOR, rid
from tela_api.packs.updates import notify_updates, version_key
from tela_api.query.service import QueryService
from tela_common.errors import ErrorCode, TelaError

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])
MIN_REASON = 3


async def _reviewer(principal: Principal = Depends(get_principal)) -> Principal:
    """An Admin who authors packs or a certifier (DEC-26 point 5)."""
    if not (principal.has(Perm.PACKS_AUTHOR) or principal.has(Perm.CERTIFICATION_DECIDE)):
        raise TelaError(ErrorCode.FORBIDDEN, "You don't have permission to do that.",
                        {"missing": [Perm.PACKS_AUTHOR, Perm.CERTIFICATION_DECIDE]})
    return principal


REVIEWER = Depends(_reviewer)


class ChecksIn(BaseModel):
    connection_id: str = Field(min_length=36, max_length=36)


class SubmitIn(ChecksIn):
    reasons: dict[str, str] = Field(default_factory=dict, description="A reason for each warning, by its id")
    note: str | None = Field(default=None, max_length=1000)


class ReturnIn(BaseModel):
    reason: str = Field(min_length=MIN_REASON, max_length=1000)


class NextIn(BaseModel):
    version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


# --------------------------------------------------------------------------- helpers


async def _attachment_hashes(conn: AsyncConnection, pack_id: str) -> list[str]:
    rows = await fetch_all(conn, "SELECT content_hash FROM model.pack_attachments WHERE pack_id = CAST(:p AS uuid)",
                           {"p": pack_id})
    return [str(r["content_hash"]) for r in rows]


async def _revision(conn: AsyncConnection, row: store.PackRow, docs: dict[str, Any]) -> str:
    return rc.content_revision(row, docs, await _attachment_hashes(conn, row.id))


async def _siblings(conn: AsyncConnection, row: store.PackRow) -> list[store.PackRow]:
    """Every version of this pack (same key and owner)."""
    return [r for r in await store.all_packs(conn) if r.pack_key == row.pack_key and r.tenant_id == row.tenant_id]


async def previous_release(conn: AsyncConnection, row: store.PackRow) -> store.PackRow | None:
    """The newest released (or retired) version below this one."""
    older = [r for r in await _siblings(conn, row) if r.status != "draft" and r.id != row.id
             and version_key(r.pack_version) < version_key(row.pack_version)]
    return max(older, key=lambda r: version_key(r.pack_version), default=None)


async def _diff(conn: AsyncConnection, row: store.PackRow, docs: dict[str, Any]) -> dict[str, Any]:
    prev = await previous_release(conn, row)
    before = (rc.expanded(prev.manifest, await store.documents(conn, prev.id)), await store.documents(conn, prev.id)) \
        if prev else None
    changes = rc.diff(before, (rc.expanded(row.manifest, docs), docs))
    return {"against": {"id": prev.id, "version": prev.pack_version} if prev else None, "changes": changes,
            "summary": rc.diff_summary(changes)}


async def run_checks(conn: AsyncConnection, request: Request, principal: Principal, row: store.PackRow,
                     docs: dict[str, Any], connection_id: str) -> rc.Checks:
    checks = rc.static_checks(row.manifest, docs)
    if checks.errors:  # a pack that doesn't read can't be bound
        return checks
    service: QueryService = request.app.state.query_service
    return checks.merge(await rc.source_checks(conn, service, principal, row, row.manifest, docs, connection_id,
                                               rid(request)))


def _versions_rule(row: store.PackRow, siblings: list[store.PackRow]) -> None:
    """A released version number is never reused, and a new release is above every released one (semver)."""
    released = [r for r in siblings if r.status != "draft" and r.id != row.id]
    top = max(released, key=lambda r: version_key(r.pack_version), default=None)
    if top is not None and version_key(row.pack_version) <= version_key(top.pack_version):
        raise TelaError(ErrorCode.VALIDATION_ERROR, f"Version {top.pack_version} is already released. Give this "
                                                    f"draft a higher version than {top.pack_version}.",
                        {"field": "pack_version"})


def _missing_reasons(checks: rc.Checks, reasons: dict[str, str]) -> list[str]:
    return [w["id"] for w in checks.warnings if len((reasons.get(w["id"]) or "").strip()) < MIN_REASON]


async def _reviewers(conn: AsyncConnection, except_: set[str | None]) -> set[str]:
    users = set(await users_with_permission(conn, Perm.PACKS_AUTHOR)) | set(
        await users_with_permission(conn, Perm.CERTIFICATION_DECIDE))
    return {u for u in users if u not in except_}


def _not_author(row: store.PackRow, principal: Principal, what: str) -> None:
    if principal.user_id in (row.created_by, row.submitted_by):
        raise TelaError(ErrorCode.FORBIDDEN, f"You authored or submitted this version, so someone else must {what} "
                                             "it (four eyes).", {"reason": "four_eyes"})


def _tenant_draft_in_review(row: store.PackRow) -> None:
    if row.system or row.status != "draft":
        what = "a system pack" if row.system else row.status
        raise TelaError(ErrorCode.CONFLICT, f"Version {row.pack_version} is {what} and is not waiting for review.")
    if not row.in_review:
        raise TelaError(ErrorCode.CONFLICT, "This version has not been submitted for review.",
                        {"reason": "not_submitted"})


def _link(row: store.PackRow) -> str:
    return f"/admin/packs/{row.id}?tab=versions"


# --------------------------------------------------------------------------- routes


@router.get("/{pack_id}/review")
async def get_review(pack_id: str, principal: Principal = REVIEWER) -> dict[str, Any]:
    """Where this version is in its review, who did what (names), what was submitted (the checks, each warning's
    reason, the note) and what the viewer may do now."""
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        state = store.review_state(row)
        people = await _names(conn, [row.created_by, row.submitted_by, row.returned_by, row.released_by])
    me = principal.user_id
    author = me in (row.created_by, row.submitted_by)
    can_author = principal.has(Perm.PACKS_AUTHOR)
    return {"state": state, "people": people, "review": row.review,
            "you": {"author": author,
                    "can_submit": can_author and row.editable,
                    "can_withdraw": can_author and row.in_review,
                    "can_release": row.in_review and not author,
                    "can_return": row.in_review and not author,
                    "can_open_next": can_author and not row.system and row.status != "draft"}}


@router.post("/{pack_id}/release-checks")
async def release_checks(pack_id: str, body: ChecksIn, request: Request,
                         principal: Principal = REVIEWER) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        docs = await store.documents(conn, row.id)
        checks = await run_checks(conn, request, principal, row, docs, body.connection_id)
        changes = await _diff(conn, row, docs)
        revision = await _revision(conn, row, docs)
    return {"checks": checks.out(), "diff": changes, "revision": revision}


@router.get("/{pack_id}/diff")
async def get_diff(pack_id: str, principal: Principal = REVIEWER) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        return await _diff(conn, row, await store.documents(conn, row.id))


@router.post("/{pack_id}/submit")
async def submit(pack_id: str, body: SubmitIn, request: Request, response: Response,
                 if_match: str | None = Header(default=None), principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=True)
        store.require_editable(row)
        store.require_revision(row, if_match)
        _versions_rule(row, await _siblings(conn, row))
        docs = await store.documents(conn, row.id)
        checks = await run_checks(conn, request, principal, row, docs, body.connection_id)
        if checks.errors:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Fix the {len(checks.errors)} errors before submitting.",
                            {"checks": checks.out()})
        missing = _missing_reasons(checks, body.reasons)
        if missing:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Give a reason for each warning ({len(missing)} without one).",
                            {"missing_reasons": missing, "checks": checks.out()})
        changes = await _diff(conn, row, docs)
        revision = await _revision(conn, row, docs)
        review = {"connection_id": body.connection_id, "note": (body.note or "").strip() or None,
                  "reasons": {w["id"]: body.reasons[w["id"]].strip() for w in checks.warnings},
                  "checks": checks.out(), "diff": changes["summary"], "against": changes["against"]}
        await conn.execute(text(
            "UPDATE model.starter_packs SET submitted_by = CAST(:u AS uuid), submitted_at = now(), "
            "submitted_revision = :r, review = CAST(:rv AS jsonb), returned_by = NULL, returned_at = NULL, "
            "returned_reason = NULL WHERE id = CAST(:id AS uuid)"),
            {"u": principal.user_id, "r": revision, "rv": json.dumps(review, default=str), "id": row.id})
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.submitted", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "revision": revision,
                                    "connection": body.connection_id, "warnings": len(checks.warnings),
                                    "changes": changes["summary"]})
        await notify(conn, principal.tenant_id, await _reviewers(conn, {principal.user_id, row.created_by}),
                     kind="pack.review_requested", title=f"{row.title} {row.pack_version} is waiting for review",
                     body=f"Submitted with {len(checks.warnings)} warnings explained. Someone other than its author "
                          "releases it or sends it back.", link=_link(row), by=principal.user_id)
        saved = await store.load(conn, row.id)
    response.headers["ETag"] = saved.etag
    return {**store.summary(saved), "checks": checks.out(), "diff": changes}


@router.post("/{pack_id}/withdraw")
async def withdraw(pack_id: str, request: Request, response: Response,
                   principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=True)
        _tenant_draft_in_review(row)
        await _clear(conn, row)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.withdrawn", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request), details={"pack": row.pack_key, "version": row.pack_version})
        saved = await store.load(conn, row.id)
    response.headers["ETag"] = saved.etag
    return store.summary(saved)


async def _clear(conn: AsyncConnection, row: store.PackRow, *, returned_by: str | None = None,
                 reason: str | None = None) -> None:
    await conn.execute(text(
        "UPDATE model.starter_packs SET submitted_by = NULL, submitted_at = NULL, submitted_revision = NULL, "
        "returned_by = CAST(:by AS uuid), returned_at = CASE WHEN CAST(:by AS uuid) IS NULL THEN NULL ELSE now() END, "
        "returned_reason = :reason WHERE id = CAST(:id AS uuid)"), {"by": returned_by, "reason": reason, "id": row.id})


@router.post("/{pack_id}/return")
async def send_back(pack_id: str, body: ReturnIn, request: Request, response: Response,
                    principal: Principal = REVIEWER) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=True)
        _tenant_draft_in_review(row)
        _not_author(row, principal, "send back")
        reason = body.reason.strip()
        await _clear(conn, row, returned_by=principal.user_id, reason=reason)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.returned", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "reason": reason})
        await notify(conn, principal.tenant_id, {u for u in (row.submitted_by, row.created_by) if u},
                     kind="pack.review_returned", title=f"{row.title} {row.pack_version} was sent back",
                     body=reason, link=_link(row), by=principal.user_id)
        saved = await store.load(conn, row.id)
    response.headers["ETag"] = saved.etag
    return store.summary(saved)


@router.post("/{pack_id}/release")
async def release(pack_id: str, request: Request, response: Response,
                  principal: Principal = REVIEWER) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id, lock=True)
        _tenant_draft_in_review(row)
        _not_author(row, principal, "release")
        _versions_rule(row, await _siblings(conn, row))
        docs = await store.documents(conn, row.id)
        if await _revision(conn, row, docs) != row.submitted_revision:
            raise TelaError(ErrorCode.CONFLICT, "This version changed after it was submitted. Send it back so the "
                                                "author submits it again.", {"reason": "changed"})
        review = row.review or {}
        checks = await run_checks(conn, request, principal, row, docs, str(review.get("connection_id")))
        if checks.errors:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The release checks fail now. Send it back with the errors.",
                            {"checks": checks.out()})
        missing = _missing_reasons(checks, review.get("reasons") or {})
        if missing:
            raise TelaError(ErrorCode.VALIDATION_ERROR, "The checks give new warnings since it was submitted. Send it "
                                                        "back so the author explains them.",
                            {"missing_reasons": missing, "checks": checks.out()})
        changes = await _diff(conn, row, docs)
        stored = store.ordered({**rc.expanded(row.manifest, docs),
                                "released_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
        await conn.execute(text(
            "UPDATE model.starter_packs SET status = 'released', released_at = now(), released_by = CAST(:u AS uuid), "
            "manifest = CAST(:m AS jsonb), manifest_sha256 = :sha, review = CAST(:rv AS jsonb) "
            "WHERE id = CAST(:id AS uuid)"),
            {"u": principal.user_id, "m": json.dumps(stored, default=str), "sha": document_sha(stored),
             "rv": json.dumps({**review, "release_checks": checks.out()}, default=str), "id": row.id})
        models, notified = await notify_updates(conn, principal.tenant_id, pack_key=row.pack_key)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.released", object_type="pack",
                           object_id=row.id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "version": row.pack_version, "author": row.created_by,
                                    "submitted_by": row.submitted_by, "revision": row.submitted_revision,
                                    "changes": changes["summary"], "against": changes["against"],
                                    "connections_told": models})
        await notify(conn, principal.tenant_id, {u for u in (row.submitted_by, row.created_by) if u},
                     kind="pack.released", title=f"{row.title} {row.pack_version} is released",
                     body="It can now be installed, and connections with an older version are offered the update.",
                     link=_link(row), by=principal.user_id)
        saved = await store.load(conn, row.id)
        where = await store.installs(conn, [row.id])
    response.headers["ETag"] = saved.etag
    return {**store.summary(saved, where.get(row.id)), "diff": changes,
            "update_notice": {"connections": models, "notified": notified}}


def _next_minor(version: str) -> str:
    major, minor, _ = (int(p) for p in version.split("."))
    return f"{major}.{minor + 1}.0"


@router.post("/{pack_id}/next-version", status_code=201)
async def next_version(pack_id: str, request: Request, response: Response, body: NextIn | None = None,
                       principal: Principal = AUTHOR) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as conn:
        row = await store.load(conn, pack_id)
        if row.system:
            raise TelaError(ErrorCode.CONFLICT, "System packs are read-only. Clone the pack to edit it.")
        if row.status == "draft":
            raise TelaError(ErrorCode.CONFLICT, "This version is a draft: edit it here.")
        siblings = await _siblings(conn, row)
        draft = next((r for r in siblings if r.status == "draft"), None)
        if draft is not None:
            raise TelaError(ErrorCode.CONFLICT, f"Version {draft.pack_version} is already the next draft: open it.",
                            {"pack_id": draft.id})
        top = max((version_key(r.pack_version) for r in siblings), default=(0, 0, 0))
        version = body.version if body and body.version else _next_minor(".".join(map(str, top)))
        if version_key(version) <= top:
            raise TelaError(ErrorCode.VALIDATION_ERROR, f"Use a version above {'.'.join(map(str, top))}.",
                            {"field": "version"})
        docs = await store.documents(conn, row.id)
        head = store.as_written(dict(row.manifest), docs)
        head.update(pack_version=version, released_at=datetime.now(UTC).strftime("%Y-%m-%dT00:00:00Z"))
        new_id = await store.insert(conn, principal, head, docs, based_on=row.id)
        await attachments.copy_to_pack(conn, principal, row.id, new_id)
        await audit.record(conn, tenant_id=principal.tenant_id, action="pack.version_opened", object_type="pack",
                           object_id=new_id, outcome="success", actor_id=principal.user_id,
                           correlation_id=rid(request),
                           details={"pack": row.pack_key, "from": row.pack_version, "version": version})
        saved = await store.load(conn, new_id)
    response.headers["ETag"] = saved.etag
    return store.summary(saved)


async def _names(conn: AsyncConnection, user_ids: list[str | None]) -> dict[str, str]:
    """Display names of the people in a review, for the Versions tab."""
    ids = sorted({u for u in user_ids if u})
    if not ids:
        return {}
    rows = await fetch_all(conn, "SELECT id, display_name FROM iam.users WHERE id = ANY(CAST(:ids AS uuid[]))",
                           {"ids": ids})
    return {str(r["id"]): str(r["display_name"] or "") for r in rows}

