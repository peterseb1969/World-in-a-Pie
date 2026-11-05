"""Cross-target invariant tests for the public Caddy and the k8s Ingress.

These are the drift-prevention layer. wip-caddy is the front door on every
target (on k8s behind one catch-all Ingress), so its config must carry the
same routes, with the same flags, on compose and k8s.
"""

from __future__ import annotations

import pytest

from wip_deploy.config_gen import generate_caddy_config, generate_ingress_config
from wip_deploy.discovery import Discovery
from wip_deploy.spec import AppRef, Deployment

# ────────────────────────────────────────────────────────────────────
# Caddy
# ────────────────────────────────────────────────────────────────────


class TestCaddy:
    def test_basic_fields(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg.hostname == "wip.local"
        assert cfg.tls_mode == "internal"

    def test_gateway_on_when_auth_gateway_true(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg.gateway_enabled is True
        assert cfg.gateway_service == "wip-auth-gateway"
        assert cfg.gateway_port == 4180

    def test_gateway_off_when_auth_gateway_false(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        cfg = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
        assert cfg.gateway_enabled is False

    def test_dex_proxy_when_dex_active(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg.has_dex is True

    def test_no_dex_proxy_for_api_key_only(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        d.spec.auth.mode = "api-key-only"
        d.spec.auth.users = []
        cfg = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
        assert cfg.has_dex is False


# ────────────────────────────────────────────────────────────────────
# NGINX Ingress
# ────────────────────────────────────────────────────────────────────


class TestIngress:
    def test_rejects_non_k8s_target(self, compose_deployment: Deployment) -> None:
        with pytest.raises(ValueError, match="target=k8s"):
            generate_ingress_config(compose_deployment)

    def test_basic_fields(self, k8s_deployment: Deployment) -> None:
        cfg = generate_ingress_config(k8s_deployment)
        assert cfg.hostname == "wip-kubi.local"
        assert cfg.ingress_class == "nginx"
        assert cfg.controller == "nginx"
        assert cfg.tls_secret_name == "wip-tls"
        assert cfg.namespace == "wip"

    def test_every_request_goes_to_wip_caddy(self, k8s_deployment: Deployment) -> None:
        cfg = generate_ingress_config(k8s_deployment)
        assert (cfg.backend_service, cfg.backend_port) == ("wip-caddy", 8080)

    def test_nginx_annotations(self, k8s_deployment: Deployment) -> None:
        a = generate_ingress_config(k8s_deployment).annotations
        assert a["nginx.ingress.kubernetes.io/proxy-body-size"] == "0"
        assert a["nginx.ingress.kubernetes.io/proxy-read-timeout"] == "3600"
        assert a["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"
        assert a["nginx.ingress.kubernetes.io/ssl-redirect"] == "true"
        # No routing, no auth: wip-caddy owns both.
        assert not any("auth-url" in k or "rewrite-target" in k for k in a)

    def test_traefik_annotations(self, k8s_deployment: Deployment) -> None:
        d = k8s_deployment.model_copy(deep=True)
        assert d.spec.platform.k8s is not None
        d.spec.platform.k8s.ingress_controller = "traefik"
        a = generate_ingress_config(d).annotations
        assert a == {
            "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
            "traefik.ingress.kubernetes.io/router.tls": "true",
        }
        # Never a Middleware reference: the route must not depend on a CRD.
        assert not any("middlewares" in k for k in a)


# ────────────────────────────────────────────────────────────────────
# Cross-target invariants (the drift-prevention layer)
# ────────────────────────────────────────────────────────────────────


class TestCrossTargetInvariants:
    def test_k8s_front_door_has_compose_routes(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """wip-caddy is one generator on both targets: the same routes,
        auth, streaming and strip flags, for the same spec."""
        compose = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        k8s = generate_caddy_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )
        assert compose.routes == k8s.routes
        assert compose.gateway_enabled and k8s.gateway_enabled

    def test_k8s_front_door_is_plain_http_with_set_forwarded_headers(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        compose = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        k8s = generate_caddy_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )
        assert compose.plain_http_port is None and compose.forwarded_scheme is None
        assert k8s.plain_http_port == 8080
        assert (k8s.forwarded_scheme, k8s.forwarded_host) == ("https", "wip-kubi.local")

    def test_gateway_protects_app_routes_on_both_targets(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        for d in (compose_deployment, k8s_deployment):
            d = d.model_copy(deep=True)
            d.spec.apps = [AppRef(name="dnd")]
            caddy = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
            dnd_route = next(r for r in caddy.routes if r.path == "/apps/dnd")
            assert dnd_route.auth_protected is True
            assert caddy.gateway_enabled is True

    def test_streaming_flag_preserved_across_targets(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """document-store has streaming=True in its manifest."""
        for d in (compose_deployment, k8s_deployment):
            caddy = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
            ds = next(r for r in caddy.routes if r.path == "/api/document-store")
            assert ds.streaming is True

    def test_strip_prefix_flag_preserved_across_targets(
        self,
        maximal_compose_deployment: Deployment,
        maximal_k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """CASE-54: garage has strip_prefix=True on /garage so the store
        sees the request at its root."""
        for d in (maximal_compose_deployment, maximal_k8s_deployment):
            caddy = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
            assert next(r for r in caddy.routes if r.path == "/garage").strip_prefix is True
            assert next(r for r in caddy.routes if r.path == "/api/document-store").strip_prefix is False

    def test_inactive_components_absent_from_both_targets(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Inactive optional components (e.g., reporting-sync in standard)
        contribute no routes on either target."""
        for d in (compose_deployment, k8s_deployment):
            caddy = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
            assert "/api/reporting-sync" not in {r.path for r in caddy.routes}
