"""Caddy config generation (compose + dev targets).

Produces a structured `CaddyConfig` that the compose renderer serializes
to a Caddyfile. Routes consume `ResolvedRoute` from `routing.py` so the
compose and k8s paths never disagree about which route is auth-protected.

The Caddy renderer uses a `forward_auth` block for every auth-protected
route when `auth.gateway=True`. The gateway injects X-WIP-User /
X-WIP-Groups / X-API-Key headers before forwarding to the backend.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.routing import ResolvedRoute, resolve_routes
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


@dataclass(frozen=True)
class CaddyConfig:
    hostname: str
    https_port: int  # external HTTPS port Caddy binds to inside the container
    tls_mode: str  # "internal" | "letsencrypt" | "external"
    admin_email: str | None  # present for letsencrypt
    gateway_enabled: bool  # forward_auth blocks emitted iff True
    gateway_service: str  # DNS name of the auth-gateway container
    gateway_port: int
    routes: list[ResolvedRoute]
    has_dex: bool  # whether to emit /dex/* proxy
    dex_service: str  # DNS name of dex


def generate_caddy_config(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> CaddyConfig:
    """Build the Caddy config. Only meaningful for compose/dev — k8s uses
    an Ingress instead. Callers pick the right renderer based on target."""
    net = deployment.spec.network

    gateway = next((c for c in components if c.metadata.name == "auth-gateway"), None)
    gateway_active = gateway is not None and is_component_active(gateway, deployment)
    gateway_port = 4180
    if gateway is not None and gateway.spec.ports:
        gateway_port = gateway.spec.ports[0].container_port

    dex = next((c for c in components if c.metadata.name == "dex"), None)
    dex_active = dex is not None and is_component_active(dex, deployment)

    return CaddyConfig(
        hostname=net.hostname,
        https_port=net.https_port,
        tls_mode=net.tls,
        admin_email=None,  # populated by the renderer from spec when letsencrypt
        gateway_enabled=gateway_active and deployment.spec.auth.gateway,
        gateway_service="wip-auth-gateway",
        gateway_port=gateway_port,
        routes=resolve_routes(deployment, components, apps),
        has_dex=dex_active,
        dex_service="wip-dex",
    )
