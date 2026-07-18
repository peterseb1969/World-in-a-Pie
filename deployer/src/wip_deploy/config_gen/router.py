"""Internal HTTP router config generation.

Apps that proxy API calls server-side (e.g., react-console's SSR proxy
via @wip/proxy) need a single entry point that multiplexes `/api/*`
paths to the right backend service. This is the target-agnostic
"internal API aggregator" concept.

The compose path historically used Caddy's `:8080` listener for this.
That was a renderer special-case — the intent layer didn't express the
concept, so k8s had no equivalent and SSR proxies hit 502.

Now: `wip-router` is an explicit Component (see
`components/wip-router/wip-component.yaml`). This module generates its
Caddyfile from other components' `/api/*` routes. Both renderers emit
wip-router as an ordinary service; the Caddyfile is produced here and
mounted into the container.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.env import _default_port
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────

# The router itself listens on this port. Apps set WIP_BASE_URL to
# `http://wip-router:<this>`; the router reverse-proxies /api/* to
# backends. Not user-configurable today — tied to the wip-router
# manifest's declared port.
ROUTER_LISTEN_PORT = 8080


@dataclass(frozen=True)
class RouterRoute:
    """One `handle <path>[*] { reverse_proxy <backend> }` line in the
    router's Caddyfile.

    `redirect_bare_path=True` (the default for /api/<svc>/*): emit only
    `handle <path>/*`. Apps + tools hit `/api/<svc>/<sub>` directly; the
    bare path never matters.

    `redirect_bare_path=False` (the /mcp pattern from CASE-312): emit
    BOTH `handle <path>` AND `handle <path>/*`. The MCP StreamableHTTP
    transport mounts at the bare path; routing only `/*` falls through
    to Caddy's empty 200 → "Unexpected content type: null" → client crash.
    """

    path: str
    backend_host: str  # short name, e.g. "wip-registry"
    backend_port: int
    streaming: bool
    # CASE-378: bare-path handling. Mirrors compose_caddy.py's logic
    # for the inner router. Default True keeps existing /api/* routes
    # behavior; False is the /mcp case.
    redirect_bare_path: bool = True


@dataclass(frozen=True)
class RouterConfig:
    """Router config — renderer-independent. `render_router_caddyfile`
    turns this into Caddyfile text."""

    listen_port: int
    routes: list[RouterRoute]


def generate_router_config(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> RouterConfig:
    """Collect platform-service routes (/api/* + /mcp) for the router.

    App routes (/apps/*) aren't included — apps are the CALLERS of the
    router, not routees. The router exists to give apps a uniform
    entry point to the WIP service layer.

    CASE-378: /mcp is now included. The router is the apps-side handle
    for the WIP backend; MCP is part of that surface. Apps can set
    MCP_URL to the router (e.g. http://wip-router:8080/mcp) the same
    way they set WIP_BASE_URL.
    """
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    routes: list[RouterRoute] = []

    for c in components:
        if not is_component_active(c, deployment):
            continue
        if c.metadata.name == "router":
            continue  # don't route the router to itself
        for r in c.spec.routes:
            # /api/<svc>/* (platform services) + /mcp (MCP server) go
            # through the router. /apps/* are caller routes; never routed.
            if not (r.path.startswith("/api/") or r.path == "/mcp"):
                continue
            port = _default_port(c)
            routes.append(RouterRoute(
                path=r.path,
                backend_host=f"wip-{c.metadata.name}",
                backend_port=port.container_port,
                streaming=r.streaming,
                redirect_bare_path=r.redirect_bare_path,
            ))

    # Apps don't currently contribute /api/* routes, but guard against
    # a future app that does.
    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        for r in a.spec.routes:
            if not r.path.startswith("/api/"):
                continue
            port = _default_port(a)  # type: ignore[arg-type]
            routes.append(RouterRoute(
                path=r.path,
                backend_host=f"wip-{a.metadata.name}",
                backend_port=port.container_port,
                streaming=r.streaming,
                redirect_bare_path=r.redirect_bare_path,
            ))

    routes.sort(key=lambda r: (r.path, r.backend_host))

    return RouterConfig(
        listen_port=ROUTER_LISTEN_PORT,
        routes=routes,
    )
