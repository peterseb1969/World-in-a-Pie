"""Tests for Deployment + nested spec models.

Focused on the load-bearing validators — the ones that would otherwise
surface as renderer bugs or silent mis-deploys.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from wip_deploy.spec import (
    ApplySpec,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    DexUser,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

# ────────────────────────────────────────────────────────────────────
# Auth
# ────────────────────────────────────────────────────────────────────


class TestAuthSpec:
    def test_oidc_with_gateway_is_valid(self) -> None:
        AuthSpec(mode="oidc", gateway=True)

    def test_api_key_only_without_gateway_is_valid(self) -> None:
        AuthSpec(mode="api-key-only", gateway=False, users=[])

    def test_api_key_only_with_gateway_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="gateway=True requires"):
            AuthSpec(mode="api-key-only", gateway=True)

    def test_oidc_without_users_rejected(self) -> None:
        with pytest.raises(
            PydanticValidationError, match="requires at least one Dex user"
        ):
            AuthSpec(mode="oidc", gateway=True, users=[])

    def test_default_users_are_admin_editor_viewer(self) -> None:
        auth = AuthSpec(mode="oidc", gateway=True)
        groups = {u.group for u in auth.users}
        assert groups == {"wip-admins", "wip-editors", "wip-viewers"}


# ────────────────────────────────────────────────────────────────────
# Network
# ────────────────────────────────────────────────────────────────────


class TestNetworkSpec:
    def test_internal_tls_works_with_localhost(self) -> None:
        NetworkSpec(hostname="localhost", tls="internal")

    def test_internal_tls_works_with_dot_local(self) -> None:
        NetworkSpec(hostname="wip-kubi.local", tls="internal")

    def test_letsencrypt_requires_public_hostname(self) -> None:
        with pytest.raises(
            PydanticValidationError, match="requires a public hostname"
        ):
            NetworkSpec(hostname="wip.local", tls="letsencrypt")

    def test_letsencrypt_accepts_public_hostname(self) -> None:
        NetworkSpec(hostname="wip.example.com", tls="letsencrypt")

    def test_letsencrypt_rejects_localhost(self) -> None:
        with pytest.raises(PydanticValidationError):
            NetworkSpec(hostname="localhost", tls="letsencrypt")


# ────────────────────────────────────────────────────────────────────
# Platform
# ────────────────────────────────────────────────────────────────────


class TestPlatformMatchesTarget:
    def _build(self, target: str, platform: PlatformSpec, **overrides: object) -> Deployment:
        return Deployment(
            metadata=DeploymentMetadata(name="t"),
            spec=DeploymentSpec(
                target=target,  # type: ignore[arg-type]
                auth=AuthSpec(mode="oidc", gateway=True),
                network=NetworkSpec(hostname="wip.local"),
                platform=platform,
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
                **overrides,  # type: ignore[arg-type]
            ),
        )

    def test_compose_with_compose_block_ok(self) -> None:
        self._build(
            "compose",
            PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        )

    def test_compose_without_compose_block_rejected(self) -> None:
        with pytest.raises(
            PydanticValidationError, match="requires platform.compose"
        ):
            self._build("compose", PlatformSpec(k8s=K8sPlatform()))

    def test_k8s_with_k8s_block_ok(self) -> None:
        # k8s-secret backend required when target=k8s per below; use file+k8s mix.
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
        assert d.spec.target == "k8s"

    def test_dev_with_dev_block_ok(self) -> None:
        self._build("dev", PlatformSpec(dev=DevPlatform()))

    def test_dev_without_dev_block_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="requires platform.dev"):
            self._build("dev", PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")))


# ────────────────────────────────────────────────────────────────────
# Secrets backend vs target
# ────────────────────────────────────────────────────────────────────


class TestSecretsBackendVsTarget:
    def test_k8s_secret_with_compose_rejected(self) -> None:
        with pytest.raises(
            PydanticValidationError, match="k8s-secret.*requires target='k8s'"
        ):
            Deployment(
                metadata=DeploymentMetadata(name="t"),
                spec=DeploymentSpec(
                    target="compose",
                    auth=AuthSpec(mode="oidc", gateway=True),
                    network=NetworkSpec(hostname="wip.local"),
                    platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
                    secrets=SecretsSpec(backend="k8s-secret"),
                ),
            )

    def test_file_backend_works_on_k8s(self) -> None:
        # Nothing forbids file backend on k8s (e.g., SOPS-in-file style).
        # Only k8s-secret backend is strictly tied to k8s target.
        Deployment(
            metadata=DeploymentMetadata(name="t"),
            spec=DeploymentSpec(
                target="k8s",
                auth=AuthSpec(mode="oidc", gateway=True),
                network=NetworkSpec(hostname="wip-kubi.local"),
                platform=PlatformSpec(k8s=K8sPlatform()),
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
            ),
        )


# ────────────────────────────────────────────────────────────────────
# Duplicate detection
# ────────────────────────────────────────────────────────────────────


class TestUniqueness:
    def test_duplicate_app_names_rejected(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        from wip_deploy.spec import AppRef

        d = minimal_compose_deployment.model_copy(deep=True)
        with pytest.raises(PydanticValidationError, match="duplicate app names"):
            d.spec.apps = [AppRef(name="dnd"), AppRef(name="dnd")]


# ────────────────────────────────────────────────────────────────────
# Defaults sanity
# ────────────────────────────────────────────────────────────────────


class TestDefaults:
    def test_apply_defaults_wait_true_300s_fail(self) -> None:
        apply = ApplySpec()
        assert apply.wait is True
        assert apply.timeout_seconds == 300
        assert apply.on_timeout == "fail"

    def test_deployment_default_kind_and_api_version(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        assert minimal_compose_deployment.api_version == "wip.dev/v1"
        assert minimal_compose_deployment.kind == "Deployment"


# ────────────────────────────────────────────────────────────────────
# Extra-field rejection (catches YAML typos)
# ────────────────────────────────────────────────────────────────────


class TestExtraFields:
    def test_unknown_field_on_network_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="Extra inputs"):
            NetworkSpec(hostname="wip.local", tls="internal", http_prot=8080)  # type: ignore[call-arg]

    def test_unknown_field_on_dex_user_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="Extra inputs"):
            DexUser(
                email="a@b.c",
                username="a",
                group="g",
                role="admin",  # type: ignore[call-arg]
            )
