"""Namespace grant management API endpoints.

Provides grant CRUD (bulk-first) and user-facing permission queries.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from wip_auth import UserIdentity, get_current_identity

from ..models.grant import (
    GrantCreate,
    GrantResponse,
    GrantRevoke,
    MyNamespaceResponse,
    NamespaceGrant,
)
from ..models.namespace import Namespace
from ..services.auth import require_api_key

router = APIRouter()        # /{prefix}/grants — mounted at /namespaces
my_router = APIRouter()     # /my/namespaces — mounted at /my

# Permission hierarchy for comparison
_PERMISSION_LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}


def _is_superadmin(identity: UserIdentity) -> bool:
    """Check if identity has superadmin access (wip-admins group)."""
    return identity.has_any_group(["wip-admins"])


# Groups whose API keys are allowed to have namespaces=None (all-namespace access).
# wip-admins: human/admin keys, wip-services: service-to-service keys (e.g. reporting-sync).
_PRIVILEGED_GROUPS = {"wip-admins", "wip-services"}


def _grant_to_response(grant: NamespaceGrant) -> GrantResponse:
    return GrantResponse(
        namespace=grant.namespace,
        subject=grant.subject,
        subject_type=grant.subject_type,
        permission=grant.permission,
        granted_by=grant.granted_by,
        granted_at=grant.granted_at,
        expires_at=grant.expires_at,
    )


async def _resolve_permission(identity: UserIdentity, namespace: str) -> str:
    """Resolve the effective permission for an identity on a namespace.

    Resolution order:
    0. Locked namespace → always "none" (deletion in progress)
    1. Superadmin bypass (wip-admins group)
    2. API key namespace check:
       - Scoped key: namespace must be in key's list, otherwise "none"
       - Unscoped key (namespaces=None): only allowed for privileged groups
         (wip-admins, wip-services); non-privileged unscoped keys get "none"
    3. Direct user grant (by email or API key name)
    4. Group grants (any matching group)
    5. API key namespace list fallback (read-only if in list but no grant)
    6. Default: none
    """
    # Locked namespaces are inaccessible to everyone
    ns = await Namespace.find_one({"prefix": namespace})
    if ns and ns.status == "locked":
        return "none"

    if _is_superadmin(identity):
        return "admin"

    # Determine subject identifiers to check
    subjects = []
    if identity.auth_method == "api_key":
        subjects.append(("api_key", identity.username))
        # Check API key namespace restrictions
        namespaces = (identity.raw_claims or {}).get("namespaces")
        if namespaces is not None:
            if namespace not in namespaces:
                return "none"
            # API key with namespace list but no explicit grant → read
            # (can be overridden by explicit grant below)
        else:
            # Unscoped key (namespaces=None): only privileged groups may omit scoping
            if not any(g in _PRIVILEGED_GROUPS for g in identity.groups):
                return "none"
    else:
        # OIDC user — check by email
        if identity.email:
            subjects.append(("user", identity.email))
        subjects.append(("user", identity.user_id))

    # Add group subjects
    for group in identity.groups:
        subjects.append(("group", group))

    if not subjects:
        return "none"

    # Query all matching grants for this namespace
    query = {
        "namespace": namespace,
        "$or": [
            {"subject": subj, "subject_type": stype}
            for stype, subj in subjects
        ],
    }
    grants = await NamespaceGrant.find(query).to_list()

    # Find highest non-expired permission
    best = "none"
    for grant in grants:
        if grant.is_expired():
            continue
        if _PERMISSION_LEVELS.get(grant.permission, 0) > _PERMISSION_LEVELS.get(best, 0):
            best = grant.permission

    # If no explicit grant but API key has namespace in its list → read
    if best == "none" and identity.auth_method == "api_key":
        namespaces = (identity.raw_claims or {}).get("namespaces")
        if namespaces is not None and namespace in namespaces:
            best = "read"

    return best


# =============================================================================
# Grant management (requires admin on the namespace)
# =============================================================================


@router.get(
    "/{prefix}/grants",
    response_model=list[GrantResponse],
    summary="List grants for a namespace",
)
async def list_grants(
    prefix: str,
    _: str = Depends(require_api_key),
):
    identity = get_current_identity()

    # Must be admin on this namespace (or superadmin)
    permission = await _resolve_permission(identity, prefix)
    if _PERMISSION_LEVELS.get(permission, 0) < _PERMISSION_LEVELS["admin"]:
        raise HTTPException(403, f"Requires admin access to namespace '{prefix}'")

    grants = await NamespaceGrant.find({"namespace": prefix}).to_list()
    return [_grant_to_response(g) for g in grants]


@router.post(
    "/{prefix}/grants",
    summary="Create grants for a namespace (bulk)",
)
async def create_grants(
    prefix: str,
    items: list[GrantCreate],
    _: str = Depends(require_api_key),
):
    identity = get_current_identity()

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
            # Upsert: update permission if grant already exists
            existing = await NamespaceGrant.find_one({
                "namespace": prefix,
                "subject": item.subject,
                "subject_type": item.subject_type,
            })
            if existing:
                existing.permission = item.permission
                existing.expires_at = item.expires_at
                existing.granted_by = identity.identity_string
                existing.granted_at = datetime.now(UTC)
                await existing.save()
                results.append({
                    "index": i, "status": "updated",
                    "subject": item.subject, "permission": item.permission,
                })
            else:
                grant = NamespaceGrant(
                    namespace=prefix,
                    subject=item.subject,
                    subject_type=item.subject_type,
                    permission=item.permission,
                    granted_by=identity.identity_string,
                    expires_at=item.expires_at,
                )
                await grant.create()
                results.append({
                    "index": i, "status": "created",
                    "subject": item.subject, "permission": item.permission,
                })
        except Exception as e:
            results.append({
                "index": i, "status": "error",
                "subject": item.subject, "error": str(e),
            })

    succeeded = sum(1 for r in results if r["status"] != "error")
    return {
        "results": results,
        "total": len(items),
        "succeeded": succeeded,
        "failed": len(items) - succeeded,
    }


@router.delete(
    "/{prefix}/grants",
    summary="Revoke grants for a namespace (bulk)",
)
async def revoke_grants(
    prefix: str,
    items: list[GrantRevoke],
    _: str = Depends(require_api_key),
):
    identity = get_current_identity()

    permission = await _resolve_permission(identity, prefix)
    if _PERMISSION_LEVELS.get(permission, 0) < _PERMISSION_LEVELS["admin"]:
        raise HTTPException(403, f"Requires admin access to namespace '{prefix}'")

    results = []
    for i, item in enumerate(items):
        grant = await NamespaceGrant.find_one({
            "namespace": prefix,
            "subject": item.subject,
            "subject_type": item.subject_type,
        })
        if grant:
            await grant.delete()
            results.append({"index": i, "status": "revoked", "subject": item.subject})
        else:
            results.append({"index": i, "status": "not_found", "subject": item.subject})

    succeeded = sum(1 for r in results if r["status"] == "revoked")
    return {
        "results": results,
        "total": len(items),
        "succeeded": succeeded,
        "failed": 0,
    }


# =============================================================================
# User-facing permission queries
# =============================================================================


@my_router.get(
    "/namespaces",
    response_model=list[MyNamespaceResponse],
    summary="List namespaces I can access",
)
async def my_namespaces(
    _: str = Depends(require_api_key),
):
    """List all namespaces the caller has access to, with permission levels."""
    identity = get_current_identity()

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
    _: str = Depends(require_api_key),
):
    """Get the caller's permission level on a specific namespace.

    Returns 404 if the namespace doesn't exist or the caller has no access
    (to avoid leaking namespace names).
    """
    identity = get_current_identity()

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
    groups: str | None = None,
    auth_method: str = "jwt",
    _: str = Depends(require_api_key),
):
    """Internal endpoint for other WIP services to check permissions.

    Services call this to resolve a user's permission on a namespace
    without that user being the direct caller.

    Groups are read from the X-User-Groups header (preferred) or the
    groups query parameter (legacy, deprecated).

    Only callable by privileged API keys (wip-admins or wip-services group).
    """
    caller = get_current_identity()
    if not any(g in _PRIVILEGED_GROUPS for g in caller.groups):
        raise HTTPException(403, "Only service accounts can call this endpoint")

    # Prefer groups from header (M2 — avoid leaking in logs/caches)
    header_groups = request.headers.get("X-User-Groups")
    groups_str = header_groups or groups
    group_list = groups_str.split(",") if groups_str else []
    synthetic = UserIdentity(
        user_id=user_id,
        username=email or user_id,
        email=email,
        groups=group_list,
        auth_method=auth_method,
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
    groups: str | None = None,
    auth_method: str = "jwt",
    _: str = Depends(require_api_key),
):
    """Internal endpoint for services to get a user's accessible namespaces.

    Returns the list of namespace prefixes the user can access, plus
    whether they are a superadmin (meaning: access to everything).

    Groups are read from X-User-Groups header (preferred) or groups query param.

    Only callable by privileged API keys (wip-admins or wip-services group).
    """
    caller = get_current_identity()
    if not any(g in _PRIVILEGED_GROUPS for g in caller.groups):
        raise HTTPException(403, "Only service accounts can call this endpoint")

    header_groups = request.headers.get("X-User-Groups")
    groups_str = header_groups or groups
    group_list = groups_str.split(",") if groups_str else []
    synthetic = UserIdentity(
        user_id=user_id,
        username=email or user_id,
        email=email,
        groups=group_list,
        auth_method=auth_method,
    )

    if _is_superadmin(synthetic):
        return {"namespaces": None, "is_superadmin": True}

    all_ns = await Namespace.find({"status": "active"}).to_list()
    accessible = []
    for ns in all_ns:
        perm = await _resolve_permission(synthetic, ns.prefix)
        if perm != "none":
            accessible.append(ns.prefix)

    return {"namespaces": accessible, "is_superadmin": False}
