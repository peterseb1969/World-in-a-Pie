"""System backup and restore of the Registry's auth state (platform admins only).

The archive holds every stored API key (bcrypt hashes, never plaintexts),
every namespace grant and every namespace reservation; namespace data
backups never carry them. See services/system_backup.py for the rules.
The archive is sensitive: keep it like the install's own secrets.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from wip_auth import get_identity_string

from ..services import key_shadowing
from ..services.auth import require_admin_key
from ..services.system_backup import (
    SaltMismatch,
    SystemArchive,
    SystemRestoreReport,
    build_archive,
    restore_archive,
)
from .api_keys import _api_key_provider_or_none

logger = logging.getLogger("registry.system_backup")

router = APIRouter()


@router.post(
    "/system-backup",
    response_model=SystemArchive,
    summary="Back up the Registry's API keys, grants and reservations",
)
async def system_backup(_admin: str = Depends(require_admin_key)) -> SystemArchive:
    """Return the system backup archive: every stored API key record (its
    bcrypt hash and metadata, never a plaintext; managed records included),
    every namespace grant and every namespace reservation, with the OIDC
    issuer and a fingerprint of the API key hash salt.

    Config keys, the master key among them, are not records and are not
    included. Namespace data is not included either; back it up with the
    namespace backup. The archive is sensitive: anyone holding it holds every
    key hash."""
    archive = await build_archive()
    logger.warning(
        "System backup taken by %s: %d key(s), %d grant(s), %d reservation(s)",
        get_identity_string(), len(archive.api_keys), len(archive.grants),
        len(archive.reservations),
    )
    return archive


@router.post(
    "/system-restore",
    response_model=SystemRestoreReport,
    summary="Restore a system backup (dry run by default)",
)
async def system_restore(
    archive: SystemArchive,
    dry_run: bool = Query(True, description="Report what would change, write nothing"),
    overwrite: bool = Query(
        False, description="Replace records that exist here with different content"
    ),
    include_user_grants: bool = Query(
        False,
        description="Restore user grants although the archive comes from another OIDC issuer",
    ),
    _admin: str = Depends(require_admin_key),
) -> SystemRestoreReport:
    """Restore an archive from POST /system-backup.

    Upserts by identity (key name; grant namespace, subject and subject type;
    reservation prefix) and never deletes. Restored records arrive unmanaged,
    with origin "restored". A record wip-deploy manages here, a grant to a key
    whose grants the key file declares, and a key named like a loaded config
    key are never replaced. A grant on a namespace that does not exist is
    skipped: restore the namespace data first, then re-run. User grants from
    another OIDC issuer are skipped unless include_user_grants is set.

    Refused (409) when the archive's API key hash salt differs from this
    instance's. Runs as a dry run unless dry_run=false."""
    provider = _api_key_provider_or_none()
    config_names = provider.config_key_names() if provider is not None else frozenset()
    try:
        report, written = await restore_archive(
            archive,
            dry_run=dry_run,
            overwrite=overwrite,
            include_user_grants=include_user_grants,
            config_key_names=config_names,
            restored_by=get_identity_string(),
        )
    except SaltMismatch as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    if written and provider is not None:
        # A restored key with a config key's plaintext must be withheld
        # before the Registry's provider learns the record.
        await key_shadowing.reconcile(provider)
        async with key_shadowing.provider_lock:
            for old_hash, doc in written:
                if old_hash:
                    provider.remove_key(old_hash)
                provider.remove_key(doc.key_hash)
                if doc.enabled and not key_shadowing.is_shadowed(doc.name):
                    provider.add_key(key_shadowing.runtime_record(doc))

    if not dry_run:
        logger.warning(
            "System restore by %s: keys +%d ~%d, grants +%d ~%d, reservations +%d ~%d",
            get_identity_string(),
            len(report.api_keys.created), len(report.api_keys.replaced),
            len(report.grants.created), len(report.grants.replaced),
            len(report.reservations.created), len(report.reservations.replaced),
        )
    return report
