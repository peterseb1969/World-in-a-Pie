"""Namespace deletion API endpoints.

Provides dry-run, delete, resume, and status endpoints for
crash-safe namespace deletion with persistent journals.
"""

from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from wip_auth import UserIdentity, get_auth_config, get_identity_string

from ..models.namespace import Namespace, set_unless_locked, unwritten_answer
from ..services.auth import require_admin_key, require_api_key
from ..services.namespace_deletion import (
    DeletionAlreadyRunning,
    NamespaceDeletionService,
    deletion_refusals,
)

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
    deleted_by: str = Query(
        None,
        description=(
            "A free-text note on who asked for the deletion, recorded in the "
            "journal as requested_note. The journal's requested_by is always "
            "the authenticated caller."
        ),
    ),
    api_key: str = Depends(require_admin_key),
):
    """Delete a namespace and all its data.

    Requires deletion_mode='full' on the namespace. Creates a persistent
    journal for crash-safe execution across MongoDB, the object store, and PostgreSQL.

    - dry_run=true: Returns impact report (entity counts, inbound references)
    - force=true: Proceeds even if other namespaces reference this one
    """
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(404, f"Namespace not found: {prefix}")

    if dry_run:
        report = await _deletion_service.dry_run(prefix)
        return report

    # The refusals the dry run reports, in the same order.
    if refusals := deletion_refusals(ns):
        _rule, message, status = refusals[0]
        raise HTTPException(status, message)

    try:
        journal = await _deletion_service.start_deletion(
            prefix=prefix,
            force=force,
            requested_by=get_identity_string(),
            requested_note=deleted_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    # Both terminal-success states carry the summary; completed_with_warnings
    # additionally means a best-effort object-store/PostgreSQL step degraded — the
    # per-step errors are in the deletion-status journal view.
    if journal.status in ("completed", "completed_with_warnings"):
        return {
            "status": journal.status,
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
    identity: UserIdentity = Depends(require_api_key),
):
    """Get the current journal state for an in-progress or completed deletion.

    Platform admins only — the callers who can start a deletion. Anyone else
    gets 404, as for a namespace with no journal: the journal lists where the
    namespace's data lived and what other namespaces referenced in it.
    """
    not_found = HTTPException(404, f"No deletion journal found for namespace '{prefix}'")
    if not identity.has_any_group(get_auth_config().admin_groups):
        raise not_found
    journal = await _deletion_service.get_deletion_status(prefix)
    if not journal:
        raise not_found

    return {
        "namespace": journal.namespace,
        "status": journal.status,
        "requested_by": journal.requested_by,
        "requested_note": journal.requested_note,
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
    """Resume an unfinished deletion from where it left off.

    Picks up the namespace's deletion that is still in progress, stopped on a
    failed step, or completed with degraded best-effort steps (stored files,
    reporting rows), and runs every step not completed. A namespace locked
    with no journal gets one. 409 while the deletion is running already.
    There is no abandon: a partly deleted namespace is finished, not undone.
    """
    try:
        journal = await _deletion_service.resume_deletion(prefix)
    except DeletionAlreadyRunning as e:
        raise HTTPException(409, str(e)) from None
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
    updated_by: str = Query(
        None, description="A free-text note on who made the change, kept as actor_note; updated_by is the authenticated caller",
    ),
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

    # A namespace mid-deletion keeps the mode it is being deleted under.
    if not await set_unless_locked(ns, {
        "deletion_mode": cast(Literal["retain", "full"], deletion_mode),
        "updated_at": datetime.now(UTC), "updated_by": get_identity_string(),
        "actor_note": updated_by,
    }):
        raise HTTPException(*await unwritten_answer(ns, prefix))

    return {
        "prefix": prefix,
        "deletion_mode": ns.deletion_mode,
        "status": ns.status,
    }
