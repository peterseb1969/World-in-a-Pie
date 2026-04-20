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
    app_sources: dict[str, Path] | None = None,
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
                dev=DevPlatform(
                    mode="simple",
                    source_mount=source_mount,
                    app_sources=app_sources or {},
                )
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
        # Core compose file set — materialized build-contexts/* paths
        # live alongside but we don't enumerate them here (tests for
        # the bake behavior are under TestBuildContext).
        assert {
            "docker-compose.yaml",
            ".env",
            "config/caddy/Caddyfile",
            "config/dex/config.yaml",
        } <= paths

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
        # Auth services now get a materialized per-component build
        # context under ./build-contexts/<name>/ so wip-auth gets baked
        # into the dev image (mirrors production build-release.sh).
        assert reg["build"]["context"] == "./build-contexts/registry"
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

    def test_auth_service_build_context_bakes_wip_auth(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Services that import wip_auth need it baked into the dev
        image. The materialized build context under
        build-contexts/<name>/ contains the wip-auth source, and the
        patched Dockerfile installs it."""
        d = _dev_deployment(modules=["reporting-sync"])
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        paths = {str(p) for p in tree.paths()}
        # wip-auth source materialized under the service's build context
        assert "build-contexts/registry/wip-auth/pyproject.toml" in paths
        # Dockerfile patched with wip-auth install
        dockerfile = tree.files[Path("build-contexts/registry/Dockerfile")].content
        assert "COPY wip-auth /tmp/wip-auth" in dockerfile
        assert "pip install --no-cache-dir /tmp/wip-auth" in dockerfile

    def test_router_caddyfile_rendered_and_mounted(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """wip-router needs its generated Caddyfile written to the tree
        AND bind-mounted into the container. Without either, wip-router
        boots with the stock caddy default Caddyfile (listens on :80,
        serves static files) and every /api/* request returns 502.
        """
        d = _dev_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        paths = {str(p) for p in tree.paths()}
        assert "config/router/Caddyfile" in paths

        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        router = doc["services"]["router"]
        mounts = router.get("volumes", [])
        assert any(
            m.endswith("config/router/Caddyfile:/etc/caddy/Caddyfile:ro")
            for m in mounts
        ), f"router has no Caddyfile mount: {mounts}"

    def test_app_without_source_override_uses_registry_image(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Default behavior: apps use their registry image, no build block.
        Backward compatibility — existing dev installs keep working."""
        d = _dev_deployment(apps=["react-console"])
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert "build" not in rc
        assert "image" in rc

    def test_app_source_override_emits_build_and_mount(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-55: --app-source NAME=PATH directs the dev renderer to
        build the app locally from PATH + mount PATH at /app for live
        code reflection.
        """
        fake_app = tmp_path / "fake-rc-checkout"
        fake_app.mkdir()
        (fake_app / "Dockerfile").write_text("FROM node:20\n")
        d = _dev_deployment(
            apps=["react-console"],
            app_sources={"react-console": fake_app},
        )
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert "build" in rc
        assert rc["build"]["context"] == str(fake_app)
        # No Dockerfile.dev in this fake repo → fallback to default.
        assert "dockerfile" not in rc["build"]
        # Source mount of the app dir so edits on host reflect in container.
        mounts = rc.get("volumes", [])
        assert any(m.startswith(f"{fake_app}:/app") for m in mounts), mounts

    def test_app_source_override_adds_node_modules_shadow(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-55 follow-up: --app-source must add a named
        node_modules volume shadowing /app/node_modules so host-installed
        node_modules (wrong platform on Mac) don't leak into the
        container, and the container's own install doesn't clobber
        the host's. Named (not anonymous) because anonymous volumes
        are scoped to `podman-compose run --rm` containers — a
        one-off npm ci into the service container via `run` wouldn't
        persist to the main service container."""
        fake_app = tmp_path / "fake-rc-checkout-nodemods"
        fake_app.mkdir()
        (fake_app / "Dockerfile").write_text("FROM node:20\n")
        d = _dev_deployment(
            apps=["react-console"],
            app_sources={"react-console": fake_app},
        )
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        mounts = rc.get("volumes", [])
        assert f"{fake_app}:/app:rw" in mounts
        assert "react-console-node-modules:/app/node_modules" in mounts
        # And the named volume must be declared at the top level.
        assert "react-console-node-modules" in doc.get("volumes", {})

    def test_app_source_override_routes_to_dev_port(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-55 follow-up: when an app manifest declares a `dev`
        port and --app-source is set for that app, Caddy should route
        /apps/<name>/* to the dev port (Vite on 5174 for RC) instead
        of the prod port (3011 for RC). Without this, browser hits
        Express which has nothing to serve in dev mode.
        """
        fake_app = tmp_path / "fake-rc-checkout-dev-port"
        fake_app.mkdir()
        (fake_app / "Dockerfile").write_text("FROM node:20\n")
        d = _dev_deployment(
            apps=["react-console"],
            app_sources={"react-console": fake_app},
        )
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        caddyfile = tree.files[Path("config/caddy/Caddyfile")].content
        # Route block for /apps/rc/* must target the dev port (5174),
        # not the prod :3011.
        rc_idx = caddyfile.index("handle /apps/rc/*")
        rc_block = caddyfile[rc_idx: rc_idx + 600]
        assert "reverse_proxy wip-react-console:5174" in rc_block, rc_block
        assert "wip-react-console:3011" not in rc_block, rc_block

    def test_no_override_uses_prod_port_even_with_dev_port_declared(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Backward-compat: declaring a `dev` port in the manifest
        must NOT affect routing when --app-source is absent. Default
        behavior stays on the prod port."""
        d = _dev_deployment(apps=["react-console"])  # no app_sources
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        caddyfile = tree.files[Path("config/caddy/Caddyfile")].content
        rc_idx = caddyfile.index("handle /apps/rc/*")
        rc_block = caddyfile[rc_idx: rc_idx + 600]
        assert "reverse_proxy wip-react-console:3011" in rc_block

    def test_app_source_override_forces_node_env_development(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-55 follow-up: manifests can declare NODE_ENV=production
        as a literal (correct for prod images), but with --app-source the
        container runs `npm run dev` and needs devDependencies. The
        renderer must override NODE_ENV to development so `npm ci` inside
        the container installs dev deps (vite, concurrently, etc.)."""
        fake_app = tmp_path / "fake-rc-checkout-node-env"
        fake_app.mkdir()
        (fake_app / "Dockerfile").write_text("FROM node:20\n")
        d = _dev_deployment(
            apps=["react-console"],
            app_sources={"react-console": fake_app},
        )
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert rc["environment"]["NODE_ENV"] == "development"

    def test_app_source_override_mirrors_vite_base_path(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-55 follow-up: apps in --app-source should get
        VITE_BASE_PATH set to APP_BASE_PATH so Vite's `base` and proxy
        prefixes match the Caddy-exposed path. Without this, Vite
        renders assets at bare paths (/@vite/client) instead of
        /apps/rc/@vite/client, and Caddy 404s them."""
        fake_app = tmp_path / "fake-rc-checkout-vite-base"
        fake_app.mkdir()
        (fake_app / "Dockerfile").write_text("FROM node:20\n")
        d = _dev_deployment(
            apps=["react-console"],
            app_sources={"react-console": fake_app},
        )
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert rc["environment"]["APP_BASE_PATH"] == "/apps/rc"
        assert rc["environment"]["VITE_BASE_PATH"] == "/apps/rc"

    def test_no_override_does_not_add_vite_base_path(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Without --app-source, the renderer must not inject
        VITE_BASE_PATH — the registry image for the app bakes its own
        base path, and adding one could conflict."""
        d = _dev_deployment(apps=["react-console"])  # no app_sources
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert "VITE_BASE_PATH" not in rc["environment"]

    def test_no_override_preserves_manifest_node_env(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Without --app-source, the manifest's NODE_ENV literal is
        preserved (production for RC)."""
        d = _dev_deployment(apps=["react-console"])  # no app_sources
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert rc["environment"]["NODE_ENV"] == "production"

    def test_app_source_override_prefers_dockerfile_dev_when_present(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-55: when <PATH>/Dockerfile.dev exists, the renderer uses
        it instead of Dockerfile. Lets the app define its own dev-mode
        command (e.g., `npm run dev`) without baking that into the
        renderer."""
        fake_app = tmp_path / "fake-rc-checkout-with-dockerfile-dev"
        fake_app.mkdir()
        (fake_app / "Dockerfile").write_text("FROM node:20\n")
        (fake_app / "Dockerfile.dev").write_text("FROM node:20\nCMD [\"npm\",\"run\",\"dev\"]\n")
        d = _dev_deployment(
            apps=["react-console"],
            app_sources={"react-console": fake_app},
        )
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        rc = doc["services"]["react-console"]
        assert rc["build"]["dockerfile"] == "Dockerfile.dev"

    def test_document_store_bakes_wip_toolkit_in_addition(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """document-store uses wip-toolkit (BackupEngine) in addition
        to wip-auth — both need to bake in."""
        d = _dev_deployment(modules=["reporting-sync"])
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d, real_discovery.components, real_discovery.apps, s,
            repo_root=REPO_ROOT,
        )
        dockerfile = tree.files[Path("build-contexts/document-store/Dockerfile")].content
        assert "COPY wip-toolkit /tmp/wip-toolkit" in dockerfile
        assert "pip install --no-cache-dir /tmp/wip-toolkit" in dockerfile


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
        # entrypoint holds the binary, command holds args incl. --reload.
        assert reg["entrypoint"] == ["uvicorn"]
        assert "--reload" in reg["command"]

    def test_non_uvicorn_command_unchanged(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Dex runs `dex serve ...` — no --reload appended."""
        doc = self._compose_doc(tmp_path, real_discovery)
        dex = doc["services"]["dex"]
        assert dex["entrypoint"] == ["dex"]
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
        assert mcp["entrypoint"] == ["python"]
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
