"""SpecContext — computed values derived from a Deployment spec.

`from_spec: <dotted-path>` in a manifest resolves to an attribute on this
context. Centralizing the computation here means one place changes if, for
example, the issuer URL format evolves.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


@dataclass(frozen=True)
class SpecContextNetwork:
    hostname: str
    cors_origins: str
    internal_base_url: str  # URL the gateway/apps use to reach Caddy internally
    # CASE-358: external base URL of the WIP this deployment talks to.
    # - When network.remote_wip_url is set → that URL verbatim (cross-host
    #   case, e.g., Console-on-Mac wanting Pi's URL).
    # - Otherwise → this install's own _public_base
    #   (`https://<hostname>:<https_port>`, with default-port stripping).
    # Apps reach this via `from_spec: network.external_base_url`.
    external_base_url: str


@dataclass(frozen=True)
class SpecContextAuth:
    # URL browsers see. Dex's own `issuer:` is rendered from the same
    # `_public_base`, so it is also the `iss` of every token Dex mints and
    # the value services must compare it against (WIP_AUTH_JWT_ISSUER_URL).
    issuer_url_public: str
    issuer_url_internal: str  # URL the gateway uses server-to-server
    # Where services fetch Dex's signing keys (WIP_AUTH_JWT_JWKS_URI). Dex
    # serves them at <issuer>/keys, not at the .well-known/jwks.json path
    # wip-auth would otherwise derive, and services reach Dex internally.
    jwks_url_internal: str
    # Accepted token audiences (WIP_AUTH_JWT_AUDIENCE), comma-separated:
    # Dex sets `aud` to the requesting client, so every Dex client this
    # deployment registers (the gateway's and each app's own) is listed.
    jwt_audience: str
    callback_url: str
    # WIP_AUTH_MODE for backend services using the wip-auth library.
    # Maps deployment.spec.auth.mode → wip-auth's runtime mode:
    #   "api-key-only" → "api_key_only"
    #   "oidc"         → "jwt_only"
    #   "hybrid"       → "dual"
    wip_auth_mode: str
    # The group policy for logins federated through a Dex connector:
    # reserved groups are stripped and auth.group_mappings applied. JSON;
    # empty when no connector is declared, which leaves every token's groups
    # exactly as issued. Rendered as a file (security.federated_groups_file)
    # that wip-auth and the auth-gateway re-read while running.
    federated_groups: str


@dataclass(frozen=True)
class SpecContextFeatures:
    files_enabled: str  # "true"/"false" — string for direct env injection
    # Browser-reachable object-store URL. document-store rewrites presigned URLs
    # from the internal endpoint (http://wip-garage:3900) to this one
    # before returning them to clients. Routed through Caddy/Ingress with
    # prefix stripping — shared origin with the rest of the API, one cert,
    # no mixed-content issues.
    file_storage_public_endpoint: str


@dataclass(frozen=True)
class SpecContextSecurity:
    # "dev" | "prod" — the deployment's declared security posture,
    # injected as WIP_VARIANT so the services' production-safety guards
    # (refuse-to-start on known-default secrets) arm exactly when the
    # operator asserted prod. String for direct env injection.
    variant: str
    # Container path of the rendered config-file API keys, injected as
    # WIP_AUTH_API_KEYS_FILE. Every install has the file: the canary key
    # is always in it.
    api_keys_file: str
    # Container path of the rendered federated group policy, injected as
    # WIP_AUTH_FEDERATED_GROUPS_FILE. Every install has the file (empty
    # without connectors), so a mapping change never alters a definition.
    federated_groups_file: str


@dataclass(frozen=True)
class SpecContext:
    """All spec-derived computed values. Flat section-nested layout
    matches the dotted paths used in manifests (`auth.issuer_url_public`,
    `network.cors_origins`, etc.)."""

    network: SpecContextNetwork
    auth: SpecContextAuth
    features: SpecContextFeatures
    security: SpecContextSecurity


# ────────────────────────────────────────────────────────────────────


def make_spec_context(
    deployment: Deployment,
    components: list[Component],
    apps: list[App] | None = None,
) -> SpecContext:
    """Compute all derived values for a Deployment.

    `apps` feeds the accepted token audiences (an app may register its own
    Dex client); the renderers always pass it. Omitting it means "no apps".
    """
    net = _compute_network(deployment)
    auth = _compute_auth(deployment, components, apps or [])
    features = _compute_features(deployment, components)
    from wip_deploy.config_gen.api_keys import (
        API_KEYS_CONTAINER_PATH,
        FEDERATED_GROUPS_CONTAINER_PATH,
    )

    security = SpecContextSecurity(
        variant=deployment.spec.variant,
        api_keys_file=API_KEYS_CONTAINER_PATH,
        federated_groups_file=FEDERATED_GROUPS_CONTAINER_PATH,
    )
    return SpecContext(network=net, auth=auth, features=features, security=security)


def resolve_from_spec(path: str, ctx: SpecContext) -> str:
    """Resolve a `from_spec: <dotted.path>` reference against a SpecContext."""
    parts = path.split(".")
    obj: object = ctx
    for part in parts:
        try:
            obj = getattr(obj, part)
        except AttributeError as e:
            raise KeyError(
                f"from_spec path {path!r} failed at {part!r}"
            ) from e
    if not isinstance(obj, str):
        raise TypeError(
            f"from_spec path {path!r} resolved to {type(obj).__name__}, expected str"
        )
    return obj


# ────────────────────────────────────────────────────────────────────


def _format_url(host: str, port: int, scheme: str = "https") -> str:
    """Build a URL, omitting the port when it's the scheme's default.

    Avoids emitting `:8443` when the browser expects the standard port
    (compose/dev default) vs `:443` for k8s (LoadBalancer Service).
    Browsers treat `https://host` and `https://host:443` as identical
    origins, but OIDC redirect_uris must match exactly — so the convention
    is to omit default ports.
    """
    defaults = {"https": 443, "http": 80}
    if port == defaults.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _public_base(deployment: Deployment) -> str:
    """Public base URL browsers hit. Uses `network.https_port` uniformly
    across all targets — defaults differ (443 for k8s, 8443 for
    compose/dev), and URL formatting strips the port when default."""
    net = deployment.spec.network
    return _format_url(net.hostname, net.https_port)


def _compute_network(deployment: Deployment) -> SpecContextNetwork:
    net = deployment.spec.network

    # CORS origins: external URL always allowed; on network installs
    # we also allow localhost so dev browsers hitting via 127.0.0.1
    # still work.
    external = _public_base(deployment)
    if net.hostname == "localhost":
        cors = external
    else:
        localhost_origin = _format_url("localhost", net.https_port)
        cors = f"{external},{localhost_origin}"

    # Internal base URL — apps proxying API calls server-side point
    # WIP_BASE_URL at the wip-router component. The concrete URL is
    # resolved via `from_component: wip-router` in each app's env, so
    # there's no target-specific string hardcoded here. This field
    # remains for back-compat with the few callers that still use
    # `from_spec: network.internal_base_url` — they should migrate.
    internal_base = "http://wip-router:8080"

    # CASE-358: external_base_url — what cross-host apps need.
    # When --remote-wip set the URL of a remote WIP install, that
    # wins. Otherwise this install's own public URL.
    external_base_url = net.remote_wip_url or external

    return SpecContextNetwork(
        hostname=net.hostname,
        cors_origins=cors,
        internal_base_url=internal_base,
        external_base_url=external_base_url,
    )


# auth.mode (deployment spec) → WIP_AUTH_MODE (wip-auth library env).
# wip-auth uses underscored mode names (`api_key_only`, `jwt_only`,
# `dual`); the deployment spec uses kebab-case (`api-key-only`, `oidc`,
# `hybrid`). The mapping is fixed at three pairs and lives here so any
# future mode-name change is in one place.
_AUTH_MODE_TO_WIP_AUTH_MODE: dict[str, str] = {
    "api-key-only": "api_key_only",
    "oidc": "jwt_only",
    "hybrid": "dual",
}


def _compute_auth(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> SpecContextAuth:
    from wip_deploy.config_gen.dex import generate_dex_config

    public_base = _public_base(deployment)
    auth_mode = deployment.spec.auth.mode
    # Internal Dex is on port 5556 inside the network, in the Dex
    # component; path prefix is /dex because Dex expects it.
    issuer_url_internal = "http://wip-dex:5556/dex"
    dex = generate_dex_config(deployment, components, apps)
    client_ids = [c.client_id for c in dex.clients] if dex is not None else []
    return SpecContextAuth(
        issuer_url_public=f"{public_base}/dex",
        issuer_url_internal=issuer_url_internal,
        jwks_url_internal=f"{issuer_url_internal}/keys",
        # Without Dex (api-key-only) services build no JWT provider, so the
        # value is never read; wip-auth's own default keeps it non-empty.
        jwt_audience=",".join(client_ids) or "wip",
        callback_url=f"{public_base}/auth/callback",
        wip_auth_mode=_AUTH_MODE_TO_WIP_AUTH_MODE[auth_mode],
        federated_groups=federated_groups_policy(deployment),
    )


def federated_groups_policy(deployment: Deployment) -> str:
    import json

    from wip_deploy.spec.deployment import LOCAL_CONNECTOR_ID, RESERVED_GROUPS

    auth = deployment.spec.auth
    if not auth.connectors:
        return ""
    mappings = [
        {"email": m.email, "group": m.group}
        if m.email is not None
        else {"email_domain": m.email_domain, "group": m.group}
        for m in auth.group_mappings
    ]
    return json.dumps(
        {
            "local_connectors": [LOCAL_CONNECTOR_ID],
            "reserved_groups": list(RESERVED_GROUPS),
            "mappings": mappings,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def active_storage_module(
    deployment: Deployment, components: list[Component]
) -> str | None:
    """The object-storage module document-store talks to: garage when
    active, else None. (MinIO was retired after the fleet migration;
    during the migration this function implemented the garage-wins-over-
    minio precedence.)
    """
    comp = next((c for c in components if c.metadata.name == "garage"), None)
    if comp is not None and is_component_active(comp, deployment):
        return "garage"
    return None


def _compute_features(
    deployment: Deployment, components: list[Component]
) -> SpecContextFeatures:
    # File storage is "on" iff the garage module is active. The public
    # presigned-URL prefix follows the module's route path.
    storage = active_storage_module(deployment, components)
    return SpecContextFeatures(
        files_enabled="true" if storage is not None else "false",
        file_storage_public_endpoint=(
            f"{_public_base(deployment)}/{storage or 'garage'}"
        ),
    )
