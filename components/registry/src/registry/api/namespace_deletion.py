"""Namespace deletion API endpoints.

Provides dry-run, delete, resume, and status endpoints for
crash-safe namespace deletion with persistent journals.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models.namespace import Namespace
from ..services.auth import require_admin_key, require_api_key
from ..services.namespace_deletion import NamespaceDeletionService

router = APIRouter()

_deletion_service = NamespaceDeletionService()


def get_deletion_service() -> NamespaceDeletionService:
    """Get the singleton deletion service (enables startup recovery)."""
    return _deletion_service


@router.delete(
    "/{prefix}",
    summary="Delete namespace (with journal)",
)
async def delete_namespace(
    prefix: str,
    dry_run: bool = Query(False, description="Return impact report without making changes"),
    force: bool = Query(False, description="Proceed despite inbound references"),
    deleted_by: str = Query(None, description="User requesting deletion"),
    api_key: str = Depends(require_admin_key),
):
    """Delete a namespace and all its data.

    Requires deletion_mode='full' on the namespace. Creates a persistent
    journal for crash-safe execution across MongoDB, MinIO, and PostgreSQL.

    - dry_run=true: Returns impact report (entity counts, inbound references)
    - force=true: Proceeds even if other namespaces reference this one
    """
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(404, f"Namespace not found: {prefix}")

    if dry_run:
        report = await _deletion_service.dry_run(prefix)
        return report

    # Validation
    if prefix == "wip":
        raise HTTPException(400, "Cannot delete the default 'wip' namespace")

    if ns.deletion_mode != "full":
        raise HTTPException(
            400,
            f"Namespace '{prefix}' has deletion_mode='retain'. "
            "Set deletion_mode='full' first via PATCH."
        )

    if ns.status == "locked":
        raise HTTPException(409, f"Namespace '{prefix}' is already locked (deletion in progress)")

    try:
        journal = await _deletion_service.start_deletion(
            prefix=prefix,
            force=force,
            requested_by=deleted_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    if journal.status == "completed":
        return {
            "status": "completed",
            "namespace": prefix,
            "summary": journal.summary,
        }
    else:
        return {
            "status": journal.status,
            "namespace": prefix,
            "steps_completed": sum(1 for s in journal.steps if s.status == "completed"),
            "steps_total": len(journal.steps),
        }


@router.get(
    "/{prefix}/deletion-status",
    summary="Check deletion status",
)
async def deletion_status(
    prefix: str,
    api_key: str = Depends(require_api_key),
):
    """Get the current journal state for an in-progress or completed deletion."""
    journal = await _deletion_service.get_deletion_status(prefix)
    if not journal:
        raise HTTPException(404, f"No deletion journal found for namespace '{prefix}'")

    return {
        "namespace": journal.namespace,
        "status": journal.status,
        "requested_by": journal.requested_by,
        "requested_at": journal.requested_at.isoformat() if journal.requested_at else None,
        "completed_at": journal.completed_at.isoformat() if journal.completed_at else None,
        "force": journal.force,
        "broken_references": [r.model_dump() for r in journal.broken_references],
        "steps": [s.model_dump() for s in journal.steps],
        "summary": journal.summary,
    }


@router.post(
    "/{prefix}/resume-delete",
    summary="Resume incomplete deletion",
)
async def resume_deletion(
    prefix: str,
    api_key: str = Depends(require_admin_key),
):
    """Resume an incomplete deletion from where it left off.

    Used when a backend was unavailable during the initial attempt.
    """
    try:
        journal = await _deletion_service.resume_deletion(prefix)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    return {
        "status": journal.status,
        "namespace": prefix,
        "summary": journal.summary,
    }


@router.patch(
    "/{prefix}",
    summary="Update namespace deletion mode",
)
async def update_deletion_mode(
    prefix: str,
    deletion_mode: str = Query(..., description="New deletion mode: 'retain' or 'full'"),
    confirm_enable_deletion: bool = Query(
        False,
        description="Required when changing from 'retain' to 'full'"
    ),
    updated_by: str = Query(None, description="User making the change"),
    api_key: str = Depends(require_admin_key),
):
    """Update a namespace's deletion_mode.

    Changing retain -> full requires confirm_enable_deletion=true.
    The 'wip' namespace is always retain and cannot be changed.
    """
    if deletion_mode not in ("retain", "full"):
        raise HTTPException(400, "deletion_mode must be 'retain' or 'full'")

    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(404, f"Namespace not found: {prefix}")

    if prefix == "wip" and deletion_mode == "full":
        raise HTTPException(400, "Cannot enable deletion on the default 'wip' namespace")

    if ns.deletion_mode == "retain" and deletion_mode == "full" and not confirm_enable_deletion:
        raise HTTPException(
            400,
            "Changing from 'retain' to 'full' requires confirm_enable_deletion=true"
        )

    ns.deletion_mode = deletion_mode
    ns.updated_at = datetime.now(UTC)
    ns.updated_by = updated_by
    await ns.save()

    return {
        "prefix": prefix,
        "deletion_mode": ns.deletion_mode,
        "status": ns.status,
    }
