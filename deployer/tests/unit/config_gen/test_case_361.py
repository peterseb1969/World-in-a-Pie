"""Regression coverage for CASE-361 — WIP_AUTH_MODE from spec.

Before CASE-361, `deployment.spec.auth.mode` only controlled whether the
auth-gateway was in the request path. Backend services running wip-auth
defaulted to `api_key_only` regardless of spec.auth.mode, making
oidc/hybrid bearer flows silently inert.

After CASE-361, every backend service that uses wip-auth (registry,
def-store, template-store, document-store, reporting-sync) reads
`WIP_AUTH_MODE` from `from_spec: auth.wip_auth_mode`, which maps:

    spec.auth.mode      → WIP_AUTH_MODE
    --------------------|-------------------
    api-key-only        → api_key_only
    oidc                → jwt_only
    hybrid              → dual

Plus `WIP_AUTH_JWT_ISSUER_URL` flows from
`from_spec: auth.issuer_url_internal` so jwt_only/dual modes have an
issuer to validate against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.config_gen import (
    Literal,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.discovery import Discovery
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import Deployment

# Services that import wip_auth — must carry WIP_AUTH_MODE after CASE-361.
_AUTH_SERVICES = frozenset({
    "registry",
    "def-store",
    "template-store",
    "document-store",
    "reporting-sync",
})


def _secrets_for(
    tmp_path: Path, deployment: Deployment, discovery: Discovery
) -> ResolvedSecrets:
    """Same pattern as test_compose.py — collect whatever the deployment
    actually needs against a tmp file backend, so the test doesn't have
    to enumerate the secret-name list and stay in sync with manifests."""
    return ensure_secrets(
        deployment,
        discovery.components,
        discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )


# ────────────────────────────────────────────────────────────────────
# SpecContext computation
# ────────────────────────────────────────────────────────────────────


class TestSpecContextAuthMode:
    @pytest.mark.parametrize(
        ("spec_mode", "spec_gateway", "expected_wip_mode"),
        [
            # api-key-only is incompatible with gateway=True (AuthSpec
            # validator enforces). Set gateway=False BEFORE mode to avoid
            # the per-field validate_assignment tripping.
            ("api-key-only", False, "api_key_only"),
            ("oidc", True, "jwt_only"),
            ("hybrid", True, "dual"),
        ],
    )
    def test_mode_maps_correctly(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        spec_mode: str,
        spec_gateway: bool,
        expected_wip_mode: str,
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        # Order matters — gateway=False must land before mode flips to
        # api-key-only, otherwise gateway_requires_oidc validator fires.
        d.spec.auth.gateway = spec_gateway
        d.spec.auth.mode = spec_mode  # type: ignore[assignment]
        ctx = make_spec_context(d, real_discovery.components)
        assert ctx.auth.wip_auth_mode == expected_wip_mode

    def test_default_compose_deployment_uses_jwt_only(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """The standard fixture is auth.mode=oidc, so wip_auth_mode is
        jwt_only — the most common case for an OIDC-gated install."""
        ctx = make_spec_context(compose_deployment, real_discovery.components)
        assert compose_deployment.spec.auth.mode == "oidc"
        assert ctx.auth.wip_auth_mode == "jwt_only"

    def test_k8s_target_uses_same_mapping(
        self,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """The mode mapping is target-agnostic — same auth.mode produces
        the same WIP_AUTH_MODE regardless of compose/k8s."""
        ctx = make_spec_context(k8s_deployment, real_discovery.components)
        assert k8s_deployment.spec.auth.mode == "oidc"
        assert ctx.auth.wip_auth_mode == "jwt_only"


# ────────────────────────────────────────────────────────────────────
# Env injection — every backend service that uses wip-auth must carry
# WIP_AUTH_MODE in its required env, and WIP_AUTH_JWT_ISSUER_URL in
# its optional env.
# ────────────────────────────────────────────────────────────────────


class TestEnvInjection:
    def test_every_auth_service_has_wip_auth_mode_required(
        self,
        maximal_compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Without this, the spec.auth.mode value never reaches the
        services — the gap CASE-361 names."""
        ctx = make_spec_context(
            maximal_compose_deployment, real_discovery.components
        )
        resolved = resolve_all_env(
            maximal_compose_deployment,
            real_discovery.components,
            real_discovery.apps,
            ctx,
        )
        for svc in _AUTH_SERVICES:
            assert svc in resolved, (
                f"service {svc!r} not in resolved env — manifest discovery gap"
            )
            assert "WIP_AUTH_MODE" in resolved[svc].required, (
                f"service {svc!r} missing WIP_AUTH_MODE in required env — "
                f"CASE-361 contract"
            )

    def test_every_auth_service_has_wip_auth_jwt_issuer_url_optional(
        self,
        maximal_compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        ctx = make_spec_context(
            maximal_compose_deployment, real_discovery.components
        )
        resolved = resolve_all_env(
            maximal_compose_deployment,
            real_discovery.components,
            real_discovery.apps,
            ctx,
        )
        for svc in _AUTH_SERVICES:
            assert "WIP_AUTH_JWT_ISSUER_URL" in resolved[svc].optional, (
                f"service {svc!r} missing WIP_AUTH_JWT_ISSUER_URL in optional "
                f"env — jwt_only/dual modes would have nothing to validate against"
            )

    def test_wip_auth_mode_value_reflects_spec_auth_mode(
        self,
        maximal_compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """auth.mode=oidc (the maximal fixture's default) → WIP_AUTH_MODE
        resolves to 'jwt_only' as a Literal value the renderer will write
        verbatim into env."""
        ctx = make_spec_context(
            maximal_compose_deployment, real_discovery.components
        )
        resolved = resolve_all_env(
            maximal_compose_deployment,
            real_discovery.components,
            real_discovery.apps,
            ctx,
        )
        for svc in _AUTH_SERVICES:
            value = resolved[svc].required["WIP_AUTH_MODE"]
            assert isinstance(value, Literal), (
                f"WIP_AUTH_MODE for {svc!r} should resolve to a Literal, got "
                f"{type(value).__name__}"
            )
            assert value.value == "jwt_only"

    def test_wip_auth_jwt_issuer_url_value_matches_internal_dex(
        self,
        maximal_compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """The issuer URL is the internal Dex endpoint — wip-auth in the
        service calls Dex server-to-server, not the public URL."""
        ctx = make_spec_context(
            maximal_compose_deployment, real_discovery.components
        )
        resolved = resolve_all_env(
            maximal_compose_deployment,
            real_discovery.components,
            real_discovery.apps,
            ctx,
        )
        for svc in _AUTH_SERVICES:
            value = resolved[svc].optional["WIP_AUTH_JWT_ISSUER_URL"]
            assert isinstance(value, Literal)
            assert value.value == "http://wip-dex:5556/dex"

    def test_api_key_only_install_still_carries_env(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Even in api-key-only mode, WIP_AUTH_MODE is set (to
        'api_key_only') and WIP_AUTH_JWT_ISSUER_URL is set (and ignored
        by wip-auth in this mode). The env is always uniform — only the
        runtime behavior changes."""
        d = compose_deployment.model_copy(deep=True)
        # Order matters — gateway=False must land before mode flips to
        # api-key-only, otherwise gateway_requires_oidc validator fires.
        d.spec.auth.gateway = False
        d.spec.auth.mode = "api-key-only"
        ctx = make_spec_context(d, real_discovery.components)
        resolved = resolve_all_env(
            d, real_discovery.components, real_discovery.apps, ctx,
        )
        # Only check services active in the compose_deployment fixture.
        # mcp-server is the only optional active; reporting-sync is not.
        for svc in ("registry", "def-store", "template-store", "document-store"):
            mode_val = resolved[svc].required["WIP_AUTH_MODE"]
            assert isinstance(mode_val, Literal)
            assert mode_val.value == "api_key_only"


# ────────────────────────────────────────────────────────────────────
# Renderer integration — both compose and k8s carry the env through.
# ────────────────────────────────────────────────────────────────────


class TestRendererCarry:
    def test_compose_renders_wip_auth_mode_per_service(
        self,
        tmp_path: Path,
        maximal_compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Smoke-level: each service block in the rendered compose has
        WIP_AUTH_MODE in its environment block."""
        import yaml

        from wip_deploy.renderers.compose import render_compose

        secrets = _secrets_for(
            tmp_path, maximal_compose_deployment, real_discovery
        )
        tree = render_compose(
            maximal_compose_deployment,
            real_discovery.components,
            real_discovery.apps,
            secrets,
        )
        compose_yaml = tree.files[Path("docker-compose.yaml")].content
        data = yaml.safe_load(compose_yaml)
        services = data["services"]
        for svc in _AUTH_SERVICES:
            assert svc in services, f"service {svc!r} not in rendered compose"
            env = services[svc]["environment"]
            assert "WIP_AUTH_MODE" in env, (
                f"service {svc!r} compose block missing WIP_AUTH_MODE — "
                f"would default to api_key_only and ignore spec.auth.mode"
            )
            assert env["WIP_AUTH_MODE"] == "jwt_only"

    def test_k8s_renders_wip_auth_mode_per_service(
        self,
        tmp_path: Path,
        maximal_k8s_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """Same smoke for k8s: each service Deployment's container env
        carries WIP_AUTH_MODE."""
        from wip_deploy.renderers.k8s import render_k8s

        secrets = _secrets_for(
            tmp_path, maximal_k8s_deployment, real_discovery
        )
        tree = render_k8s(
            maximal_k8s_deployment,
            real_discovery.components,
            real_discovery.apps,
            secrets,
        )
        for svc in _AUTH_SERVICES:
            path = Path(f"services/{svc}.yaml")
            assert path in tree.files, (
                f"k8s tree missing services/{svc}.yaml — service render skipped"
            )
            content = tree.files[path].content
            assert "WIP_AUTH_MODE" in content, (
                f"k8s manifest for {svc!r} missing WIP_AUTH_MODE in env"
            )
            # Confirm the literal value is present (the renderer writes
            # env entries as `- name: X` / `value: Y` pairs).
            assert "jwt_only" in content, (
                f"k8s manifest for {svc!r} should carry WIP_AUTH_MODE=jwt_only"
            )
