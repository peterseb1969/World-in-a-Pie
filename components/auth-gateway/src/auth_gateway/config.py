"""Configuration for the auth gateway, loaded from environment variables."""

import os


class Settings:
    """Auth gateway settings from env vars."""

    # OIDC — external issuer is what the browser sees (and what Dex puts in tokens).
    # Internal issuer is the container-network URL for server-side discovery/token exchange.
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "https://localhost:8443/dex")
    oidc_internal_issuer: str = os.getenv(
        "OIDC_INTERNAL_ISSUER", oidc_issuer
    )
    oidc_client_id: str = os.getenv("OIDC_CLIENT_ID", "wip-gateway")
    oidc_client_secret: str = os.getenv("OIDC_CLIENT_SECRET", "")
    callback_url: str = os.getenv(
        "CALLBACK_URL", "https://localhost:8443/auth/callback"
    )

    # Session — signed cookie, no external storage.
    session_secret: str = os.getenv("SESSION_SECRET", "change-me-in-production")
    session_max_age: int = int(os.getenv("SESSION_MAX_AGE", "86400"))  # 24 hours

    # WIP API key — injected into X-API-Key header so TrustedHeaderProvider
    # on backend services accepts the gateway's identity headers.
    api_key: str = os.getenv("API_KEY", "")

    # Hostname for constructing external URLs.
    wip_hostname: str = os.getenv("WIP_HOSTNAME", "localhost")

    # Default redirect after login if no return_to was provided.
    default_redirect: str = os.getenv("DEFAULT_REDIRECT", "/apps/rc/")

    # Scopes to request from Dex.
    oidc_scopes: str = os.getenv("OIDC_SCOPES", "openid email profile groups offline_access")


settings = Settings()
