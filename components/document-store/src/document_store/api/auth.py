"""Authentication for the Document Store API.

This module provides authentication using the wip-auth shared library.
It re-exports the common auth functions for backward compatibility.
"""

from fastapi import HTTPException

from wip_auth import (
    AuthConfig,
    UserIdentity,
    check_namespace_permission,
    get_actor_info,
    get_auth_config,
    get_identity_owner,
    get_identity_string,
    optional_identity,
    require_admin,
    require_api_key,
    require_groups,
    require_identity,
    reset_auth_config,
    set_auth_config,
)

# Re-export for backward compatibility
__all__ = [
    "AuthConfig",
    "UserIdentity",
    "check_readable",
    "get_actor_info",
    "get_auth_config",
    "get_identity_owner",
    "get_identity_string",
    "optional_identity",
    "require_admin",
    "require_api_key",
    "require_groups",
    "require_identity",
    "reset_auth_config",
    "set_api_key",
    "set_auth_config",
    "write_refusals",
]


def set_api_key(key: str) -> None:
    """Set the API key (for testing).

    This is a backward compatibility function. It configures
    the auth system to use a specific API key.
    """
    config = AuthConfig(
        mode="api_key_only",
        legacy_api_key=key,
    )
    set_auth_config(config)


async def check_readable(identity: UserIdentity, namespace: str, not_found: str) -> None:
    """Read on the namespace of an entity the caller asked for by id.

    An entity in a namespace the caller cannot see answers exactly as an id
    that names nothing (``not_found``), not "Namespace not found": the
    different text would confirm that the id exists, and where. Only the
    detail changes; the status was already 404. A namespace the caller names
    itself is checked with check_namespace_permission as before.
    """
    try:
        await check_namespace_permission(identity, namespace, "read")
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=not_found) from None
        raise


async def write_refusals(
    identity: UserIdentity, namespaces: set[str], not_found: str
) -> dict[str, tuple[str, str]]:
    """Per namespace, why ``identity`` may not write there, as (error,
    error_code); a namespace absent from the result may be written.

    For bulk writes: asked once per namespace and before any item of the batch
    is written, so a refusal is reported on its items while the rest proceed,
    and an authorization outage (503) refuses the whole request before any
    item has changed. A namespace the caller cannot see answers ``not_found``
    with the text of an id that names nothing, as check_readable does, so the
    response does not confirm that the entity exists, or where.
    """
    refusals: dict[str, tuple[str, str]] = {}
    for ns in sorted(namespaces):
        try:
            await check_namespace_permission(identity, ns, "write")
        except HTTPException as exc:
            if exc.status_code == 403:
                refusals[ns] = (str(exc.detail), "forbidden")
            elif exc.status_code == 404:
                refusals[ns] = (not_found, "not_found")
            else:
                raise
    return refusals
