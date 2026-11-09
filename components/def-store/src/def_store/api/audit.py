"""Audit log API endpoints for the Def-Store service."""


from beanie.odm.enums import SortDirection
from fastapi import APIRouter, Depends, Query

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
    resolve_accessible_namespaces,
    resolve_or_404,
    resolve_term_by_fields_or_404,
)

from ..models.api_models import AuditLogEntry, AuditLogResponse
from ..models.audit_log import AUDIT_ACTIONS_TEXT, AuditAction, TermAuditLog
from .auth import require_api_key

router = APIRouter(prefix="/audit", tags=["Audit Log"])


def _to_audit_entry(log: TermAuditLog) -> AuditLogEntry:
    """Convert TermAuditLog document to API response."""
    return AuditLogEntry(
        namespace=log.namespace,
        term_id=log.term_id,
        terminology_id=log.terminology_id,
        action=log.action,
        changed_at=log.changed_at,
        changed_by=log.changed_by,
        changed_fields=log.changed_fields,
        previous_values=log.previous_values,
        new_values=log.new_values,
        comment=log.comment
    )


async def _audited_namespace(
    identity: UserIdentity, entity_namespace: str | None, id_field: str, entity_id: str,
) -> str | None:
    """The namespace whose audit rows an id-scoped door reads, after checking
    read on it — None when there is no history to read.

    Audit rows expose previous and new values, so the read gate applies even
    when the entity itself is gone (hard-deleted): the namespace then comes
    from the entity's most recent audit row. Reads are scoped to that one
    namespace — a terminology_id is unique per namespace only, and another
    namespace's rows under the same id are not this entity's history.
    """
    namespace = entity_namespace
    if namespace is None:
        latest = await TermAuditLog.find({id_field: entity_id})\
            .sort([("changed_at", SortDirection.DESCENDING)]).first_or_none()
        if latest is None:
            return None
        namespace = latest.namespace
    await check_namespace_permission(identity, namespace, "read")
    return namespace


async def _page(query: dict, page: int, page_size: int) -> AuditLogResponse:
    total = await TermAuditLog.find(query).count()
    logs = await TermAuditLog.find(query)\
        .sort([("changed_at", SortDirection.DESCENDING)])\
        .skip((page - 1) * page_size)\
        .limit(page_size)\
        .to_list()
    return AuditLogResponse(
        items=[_to_audit_entry(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size
    )


def _empty(page: int, page_size: int) -> AuditLogResponse:
    return AuditLogResponse(items=[], total=0, page=page, page_size=page_size)


@router.get(
    "/terms/{term_id}",
    response_model=AuditLogResponse,
    summary="Get audit log for a term",
    description="Retrieve the complete change history for a specific term."
)
async def get_term_audit_log(
    term_id: str,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    terminology: str | None = Query(
        None,
        description="Terminology scoping a value-form term identifier. When "
                    "present, the identifier is treated as the OPAQUE raw value "
                    "(never colon-parsed) — required for values that themselves "
                    "contain ':' (OBO ids like GO:0000278).",
    ),
    identity: UserIdentity = Depends(require_api_key)
) -> AuditLogResponse:
    """Get audit log entries for a specific term."""
    if terminology is not None:
        # Field-form door: the identifier is the raw value, uninterpreted
        term_id = await resolve_term_by_fields_or_404(
            term_id, terminology, namespace, param_name="term_id"
        )
    else:
        term_id = await resolve_or_404(term_id, "term", namespace=namespace, param_name="term_id")

    from ..models.term import Term as _Term
    existing = await _Term.find_one({"term_id": term_id})
    ns = await _audited_namespace(
        identity, existing.namespace if existing else None, "term_id", term_id,
    )
    if ns is None:
        return _empty(page, page_size)
    return await _page({"namespace": ns, "term_id": term_id}, page, page_size)


@router.get(
    "/terminologies/{terminology_id}",
    response_model=AuditLogResponse,
    summary="Get audit log for a terminology",
    description="Retrieve the change history for all terms in a terminology."
)
async def get_terminology_audit_log(
    terminology_id: str,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    action: AuditAction | None = Query(None, description=f"Filter by action: {AUDIT_ACTIONS_TEXT}"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> AuditLogResponse:
    """Get audit log entries for all terms in a terminology."""
    terminology_id = await resolve_or_404(terminology_id, "terminology", namespace=namespace, param_name="terminology_id")

    from ..models.terminology import Terminology as _T
    lookup = {"terminology_id": terminology_id}
    if namespace:
        lookup["namespace"] = namespace
    existing = await _T.find_one(lookup)
    ns = await _audited_namespace(
        identity, existing.namespace if existing else None, "terminology_id", terminology_id,
    )
    if ns is None:
        return _empty(page, page_size)
    query: dict = {"namespace": ns, "terminology_id": terminology_id}
    if action:
        query["action"] = action
    return await _page(query, page, page_size)


@router.get(
    "",
    response_model=AuditLogResponse,
    summary="Get recent audit log entries",
    description="Retrieve recent changes across all terminologies."
)
async def get_recent_audit_log(
    action: AuditAction | None = Query(None, description=f"Filter by action: {AUDIT_ACTIONS_TEXT}"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> AuditLogResponse:
    """Get recent audit log entries across all terminologies."""
    # CASE-384 follow-up — restrict to namespaces the caller can read.
    # Superadmin gets None (no filter). Audit logs join to TermAuditLog
    # which stores namespace on each row.
    accessible = await resolve_accessible_namespaces(identity)

    query: dict = {}
    if action:
        query["action"] = action
    if accessible is not None:
        query["namespace"] = {"$in": accessible}

    return await _page(query, page, page_size)
