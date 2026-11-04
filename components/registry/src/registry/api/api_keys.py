"""API key management endpoints.

Runtime API keys are stored in MongoDB and managed here. Config-file keys
(wip-admins, wip-services) are read-only and cannot be modified via these endpoints.
"""

import logging
import math
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from wip_auth import (
    APIKeyProvider,
    APIKeyRecord,
    UserIdentity,
    get_auth_config,
    get_identity_string,
    hash_api_key,
)

from ..models.api_key import (
    APIKeyCreatedResponse,
    APIKeyCreateRequest,
    APIKeyListResponse,
    APIKeyResponse,
    APIKeySyncRecord,
    APIKeyUpdateRequest,
    NamespaceAddRequest,
    StoredAPIKey,
    generate_plaintext_key,
)
from ..models.grant import NamespaceGrant
from ..models.reservation import RuntimeReservation
from ..services import key_import, key_shadowing, reservation_store
from ..services.auth import require_admin_key, require_groups
from ..services.managed import key_refusal
from ..services.reservation_store import ReservationRefused
from ..services.reservations import ReservationSettings, ReservationsUnavailable

logger = logging.getLogger("registry.api_keys")

router = APIRouter()

# Module-level reference to the APIKeyProvider, set during startup
_api_key_provider: APIKeyProvider | None = None


def configure_api_key_management(provider: APIKeyProvider) -> None:
    """Called during startup to wire the provider."""
    global _api_key_provider
    _api_key_provider = provider


def _is_config_key(name: str) -> bool:
    """A config-file key (cannot be changed through the API). Asked of the
    provider each time: a key-file reload changes the set at runtime."""
    return _api_key_provider is not None and name in _api_key_provider.config_key_names()


async def _managed_doc(name: str) -> StoredAPIKey | None:
    """The stored record of `name` when wip-deploy manages it, else None."""
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    return doc if doc is not None and doc.managed_by is not None else None


async def _refuse_if_managed(name: str) -> None:
    """409 managed_by_deployer for a key imported from the install's key file."""
    if await _managed_doc(name) is not None:
        raise key_refusal(name)


def _config_names() -> set[str]:
    return set(_api_key_provider.config_key_names()) if _api_key_provider is not None else set()


def _api_key_provider_or_none() -> APIKeyProvider | None:
    """The Registry's API key provider, or None before startup wired it."""
    return _api_key_provider


def _get_provider() -> APIKeyProvider:
    if _api_key_provider is None:
        raise HTTPException(
            status_code=503, detail="API key management not initialized"
        )
    return _api_key_provider


async def _declared_grants(docs: list[StoredAPIKey]) -> dict[str, dict[str, str]]:
    """The imported grants of each managed key whose grants the key file
    declares, by key name ({namespace: permission})."""
    names = [d.name for d in docs if d.managed_by is not None and d.grants_managed]
    if not names:
        return {}
    out: dict[str, dict[str, str]] = {n: {} for n in names}
    for g in await NamespaceGrant.find(
        {"subject_type": "api_key", "subject": {"$in": names}, "managed_by": {"$ne": None}}
    ).to_list():
        out[g.subject][g.namespace] = g.permission
    return out


def _stored_to_response(
    doc: StoredAPIKey,
    reserved: list[str] | None = None,
    grants: dict[str, str] | None = None,
) -> APIKeyResponse:
    """Convert a StoredAPIKey document to an APIKeyResponse. `grants` are a
    managed key's declared grants, None for every other key."""
    return APIKeyResponse(
        name=doc.name,
        owner=doc.owner,
        groups=doc.groups,
        description=doc.description,
        created_at=doc.created_at,
        expires_at=doc.expires_at,
        enabled=doc.enabled,
        namespaces=doc.namespaces,
        created_by=doc.created_by,
        source="runtime",
        grants=grants,
        shadowed_by=key_shadowing.shadowed_by(doc.name),
        reserved_namespaces=sorted(reserved or []),
        managed_by=doc.managed_by,
        origin=doc.origin,
    )


async def _reserved_by(name: str) -> list[str]:
    return [r.prefix for r in await reservation_store.reservations_of(name)]


async def _check_reservation(prefix: str, settings: ReservationSettings, owner: str) -> None:
    """The reservation checks as an HTTP error."""
    try:
        await reservation_store.check_new_reservation(prefix, settings, owner)
    except ReservationRefused as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except ReservationsUnavailable as e:
        raise HTTPException(
            status_code=503, detail=f"Namespace reservations are unavailable: {e}"
        ) from e


async def _set_grant(name: str, prefix: str, permission: Literal["read", "write", "admin"]) -> None:
    """Create or replace the key's grant on `prefix`."""
    grant = await NamespaceGrant.find_one({
        "namespace": prefix, "subject": name, "subject_type": "api_key",
    })
    if grant is None:
        await NamespaceGrant(
            namespace=prefix,
            subject=name,
            subject_type="api_key",
            permission=permission,
            granted_by=get_identity_string(),
        ).create()
    elif grant.permission != permission or grant.orphaned:
        # A platform admin setting the key's grant is an explicit decision,
        # so it also activates a grant a system restore left orphaned.
        grant.permission = permission
        grant.orphaned = False
        grant.orphaned_at = None
        await grant.save()


def _grant_query(name: str, prefix: str | None = None) -> dict:
    """The key's grants (on `prefix`, or everywhere). Both subject spellings:
    the bare name (canonical) and the "apikey:<name>" user_id form."""
    query: dict = {"subject_type": "api_key", "subject": {"$in": [name, f"apikey:{name}"]}}
    if prefix is not None:
        query["namespace"] = prefix
    return query


async def _refresh_provider(doc: StoredAPIKey) -> None:
    """Put the key's current record in the provider, unless a config key shadows it."""
    provider = _get_provider()
    async with key_shadowing.provider_lock:
        provider.remove_key(doc.key_hash)
        if not key_shadowing.is_shadowed(doc.name):
            provider.add_key(key_shadowing.runtime_record(doc))


def _config_key_to_response(
    record: APIKeyRecord, managed: StoredAPIKey | None = None
) -> APIKeyResponse:
    """Convert a config-file APIKeyRecord to an APIKeyResponse. `managed` is
    the Registry's imported record of the same key, if there is one."""
    return APIKeyResponse(
        name=record.name,
        owner=record.owner,
        groups=record.groups,
        description=record.description,
        created_at=record.created_at or datetime.now(UTC),
        expires_at=record.expires_at,
        enabled=record.enabled,
        namespaces=record.namespaces,
        created_by="config-file",
        source="config",
        grants=dict(record.grants) if record.grants is not None else None,
        managed_by=managed.managed_by if managed is not None else None,
        origin=managed.origin if managed is not None else None,
    )


@router.post(
    "",
    response_model=APIKeyCreatedResponse,
    summary="Create a runtime API key",
    status_code=201,
)
async def create_api_key(
    request: APIKeyCreateRequest,
    _admin: str = Depends(require_admin_key),
) -> APIKeyCreatedResponse:
    """Create a new runtime API key. The plaintext is returned once and never stored.

    With `reserve`, the key also reserves those namespaces (each must be in
    `namespaces`) and gets `admin` on each, so it can create them with the
    reserved settings: an app key for an app wip-deploy does not manage.
    Every reservation is checked before anything is written.
    """
    provider = _get_provider()

    # CASE-450: grant_permission without a namespace scope is meaningless —
    # there is nothing to grant on.
    if request.grant_permission and not request.namespaces:
        raise HTTPException(
            status_code=422,
            detail="grant_permission requires `namespaces` to be set",
        )

    # A key the install's key file declares belongs to wip-deploy
    await _refuse_if_managed(request.name)

    # Reject name collision with config-file keys
    if _is_config_key(request.name):
        raise HTTPException(
            status_code=409,
            detail=f"Name '{request.name}' conflicts with a config-file key",
        )

    # Reject name collision with existing runtime keys
    existing = await StoredAPIKey.find_one(StoredAPIKey.name == request.name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Runtime key '{request.name}' already exists",
        )

    reserve = request.reserve or []
    reserved_prefixes = [r.prefix for r in reserve]
    if len(set(reserved_prefixes)) != len(reserved_prefixes):
        raise HTTPException(status_code=422, detail="reserve lists a namespace twice")
    outside = sorted(set(reserved_prefixes) - set(request.namespaces or []))
    if outside:
        raise HTTPException(
            status_code=422,
            detail=f"reserve: {', '.join(outside)} not in `namespaces`; a key reserves "
            "only namespaces it is scoped to",
        )
    for r in reserve:
        await _check_reservation(r.prefix, r.settings, request.name)

    # Generate and hash
    plaintext = generate_plaintext_key()
    config = get_auth_config()
    key_hash = hash_api_key(plaintext, config.api_key_hash_salt)

    # Persist to MongoDB
    doc = StoredAPIKey(
        name=request.name,
        key_hash=key_hash,
        owner=request.owner,
        groups=request.groups,
        description=request.description,
        expires_at=request.expires_at,
        namespaces=request.namespaces,
        created_by=get_identity_string(),
    )
    await doc.insert()

    # Add to local provider so it works immediately. A plaintext the Registry
    # generated cannot already be a config key's; a config key that takes it
    # later is caught by the next shadow check.
    provider.add_key(key_shadowing.runtime_record(doc))

    logger.info("Created runtime API key: name=%s owner=%s created_by=%s",
                doc.name, doc.owner, doc.created_by)

    # Optionally create namespace grants for the new key in the same call, so
    # a scoped key is usable (not read-only) immediately. Same semantics as
    # POST /namespaces/{prefix}/grants, with the canonical api_key subject
    # (the bare key name). Namespace existence is intentionally not enforced:
    # key namespace scoping isn't either, and app provisioning may create the
    # namespace after the key. A reserved namespace always gets admin.
    granted_namespaces: list[str] | None = None
    if (request.grant_permission or reserve) and request.namespaces:
        granted_namespaces = []
        for prefix in request.namespaces:
            permission: Literal["read", "write", "admin"]
            if prefix in reserved_prefixes:
                permission = "admin"
            elif request.grant_permission:
                permission = request.grant_permission
            else:
                continue
            await _set_grant(doc.name, prefix, permission)
            granted_namespaces.append(prefix)
        logger.info(
            "Granted on %s to api_key %s (grant_permission=%s, reserved=%s)",
            granted_namespaces, doc.name, request.grant_permission, reserved_prefixes,
        )

    for r in reserve:
        await reservation_store.save_reservation(
            r.prefix, r.settings, doc.name, get_identity_string()
        )
    if reserve:
        logger.info("Runtime key %s reserves %s", doc.name, reserved_prefixes)

    return APIKeyCreatedResponse(
        granted_namespaces=granted_namespaces,
        name=doc.name,
        owner=doc.owner,
        groups=doc.groups,
        description=doc.description,
        created_at=doc.created_at,
        expires_at=doc.expires_at,
        enabled=doc.enabled,
        namespaces=doc.namespaces,
        created_by=doc.created_by,
        source="runtime",
        plaintext_key=plaintext,
        reserved_namespaces=sorted(reserved_prefixes),
    )


@router.get(
    "",
    response_model=APIKeyListResponse,
    summary="List all API keys",
)
async def list_api_keys(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    _admin: str = Depends(require_admin_key),
) -> APIKeyListResponse:
    """List all API keys (config + runtime) with pagination.

    Pagination follows the platform-wide convention — see `wip://conventions`.
    Results are sorted by name (deterministic) and sliced server-side.
    No hashes returned.
    """
    provider = _get_provider()
    results: list[APIKeyResponse] = []
    config_names = _config_names()

    # Runtime keys from MongoDB (source of truth for metadata)
    runtime_docs = await StoredAPIKey.find_all().to_list()
    managed = {d.name: d for d in runtime_docs if d.managed_by is not None}

    # Config-file keys, marked managed when the Registry holds their import
    for record in provider.iter_keys():
        if record.name in config_names:
            results.append(_config_key_to_response(record, managed.get(record.name)))

    reserved: dict[str, list[str]] = {}
    for r in await RuntimeReservation.find(RuntimeReservation.managed_by == None).to_list():  # noqa: E711
        reserved.setdefault(r.app, []).append(r.prefix)
    declared = await _declared_grants(runtime_docs)
    for doc in runtime_docs:
        # A dormant import is the config key listed above: list it once.
        if key_import.is_dormant(doc, config_names):
            continue
        results.append(_stored_to_response(doc, reserved.get(doc.name), declared.get(doc.name)))

    results.sort(key=lambda r: r.name)
    total = len(results)
    start = (page - 1) * page_size
    end = start + page_size
    return APIKeyListResponse(
        items=results[start:end],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get(
    "/sync",
    response_model=list[APIKeySyncRecord],
    summary="Sync endpoint for service key polling",
)
async def sync_api_keys(
    identity: UserIdentity = Depends(require_groups(["wip-services", "wip-admins"])),
) -> list[APIKeySyncRecord]:
    """Return enabled runtime keys with hashes for service polling.

    Only returns runtime keys — config keys are already loaded by each service.
    A runtime key with the same plaintext as a config key is shadowed and
    left out, so every service resolves that plaintext as the config key;
    the key listing names the config key that shadows it.
    Requires the wip-services or wip-admins group — enforced by the
    require_groups dependency above. This endpoint returns
    key_hash values, so it must never be reachable by an unprivileged
    key; the legitimate consumer is each service's key-sync poller, which
    authenticates with its admin/service-scoped Registry key.
    """
    docs = await StoredAPIKey.find(StoredAPIKey.enabled == True).to_list()  # noqa: E712
    # A managed key the Registry also loads as a config key of the same name
    # is dormant: every service already holds that plaintext as the config key.
    config_names = _config_names()
    return [
        APIKeySyncRecord(
            name=doc.name,
            key_hash=doc.key_hash,
            owner=doc.owner,
            groups=doc.groups,
            description=doc.description,
            created_at=doc.created_at,
            expires_at=doc.expires_at,
            enabled=doc.enabled,
            namespaces=doc.namespaces,
        )
        for doc in docs
        if not key_shadowing.is_shadowed(doc.name)
        and not key_import.is_dormant(doc, config_names)
    ]


@router.get(
    "/{name}",
    response_model=APIKeyResponse,
    summary="Get a single API key by name",
)
async def get_api_key(
    name: str,
    _admin: str = Depends(require_admin_key),
) -> APIKeyResponse:
    """Get metadata for a single API key (config or runtime)."""
    provider = _get_provider()

    # Check config keys first
    if _is_config_key(name):
        for record in provider.iter_keys():
            if record.name == name:
                return _config_key_to_response(record, await _managed_doc(name))

    # Check runtime keys
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if doc:
        declared = await _declared_grants([doc])
        return _stored_to_response(doc, await _reserved_by(name), declared.get(name))

    raise HTTPException(status_code=404, detail=f"API key '{name}' not found")


@router.patch(
    "/{name}",
    response_model=APIKeyResponse,
    summary="Update a runtime API key",
)
async def update_api_key(
    name: str,
    request: APIKeyUpdateRequest,
    _admin: str = Depends(require_admin_key),
) -> APIKeyResponse:
    """Update metadata on a runtime API key. Config-file keys cannot be modified."""
    await _refuse_if_managed(name)
    if _is_config_key(name):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot modify config-file key '{name}' via API",
        )

    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Runtime key '{name}' not found")

    reserved = await _reserved_by(name)
    if request.namespaces is not None:
        dropped = sorted(set(reserved) - set(request.namespaces))
        if dropped:
            raise HTTPException(
                status_code=409,
                detail=f"namespaces would drop {', '.join(dropped)}, which the key "
                f"reserves; narrow with DELETE /api-keys/{name}/namespaces/<prefix>, "
                "which also handles the reservation and the grant",
            )

    # Apply updates
    if request.description is not None:
        doc.description = request.description
    if request.groups is not None:
        doc.groups = request.groups
    if request.namespaces is not None:
        doc.namespaces = request.namespaces
    if request.expires_at is not None:
        doc.expires_at = request.expires_at
    if request.enabled is not None:
        doc.enabled = request.enabled

    await doc.save()
    await _refresh_provider(doc)

    logger.info("Updated runtime API key: name=%s by=%s", name, get_identity_string())

    return _stored_to_response(doc, reserved)


async def _runtime_key(name: str) -> StoredAPIKey:
    await _refuse_if_managed(name)
    if _is_config_key(name):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot modify config-file key '{name}' via API; wip-deploy manages it",
        )
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Runtime key '{name}' not found")
    return doc


@router.put(
    "/{name}/namespaces/{prefix}",
    response_model=APIKeyResponse,
    summary="Widen a runtime API key to a namespace",
)
async def add_key_namespace(
    name: str,
    prefix: str,
    request: NamespaceAddRequest,
    _admin: str = Depends(require_admin_key),
) -> APIKeyResponse:
    """Scope a runtime key to one more namespace, with a grant on it.

    With `reserve`, the key also reserves the namespace with those settings
    and gets `admin` on it, so it may create it. Idempotent: repeating the
    call changes nothing, and re-reserving with other settings replaces
    them (an existing namespace is never retuned by a reservation).
    """
    doc = await _runtime_key(name)
    if doc.namespaces is None:
        raise HTTPException(
            status_code=400,
            detail=f"Runtime key '{name}' is not namespace-scoped; there is no scope to widen",
        )
    permission = request.permission or ("admin" if request.reserve is not None else "read")
    if request.reserve is not None:
        if permission != "admin":
            raise HTTPException(
                status_code=422,
                detail="a key reserving a namespace holds admin on it; omit permission or send 'admin'",
            )
        await _check_reservation(prefix, request.reserve, name)

    if prefix not in doc.namespaces:
        doc.namespaces = [*doc.namespaces, prefix]
        await doc.save()
        await _refresh_provider(doc)
    await _set_grant(name, prefix, permission)
    if request.reserve is not None:
        await reservation_store.save_reservation(
            prefix, request.reserve, name, get_identity_string()
        )
    logger.info(
        "Widened runtime key %s to %s (permission=%s, reserved=%s) by=%s",
        name, prefix, permission, request.reserve is not None, get_identity_string(),
    )
    return _stored_to_response(doc, await _reserved_by(name))


@router.delete(
    "/{name}/namespaces/{prefix}",
    summary="Narrow a runtime API key by one namespace",
)
async def remove_key_namespace(
    name: str,
    prefix: str,
    _admin: str = Depends(require_admin_key),
) -> dict:
    """Take a namespace out of a runtime key's scope and revoke its grant
    there. A reservation of the namespace is released when the namespace
    was never created; otherwise it is kept (`reservation: "kept"`) for an
    operator to release with DELETE /reservations/{prefix}. The namespace
    itself is never touched."""
    doc = await _runtime_key(name)
    if not doc.namespaces or prefix not in doc.namespaces:
        raise HTTPException(
            status_code=404, detail=f"Runtime key '{name}' is not scoped to '{prefix}'"
        )
    doc.namespaces = [n for n in doc.namespaces if n != prefix]
    await doc.save()
    await _refresh_provider(doc)
    await NamespaceGrant.find(_grant_query(name, prefix)).delete()
    reservation = None
    held = await RuntimeReservation.find_one(
        RuntimeReservation.prefix == prefix
    )
    if held is not None and held.app == name:
        reservation = await reservation_store.release(prefix)
    logger.info(
        "Narrowed runtime key %s by %s (reservation=%s) by=%s",
        name, prefix, reservation, get_identity_string(),
    )
    return {
        "name": name,
        "prefix": prefix,
        "namespaces": doc.namespaces,
        "reservation": reservation,
    }


@router.delete(
    "/{name}",
    summary="Revoke (delete) a runtime API key",
)
async def delete_api_key(
    name: str,
    _admin: str = Depends(require_admin_key),
) -> dict:
    """Hard-delete a runtime API key and its namespace grants.

    Config-file keys cannot be deleted. The response lists the namespaces
    whose grants to the key were revoked with it (`revoked_grants`). The
    key's namespace reservations are released where the namespace was never
    created (`released_reservations`) and kept where it exists
    (`kept_reservations`; release them with DELETE /reservations/{prefix}).
    """
    await _refuse_if_managed(name)
    if _is_config_key(name):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete config-file key '{name}' via API",
        )

    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Runtime key '{name}' not found")

    provider = _get_provider()

    # Remove from provider first, then from DB
    async with key_shadowing.provider_lock:
        provider.remove_key(doc.key_hash)
        await doc.delete()

    # The key's namespace grants go with it. Grants are keyed by the key's
    # name, so a grant left behind would silently pass to the next key
    # created under the same name. Both subject spellings are matched: the
    # bare name (canonical) and the "apikey:<name>" user_id form.
    grant_query = _grant_query(name)
    grants = await NamespaceGrant.find(grant_query).to_list()
    if grants:
        await NamespaceGrant.find(grant_query).delete()
    revoked_grants = sorted({g.namespace for g in grants})

    # Its reservations: released when the namespace was never created, kept
    # (and reported) when it exists, so an operator decides. The namespace
    # itself is never deleted as a side effect.
    released: list[str] = []
    kept: list[str] = []
    for r in await reservation_store.reservations_of(name):
        outcome = await reservation_store.release(r.prefix)
        (released if outcome == "released" else kept).append(r.prefix)

    logger.info(
        "Revoked runtime API key: name=%s by=%s grants_revoked=%s "
        "reservations_released=%s reservations_kept=%s",
        name, get_identity_string(), revoked_grants, released, kept,
    )

    return {
        "status": "deleted",
        "name": name,
        "revoked_grants": revoked_grants,
        "released_reservations": sorted(released),
        "kept_reservations": sorted(kept),
    }
