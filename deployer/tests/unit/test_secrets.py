"""Tests for the secrets orchestrator.

The collection rules are the load-bearing part — getting them wrong
means either secrets get generated when they shouldn't (noise on disk)
or needed secrets are missing (renders fail).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.discovery import Discovery, discover
from wip_deploy.secrets import collect_required_secrets, ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _compose_deployment(
    *,
    modules: list[str] | None = None,
    apps: list[str] | None = None,
    auth_mode: str = "oidc",
    auth_gateway: bool = True,
) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": modules or ["mcp-server"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in (apps or [])],
            auth=AuthSpec(
                mode=auth_mode,  # type: ignore[arg-type]
                gateway=auth_gateway,
                users=[] if auth_mode == "api-key-only" else None,  # type: ignore[arg-type]
            )
            if auth_mode == "api-key-only"
            else AuthSpec(mode=auth_mode, gateway=auth_gateway),  # type: ignore[arg-type]
            network=NetworkSpec(hostname="wip.local"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


# ────────────────────────────────────────────────────────────────────
# Collection rules
# ────────────────────────────────────────────────────────────────────


class TestCollectRequiredSecrets:
    def test_standard_compose_collects_core_secrets(
        self, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )

        # API key is referenced by every core service
        assert "api-key" in names

        # Dex users: admin, editor, viewer
        assert "dex-password-admin" in names
        assert "dex-password-editor" in names
        assert "dex-password-viewer" in names

        # Dex clients: auth-gateway's wip-gateway client is the canonical one
        assert "dex-client-wip-gateway" in names

        # Gateway's session secret is its own
        assert "gateway-session-secret" in names

    def test_inactive_component_secrets_not_collected(
        self, real_discovery: Discovery
    ) -> None:
        """reporting-sync's POSTGRES_PASSWORD secret must not be in the
        set unless reporting-sync is active."""
        d = _compose_deployment()  # reporting-sync not active
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert "postgres-password" not in names

    def test_reporting_sync_active_pulls_postgres_password(
        self, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment(modules=["reporting-sync"])
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert "postgres-password" in names

    def test_api_key_only_skips_dex_secrets(
        self, real_discovery: Discovery
    ) -> None:
        """api-key-only mode → no Dex → no dex-* secrets."""
        d = _compose_deployment(auth_mode="api-key-only", auth_gateway=False)
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert not any(n.startswith("dex-") for n in names)

    def test_gateway_off_removes_gateway_and_its_secrets(
        self, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment(auth_gateway=False)
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        # auth-gateway component goes inactive → its session secret drops
        assert "gateway-session-secret" not in names
        # And its Dex client
        assert "dex-client-wip-gateway" not in names

    def test_enabled_app_with_oidc_adds_its_client_secret(
        self, real_discovery: Discovery
    ) -> None:
        """dnd app declares an oidc_client → its client secret joins."""
        d = _compose_deployment(apps=["dnd"])
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert "dex-client-dnd" in names

    def test_disabled_app_excluded_from_required_env(
        self, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        d.spec.apps = [AppRef(name="dnd", enabled=False)]
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        # dnd's Dex client must not appear
        assert "dex-client-dnd" not in names


# ────────────────────────────────────────────────────────────────────
# End-to-end ensure_secrets
# ────────────────────────────────────────────────────────────────────


class TestEnsureSecrets:
    def test_ensure_generates_every_collected_secret(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        backend = FileSecretBackend(tmp_path / "secrets")

        resolved = ensure_secrets(
            d, real_discovery.components, real_discovery.apps, backend
        )

        required = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert set(resolved.values.keys()) == required
        # Every value looks like our default generator (URL-safe, ≥ 20 chars)
        for name, value in resolved.values.items():
            assert len(value) >= 20, f"{name}: {value!r}"

    def test_reinstall_preserves_values(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """The single most important lifecycle property: re-running
        ensure_secrets on an existing install reads back the same
        values it generated the first time."""
        d = _compose_deployment()
        dir_ = tmp_path / "secrets"

        r1 = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )

        r2 = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )

        assert r1.values == r2.values

    def test_resolved_secrets_get_and_try_get(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        resolved = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(tmp_path / "s"),
        )

        assert resolved.get("api-key")
        assert resolved.try_get("never-existed") is None
        with pytest.raises(KeyError):
            resolved.get("never-existed")

    def test_added_module_generates_only_new_secrets(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Starting small and adding modules shouldn't regenerate
        unrelated existing secrets."""
        dir_ = tmp_path / "secrets"

        # First install: standard-like
        d1 = _compose_deployment()
        r1 = ensure_secrets(
            d1, real_discovery.components, real_discovery.apps, FileSecretBackend(dir_)
        )

        # Second install: add reporting-sync (introduces postgres-password)
        d2 = _compose_deployment(modules=["reporting-sync"])
        r2 = ensure_secrets(
            d2, real_discovery.components, real_discovery.apps, FileSecretBackend(dir_)
        )

        # Every name that was in r1 must still be the same in r2.
        for name, value in r1.values.items():
            assert r2.values[name] == value
        # And postgres-password is new.
        assert "postgres-password" in r2.values
        assert "postgres-password" not in r1.values


# ────────────────────────────────────────────────────────────────────
# CASE-295: derived bcrypt secrets for dex passwords (deterministic renders)
# ────────────────────────────────────────────────────────────────────


class TestBcryptDerivedSecrets:
    def test_collect_pairs_each_dex_password_with_bcrypt(
        self, real_discovery: Discovery
    ) -> None:
        """Every collected dex user password gets a paired `.bcrypt`
        derived secret. Without this, the renderer would re-hash on
        every render and `wip-deploy status --diff` would always
        report drift on the dex-config ConfigMap."""
        d = _compose_deployment()
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )

        for user in ("admin", "editor", "viewer"):
            plaintext_name = f"dex-password-{user}"
            assert plaintext_name in names
            assert f"{plaintext_name}.bcrypt" in names

    def test_ensure_caches_bcrypt_so_subsequent_renders_match(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Two installs into the same secret backend produce identical
        `.bcrypt` values — proves the hash is generated once and reused.

        Without the cache, bcrypt.gensalt() would emit different hashes
        on each call (intentional bcrypt behaviour for password storage,
        but disastrous for render determinism)."""
        import bcrypt

        dir_ = tmp_path / "secrets"
        d = _compose_deployment()

        r1 = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )
        r2 = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )

        admin_bcrypt = r1.get("dex-password-admin.bcrypt")
        assert r2.get("dex-password-admin.bcrypt") == admin_bcrypt
        # And the cached hash actually verifies against the plaintext.
        assert bcrypt.checkpw(
            r1.get("dex-password-admin").encode(),
            admin_bcrypt.encode(),
        )

    def test_bcrypt_persists_to_file_backend(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """The `.bcrypt` derived secret is written to disk like any
        other — recoverable across processes, not just in-memory."""
        dir_ = tmp_path / "secrets"
        d = _compose_deployment()

        ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )

        # Inspect the on-disk layout directly — bypass the backend.
        bcrypt_file = dir_ / "dex-password-admin.bcrypt"
        assert bcrypt_file.exists()
        contents = bcrypt_file.read_text()
        # bcrypt hashes start with $2b$ or $2a$
        assert contents.startswith("$2"), f"unexpected hash format: {contents!r}"

    def test_dex_config_render_is_deterministic(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Render the dex config twice with the same secrets backend
        and assert byte-identical output. This is the contract that
        `wip-deploy status --diff` (CASE-294) depends on."""
        from wip_deploy.config_gen.dex import generate_dex_config
        from wip_deploy.renderers.compose_dex import render_dex_config

        dir_ = tmp_path / "secrets"
        d = _compose_deployment()

        s1 = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )
        s2 = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(dir_),
        )

        dex_cfg = generate_dex_config(
            d, real_discovery.components, real_discovery.apps
        )
        assert dex_cfg is not None
        out1 = render_dex_config(dex_cfg, s1)
        out2 = render_dex_config(dex_cfg, s2)
        assert out1 == out2
