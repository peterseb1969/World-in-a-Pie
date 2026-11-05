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
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# The public Caddy's image on every target, and the port it listens on
# behind a k8s ingress controller.
PUBLIC_CADDY_IMAGE = "docker.io/library/caddy:2"
PUBLIC_CADDY_K8S_PORT = 8080


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
    # Bare-host `/` redirect target (CASE-368), or None to leave `/`
    # unhandled. Resolved in the shared layer so every target agrees.
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
    gateway_active = gateway is not None and is_component_active(gateway, deployment)
    gateway_port = 4180
    if gateway is not None and gateway.spec.ports:
        gateway_port = gateway.spec.ports[0].container_port

    dex = next((c for c in components if c.metadata.name == "dex"), None)
    dex_active = dex is not None and is_component_active(dex, deployment)

    behind_ingress = deployment.spec.target == "k8s"
    k8s = deployment.spec.platform.k8s
    namespace = k8s.namespace if behind_ingress and k8s is not None else "wip"

    return CaddyConfig(
        hostname=net.hostname,
        https_port=net.https_port,
        tls_mode=net.tls,
        admin_email=None,  # populated by the renderer from spec when letsencrypt
        gateway_enabled=gateway_active and deployment.spec.auth.gateway,
        gateway_service=f"wip-auth-gateway.{namespace}.svc.cluster.local" if behind_ingress else "wip-auth-gateway",
        gateway_port=gateway_port,
        routes=resolve_routes(deployment, components, apps),
        has_dex=dex_active,
        dex_service="wip-dex",
        root_redirect=resolve_root_redirect(deployment, apps),
        emit_hardening_headers=(net.tls == "letsencrypt"),
        plain_http_port=PUBLIC_CADDY_K8S_PORT if behind_ingress else None,
        # The k8s Ingress always carries the hostname's TLS block.
        forwarded_scheme="https" if behind_ingress else None,
        forwarded_host=net.hostname if behind_ingress else None,
        target=deployment.spec.target,
        namespace=namespace,
    )
