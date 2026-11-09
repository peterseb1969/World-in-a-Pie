"""App attachments: put an app under the umbrella, list, deactivate.

POST /attachments upserts by `name`, bulk-first: once the request body
validates, the answer is 200 with per-item results. An item the request
schema refuses (an unknown field, a name outside the pattern, a missing
`auth_required` or `allowed_groups`) rejects the whole request with 422, and
nothing is written. Writing an inactive attachment again revives it. There is no hard
delete: POST /attachments/deactivate retires one, and its route goes away
from the router's app edge.

Writing needs platform admin: an attached app is served on WIP's origin and
is trusted with the sessions of everyone who visits it. Reading is open to a
platform admin and to the router's app edge (group `wip-app-edge`).

A write replaces the whole record; `auth_required` and `allowed_groups` are
required on every item, so a partial write fails instead of widening access.

Per-item error codes: `validation_failed` (details.rule: `upstream_invalid`,
`policy_unavailable` or `duplicate_name`), `forbidden` (details.rule:
`upstream_blocked`), `name_collision` (a name the install's own apps route
under), `managed_by_deployer` (a record the deployer owns),
`concurrency_conflict` (another request created the same name at the same
moment; retry), `not_found` (deactivating a name with no attachment),
`invalid_state` (details.rule `no_app_edge`: the install runs no app edge, so
every write is refused; deactivating still works).
"""

import logging
import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from wip_auth import get_identity_string

from ..models.attachment import (
    NAME_PATTERN,
    AppAttachment,
    AttachmentBulkResponse,
    AttachmentBulkResultItem,
    AttachmentFields,
    AttachmentListResponse,
    AttachmentRequest,
    AttachmentResponse,
    EdgeState,
)
from ..services.attachments import (
    AttachPolicyUnavailable,
    UpstreamInvalid,
    check_upstream,
    load_policy,
    resolve_install_addresses,
)
from ..services.auth import require_admin_key, require_attachment_reader
from ..services.managed import MANAGED_BY_DEPLOYER

logger = logging.getLogger("registry.attachments")

router = APIRouter()

NO_APP_EDGE_MESSAGE = (
    "This install runs no app edge, so nothing would serve the attachment. "
    "An operator enables it with `wip-deploy add-module app-edge`."
)


def _error(index: int, name: str, code: str, message: str, rule: str | None = None) -> AttachmentBulkResultItem:
    return AttachmentBulkResultItem(
        index=index,
        status="error",
        name=name,
        error=message,
        error_code=code,
        details={"rule": rule} if rule else None,
    )


def _managed(index: int, doc: AppAttachment) -> AttachmentBulkResultItem:
    return _error(
        index, doc.name, MANAGED_BY_DEPLOYER,
        f"Attachment {doc.name!r} is managed by wip-deploy; change it with wip-deploy, "
        "not through the API",
    )


def _envelope(results: list[AttachmentBulkResultItem]) -> AttachmentBulkResponse:
    failed = sum(1 for r in results if r.status == "error")
    return AttachmentBulkResponse(
        results=results, total=len(results), succeeded=len(results) - failed, failed=failed
    )


@router.post(
    "",
    response_model=AttachmentBulkResponse,
    summary="Attach apps under the umbrella (upsert by name)",
)
async def upsert_attachments(
    items: list[AttachmentRequest],
    _admin: str = Depends(require_admin_key),
) -> AttachmentBulkResponse:
    """Create, update or revive attachments. Each item's status is
    `created`, `updated`, `unchanged` or `error`."""
    try:
        policy = load_policy()
    except AttachPolicyUnavailable as e:
        return _envelope(
            [_error(i, it.name, "validation_failed", f"Attach policy unavailable: {e}", "policy_unavailable")
             for i, it in enumerate(items)]
        )
    if not policy.app_edge:
        return _envelope(
            [_error(i, it.name, "invalid_state", NO_APP_EDGE_MESSAGE, "no_app_edge")
             for i, it in enumerate(items)]
        )
    reserved = set(policy.reserved_names)
    install_addresses = await resolve_install_addresses(policy)
    who = get_identity_string()
    results: list[AttachmentBulkResultItem] = []
    seen: set[str] = set()

    for i, item in enumerate(items):
        if item.name in seen:
            results.append(_error(i, item.name, "validation_failed", f"{item.name!r} appears twice in this request", "duplicate_name"))
            continue
        seen.add(item.name)
        if item.name in reserved:
            results.append(_error(
                i, item.name, "name_collision",
                f"{item.name!r} is the label of an app this install deploys: its origin is taken",
            ))
            continue
        try:
            check = await check_upstream(item.upstream, policy, install_addresses)
        except UpstreamInvalid as e:
            results.append(_error(i, item.name, "validation_failed", str(e), "upstream_invalid"))
            continue
        if check.blocked:
            results.append(_error(
                i, item.name, "forbidden",
                f"Upstream refused: {check.blocked}. The router's app edge must never "
                "proxy into the install or to the host's special addresses.",
                "upstream_blocked",
            ))
            continue

        fields = item.model_dump(include=set(AttachmentFields.model_fields))
        existing = await AppAttachment.find_one(AppAttachment.name == item.name)
        now = datetime.now(UTC)
        if existing is not None and existing.managed_by is not None:
            results.append(_managed(i, existing))
            continue
        if existing is None:
            doc = AppAttachment(name=item.name, created_by=who, updated_by=who, **fields)
            try:
                await doc.insert()
            except DuplicateKeyError:
                results.append(_error(
                    i, item.name, "concurrency_conflict",
                    f"Another request created {item.name!r} at the same moment; retry",
                ))
                continue
            status = "created"
        else:
            current = existing.model_dump(include=set(AttachmentFields.model_fields))
            if current == fields and existing.status == "active":
                status = "unchanged"
                doc = existing
            else:
                for k, v in fields.items():
                    setattr(existing, k, v)
                existing.status = "active"
                existing.updated_at = now
                existing.updated_by = who
                await existing.save()
                doc = existing
                status = "updated"
        if status != "unchanged":
            logger.info("Attachment %s %s: -> %s by %s", item.name, status, item.upstream, who)
        results.append(AttachmentBulkResultItem(
            index=i, status=status, id=doc.name, name=doc.name,
            warnings=check.warnings,
        ))
    return _envelope(results)


class DeactivateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=NAME_PATTERN)


@router.post(
    "/deactivate",
    response_model=AttachmentBulkResponse,
    summary="Deactivate attachments (never deleted)",
)
async def deactivate_attachments(
    items: list[DeactivateItem],
    _admin: str = Depends(require_admin_key),
) -> AttachmentBulkResponse:
    who = get_identity_string()
    results: list[AttachmentBulkResultItem] = []
    for i, item in enumerate(items):
        doc = await AppAttachment.find_one(AppAttachment.name == item.name)
        if doc is None:
            results.append(_error(i, item.name, "not_found", f"No attachment named {item.name!r}"))
            continue
        if doc.managed_by is not None:
            results.append(_managed(i, doc))
            continue
        if doc.status == "inactive":
            status = "unchanged"
        else:
            doc.status = "inactive"
            doc.updated_at = datetime.now(UTC)
            doc.updated_by = who
            await doc.save()
            status = "deactivated"
            logger.info("Attachment %s deactivated by %s", doc.name, who)
        results.append(AttachmentBulkResultItem(index=i, status=status, id=doc.name, name=doc.name))
    return _envelope(results)


def _edge_state() -> EdgeState:
    """Whether this install serves attachments, as the Registry reads the
    attach policy. A read never fails on the policy: a file the Registry
    cannot use reads as `policy_unavailable`. That is the Registry's reading
    only: the app edge reads the file on its own and never consults this, so
    it may still serve."""
    try:
        policy = load_policy()
    except AttachPolicyUnavailable:
        return "policy_unavailable"
    return "serving" if policy.app_edge else "no_app_edge"


@router.get("", response_model=AttachmentListResponse, summary="List attachments")
async def list_attachments(
    status: str | None = Query(None, pattern="^(active|inactive)$", description="Only this status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    _reader: object = Depends(require_attachment_reader),
) -> AttachmentListResponse:
    """Attachments sorted by name. Each carries `served` and, when not served,
    `not_served_reason`."""
    edge = _edge_state()
    query = AppAttachment.find(AppAttachment.status == status) if status else AppAttachment.find_all()
    total = await query.count()
    docs = await query.sort("+name").skip((page - 1) * page_size).limit(page_size).to_list()
    return AttachmentListResponse(
        items=[AttachmentResponse.of(d, edge) for d in docs],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get("/{name}", response_model=AttachmentResponse, summary="Get one attachment")
async def get_attachment(
    name: str,
    _reader: object = Depends(require_attachment_reader),
) -> AttachmentResponse:
    doc = await AppAttachment.find_one(AppAttachment.name == name)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"No attachment named {name!r}")
    return AttachmentResponse.of(doc, _edge_state())
