"""API key management endpoints.

Runtime API keys are stored in MongoDB and managed here. Config-file keys
(wip-admins, wip-services) are read-only and cannot be modified via these endpoints.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from wip_auth import (
    APIKeyProvider,
    APIKeyRecord,
    get_auth_config,
    get_identity_string,
    hash_api_key,
)

from ..models.api_key import (
    APIKeyCreateRequest,
    APIKeyCreatedResponse,
    APIKeyResponse,
    APIKeySyncRecord,
    APIKeyUpdateRequest,
    StoredAPIKey,
    generate_plaintext_key,
)
from ..services.auth import require_admin_key, require_api_key

logger = logging.getLogger("registry.api_keys")

router = APIRouter()

# Module-level reference to the APIKeyProvider, set during startup
_api_key_provider: APIKeyProvider | None = None
_config_key_names: set[str] = set()


def configure_api_key_management(
    provider: APIKeyProvider, config_key_names: set[str]
) -> None:
    """Called during startup to wire the provider and config key names."""
    global _api_key_provider, _config_key_names
    _api_key_provider = provider
    _config_key_names = config_key_names


def _get_provider() -> APIKeyProvider:
    if _api_key_provider is None:
        raise HTTPException(
            status_code=503, detail="API key management not initialized"
        )
    return _api_key_provider


def _stored_to_response(doc: StoredAPIKey) -> APIKeyResponse:
    """Convert a StoredAPIKey document to an APIKeyResponse."""
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
    )


def _config_key_to_response(record: APIKeyRecord) -> APIKeyResponse:
    """Convert a config-file APIKeyRecord to an APIKeyResponse."""
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
    """Create a new runtime API key. The plaintext is returned once and never stored."""
    provider = _get_provider()

    # Reject name collision with config-file keys
    if request.name in _config_key_names:
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

    # Add to local provider so it works immediately
    provider.add_key(APIKeyRecord(
        name=doc.name,
        key_hash=doc.key_hash,
        owner=doc.owner,
        groups=doc.groups,
        description=doc.description,
        created_at=doc.created_at,
        expires_at=doc.expires_at,
        enabled=doc.enabled,
        namespaces=doc.namespaces,
    ))

    logger.info("Created runtime API key: name=%s owner=%s created_by=%s",
                doc.name, doc.owner, doc.created_by)

    return APIKeyCreatedResponse(
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
    )


@router.get(
    "",
    response_model=list[APIKeyResponse],
    summary="List all API keys",
)
async def list_api_keys(
    _admin: str = Depends(require_admin_key),
) -> list[APIKeyResponse]:
    """List all API keys (config + runtime). No hashes returned."""
    provider = _get_provider()
    results: list[APIKeyResponse] = []

    # Config-file keys
    for record in provider._keys:
        if record.name in _config_key_names:
            results.append(_config_key_to_response(record))

    # Runtime keys from MongoDB (source of truth for metadata)
    runtime_docs = await StoredAPIKey.find_all().to_list()
    for doc in runtime_docs:
        results.append(_stored_to_response(doc))

    return results


@router.get(
    "/sync",
    response_model=list[APIKeySyncRecord],
    summary="Sync endpoint for service key polling",
)
async def sync_api_keys(
    _key: str = Depends(require_api_key),
) -> list[APIKeySyncRecord]:
    """Return enabled runtime keys with hashes for service polling.

    Only returns runtime keys — config keys are already loaded by each service.
    Requires wip-services or wip-admins group (enforced by require_api_key +
    the caller must be a service key).
    """
    docs = await StoredAPIKey.find(StoredAPIKey.enabled == True).to_list()  # noqa: E712
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
    if name in _config_key_names:
        for record in provider._keys:
            if record.name == name:
                return _config_key_to_response(record)

    # Check runtime keys
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if doc:
        return _stored_to_response(doc)

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
    if name in _config_key_names:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot modify config-file key '{name}' via API",
        )

    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Runtime key '{name}' not found")

    provider = _get_provider()

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

    # Update the in-memory provider: remove old, add updated
    provider.remove_key(doc.key_hash)
    provider.add_key(APIKeyRecord(
        name=doc.name,
        key_hash=doc.key_hash,
        owner=doc.owner,
        groups=doc.groups,
        description=doc.description,
        created_at=doc.created_at,
        expires_at=doc.expires_at,
        enabled=doc.enabled,
        namespaces=doc.namespaces,
    ))

    logger.info("Updated runtime API key: name=%s by=%s", name, get_identity_string())

    return _stored_to_response(doc)


@router.delete(
    "/{name}",
    summary="Revoke (delete) a runtime API key",
)
async def delete_api_key(
    name: str,
    _admin: str = Depends(require_admin_key),
) -> dict:
    """Hard-delete a runtime API key. Config-file keys cannot be deleted."""
    if name in _config_key_names:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete config-file key '{name}' via API",
        )

    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Runtime key '{name}' not found")

    provider = _get_provider()

    # Remove from provider first, then from DB
    provider.remove_key(doc.key_hash)
    await doc.delete()

    logger.info("Revoked runtime API key: name=%s by=%s", name, get_identity_string())

    return {"status": "deleted", "name": name}
