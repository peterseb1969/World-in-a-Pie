"""Configuration for the auth gateway, loaded from environment variables."""

import logging
import os
import sys

logger = logging.getLogger("auth_gateway.config")

# The insecure fallback for SESSION_SECRET. Publicly visible in this repo,
# so any deployment still running it has forgeable session cookies — and a
# forged session yields X-WIP-User / X-WIP-Groups / X-API-Key injection at
# the gateway. wip-deploy generates a random gateway-session-secret for
# every install; this default only survives hand-rolled deployments.
DEFAULT_SESSION_SECRET = "change-me-in-production"


def check_production_security(session_secret: str | None = None) -> None:
    """Refuse to start in production mode with the default session secret.

    Local mirror of wip_auth.security.check_production_security — a local
    copy, not an import, because auth-gateway deliberately carries no
    wip-auth dependency (it is the one backend service kept free of it).
    Same trigger contract as the other five services' guards:
    WIP_VARIANT=prod arms the check, anything else returns early.
    """
    variant = os.getenv("WIP_VARIANT", "dev")
    if variant != "prod":
        return

    secret = session_secret if session_secret is not None else settings.session_secret
    if secret == DEFAULT_SESSION_SECRET:
        logger.critical(
            "SECURITY: Default session secret detected in production mode! "
            "'change-me-in-production' is publicly documented — session "
            "cookies signed with it can be forged, yielding full identity "
            "spoofing at the gateway. wip-deploy install generates a random "
            "gateway-session-secret — redeploy with it, or set SESSION_SECRET "
            "in the install's secret backend (~/.wip-deploy/<name>/secrets/). "
            "Refusing to start."
        )
        sys.exit(1)


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

    # Unauthenticated /auth/verify behaviour — who owns the browser's
    # login redirect. Which value is correct is a property of the
    # reverse-proxy family in front of the gateway, not of the request:
    #
    #   "401" (default): every unauthenticated request gets 401 +
    #   X-Auth-Redirect, and the proxy turns that into the login flow
    #   (nginx-ingress `auth-signin`, Caddy `handle_response @401`).
    #   MANDATORY behind nginx's auth_request, which treats any status
    #   other than 2xx/401/403 from the auth subrequest — a 302
    #   included — as an internal error and answers the browser 500.
    #
    #   "redirect": browser requests (Accept: text/html) get a 302 to
    #   the absolute public login URL; API callers still get the 401 +
    #   X-Auth-Redirect contract. For proxies like Traefik whose
    #   forwardAuth passes non-2xx auth responses to the client
    #   verbatim and has no mechanism of its own to start a login flow.
    #
    # Sniffing the proxy family from request headers instead of
    # configuring it is not possible: every proxy forwards the
    # browser's Accept header to the auth subrequest.
    auth_redirect_mode: str = os.getenv("AUTH_REDIRECT_MODE", "401")


def check_redirect_mode(mode: str | None = None) -> None:
    """Refuse to start on an invalid AUTH_REDIRECT_MODE.

    A typo silently treated as the default would put a Traefik
    deployment back on bare 401s — browsers see an Unauthorized page
    and the misconfiguration surfaces as a UX bug far from its cause.
    Fail loudly at startup instead.
    """
    value = mode if mode is not None else settings.auth_redirect_mode
    if value not in ("401", "redirect"):
        logger.critical(
            "Invalid AUTH_REDIRECT_MODE %r — must be '401' (proxy owns the "
            "login redirect; nginx-ingress/Caddy) or 'redirect' (gateway "
            "302s browsers to the login URL; Traefik-style forwardAuth). "
            "Refusing to start.",
            value,
        )
        sys.exit(1)


settings = Settings()
