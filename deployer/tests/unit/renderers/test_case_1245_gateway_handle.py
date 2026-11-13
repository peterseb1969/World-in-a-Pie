"""CASE-1245 item 4 — an app origin must not hand its session paths to a gateway it has no gateway.

Every app origin served with a gateway on answers `/auth/handoff`, `/auth/renew`,
`/auth/renewed`, `/auth/logout` and `/auth/userinfo` by proxying to
`wip-auth-gateway:4180`. That block was emitted unconditionally, on both the
per-app site and the k8s catch-all site — so in an install where
`auth.gateway=False` (api-key-only, or an explicit `--no-auth-gateway`) the render
pointed at a service it never emitted. `forward_auth` was already gated on the
gateway being on (`config_gen/routing.py`); the reserved-path handle was the
asymmetric half, and `CaddyConfig` had no field with which to tell.

Those paths stay reserved from the apps (`spec/app.py` reserves them because the
origin serves them), so with the gateway off they end in a 404 instead of falling
through to the app behind them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.config_gen.caddy import CaddyConfig, generate_caddy_config
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

from .test_compose import _secrets

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()

GATEWAY_PATHS = ("/auth/handoff", "/auth/renew", "/auth/renewed", "/auth/logout",
                "/auth/userinfo")


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _install(name: str = "t", *, gateway: bool, target: str = "compose") -> Deployment:
    platform = (
        PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d"))
        if target == "compose"
        else PlatformSpec(
            dev=DevPlatform(mode="simple", apps_from_registry=["react-console"])
        )
        if target == "dev"
        else PlatformSpec(k8s=K8sPlatform(namespace="wip"))
    )
    return Deployment(
        metadata=DeploymentMetadata(name=name),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            apps=[AppRef(name="react-console", enabled=True)],
            auth=(
                AuthSpec(mode="hybrid", gateway=True)
                if gateway
                else AuthSpec(mode="api-key-only", gateway=False)
            ),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _edge_caddyfile(
    tmp_path: Path, discovery: Discovery, *, gateway: bool, target: str
) -> str:
    d = _install(gateway=gateway, target=target)
    secrets = _secrets(tmp_path, d, discovery)
    if target == "compose":
        tree = render_compose(d, discovery.components, discovery.apps, secrets)
    else:
        tree = render_dev_simple(
            d, discovery.components, discovery.apps, secrets, repo_root=REPO_ROOT
        )
    return tree.files[Path("config/caddy/Caddyfile")].content


class TestGatewayOffLeavesNoDanglingUpstream:
    @pytest.mark.parametrize("target", ["compose", "dev"])
    def test_the_gateway_host_appears_nowhere_in_the_edge_caddyfile(
        self, tmp_path: Path, real_discovery: Discovery, target: str
    ) -> None:
        """Not just the app site: no emitted block may dial a service the render
        did not produce."""
        text = _edge_caddyfile(tmp_path, real_discovery, gateway=False, target=target)
        assert "wip-auth-gateway" not in text

    @pytest.mark.parametrize("target", ["compose", "dev"])
    def test_the_reserved_paths_are_reserved_and_answer_404(
        self, tmp_path: Path, real_discovery: Discovery, target: str
    ) -> None:
        text = _edge_caddyfile(tmp_path, real_discovery, gateway=False, target=target)
        matcher = f"@gateway path {' '.join(GATEWAY_PATHS)}"
        assert matcher in text, "the paths stay reserved from apps even with no gateway"
        assert "handle @gateway {\n        respond 404\n    }" in text


class TestGatewayOnIsUnchanged:
    @pytest.mark.parametrize("target", ["compose", "dev"])
    def test_the_reserved_paths_still_reach_the_gateway(
        self, tmp_path: Path, real_discovery: Discovery, target: str
    ) -> None:
        text = _edge_caddyfile(tmp_path, real_discovery, gateway=True, target=target)
        assert f"@gateway path {' '.join(GATEWAY_PATHS)}" in text
        assert "reverse_proxy wip-auth-gateway:4180" in text
        assert "handle @gateway {\n        respond 404" not in text


class TestTheK8sCatchAllSite:
    """The attached-apps site on k8s repeats the same handle against the
    Service FQDN, so it needs the same guard and is exercised here directly."""

    @staticmethod
    def _cfg(*, gateway_active: bool) -> CaddyConfig:
        return CaddyConfig(
            hostname="wip.example.org",
            https_port=443,
            tls_mode="external",
            gateway_service="wip-auth-gateway.wip.svc.cluster.local",
            gateway_port=4180,
            gateway_active=gateway_active,
            routes=[],
            root_redirect=None,
            plain_http_port=8080,
            forwarded_scheme="https",
            forwarded_host="wip.example.org",
            target="k8s",
            namespace="wip",
            edge_backend="wip-app-edge.wip.svc.cluster.local:8080",
        )

    def test_no_gateway_offers_no_upstream(self) -> None:
        text = render_caddyfile(self._cfg(gateway_active=False))
        assert "wip-auth-gateway" not in text
        assert "handle @gateway {\n        respond 404\n    }" in text

    def test_gateway_on_still_proxies_with_the_forwarded_host(self) -> None:
        text = render_caddyfile(self._cfg(gateway_active=True))
        assert (
            "reverse_proxy wip-auth-gateway.wip.svc.cluster.local:4180 {" in text
        )
        assert "header_up X-Forwarded-Host {host}" in text


class TestTheConfigAnswers:
    @pytest.mark.parametrize(
        ("target", "gateway"),
        [("compose", True), ("compose", False), ("k8s", True), ("k8s", False)],
    )
    def test_gateway_active_is_the_spec_answer(
        self, real_discovery: Discovery, target: str, gateway: bool
    ) -> None:
        d = _install(gateway=gateway, target=target)
        cfg = generate_caddy_config(d, real_discovery.components, real_discovery.apps)
        assert cfg.gateway_active is gateway

    def test_the_field_has_no_default(self) -> None:
        """Defaulting it would let a new call site inherit today's bug."""
        with pytest.raises(TypeError):
            CaddyConfig(  # type: ignore[call-arg]
                hostname="wip.local",
                https_port=8443,
                tls_mode="internal",
                gateway_service="wip-auth-gateway",
                gateway_port=4180,
                routes=[],
                root_redirect=None,
            )
