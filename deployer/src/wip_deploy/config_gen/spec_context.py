"""SpecContext — computed values derived from a Deployment spec.

`from_spec: <dotted-path>` in a manifest resolves to an attribute on this
context. Centralizing the computation here means one place changes if, for
example, the issuer URL format evolves.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.component import Component


@dataclass(frozen=True)
class SpecContextNetwork:
    hostname: str
    cors_origins: str
    internal_base_url: str  # URL the gateway/apps use to reach Caddy internally


@dataclass(frozen=True)
class SpecContextAuth:
    issuer_url_public: str  # URL browsers see
    issuer_url_internal: str  # URL the gateway uses server-to-server
    callback_url: str


@dataclass(frozen=True)
class SpecContextFeatures:
    files_enabled: str  # "true"/"false" — string for direct env injection


@dataclass(frozen=True)
class SpecContext:
    """All spec-derived computed values. Flat section-nested layout
    matches the dotted paths used in manifests (`auth.issuer_url_public`,
    `network.cors_origins`, etc.)."""

    network: SpecContextNetwork
    auth: SpecContextAuth
    features: SpecContextFeatures


# ────────────────────────────────────────────────────────────────────


def make_spec_context(
    deployment: Deployment, components: list[Component]
) -> SpecContext:
    """Compute all derived values for a Deployment."""
    net = _compute_network(deployment)
    auth = _compute_auth(deployment)
    features = _compute_features(deployment, components)
    return SpecContext(network=net, auth=auth, features=features)


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

    return SpecContextNetwork(
        hostname=net.hostname,
        cors_origins=cors,
        internal_base_url=internal_base,
    )


def _compute_auth(deployment: Deployment) -> SpecContextAuth:
    public_base = _public_base(deployment)
    return SpecContextAuth(
        issuer_url_public=f"{public_base}/dex",
        # Internal Dex is on port 5556 inside the network, in the Dex
        # component; path prefix is /dex because Dex expects it.
        issuer_url_internal="http://wip-dex:5556/dex",
        callback_url=f"{public_base}/auth/callback",
    )


def _compute_features(
    deployment: Deployment, components: list[Component]
) -> SpecContextFeatures:
    # File storage is "on" iff minio is active in this deployment.
    minio = next(
        (c for c in components if c.metadata.name == "minio"), None
    )
    files_on = minio is not None and is_component_active(minio, deployment)
    return SpecContextFeatures(files_enabled="true" if files_on else "false")
