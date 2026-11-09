"""Shared route resolution.

Every active component + enabled app contributes HTTP routes. wip-caddy's
Caddyfile, the public front door on every target (on k8s behind one
catch-all Ingress), is rendered from this list of `ResolvedRoute` objects.

Putting this computation here — not inside a target's renderer — guarantees
the targets never disagree about which route is auth-protected, which one
streams, what the backend port is, etc.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.env import _default_port
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component, Port, Route

# The headers the auth gateway's verify answer carries and the proxy copies
# onto a protected request: the logged-in user's identity, nothing else. The
# gateway holds no API key, so no key travels with them.
GATEWAY_IDENTITY_HEADERS: tuple[str, ...] = ("X-WIP-User", "X-WIP-Groups")

# Identity headers an app may read (`@wip/proxy` forwards none of them to WIP).
# On a protected route the proxy's copy of the gateway's answer overwrites or
# removes the gateway's two whatever the browser sent; the rest the gateway
# never supplies, so the renderers remove them. On an app route the gateway
# does not protect, nothing overwrites anything, so the renderers remove all
# of them from the client's request before it reaches the app.
CLIENT_IDENTITY_HEADERS: tuple[str, ...] = (*GATEWAY_IDENTITY_HEADERS, "X-WIP-Auth-Method")


@dataclass(frozen=True)
class ResolvedRoute:
    """A routing rule the renderer will emit.

    `auth_protected` encodes the two-part decision (route declared
    auth_required AND deployment enables the gateway); `allowed_groups`
    narrows who the gateway lets through, rendered via verify_path().
    """

    path: str
    backend_component: str
    backend_port: int
    auth_protected: bool
    streaming: bool
    strip_prefix: bool
    redirect_bare_path: bool
    # Rewrite Host to the upstream's host:port when proxying — backends
    # that validate the signed Host of presigned URLs (object storage).
    upstream_host_header: bool = False
    # Subpaths within `path` to NOT strip when strip_prefix=True.
    # An object store's own admin/health endpoints under its public
    # prefix are the canonical case. CASE-248.
    preserve_prefix_subpaths: tuple[str, ...] = ()
    # Groups the gateway lets through on this route (empty = any logged-in
    # user). Only meaningful when auth_protected.
    allowed_groups: tuple[str, ...] = ()
    # The label of the app the route belongs to, or None for a platform
    # component. An app's routes are paths on its own origin,
    # https://<label>.<hostname>, and have client-sent identity headers
    # removed (see client_identity_headers_to_strip), so an app never reads a
    # forged user from the browser.
    app: str | None = None

    @property
    def is_app(self) -> bool:
        return self.app is not None

    def client_identity_headers_to_strip(self) -> tuple[str, ...]:
        """The client-sent identity headers the renderer removes on this
        route: none on a platform route; on a protected app route those
        the gateway's answer does not overwrite; on an unprotected app
        route all of them."""
        if not self.is_app:
            return ()
        if self.auth_protected:
            return tuple(h for h in CLIENT_IDENTITY_HEADERS if h not in GATEWAY_IDENTITY_HEADERS)
        return CLIENT_IDENTITY_HEADERS


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


def resolve_root_redirect(
    deployment: Deployment,
    apps: list[App],
) -> str | None:
    """Where a bare-host (`/`) request on WIP's origin is sent, or None to
    emit no redirect.

    CASE-368: a fresh install left `https://<host>/` with no handler, and
    Caddy answers an unhandled path with 200 and an empty body, a confusing
    first contact. This resolves where `/` should send the user, computed
    once here so every target's wip-caddy agrees (same cross-target
    invariant as resolve_routes).

    Policy:
      - First enabled app (in `spec.apps` / `--app` declaration order) → the
        root of its origin, https://<label>.<hostname>[:port]/.
        Deterministic; couples to declaration order by design (the operator
        lists their primary app first).
      - No apps enabled → None: there is nothing to send the user to.
        wip-caddy then answers `/` with a page saying no app is enabled
        yet. (It redirected to `/auth/login` when the gateway was on; a
        login lands back on `/`, so that was a loop.)
    """
    by_name = {a.metadata.name: a for a in apps}
    for ref in deployment.spec.apps:
        if not ref.enabled:
            continue
        app = by_name.get(ref.name)
        if app is not None:
            return app_origin(deployment, app.app_metadata.label) + "/"
    return None


def app_origin(deployment: Deployment, label: str) -> str:
    """https://<label>.<hostname>[:port], on the install's public port."""
    net = deployment.spec.network
    port = "" if net.https_port == 443 else f":{net.https_port}"
    return f"https://{label}.{net.hostname}{port}"


def _routes_for(
    owner: Component | App,
    gateway_on: bool,
    app_sources_with_dev_port: set[str],
) -> list[ResolvedRoute]:
    if not owner.spec.routes:
        return []
    port = _pick_port(owner, app_sources_with_dev_port)
    app = owner.app_metadata.label if isinstance(owner, App) else None
    return [
        _resolve_one(owner.metadata.name, r, port.container_port, gateway_on, app=app)
        for r in owner.spec.routes
    ]


def _pick_port(owner: Component | App, app_sources_with_dev_port: set[str]) -> Port:
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
    component_name: str, route: Route, port: int, gateway_on: bool, *, app: str | None = None
) -> ResolvedRoute:
    return ResolvedRoute(
        path=route.path,
        backend_component=component_name,
        backend_port=port,
        auth_protected=route.auth_required and gateway_on,
        streaming=route.streaming,
        strip_prefix=route.strip_prefix,
        redirect_bare_path=route.redirect_bare_path,
        preserve_prefix_subpaths=tuple(route.preserve_prefix_subpaths),
        upstream_host_header=route.upstream_host_header,
        allowed_groups=tuple(route.allowed_groups),
        app=app,
    )


def verify_query(allowed_groups: tuple[str, ...]) -> str:
    """The query string that carries a route's allowed groups to the
    gateway's verify endpoint ("" when any logged-in user may pass).

    The one definition every renderer uses, so the group list reaches the
    gateway the same way on every target. Names are URL-safe by spec
    validation.
    """
    if not allowed_groups:
        return ""
    return f"?allowed_groups={','.join(allowed_groups)}"


def verify_path(route: ResolvedRoute) -> str:
    """The gateway verify path (with query) for an auth-protected route."""
    return f"/auth/verify{verify_query(route.allowed_groups)}"
