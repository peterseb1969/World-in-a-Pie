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
- **A grant without its namespace is kept, orphaned.** A grant is written
  effective when a namespace (active or archived) or a reservation here, as
  this restore leaves it, holds the namespace id the grant was archived
  with: a key that reserves a prefix holds admin on it before the namespace
  exists (reservations are restored first for that reason). A namespace or
  reservation of the same name with another id is another namespace: the
  grant is written `orphaned`, with its id, and listed. An orphan is no
  grant until it becomes effective: when a namespace with its id is created
  (a data restore of that namespace does), when a namespace admin activates
  it, or when the same grant is given again. A namespace of the same name
  created under another id does not inherit it. So the system and data
  restores work in either order. A grant the archive holds orphaned is
  written orphaned, and never replaces a grant that is effective here.
- **A version 1 archive's key scopes become read grants.** Archives are
  version 2 now: a runtime key's namespaces list is a scope only, and every
  read the key has is a grant in the archive. A version 1 archive was mostly
  made while the list gave read by itself, so its keys hold no grant where
  they relied on it. Not always: develop builds of 2026-11-08 to 2026-11-13
  (no release) read only through grants and still wrote version 1, so from
  their archives this also gives back reads revoked in that window; the dry
  run lists every one. The restore writes a read grant for each (key,
  listed namespace) that the archive holds no grant for, to a key the
  restore leaves as the archive's (never one of this install's that shares
  the name). With the id the archive records for the namespace (a
  reservation's or an effective grant's, never an orphan's), it is restored
  like any archived grant. With none recorded: effective where a namespace
  of the name is here; orphaned under the reservation's id where only a
  reservation of the name is here, so it becomes read when that namespace
  is created; skipped, with a note to restore again after the namespace,
  where nothing of the name is (or only an install's reservation, which
  holds no id yet). `scope_read_grants` lists those written. A version 2
  archive gets none: there a listed namespace without a grant is access that
  was ended.
- **Attachments come last,** after keys and grants, so an attached app's key
  exists when its route returns. Each is matched by name and restored with
  its archived status; the attach-time rules hold: a name one of this
  install's own apps routes under, an upstream that is not a valid URL, or
  one that resolves from here to a blocked target is skipped and listed,
  never written. The blocked-target check is advisory, as at attach time
  (the router's app edge checks again where it resolves). An upstream that
  does not resolve from here is written and listed as `unresolved`: in
  disaster recovery the app often comes back after the stack. When the
  attach policy file is unusable, every attachment is skipped; when it says
  the install runs no app edge, every active one is (`no_app_edge`: nothing
  would serve it), while an inactive one is restored as a record. Each entry is
  checked on its own: one that fails the attach rules (a record stored
  before a rule existed, or an edited archive) is skipped and listed, and
  never stops the backup or the rest of the restore. An entry the source's
  deployer manages is skipped: the install that deploys the app re-creates
  it, and on another stack it would point at a host that is not there.
- **Three modes.** `upsert` (the default) creates what is missing and leaves
  a record that differs; `overwrite` also replaces it; `mirror` also removes
  every record the archive does not hold, so the archive becomes the exact
  auth state. A whole-stack archive's mirror is stack-wide; a scoped
  archive's removes only within its scope (ArchiveScope). It adds no new kind
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
  changed since. Writes are not one transaction. A record another request
  creates while the restore runs is skipped and listed (`concurrent_write`),
  and the restore goes on; any other failure (the database is unreachable)
  stops the run with an error and no report, and re-running converges. After a
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

from beanie.exceptions import RevisionIdWasChanged
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator
from pymongo.errors import DuplicateKeyError

from wip_auth import PRIVILEGED_GROUPS, get_auth_config

from ..models.api_key import StoredAPIKey, key_name_error
from ..models.attachment import REMOVED_FIELDS as REMOVED_ATTACHMENT_FIELDS
from ..models.attachment import AppAttachment, AttachmentFields, AttachmentRequest
from ..models.grant import NamespaceGrant
from ..models.namespace import Namespace
from ..models.reservation import RuntimeReservation
from .key_salt import adopt as adopt_salt
from .key_salt import current_salt
from .key_salt import write_lock as key_salt_write_lock
from .namespace_identity import id_held_for, new_namespace_id

logger = logging.getLogger("registry.system_backup")

ARCHIVE_FORMAT: Final[Literal["wip-system-backup"]] = "wip-system-backup"
# Version 2: a key's namespaces list is a scope only, so every read a key has
# is a grant in the archive. Version 1 archives were mostly made while the
# list itself gave read on each namespace it named; a restore turns that into
# read grants (SystemArchive._scope_read_grants). Develop builds of 2026-11-08
# to 2026-11-13 (unreleased) also wrote version 1 under the new rule.
ARCHIVE_VERSION: Final[Literal[2]] = 2
BOOTSTRAP_GRANTOR = "system:bootstrap"

Origin = Literal["file", "console", "restored"]


# --- Archive shape ---


class ArchiveScope(BaseModel):
    """What a scoped backup covers, declared rather than inferred. A grant or
    reservation is in scope by its namespace or prefix; a key only when it
    holds a non-empty list of namespaces, every one in scope, and is in no
    stack-wide group; an attachment only when named.

    A key in a stack-wide group (wip-admins, wip-services) reaches every
    namespace whatever its own list says: the Registry grants such a key
    admin, or trusts it as a service, before its namespace list is consulted. So it is never inside a scope, and a scoped
    restore neither writes, replaces nor removes one.

    A restore of a scoped archive changes nothing outside it: the archive's
    own records outside the scope are skipped (`outside_archive_scope`, so an
    edited archive cannot reach past it), a key whose copy here reaches
    outside the scope is not replaced (`out_of_scope_here`), and a mirror
    removes only in-scope records: never a key in `keys_left_out`, nor one
    with a grant outside the scope (removing it would delete that grant)."""

    model_config = ConfigDict(extra="forbid")

    namespaces: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(
        default_factory=list,
        description="Attachments in scope, by name. Attachments have no namespace, "
        "so a scoped backup carries only those named here",
    )
    keys_left_out: list[str] = Field(
        default_factory=list,
        description="Keys the scope touches that the archive does not carry, because "
        "they reach outside it: every key with a grant or "
        "reservation in scope, every key whose namespaces overlap the scope without "
        "fitting in it, every key with no namespace list, and every key in a "
        "stack-wide group (wip-admins, wip-services), which reaches every namespace. "
        "A scoped mirror never removes them",
    )

    def holds_key(self, namespaces: list[str] | None, groups: list[str]) -> bool:
        return (
            bool(namespaces)
            and set(namespaces or []) <= set(self.namespaces)
            and not stack_wide(groups)
        )


def stack_wide(groups: list[str]) -> bool:
    """Whether a key in these groups reaches every namespace, whatever its
    list: the privileged groups (wip-admins, wip-services)."""
    return bool(set(groups) & PRIVILEGED_GROUPS)


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
    namespace_id: str | None = Field(
        ...,
        description=(
            "The identity of the namespace the grant is for: its own on an "
            "orphan, else that of the namespace (or reservation) holding the name "
            "on the source; null when neither held it. Required: an archive "
            "without namespace ids is refused."
        ),
    )

    @model_validator(mode="after")
    def _canonical_key_subject(self) -> "ArchiveGrant":
        # A key's grant subject is its bare name: the grant API normalises
        # the "apikey:<name>" spelling on every write, and permission checks
        # match the bare name. An archive from a stack that still stored the
        # old spelling is read the same way, so it matches what is here.
        if self.subject_type == "api_key":
            self.subject = self.subject.removeprefix("apikey:")
        return self


_PERMISSION_RANK: Final = {"read": 1, "write": 2, "admin": 3}


def _one_grant_per_identity(grants: list[ArchiveGrant]) -> list[ArchiveGrant]:
    """One grant per (namespace, subject_type, subject), in archive order.
    Two archived spellings of one key's grant collapse into one: the higher
    permission, and between equals the effective (non-orphaned) one."""
    kept: dict[tuple[str, str, str], ArchiveGrant] = {}
    for g in grants:
        identity = (g.namespace, g.subject_type, g.subject)
        held = kept.get(identity)
        if held is None or (_PERMISSION_RANK[g.permission], not g.orphaned) > (
            _PERMISSION_RANK[held.permission], not held.orphaned
        ):
            kept[identity] = g
    return list(kept.values())


def _scope_read_grants(archive: "SystemArchive") -> list[ArchiveGrant]:
    """The read grants a version 1 archive's runtime keys held by their
    namespaces list alone: one per listed namespace the key holds no
    archived grant on (any permission, orphaned included, is the decision
    that was made there). Keys the source's deployer managed and keys with
    an invalid name are left out (_scope_gave_read).

    Each carries the id the archive records for its namespace (a reservation
    or effective grant of the same prefix), so a namespace of that name here
    with another id gets it orphaned. With no id recorded it is placed by
    name at restore (_place_scope_grant)."""
    held = {(g.namespace, g.subject) for g in archive.grants if g.subject_type == "api_key"}
    ids = _archived_namespace_ids(archive)
    return [
        ArchiveGrant(
            namespace=prefix, subject=k.name, subject_type="api_key", permission="read",
            granted_by="system:key-scope-restore", origin="restored",
            namespace_id=ids.get(prefix),
        )
        for k in archive.api_keys if _scope_gave_read(k, archive.source.scope)
        for prefix in dict.fromkeys(k.namespaces or [])
        if (prefix, k.name) not in held
    ]


def _archived_namespace_ids(archive: "SystemArchive") -> dict[str, str]:
    """The id of the namespace that held each prefix on the source: a
    reservation's, else an effective grant's. An orphaned grant's id is never
    used: it names a namespace that was not the one holding the name (one
    deleted, or another of the same name), so a read placed under it would be
    orphaned against a namespace that never comes."""
    ids: dict[str, str] = {
        r.prefix: r.namespace_id for r in archive.reservations if r.namespace_id is not None
    }
    for g in archive.grants:
        if g.namespace_id is not None and not g.orphaned:
            ids.setdefault(g.namespace, g.namespace_id)
    return ids


def _scope_gave_read(k: ArchiveKey, scope: ArchiveScope | None) -> bool:
    """Whether a version 1 key's namespaces list gave it read: a runtime key
    with a list, not the source deployer's, with a valid name. As the startup
    backfill did, a key in a stack-wide group counts too: such a key that is
    not a platform admin read its listed namespaces through the list as well.
    A scoped archive holds only keys inside its scope (holds_key, which also
    leaves out stack-wide keys). Whether the key is written here is decided
    at restore (_restore_grants), against this install."""
    return (
        k.managed_by is None and bool(k.namespaces) and not key_name_error(k.name)
        and (scope is None or scope.holds_key(k.namespaces, k.groups))
    )


class ArchiveReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    namespace_id: str | None = Field(
        ...,
        description=(
            "The identity of the namespace reserved. Required; null only for a "
            "reservation the install's file made (managed_by wip-deploy) while no "
            "namespace of its name existed, which holds none."
        ),
    )
    app: str
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    created_by: str = "system"
    managed_by: Literal["wip-deploy"] | None = None
    origin: Origin = "console"

    @model_validator(mode="after")
    def _id_unless_install_reservation(self) -> "ArchiveReservation":
        if self.namespace_id is None and self.managed_by is None:
            raise ValueError(
                f"reservation of '{self.prefix}': namespace_id is required (null only for "
                "a reservation managed by wip-deploy)"
            )
        return self


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
    """The system backup: the Registry's API keys, grants, reservations and
    attachments."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["wip-system-backup"] = ARCHIVE_FORMAT
    version: Literal[1, 2] = Field(
        default=ARCHIVE_VERSION,
        description=(
            "2: every read a key has is a grant in the archive. 1: made while a "
            "runtime key's namespaces list gave read by itself (or by an unreleased "
            "develop build of 2026-11-08 to 2026-11-13, which read through grants "
            "only); a restore writes that read as a grant (listed in the report's "
            "scope_read_grants)"
        ),
    )
    created_at: datetime
    source: ArchiveSource
    api_keys: list[ArchiveKey] = Field(default_factory=list)
    grants: list[ArchiveGrant] = Field(default_factory=list)
    reservations: list[ArchiveReservation] = Field(default_factory=list)
    attachments: list[ArchiveAttachment] = Field(default_factory=list)

    _scope_read_grants: set[str] = PrivateAttr(default_factory=set)

    @model_validator(mode="after")
    def _one_grant_each(self) -> "SystemArchive":
        self.grants = _one_grant_per_identity(self.grants)
        return self

    @model_validator(mode="after")
    def _scope_read_as_grants(self) -> "SystemArchive":
        # A version 1 archive comes from a stack where a runtime key's
        # namespaces list gave read on each namespace it named, with no
        # grant. Restored as it stands, such a key would lose every read
        # it had. The read is written as the grant it now has to be. Never
        # for version 2: there a listed namespace without a grant is a
        # grant revoked or purged, and writing one would hand access back.
        if self.version == 1:
            added = _scope_read_grants(self)
            self.grants += added
            self._scope_read_grants = {
                _grant_id(g.namespace, g.subject_type, g.subject) for g in added
            }
        return self

    def is_scope_read_grant(self, g: "ArchiveGrant") -> bool:
        return _grant_id(g.namespace, g.subject_type, g.subject) in self._scope_read_grants

    @model_validator(mode="after")
    def _no_prefixed_attachments(self) -> "SystemArchive":
        # An archive made while attachments were served under /apps/<name>
        # carries their prefix settings, which mean nothing at an origin's
        # root. There is no conversion here: restore it on an install from
        # before that change, which drops them, and back it up again.
        old = sorted({
            a.name for a in self.attachments
            if any(f in (a.model_extra or {}) for f in REMOVED_ATTACHMENT_FIELDS)
        })
        if old:
            raise ValueError(
                f"attachment(s) {', '.join(old)} carry {', '.join(REMOVED_ATTACHMENT_FIELDS)}: "
                "this archive was made while attachments were served under /apps/<name>. "
                "Restore it on an install from before attachments moved to their origin's "
                "root, then back that install up again"
            )
        return self


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
    "app_route",                # an app this install deploys has this label
    "attach_policy_unavailable",
    "no_app_edge",              # active in the archive; this install runs no app edge to serve it
    "upstream_invalid",
    "upstream_blocked",
    "concurrent_write",         # another request attached the name during the restore
    "mirror_removes_key",       # a grant to a key the archive lacks and the mirror removes
    "caller_key",               # refused: the mirror would remove the key of this request
    "outside_archive_scope",    # a scoped archive's record outside its own declared scope
    "out_of_scope_here",        # a scoped restore would replace a key that reaches outside the scope here
    "scope_namespace_absent",   # a version 1 key's list named it, the archive has no id, and it is not here
    "scope_key_not_restored",   # a version 1 key's list named it, and the key here is not the archive's
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
_OUTSIDE_ARCHIVE_SCOPE = (
    "outside the scope the archive declares; a scoped archive is restored only within it"
)


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
        description="Overwrite and mirror: records the restore must not write or "
        "remove: the key this request authenticates with, which a mirror would remove "
        "or a replacing restore would replace by the archive's different record. A "
        "plan with refusals is not applied.",
    )
    plan_digest: str | None = Field(
        default=None,
        description="Mirror only: a digest of what the mirror removes. Pass it as "
        "`expect` on the apply to refuse a plan that changed since this dry run.",
    )
    scope_read_grants: list[str] = Field(
        default_factory=list,
        description="Version 1 archives only: the read grants written (or, in a dry "
        "run, to be written) for a key's namespaces list, where the archive holds no "
        "grant, as grant ids. They also appear under `grants` (and under "
        "`grants.orphaned` when written orphaned); one not written is under "
        "`grants.skipped`",
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


def _require_id(r: RuntimeReservation) -> str | None:
    """The reservation's id; none only for an install's reservation of an
    absent namespace (namespace_identity)."""
    if not r.namespace_id and r.managed_by is None:
        # The startup migration gives every runtime reservation its id first.
        raise RuntimeError(f"reservation of '{r.prefix}' has no namespace_id")
    return r.namespace_id or None


def _grant_id(namespace: str, subject_type: str, subject: str) -> str:
    return f"{namespace}/{subject_type}:{subject}"


def _key_content(k: StoredAPIKey | ArchiveKey) -> tuple:
    return (
        k.key_hash, k.owner, sorted(k.groups), k.description, _norm_dt(k.expires_at),
        k.enabled, sorted(k.namespaces) if k.namespaces is not None else None,
    )


def _grant_content(
    g: NamespaceGrant | ArchiveGrant, orphaned: bool | None = None, namespace_id: str | None = None,
) -> tuple:
    if orphaned is None:
        return (g.permission, _norm_dt(g.expires_at), g.orphaned, g.namespace_id if g.orphaned else None)
    return (g.permission, _norm_dt(g.expires_at), orphaned, namespace_id if orphaned else None)


def _reservation_content(r: RuntimeReservation | ArchiveReservation, namespace_id: str | None) -> tuple:
    return (r.app, r.settings, namespace_id)


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
        keys, grants, reservations, attachments = _narrow_to_scope(
            scope, keys, grants, reservations, attachments,
        )
    held = await _held_namespace_ids(grants)
    return SystemArchive(
        created_at=datetime.now(UTC),
        source=ArchiveSource(
            oidc_issuer=current_issuer(), salt_fingerprint=salt_fingerprint(),
            salt=current_salt(), scope=scope,
        ),
        api_keys=[_archive_key(k) for k in sorted(keys, key=lambda k: k.name)],
        grants=[
            _archive_grant(g, held)
            for g in sorted(grants, key=lambda g: (g.namespace, g.subject_type, g.subject))
        ],
        reservations=[_archive_reservation(r) for r in sorted(reservations, key=lambda r: r.prefix)],
        attachments=[_archive_attachment(a) for a in sorted(attachments, key=lambda a: a.name)],
    )


def _narrow_to_scope(
    scope: ArchiveScope,
    keys: list[StoredAPIKey],
    grants: list[NamespaceGrant],
    reservations: list[RuntimeReservation],
    attachments: list[AppAttachment],
) -> tuple[list[StoredAPIKey], list[NamespaceGrant], list[RuntimeReservation], list[AppAttachment]]:
    """The records a scoped archive carries: grants and reservations in its
    namespaces, the attachments it names, and the keys it holds; the keys it
    touches but cannot carry are recorded on the scope (keys_left_out)."""
    in_ns = set(scope.namespaces)
    grants = [g for g in grants if g.namespace in in_ns]
    reservations = [r for r in reservations if r.prefix in in_ns]
    attachments = [a for a in attachments if a.name in set(scope.attachments)]
    carried = [k for k in keys if scope.holds_key(k.namespaces, k.groups)]
    scope.keys_left_out = _keys_left_out(keys, carried, grants, reservations, in_ns)
    return carried, grants, reservations, attachments


def _keys_left_out(
    keys: list[StoredAPIKey],
    carried: list[StoredAPIKey],
    grants: list[NamespaceGrant],
    reservations: list[RuntimeReservation],
    in_ns: set[str],
) -> list[str]:
    """Keys a scoped archive touches but cannot carry: a grant to them or a
    reservation they own in scope, or namespaces of their own that reach
    into it (overlapping, or none at all). Named so the operator sees what
    stays behind, and so a scoped mirror never removes them: only the
    source knows how far a key reached, and a copy elsewhere may since have
    been narrowed into the scope."""
    touched = _touched_keys(grants, reservations) | _keys_reaching_into(keys, in_ns)
    stored = {k.name for k in keys}
    return sorted((touched & stored) - {k.name for k in carried})


def _touched_keys(grants: list[NamespaceGrant], reservations: list[RuntimeReservation]) -> set[str]:
    """Keys granted on, or owning a reservation in, the scope."""
    touched = {g.subject.removeprefix("apikey:") for g in grants if g.subject_type == "api_key"}
    return touched | {r.app for r in reservations if r.managed_by is None}


def _keys_reaching_into(keys: list[StoredAPIKey], in_ns: set[str]) -> set[str]:
    """Keys whose own namespaces reach into the scope: overlapping, none at
    all, or stack-wide groups."""
    return {
        k.name for k in keys
        if not k.namespaces or set(k.namespaces) & in_ns or stack_wide(k.groups)
    }


async def _held_namespace_ids(grants: list[NamespaceGrant]) -> dict[str, str | None]:
    """The id each granted namespace holds, for the grants that are not
    orphaned (an orphaned grant carries its own)."""
    held: dict[str, str | None] = {}
    for g in grants:
        if not g.orphaned and g.namespace not in held:
            held[g.namespace] = await id_held_for(g.namespace)
    return held


def _archive_key(k: StoredAPIKey) -> ArchiveKey:
    return ArchiveKey(
        name=k.name, key_hash=k.key_hash, owner=k.owner, groups=list(k.groups),
        description=k.description, created_at=k.created_at, expires_at=k.expires_at,
        enabled=k.enabled, namespaces=k.namespaces, created_by=k.created_by,
        managed_by=k.managed_by, origin=k.origin,
    )


def _archive_grant(g: NamespaceGrant, held: dict[str, str | None]) -> ArchiveGrant:
    return ArchiveGrant(
        namespace=g.namespace, subject=g.subject, subject_type=g.subject_type,
        permission=g.permission, granted_by=g.granted_by, granted_at=g.granted_at,
        expires_at=g.expires_at, managed_by=g.managed_by, origin=g.origin,
        orphaned=g.orphaned, orphaned_at=g.orphaned_at,
        namespace_id=g.namespace_id if g.orphaned else held[g.namespace],
    )


def _archive_reservation(r: RuntimeReservation) -> ArchiveReservation:
    return ArchiveReservation(
        prefix=r.prefix, namespace_id=_require_id(r), app=r.app,
        settings=dict(r.settings), created_at=r.created_at,
        created_by=r.created_by, managed_by=r.managed_by, origin=r.origin,
    )


def _archive_attachment(a: AppAttachment) -> ArchiveAttachment:
    return ArchiveAttachment(
        name=a.name, **_attachment_fields(a), status=a.status,
        managed_by=a.managed_by, created_at=a.created_at, created_by=a.created_by,
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
    plan, refused = await _plan_and_refusals(
        archive, mode, config_key_names, user_grants, caller_key,
    )
    _refuse_unsafe_apply(refused, plan, dry_run=dry_run, expect=expect)

    adopt, salt_note = (
        await _salt_to_adopt(archive, plan, refused, config_key_names)
        if archive.source.salt_fingerprint != salt_fingerprint() else (None, None)
    )

    report = _restore_report(archive, plan, refused, dry_run=dry_run, mode=mode, adopt=adopt)
    if salt_note:
        report.warnings.append(salt_note)
    if adopt:
        report.warnings.append(_salt_adopted_warning(dry_run))
        if not dry_run:
            await _adopt_while_fresh(adopt, archive, plan, config_key_names)
    if await _holds_own_state():
        report.warnings.append(
            "This instance already holds API keys, grants or reservations of its own. "
            "Restoring brings back the archive's state, including access revoked "
            "since the backup was taken. Review the lists below before applying."
        )
    report.warnings += _scope_warnings(archive, mode)
    if not same_issuer:
        report.warnings.append(_issuer_warning(archive, include_user_grants))

    written_keys, removed_hashes = await _run_restore_steps(
        archive, report, plan, dry_run=dry_run, overwrite=overwrite,
        user_grants=user_grants, config_key_names=config_key_names, restored_by=restored_by,
    )
    if archive._scope_read_grants:
        report.warnings.append(_V1_WARNING)
    return report, written_keys, removed_hashes


_V1_WARNING = (
    "A version 1 archive. Its keys' namespaces lists are restored as read grants "
    "(scope_read_grants lists those written; the rest are under grants.skipped). An "
    "archive made by a develop build of 2026-11-08 to 2026-11-13 is also version 1, "
    "though its source already read only through grants: from such an archive this "
    "also gives back any read the source revoked in that window. Review them."
)


async def _plan_and_refusals(
    archive: SystemArchive,
    mode: RestoreMode,
    config_key_names: frozenset[str],
    user_grants: bool,
    caller_key: str | None,
) -> tuple["MirrorPlan | None", list[SkippedRecord]]:
    """A mirror's plan (None for other modes), and the records the restore
    may not write: the plan's refusals, and in an overwriting mode a
    replacement of the caller's own key."""
    plan = (
        await _plan_mirror(archive, config_key_names, user_grants, caller_key)
        if mode == "mirror" else None
    )
    refused = list(plan.refused) if plan is not None else []
    if mode != "upsert":
        refused += await _caller_replacement(archive, caller_key, config_key_names)
    return plan, refused


async def _adopt_while_fresh(
    salt: str, archive: SystemArchive, plan: "MirrorPlan | None", config_key_names: frozenset[str],
) -> None:
    """Adopt the archive's salt, deciding freshness again under the lock key
    creation holds (key_salt.write_lock): a key created since the first
    decision was stored under this stack's salt, and adopting would kill it.
    Nothing is written yet when this refuses."""
    async with key_salt_write_lock:
        fresh = await is_fresh() if plan is None else await _fresh_after(plan, archive, config_key_names)
        if not fresh:
            raise SaltMismatch(
                "An API key of this stack's own was created while the restore was "
                "being prepared; adopting the archive's salt would invalidate it. "
                "Nothing was written."
            )
        await adopt_salt(salt)


def _salt_adopted_warning(dry_run: bool) -> str:
    return (
        "The archive was made under another runtime-key salt and this stack holds "
        "no API keys of its own: "
        + ("its salt would be adopted, and the install's managed keys re-hashed."
           if dry_run else
           "its salt is adopted, and the install's managed keys are re-hashed. "
           "Services verify under it from their next key sync.")
    )


def _issuer_warning(archive: SystemArchive, include_user_grants: bool) -> str:
    return (
        f"The archive was made under OIDC issuer {archive.source.oidc_issuer!r}, "
        f"this instance uses {current_issuer()!r}: user grants are "
        + ("restored (include_user_grants)." if include_user_grants
           else "skipped; pass include_user_grants to restore them.")
    )


async def _run_restore_steps(
    archive: SystemArchive,
    report: SystemRestoreReport,
    plan: "MirrorPlan | None",
    *,
    dry_run: bool,
    overwrite: bool,
    user_grants: bool,
    config_key_names: frozenset[str],
    restored_by: str,
) -> tuple[list[tuple[str | None, StoredAPIKey]], list[str]]:
    """Restore (or report) reservations, keys, grants and attachments in
    that order, then apply a mirror's removals. Returns the keys written
    and the hashes of the keys the mirror removed."""
    planned = await _restore_reservations(archive, report, dry_run, overwrite, restored_by)
    written_keys = await _restore_keys(archive, report, dry_run, overwrite, config_key_names)
    removed_keys = {k.name for k in plan.keys} if plan is not None else set()
    await _restore_grants(archive, report, dry_run, overwrite, user_grants, planned, removed_keys)
    await _restore_attachments(archive, report, dry_run, overwrite, restored_by)
    removed_hashes: list[str] = []
    if plan is not None and not dry_run:
        removed_hashes = await _apply_mirror(plan, restored_by)
    return written_keys, removed_hashes


def _refuse_unsafe_apply(
    refused: list[SkippedRecord],
    plan: "MirrorPlan | None",
    *,
    dry_run: bool,
    expect: str | None,
) -> None:
    """Refuse an apply that the dry run would have flagged: a record the
    restore may not write, or a mirror whose plan changed since the dry run
    that returned ``expect``. Nothing is written either way."""
    if dry_run:
        return
    if refused:
        raise RestoreRefused(
            "The restore would " + "; ".join(f"{r.reason} ({r.id})" for r in refused)
            + ". Nothing was written."
        )
    if plan is not None and expect is not None and expect != plan.digest:
        raise PlanChanged(
            "The mirror plan changed since the dry run that returned the expected "
            "digest. Nothing was written; run the dry run again and review it."
        )


async def _salt_to_adopt(
    archive: SystemArchive,
    plan: "MirrorPlan | None",
    refused: list[SkippedRecord],
    config_key_names: frozenset[str],
) -> tuple[str | None, str | None]:
    """(the archive's salt to adopt, a note for the report) for an archive
    made under another runtime-key salt.

    The salt can be adopted only when it is known (carried by the archive,
    or the configured one it names by fingerprint) and this stack holds no
    API keys of its own that adopting it would invalidate; otherwise the
    restore cannot go on (SaltMismatch). With refusals pending (a dry run
    only), the decision waits for them and the note says so.
    """
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
        return None, (
            "The archive was made under another runtime-key salt. Whether this "
            "stack can adopt it is decided once the refusals below are resolved."
        )
    if not fresh:
        raise SaltMismatch(
            "The archive's key hashes were made under a different runtime-key salt, "
            "and this stack already holds API keys of its own (not managed by its "
            "key file), which adopting the archive's salt would invalidate. Restore "
            "onto a fresh stack, or revoke this stack's own keys first."
        )
    return archive_salt, None


def _restore_report(
    archive: SystemArchive,
    plan: "MirrorPlan | None",
    refused: list[SkippedRecord],
    *,
    dry_run: bool,
    mode: RestoreMode,
    adopt: str | None,
) -> SystemRestoreReport:
    """The report a restore starts from: its refusals and, for a mirror, the
    records the plan removes and the plan's digest."""
    report = SystemRestoreReport(dry_run=dry_run, mode=mode, salt_adopted=adopt is not None)
    report.refused = refused
    if plan is not None:
        report.plan_digest = plan.digest
        report.api_keys.deleted = sorted(k.name for k in plan.keys)
        report.grants.deleted = sorted(plan.grant_ids)
        report.reservations.deleted = sorted(r.prefix for r in plan.reservations)
        report.attachments.deactivated = sorted(a.name for a in plan.attachments)
    return report


def _scope_warnings(archive: SystemArchive, mode: RestoreMode) -> list[str]:
    """What a scoped archive's restore should tell the operator: its scope,
    grants to keys it does not carry, and reservations owned by such keys."""
    scope = archive.source.scope
    if scope is None:
        return []
    warnings = [
        f"A scoped archive: namespaces {scope.namespaces or '(none)'}, attachments "
        f"{scope.attachments or '(none)'}."
        + (" A mirror removes only within this scope." if mode == "mirror" else "")
    ]
    carried = {k.name for k in archive.api_keys}
    uncarried = _grants_to_uncarried_keys(archive, carried)
    if uncarried:
        warnings.append(
            "Grants to keys the archive does not carry (they hold a namespace outside "
            "the scope, or none): " + ", ".join(uncarried) + ". Each is written as "
            "any grant: effective where the key exists here, otherwise it gives nothing."
        )
    orphan_owned = _reservations_of_uncarried_keys(archive, carried)
    if orphan_owned:
        warnings.append(
            "Reservations owned by keys the archive does not carry: "
            + ", ".join(orphan_owned) + ". Where the owner key does not exist here, "
            "only a platform admin can create such a namespace."
        )
    return warnings


def _grants_to_uncarried_keys(archive: SystemArchive, carried: set[str]) -> list[str]:
    """The archive's grants to API keys it does not carry, as grant ids."""
    return sorted({
        _grant_id(g.namespace, g.subject_type, g.subject) for g in archive.grants
        if g.subject_type == "api_key" and g.subject.removeprefix("apikey:") not in carried
    })


def _reservations_of_uncarried_keys(archive: SystemArchive, carried: set[str]) -> list[str]:
    """The archive's key-owned reservations whose owner key it does not carry."""
    return sorted(
        f"{r.prefix} (owner {r.app})" for r in archive.reservations
        if r.managed_by is None and r.app not in carried
    )


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
    await _mirror_keys(plan, archive, config_key_names, caller_key, in_ns)
    removed_subjects = set().union(*(_key_subjects(k.name) for k in plan.keys))
    plan.grants = await _mirror_grants(archive, user_grants, in_ns, removed_subjects)
    plan.reservations = await _mirror_reservations(archive, in_ns)
    plan.attachments = await _mirror_attachments(archive)
    return plan


async def _mirror_keys(
    plan: MirrorPlan,
    archive: SystemArchive,
    config_key_names: frozenset[str],
    caller_key: str | None,
    in_ns: set[str] | None,
) -> None:
    """The unmanaged keys a mirror removes, into ``plan.keys``; the caller's
    own key is refused instead of removed."""
    scope = archive.source.scope
    archived_keys = {k.name for k in archive.api_keys}
    granted_outside = await _keys_granted_outside(in_ns)
    for doc in await StoredAPIKey.find({"managed_by": None}).to_list():
        if doc.name in archived_keys or doc.name in config_key_names:
            continue
        if scope is not None and _key_outside_scope(doc, scope, granted_outside):
            continue
        if caller_key is not None and doc.name == caller_key:
            plan.refused.append(_skip_named(
                doc.name, "caller_key", "remove the key this request authenticates with",
            ))
            continue
        plan.keys.append(doc)


async def _keys_granted_outside(in_ns: set[str] | None) -> set[str]:
    """Keys with a grant outside the scope. Such a grant gives no access while
    the key's namespaces lie inside the scope, but it is a record outside it,
    and removing the key would delete it with the key."""
    if in_ns is None:
        return set()
    return {
        g.subject.removeprefix("apikey:")
        for g in await NamespaceGrant.find(
            {"subject_type": "api_key", "namespace": {"$nin": sorted(in_ns)}}
        ).to_list()
    }


def _key_outside_scope(doc: StoredAPIKey, scope: ArchiveScope, granted_outside: set[str]) -> bool:
    """Whether a scoped mirror must leave a key alone. The archive's own word
    first: a key the source left out (it held a namespace outside the scope
    there) is never removed, even when this stack's copy of it holds only
    in-scope namespaces."""
    return (
        doc.name in scope.keys_left_out
        or not scope.holds_key(doc.namespaces, doc.groups)
        or doc.name in granted_outside
    )


async def _mirror_grants(
    archive: SystemArchive,
    user_grants: bool,
    in_ns: set[str] | None,
    removed_subjects: set[str],
) -> list[NamespaceGrant]:
    """The unmanaged grants a mirror removes: every grant to a key it removes,
    and, inside the scope, every grant the archive does not hold and the key
    file does not own. Bootstrap grants, and user grants a restore would not
    write, are kept."""
    archived_grants = {_grant_id(g.namespace, g.subject_type, g.subject) for g in archive.grants}
    removed: list[NamespaceGrant] = []
    for g in await NamespaceGrant.find({"managed_by": None}).to_list():
        if g.granted_by == BOOTSTRAP_GRANTOR:
            continue
        if g.subject_type == "user" and not user_grants:
            continue
        removed_with_key = g.subject_type == "api_key" and g.subject in removed_subjects
        if removed_with_key or await _grant_unclaimed(g, in_ns, archived_grants):
            removed.append(g)
    return removed


async def _grant_unclaimed(
    g: NamespaceGrant, in_ns: set[str] | None, archived_grants: set[str],
) -> bool:
    """A grant inside the scope that neither the archive holds nor the key
    file owns."""
    if in_ns is not None and g.namespace not in in_ns:
        return False
    if _grant_id(g.namespace, g.subject_type, g.subject) in archived_grants:
        return False
    return not await _grant_owned_by_file(_as_archive_grant(g), g)


async def _mirror_reservations(
    archive: SystemArchive, in_ns: set[str] | None,
) -> list[RuntimeReservation]:
    """The unmanaged reservations inside the scope the archive does not hold."""
    archived_prefixes = {r.prefix for r in archive.reservations}
    return [
        r for r in await RuntimeReservation.find({"managed_by": None}).to_list()
        if r.prefix not in archived_prefixes and (in_ns is None or r.prefix in in_ns)
    ]


async def _mirror_attachments(archive: SystemArchive) -> list[AppAttachment]:
    """The unmanaged active attachments inside the scope the archive does not
    hold."""
    scope = archive.source.scope
    archived_attachments = {a.name for a in archive.attachments}
    in_attachments = set(scope.attachments) if scope else None
    return [
        a for a in await AppAttachment.find({"managed_by": None, "status": "active"}).to_list()
        if a.name not in archived_attachments
        and (in_attachments is None or a.name in in_attachments)
    ]


async def _caller_replacement(
    archive: SystemArchive, caller_key: str | None, config_key_names: frozenset[str],
) -> list[SkippedRecord]:
    """A replacing restore (overwrite, mirror) must not swap the key of the
    request for the archive's different record of that name: a re-created
    admin key restored from an older archive would lock the operator out.
    Only a replacement the restore would make is refused: a key it skips
    (_key_skip: a config key's name, a managed record, outside the archive's
    scope, reaching outside it here) stays as it is."""
    if caller_key is None:
        return []
    archived = next((k for k in archive.api_keys if k.name == caller_key), None)
    if archived is None:
        return []
    current = await StoredAPIKey.find_one(StoredAPIKey.name == caller_key)
    if current is None or _key_skip(archive, archived, current, config_key_names) is not None:
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
        permission=g.permission, namespace_id=g.namespace_id,
    )


def _key_skip(
    archive: SystemArchive, k: ArchiveKey, existing: StoredAPIKey | None,
    config_key_names: frozenset[str],
) -> SkippedRecord | None:
    """Why the restore leaves the archive's key record unwritten, or None.

    The one rule for both the key restore and the salt decision
    (_fresh_after): a key the restore skips keeps this stack's hash, so it
    must count against adopting another salt.
    """
    name_error = key_name_error(k.name)
    if name_error:
        return _skip_named(k.name, "invalid_record", name_error)
    scope = archive.source.scope
    if scope is not None and not scope.holds_key(k.namespaces, k.groups):
        return _skip_named(k.name, "outside_archive_scope", _OUTSIDE_ARCHIVE_SCOPE)
    if existing is not None and existing.managed_by is not None:
        return _skip_named(k.name, "managed_by_deployer", _MANAGED)
    if k.name in config_key_names:
        return _skip_named(
            k.name, "config_key_name", "a config key of this name is loaded on this instance",
        )
    if (
        existing is not None and scope is not None
        and _key_content(existing) != _key_content(k)
        and not scope.holds_key(existing.namespaces, existing.groups)
    ):
        # The scope bounds what a scoped restore may change. Here the key
        # also reaches outside it (a namespace outside it, none, or a
        # stack-wide group); the archive's record would take that access away.
        return _skip_named(
            k.name, "out_of_scope_here",
            "here the key reaches outside the archive's scope (a namespace outside "
            "it, none, or a stack-wide group); a scoped restore does not replace it",
        )
    return None


async def _fresh_after(
    plan: MirrorPlan, archive: SystemArchive, config_key_names: frozenset[str],
) -> bool:
    """Whether, after this mirror, no stored key here depends on this stack's
    salt except the managed ones: each of its own keys is either removed or
    written from the archive's record. A key the restore skips (_key_skip)
    keeps this stack's hash, so it makes the stack not fresh."""
    removed = {k.name for k in plan.keys}
    archived = {k.name: k for k in archive.api_keys}
    for doc in await StoredAPIKey.find({"managed_by": None}).to_list():
        if doc.name in removed:
            continue
        k = archived.get(doc.name)
        if k is None or _key_skip(archive, k, doc, config_key_names) is not None:
            return False
    return True


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
    from .attachments import AttachPolicyUnavailable, load_policy, resolve_install_addresses

    out = report.attachments
    if not archive.attachments:
        return
    try:
        policy = load_policy()
    except AttachPolicyUnavailable as e:
        for a in archive.attachments:
            out.skipped.append(_skip_named(a.name, "attach_policy_unavailable", f"attach policy unavailable: {e}"))
        return
    install_addresses = await resolve_install_addresses(policy)
    for a in archive.attachments:
        await _restore_attachment(
            a, out, archive.source.scope, policy, install_addresses,
            dry_run=dry_run, overwrite=overwrite, restored_by=restored_by,
        )


def _attachment_refusal(
    a: ArchiveAttachment, scope: ArchiveScope | None, reserved: set[str],
) -> tuple[Any, SkippedRecord | None]:
    """(the attach request the record makes, None), or (None, why it is
    skipped before the target is read): outside the archive's scope, managed
    on the source, an invalid record, or an app's own route label."""
    if scope is not None and a.name not in scope.attachments:
        return None, _skip_named(a.name, "outside_archive_scope", _OUTSIDE_ARCHIVE_SCOPE)
    if a.managed_by is not None:
        return None, _skip_named(
            a.name, "managed_on_source",
            "managed by wip-deploy on the source; the install that deploys the app re-creates it",
        )
    request, why = _validated_attachment(a)
    if request is None:
        return None, _skip_named(a.name, "invalid_record", why or "invalid")
    if a.name in reserved:
        return None, _skip_named(
            a.name, "app_route", f"{a.name!r} is the label of an app this install deploys",
        )
    return request, None


async def _restore_attachment(
    a: ArchiveAttachment,
    out: Any,
    scope: ArchiveScope | None,
    policy: Any,
    install_addresses: Any,
    *,
    dry_run: bool,
    overwrite: bool,
    restored_by: str,
) -> None:
    """Restore (or report) one archived attachment into ``out``."""
    request, skip = _attachment_refusal(a, scope, set(policy.reserved_names))
    if skip is not None:
        out.skipped.append(skip)
        return
    existing = await AppAttachment.find_one(AppAttachment.name == a.name)
    if existing is not None and existing.managed_by is not None:
        out.skipped.append(_skip_named(a.name, "managed_by_deployer", _MANAGED))
        return
    fields = _attachment_fields(request)
    status: Literal["active", "inactive"] = "active" if a.status == "active" else "inactive"
    if existing is not None and (_attachment_fields(existing), existing.status) == (fields, status):
        out.unchanged.append(a.name)
        return
    skip = _attachment_write_refusal(a, existing, status, policy, overwrite)
    if skip is None:
        check, skip = await _attachment_upstream(a, request, policy, install_addresses)
    if skip is not None:
        out.skipped.append(skip)
        return
    await _take_attachment(
        a, out, existing, fields, status, request, check, dry_run=dry_run, restored_by=restored_by,
    )


async def _take_attachment(
    a: ArchiveAttachment,
    out: Any,
    existing: AppAttachment | None,
    fields: dict[str, Any],
    status: Literal["active", "inactive"],
    request: Any,
    check: Any,
    *,
    dry_run: bool,
    restored_by: str,
) -> None:
    """Report an attachment as replaced or created (and unresolved when its
    upstream check warned), and write it unless this is a dry run."""
    (out.replaced if existing is not None else out.created).append(a.name)
    if check.warnings:
        out.unresolved.append(a.name)
    if dry_run:
        return
    if await _write_attachment(a, existing, fields, status, restored_by, out):
        logger.info(
            "Attachment %s restored (%s) by %s: -> %s",
            a.name, status, restored_by, request.upstream,
        )


def _attachment_write_refusal(
    a: ArchiveAttachment,
    existing: AppAttachment | None,
    status: Literal["active", "inactive"],
    policy: Any,
    overwrite: bool,
) -> SkippedRecord | None:
    """Why a differing attachment is not written: an active one this install
    has no app edge to serve, or one that differs without overwrite. Checked
    after the unchanged check: an identical record here is reported as
    unchanged, not skipped; only a write is refused without an edge."""
    if status == "active" and not policy.app_edge:
        return _skip_named(
            a.name, "no_app_edge",
            "this install runs no app edge to serve it (`wip-deploy add-module app-edge`)",
        )
    if existing is not None and not overwrite:
        return _skip_named(a.name, "differs", _DIFFERS)
    return None


async def _attachment_upstream(
    a: ArchiveAttachment, request: Any, policy: Any, install_addresses: Any,
) -> tuple[Any, SkippedRecord | None]:
    """The upstream check of an attachment to write: (its result, None), or
    (None, why its upstream is refused)."""
    from .attachments import UpstreamInvalid, check_upstream

    try:
        check = await check_upstream(request.upstream, policy, install_addresses)
    except UpstreamInvalid as e:
        return None, _skip_named(a.name, "upstream_invalid", str(e))
    if check.blocked:
        return None, _skip_named(a.name, "upstream_blocked", f"upstream refused: {check.blocked}")
    return check, None


async def _write_attachment(
    a: ArchiveAttachment,
    existing: AppAttachment | None,
    fields: dict[str, Any],
    status: Literal["active", "inactive"],
    restored_by: str,
    out: Any,
) -> bool:
    """Replace or insert the attachment; False when another request attached
    the name during the restore (the item becomes a concurrent_write skip).

    A replace saves by the record's id and upserts: a record deleted since
    the read is written back as the archive holds it. One deleted and
    attached again under the same name (a new id) collides on the name and
    is skipped, as an insert that loses is.
    """
    now = datetime.now(UTC)
    try:
        if existing is not None:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.status = status
            existing.updated_at = now
            existing.updated_by = restored_by
            await existing.save()
        else:
            await AppAttachment(
                name=a.name, **fields, status=status, managed_by=None,
                created_at=a.created_at or now, created_by=a.created_by,
                updated_at=now, updated_by=restored_by,
            ).insert()
    except (DuplicateKeyError, RevisionIdWasChanged):
        # Beanie raises an upsert's duplicate key as RevisionIdWasChanged.
        (out.replaced if existing is not None else out.created).remove(a.name)
        if a.name in out.unresolved:
            out.unresolved.remove(a.name)
        out.skipped.append(_skip_named(
            a.name, "concurrent_write", "attached by another request during the restore",
        ))
        return False
    return True


async def _restore_reservations(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    restored_by: str,
) -> dict[str, str]:
    """Restore the archive's reservations. Returns {prefix: namespace_id} for
    each one written or planned (a dry run writes nothing), so the grants
    decide against what the restore leaves.

    A reservation of a name whose namespace exists here reserves that
    namespace and takes its id; otherwise it keeps the archive's."""
    out = report.reservations
    scope = archive.source.scope
    planned: dict[str, str] = {}
    for r in archive.reservations:
        if scope is not None and r.prefix not in scope.namespaces:
            out.skipped.append(_skip_reservation(r.prefix, "outside_archive_scope", _OUTSIDE_ARCHIVE_SCOPE))
            continue
        ns = await Namespace.find_one(Namespace.prefix == r.prefix)
        existing = await RuntimeReservation.find_one(RuntimeReservation.prefix == r.prefix)
        # Restored, a reservation is a runtime one and holds an id. One the
        # archive did not record (an install's reservation of an absent
        # namespace) keeps the id a reservation here already holds, else a
        # new one.
        target_id = (
            ns.namespace_id if ns is not None and ns.namespace_id
            else r.namespace_id or (existing.namespace_id if existing else None) or new_namespace_id()
        )
        if existing is not None:
            if existing.managed_by is not None:
                out.skipped.append(_skip_reservation(r.prefix, "managed_by_deployer", _MANAGED))
                continue
            if _reservation_content(existing, existing.namespace_id) == _reservation_content(r, target_id):
                out.unchanged.append(r.prefix)
                continue
            if not overwrite:
                out.skipped.append(_skip_reservation(r.prefix, "differs", _DIFFERS))
                continue
            out.replaced.append(r.prefix)
        else:
            out.created.append(r.prefix)
        planned[r.prefix] = target_id
        if dry_run:
            continue
        fields = {
            "namespace_id": target_id, "app": r.app, "settings": dict(r.settings),
            "created_at": r.created_at or datetime.now(UTC),
            "created_by": r.created_by or restored_by,
            "managed_by": None, "origin": "restored",
        }
        if existing is not None:
            await existing.set(fields)
            continue
        try:
            await RuntimeReservation(prefix=r.prefix, **fields).insert()
        except DuplicateKeyError:
            del planned[r.prefix]
            _concurrent_write(out, r.prefix, _skip_reservation(
                r.prefix, "concurrent_write", "reserved by another request during the restore",
            ))
    return planned


async def _restore_keys(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    config_key_names: frozenset[str],
) -> list[tuple[str | None, StoredAPIKey]]:
    out = report.api_keys
    written: list[tuple[str | None, StoredAPIKey]] = []
    for k in archive.api_keys:
        existing = await StoredAPIKey.find_one(StoredAPIKey.name == k.name)
        skip = _key_skip(archive, k, existing, config_key_names)
        if skip is not None:
            out.skipped.append(skip)
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
            continue
        doc = StoredAPIKey(name=k.name, **fields)
        try:
            await doc.insert()
        except DuplicateKeyError:
            _concurrent_write(out, k.name, _skip_named(
                k.name, "concurrent_write", "created by another request during the restore",
            ))
            continue
        written.append((None, doc))
    return written


def _concurrent_write(out: RecordReport, record_id: str, skip: SkippedRecord) -> None:
    """A record another request created between the restore's read and its
    insert: listed as skipped instead of created; the restore goes on."""
    out.created.remove(record_id)
    if record_id in out.orphaned:
        out.orphaned.remove(record_id)
    out.skipped.append(skip)


async def _grantable(g: ArchiveGrant, planned: dict[str, str]) -> bool:
    """Whether a grant is written effective: its namespace is here.

    That is a namespace (active or archived), or a reservation (as this
    restore leaves it: ``planned``), holding the namespace id the grant was
    archived with. A namespace or reservation of the same name with another
    id is another namespace: the grant is written orphaned, with its id, and a
    namespace created later with that id activates it. A grant archived
    without an id (no namespace or reservation held its name on the source)
    is written as it was: effective, for whichever namespace takes the name.
    """
    if g.namespace_id is None:
        return True
    ns = await Namespace.find_one({"prefix": g.namespace, "status": {"$in": ["active", "archived"]}})
    if ns is not None:
        return ns.namespace_id == g.namespace_id
    if g.namespace in planned:
        return planned[g.namespace] == g.namespace_id
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == g.namespace)
    return doc is not None and doc.namespace_id == g.namespace_id


async def _grant_owned_by_file(g: ArchiveGrant, existing: NamespaceGrant | None) -> bool:
    if existing is not None and existing.managed_by is not None:
        return True
    if g.subject_type != "api_key":
        return False
    key = await StoredAPIKey.find_one(StoredAPIKey.name == g.subject)
    return key is not None and key.managed_by is not None and key.grants_managed


async def _restore_grants(
    archive: SystemArchive, report: SystemRestoreReport, dry_run: bool, overwrite: bool,
    user_grants: bool, planned: dict[str, str], removed_keys: set[str] | None = None,
) -> None:
    out = report.grants
    # The keys this restore leaves as the archive's: a version 1 key's scope
    # read is given only to them, never to a key of this install's that
    # merely shares the name (a config key, one the deployer manages here).
    archive_keys = await _keys_left_as_archived(archive, report.api_keys)
    for g in archive.grants:
        skip = _grant_refusal(g, archive.source.scope, user_grants, removed_keys)
        if skip is None and archive.is_scope_read_grant(g):
            placed = await _place_scope_grant(g, planned, archive_keys)
            if isinstance(placed, SkippedRecord):
                skip = placed
            else:
                g = placed
        if skip is not None:
            out.skipped.append(skip)
            continue
        await _restore_grant(g, out, planned, dry_run=dry_run, overwrite=overwrite)
    # Only the scope reads written count as restored; a skipped one is
    # under grants.skipped, and an orphaned one also under grants.orphaned.
    written = set(out.created) | set(out.replaced)
    report.scope_read_grants = sorted(archive._scope_read_grants & written)


async def _keys_left_as_archived(archive: SystemArchive, keys: RecordReport) -> set[str]:
    """The names of the keys this restore leaves as the archive's key: written,
    unchanged, or kept as they are here because the record differs (an upsert)
    while the key itself, its hash, is the archive's."""
    names = set(keys.created) | set(keys.replaced) | set(keys.unchanged)
    differs = {s.name for s in keys.skipped if s.reason_code == "differs"}
    for k in archive.api_keys:
        if k.name in differs:
            here = await StoredAPIKey.find_one(StoredAPIKey.name == k.name)
            if here is not None and here.managed_by is None and here.key_hash == k.key_hash:
                names.add(k.name)
    return names


_RESTORE_AGAIN = (
    "Run the system restore again once the namespace is here (after its data "
    "restore) to give the read."
)


async def _place_scope_grant(
    g: ArchiveGrant, planned: dict[str, str], archive_keys: set[str],
) -> ArchiveGrant | SkippedRecord:
    """Where a read grant a version 1 key's list stood in for is written, or
    why it is not.

    Only for a key this restore leaves as the archive's. With the namespace's
    id from the archive, the id decides as for any grant (_grantable).
    Without one: a namespace of the name here takes it by name, as the list
    gave it. Where only a reservation of the name is here, it is written
    orphaned under the reservation's id, so it becomes read when that
    namespace is created and never for another one taking the name later. An
    install's reservation that holds no id yet, or nothing of the name, gets
    nothing: a name-only grant on nothing would give read on whichever
    namespace takes the name."""
    if g.subject not in archive_keys:
        return _skip_grant(
            g, "scope_key_not_restored",
            "a version 1 key's namespaces list named it, and the key here is not the "
            "archive's (see the key's own entry under api_keys)",
        )
    if g.namespace_id is not None:
        return g
    if await Namespace.find_one({"prefix": g.namespace, "status": {"$in": ["active", "archived"]}}):
        return g
    reserved_id = planned.get(g.namespace)
    if reserved_id is None:
        res = await RuntimeReservation.find_one(RuntimeReservation.prefix == g.namespace)
        if res is not None and res.namespace_id is None:
            return _skip_grant(
                g, "scope_namespace_absent",
                "the key's namespaces list named it on the source, the archive records "
                "no id for it, and only an install's reservation of that name is here, "
                "which holds no namespace id yet. " + _RESTORE_AGAIN,
            )
        reserved_id = res.namespace_id if res is not None else None
    if reserved_id is not None:
        return g.model_copy(update={"namespace_id": reserved_id, "orphaned": True})
    return _skip_grant(
        g, "scope_namespace_absent",
        "the key's namespaces list named it on the source, the archive records no "
        "id for it, and no namespace or reservation of that name is here. " + _RESTORE_AGAIN,
    )


def _grant_refusal(
    g: ArchiveGrant,
    scope: ArchiveScope | None,
    user_grants: bool,
    removed_keys: set[str] | None,
) -> SkippedRecord | None:
    """Why a grant is skipped before the target is read: outside the
    archive's scope, a user grant from another OIDC issuer, or a grant to a
    key the mirror removes."""
    if scope is not None and g.namespace not in scope.namespaces:
        return _skip_grant(g, "outside_archive_scope", _OUTSIDE_ARCHIVE_SCOPE)
    if g.subject_type == "user" and not user_grants:
        return _skip_grant(g, "other_oidc_issuer", "user grant from another OIDC issuer")
    if (
        removed_keys and g.subject_type == "api_key"
        and g.subject.removeprefix("apikey:") in removed_keys
    ):
        return _skip_grant(
            g, "mirror_removes_key", "its key is not in the archive, and the mirror removes it",
        )
    return None


async def _restore_grant(
    g: ArchiveGrant, out: Any, planned: dict[str, str], *, dry_run: bool, overwrite: bool,
) -> None:
    """Restore (or report) one archived grant into ``out``."""
    gid = _grant_id(g.namespace, g.subject_type, g.subject)
    # Ownership first: the import writes a key's declared grants whether
    # or not the namespace exists, and "restore the namespace" would send
    # an operator after one that never comes.
    existing = await NamespaceGrant.find_one({
        "namespace": g.namespace, "subject": g.subject, "subject_type": g.subject_type,
    })
    if await _grant_owned_by_file(g, existing):
        out.skipped.append(_skip_grant(g, "managed_by_deployer", _MANAGED))
        return
    orphaned = g.orphaned or not await _grantable(g, planned)
    if existing is not None:
        settled = _existing_grant_outcome(g, existing, orphaned, overwrite)
        if settled == "unchanged":
            out.unchanged.append(gid)
            return
        if settled is not None:
            out.skipped.append(settled)
            return
        out.replaced.append(gid)
    else:
        out.created.append(gid)
    if orphaned:
        out.orphaned.append(gid)
    if not dry_run and not await _write_grant(g, existing, orphaned):
        _concurrent_write(out, gid, _skip_grant(
            g, "concurrent_write", "granted by another request during the restore",
        ))


def _existing_grant_outcome(
    g: ArchiveGrant, existing: NamespaceGrant, orphaned: bool, overwrite: bool,
) -> SkippedRecord | Literal["unchanged"] | None:
    """What a grant this instance already holds comes to: "unchanged", a
    skip, or None to replace it."""
    if not existing.orphaned and g.orphaned:
        # An archived orphan was never access on its source. Replacing an
        # effective grant here with it would take access away, or (with a
        # higher permission) hand out what nobody granted.
        return _skip_grant(
            g, "orphaned_in_archive",
            "orphaned in the archive (or placed under a reservation's id), effective "
            "here; left as it is",
        )
    if _grant_content(existing) == _grant_content(g, orphaned, g.namespace_id):
        return "unchanged"
    if overwrite:
        return None
    if existing.orphaned and not orphaned:
        return _skip_grant(
            g, "orphaned_here",
            "orphaned on this instance; activate it on the namespace, or pass "
            "overwrite to replace it",
        )
    return _skip_grant(g, "differs", _DIFFERS)


async def _write_grant(g: ArchiveGrant, existing: NamespaceGrant | None, orphaned: bool) -> bool:
    """Write the archived grant over the one held here, or insert it. False
    when another request created the grant since it was read."""
    fields = {
        "permission": g.permission, "granted_by": g.granted_by,
        "granted_at": g.granted_at or datetime.now(UTC), "expires_at": g.expires_at,
        "managed_by": None, "origin": "restored",
        "orphaned": orphaned,
        "orphaned_at": (g.orphaned_at or datetime.now(UTC)) if orphaned else None,
        "namespace_id": g.namespace_id if orphaned else None,
    }
    if existing is not None:
        await existing.set(fields)
        return True
    try:
        await NamespaceGrant(
            namespace=g.namespace, subject=g.subject, subject_type=g.subject_type, **fields,
        ).insert()
    except DuplicateKeyError:
        return False
    return True
