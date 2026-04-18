"""Tests for render_dev_simple — the dev-mode renderer.

Dev mode is compose-shaped output with three key differences:
  - `build:` contexts replace image pulls
  - Source mounts inject `components/<name>/src` into containers
  - Python services (uvicorn-based) get `--reload` for hot reload
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_dev_simple
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _dev_deployment(
    *,
    modules: list[str] | None = None,
    apps: list[str] | None = None,
    source_mount: bool = True,
) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="dev-test"),
        spec=DeploymentSpec(
            target="dev",
            modules={"optional": modules or ["mcp-server"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in (apps or [])],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="localhost"),
            images=ImagesSpec(tag="dev"),
            platform=PlatformSpec(
                dev=DevPlatform(mode="simple", source_mount=source_mount)
            ),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(
    tmp_path: Path, deployment: Deployment, discovery: Discovery
) -> ResolvedSecrets:
    return ensure_secrets(
        deployment,
        discovery.components,
        discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )


# ────────────────────────────────────────────────────────────────────
# Tree shape
# ────────────────────────────────────────────────────────────────────


class TestTreeShape:
    def test_emits_compose_file_set(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _dev_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        paths = {str(p) for p in tree.paths()}
        assert paths == {
            "docker-compose.yaml",
            ".env",
            "config/caddy/Caddyfile",
            "config/dex/config.yaml",
        }

    def test_env_file_is_0600(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _dev_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        assert tree.files[Path(".env")].mode == 0o600


# ────────────────────────────────────────────────────────────────────
# Build contexts
# ────────────────────────────────────────────────────────────────────


class TestBuildContext:
    def _compose_doc(
        self, tmp_path: Path, discovery: Discovery, **overrides: object
    ) -> dict:  # type: ignore[type-arg]
        d = _dev_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_dev_simple(
            d, discovery.components, discovery.apps, s, repo_root=REPO_ROOT,
        )
        return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)

    def test_python_service_gets_build_block(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._compose_doc(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        assert "build" in reg
        assert reg["build"]["context"].endswith("components/registry")
        # Still tags the image for tidy `podman images`
        assert reg["image"] == "registry:dev"

    def test_infrastructure_components_use_image_not_build(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """MongoDB has no build_context — keeps the upstream image pull."""
        doc = self._compose_doc(tmp_path, real_discovery)
        mongo = doc["services"]["mongodb"]
        assert "build" not in mongo
        assert mongo["image"] == "docker.io/library/mongo:7"


# ────────────────────────────────────────────────────────────────────
# Source mounts
# ────────────────────────────────────────────────────────────────────


class TestSourceMounts:
    def _compose_doc(
        self, tmp_path: Path, discovery: Discovery, **overrides: object
    ) -> dict:  # type: ignore[type-arg]
        d = _dev_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_dev_simple(
            d, discovery.components, discovery.apps, s, repo_root=REPO_ROOT,
        )
        return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)

    def test_python_service_gets_source_mount(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._compose_doc(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        mounts = reg.get("volumes", [])
        assert any(
            m.endswith("components/registry/src:/app/src:ro") for m in mounts
        )

    def test_source_mount_disabled_via_flag(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._compose_doc(tmp_path, real_discovery, source_mount=False)
        reg = doc["services"]["registry"]
        mounts = reg.get("volumes", [])
        assert not any(":/app/src" in m for m in mounts)

    def test_infrastructure_has_no_source_mount(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._compose_doc(tmp_path, real_discovery)
        mongo = doc["services"]["mongodb"]
        mounts = mongo.get("volumes", [])
        assert not any(":/app/src" in m for m in mounts)


# ────────────────────────────────────────────────────────────────────
# --reload
# ────────────────────────────────────────────────────────────────────


class TestHotReload:
    def _compose_doc(
        self, tmp_path: Path, discovery: Discovery, **overrides: object
    ) -> dict:  # type: ignore[type-arg]
        d = _dev_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_dev_simple(
            d, discovery.components, discovery.apps, s, repo_root=REPO_ROOT,
        )
        return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)

    def test_uvicorn_command_gets_reload(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._compose_doc(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        assert "--reload" in reg["command"]

    def test_non_uvicorn_command_unchanged(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Dex runs `dex serve ...` — no --reload appended."""
        doc = self._compose_doc(tmp_path, real_discovery)
        dex = doc["services"]["dex"]
        assert dex["command"][0] == "dex"
        assert "--reload" not in dex["command"]

    def test_mcp_server_python_module_unchanged(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """mcp-server uses `python -m wip_mcp --http` — not uvicorn-based,
        so no --reload (python -m has its own semantics)."""
        doc = self._compose_doc(
            tmp_path, real_discovery, modules=["mcp-server"]
        )
        mcp = doc["services"]["mcp-server"]
        assert mcp["command"][0] == "python"
        assert "--reload" not in mcp["command"]


# ────────────────────────────────────────────────────────────────────
# Target validation
# ────────────────────────────────────────────────────────────────────


class TestTargetValidation:
    def test_rejects_non_dev_target(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        from wip_deploy.spec import ComposePlatform
        d = Deployment(
            metadata=DeploymentMetadata(name="bad"),
            spec=DeploymentSpec(
                target="compose",
                modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
                apps=[],
                auth=AuthSpec(mode="oidc", gateway=True),
                network=NetworkSpec(hostname="localhost"),
                images=ImagesSpec(),
                platform=PlatformSpec(compose=ComposePlatform(data_dir=tmp_path)),
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
            ),
        )
        s = _secrets(tmp_path, d, real_discovery)
        with pytest.raises(ValueError, match="target=dev"):
            render_dev_simple(
                d, real_discovery.components, real_discovery.apps, s,
                repo_root=REPO_ROOT,
            )

    def test_rejects_tilt_mode(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _dev_deployment()
        d.spec.platform = PlatformSpec(dev=DevPlatform(mode="tilt"))
        s = _secrets(tmp_path, d, real_discovery)
        with pytest.raises(ValueError, match="simple"):
            render_dev_simple(
                d, real_discovery.components, real_discovery.apps, s,
                repo_root=REPO_ROOT,
            )
