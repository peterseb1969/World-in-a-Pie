"""Tests for WIPConfig configuration resolution."""

import os
import pytest

from wip_toolkit.config import WIPConfig, DEFAULT_DEV_API_KEY, SERVICE_PORTS, SERVICE_PREFIXES, _read_env_var


class TestWIPConfigDefaults:
    """Test default configuration values."""

    def test_default_host(self):
        config = WIPConfig()
        assert config.host == "localhost"

    def test_default_proxy_disabled(self):
        config = WIPConfig()
        assert config.proxy is False

    def test_default_proxy_port(self):
        config = WIPConfig()
        assert config.proxy_port == 8443

    def test_default_verify_ssl(self):
        config = WIPConfig()
        assert config.verify_ssl is True

    def test_default_verbose(self):
        config = WIPConfig()
        assert config.verbose is False

    def test_default_api_key_falls_back_to_dev_key(self, monkeypatch):
        """With no env var and no .env file, falls back to dev key."""
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
        config = WIPConfig()
        assert config.api_key == DEFAULT_DEV_API_KEY


class TestWIPConfigProxyMode:
    """Test proxy mode URL construction."""

    def test_proxy_uses_https(self):
        config = WIPConfig(host="myhost", proxy=True)
        for service in SERVICE_PORTS:
            assert config._service_urls[service].startswith("https://")

    def test_proxy_uses_proxy_port(self):
        config = WIPConfig(host="myhost", proxy=True, proxy_port=9443)
        for service in SERVICE_PORTS:
            assert ":9443" in config._service_urls[service]

    def test_proxy_all_services_same_base(self):
        config = WIPConfig(host="myhost", proxy=True)
        urls = set(config._service_urls.values())
        assert len(urls) == 1
        assert urls.pop() == "https://myhost:8443"

    def test_proxy_custom_host(self):
        config = WIPConfig(host="wip-pi.local", proxy=True)
        for service in SERVICE_PORTS:
            assert "wip-pi.local" in config._service_urls[service]


class TestWIPConfigDirectMode:
    """Test direct mode URL construction (no proxy)."""

    def test_direct_uses_http(self):
        config = WIPConfig(host="localhost", proxy=False)
        for service in SERVICE_PORTS:
            assert config._service_urls[service].startswith("http://")

    def test_direct_uses_service_ports(self):
        config = WIPConfig(host="localhost", proxy=False)
        for service, port in SERVICE_PORTS.items():
            assert f":{port}" in config._service_urls[service]

    def test_direct_each_service_has_unique_url(self):
        config = WIPConfig(host="localhost", proxy=False)
        urls = list(config._service_urls.values())
        assert len(urls) == len(set(urls)), "Each service should have a unique URL in direct mode"

    def test_direct_custom_host(self):
        config = WIPConfig(host="192.168.1.100", proxy=False)
        for service in SERVICE_PORTS:
            assert "192.168.1.100" in config._service_urls[service]


class TestWIPConfigServiceUrl:
    """Test service_url() method returns correct full URL."""

    def test_service_url_direct_mode(self):
        config = WIPConfig(host="localhost", proxy=False)
        for service, port in SERVICE_PORTS.items():
            prefix = SERVICE_PREFIXES[service]
            expected = f"http://localhost:{port}{prefix}"
            assert config.service_url(service) == expected

    def test_service_url_proxy_mode(self):
        config = WIPConfig(host="myhost", proxy=True)
        for service in SERVICE_PORTS:
            prefix = SERVICE_PREFIXES[service]
            expected = f"https://myhost:8443{prefix}"
            assert config.service_url(service) == expected

    def test_service_url_registry(self):
        config = WIPConfig(host="localhost", proxy=False)
        assert config.service_url("registry") == "http://localhost:8001/api/registry"

    def test_service_url_def_store(self):
        config = WIPConfig(host="localhost", proxy=False)
        assert config.service_url("def-store") == "http://localhost:8002/api/def-store"

    def test_service_url_template_store(self):
        config = WIPConfig(host="localhost", proxy=False)
        assert config.service_url("template-store") == "http://localhost:8003/api/template-store"

    def test_service_url_document_store(self):
        config = WIPConfig(host="localhost", proxy=False)
        assert config.service_url("document-store") == "http://localhost:8004/api/document-store"

    def test_service_url_reporting_sync(self):
        config = WIPConfig(host="localhost", proxy=False)
        assert config.service_url("reporting-sync") == "http://localhost:8005/api/reporting-sync"

    def test_service_url_unknown_service_raises(self):
        config = WIPConfig()
        with pytest.raises(KeyError):
            config.service_url("nonexistent-service")


class TestWIPConfigApiKey:
    """Test API key resolution."""

    def test_api_key_from_environment_variable(self, monkeypatch):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "my_secret_key")
        config = WIPConfig()
        assert config.api_key == "my_secret_key"

    def test_api_key_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "env_key")
        config = WIPConfig(api_key="explicit_key")
        assert config.api_key == "explicit_key"

    def test_api_key_fallback_to_dev_key(self, monkeypatch):
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
        config = WIPConfig()
        assert config.api_key == DEFAULT_DEV_API_KEY

    def test_api_key_from_env_file(self, monkeypatch, tmp_path):
        """API key read from .env file when env var is not set."""
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text('WIP_AUTH_LEGACY_API_KEY=file_api_key\n')
        monkeypatch.chdir(tmp_path)
        config = WIPConfig()
        assert config.api_key == "file_api_key"

    def test_env_var_takes_precedence_over_file(self, monkeypatch, tmp_path):
        """Environment variable takes precedence over .env file."""
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "env_key")
        env_file = tmp_path / ".env"
        env_file.write_text('WIP_AUTH_LEGACY_API_KEY=file_key\n')
        monkeypatch.chdir(tmp_path)
        config = WIPConfig()
        assert config.api_key == "env_key"


class TestReadEnvVar:
    """Test the _read_env_var helper function."""

    def test_reads_simple_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_VAR=hello\n")
        assert _read_env_var(env_file, "MY_VAR") == "hello"

    def test_returns_none_for_missing_var(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER_VAR=value\n")
        assert _read_env_var(env_file, "MY_VAR") is None

    def test_strips_quotes_single(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_VAR='quoted_value'\n")
        assert _read_env_var(env_file, "MY_VAR") == "quoted_value"

    def test_strips_quotes_double(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('MY_VAR="double_quoted"\n')
        assert _read_env_var(env_file, "MY_VAR") == "double_quoted"

    def test_skips_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nMY_VAR=value\n")
        assert _read_env_var(env_file, "MY_VAR") == "value"

    def test_skips_lines_without_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("no_equals_sign\nMY_VAR=value\n")
        assert _read_env_var(env_file, "MY_VAR") == "value"

    def test_empty_value_returns_none(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_VAR=\n")
        assert _read_env_var(env_file, "MY_VAR") is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        env_file = tmp_path / "nonexistent.env"
        assert _read_env_var(env_file, "MY_VAR") is None

    def test_handles_whitespace_around_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("  MY_VAR = value_with_spaces  \n")
        assert _read_env_var(env_file, "MY_VAR") == "value_with_spaces"

    def test_value_with_equals_sign(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_VAR=key=value\n")
        assert _read_env_var(env_file, "MY_VAR") == "key=value"

    def test_multiple_vars(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("VAR_A=alpha\nVAR_B=beta\nVAR_C=gamma\n")
        assert _read_env_var(env_file, "VAR_A") == "alpha"
        assert _read_env_var(env_file, "VAR_B") == "beta"
        assert _read_env_var(env_file, "VAR_C") == "gamma"
