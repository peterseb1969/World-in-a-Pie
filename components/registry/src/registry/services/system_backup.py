"""System backup: the Registry's auth state, apart from any namespace's data.

A namespace (data) backup carries documents, templates and terminologies; it
never carries key hashes, grants or reservations, so merge and fresh restores
can re-map namespaces freely. This module backs up and restores those three
collections on their own:

- every stored API key record (the bcrypt hash and its metadata, never a
  plaintext), managed records included, so one archive reproduces the
  instance's auth state after a wipe or on other hardware;
- every namespace grant;
- every namespace reservation.

The master key and every other config key are not records here and are not
carried: the install's config re-creates them.

Restore rules:

- **The salt travels with the archive.** Runtime key hashes are salted with
  the salt the Registry keeps for the stack (key_salt). The archive carries
  it and its fingerprint. A restore onto a stack with the same salt proceeds.
  A restore onto a *fresh* stack adopts the archive's salt: fresh means no
  stored key that the install's files do not manage (no Console or restored
  key), so no hash on the stack depends on the old salt except the managed
  keys, which the import re-hashes from their plaintexts. A stack that is not
  fresh refuses another salt (409): its own keys would die. An archive
  without the salt itself (only its fingerprint) can never be adopted.
- **Upsert by identity, never delete.** A key is identified by its name, a
  grant by (namespace, subject, subject_type), a reservation by its prefix.
  A missing record is created; an identical one is left alone; a different
  one is replaced only with `overwrite`, otherwise listed and skipped. Records
  the archive does not hold are kept.
- **Every restored record arrives unmanaged**, `origin: "restored"`. The key
  import re-adopts any that the install's own files declare, under its own
  takeover rule; a restore invents no ownership rule.
- **A record wip-deploy manages on the target is never replaced**, nor is a
  grant to a key whose grants the key file declares, nor a key named like a
  loaded config key: the install's files own those.
- **User grants follow the OIDC issuer.** From the same issuer they are
  restored; from another one they are skipped and listed unless
  `include_user_grants` is set, since the same email may name someone else.
  Group and key grants are always restored.
- **A grant without its namespace is kept, orphaned.** A grant on a namespace
  that exists, or on a prefix reserved here, is written effective: a key that
  reserves a prefix holds admin on it before the namespace exists, and needs
  that grant to create it (reservations are restored first for that reason).
  Any other grant is written `orphaned` and listed: it gives nothing until it
  is activated, which happens when a platform admin creates the namespace
  (a data restore an admin runs does), or when a namespace admin activates
  it. A key that reserves the prefix later and creates the namespace does
  not inherit it. So the system and data restores work in either order. A
  grant the archive holds orphaned stays orphaned, wherever it lands.
- **Dry run by default.** The report lists what would be created, replaced,
  left unchanged and skipped (with the reason), and leads with a warning when
  the target holds auth state of its own: restoring old state brings back old
  access, including access revoked since the backup.
"""

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wip_auth import get_auth_config

from ..models.api_key import StoredAPIKey
from ..models.grant import NamespaceGrant
from ..models.namespace import Namespace
from ..models.reservation import RuntimeReservation
from .key_salt import adopt as adopt_salt
from .key_salt import current_salt

logger = logging.getLogger("registry.system_backup")

ARCHIVE_FORMAT: Final[Literal["wip-system-backup"]] = "wip-system-backup"
ARCHIVE_VERSION: Final[Literal[1]] = 1
BOOTSTRAP_GRANTOR = "system:bootstrap"

Origin = Literal["file", "console", "restored"]


# --- Archive shape ---


class ArchiveSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oidc_issuer: str | None = Field(
        default=None, description="The OIDC issuer user grants were made under"
    )
    salt_fingerprint: str = Field(
        ..., description="sha256 of the runtime-key salt the hashes were made under"
    )
    salt: str | None = Field(
        default=None,
        description=(
            "The runtime-key salt itself, so a restore onto a fresh stack can adopt "
            "it. Not secret against the archive's holder; the archive is sensitive "
            "anyway, since it holds every key hash"
        ),
    )

    @model_validator(mode="after")
    def _salt_matches_fingerprint(self) -> "ArchiveSource":
        if self.salt is not None and salt_fingerprint(self.salt) != self.salt_fingerprint:
            raise ValueError("source.salt does not match source.salt_fingerprint")
        return self


class ArchiveKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    key_hash: str
    owner: str = "system"
    groups: list[str] = Field(default_factory=list)
    description: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    enabled: bool = True
    namespaces: list[str] | None = None
    created_by: str = "system"
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None, description="On the source; a restored record arrives unmanaged"
    )
    origin: Origin = "console"


class ArchiveGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    subject: str
    subject_type: Literal["user", "api_key", "group"]
    permission: Literal["read", "write", "admin"]
    granted_by: str = "system"
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    managed_by: Literal["wip-deploy"] | None = None
    origin: Origin = "console"
    orphaned: bool = Field(
        default=False, description="Orphaned on the source; a restore keeps it orphaned"
    )
    orphaned_at: datetime | None = None


class ArchiveReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    app: str
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    created_by: str = "system"
    managed_by: Literal["wip-deploy"] | None = None
    origin: Origin = "console"


class SystemArchive(BaseModel):
    """The system backup: the Registry's API keys, grants and reservations."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["wip-system-backup"] = ARCHIVE_FORMAT
    version: Literal[1] = ARCHIVE_VERSION
    created_at: datetime
    source: ArchiveSource
    api_keys: list[ArchiveKey] = Field(default_factory=list)
    grants: list[ArchiveGrant] = Field(default_factory=list)
    reservations: list[ArchiveReservation] = Field(default_factory=list)


# --- Report shape ---


class SkippedRecord(BaseModel):
    id: str = Field(..., description="The record's identity: key name, 'namespace/subject_type:subject', or prefix")
    reason: str


class RecordReport(BaseModel):
    created: list[str] = Field(default_factory=list)
    replaced: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    skipped: list[SkippedRecord] = Field(default_factory=list)
    orphaned: list[str] = Field(
        default_factory=list,
        description="Grants only: those of `created` and `replaced` written orphaned, "
        "because their namespace neither exists nor is reserved here (or they were "
        "orphaned in the archive). They give nothing until activated.",
    )


class SystemRestoreReport(BaseModel):
    dry_run: bool
    salt_adopted: bool = Field(
        default=False,
        description=(
            "The archive's runtime-key salt replaces this stack's (fresh stack); "
            "in a dry run, that it would"
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    reservations: RecordReport = Field(default_factory=RecordReport)
    api_keys: RecordReport = Field(default_factory=RecordReport)
    grants: RecordReport = Field(default_factory=RecordReport)


class SaltMismatch(Exception):
    """The archive's hashes were made with another salt than the target's."""


# --- Helpers ---


def salt_fingerprint(salt: str | None = None) -> str:
    if salt is None:
        salt = current_salt()
    return "sha256:" + hashlib.sha256(salt.encode()).hexdigest()


def current_issuer() -> str | None:
    issuer = get_auth_config().jwt_issuer_url
    return issuer.rstrip("/") if issuer else None


def _norm_dt(value: datetime | None) -> datetime | None:
    """A datetime as MongoDB keeps it: UTC, millisecond precision. MongoDB
    hands back naive UTC datetimes, the archive aware ones."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _grant_id(namespace: str, subject_type: str, subject: str) -> str:
    return f"{namespace}/{subject_type}:{subject}"


def _key_content(k: StoredAPIKey | ArchiveKey) -> tuple:
    return (
        k.key_hash, k.owner, sorted(k.groups), k.description, _norm_dt(k.expires_at),
        k.enabled, sorted(k.namespaces) if k.namespaces is not None else None,
    )


def _grant_content(g: NamespaceGrant | ArchiveGrant, orphaned: bool | None = None) -> tuple:
    return (g.permission, _norm_dt(g.expires_at), g.orphaned if orphaned is None else orphaned)


def _reservation_content(r: RuntimeReservation | ArchiveReservation) -> tuple:
    return (r.app, r.settings)


# --- Backup ---


async def build_archive() -> SystemArchive:
    keys = await StoredAPIKey.find_all().to_list()
    grants = await NamespaceGrant.find_all().to_list()
    reservations = await RuntimeReservation.find_all().to_list()
    return SystemArchive(
        created_at=datetime.now(UTC),
        source=ArchiveSource(
            oidc_issuer=current_issuer(), salt_fingerprint=salt_fingerprint(),
            salt=current_salt(),
        ),
        api_keys=[
            ArchiveKey(
                name=k.name, key_hash=k.key_hash, owner=k.owner, groups=list(k.groups),
                description=k.description, created_at=k.created_at, expires_at=k.expires_at,
                enabled=k.enabled, namespaces=k.namespaces, created_by=k.created_by,
                managed_by=k.managed_by, origin=k.origin,
            )
            for k in sorted(keys, key=lambda k: k.name)
        ],
        grants=[
            ArchiveGrant(
                namespace=g.namespace, subject=g.subject, subject_type=g.subject_type,
                permission=g.permission, granted_by=g.granted_by, granted_at=g.granted_at,
                expires_at=g.expires_at, managed_by=g.managed_by, origin=g.origin,
                orphaned=g.orphaned, orphaned_at=g.orphaned_at,
            )
            for g in sorted(grants, key=lambda g: (g.namespace, g.subject_type, g.subject))
        ],
        reservations=[
            ArchiveReservation(
                prefix=r.prefix, app=r.app, settings=dict(r.settings), created_at=r.created_at,
                created_by=r.created_by, managed_by=r.managed_by, origin=r.origin,
            )
            for r in sorted(reservations, key=lambda r: r.prefix)
        ],
    )


# --- Restore ---


async def is_fresh() -> bool:
    """Whether no stored API key depends on the stack's runtime salt except
    the managed ones, which the key import re-hashes from their plaintexts."""
    return await StoredAPIKey.find_one({"managed_by": None}) is None


async def _holds_own_state() -> bool:
    """Whether the target holds auth state beyond what a fresh install has:
    the bootstrap grants and the records its own files manage."""
    if await StoredAPIKey.find_one({"managed_by": None}):
        return True
    if await RuntimeReservation.find_one({"managed_by": None}):
        return True
    return bool(await NamespaceGrant.find_one(
        {"managed_by": None, "granted_by": {"$ne": BOOTSTRAP_GRANTOR}}
    ))


async def restore_archive(
    archive: SystemArchive,
    *,
    dry_run: bool = True,
    overwrite: bool = False,
    include_user_grants: bool = False,
    config_key_names: frozenset[str] = frozenset(),
    restored_by: str = "system",
) -> tuple[SystemRestoreReport, list[tuple[str | None, StoredAPIKey]]]:
    """Restore the archive's records (or report what a restore would do).

    Returns the report and, for each key written, (the hash it replaced or
    None, the written record), so the caller can bring the Registry's own key
    provider in step.
    """
    adopt = False
    if archive.source.salt_fingerprint != salt_fingerprint():
        if archive.source.salt is None:
            raise SaltMismatch(
                "The archive's key hashes were made under a different runtime-key salt, "
                "and the archive does not carry the salt itself, so it cannot be "
                "adopted; restored keys could never authenticate here."
            )
        if not await is_fresh():
            raise SaltMismatch(
                "The archive's key hashes were made under a different runtime-key salt, "
                "and this stack already holds API keys of its own (not managed by its "
                "key file), which adopting the archive's salt would invalidate. Restore "
                "onto a fresh stack, or revoke this stack's own keys first."
            )
        adopt = True

    report = SystemRestoreReport(dry_run=dry_run, salt_adopted=adopt)
    if adopt:
        report.warnings.append(
            "The archive was made under another runtime-key salt and this stack holds "
            "no API keys of its own: "
            + ("its salt would be adopted, and the install's managed keys re-hashed."
               if dry_run else
               "its salt is adopted, and the install's managed keys are re-hashed. "
               "Services verify under it from their next key sync.")
        )
        if not dry_run:
            await adopt_salt(archive.source.salt)  # type: ignore[arg-type]
    if await _holds_own_state():
        report.warnings.append(
            "This instance already holds API keys, grants or reservations of its own. "
            "Restoring brings back the archive's state, including access revoked "
            "since the backup was taken. Review the lists below before applying."
        )
    same_issuer = archive.source.oidc_issuer == current_issuer()
    if not same_issuer:
        report.warnings.append(
            f"The archive was made under OIDC issuer {archive.source.oidc_issuer!r}, "
            f"this instance uses {current_issuer()!r}: user grants are "
            + ("restored (include_user_grants)." if include_user_grants
               else "skipped; pass include_user_grants to restore them.")
        )

    await _restore_reservations(archive, report, dry_run, overwrite, restored_by)
    written_keys = await _restore_keys(archive, report, dry_run, overwrite, config_key_names)
    await _restore_grants(
        archive, report, dry_run, overwrite, same_issuer or include_user_grants
    )
    return report, written_keys


async def _restore_reservations(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    restored_by: str,
) -> None:
    out = report.reservations
    for r in archive.reservations:
        existing = await RuntimeReservation.find_one(RuntimeReservation.prefix == r.prefix)
        if existing is not None:
            if existing.managed_by is not None:
                out.skipped.append(SkippedRecord(id=r.prefix, reason="managed by wip-deploy on this instance"))
                continue
            if _reservation_content(existing) == _reservation_content(r):
                out.unchanged.append(r.prefix)
                continue
            if not overwrite:
                out.skipped.append(SkippedRecord(id=r.prefix, reason="differs on this instance; pass overwrite to replace it"))
                continue
            out.replaced.append(r.prefix)
        else:
            out.created.append(r.prefix)
        if dry_run:
            continue
        fields = {
            "app": r.app, "settings": dict(r.settings),
            "created_at": r.created_at or datetime.now(UTC),
            "created_by": r.created_by or restored_by,
            "managed_by": None, "origin": "restored",
        }
        if existing is not None:
            await existing.set(fields)
        else:
            await RuntimeReservation(prefix=r.prefix, **fields).insert()


async def _restore_keys(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    config_key_names: frozenset[str],
) -> list[tuple[str | None, StoredAPIKey]]:
    out = report.api_keys
    written: list[tuple[str | None, StoredAPIKey]] = []
    for k in archive.api_keys:
        existing = await StoredAPIKey.find_one(StoredAPIKey.name == k.name)
        if existing is not None and existing.managed_by is not None:
            out.skipped.append(SkippedRecord(id=k.name, reason="managed by wip-deploy on this instance"))
            continue
        if k.name in config_key_names:
            out.skipped.append(SkippedRecord(id=k.name, reason="a config key of this name is loaded on this instance"))
            continue
        if existing is not None:
            if _key_content(existing) == _key_content(k):
                out.unchanged.append(k.name)
                continue
            if not overwrite:
                out.skipped.append(SkippedRecord(id=k.name, reason="differs on this instance; pass overwrite to replace it"))
                continue
            out.replaced.append(k.name)
        else:
            out.created.append(k.name)
        if dry_run:
            continue
        fields = {
            "key_hash": k.key_hash, "owner": k.owner, "groups": list(k.groups),
            "description": k.description, "created_at": k.created_at or datetime.now(UTC),
            "expires_at": k.expires_at, "enabled": k.enabled, "namespaces": k.namespaces,
            "created_by": k.created_by, "managed_by": None, "origin": "restored",
            "grants_managed": False,
        }
        if existing is not None:
            old_hash = existing.key_hash
            await existing.set(fields)
            written.append((old_hash, existing))
        else:
            doc = StoredAPIKey(name=k.name, **fields)
            await doc.insert()
            written.append((None, doc))
    return written


async def _grantable(namespace: str, report: SystemRestoreReport, dry_run: bool) -> bool:
    """Whether a grant is written effective: on a namespace that exists, or on
    a reserved prefix (a key that reserves a prefix holds admin on it before
    the namespace is created, and needs that grant to create it). Any other
    grant is written orphaned. A dry run counts the reservations it would
    restore."""
    if await Namespace.find_one({"prefix": namespace, "status": "active"}):
        return True
    if dry_run and namespace in (*report.reservations.created, *report.reservations.replaced):
        return True
    return bool(await RuntimeReservation.find_one(RuntimeReservation.prefix == namespace))


async def _grant_owned_by_file(g: ArchiveGrant, existing: NamespaceGrant | None) -> bool:
    if existing is not None and existing.managed_by is not None:
        return True
    if g.subject_type != "api_key":
        return False
    key = await StoredAPIKey.find_one(StoredAPIKey.name == g.subject)
    return key is not None and key.managed_by is not None and key.grants_managed


async def _restore_grants(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    user_grants: bool,
) -> None:
    out = report.grants
    active: dict[str, bool] = {}
    for g in archive.grants:
        gid = _grant_id(g.namespace, g.subject_type, g.subject)
        if g.subject_type == "user" and not user_grants:
            out.skipped.append(SkippedRecord(id=gid, reason="user grant from another OIDC issuer"))
            continue
        # Ownership first: the import writes a key's declared grants whether
        # or not the namespace exists, and "restore the namespace" would send
        # an operator after one that never comes.
        existing = await NamespaceGrant.find_one({
            "namespace": g.namespace, "subject": g.subject, "subject_type": g.subject_type,
        })
        if await _grant_owned_by_file(g, existing):
            out.skipped.append(SkippedRecord(id=gid, reason="managed by wip-deploy on this instance"))
            continue
        if g.namespace not in active:
            active[g.namespace] = await _grantable(g.namespace, report, dry_run)
        orphaned = g.orphaned or not active[g.namespace]
        if existing is not None:
            if _grant_content(existing) == _grant_content(g, orphaned):
                out.unchanged.append(gid)
                continue
            if not overwrite:
                reason = (
                    "orphaned on this instance; activate it on the namespace, or pass "
                    "overwrite to replace it"
                    if existing.orphaned and not orphaned
                    else "differs on this instance; pass overwrite to replace it"
                )
                out.skipped.append(SkippedRecord(id=gid, reason=reason))
                continue
            out.replaced.append(gid)
        else:
            out.created.append(gid)
        if orphaned:
            out.orphaned.append(gid)
        if dry_run:
            continue
        fields = {
            "permission": g.permission, "granted_by": g.granted_by,
            "granted_at": g.granted_at or datetime.now(UTC), "expires_at": g.expires_at,
            "managed_by": None, "origin": "restored",
            "orphaned": orphaned,
            "orphaned_at": (g.orphaned_at or datetime.now(UTC)) if orphaned else None,
        }
        if existing is not None:
            await existing.set(fields)
        else:
            await NamespaceGrant(
                namespace=g.namespace, subject=g.subject, subject_type=g.subject_type, **fields,
            ).insert()
