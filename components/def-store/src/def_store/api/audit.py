"""Audit log API endpoints for the Def-Store service."""


from fastapi import APIRouter, Depends, Query

from wip_auth import resolve_or_404

from ..models.api_models import AuditLogEntry, AuditLogResponse
from ..models.audit_log import TermAuditLog
from .auth import require_api_key

router = APIRouter(prefix="/audit", tags=["Audit Log"])


def _to_audit_entry(log: TermAuditLog) -> AuditLogEntry:
    """Convert TermAuditLog document to API response."""
    return AuditLogEntry(
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


@router.get(
    "/terms/{term_id}",
    response_model=AuditLogResponse,
    summary="Get audit log for a term",
    description="Retrieve the complete change history for a specific term."
)
async def get_term_audit_log(
    term_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
) -> AuditLogResponse:
    """Get audit log entries for a specific term."""
    term_id = await resolve_or_404(term_id, "term", namespace=None, param_name="term_id")
    query = {"term_id": term_id}

    total = await TermAuditLog.find(query).count()
    skip = (page - 1) * page_size

    logs = await TermAuditLog.find(query)\
        .sort([("changed_at", -1)])\
        .skip(skip)\
        .limit(page_size)\
        .to_list()

    return AuditLogResponse(
        items=[_to_audit_entry(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get(
    "/terminologies/{terminology_id}",
    response_model=AuditLogResponse,
    summary="Get audit log for a terminology",
    description="Retrieve the change history for all terms in a terminology."
)
async def get_terminology_audit_log(
    terminology_id: str,
    action: str | None = Query(None, description="Filter by action: created, updated, deprecated, deleted"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
) -> AuditLogResponse:
    """Get audit log entries for all terms in a terminology."""
    terminology_id = await resolve_or_404(terminology_id, "terminology", namespace=None, param_name="terminology_id")
    query = {"terminology_id": terminology_id}
    if action:
        query["action"] = action

    total = await TermAuditLog.find(query).count()
    skip = (page - 1) * page_size

    logs = await TermAuditLog.find(query)\
        .sort([("changed_at", -1)])\
        .skip(skip)\
        .limit(page_size)\
        .to_list()

    return AuditLogResponse(
        items=[_to_audit_entry(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get(
    "",
    response_model=AuditLogResponse,
    summary="Get recent audit log entries",
    description="Retrieve recent changes across all terminologies."
)
async def get_recent_audit_log(
    action: str | None = Query(None, description="Filter by action: created, updated, deprecated, deleted"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
) -> AuditLogResponse:
    """Get recent audit log entries across all terminologies."""
    query = {}
    if action:
        query["action"] = action

    total = await TermAuditLog.find(query).count()
    skip = (page - 1) * page_size

    logs = await TermAuditLog.find(query)\
        .sort([("changed_at", -1)])\
        .skip(skip)\
        .limit(page_size)\
        .to_list()

    return AuditLogResponse(
        items=[_to_audit_entry(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size
    )
