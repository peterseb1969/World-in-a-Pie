"""Tests for route resolution — the heart of the cross-target auth
invariant."""

from __future__ import annotations

from wip_deploy.config_gen import resolve_routes
from wip_deploy.discovery import Discovery
from wip_deploy.spec import Deployment


class TestActiveComponentsContributeRoutes:
    def test_standard_deployment_includes_api_routes(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        paths = {r.path for r in routes}
        assert "/api/registry" in paths
        assert "/api/document-store" in paths
        assert "/api/template-store" in paths

    def test_inactive_component_routes_omitted(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        # reporting-sync not active in standard → its /api/reporting-sync
        # route must not appear.
        paths = {r.path for r in routes}
        assert "/api/reporting-sync" not in paths
        assert "/api/ingest-gateway" not in paths


class TestAppRoutes:
    def test_enabled_app_routes_appear(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        from wip_deploy.spec import AppRef

        d.spec.apps = [AppRef(name="dnd")]

        routes = resolve_routes(d, real_discovery.components, real_discovery.apps)
        paths = {r.path for r in routes}
        assert "/apps/dnd" in paths

    def test_disabled_app_routes_omitted(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        from wip_deploy.spec import AppRef

        d.spec.apps = [AppRef(name="dnd", enabled=False)]

        routes = resolve_routes(d, real_discovery.components, real_discovery.apps)
        paths = {r.path for r in routes}
        assert "/apps/dnd" not in paths


class TestAuthProtection:
    def test_api_routes_are_not_gateway_protected(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """API routes have auth_required=False — backend services handle
        API-key auth themselves. The gateway only protects browser-facing
        app routes."""
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        api_registry = next(r for r in routes if r.path == "/api/registry")
        assert api_registry.auth_protected is False

    def test_app_routes_are_gateway_protected(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        from wip_deploy.spec import AppRef
        d = compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="react-console")]
        routes = resolve_routes(d, real_discovery.components, real_discovery.apps)
        rc = next(r for r in routes if r.path == "/apps/rc")
        assert rc.auth_protected is True

    def test_gateway_off_disables_all_protection(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False

        routes = resolve_routes(d, real_discovery.components, real_discovery.apps)
        assert all(r.auth_protected is False for r in routes)

    def test_console_root_is_not_auth_protected(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        console_root = next((r for r in routes if r.backend_component == "console"), None)
        assert console_root is not None
        # Console route manifest has auth_required=False
        assert console_root.auth_protected is False


class TestBackendPortResolution:
    def test_api_routes_point_at_service_http_port(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        by_component = {r.backend_component: r for r in routes}
        assert by_component["registry"].backend_port == 8001
        assert by_component["def-store"].backend_port == 8002
        assert by_component["template-store"].backend_port == 8003
        assert by_component["document-store"].backend_port == 8004


class TestStreaming:
    def test_document_store_route_is_streaming(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        ds = next(r for r in routes if r.path == "/api/document-store")
        assert ds.streaming is True

    def test_non_streaming_routes(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        routes = resolve_routes(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        reg = next(r for r in routes if r.path == "/api/registry")
        assert reg.streaming is False
