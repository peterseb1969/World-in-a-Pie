"""Renderer + spec coverage for CASE-373 Phase 1 — `external_ca_mount` slot.

Three layers:

  1. **Spec** — `NetworkSpec.external_ca_mount` defaults False, accepts
     True, round-trips through model_dump. Set by the install verb
     when `<install_dir>/secrets/external-ca.crt` exists (driven by a
     prior `wip-deploy import-bundle`).

  2. **Compose renderer** — when the flag is True, app containers get
     `NODE_EXTRA_CA_CERTS=/etc/ssl/certs/external-ca.crt` plus a
     `./secrets/external-ca.crt:/etc/ssl/certs/external-ca.crt:ro`
     bind-mount. Backend service containers (Component instances) do
     NOT receive these even when the flag is set — the slot is
     app-only because backends on the same host don't need cross-host
     trust.

  3. **Dev renderer** — same shape as compose. Same render path
     (`_service_block`-equivalent), same gate.

The install-time auto-flip (cli.py: `if (target_dir / "secrets" /
"external-ca.crt").exists(): set flag`) is exercised via a tmp-path
filesystem fixture in a separate end-to-end-ish test below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.cli import app
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

runner = CliRunner()
REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


# ──────────────────────────────────────────────────────────────────────
# Spec layer


class TestNetworkSpecExternalCAMount:
    def test_defaults_to_false(self) -> None:
        net = NetworkSpec(hostname="localhost")
        assert net.external_ca_mount is False

    def test_accepts_true(self) -> None:
        net = NetworkSpec(hostname="localhost", external_ca_mount=True)
        assert net.external_ca_mount is True

    def test_round_trips_through_model_dump(self) -> None:
        net = NetworkSpec(hostname="localhost", external_ca_mount=True)
        rehydrated = NetworkSpec.model_validate(net.model_dump())
        assert rehydrated.external_ca_mount is True


# ──────────────────────────────────────────────────────────────────────
# Renderer integration — drives `wip-deploy install --apps-only` so the
# real preset + manifest pipeline lights up. Switching the flag and
# inspecting the rendered docker-compose.yaml is the cleanest assertion
# surface (deeper than poking _service_block, less fragile than smoke).


def _render_apps_only(
    *,
    target: str,
    install_dir: Path,
    seed_ca: bool,
) -> Path:
    """Run `wip-deploy render --apps-only` against tmp install_dir.

    `render` is used (not `install`) because the test environment has
    no real containers to bring up — render writes the tree without
    preflight or apply, which is what we're asserting on.

    seed_ca=True writes a fake CA file before render — the verb's
    auto-detection then flips `network.external_ca_mount`.

    Returns the install_dir for inspection.
    """
    if seed_ca:
        secrets = install_dir / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        (secrets / "external-ca.crt").write_text(
            "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"
        )

    args = [
        "render",
        "--target", target,
        "--name", install_dir.name,
        "--output-dir", str(install_dir),
        "--apps-only",
        "--remote-wip", "https://wip.example",
        "--app", "react-console",
        "--app-from-registry", "react-console",
        "--registry", "ghcr.io/test",
        "--repo-root", str(REPO_ROOT),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, (
        f"render failed: exit={result.exit_code}\n"
        f"output:\n{result.output}"
    )
    return install_dir


def _read_compose(install_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((install_dir / "docker-compose.yaml").read_text())


@pytest.fixture
def deployment_with_ca() -> Deployment:
    """A deployment with external_ca_mount=True for direct renderer tests."""
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(
                hostname="localhost", tls="internal", external_ca_mount=True,
            ),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


@pytest.fixture
def deployment_without_ca() -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="localhost", tls="internal"),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Compose renderer


class TestComposeRendererInjection:
    """Direct `_service_block` test against minimal owners.

    The full install pipeline is exercised by the install-verb-driven
    tests further down; here we isolate the per-service injection
    decision so we can assert the App-vs-Component branch quickly.
    """

    def _build_app(self) -> object:
        from wip_deploy.spec.app import App, AppMetadata
        from wip_deploy.spec.component import (
            ComponentMetadata,
            ComponentSpec,
            ImageRef,
        )

        return App(
            metadata=ComponentMetadata(
                name="test-app",
                category="optional",
                description="test app",
            ),
            spec=ComponentSpec(image=ImageRef(name="test-app", tag="latest")),
            app_metadata=AppMetadata(
                display_name="Test", route_prefix="/apps/test"
            ),
        )

    def _build_component(self) -> object:
        from wip_deploy.spec.component import (
            Component,
            ComponentMetadata,
            ComponentSpec,
            ImageRef,
        )

        return Component(
            metadata=ComponentMetadata(
                name="backend-svc",
                category="core",
                description="backend service",
            ),
            spec=ComponentSpec(image=ImageRef(name="backend-svc", tag="latest")),
        )

    def _render(
        self, owner: object, deployment: Deployment, *, is_app: bool
    ) -> dict[str, Any]:
        from wip_deploy.config_gen import ResolvedEnv
        from wip_deploy.renderers.compose import _service_block

        empty_env = ResolvedEnv(required={}, optional={})
        return _service_block(
            owner,  # type: ignore[arg-type]
            deployment,
            empty_env,
            is_app=is_app,
            healthcheck_owners=set(),
            active_names=set(),
        )

    def test_app_gets_ca_env_when_flag_set(
        self, deployment_with_ca: Deployment
    ) -> None:
        block = self._render(self._build_app(), deployment_with_ca, is_app=True)
        assert (
            block["environment"]["NODE_EXTRA_CA_CERTS"]
            == "/etc/ssl/certs/external-ca.crt"
        )

    def test_app_gets_ca_volume_when_flag_set(
        self, deployment_with_ca: Deployment
    ) -> None:
        block = self._render(self._build_app(), deployment_with_ca, is_app=True)
        assert (
            "./secrets/external-ca.crt:/etc/ssl/certs/external-ca.crt:ro"
            in block["volumes"]
        )

    def test_app_no_injection_when_flag_unset(
        self, deployment_without_ca: Deployment
    ) -> None:
        block = self._render(self._build_app(), deployment_without_ca, is_app=True)
        assert "NODE_EXTRA_CA_CERTS" not in block.get("environment", {})
        assert all(
            "external-ca.crt" not in v for v in block.get("volumes", [])
        )

    def test_backend_component_never_gets_injection(
        self, deployment_with_ca: Deployment
    ) -> None:
        """Even when the flag is set, backend Components on the same
        host don't need cross-host trust. The slot is app-only."""
        block = self._render(
            self._build_component(), deployment_with_ca, is_app=False
        )
        assert "NODE_EXTRA_CA_CERTS" not in block.get("environment", {})
        assert all(
            "external-ca.crt" not in v for v in block.get("volumes", [])
        )


# ──────────────────────────────────────────────────────────────────────
# Dev renderer — exercises the same gate via dev's branch.


class TestDevRendererInjection:
    """Dev path is exercised via the install verb's --target dev flow.

    Calling `_dev_service_block` directly is brittle (signature
    couples to internal build-context plumbing). The install-verb
    path covers the same code branch with a stable surface.
    """

    def test_dev_app_gets_ca_env_when_flag_set(self, tmp_path: Path) -> None:
        install_dir = _render_apps_only(
            target="dev",
            install_dir=tmp_path / "dev-with-ca",
            seed_ca=True,
        )
        compose = _read_compose(install_dir)
        rc = compose["services"]["react-console"]
        env = rc.get("environment") or {}
        assert env.get("NODE_EXTRA_CA_CERTS") == "/etc/ssl/certs/external-ca.crt"
        volumes = rc.get("volumes") or []
        assert any("external-ca.crt" in v for v in volumes)


# ──────────────────────────────────────────────────────────────────────
# End-to-end via install verb — auto-detection of seed file flips the flag


class TestInstallAutoDetectsSeededCA:
    """The install verb checks for `secrets/external-ca.crt` in the
    target install dir and flips `network.external_ca_mount` to True
    when it's present. No CLI flag — the bundle-import is the trigger."""

    def test_seeded_ca_causes_app_injection(self, tmp_path: Path) -> None:
        install_dir = _render_apps_only(
            target="compose",
            install_dir=tmp_path / "with-ca",
            seed_ca=True,
        )
        compose = _read_compose(install_dir)
        services = compose["services"]
        # react-console is the only app in this test scaffold
        rc = services["react-console"]
        env = rc.get("environment") or {}
        assert env.get("NODE_EXTRA_CA_CERTS") == "/etc/ssl/certs/external-ca.crt"
        volumes = rc.get("volumes") or []
        assert any("external-ca.crt" in v for v in volumes)

    def test_no_seeded_ca_means_no_injection(self, tmp_path: Path) -> None:
        install_dir = _render_apps_only(
            target="compose",
            install_dir=tmp_path / "no-ca",
            seed_ca=False,
        )
        compose = _read_compose(install_dir)
        rc = compose["services"]["react-console"]
        env = rc.get("environment") or {}
        assert "NODE_EXTRA_CA_CERTS" not in env
        volumes = rc.get("volumes") or []
        assert all("external-ca.crt" not in v for v in volumes)
