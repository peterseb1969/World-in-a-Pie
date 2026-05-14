"""Tests for wip-router config generation + Caddyfile emission.

wip-router replaces the compose-era hardcoded :8080 Caddy listener.
These tests assert the new pipeline is equivalent in semantics (/api/*
paths routed to the right backends) and works on both targets.
"""

from __future__ import annotations

from wip_deploy.config_gen.router import (
    ROUTER_LISTEN_PORT,
    generate_router_config,
)
from wip_deploy.discovery import Discovery
from wip_deploy.renderers.router_caddy import render_router_caddyfile
from wip_deploy.spec import Deployment


class TestRouterConfig:
    def test_standard_emits_registry_def_store_template_store_document_store(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        paths = {r.path for r in cfg.routes}
        # The standard preset activates core services — their /api/*
        # routes should all be in the router.
        assert "/api/registry" in paths
        assert "/api/def-store" in paths
        assert "/api/template-store" in paths
        assert "/api/document-store" in paths

    def test_inactive_component_routes_omitted(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """reporting-sync isn't in standard preset → its route shouldn't
        appear in the router config."""
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        paths = {r.path for r in cfg.routes}
        assert "/api/reporting-sync" not in paths

    def test_backend_uses_short_dns_name(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """Short DNS names work on both compose (container network) and
        k8s (namespace search path). No target-specific resolution
        needed."""
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        reg = next(r for r in cfg.routes if r.path == "/api/registry")
        assert reg.backend_host == "wip-registry"
        assert reg.backend_port == 8001

    def test_streaming_preserved(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        ds = next(r for r in cfg.routes if r.path == "/api/document-store")
        assert ds.streaming is True

    def test_listen_port_is_constant(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        assert cfg.listen_port == ROUTER_LISTEN_PORT == 8080

    def test_mcp_route_included(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """CASE-378: /mcp is now routed through the inner router so apps
        that use `from_component: router` can reach MCP without hard-
        coding wip-mcp-server. The bare-path-no-redirect case (CASE-312
        signature) is also covered — see test_mcp_route_emits_bare_handle
        in the renderer test below."""
        d = compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["mcp-server"]
        cfg = generate_router_config(
            d, real_discovery.components, real_discovery.apps
        )
        paths = {r.path for r in cfg.routes}
        assert "/mcp" in paths
        mcp_route = next(r for r in cfg.routes if r.path == "/mcp")
        assert mcp_route.backend_host == "wip-mcp-server"
        assert mcp_route.backend_port == 8007
        # /mcp's manifest declares redirect_bare_path: false
        assert mcp_route.redirect_bare_path is False

    def test_router_excluded_from_its_own_routes(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """wip-router is in the component list but should never route to
        itself — infinite loop."""
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        hosts = {r.backend_host for r in cfg.routes}
        assert "wip-router" not in hosts


class TestRouterCaddyfile:
    def _render(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> str:
        cfg = generate_router_config(
            compose_deployment,
            real_discovery.components,
            real_discovery.apps,
        )
        return render_router_caddyfile(cfg)

    def test_emits_listen_block(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        caddyfile = self._render(compose_deployment, real_discovery)
        assert ":8080 {" in caddyfile

    def test_disables_auto_https(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """Router is HTTP-only — auto_https off avoids the startup ACME
        probe that hits upstream when no HTTPS site exists."""
        caddyfile = self._render(compose_deployment, real_discovery)
        assert "auto_https off" in caddyfile

    def test_reverse_proxy_per_route(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        caddyfile = self._render(compose_deployment, real_discovery)
        assert "handle /api/registry/*" in caddyfile
        assert "reverse_proxy wip-registry:8001" in caddyfile

    def test_streaming_flush_interval(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        caddyfile = self._render(compose_deployment, real_discovery)
        # document-store is streaming — should get flush_interval -1
        ds_start = caddyfile.index("handle /api/document-store/*")
        ds_block = caddyfile[ds_start : ds_start + 200]
        assert "flush_interval -1" in ds_block

    def test_mcp_emits_bare_and_trailing_handles(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """CASE-378: /mcp's redirect_bare_path=false means the renderer
        must emit BOTH `handle /mcp` and `handle /mcp/*`. Without the
        bare-path handler, callers hitting the bare URL get Caddy's
        empty 200 (CASE-312 signature) and crash."""
        d = compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["mcp-server"]
        caddyfile = self._render(d, real_discovery)
        assert "handle /mcp {" in caddyfile
        assert "handle /mcp/* {" in caddyfile
        # Both handles must reverse-proxy to mcp-server.
        # Confirm by counting matches in /mcp-blocks.
        mcp_count = caddyfile.count("reverse_proxy wip-mcp-server:8007")
        assert mcp_count == 2, (
            f"expected /mcp + /mcp/* both routing to mcp-server, "
            f"got {mcp_count} matches"
        )

    def test_api_routes_emit_only_trailing_handle(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """CASE-378: /api/<svc>/* routes default `redirect_bare_path=True`,
        so only `handle /api/<svc>/*` is emitted (no sibling bare-path
        handle). This is the existing behavior — making sure the new
        bare-path-emit logic only kicks in for redirect_bare_path=False
        routes."""
        caddyfile = self._render(compose_deployment, real_discovery)
        # `handle /api/registry {` (bare) should NOT appear — only the
        # trailing `handle /api/registry/* {` version.
        assert "handle /api/registry {" not in caddyfile
        assert "handle /api/registry/* {" in caddyfile


class TestAppEnvResolution:
    """End-to-end: apps pointing at `from_component: wip-router` should
    resolve to the router URL on both compose and k8s, without any
    hardcoded strings."""

    def test_compose_resolves_to_short_dns(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        from wip_deploy.config_gen import make_spec_context, resolve_all_env

        d = compose_deployment.model_copy(deep=True)
        from wip_deploy.spec import AppRef
        d.spec.apps = [AppRef(name="react-console")]
        ctx = make_spec_context(d, real_discovery.components)
        env = resolve_all_env(d, real_discovery.components, real_discovery.apps, ctx)
        rc_env = env["react-console"].merged()
        from wip_deploy.config_gen import Literal
        wip_base = rc_env["WIP_BASE_URL"]
        assert isinstance(wip_base, Literal)
        assert wip_base.value == "http://wip-router:8080"

    def test_k8s_resolves_to_cluster_dns(
        self, k8s_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        from wip_deploy.config_gen import make_spec_context, resolve_all_env

        d = k8s_deployment.model_copy(deep=True)
        from wip_deploy.spec import AppRef
        d.spec.apps = [AppRef(name="react-console")]
        ctx = make_spec_context(d, real_discovery.components)
        env = resolve_all_env(d, real_discovery.components, real_discovery.apps, ctx)
        rc_env = env["react-console"].merged()
        from wip_deploy.config_gen import Literal
        wip_base = rc_env["WIP_BASE_URL"]
        assert isinstance(wip_base, Literal)
        # Cluster DNS form — no hardcoded target-specific string leaking.
        assert wip_base.value == "http://wip-router.wip.svc.cluster.local:8080"
