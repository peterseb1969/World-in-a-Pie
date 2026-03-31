"""WIP Authentication Library.

A pluggable authentication library for World In a Pie (WIP) services.
Supports multiple authentication modes and providers.

Quick Start:
    from wip_auth import setup_auth, require_identity
    from fastapi import FastAPI, Depends

    app = FastAPI()

    # Setup auth (reads from environment)
    setup_auth(app)

    @app.get("/protected")
    async def protected(identity = Depends(require_identity())):
        return {"user": identity.username}

Configuration via environment variables:
    WIP_AUTH_MODE=api_key_only      # or: none, jwt_only, dual
    WIP_AUTH_LEGACY_API_KEY=xxx     # backward compatible API key
    WIP_AUTH_JWT_ISSUER_URL=xxx     # OIDC issuer (if using JWT)

For more details, see the README.md.
"""

from .config import (
    AuthConfig,
    get_auth_config,
    reset_auth_config,
    set_auth_config,
)
from .dependencies import (
    optional_identity,
    require_admin,
    require_api_key,
    require_groups,
    require_identity,
    require_namespace_admin,
    require_namespace_read,
    require_namespace_write,
)
from .identity import (
    clear_current_identity,
    get_actor_info,
    get_current_identity,
    get_identity_owner,
    get_identity_string,
    set_current_identity,
)
from .middleware import AuthMiddleware, create_auth_middleware
from .models import APIKeyRecord, AuthResult, UserIdentity
from .permissions import (
    check_namespace_permission,
    clear_permission_cache,
    permission_sufficient,
    resolve_accessible_namespaces,
    resolve_permission,
)
from .providers import (
    APIKeyProvider,
    AuthProvider,
    NoAuthProvider,
    OIDCProvider,
    TrustedHeaderProvider,
    hash_api_key,
)
from .query_validation import RejectUnknownQueryParamsMiddleware
from .ratelimit import setup_rate_limiting
from .resolve import (
    EntityNotFoundError,
    clear_resolution_cache,
    is_canonical_format,
    resolve_entity_id,
    resolve_entity_ids,
)
from .security import check_production_security

__version__ = "0.4.0"

__all__ = [
    "APIKeyProvider",
    "APIKeyRecord",
    # Config
    "AuthConfig",
    # Middleware
    "AuthMiddleware",
    # Providers
    "AuthProvider",
    "AuthResult",
    "EntityNotFoundError",
    "NoAuthProvider",
    "OIDCProvider",
    "RejectUnknownQueryParamsMiddleware",
    "TrustedHeaderProvider",
    # Models
    "UserIdentity",
    # Permissions
    "check_namespace_permission",
    # Security
    "check_production_security",
    "clear_current_identity",
    "clear_permission_cache",
    "clear_resolution_cache",
    "create_auth_middleware",
    "create_providers_from_config",
    "get_actor_info",
    "get_auth_config",
    # Identity context
    "get_current_identity",
    "get_identity_owner",
    "get_identity_string",
    "hash_api_key",
    "is_canonical_format",
    "optional_identity",
    "permission_sufficient",
    "require_admin",
    "require_api_key",
    "require_groups",
    # Dependencies
    "require_identity",
    "require_namespace_admin",
    "require_namespace_read",
    "require_namespace_write",
    "reset_auth_config",
    "resolve_accessible_namespaces",
    # Synonym resolution
    "resolve_entity_id",
    "resolve_entity_ids",
    "resolve_permission",
    "set_auth_config",
    "set_current_identity",
    # Setup
    "setup_auth",
    # Rate limiting
    "setup_rate_limiting",
]


def create_providers_from_config(
    config: AuthConfig | None = None,
) -> list[AuthProvider]:
    """Create auth providers based on configuration.

    This factory function creates the appropriate providers based on
    the auth mode in the configuration.

    Args:
        config: Auth configuration (uses get_auth_config() if None)

    Returns:
        List of configured auth providers
    """
    if config is None:
        config = get_auth_config()

    providers: list[AuthProvider] = []

    if config.mode == "none":
        # No auth mode - use pass-through provider
        providers.append(NoAuthProvider(default_groups=config.default_groups))

    elif config.mode == "api_key_only":
        # API key only - load keys and create provider
        keys = config.load_api_keys()

        # Trusted header provider runs first (checks X-WIP-User + API key)
        if config.trust_proxy_headers and keys:
            providers.append(TrustedHeaderProvider(
                keys=keys,
                header_name=config.api_key_header,
                hash_salt=config.api_key_hash_salt,
                default_groups=config.default_groups,
            ))

        providers.append(APIKeyProvider(
            keys=keys,
            header_name=config.api_key_header,
            hash_salt=config.api_key_hash_salt,
            default_groups=config.default_groups,
        ))

    elif config.mode == "jwt_only":
        # JWT only - create OIDC provider
        if not config.jwt_issuer_url and not config.jwt_jwks_uri:
            raise ValueError(
                "JWT mode requires WIP_AUTH_JWT_ISSUER_URL or WIP_AUTH_JWT_JWKS_URI"
            )
        providers.append(OIDCProvider(
            issuer_url=config.jwt_issuer_url,
            jwks_url=config.jwt_jwks_uri,
            audience=config.jwt_audience,
            algorithms=config.jwt_algorithms,
            groups_claim=config.jwt_groups_claim,
            default_groups=config.default_groups,
            verify_exp=config.jwt_verify_exp,
            leeway=config.jwt_leeway_seconds,
        ))

    elif config.mode == "dual":
        # Dual mode - try trusted headers first, then JWT, then API key
        keys = config.load_api_keys()

        # Trusted header provider runs first (checks X-WIP-User + API key)
        if config.trust_proxy_headers and keys:
            providers.append(TrustedHeaderProvider(
                keys=keys,
                header_name=config.api_key_header,
                hash_salt=config.api_key_hash_salt,
                default_groups=config.default_groups,
            ))

        # JWT is checked next because Bearer token is more explicit
        if config.jwt_issuer_url or config.jwt_jwks_uri:
            providers.append(OIDCProvider(
                issuer_url=config.jwt_issuer_url,
                jwks_url=config.jwt_jwks_uri,
                audience=config.jwt_audience,
                algorithms=config.jwt_algorithms,
                groups_claim=config.jwt_groups_claim,
                default_groups=config.default_groups,
                verify_exp=config.jwt_verify_exp,
                leeway=config.jwt_leeway_seconds,
            ))

        if keys:
            providers.append(APIKeyProvider(
                keys=keys,
                header_name=config.api_key_header,
                hash_salt=config.api_key_hash_salt,
                default_groups=config.default_groups,
            ))

    return providers


def setup_auth(app, config: AuthConfig | None = None) -> list[AuthProvider]:
    """Setup authentication for a FastAPI application.

    This is the main entry point for configuring auth. It:
    1. Loads configuration from environment (or uses provided config)
    2. Creates appropriate providers based on auth mode
    3. Adds the auth middleware to the application

    Args:
        app: The FastAPI application
        config: Optional auth configuration (loads from env if None)

    Returns:
        List of configured providers (for testing/inspection)

    Example:
        from fastapi import FastAPI
        from wip_auth import setup_auth

        app = FastAPI()
        setup_auth(app)  # Reads from WIP_AUTH_* env vars
    """
    if config is None:
        config = get_auth_config()
    else:
        set_auth_config(config)

    providers = create_providers_from_config(config)

    if providers:
        middleware_class = create_auth_middleware(providers)
        app.add_middleware(middleware_class)

    return providers
