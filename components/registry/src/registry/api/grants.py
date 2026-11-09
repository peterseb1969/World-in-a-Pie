"""Namespace grant management API endpoints.

Provides grant CRUD (bulk-first) and user-facing permission queries.
"""

import logging
import math
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wip_auth import PRIVILEGED_GROUPS, UserIdentity, is_superadmin

from ..models.api_key import StoredAPIKey
from ..models.grant import (
    GrantActivate,
    GrantActivateBulkResponse,
    GrantBulkResponse,
    GrantCreate,
    GrantListResponse,
    GrantResponse,
    GrantRevoke,
    GrantRevokeBulkResponse,
    MyNamespaceResponse,
    NamespaceGrant,
)
from ..models.namespace import Namespace
from ..models.reservation import RuntimeReservation
from ..services.auth import require_admin_key, require_api_key
from ..services.errors import error_fields
from ..services.event_bus import access_changed
from ..services.managed import MANAGED_BY_DEPLOYER, grant_refusal

# The wire-level set of auth_method values UserIdentity accepts. Mirrors
# the Literal in libs/wip-auth/src/wip_auth/models.py::UserIdentity.
AuthMethod = Literal["jwt", "api_key", "gateway_oidc", "none"]

router = APIRouter()        # /{prefix}/grants — mounted at /namespaces
my_router = APIRouter()     # /my/namespaces — mounted at /my
all_router = APIRouter()    # every grant, for platform admins — mounted at /grants

logger = logging.getLogger("registry.grants")

# Permission hierarchy for comparison
_PERMISSION_LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}


def _build_synthetic_identity(
    *,
    user_id: str,
    email: str | None,
    groups: list[str],
    auth_method: AuthMethod,
    key_namespaces_header: str | None,
    username: str | None = None,
) -> UserIdentity:
    """Reconstruct the calling service's UserIdentity at the Registry side.

    CASE-351: when the calling service forwards an api-key's namespace
    scope via X-Key-Namespaces, that list must land on the synthetic
    identity's `raw_claims["namespaces"]`, where `_resolve_permission`
    reads a key's scope (in `_grant_subjects`) and a reservation's owner
    (in `reservation_owner`).

    Header absent → raw_claims=None, matching today's behaviour for
    unscoped admin/services keys and for non-api_key identities.

    Empty header (no namespaces in scope) is treated as an explicit
    empty list, not an absent header — a key explicitly scoped to no
    namespaces is meaningfully different from an unscoped key. (Today
    no caller produces this shape, but the distinction is recorded
    so future code can rely on it.)
    """
    raw_claims: dict | None = None
    if key_namespaces_header is not None:
        # Strip per-item whitespace from CSV; drop empty fragments so
        # a stray comma in the header doesn't poison the list.
        ns_list = [
            ns.strip() for ns in key_namespaces_header.split(",") if ns.strip()
        ]
        raw_claims = {"namespaces": ns_list}
    # CASE-450: callers that forward `username` (api-key name) get exact
    # grant-subject parity with direct Registry calls. The email/user_id
    # fallback keeps old-lib callers working (covered by the compat match
    # in _resolve_permission).
    return UserIdentity(
        user_id=user_id,
        username=username or email or user_id,
        email=email,
        groups=groups,
        auth_method=auth_method,
        raw_claims=raw_claims,
    )


async def _grant_is_managed(
    namespace: str, subject: str, subject_type: str, grant: NamespaceGrant | None
) -> bool:
    """A grant wip-deploy owns: one imported from the install's key file, or
    any grant to a key whose grants the key file declares (the import would
    undo a change made through the API)."""
    if grant is not None and grant.managed_by is not None:
        return True
    if subject_type != "api_key":
        return False
    key = await StoredAPIKey.find_one(StoredAPIKey.name == subject)
    return key is not None and key.managed_by is not None and key.grants_managed


def _grant_to_response(grant: NamespaceGrant) -> GrantResponse:
    return GrantResponse(
        namespace=grant.namespace,
        subject=grant.subject,
        subject_type=grant.subject_type,
        permission=grant.permission,
        granted_by=grant.granted_by,
        granted_at=grant.granted_at,
        expires_at=grant.expires_at,
        managed_by=grant.managed_by,
        orphaned=grant.orphaned,
        orphaned_at=grant.orphaned_at,
    )


def _grant_label(grant: NamespaceGrant) -> str:
    return f"{grant.namespace}/{grant.subject_type}:{grant.subject}"


async def reservation_owner(identity: UserIdentity, prefix: str) -> str | None:
    """The calling key's name when it owns the runtime reservation of
    `prefix`, else None.

    A runtime reservation (one the Registry's API made, not the install's
    file) names the key that reserved the prefix. It is that key's standing
    approval to create the namespace, independent of the namespace's
    lifecycle: deleting the namespace purges every grant on it, the owner's
    `admin` included, but leaves the reservation, and the owner must still be
    able to bootstrap it again, by upsert or by restoring its backup. A file
    reservation names an app, not a key; its key's grants come back through
    the key import instead.

    Only while the prefix is in the key's own namespace scope: narrowing a
    key off a namespace that exists keeps the reservation for an operator to
    release, and that narrowing must not come undone when the namespace is
    later deleted. A key that reserves a prefix always holds it in scope, so
    an unscoped key is never an owner.
    """
    if identity.auth_method != "api_key":
        return None
    scope = (identity.raw_claims or {}).get("namespaces")
    if not isinstance(scope, list) or prefix not in scope:
        return None
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if doc is None or doc.managed_by is not None:
        return None
    name = identity.username.removeprefix("apikey:")
    return name if doc.app == name else None


async def _resolve_permission(identity: UserIdentity, namespace: str) -> str:
    """Resolve the effective permission for an identity on a namespace.

    Resolution order (the first step that answers decides):
    0. Locked namespace → "none" for everyone (deletion in progress)
    1. Superadmin (wip-admins group) → "admin"
    2. The owner of a runtime reservation of the prefix, while the namespace
       does not exist → "admin"
    3. A config-file key that declares grants → its own declaration
       (`_config_key_permission`)
    4. API key scope (`_grant_subjects`): a scoped key outside its list, or
       an unscoped key outside the privileged groups (wip-admins,
       wip-services) → "none"
    5. The highest unexpired, non-orphaned grant to the caller (a key by
       name, a user by email and id) or to one of its groups
    6. Otherwise "none"

    A runtime key's namespace list is a scope, never a permission: it caps
    what the key's grants reach and gives nothing itself. Key creation
    writes a read grant on each listed namespace it gives no other
    permission on, so the access ends with the grant — when the namespace is
    deleted (its grants are purged), when a restore writes it orphaned, or
    when it is revoked. A config-file key that declares grants resolves from
    its own declaration (step 3).
    """
    # Locked namespaces are inaccessible to everyone
    ns = await Namespace.find_one({"prefix": namespace})
    if ns and ns.status == "locked":
        return "none"

    if is_superadmin(identity):
        return "admin"

    # The owner of a runtime reservation is admin on its prefix while the
    # namespace does not exist: the reservation is its standing approval to
    # create it, by upsert or by a restore (which checks admin first), even
    # after a deletion purged its grant. Once the namespace exists, its
    # grants decide again.
    if ns is None and await reservation_owner(identity, namespace) is not None:
        return "admin"

    declared = _config_key_permission(identity, namespace)
    if declared is not None:
        return declared

    subjects = _grant_subjects(identity, namespace)
    if not subjects:
        return "none"
    return await _best_grant(namespace, subjects)


def _config_key_permission(identity: UserIdentity, namespace: str) -> str | None:
    """A config-file key that declares grants (a spec-declared key) resolves
    locally from its own definition, in the same order as wip-auth's
    resolve_permission: the declared grant, else read on a namespace in the
    key's scope, else none. Without this the Registry would answer "read"
    where every other service answers the declared "write". None for any
    other caller."""
    config_grants = (identity.raw_claims or {}).get("grants")
    if identity.auth_method != "api_key" or not isinstance(config_grants, dict):
        return None
    declared = config_grants.get(namespace)
    if declared is not None:
        return str(declared)
    key_namespaces = (identity.raw_claims or {}).get("namespaces") or []
    return "read" if namespace in key_namespaces else "none"


def _grant_subjects(identity: UserIdentity, namespace: str) -> list[tuple[str, str]]:
    """The (subject_type, subject) pairs whose grants count for the caller on
    the namespace: the key (by name) or the user (by email and id), and its
    groups. Empty when a key's scope excludes the namespace, or when an
    unscoped key is not in a privileged group (only those may omit
    scoping)."""
    subjects: list[tuple[str, str]] = []
    if identity.auth_method == "api_key":
        subjects.append(("api_key", identity.username))
        # CASE-450 compat: cross-service callers on a pre-450 wip-auth don't
        # forward username, so the synthetic identity carries the user_id
        # form ("apikey:<name>") while grants are stored under the bare key
        # name. Match both spellings.
        if identity.username.startswith("apikey:"):
            subjects.append(("api_key", identity.username.removeprefix("apikey:")))
        namespaces = (identity.raw_claims or {}).get("namespaces")
        if namespaces is not None:
            if namespace not in namespaces:
                return []
        elif not any(g in PRIVILEGED_GROUPS for g in identity.groups):
            return []
    else:
        # OIDC user — check by email
        if identity.email:
            subjects.append(("user", identity.email))
        subjects.append(("user", identity.user_id))
    subjects.extend(("group", group) for group in identity.groups)
    return subjects


async def _best_grant(namespace: str, subjects: list[tuple[str, str]]) -> str:
    """The highest unexpired permission granted to any of the subjects on the
    namespace. An orphaned grant gives nothing until it is activated."""
    query = {
        "namespace": namespace,
        "orphaned": {"$ne": True},
        "$or": [
            {"subject": subj, "subject_type": stype}
            for stype, subj in subjects
        ],
    }
    best = "none"
    for grant in await NamespaceGrant.find(query).to_list():
        if grant.is_expired():
            continue
        if _PERMISSION_LEVELS.get(grant.permission, 0) > _PERMISSION_LEVELS.get(best, 0):
            best = grant.permission
    return best

async def resolve_accessible_namespaces(identity: UserIdentity) -> list[str] | None:
    """Namespaces a directly-calling identity may read, or None for superadmin.

    None means "no restriction" (superadmin) — callers should apply no
    namespace filter. A concrete list (possibly empty) means "restrict reads
    to exactly these prefixes." Used by the registry's own enumeration
    endpoints to scope listings to the caller's grants, the same way the
    stores scope their listings via wip_auth.permissions. This is the
    in-process twin of the service-to-service ``/my/accessible-namespaces``
    endpoint: the identity is already the direct caller here, so there is no
    synthetic-identity reconstruction and no HTTP self-call.
    """
    if is_superadmin(identity):
        return None

    all_ns = await Namespace.find({"status": "active"}).to_list()
    return [
        ns.prefix
        for ns in all_ns
        if await _resolve_permission(identity, ns.prefix) != "none"
    ]


class NamespaceAccess:
    """A direct caller's permission per namespace, memoised for one request.

    Privileged callers (wip-admins, wip-services) are unrestricted: they are
    the platform services calling with the install's key, and they act on
    every namespace on behalf of their own callers. Everyone else gets
    exactly what _resolve_permission grants — reading an entry needs read
    on its namespace, changing one needs write.

    Isolation rules (open mode, allowed_external_refs) decide what a
    namespace may REFERENCE, never what a caller may READ, so they play no
    part here; the stores' read checks work the same way. A cross-namespace
    reference is resolved by the service that validates it, with its own
    key.
    """

    def __init__(self, identity: UserIdentity) -> None:
        self._identity = identity
        self.unrestricted = identity.has_any_group(PRIVILEGED_GROUPS)
        self._permissions: dict[str, str] = {}

    async def allows(self, namespace: str, required: str) -> bool:
        if self.unrestricted:
            return True
        if namespace not in self._permissions:
            self._permissions[namespace] = await _resolve_permission(
                self._identity, namespace
            )
        return (
            _PERMISSION_LEVELS.get(self._permissions[namespace], 0)
            >= _PERMISSION_LEVELS[required]
        )

    async def readable_namespaces(self) -> list[str] | None:
        """The namespaces this caller may read, or None for no restriction."""
        if self.unrestricted:
            return None
        return await resolve_accessible_namespaces(self._identity)


# =============================================================================
# Grant management (requires admin on the namespace)
# =============================================================================


async def _announce_grant_changes(results: list[dict], changed: tuple[str, ...]) -> None:
    """Tell other services' grant caches when any item changed a grant."""
    if any(r["status"] in changed for r in results):
        await access_changed(keys=False)


@router.get(
    "/{prefix}/grants",
    response_model=list[GrantResponse],
    summary="List grants for a namespace",
)
async def list_grants(
    prefix: str,
    identity: UserIdentity = Depends(require_api_key),
):

    # Must be admin on this namespace (or superadmin)
    permission = await _resolve_permission(identity, prefix)
    if _PERMISSION_LEVELS.get(permission, 0) < _PERMISSION_LEVELS["admin"]:
        raise HTTPException(403, f"Requires admin access to namespace '{prefix}'")

    grants = await NamespaceGrant.find({"namespace": prefix}).to_list()
    return [_grant_to_response(g) for g in grants]


@router.post(
    "/{prefix}/grants",
    response_model=GrantBulkResponse,
    summary="Create grants for a namespace (bulk)",
)
async def create_grants(
    prefix: str,
    items: list[GrantCreate],
    identity: UserIdentity = Depends(require_api_key),
):

    # Must be admin on this namespace
    permission = await _resolve_permission(identity, prefix)
    if _PERMISSION_LEVELS.get(permission, 0) < _PERMISSION_LEVELS["admin"]:
        raise HTTPException(403, f"Requires admin access to namespace '{prefix}'")

    # Verify namespace exists
    ns = await Namespace.find_one({"prefix": prefix, "status": "active"})
    if not ns:
        raise HTTPException(404, f"Namespace '{prefix}' not found")

    results = []
    for i, item in enumerate(items):
        try:
            # CASE-450: canonical api_key subject is the bare key name —
            # normalize a user_id-form spelling so one logical grant has
            # one stored record regardless of how the operator wrote it.
            subject = item.subject
            if item.subject_type == "api_key":
                subject = subject.removeprefix("apikey:")
            # Upsert: update permission if grant already exists
            existing = await NamespaceGrant.find_one({
                "namespace": prefix,
                "subject": subject,
                "subject_type": item.subject_type,
            })
            if await _grant_is_managed(prefix, subject, item.subject_type, existing):
                raise grant_refusal(prefix, subject)
            if existing:
                # Granting again is an admin's explicit decision, so it also
                # activates an orphaned grant.
                existing.permission = item.permission
                existing.expires_at = item.expires_at
                existing.granted_by = identity.identity_string
                existing.granted_at = datetime.now(UTC)
                existing.orphaned = False
                existing.orphaned_at = None
                existing.namespace_id = None
                await existing.save()
                results.append({
                    "index": i, "status": "updated",
                    "subject": subject, "permission": item.permission,
                })
            else:
                grant = NamespaceGrant(
                    namespace=prefix,
                    subject=subject,
                    subject_type=item.subject_type,
                    permission=item.permission,
                    granted_by=identity.identity_string,
                    expires_at=item.expires_at,
                )
                await grant.create()
                results.append({
                    "index": i, "status": "created",
                    "subject": subject, "permission": item.permission,
                })
        except Exception as e:
            results.append({
                "index": i, "status": "error",
                "subject": item.subject, **error_fields(e),
            })

    succeeded = sum(1 for r in results if r["status"] != "error")
    await _announce_grant_changes(results, ("created", "updated"))
    return {
        "results": results,
        "total": len(items),
        "succeeded": succeeded,
        "failed": len(items) - succeeded,
    }


@router.delete(
    "/{prefix}/grants",
    response_model=GrantRevokeBulkResponse,
    summary="Revoke grants for a namespace (bulk)",
)
async def revoke_grants(
    prefix: str,
    items: list[GrantRevoke],
    identity: UserIdentity = Depends(require_api_key),
):

    permission = await _resolve_permission(identity, prefix)
    if _PERMISSION_LEVELS.get(permission, 0) < _PERMISSION_LEVELS["admin"]:
        raise HTTPException(403, f"Requires admin access to namespace '{prefix}'")

    results = []
    for i, item in enumerate(items):
        # CASE-450: match the canonical (bare key name) spelling — see create.
        subject = item.subject
        if item.subject_type == "api_key":
            subject = subject.removeprefix("apikey:")
        grant = await NamespaceGrant.find_one({
            "namespace": prefix,
            "subject": subject,
            "subject_type": item.subject_type,
        })
        if await _grant_is_managed(prefix, subject, item.subject_type, grant):
            results.append({
                "index": i, "status": "error", "subject": subject,
                "error": grant_refusal(prefix, subject).detail,
                "error_code": MANAGED_BY_DEPLOYER,
            })
            continue
        if grant:
            await grant.delete()
            results.append({"index": i, "status": "revoked", "subject": subject})
        else:
            results.append({"index": i, "status": "not_found", "subject": subject})

    succeeded = sum(1 for r in results if r["status"] == "revoked")
    await _announce_grant_changes(results, ("revoked",))
    return {
        "results": results,
        "total": len(items),
        "succeeded": succeeded,
        "failed": sum(1 for r in results if r["status"] == "error"),
    }


@router.post(
    "/{prefix}/grants/activate",
    response_model=GrantActivateBulkResponse,
    summary="Activate orphaned grants on a namespace (bulk)",
)
async def activate_grants(
    prefix: str,
    items: list[GrantActivate],
    identity: UserIdentity = Depends(require_api_key),
):
    """Make orphaned grants effective. An orphaned grant is one a system
    restore wrote orphaned: held orphaned in the archive, or made for a
    namespace not on this install (no namespace or reservation holds its
    namespace_id). It counts as no grant until activated here, re-granted, or
    a namespace with its namespace_id is created. Activated
    here, it belongs to this namespace, whatever it was made for. Needs admin on the namespace, which must
    exist. Revoking an orphan works like revoking any grant.

    Per item: activated, unchanged (not orphaned), not_found, or error
    (managed_by_deployer)."""
    permission = await _resolve_permission(identity, prefix)
    if _PERMISSION_LEVELS.get(permission, 0) < _PERMISSION_LEVELS["admin"]:
        raise HTTPException(403, f"Requires admin access to namespace '{prefix}'")
    if not await Namespace.find_one({"prefix": prefix, "status": "active"}):
        raise HTTPException(404, f"Namespace '{prefix}' not found")

    results = []
    for i, item in enumerate(items):
        subject = item.subject
        if item.subject_type == "api_key":
            subject = subject.removeprefix("apikey:")
        grant = await NamespaceGrant.find_one({
            "namespace": prefix, "subject": subject, "subject_type": item.subject_type,
        })
        if grant is None:
            results.append({"index": i, "status": "not_found", "subject": subject})
        elif await _grant_is_managed(prefix, subject, item.subject_type, grant):
            results.append({
                "index": i, "status": "error", "subject": subject,
                "error": grant_refusal(prefix, subject).detail,
                "error_code": MANAGED_BY_DEPLOYER,
            })
        elif not grant.orphaned:
            results.append({"index": i, "status": "unchanged", "subject": subject})
        else:
            await grant.set({"orphaned": False, "orphaned_at": None, "namespace_id": None})
            logger.info(
                "%s activated orphaned grant %s", identity.identity_string, _grant_label(grant)
            )
            results.append({"index": i, "status": "activated", "subject": subject})

    await _announce_grant_changes(results, ("activated",))
    return {
        "results": results,
        "total": len(items),
        "succeeded": sum(1 for r in results if r["status"] in ("activated", "unchanged")),
        "failed": sum(1 for r in results if r["status"] == "error"),
    }


@all_router.get(
    "",
    response_model=GrantListResponse,
    summary="List grants across namespaces (platform admin)",
)
async def list_all_grants(
    orphaned: bool | None = Query(None, description="Only orphaned (true) or only effective (false) grants"),
    namespace: str | None = Query(None, description="Only grants on this namespace prefix"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    _admin: str = Depends(require_admin_key),
) -> GrantListResponse:
    """Every grant, sorted by namespace, subject type and subject. The
    per-namespace listing needs admin on the namespace; an orphaned grant's
    namespace usually does not exist, so this is where they are found."""
    query: dict = {}
    if orphaned is True:
        query["orphaned"] = True
    elif orphaned is False:
        query["orphaned"] = {"$ne": True}
    if namespace is not None:
        query["namespace"] = namespace
    finder = NamespaceGrant.find(query)
    total = await finder.count()
    docs = await (
        finder.sort("+namespace", "+subject_type", "+subject")
        .skip((page - 1) * page_size).limit(page_size).to_list()
    )
    return GrantListResponse(
        items=[_grant_to_response(g) for g in docs],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


# =============================================================================
# User-facing permission queries
# =============================================================================


@my_router.get(
    "/namespaces",
    response_model=list[MyNamespaceResponse],
    summary="List namespaces I can access",
)
async def my_namespaces(
    identity: UserIdentity = Depends(require_api_key),
):
    """List all namespaces the caller has access to, with permission levels."""

    # Get all active namespaces
    all_ns = await Namespace.find({"status": "active"}).to_list()

    accessible = []
    for ns in all_ns:
        perm = await _resolve_permission(identity, ns.prefix)
        if perm != "none":
            accessible.append(MyNamespaceResponse(
                prefix=ns.prefix,
                description=ns.description,
                permission=perm,
            ))

    return accessible


@my_router.get(
    "/namespaces/{prefix}/permission",
    summary="Get my permission on a specific namespace",
)
async def my_namespace_permission(
    prefix: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """Get the caller's permission level on a specific namespace.

    Returns 404 if the namespace doesn't exist or the caller has no access
    (to avoid leaking namespace names).
    """

    # Check namespace exists
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(404, "Namespace not found")

    perm = await _resolve_permission(identity, prefix)
    if perm == "none":
        raise HTTPException(404, "Namespace not found")

    return {"namespace": prefix, "permission": perm}


@my_router.get(
    "/check-permission",
    summary="Check permission for a given identity (internal, service-to-service)",
)
async def check_permission_internal(
    request: Request,
    namespace: str,
    user_id: str,
    email: str | None = None,
    username: str | None = None,
    groups: str | None = None,
    auth_method: AuthMethod = "jwt",
    identity: UserIdentity = Depends(require_api_key),
):
    """Internal endpoint for other WIP services to check permissions.

    Services call this to resolve a user's permission on a namespace
    without that user being the direct caller.

    Groups are read from the X-User-Groups header (preferred) or the
    groups query parameter (legacy, deprecated).

    Only callable by privileged API keys (wip-admins or wip-services group).
    """
    caller = identity
    if not any(g in PRIVILEGED_GROUPS for g in caller.groups):
        raise HTTPException(403, "Only service accounts can call this endpoint")

    # Prefer groups from header (M2 — avoid leaking in logs/caches)
    header_groups = request.headers.get("X-User-Groups")
    groups_str = header_groups or groups
    group_list = groups_str.split(",") if groups_str else []
    # CASE-351 — the calling service forwards the api-key's namespace
    # scope so _resolve_permission's api_key branch can see the same
    # scoping the calling service does. Absent header = unscoped key
    # (admin/services) or non-api_key identity — both match existing
    # behaviour.
    synthetic = _build_synthetic_identity(
        user_id=user_id,
        email=email,
        username=username,
        groups=group_list,
        auth_method=auth_method,
        key_namespaces_header=request.headers.get("X-Key-Namespaces"),
    )

    perm = await _resolve_permission(synthetic, namespace)
    return {"namespace": namespace, "user_id": user_id, "permission": perm}


@my_router.get(
    "/accessible-namespaces",
    summary="Get accessible namespaces for a given identity (internal, service-to-service)",
)
async def accessible_namespaces_internal(
    request: Request,
    user_id: str,
    email: str | None = None,
    username: str | None = None,
    groups: str | None = None,
    auth_method: AuthMethod = "jwt",
    identity: UserIdentity = Depends(require_api_key),
):
    """Internal endpoint for services to get a user's accessible namespaces.

    Returns the list of namespace prefixes the user can access, plus
    whether they are a superadmin (meaning: access to everything).

    Groups are read from X-User-Groups header (preferred) or groups query param.

    Only callable by privileged API keys (wip-admins or wip-services group).
    """
    caller = identity
    if not any(g in PRIVILEGED_GROUPS for g in caller.groups):
        raise HTTPException(403, "Only service accounts can call this endpoint")

    header_groups = request.headers.get("X-User-Groups")
    groups_str = header_groups or groups
    group_list = groups_str.split(",") if groups_str else []
    # CASE-351 — see check_permission_internal for the rationale.
    synthetic = _build_synthetic_identity(
        user_id=user_id,
        email=email,
        username=username,
        groups=group_list,
        auth_method=auth_method,
        key_namespaces_header=request.headers.get("X-Key-Namespaces"),
    )

    if is_superadmin(synthetic):
        return {"namespaces": None, "is_superadmin": True}

    all_ns = await Namespace.find({"status": "active"}).to_list()
    accessible = []
    for ns in all_ns:
        perm = await _resolve_permission(synthetic, ns.prefix)
        if perm != "none":
            accessible.append(ns.prefix)

    return {"namespaces": accessible, "is_superadmin": False}
