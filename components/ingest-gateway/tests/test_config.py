"""Tests for the Ingest Gateway configuration module."""

import os

import pytest
from unittest.mock import patch

from ingest_gateway.config import Settings


class TestDefaultValues:
    """Verify all default configuration values."""

    def test_service_name(self):
        s = Settings()
        assert s.service_name == "wip-ingest-gateway"

    def test_debug_default_false(self):
        s = Settings()
        assert s.debug is False

    def test_nats_url_default(self):
        s = Settings()
        assert s.nats_url == "nats://localhost:4222"

    def test_nats_ingest_stream_name(self):
        s = Settings()
        assert s.nats_ingest_stream_name == "WIP_INGEST"

    def test_nats_ingest_consumer_name(self):
        s = Settings()
        assert s.nats_ingest_consumer_name == "ingest-gateway"

    def test_nats_ingest_durable_name(self):
        s = Settings()
        assert s.nats_ingest_durable_name == "ingest-gateway-durable"

    def test_nats_results_stream_name(self):
        s = Settings()
        assert s.nats_results_stream_name == "WIP_INGEST_RESULTS"

    def test_def_store_url(self):
        s = Settings()
        assert s.def_store_url == "http://localhost:8002"

    def test_template_store_url(self):
        s = Settings()
        assert s.template_store_url == "http://localhost:8003"

    def test_document_store_url(self):
        s = Settings()
        assert s.document_store_url == "http://localhost:8004"

    def test_api_key_default(self):
        s = Settings()
        assert s.api_key == "dev_master_key_for_testing"

    def test_batch_size(self):
        s = Settings()
        assert s.batch_size == 100

    def test_retry_attempts(self):
        s = Settings()
        assert s.retry_attempts == 3

    def test_retry_delay_ms(self):
        s = Settings()
        assert s.retry_delay_ms == 1000

    def test_http_timeout_seconds(self):
        s = Settings()
        assert s.http_timeout_seconds == 30.0

    def test_stream_max_msgs(self):
        s = Settings()
        assert s.stream_max_msgs == 1_000_000

    def test_stream_max_bytes(self):
        s = Settings()
        assert s.stream_max_bytes == 1024 * 1024 * 1024  # 1GB

    def test_results_max_age_seconds(self):
        s = Settings()
        assert s.results_max_age_seconds == 60 * 60 * 24 * 7  # 7 days


class TestEnvironmentVariables:
    """Settings are loaded from environment variables using their alias."""

    def test_nats_url_from_env(self):
        with patch.dict(os.environ, {"NATS_URL": "nats://nats-server:4222"}):
            s = Settings()
            assert s.nats_url == "nats://nats-server:4222"

    def test_debug_from_env(self):
        with patch.dict(os.environ, {"DEBUG": "true"}):
            s = Settings()
            assert s.debug is True

    def test_def_store_url_from_env(self):
        with patch.dict(os.environ, {"DEF_STORE_URL": "http://def-store:8002"}):
            s = Settings()
            assert s.def_store_url == "http://def-store:8002"

    def test_template_store_url_from_env(self):
        with patch.dict(os.environ, {"TEMPLATE_STORE_URL": "http://template-store:8003"}):
            s = Settings()
            assert s.template_store_url == "http://template-store:8003"

    def test_document_store_url_from_env(self):
        with patch.dict(os.environ, {"DOCUMENT_STORE_URL": "http://document-store:8004"}):
            s = Settings()
            assert s.document_store_url == "http://document-store:8004"

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"API_KEY": "prod-secret-key-12345"}):
            s = Settings()
            assert s.api_key == "prod-secret-key-12345"

    def test_batch_size_from_env(self):
        with patch.dict(os.environ, {"BATCH_SIZE": "500"}):
            s = Settings()
            assert s.batch_size == 500

    def test_retry_attempts_from_env(self):
        with patch.dict(os.environ, {"RETRY_ATTEMPTS": "5"}):
            s = Settings()
            assert s.retry_attempts == 5

    def test_retry_delay_ms_from_env(self):
        with patch.dict(os.environ, {"RETRY_DELAY_MS": "2000"}):
            s = Settings()
            assert s.retry_delay_ms == 2000

    def test_http_timeout_seconds_from_env(self):
        with patch.dict(os.environ, {"HTTP_TIMEOUT_SECONDS": "60.0"}):
            s = Settings()
            assert s.http_timeout_seconds == 60.0

    def test_stream_max_msgs_from_env(self):
        with patch.dict(os.environ, {"STREAM_MAX_MSGS": "500000"}):
            s = Settings()
            assert s.stream_max_msgs == 500_000

    def test_stream_max_bytes_from_env(self):
        with patch.dict(os.environ, {"STREAM_MAX_BYTES": "2147483648"}):
            s = Settings()
            assert s.stream_max_bytes == 2_147_483_648  # 2GB

    def test_results_max_age_seconds_from_env(self):
        with patch.dict(os.environ, {"RESULTS_MAX_AGE_SECONDS": "86400"}):
            s = Settings()
            assert s.results_max_age_seconds == 86400  # 1 day

    def test_nats_ingest_stream_name_from_env(self):
        with patch.dict(os.environ, {"NATS_INGEST_STREAM_NAME": "CUSTOM_INGEST"}):
            s = Settings()
            assert s.nats_ingest_stream_name == "CUSTOM_INGEST"

    def test_nats_ingest_consumer_name_from_env(self):
        with patch.dict(os.environ, {"NATS_INGEST_CONSUMER_NAME": "custom-consumer"}):
            s = Settings()
            assert s.nats_ingest_consumer_name == "custom-consumer"

    def test_nats_ingest_durable_name_from_env(self):
        with patch.dict(os.environ, {"NATS_INGEST_DURABLE_NAME": "custom-durable"}):
            s = Settings()
            assert s.nats_ingest_durable_name == "custom-durable"

    def test_nats_results_stream_name_from_env(self):
        with patch.dict(os.environ, {"NATS_RESULTS_STREAM_NAME": "CUSTOM_RESULTS"}):
            s = Settings()
            assert s.nats_results_stream_name == "CUSTOM_RESULTS"


class TestNatsUrlConfiguration:
    """NATS URL supports various connection patterns."""

    def test_default_localhost(self):
        s = Settings()
        assert s.nats_url == "nats://localhost:4222"

    def test_custom_host_and_port(self):
        with patch.dict(os.environ, {"NATS_URL": "nats://nats.internal:4223"}):
            s = Settings()
            assert s.nats_url == "nats://nats.internal:4223"

    def test_tls_url(self):
        with patch.dict(os.environ, {"NATS_URL": "tls://nats.prod.example.com:4222"}):
            s = Settings()
            assert s.nats_url == "tls://nats.prod.example.com:4222"

    def test_ip_address_url(self):
        with patch.dict(os.environ, {"NATS_URL": "nats://192.168.1.100:4222"}):
            s = Settings()
            assert s.nats_url == "nats://192.168.1.100:4222"


class TestTargetServiceUrls:
    """Each WIP service has a configurable URL."""

    def test_all_services_default_to_localhost(self):
        s = Settings()
        assert "localhost:8002" in s.def_store_url
        assert "localhost:8003" in s.template_store_url
        assert "localhost:8004" in s.document_store_url

    def test_services_configurable_for_container_network(self):
        env = {
            "DEF_STORE_URL": "http://wip-def-store:8002",
            "TEMPLATE_STORE_URL": "http://wip-template-store:8003",
            "DOCUMENT_STORE_URL": "http://wip-document-store:8004",
        }
        with patch.dict(os.environ, env):
            s = Settings()
            assert s.def_store_url == "http://wip-def-store:8002"
            assert s.template_store_url == "http://wip-template-store:8003"
            assert s.document_store_url == "http://wip-document-store:8004"

    def test_each_service_port_is_distinct(self):
        """Default service URLs use distinct ports."""
        s = Settings()
        urls = [s.def_store_url, s.template_store_url, s.document_store_url]
        assert len(set(urls)) == len(urls), "Service URLs must be distinct"


class TestExtraFieldsIgnored:
    """The 'extra=ignore' model_config means unknown env vars do not cause errors."""

    def test_unknown_env_var_ignored(self):
        with patch.dict(os.environ, {"TOTALLY_UNKNOWN_VAR": "whatever"}):
            s = Settings()
            assert s.service_name == "wip-ingest-gateway"  # still works

    def test_multiple_unknown_env_vars_ignored(self):
        env = {
            "FOO": "bar",
            "BAZ": "123",
            "NOT_A_SETTING": "true",
        }
        with patch.dict(os.environ, env):
            s = Settings()
            assert not hasattr(s, "FOO")
            assert not hasattr(s, "BAZ")


class TestProcessingConfiguration:
    """Processing-related settings have sensible defaults and are configurable."""

    def test_batch_size_is_positive(self):
        s = Settings()
        assert s.batch_size > 0

    def test_retry_attempts_is_positive(self):
        s = Settings()
        assert s.retry_attempts > 0

    def test_retry_delay_is_positive(self):
        s = Settings()
        assert s.retry_delay_ms > 0

    def test_http_timeout_is_positive(self):
        s = Settings()
        assert s.http_timeout_seconds > 0

    def test_stream_max_msgs_is_positive(self):
        s = Settings()
        assert s.stream_max_msgs > 0

    def test_stream_max_bytes_is_positive(self):
        s = Settings()
        assert s.stream_max_bytes > 0

    def test_results_max_age_is_positive(self):
        s = Settings()
        assert s.results_max_age_seconds > 0
