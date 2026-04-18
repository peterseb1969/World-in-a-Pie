"""NGINX Ingress config generation (k8s target).

Produces a list of `IngressRule` objects plus a top-level `IngressConfig`
that the k8s renderer serializes to a single `networking.k8s.io/v1/Ingress`
resource.

The auth-gateway appears as an NGINX Ingress auth-url annotation when
`auth.gateway=True` — the target-idiomatic expression of what Caddy's
`forward_auth` does on compose.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.routing import resolve_routes
from wip_deploy.spec import Deployment
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


@dataclass(frozen=True)
class IngressRule:
    path: str
    backend_service: str  # k8s Service name (already "wip-<name>")
    backend_port: int
    auth_protected: bool
    streaming: bool


@dataclass(frozen=True)
class IngressConfig:
    hostname: str
    ingress_class: str
    tls_secret_name: str
    namespace: str
    rules: list[IngressRule]
    # Annotations: gateway forward-auth URL (None iff gateway disabled)
    gateway_auth_url: str | None
    # No body-size limit by default. nginx-ingress treats "0" as
    # unlimited, matching Caddy's (compose) default. Real backups run
    # into the multi-GB range; capping here would just surface a 413
    # at the worst moment. Clusters that need a cap for DoS protection
    # can override (K8sPlatform field — follow-up).
    proxy_body_size: str = "0"


def generate_ingress_config(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> IngressConfig:
    """Build the Ingress config. Only meaningful on the k8s target."""
    if deployment.spec.target != "k8s":
        raise ValueError("generate_ingress_config is only valid for target=k8s")

    net = deployment.spec.network
    k8s = deployment.spec.platform.k8s
    if k8s is None:
        raise ValueError("k8s target requires platform.k8s")

    gateway_on = deployment.spec.auth.gateway
    gateway_auth_url: str | None = None
    if gateway_on:
        gw = next(
            (c for c in components if c.metadata.name == "auth-gateway"), None
        )
        if gw is None or not gw.spec.ports:
            raise ValueError(
                "auth-gateway component missing or has no ports — "
                "gateway mode needs its HTTP port to wire auth-url"
            )
        # Prefer the "http"-named port; fall back to the first declared port.
        port = next((p for p in gw.spec.ports if p.name == "http"), gw.spec.ports[0])
        gateway_auth_url = (
            f"http://wip-auth-gateway.{k8s.namespace}.svc.cluster.local"
            f":{port.container_port}/auth/verify"
        )

    rules = [
        IngressRule(
            path=r.path,
            backend_service=f"wip-{r.backend_component}",
            backend_port=r.backend_port,
            auth_protected=r.auth_protected,
            streaming=r.streaming,
        )
        for r in resolve_routes(deployment, components, apps)
    ]

    return IngressConfig(
        hostname=net.hostname,
        ingress_class=k8s.ingress_class,
        tls_secret_name=k8s.tls_secret_name,
        namespace=k8s.namespace,
        rules=rules,
        gateway_auth_url=gateway_auth_url,
    )
