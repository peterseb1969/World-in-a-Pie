"""Ingress config generation (k8s target).

On k8s the install's front door is wip-caddy, the same public Caddy compose
runs (config_gen/caddy.py): every route, `forward_auth`, the identity-header
strip and the root redirect live in its Caddyfile. The cluster's ingress
controller only terminates TLS for the hostname and hands every request to
wip-caddy through ONE catch-all Ingress, so any controller works and the
routing is one code path on every target.

What stays with the controller is a handful of per-Ingress annotations,
picked by the controller flavour (`platform.k8s.ingress_controller`), and
controller-wide settings the install cannot render (Traefik's read timeout
and alias-header strategy), which the docs and preflight cover.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wip_deploy.config_gen.caddy import PUBLIC_CADDY_K8S_PORT
from wip_deploy.spec import Deployment

PUBLIC_CADDY_SERVICE = "wip-caddy"


def controller_annotations(controller: str) -> dict[str, str]:
    """The catch-all Ingress's annotations for the controller flavour.

    ingress-nginx: no body-size limit ("0" is unlimited; backups run into the
    gigabytes), one-hour timeouts for long uploads and restores (its 60 s
    default kills them), no response buffering (SSE and MCP streaming pass
    through wip-caddy), and HTTPS only.

    Traefik: only HTTPS routing. It has no body-size limit and does not
    buffer by default, and its request read timeout is an entry-point
    setting, not an annotation. No Middleware references: a missing
    Traefik CRD or object would break the route, and this Ingress must work
    with nothing controller-specific installed.
    """
    if controller == "nginx":
        return {
            "nginx.ingress.kubernetes.io/ssl-redirect": "true",
            "nginx.ingress.kubernetes.io/proxy-body-size": "0",
            "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
            "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
            "nginx.ingress.kubernetes.io/proxy-buffering": "off",
        }
    if controller == "traefik":
        return {
            "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
            "traefik.ingress.kubernetes.io/router.tls": "true",
        }
    raise ValueError(f"unsupported ingress controller {controller!r}")


@dataclass(frozen=True)
class IngressConfig:
    hostname: str
    ingress_class: str
    controller: str
    tls_secret_name: str
    namespace: str
    backend_service: str = PUBLIC_CADDY_SERVICE
    backend_port: int = PUBLIC_CADDY_K8S_PORT
    annotations: dict[str, str] = field(default_factory=dict)
    # With app origins (network.app_origins=subdomain): "*.<hostname>", so
    # every app's origin reaches wip-caddy, which tells them apart by Host.
    app_hosts: tuple[str, ...] = ()


def generate_ingress_config(deployment: Deployment) -> IngressConfig:
    """Build the catch-all Ingress config. Only meaningful on the k8s target."""
    if deployment.spec.target != "k8s":
        raise ValueError("generate_ingress_config is only valid for target=k8s")
    k8s = deployment.spec.platform.k8s
    if k8s is None:
        raise ValueError("k8s target requires platform.k8s")
    return IngressConfig(
        hostname=deployment.spec.network.hostname,
        ingress_class=k8s.ingress_class,
        controller=k8s.ingress_controller,
        tls_secret_name=k8s.tls_secret_name,
        namespace=k8s.namespace,
        annotations=controller_annotations(k8s.ingress_controller),
        app_hosts=app_origin_hosts(deployment),
    )


def app_origin_hosts(deployment: Deployment) -> tuple[str, ...]:
    """The names app origins add to the Ingress and its certificate:
    `*.<hostname>` in subdomain mode, none otherwise."""
    if deployment.spec.network.app_origins != "subdomain":
        return ()
    return (f"*.{deployment.spec.network.hostname}",)
