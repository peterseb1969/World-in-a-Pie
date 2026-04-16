"""Tests for SpecContext + resolve_from_spec."""

from __future__ import annotations

import pytest

from wip_deploy.config_gen import make_spec_context, resolve_from_spec
from wip_deploy.config_gen.spec_context import (
    SpecContext,
    SpecContextAuth,
    SpecContextFeatures,
    SpecContextNetwork,
)
from wip_deploy.discovery import Discovery
from wip_deploy.spec import Deployment


class TestSpecContextComputation:
    def test_issuer_url_matches_hostname_and_port(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        ctx = make_spec_context(compose_deployment, real_discovery.components)
        # compose_deployment has hostname=wip.local, https_port=8443 (default)
        assert ctx.auth.issuer_url_public == "https://wip.local:8443/dex"
        assert ctx.auth.callback_url == "https://wip.local:8443/auth/callback"

    def test_internal_issuer_uses_dex_service_name(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        ctx = make_spec_context(compose_deployment, real_discovery.components)
        # Internal issuer is used by the auth-gateway server-to-server
        assert ctx.auth.issuer_url_internal == "http://wip-dex:5556/dex"

    def test_files_enabled_reflects_minio_activation(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        # minio is not in default modules.optional, so files disabled.
        ctx = make_spec_context(compose_deployment, real_discovery.components)
        assert ctx.features.files_enabled == "false"

        # Enable minio → files_enabled flips true.
        d2 = compose_deployment.model_copy(deep=True)
        d2.spec.modules.optional = ["minio"]
        ctx2 = make_spec_context(d2, real_discovery.components)
        assert ctx2.features.files_enabled == "true"

    def test_cors_origins_localhost_vs_hostname(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        ctx = make_spec_context(compose_deployment, real_discovery.components)
        # Non-localhost hostname → includes localhost fallback
        assert ctx.network.cors_origins == (
            "https://wip.local:8443,https://localhost:8443"
        )

    def test_cors_origins_localhost_only_when_hostname_localhost(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        d.spec.network.hostname = "localhost"
        ctx = make_spec_context(d, real_discovery.components)
        assert ctx.network.cors_origins == "https://localhost:8443"


class TestResolveFromSpec:
    def _simple_ctx(self) -> SpecContext:
        return SpecContext(
            network=SpecContextNetwork(
                hostname="h", cors_origins="c", internal_base_url="u"
            ),
            auth=SpecContextAuth(
                issuer_url_public="pub",
                issuer_url_internal="int",
                callback_url="cb",
            ),
            features=SpecContextFeatures(files_enabled="true"),
        )

    def test_nested_path(self) -> None:
        ctx = self._simple_ctx()
        assert resolve_from_spec("auth.issuer_url_public", ctx) == "pub"
        assert resolve_from_spec("network.hostname", ctx) == "h"
        assert resolve_from_spec("features.files_enabled", ctx) == "true"

    def test_missing_path_raises_keyerror(self) -> None:
        ctx = self._simple_ctx()
        with pytest.raises(KeyError, match="network.missing"):
            resolve_from_spec("network.missing", ctx)

    def test_unknown_top_section_raises(self) -> None:
        ctx = self._simple_ctx()
        with pytest.raises(KeyError):
            resolve_from_spec("nosuchsection.whatever", ctx)
