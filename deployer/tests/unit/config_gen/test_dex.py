"""Tests for Dex config generation."""

from __future__ import annotations

from wip_deploy.config_gen import SecretRef, generate_dex_config
from wip_deploy.discovery import Discovery
from wip_deploy.spec import Deployment


class TestDexActivation:
    def test_dex_skipped_for_api_key_only(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        d.spec.auth.mode = "api-key-only"
        d.spec.auth.users = []
        cfg = generate_dex_config(d, real_discovery.components, real_discovery.apps)
        assert cfg is None

    def test_dex_generated_for_oidc(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None


class TestIssuer:
    def test_issuer_url_format(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        assert cfg.issuer == "https://wip.local:8443/dex"


class TestUsers:
    def test_default_users_appear(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        usernames = {u.username for u in cfg.users}
        assert usernames == {"admin", "editor", "viewer"}

    def test_users_carry_secret_refs_not_passwords(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        # Each user's password is a SecretRef — the backend resolves it later.
        for u in cfg.users:
            assert isinstance(u.password_secret, SecretRef)
            assert u.password_secret.name.startswith("dex-password-")

    def test_user_ids_are_stable(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        ids = {u.user_id for u in cfg.users}
        assert ids == {"admin-001", "editor-001", "viewer-001"}


class TestClients:
    def test_console_client_always_present(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        # Standard preset includes console → wip-console client should appear.
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        client_ids = {c.client_id for c in cfg.clients}
        assert "wip-console" in client_ids

    def test_gateway_client_when_gateway_on(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        client_ids = {c.client_id for c in cfg.clients}
        assert "wip-gateway" in client_ids

    def test_gateway_client_skipped_when_gateway_off(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        d = compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        cfg = generate_dex_config(d, real_discovery.components, real_discovery.apps)
        # auth.gateway=False → auth-gateway component inactive → no client
        assert cfg is not None
        client_ids = {c.client_id for c in cfg.clients}
        assert "wip-gateway" not in client_ids

    def test_app_clients_appear_for_enabled_apps_with_oidc(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        """Apps that declare their own oidc_client get a Dex client.
        Apps relying solely on gateway-forwarded headers do not."""
        from wip_deploy.spec import AppRef

        d = compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="dnd"), AppRef(name="clintrial")]

        cfg = generate_dex_config(d, real_discovery.components, real_discovery.apps)
        assert cfg is not None
        client_ids = {c.client_id for c in cfg.clients}
        # dnd manifest declares an oidc_client — its client_id appears.
        assert "dnd" in client_ids
        # clintrial has no oidc_client (gateway-only) — no entry.
        assert "clintrial" not in client_ids

    def test_disabled_app_client_absent(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        from wip_deploy.spec import AppRef

        d = compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="dnd", enabled=False)]

        cfg = generate_dex_config(d, real_discovery.components, real_discovery.apps)
        assert cfg is not None
        client_ids = {c.client_id for c in cfg.clients}
        assert "dnd" not in client_ids

    def test_client_secrets_are_secret_refs(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        cfg = generate_dex_config(
            compose_deployment, real_discovery.components, real_discovery.apps
        )
        assert cfg is not None
        for client in cfg.clients:
            assert isinstance(client.secret, SecretRef)
            assert client.secret.name.startswith("dex-client-")

    def test_app_redirect_uris_include_app_route_prefix(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        from wip_deploy.spec import AppRef

        d = compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="dnd")]

        cfg = generate_dex_config(d, real_discovery.components, real_discovery.apps)
        assert cfg is not None
        dnd_client = next(c for c in cfg.clients if c.client_id == "dnd")
        # App's route_prefix is /apps/dnd → redirect includes it
        assert any("/apps/dnd/auth/callback" in uri for uri in dnd_client.redirect_uris)


class TestDeterminism:
    def test_client_output_order_stable(
        self, compose_deployment: Deployment, real_discovery: Discovery
    ) -> None:
        from wip_deploy.spec import AppRef

        d1 = compose_deployment.model_copy(deep=True)
        d1.spec.apps = [AppRef(name="dnd"), AppRef(name="react-console")]
        d2 = compose_deployment.model_copy(deep=True)
        d2.spec.apps = [AppRef(name="react-console"), AppRef(name="dnd")]

        c1 = generate_dex_config(d1, real_discovery.components, real_discovery.apps)
        c2 = generate_dex_config(d2, real_discovery.components, real_discovery.apps)
        assert c1 is not None and c2 is not None
        # Alphabetical client_id order regardless of input ordering
        assert [c.client_id for c in c1.clients] == [c.client_id for c in c2.clients]
