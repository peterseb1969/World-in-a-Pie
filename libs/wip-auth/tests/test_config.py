"""Tests for auth configuration."""

import os
import pytest

from wip_auth import AuthConfig, get_auth_config, set_auth_config, reset_auth_config


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config before and after each test."""
    reset_auth_config()
    yield
    reset_auth_config()


class TestAuthConfig:
    """Tests for AuthConfig."""

    def test_default_mode(self):
        """Default mode should be api_key_only."""
        config = AuthConfig()
        assert config.mode == "api_key_only"

    def test_mode_from_env(self, monkeypatch):
        """Mode should be read from environment."""
        monkeypatch.setenv("WIP_AUTH_MODE", "dual")
        config = AuthConfig()
        assert config.mode == "dual"

    def test_jwt_settings(self, monkeypatch):
        """JWT settings should be read from environment."""
        monkeypatch.setenv("WIP_AUTH_JWT_ISSUER_URL", "http://auth.example.com")
        monkeypatch.setenv("WIP_AUTH_JWT_AUDIENCE", "my-app")
        config = AuthConfig()
        assert config.jwt_issuer_url == "http://auth.example.com"
        assert config.jwt_audience == "my-app"

    def test_jwks_url_from_issuer(self):
        """JWKS URL should be derived from issuer."""
        config = AuthConfig(jwt_issuer_url="http://auth.example.com")
        assert config.jwks_url == "http://auth.example.com/.well-known/jwks.json"

    def test_explicit_jwks_url(self):
        """Explicit JWKS URL should override derived one."""
        config = AuthConfig(
            jwt_issuer_url="http://auth.example.com",
            jwt_jwks_uri="http://other.example.com/keys"
        )
        assert config.jwks_url == "http://other.example.com/keys"

    def test_requires_jwt(self):
        """requires_jwt should be True for jwt_only and dual modes."""
        assert AuthConfig(mode="none").requires_jwt is False
        assert AuthConfig(mode="api_key_only").requires_jwt is False
        assert AuthConfig(mode="jwt_only").requires_jwt is True
        assert AuthConfig(mode="dual").requires_jwt is True

    def test_requires_api_key(self):
        """requires_api_key should be True for api_key_only and dual modes."""
        assert AuthConfig(mode="none").requires_api_key is False
        assert AuthConfig(mode="api_key_only").requires_api_key is True
        assert AuthConfig(mode="jwt_only").requires_api_key is False
        assert AuthConfig(mode="dual").requires_api_key is True

    def test_load_legacy_api_key(self):
        """Legacy API key should be loaded as a key record."""
        config = AuthConfig(legacy_api_key="test_key")
        keys = config.load_api_keys()
        assert len(keys) == 1
        assert keys[0].name == "legacy"
        # Legacy key gets admin groups (whatever admin_groups is configured as)
        assert keys[0].groups == config.admin_groups

    def test_algorithms_from_string(self):
        """Algorithms can be comma-separated string in constructor."""
        config = AuthConfig(jwt_algorithms="RS256,RS384,RS512")
        assert config.jwt_algorithms == ["RS256", "RS384", "RS512"]

    def test_groups_from_string(self):
        """Groups can be comma-separated string in constructor."""
        config = AuthConfig(admin_groups="admins,superusers")
        assert config.admin_groups == ["admins", "superusers"]


class TestConfigSingleton:
    """Tests for config singleton functions."""

    def test_get_auth_config_singleton(self, monkeypatch):
        """get_auth_config should return same instance."""
        monkeypatch.setenv("WIP_AUTH_MODE", "none")
        config1 = get_auth_config()
        config2 = get_auth_config()
        assert config1 is config2

    def test_set_auth_config(self):
        """set_auth_config should override singleton."""
        custom = AuthConfig(mode="jwt_only")
        set_auth_config(custom)
        assert get_auth_config() is custom

    def test_reset_auth_config(self, monkeypatch):
        """reset_auth_config should clear singleton."""
        monkeypatch.setenv("WIP_AUTH_MODE", "dual")
        _ = get_auth_config()
        reset_auth_config()
        monkeypatch.setenv("WIP_AUTH_MODE", "none")
        config = get_auth_config()
        assert config.mode == "none"


class TestLegacyEnvMapping:
    """Tests for legacy environment variable mapping."""

    def test_api_key_mapped(self, monkeypatch):
        """API_KEY should be mapped to WIP_AUTH_LEGACY_API_KEY."""
        # Clear any existing WIP_AUTH_LEGACY_API_KEY
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
        monkeypatch.setenv("API_KEY", "legacy_key_value")

        # Re-import to trigger mapping
        from wip_auth import config as config_module
        config_module._check_legacy_env_vars()

        assert os.environ.get("WIP_AUTH_LEGACY_API_KEY") == "legacy_key_value"

    def test_master_api_key_mapped(self, monkeypatch):
        """MASTER_API_KEY should be mapped to WIP_AUTH_LEGACY_API_KEY."""
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
        monkeypatch.setenv("MASTER_API_KEY", "master_key_value")

        from wip_auth import config as config_module
        config_module._check_legacy_env_vars()

        assert os.environ.get("WIP_AUTH_LEGACY_API_KEY") == "master_key_value"
