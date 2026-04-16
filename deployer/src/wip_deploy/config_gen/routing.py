"""Shared route resolution.

Every active component + enabled app contributes HTTP routes. Caddy
(compose/dev) and NGINX Ingress (k8s) consume the same list of
`ResolvedRoute` objects and emit target-idiomatic output.

Putting this computation here — not inside each renderer — guarantees the
two renderers never disagree about which route is auth-protected, which
one streams, what the backend port is, etc.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.env import _default_port
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component, Route


@dataclass(frozen=True)
class ResolvedRoute:
    """A routing rule the renderer will emit.

    `auth_protected` is the only route-level auth signal the renderer
    needs: it already encodes the two-part decision (route declared
    auth_required AND deployment enables the gateway).
    """

    path: str
    backend_component: str
    backend_port: int
    auth_protected: bool
    streaming: bool


def resolve_routes(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> list[ResolvedRoute]:
    """Return every route an active component/app contributes, in a stable
    order (component name, then path)."""
    gateway_on = deployment.spec.auth.gateway
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}
    resolved: list[ResolvedRoute] = []

    for c in components:
        if not is_component_active(c, deployment):
            continue
        resolved.extend(_routes_for(c, gateway_on))

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        resolved.extend(_routes_for(a, gateway_on))

    resolved.sort(key=lambda r: (r.backend_component, r.path))
    return resolved


def _routes_for(owner: Component | App, gateway_on: bool) -> list[ResolvedRoute]:
    if not owner.spec.routes:
        return []
    port = _default_port(owner)  # type: ignore[arg-type]
    return [
        _resolve_one(owner.metadata.name, r, port.container_port, gateway_on)
        for r in owner.spec.routes
    ]


def _resolve_one(
    component_name: str, route: Route, port: int, gateway_on: bool
) -> ResolvedRoute:
    return ResolvedRoute(
        path=route.path,
        backend_component=component_name,
        backend_port=port,
        auth_protected=route.auth_required and gateway_on,
        streaming=route.streaming,
    )
