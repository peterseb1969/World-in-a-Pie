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
    strip_prefix: bool


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

    # Dev-target + --app-source override: route to the app's `dev` port
    # (e.g. Vite on 5174) instead of its production port. See CASE-55.
    # Empty dict for non-dev targets or apps without overrides.
    app_sources_with_dev_port: set[str] = set()
    if deployment.spec.target == "dev" and deployment.spec.platform.dev:
        app_sources_with_dev_port = set(
            deployment.spec.platform.dev.app_sources.keys()
        )

    for c in components:
        if not is_component_active(c, deployment):
            continue
        resolved.extend(_routes_for(c, gateway_on, app_sources_with_dev_port))

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        resolved.extend(_routes_for(a, gateway_on, app_sources_with_dev_port))

    resolved.sort(key=lambda r: (r.backend_component, r.path))
    return resolved


def _routes_for(
    owner: Component | App,
    gateway_on: bool,
    app_sources_with_dev_port: set[str],
) -> list[ResolvedRoute]:
    if not owner.spec.routes:
        return []
    port = _pick_port(owner, app_sources_with_dev_port)
    return [
        _resolve_one(owner.metadata.name, r, port.container_port, gateway_on)
        for r in owner.spec.routes
    ]


def _pick_port(owner: Component | App, app_sources_with_dev_port: set[str]) -> object:
    """Pick the port the Caddy route targets.

    In dev mode with `--app-source <name>=<path>` set, if the app's
    manifest declares a port named `dev` (e.g. Vite on 5174), route to
    that port instead of the default `http` port. Lets apps expose
    their dev UI server on a separate port from the production server
    without affecting compose/k8s routing.

    Falls back to `_default_port` (named `http` or first declared) in
    all other cases — including non-dev targets and apps without an
    override.
    """
    if isinstance(owner, App) and owner.metadata.name in app_sources_with_dev_port:
        for port in owner.spec.ports:
            if port.name == "dev":
                return port
    return _default_port(owner)  # type: ignore[arg-type]


def _resolve_one(
    component_name: str, route: Route, port: int, gateway_on: bool
) -> ResolvedRoute:
    return ResolvedRoute(
        path=route.path,
        backend_component=component_name,
        backend_port=port,
        auth_protected=route.auth_required and gateway_on,
        streaming=route.streaming,
        strip_prefix=route.strip_prefix,
    )
