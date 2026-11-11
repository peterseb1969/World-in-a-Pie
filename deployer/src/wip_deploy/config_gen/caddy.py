"""Public Caddy config generation (every target).

Produces a structured `CaddyConfig` that `renderers/compose_caddy.py`
serializes to the Caddyfile of `wip-caddy`, the install's public front door.
Routes consume `ResolvedRoute` from `routing.py`, so every target agrees on
which route is auth-protected.

On compose and dev, wip-caddy terminates TLS itself on the host port. On
k8s it runs behind the cluster's ingress controller (nginx or Traefik),
which terminates TLS for the hostname and hands it every request on plain
HTTP; the routing, `forward_auth` and identity-header stripping are the
same Caddyfile on both.

The Caddy renderer uses a `forward_auth` block for every auth-protected
route when `auth.gateway=True`. The gateway's answer carries X-WIP-User /
X-WIP-Groups, which Caddy copies onto the request before forwarding to the
backend.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.routing import (
    ResolvedRoute,
    resolve_root_redirect,
    resolve_routes,
)
from wip_deploy.spec import Deployment
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# The public Caddy's image on every target, and the port it listens on
# behind a k8s ingress controller.
PUBLIC_CADDY_IMAGE = "docker.io/library/caddy:2"
PUBLIC_CADDY_K8S_PORT = 8080


# The app session cookie; removed from requests before they reach an app.
APP_SESSION_COOKIE = "wip_app_session"
APP_EDGE_COMPONENT = "app-edge"


@dataclass(frozen=True)
class AppSite:
    """One deployer app on its own origin, <label>.<hostname>: its label and
    its routes, which are paths on that origin."""

    label: str
    routes: tuple[ResolvedRoute, ...]


@dataclass(frozen=True)
class CaddyConfig:
    hostname: str
    https_port: int  # external HTTPS port Caddy binds to inside the container
    tls_mode: str  # "internal" | "letsencrypt" | "external"
    # Where forward_auth asks; a route is protected iff its resolved
    # `auth_protected` says so (the gateway being on is part of that).
    gateway_service: str  # DNS name of the auth-gateway container
    gateway_port: int
    # Whether this install runs the gateway at all. Required, not defaulted: a
    # render that cannot answer this points app origins at a service it may not
    # have emitted.
    gateway_active: bool
    routes: list[ResolvedRoute]
    # Bare-host `/` redirect target (the first enabled app's origin), or
    # None: `/` then answers the no-app page. Resolved in the shared layer
    # so every target agrees.
    root_redirect: str | None
    # Emit the hardening-header block (HSTS et al.) in the site block.
    # Keyed on public exposure (tls=letsencrypt): LAN installs stay
    # unchanged, and HSTS on a self-signed .local host would pin
    # browsers to a CA they may drop.
    emit_hardening_headers: bool = False
    # k8s: listen on this plain-HTTP port instead of terminating TLS. The
    # ingress controller in front owns TLS for the hostname.
    plain_http_port: int | None = None
    # k8s: the scheme and host of the install's public URL, SET on every
    # request toward the gateway and the backends. Caddy sees plain HTTP from
    # the controller, whose pod addresses are not known at render time, so
    # it can neither derive these nor trust a client's X-Forwarded-*.
    forwarded_scheme: str | None = None
    forwarded_host: str | None = None
    # The deployment's target and k8s namespace, so every backend is dialled
    # at the exact host the env layer hands services (env._component_host):
    # the short container name on compose and dev, the Service FQDN on k8s.
    # A route with upstream_host_header sends that host as Host, and object
    # storage signs presigned URLs against it (CASE-864).
    target: str = "compose"
    namespace: str = "wip"
    # k8s: the address ranges whose X-Forwarded-For wip-caddy keeps, so the
    # ingress controller's view of the client reaches the backends. Without
    # them Caddy replaces the header with its peer's address, the
    # controller pod, and every user shares one rate-limit bucket. The
    # controller runs in the cluster's pod ranges (--cluster-cidr pods=).
    # Empty on compose and dev, where wip-caddy faces the client itself and
    # must not take a client's word for its address.
    trusted_proxies: tuple[str, ...] = ()
    # Every app is served on its own origin, so app routes are not in
    # `routes`: each deployer app is an `app_sites` entry, and attached apps
    # are served by a catch-all site handed to the app edge
    # (`edge_backend`), which also answers whether a name may get a
    # certificate (on-demand TLS).
    app_sites: tuple[AppSite, ...] = ()
    edge_backend: str | None = None

    def origin(self, host: str) -> str:
        """https://<host>[:port] on this install's public port."""
        port = "" if self.https_port == 443 else f":{self.https_port}"
        return f"https://{host}{port}"

    def app_origin(self, label: str) -> str:
        return self.origin(f"{label}.{self.hostname}")

    def backend(self, component: str, port: int) -> str:
        from wip_deploy.config_gen.env import _component_host

        return f"{_component_host(component, self.target, namespace=self.namespace)}:{port}"  # type: ignore[arg-type]


def generate_caddy_config(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> CaddyConfig:
    """Build the public Caddy's config for the deployment's target."""
    net = deployment.spec.network

    gateway = next((c for c in components if c.metadata.name == "auth-gateway"), None)
    gateway_port = 4180
    if gateway is not None and gateway.spec.ports:
        gateway_port = gateway.spec.ports[0].container_port

    behind_ingress = deployment.spec.target == "k8s"
    k8s = deployment.spec.platform.k8s
    namespace = k8s.namespace if behind_ingress and k8s is not None else "wip"

    routes = resolve_routes(deployment, components, apps)
    root_redirect = resolve_root_redirect(deployment, apps)
    edge_backend: str | None = None
    routes, app_sites, edge_route = _split_app_routes(routes)
    if edge_route is not None:
        from wip_deploy.config_gen.env import _component_host

        edge_host = _component_host(APP_EDGE_COMPONENT, deployment.spec.target, namespace=namespace)  # type: ignore[arg-type]
        edge_backend = f"{edge_host}:{edge_route.backend_port}"

    return CaddyConfig(
        hostname=net.hostname,
        https_port=net.https_port,
        tls_mode=net.tls,
        gateway_service=f"wip-auth-gateway.{namespace}.svc.cluster.local" if behind_ingress else "wip-auth-gateway",
        gateway_port=gateway_port,
        # The spec says whether the install runs a gateway; the component list
        # only reflects it after activation, and the render must answer from the
        # intent rather than from what happened to survive filtering.
        gateway_active=deployment.spec.auth.gateway,
        routes=routes,
        root_redirect=root_redirect,
        emit_hardening_headers=(net.tls == "letsencrypt"),
        plain_http_port=PUBLIC_CADDY_K8S_PORT if behind_ingress else None,
        # The k8s Ingress always carries the hostname's TLS block.
        forwarded_scheme="https" if behind_ingress else None,
        forwarded_host=net.hostname if behind_ingress else None,
        target=deployment.spec.target,
        namespace=namespace,
        trusted_proxies=(
            tuple(k8s.cluster_ranges.pods) if behind_ingress and k8s is not None else ()
        ),
        app_sites=app_sites,
        edge_backend=edge_backend,
    )


def _split_app_routes(
    routes: list[ResolvedRoute],
) -> tuple[list[ResolvedRoute], tuple[AppSite, ...], ResolvedRoute | None]:
    """(routes WIP's origin keeps, one AppSite per deployer app, the app
    edge's route or None). App routes are served on their app's origin, and
    the edge's route is no route of WIP's origin: its apps get the catch-all
    site."""
    main: list[ResolvedRoute] = []
    by_label: dict[str, list[ResolvedRoute]] = {}
    edge_route: ResolvedRoute | None = None
    for r in routes:
        if r.app is not None:
            by_label.setdefault(r.app, []).append(r)
        elif r.backend_component == APP_EDGE_COMPONENT:
            edge_route = r
        else:
            main.append(r)
    sites = tuple(AppSite(label=k, routes=tuple(v)) for k, v in sorted(by_label.items()))
    return main, sites, edge_route
