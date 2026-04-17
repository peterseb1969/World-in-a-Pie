"""Tests for render_compose — the end-to-end compose renderer.

Tests take real manifests + a synthetic Deployment and verify the shape
of the rendered output. Uses yaml.safe_load to parse the result rather
than string matching, so small formatting changes don't break tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _minimal_compose(
    *,
    registry: str | None = "ghcr.io/peterseb1969",
    modules: list[str] | None = None,
    apps: list[str] | None = None,
) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": modules or ["console"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in (apps or [])],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry=registry, tag="v2.0.0"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
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
    def test_standard_emits_all_four_files(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)

        paths = {str(p) for p in tree.paths()}
        assert paths == {
            "docker-compose.yaml",
            ".env",
            "config/caddy/Caddyfile",
            "config/dex/config.yaml",
        }

    def test_api_key_only_omits_dex(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        d.spec.auth.gateway = False
        d.spec.auth.mode = "api-key-only"
        d.spec.auth.users = []
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        assert Path("config/dex/config.yaml") not in tree.files

    def test_env_file_has_0600_mode(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        assert tree.files[Path(".env")].mode == 0o600


# ────────────────────────────────────────────────────────────────────
# docker-compose.yaml shape
# ────────────────────────────────────────────────────────────────────


class TestComposeYaml:
    def _render_compose(
        self, tmp_path: Path, discovery: Discovery, **overrides: object
    ) -> dict:  # type: ignore[type-arg]
        d = _minimal_compose(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_compose(d, discovery.components, discovery.apps, s)
        return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)

    def test_caddy_is_always_included(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        assert "caddy" in doc["services"]

    def test_caddy_exposes_https_port(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        ports = doc["services"]["caddy"]["ports"]
        assert any("8443:8443" in p for p in ports)

    def test_registry_prefix_applied_to_wip_services(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(
            tmp_path, real_discovery, registry="ghcr.io/example"
        )
        reg = doc["services"]["registry"]
        assert reg["image"] == "ghcr.io/example/registry:v2.0.0"

    def test_fully_qualified_infrastructure_images_untouched(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        # mongodb uses `docker.io/library/mongo:7` regardless of spec.images
        assert doc["services"]["mongodb"]["image"] == "docker.io/library/mongo:7"
        # Dex similarly pins its own version
        assert doc["services"]["dex"]["image"] == "ghcr.io/dexidp/dex:v2.45.0"

    def test_inactive_components_absent(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        assert "postgres" not in doc["services"]
        assert "nats" not in doc["services"]
        assert "minio" not in doc["services"]
        assert "reporting-sync" not in doc["services"]

    def test_reporting_active_pulls_in_postgres_and_nats(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(
            tmp_path, real_discovery,
            modules=["console", "reporting-sync"],
        )
        assert "postgres" in doc["services"]
        assert "nats" in doc["services"]
        assert "reporting-sync" in doc["services"]

    def test_healthcheck_http_emits_cmd_shell_with_curl(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """HTTP probes render via CMD-SHELL so podman-compose's shell
        flattening doesn't break shell-metacharacter URLs. Default
        `probe: auto` emits a shell-chained curl-or-wget so images with
        either tool succeed."""
        doc = self._render_compose(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        test = reg["healthcheck"]["test"]
        assert test[0] == "CMD-SHELL"
        # Default `auto` probe: curl preferred, wget fallback.
        assert "curl -fsS" in test[1]
        assert "wget -qO-" in test[1]
        assert "http://localhost:8001/health" in test[1]

    def test_healthcheck_probe_curl_forces_curl_only(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Explicit `probe: curl` emits a curl-only probe (no wget
        fallback) — slightly smaller command for images known to
        ship curl."""
        # Override registry's probe to `curl`; render; assert.
        for c in real_discovery.components:
            if c.metadata.name == "registry" and c.spec.healthcheck:
                c.spec.healthcheck.probe = "curl"
        try:
            doc = self._render_compose(tmp_path, real_discovery)
            test = doc["services"]["registry"]["healthcheck"]["test"]
            assert "curl -fsS" in test[1]
            assert "wget" not in test[1]
        finally:
            for c in real_discovery.components:
                if c.metadata.name == "registry" and c.spec.healthcheck:
                    c.spec.healthcheck.probe = "auto"

    def test_healthcheck_probe_wget_forces_wget_only(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Explicit `probe: wget` emits a wget-only probe — for images
        that ship wget without curl (e.g., current v1.1.x apps)."""
        for c in real_discovery.components:
            if c.metadata.name == "registry" and c.spec.healthcheck:
                c.spec.healthcheck.probe = "wget"
        try:
            doc = self._render_compose(tmp_path, real_discovery)
            test = doc["services"]["registry"]["healthcheck"]["test"]
            assert "wget -qO-" in test[1]
            assert "curl" not in test[1]
        finally:
            for c in real_discovery.components:
                if c.metadata.name == "registry" and c.spec.healthcheck:
                    c.spec.healthcheck.probe = "auto"

    def test_healthcheck_command_emits_cmd_shell_quoted(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Command probes render CMD-SHELL with shlex-joined args so
        shell metacharacters (parens, quotes) are preserved."""
        doc = self._render_compose(tmp_path, real_discovery)
        mongo = doc["services"]["mongodb"]
        test = mongo["healthcheck"]["test"]
        assert test[0] == "CMD-SHELL"
        # mongosh --quiet --eval '...' — parens-safe via shlex quoting
        assert "mongosh" in test[1]
        assert "db.runCommand" in test[1]

    def test_env_secrets_go_via_env_file(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        # MASTER_API_KEY references a secret → ${API_KEY} interpolation
        assert reg["environment"]["MASTER_API_KEY"] == "${API_KEY}"
        assert reg["env_file"] == [".env"]

    def test_env_literals_go_inline(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        # DATABASE_NAME is a literal, not a secret
        assert reg["environment"]["DATABASE_NAME"] == "wip_registry"

    def test_depends_on_uses_service_healthy_when_dep_has_healthcheck(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        reg = doc["services"]["registry"]
        # mongodb has a healthcheck → service_healthy
        assert reg["depends_on"]["mongodb"] == {"condition": "service_healthy"}

    def test_depends_on_falls_back_to_service_started_for_no_healthcheck_dep(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Dex has no healthcheck (distroless). Depending on it must
        use service_started, otherwise compose hangs forever."""
        doc = self._render_compose(tmp_path, real_discovery)
        gateway = doc["services"]["auth-gateway"]
        assert gateway["depends_on"]["dex"] == {"condition": "service_started"}

    def test_network_declared(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        assert "wip-network" in doc["networks"]

    def test_volumes_for_stateful_services(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery)
        # MongoDB has storage named "data"
        assert "wip-mongodb-data" in doc["volumes"]

    def test_apps_contribute_services(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = self._render_compose(tmp_path, real_discovery, apps=["dnd"])
        assert "dnd" in doc["services"]

    def test_optional_from_secret_skipped_when_secret_not_collected(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """react-console's ANTHROPIC_API_KEY is optional + `from_secret:
        anthropic-api-key`. When no anthropic-api-key is collected (the
        user didn't supply one), the env var must be omitted — not
        emitted as ${ANTHROPIC_API_KEY} → empty string."""
        doc = self._render_compose(
            tmp_path, real_discovery, apps=["react-console"]
        )
        rc = doc["services"]["react-console"]
        env = rc.get("environment", {})
        # The optional anthropic-api-key isn't collected by default, so
        # the env var should not appear at all.
        assert "ANTHROPIC_API_KEY" not in env


# ────────────────────────────────────────────────────────────────────
# .env
# ────────────────────────────────────────────────────────────────────


class TestDotEnv:
    def test_every_secret_appears_as_shell_var(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        env_content = tree.files[Path(".env")].content

        for name in s.values:
            shell_var = name.upper().replace("-", "_")
            assert f"{shell_var}=" in env_content


# ────────────────────────────────────────────────────────────────────
# Dex config
# ────────────────────────────────────────────────────────────────────


class TestDexRender:
    def test_dex_config_parses_as_yaml(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        dex_yaml = yaml.safe_load(
            tree.files[Path("config/dex/config.yaml")].content
        )
        assert dex_yaml["issuer"] == "https://wip.local:8443/dex"
        assert dex_yaml["storage"]["type"] == "sqlite3"

    def test_dex_users_have_bcrypt_hashes(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        dex_yaml = yaml.safe_load(
            tree.files[Path("config/dex/config.yaml")].content
        )
        for user in dex_yaml["staticPasswords"]:
            # bcrypt hashes start with $2a$, $2b$, or $2y$
            assert user["hash"].startswith(("$2a$", "$2b$", "$2y$"))


# ────────────────────────────────────────────────────────────────────
# Caddyfile
# ────────────────────────────────────────────────────────────────────


class TestCaddyfile:
    def test_api_routes_have_no_forward_auth(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """API routes use API-key auth (service-level). No gateway
        forward_auth — that's only for browser-facing app routes."""
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        caddyfile = tree.files[Path("config/caddy/Caddyfile")].content

        assert "/api/registry/*" in caddyfile
        registry_block_start = caddyfile.index("handle /api/registry/*")
        registry_block = caddyfile[registry_block_start : registry_block_start + 300]
        assert "forward_auth" not in registry_block

    def test_app_routes_have_forward_auth(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """App routes (/apps/*) are gateway-protected."""
        d = _minimal_compose(apps=["react-console"])
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        caddyfile = tree.files[Path("config/caddy/Caddyfile")].content

        rc_start = caddyfile.index("handle /apps/rc/*")
        rc_block = caddyfile[rc_start : rc_start + 300]
        assert "forward_auth wip-auth-gateway:4180" in rc_block

    def test_streaming_route_sets_flush_interval(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        caddyfile = tree.files[Path("config/caddy/Caddyfile")].content

        # document-store is streaming
        ds_start = caddyfile.index("handle /api/document-store/*")
        ds_block = caddyfile[ds_start : ds_start + 300]
        assert "flush_interval -1" in ds_block

    def test_console_catchall_last(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _minimal_compose()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
        caddyfile = tree.files[Path("config/caddy/Caddyfile")].content
        assert "reverse_proxy wip-console:80" in caddyfile


# ────────────────────────────────────────────────────────────────────
# Target check
# ────────────────────────────────────────────────────────────────────


class TestTargetGuard:
    def test_render_compose_rejects_k8s_target(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = Deployment(
            metadata=DeploymentMetadata(name="t"),
            spec=DeploymentSpec(
                target="k8s",
                auth=AuthSpec(mode="oidc", gateway=True),
                network=NetworkSpec(hostname="wip-kubi.local"),
                platform=PlatformSpec(k8s=K8sPlatform()),
                secrets=SecretsSpec(backend="k8s-secret"),
            ),
        )
        with pytest.raises(ValueError, match="target=compose"):
            render_compose(d, real_discovery.components, real_discovery.apps, ResolvedSecrets({}))
