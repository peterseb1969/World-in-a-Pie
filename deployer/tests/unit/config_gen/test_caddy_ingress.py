"""Cross-target invariant tests for Caddy and NGINX Ingress generators.

These are the drift-prevention layer. Each invariant asserts the same
semantic property holds on both targets — "auth-protected route goes
through the gateway" must be expressed in both idioms identically.
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
    def test_rejects_non_k8s_target(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        with pytest.raises(ValueError, match="target=k8s"):
            generate_ingress_config(
                compose_deployment, real_discovery.components, real_discovery.apps
            )

    def test_basic_fields(
        self, k8s_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_ingress_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg.hostname == "wip-kubi.local"
        assert cfg.ingress_class == "nginx"
        assert cfg.tls_secret_name == "wip-tls"
        assert cfg.namespace == "wip"

    def test_gateway_auth_url_when_enabled(
        self, k8s_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """auth-url must include the auth-gateway's container port (4180).
        Without the port, nginx defaults to :80 which nothing listens on —
        requests sit until the 60s timeout, then return 500."""
        cfg = generate_ingress_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg.gateway_auth_url == (
            "http://wip-auth-gateway.wip.svc.cluster.local:4180/auth/verify"
        )

    def test_gateway_auth_url_none_when_disabled(
        self, k8s_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = k8s_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        cfg = generate_ingress_config(
            d, real_discovery.components, real_discovery.apps
        )
        assert cfg.gateway_auth_url is None

    def test_rules_include_backend_service_prefix(
        self, k8s_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_ingress_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )
        backends = {r.backend_service for r in cfg.rules}
        assert "wip-registry" in backends
        assert "wip-document-store" in backends


# ────────────────────────────────────────────────────────────────────
# Cross-target invariants (the drift-prevention layer)
# ────────────────────────────────────────────────────────────────────


class TestCrossTargetInvariants:
    def test_gateway_protects_app_routes_on_both_targets(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Auth-protected routes are identified as such on both compose
        and k8s, expressed in each target's idiom."""
        for d in (compose_deployment, k8s_deployment):
            d = d.model_copy(deep=True)
            d.spec.apps = [AppRef(name="dnd")]

            if d.spec.target == "compose":
                caddy = generate_caddy_config(
                    d, real_discovery.components, real_discovery.apps
                )
                dnd_route = next(r for r in caddy.routes if r.path == "/apps/dnd")
                assert dnd_route.auth_protected is True
                assert caddy.gateway_enabled is True
            else:
                ing = generate_ingress_config(
                    d, real_discovery.components, real_discovery.apps
                )
                dnd_rule = next(r for r in ing.rules if r.path == "/apps/dnd")
                assert dnd_rule.auth_protected is True
                assert ing.gateway_auth_url is not None

    def test_streaming_flag_preserved_across_targets(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """document-store has streaming=True in its manifest; both targets
        must carry that through."""
        caddy = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        ing = generate_ingress_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )

        caddy_ds = next(r for r in caddy.routes if r.path == "/api/document-store")
        ing_ds = next(r for r in ing.rules if r.path == "/api/document-store")

        assert caddy_ds.streaming is True
        assert ing_ds.streaming is True

    def test_inactive_components_absent_from_both_targets(
        self,
        compose_deployment: Deployment,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Inactive optional components (e.g., reporting-sync in standard)
        contribute no routes on either target."""
        caddy = generate_caddy_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        ing = generate_ingress_config(
            k8s_deployment, real_discovery.components, real_discovery.apps
        )

        caddy_paths = {r.path for r in caddy.routes}
        ing_paths = {r.path for r in ing.rules}

        assert "/api/reporting-sync" not in caddy_paths
        assert "/api/reporting-sync" not in ing_paths
