"""System backup: the Registry's auth state, apart from any namespace's data.

A namespace (data) backup carries documents, templates and terminologies; it
never carries key hashes, grants, reservations or attachments, so merge and
fresh restores can re-map namespaces freely. This module backs up and
restores those four collections on their own:

- every stored API key record (the bcrypt hash and its metadata, never a
  plaintext), managed records included, so one archive reproduces the
  instance's auth state after a wipe or on other hardware;
- every namespace grant;
- every namespace reservation;
- every app attachment (an app put under the umbrella through the API), so a
  restore puts attached apps back under the umbrella with no manual step.

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
  without the salt itself (only its fingerprint, as archives made before the
  salt travelled) is adopted only when the fingerprint is the configured
  salt's, which is the one salt it can name; otherwise it is refused.
- **Upsert by identity.** A key is identified by its name, a grant by
  (namespace, subject, subject_type), a reservation by its prefix, an
  attachment by its name. A missing record is created; an identical one is
  left alone; a different one is replaced only with `overwrite`, otherwise
  listed and skipped. Records the archive does not hold are kept, except in
  mirror mode (below).
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
  that exists (active or archived), or on a prefix reserved here by the same
  owner as in the archive, is written effective: a key that reserves a
  prefix holds admin on it before the namespace exists (reservations are
  restored first for that reason). Any other grant is written `orphaned` and
  listed, including one on a prefix another owner reserves here: an orphan
  is no grant until it is activated, which happens when a platform admin
  creates the namespace (a data restore an admin runs does), or when a
  namespace admin activates it. A key that reserves the prefix later and
  creates the namespace does not inherit it. So the system and data
  restores work in either order. A grant the archive holds orphaned is
  written orphaned, and never replaces a grant that is effective here.
- **Attachments come last,** after keys and grants, so an attached app's key
  exists when its route returns. Each is matched by name and restored with
  its archived status; the attach-time rules hold: a name one of this
  install's own apps routes under, an upstream that is not a valid URL, or
  one that resolves from here to a blocked target is skipped and listed,
  never written. The blocked-target check is advisory, as at attach time
  (the router's app edge checks again where it resolves). An upstream that
  does not resolve from here is written and listed as `unresolved`: in
  disaster recovery the app often comes back after the stack. When the
  attach policy file is unusable, every attachment is skipped. Each entry is
  checked on its own: one that fails the attach rules (a record stored
  before a rule existed, or an edited archive) is skipped and listed, and
  never stops the backup or the rest of the restore. An entry the source's
  deployer manages is skipped: the install that deploys the app re-creates
  it, and on another stack it would point at a host that is not there.
- **Three modes.** `upsert` (the default) creates what is missing and leaves
  a record that differs; `overwrite` also replaces it; `mirror` also removes
  every record the archive does not hold, so the archive becomes the exact
  auth state. The mirror is stack-wide (every archive is). It adds no new kind
  of delete; it sequences the existing ones: a key goes as a revoke takes it
  (with its grants), a grant as a grant revoke, a reservation as a release,
  and an attachment is deactivated, never deleted. It never removes:
  - a record the install's files manage, nor a grant to a key whose grants
    the key file declares;
  - a key named like a loaded config key;
  - the bootstrap grants;
  - user grants when the archive's user grants are not restored (another
    OIDC issuer without `include_user_grants`): this stack's users would be
    left with nothing;
  - the key the request authenticates with: a plan that would remove it is
    refused (the dry run lists it under `refused`; an apply answers 409
    `restore_refused`). So is any replacing restore (overwrite or mirror)
    that would replace that key by the archive's different record of the
    same name.
  A grant the archive holds for a key the mirror removes is not written.
  The dry run returns a `plan_digest` of what the mirror would remove; an
  apply that passes it as `expect` is refused (409) when the plan has
  changed since. Writes are not one transaction: a run that fails part-way
  has done the records its report lists, and re-running converges. After a
  mirror the stack holds no keys of its own beyond the archive's, so a
  mirror adopts an archive's other salt where an upsert would refuse it.
- **Dry run by default.** The report lists what would be created, replaced,
  left unchanged and skipped (with the reason), and leads with a warning when
  the target holds auth state of its own: restoring old state brings back old
  access, including access revoked since the backup.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pymongo.errors import DuplicateKeyError

from wip_auth import get_auth_config

from ..models.api_key import StoredAPIKey
from ..models.attachment import AppAttachment, AttachmentFields, AttachmentRequest
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


class ArchiveScope(BaseModel):
    """What a scoped backup covers, declared rather than inferred: a mirror of
    a scoped archive removes only within it. A grant or reservation is in
    scope by its namespace or prefix; a key only when it holds a non-empty
    list of namespaces, every one in scope; an attachment only when named."""

    model_config = ConfigDict(extra="forbid")

    namespaces: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(
        default_factory=list,
        description="Attachments in scope, by name. Attachments have no namespace, "
        "so a scoped backup carries only those named here",
    )
    keys_left_out: list[str] = Field(
        default_factory=list,
        description="Keys with a grant or reservation in scope that the archive does "
        "not carry, because they hold a namespace outside it or none: a scoped "
        "mirror never removes them",
    )

    def holds_key(self, namespaces: list[str] | None) -> bool:
        return bool(namespaces) and set(namespaces or []) <= set(self.namespaces)


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
    scope: ArchiveScope | None = Field(
        default=None,
        description="Set by a scoped backup: what the archive covers. Absent: the "
        "whole stack. A Registry that predates scopes refuses such an archive (422) "
        "rather than read it as a whole-stack one",
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


class ArchiveAttachment(BaseModel):
    """An attachment as archived: its name, status and provenance, and the
    attach fields (upstream, auth_required, allowed_groups, ... as
    POST /attachments takes them) as stored.

    The attach fields are not validated here but per record on restore,
    where one that fails the attach rules is skipped and listed. A stored
    record written before a rule existed must neither stop the backup nor
    take the archive's keys and grants down with it on restore."""

    model_config = ConfigDict(extra="allow")

    name: str
    status: str = "active"
    managed_by: str | None = Field(
        default=None,
        description="On the source. An entry the source's deployer manages is not "
        "restored: the install that deploys the app re-creates it",
    )
    created_at: datetime | None = None
    created_by: str = "system"


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
    attachments: list[ArchiveAttachment] = Field(default_factory=list)


# --- Report shape ---


SkipReason = Literal[
    "managed_by_deployer",      # wip-deploy manages the record here
    "managed_on_source",        # the source's deployer managed it; this install's re-creates it
    "invalid_record",           # the archived record fails today's rules for its kind
    "differs",                  # differs here; overwrite replaces it
    "orphaned_here",            # an orphan here, effective in the archive; activate or overwrite
    "orphaned_in_archive",      # an orphan in the archive, effective here; never replaced
    "other_oidc_issuer",        # a user grant from another issuer; include_user_grants restores it
    "config_key_name",          # a config key of this name is loaded here
    "app_route",                # an app this install deploys routes under /apps/<name>
    "attach_policy_unavailable",
    "upstream_invalid",
    "upstream_blocked",
    "concurrent_write",         # another request attached the name during the restore
    "mirror_removes_key",       # a grant to a key the archive lacks and the mirror removes
    "caller_key",               # refused: the mirror would remove the key of this request
]


class SkippedRecord(BaseModel):
    """A record the restore left out. Branch on `reason_code`; `reason` is
    for people. The identity fields set are the record's own: `name` (key,
    attachment), `prefix` (reservation), or `namespace`, `subject_type` and
    `subject` (grant)."""

    id: str = Field(..., description="The record's identity: key name, 'namespace/subject_type:subject', or prefix")
    reason: str
    reason_code: SkipReason
    name: str | None = None
    prefix: str | None = None
    namespace: str | None = None
    subject_type: Literal["user", "api_key", "group"] | None = None
    subject: str | None = None


def _skip_named(name: str, code: SkipReason, reason: str) -> SkippedRecord:
    return SkippedRecord(id=name, name=name, reason_code=code, reason=reason)


def _skip_reservation(prefix: str, code: SkipReason, reason: str) -> SkippedRecord:
    return SkippedRecord(id=prefix, prefix=prefix, reason_code=code, reason=reason)


def _skip_grant(g: "ArchiveGrant", code: SkipReason, reason: str) -> SkippedRecord:
    return SkippedRecord(
        id=_grant_id(g.namespace, g.subject_type, g.subject), namespace=g.namespace,
        subject_type=g.subject_type, subject=g.subject, reason_code=code, reason=reason,
    )


_MANAGED = "managed by wip-deploy on this instance"
_DIFFERS = "differs on this instance; pass overwrite to replace it"


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
    unresolved: list[str] = Field(
        default_factory=list,
        description="Attachments only: those of `created` and `replaced` whose upstream "
        "does not resolve from the Registry's network yet. Written anyway; the "
        "router's app edge checks it where it resolves.",
    )
    deleted: list[str] = Field(
        default_factory=list,
        description="Mirror only: records this instance held and the archive does not, "
        "removed (in a dry run: that would be). Keys, grants and reservations.",
    )
    deactivated: list[str] = Field(
        default_factory=list,
        description="Mirror only, attachments: active ones the archive does not hold, "
        "deactivated (attachments are never deleted)",
    )


RestoreMode = Literal["upsert", "overwrite", "mirror"]


class SystemRestoreReport(BaseModel):
    dry_run: bool
    mode: RestoreMode = "upsert"
    salt_adopted: bool = Field(
        default=False,
        description=(
            "The archive's runtime-key salt replaces this stack's (fresh stack); "
            "in a dry run, that it would"
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    refused: list[SkippedRecord] = Field(
        default_factory=list,
        description="Mirror only: records the plan would remove that it must not (the "
        "key this request authenticates with). A plan with refusals is not applied.",
    )
    plan_digest: str | None = Field(
        default=None,
        description="Mirror only: a digest of what the mirror removes. Pass it as "
        "`expect` on the apply to refuse a plan that changed since this dry run.",
    )
    reservations: RecordReport = Field(default_factory=RecordReport)
    api_keys: RecordReport = Field(default_factory=RecordReport)
    grants: RecordReport = Field(default_factory=RecordReport)
    attachments: RecordReport = Field(default_factory=RecordReport)


SALT_MISMATCH = "salt_mismatch"
RESTORE_REFUSED = "restore_refused"
PLAN_CHANGED = "plan_changed"


class SaltMismatch(Exception):
    """The archive's hashes were made with another salt than the target's, and
    this stack cannot adopt it. The API answers 409 with error_code
    `salt_mismatch`."""


class RestoreRefused(Exception):
    """The restore would remove or replace what it must not (the key of the
    request); nothing was written. The API answers 409 with error_code
    `restore_refused`."""


class PlanChanged(Exception):
    """The mirror plan differs from the one the caller expected. The API
    answers 409 with error_code `plan_changed`."""


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


async def build_archive(
    namespaces: list[str] | None = None, attachments_in_scope: list[str] | None = None,
) -> SystemArchive:
    """The whole stack, or with `namespaces` / `attachments_in_scope` a scoped
    archive that declares what it covers (ArchiveScope)."""
    keys = await StoredAPIKey.find_all().to_list()
    grants = await NamespaceGrant.find_all().to_list()
    reservations = await RuntimeReservation.find_all().to_list()
    attachments = await AppAttachment.find_all().to_list()
    scope: ArchiveScope | None = None
    if namespaces is not None or attachments_in_scope is not None:
        scope = ArchiveScope(
            namespaces=sorted(set(namespaces or [])),
            attachments=sorted(set(attachments_in_scope or [])),
        )
        in_ns = set(scope.namespaces)
        grants = [g for g in grants if g.namespace in in_ns]
        reservations = [r for r in reservations if r.prefix in in_ns]
        attachments = [a for a in attachments if a.name in set(scope.attachments)]
        carried = [k for k in keys if scope.holds_key(k.namespaces)]
        carried_names = {k.name for k in carried}
        # Keys this scope touches (a grant to them, or a reservation they own)
        # but cannot carry: named so the operator sees what stays behind.
        touched = {g.subject.removeprefix("apikey:") for g in grants if g.subject_type == "api_key"}
        touched |= {r.app for r in reservations if r.managed_by is None}
        stored = {k.name for k in keys}
        scope.keys_left_out = sorted((touched & stored) - carried_names)
        keys = carried
    return SystemArchive(
        created_at=datetime.now(UTC),
        source=ArchiveSource(
            oidc_issuer=current_issuer(), salt_fingerprint=salt_fingerprint(),
            salt=current_salt(), scope=scope,
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
        attachments=[
            ArchiveAttachment(
                name=a.name, **_attachment_fields(a), status=a.status,
                managed_by=a.managed_by, created_at=a.created_at, created_by=a.created_by,
            )
            for a in sorted(attachments, key=lambda a: a.name)
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
    mode: RestoreMode = "upsert",
    include_user_grants: bool = False,
    config_key_names: frozenset[str] = frozenset(),
    restored_by: str = "system",
    caller_key: str | None = None,
    expect: str | None = None,
) -> tuple[SystemRestoreReport, list[tuple[str | None, StoredAPIKey]], list[str]]:
    """Restore the archive's records (or report what a restore would do).

    Returns the report; for each key written, (the hash it replaced or None,
    the written record); and the hashes of the keys a mirror removed, so the
    caller can bring the Registry's own key provider in step.
    """
    overwrite = mode != "upsert"
    same_issuer = archive.source.oidc_issuer == current_issuer()
    user_grants = same_issuer or include_user_grants
    plan = (
        await _plan_mirror(archive, config_key_names, user_grants, caller_key)
        if mode == "mirror" else None
    )
    refused = list(plan.refused) if plan is not None else []
    if overwrite:
        refused += await _caller_replacement(archive, caller_key, config_key_names)
    if refused and not dry_run:
        raise RestoreRefused(
            "The restore would " + "; ".join(f"{r.reason} ({r.id})" for r in refused)
            + ". Nothing was written."
        )
    if plan is not None and not dry_run and expect is not None and expect != plan.digest:
        raise PlanChanged(
            "The mirror plan changed since the dry run that returned the expected "
            "digest. Nothing was written; run the dry run again and review it."
        )

    adopt: str | None = None
    salt_note: str | None = None
    if archive.source.salt_fingerprint != salt_fingerprint():
        archive_salt = archive.source.salt
        if archive_salt is None:
            # An archive made before archives carried the salt names it only by
            # fingerprint. Under the configured salt (every stack's default
            # before the Registry kept its own) the salt is known all the same.
            configured = get_auth_config().api_key_hash_salt
            if archive.source.salt_fingerprint == salt_fingerprint(configured):
                archive_salt = configured
        if archive_salt is None:
            raise SaltMismatch(
                "The archive's key hashes were made under a different runtime-key salt, "
                "and the archive does not carry the salt itself, so it cannot be "
                "adopted; restored keys could never authenticate here."
            )
        fresh = await is_fresh() if plan is None else await _fresh_after(plan, archive, config_key_names)
        if refused:
            # A dry run only (an apply was refused above): report the refusal,
            # which the operator must resolve first, rather than a salt error
            # it may be the cause of.
            fresh = False
            salt_note = (
                "The archive was made under another runtime-key salt. Whether this "
                "stack can adopt it is decided once the refusals below are resolved."
            )
        elif not fresh:
            raise SaltMismatch(
                "The archive's key hashes were made under a different runtime-key salt, "
                "and this stack already holds API keys of its own (not managed by its "
                "key file), which adopting the archive's salt would invalidate. Restore "
                "onto a fresh stack, or revoke this stack's own keys first."
            )
        else:
            adopt = archive_salt

    report = SystemRestoreReport(dry_run=dry_run, mode=mode, salt_adopted=adopt is not None)
    report.refused = refused
    if salt_note:
        report.warnings.append(salt_note)
    if plan is not None:
        report.plan_digest = plan.digest
        report.api_keys.deleted = sorted(k.name for k in plan.keys)
        report.grants.deleted = sorted(plan.grant_ids)
        report.reservations.deleted = sorted(r.prefix for r in plan.reservations)
        report.attachments.deactivated = sorted(a.name for a in plan.attachments)
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
            await adopt_salt(adopt)
    if await _holds_own_state():
        report.warnings.append(
            "This instance already holds API keys, grants or reservations of its own. "
            "Restoring brings back the archive's state, including access revoked "
            "since the backup was taken. Review the lists below before applying."
        )
    if archive.source.scope is not None:
        scope = archive.source.scope
        report.warnings.append(
            f"A scoped archive: namespaces {scope.namespaces or '(none)'}, attachments "
            f"{scope.attachments or '(none)'}."
            + (" A mirror removes only within this scope." if mode == "mirror" else "")
        )
        carried = {k.name for k in archive.api_keys}
        uncarried = sorted({
            _grant_id(g.namespace, g.subject_type, g.subject) for g in archive.grants
            if g.subject_type == "api_key" and g.subject.removeprefix("apikey:") not in carried
        })
        if uncarried:
            report.warnings.append(
                "Grants to keys the archive does not carry (they hold a namespace outside "
                "the scope, or none): " + ", ".join(uncarried) + ". Each is written as "
                "any grant: effective where the key exists here, otherwise it gives nothing."
            )
    if not same_issuer:
        report.warnings.append(
            f"The archive was made under OIDC issuer {archive.source.oidc_issuer!r}, "
            f"this instance uses {current_issuer()!r}: user grants are "
            + ("restored (include_user_grants)." if include_user_grants
               else "skipped; pass include_user_grants to restore them.")
        )

    await _restore_reservations(archive, report, dry_run, overwrite, restored_by)
    written_keys = await _restore_keys(archive, report, dry_run, overwrite, config_key_names)
    removed_keys = {k.name for k in plan.keys} if plan is not None else set()
    await _restore_grants(archive, report, dry_run, overwrite, user_grants, removed_keys)
    await _restore_attachments(archive, report, dry_run, overwrite, restored_by)
    removed_hashes: list[str] = []
    if plan is not None and not dry_run:
        removed_hashes = await _apply_mirror(plan, restored_by)
    return report, written_keys, removed_hashes


# --- Mirror ---


@dataclass
class MirrorPlan:
    scope: ArchiveScope | None = None
    keys: list[StoredAPIKey] = field(default_factory=list)
    grants: list[NamespaceGrant] = field(default_factory=list)
    reservations: list[RuntimeReservation] = field(default_factory=list)
    attachments: list[AppAttachment] = field(default_factory=list)
    refused: list[SkippedRecord] = field(default_factory=list)

    @property
    def grant_ids(self) -> list[str]:
        return [_grant_id(g.namespace, g.subject_type, g.subject) for g in self.grants]

    @property
    def digest(self) -> str:
        body = json.dumps({
            # The same removals under another declared scope are another plan.
            "scope": self.scope.model_dump(exclude={"keys_left_out"}) if self.scope else None,
            "api_keys": sorted(k.name for k in self.keys),
            "grants": sorted(self.grant_ids),
            "reservations": sorted(r.prefix for r in self.reservations),
            "attachments": sorted(a.name for a in self.attachments),
        }, sort_keys=True)
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _key_subjects(name: str) -> set[str]:
    """Both spellings a grant to key `name` may carry."""
    return {name, f"apikey:{name}"}


async def _plan_mirror(
    archive: SystemArchive, config_key_names: frozenset[str], user_grants: bool,
    caller_key: str | None,
) -> MirrorPlan:
    """What a mirror removes: every record here the archive does not hold,
    less the exclusions in the module docstring, and, for a scoped archive,
    only records inside its declared scope. Reads only."""
    scope = archive.source.scope
    plan = MirrorPlan(scope=scope)
    in_ns = set(scope.namespaces) if scope else None
    archived_keys = {k.name for k in archive.api_keys}
    for doc in await StoredAPIKey.find({"managed_by": None}).to_list():
        if doc.name in archived_keys or doc.name in config_key_names:
            continue
        if scope is not None and not scope.holds_key(doc.namespaces):
            continue
        if caller_key is not None and doc.name == caller_key:
            plan.refused.append(_skip_named(
                doc.name, "caller_key", "remove the key this request authenticates with",
            ))
            continue
        plan.keys.append(doc)
    removed_subjects = set().union(*(_key_subjects(k.name) for k in plan.keys))

    archived_grants = {_grant_id(g.namespace, g.subject_type, g.subject) for g in archive.grants}
    for g in await NamespaceGrant.find({"managed_by": None}).to_list():
        if g.granted_by == BOOTSTRAP_GRANTOR:
            continue
        if g.subject_type == "user" and not user_grants:
            continue
        removed_with_key = g.subject_type == "api_key" and g.subject in removed_subjects
        if not removed_with_key and in_ns is not None and g.namespace not in in_ns:
            continue
        if not removed_with_key:
            if _grant_id(g.namespace, g.subject_type, g.subject) in archived_grants:
                continue
            if await _grant_owned_by_file(_as_archive_grant(g), g):
                continue
        plan.grants.append(g)

    archived_prefixes = {r.prefix for r in archive.reservations}
    plan.reservations = [
        r for r in await RuntimeReservation.find({"managed_by": None}).to_list()
        if r.prefix not in archived_prefixes and (in_ns is None or r.prefix in in_ns)
    ]

    archived_attachments = {a.name for a in archive.attachments}
    in_attachments = set(scope.attachments) if scope else None
    plan.attachments = [
        a for a in await AppAttachment.find({"managed_by": None, "status": "active"}).to_list()
        if a.name not in archived_attachments
        and (in_attachments is None or a.name in in_attachments)
    ]
    return plan


async def _caller_replacement(
    archive: SystemArchive, caller_key: str | None, config_key_names: frozenset[str],
) -> list[SkippedRecord]:
    """A replacing restore (overwrite, mirror) must not swap the key of the
    request for the archive's different record of that name: a re-created
    admin key restored from an older archive would lock the operator out."""
    if caller_key is None or caller_key in config_key_names:
        return []
    archived = next((k for k in archive.api_keys if k.name == caller_key), None)
    if archived is None:
        return []
    current = await StoredAPIKey.find_one(StoredAPIKey.name == caller_key)
    if current is None or current.managed_by is not None:
        return []
    if _key_content(current) == _key_content(archived):
        return []
    return [_skip_named(
        caller_key, "caller_key",
        "replace the key this request authenticates with by the archive's different record",
    )]


def _as_archive_grant(g: NamespaceGrant) -> ArchiveGrant:
    return ArchiveGrant(
        namespace=g.namespace, subject=g.subject, subject_type=g.subject_type,
        permission=g.permission,
    )


async def _fresh_after(
    plan: MirrorPlan, archive: SystemArchive, config_key_names: frozenset[str],
) -> bool:
    """Whether, after this mirror, no stored key here depends on this stack's
    salt except the managed ones: each of its own keys is either removed or
    replaced by the archive's record."""
    removed = {k.name for k in plan.keys}
    replaced = {k.name for k in archive.api_keys} - config_key_names
    return all(
        doc.name in removed or doc.name in replaced
        for doc in await StoredAPIKey.find({"managed_by": None}).to_list()
    )


async def _apply_mirror(plan: MirrorPlan, removed_by: str) -> list[str]:
    """Remove what the plan lists, record by record. Returns the removed keys'
    hashes for the caller's provider."""
    for g in plan.grants:
        await NamespaceGrant.find({
            "namespace": g.namespace, "subject": g.subject, "subject_type": g.subject_type,
            "managed_by": None,
        }).delete()
    hashes: list[str] = []
    for k in plan.keys:
        # As a revoke takes it: the key, then every grant to it (both spellings).
        await StoredAPIKey.find({"name": k.name, "managed_by": None}).delete()
        await NamespaceGrant.find({
            "subject_type": "api_key", "subject": {"$in": sorted(_key_subjects(k.name))},
        }).delete()
        hashes.append(k.key_hash)
    for r in plan.reservations:
        await RuntimeReservation.find({"prefix": r.prefix, "managed_by": None}).delete()
    now = datetime.now(UTC)
    for a in plan.attachments:
        doc = await AppAttachment.find_one({"name": a.name, "managed_by": None})
        if doc is not None and doc.status == "active":
            doc.status = "inactive"
            doc.updated_at = now
            doc.updated_by = removed_by
            await doc.save()
    logger.warning(
        "System restore mirror by %s removed keys %s, grants %s, reservations %s; "
        "deactivated attachments %s",
        removed_by, sorted(k.name for k in plan.keys), sorted(plan.grant_ids),
        sorted(r.prefix for r in plan.reservations), sorted(a.name for a in plan.attachments),
    )
    return hashes


def _attachment_fields(a: AppAttachment | AttachmentRequest) -> dict[str, Any]:
    return a.model_dump(include=set(AttachmentFields.model_fields))


_ARCHIVE_ONLY = {"status", "managed_by", "created_at", "created_by"}


def _validated_attachment(a: ArchiveAttachment) -> tuple[AttachmentRequest | None, str | None]:
    """The archived entry as an attach request, or why it is not one."""
    if a.status not in ("active", "inactive"):
        return None, f"status {a.status!r} is neither 'active' nor 'inactive'"
    try:
        return AttachmentRequest.model_validate(a.model_dump(exclude=_ARCHIVE_ONLY)), None
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'record'}: {err['msg']}"
            for err in e.errors()
        )
        return None, f"fails the attach rules: {problems}"


async def _restore_attachments(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    restored_by: str,
) -> None:
    """Attachments, by name, under the attach-time rules (module docstring)."""
    from .attachments import (
        AttachPolicyUnavailable,
        UpstreamInvalid,
        check_upstream,
        load_policy,
        resolve_install_addresses,
    )

    out = report.attachments
    if not archive.attachments:
        return
    try:
        policy = load_policy()
    except AttachPolicyUnavailable as e:
        for a in archive.attachments:
            out.skipped.append(_skip_named(a.name, "attach_policy_unavailable", f"attach policy unavailable: {e}"))
        return
    reserved = set(policy.reserved_names)
    install_addresses = await resolve_install_addresses(policy)

    for a in archive.attachments:
        if a.managed_by is not None:
            out.skipped.append(_skip_named(
                a.name, "managed_on_source",
                "managed by wip-deploy on the source; the install that deploys the app re-creates it",
            ))
            continue
        request, why = _validated_attachment(a)
        if request is None:
            out.skipped.append(_skip_named(a.name, "invalid_record", why or "invalid"))
            continue
        if a.name in reserved:
            out.skipped.append(_skip_named(
                a.name, "app_route", f"/apps/{a.name} is a route of an app this install deploys",
            ))
            continue
        existing = await AppAttachment.find_one(AppAttachment.name == a.name)
        if existing is not None and existing.managed_by is not None:
            out.skipped.append(_skip_named(a.name, "managed_by_deployer", _MANAGED))
            continue
        fields = _attachment_fields(request)
        status: Literal["active", "inactive"] = "active" if a.status == "active" else "inactive"
        if existing is not None:
            if (_attachment_fields(existing), existing.status) == (fields, status):
                out.unchanged.append(a.name)
                continue
            if not overwrite:
                out.skipped.append(_skip_named(a.name, "differs", _DIFFERS))
                continue
        try:
            check = await check_upstream(request.upstream, policy, install_addresses)
        except UpstreamInvalid as e:
            out.skipped.append(_skip_named(a.name, "upstream_invalid", str(e)))
            continue
        if check.blocked:
            out.skipped.append(_skip_named(a.name, "upstream_blocked", f"upstream refused: {check.blocked}"))
            continue
        (out.replaced if existing is not None else out.created).append(a.name)
        if check.warnings:
            out.unresolved.append(a.name)
        if dry_run:
            continue
        now = datetime.now(UTC)
        if existing is not None:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.status = status
            existing.updated_at = now
            existing.updated_by = restored_by
            await existing.save()
        else:
            try:
                await AppAttachment(
                    name=a.name, **fields, status=status, managed_by=None,
                    created_at=a.created_at or now, created_by=a.created_by,
                    updated_at=now, updated_by=restored_by,
                ).insert()
            except DuplicateKeyError:
                out.created.remove(a.name)
                if a.name in out.unresolved:
                    out.unresolved.remove(a.name)
                out.skipped.append(_skip_named(
                    a.name, "concurrent_write", "attached by another request during the restore",
                ))
                continue
        logger.info(
            "Attachment %s restored (%s) by %s: /apps/%s -> %s",
            a.name, status, restored_by, a.name, request.upstream,
        )


async def _restore_reservations(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    restored_by: str,
) -> None:
    out = report.reservations
    for r in archive.reservations:
        existing = await RuntimeReservation.find_one(RuntimeReservation.prefix == r.prefix)
        if existing is not None:
            if existing.managed_by is not None:
                out.skipped.append(_skip_reservation(r.prefix, "managed_by_deployer", _MANAGED))
                continue
            if _reservation_content(existing) == _reservation_content(r):
                out.unchanged.append(r.prefix)
                continue
            if not overwrite:
                out.skipped.append(_skip_reservation(r.prefix, "differs", _DIFFERS))
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
            out.skipped.append(_skip_named(k.name, "managed_by_deployer", _MANAGED))
            continue
        if k.name in config_key_names:
            out.skipped.append(_skip_named(
                k.name, "config_key_name", "a config key of this name is loaded on this instance",
            ))
            continue
        if existing is not None:
            if _key_content(existing) == _key_content(k):
                out.unchanged.append(k.name)
                continue
            if not overwrite:
                out.skipped.append(_skip_named(k.name, "differs", _DIFFERS))
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


async def _grantable(
    namespace: str, report: SystemRestoreReport, dry_run: bool, archive_owners: dict[str, str],
) -> bool:
    """Whether a grant is written effective: on a namespace that exists here
    (active or archived), or on a prefix reserved here by the owner the
    archive's reservation names. A reservation by another owner is another
    namespace of that name: its owner must not inherit the archive's grants,
    so they are written orphaned, as on an absent prefix. A dry run counts
    the reservations it would restore."""
    if await Namespace.find_one({"prefix": namespace, "status": {"$in": ["active", "archived"]}}):
        return True
    if dry_run and namespace in (*report.reservations.created, *report.reservations.replaced):
        return True
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == namespace)
    return doc is not None and archive_owners.get(namespace) == doc.app


async def _grant_owned_by_file(g: ArchiveGrant, existing: NamespaceGrant | None) -> bool:
    if existing is not None and existing.managed_by is not None:
        return True
    if g.subject_type != "api_key":
        return False
    key = await StoredAPIKey.find_one(StoredAPIKey.name == g.subject)
    return key is not None and key.managed_by is not None and key.grants_managed


async def _restore_grants(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    user_grants: bool, removed_keys: set[str] | None = None,
) -> None:
    out = report.grants
    active: dict[str, bool] = {}
    archive_owners = {r.prefix: r.app for r in archive.reservations}
    for g in archive.grants:
        gid = _grant_id(g.namespace, g.subject_type, g.subject)
        if g.subject_type == "user" and not user_grants:
            out.skipped.append(_skip_grant(g, "other_oidc_issuer", "user grant from another OIDC issuer"))
            continue
        if (
            removed_keys and g.subject_type == "api_key"
            and g.subject.removeprefix("apikey:") in removed_keys
        ):
            out.skipped.append(_skip_grant(
                g, "mirror_removes_key", "its key is not in the archive, and the mirror removes it",
            ))
            continue
        # Ownership first: the import writes a key's declared grants whether
        # or not the namespace exists, and "restore the namespace" would send
        # an operator after one that never comes.
        existing = await NamespaceGrant.find_one({
            "namespace": g.namespace, "subject": g.subject, "subject_type": g.subject_type,
        })
        if await _grant_owned_by_file(g, existing):
            out.skipped.append(_skip_grant(g, "managed_by_deployer", _MANAGED))
            continue
        if g.namespace not in active:
            active[g.namespace] = await _grantable(g.namespace, report, dry_run, archive_owners)
        orphaned = g.orphaned or not active[g.namespace]
        if existing is not None and not existing.orphaned and g.orphaned:
            # An archived orphan was never access on its source. Replacing an
            # effective grant here with it would take access away, or (with a
            # higher permission) hand out what nobody granted.
            out.skipped.append(_skip_grant(
                g, "orphaned_in_archive", "orphaned in the archive, effective here; left as it is",
            ))
            continue
        if existing is not None:
            if _grant_content(existing) == _grant_content(g, orphaned):
                out.unchanged.append(gid)
                continue
            if not overwrite:
                if existing.orphaned and not orphaned:
                    out.skipped.append(_skip_grant(
                        g, "orphaned_here",
                        "orphaned on this instance; activate it on the namespace, or pass "
                        "overwrite to replace it",
                    ))
                else:
                    out.skipped.append(_skip_grant(g, "differs", _DIFFERS))
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
