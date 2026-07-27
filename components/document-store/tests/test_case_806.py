"""Storage client timeouts are bounded and passed through (CASE-806).

Without an explicit botocore Config the S3 client inherits 60s connect AND 60s
read with retry_mode `legacy`, so a MinIO that accepts connections but stops
answering stalls its caller for minutes. Pointed at a blackholed address, the
unconfigured health check was measured still running at 90s.

These tests guard the config reaching `create_client`, not the timeout values
themselves — the failure mode being prevented is a silent revert, where the
constants stay in the module but stop being handed to the client.
"""

from unittest.mock import MagicMock

import pytest
from botocore.config import Config

from document_store.services.file_storage_client import (
    _DATA_CONFIG,
    _HEALTH_CONFIG,
    FileStorageClient,
)


class _CapturingSession:
    """Stands in for the aiobotocore session, recording create_client kwargs."""

    def __init__(self) -> None:
        self.kwargs: dict = {}
        self.client = MagicMock()

    def create_client(self, service_name: str, **kwargs):
        self.kwargs = {"service_name": service_name, **kwargs}
        client = self.client

        class _CM:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _client_with_capture() -> tuple[FileStorageClient, _CapturingSession]:
    c = FileStorageClient(
        endpoint_url="http://minio.invalid:9000",
        access_key="k",
        secret_key="s",
        bucket="b",
    )
    session = _CapturingSession()
    c._session = session  # type: ignore[assignment]
    return c, session


@pytest.mark.asyncio
async def test_data_operations_get_the_bounded_data_config():
    c, session = _client_with_capture()

    async def _noop(**_kwargs):
        return {}

    session.client.head_object = _noop
    await c.exists("some-key")

    assert session.kwargs["config"] is _DATA_CONFIG


@pytest.mark.asyncio
async def test_health_check_gets_the_tighter_health_config():
    c, session = _client_with_capture()

    async def _buckets(**_kwargs):
        return {"Buckets": []}

    session.client.list_buckets = _buckets
    assert await c.health_check() is True

    assert session.kwargs["config"] is _HEALTH_CONFIG


def test_neither_config_leaves_a_timeout_at_the_botocore_default():
    # The whole point: 60/60 with retries unset is what the client had before,
    # and what it silently falls back to if the config stops being passed.
    default = Config()
    assert default.connect_timeout == 60
    assert default.read_timeout == 60
    assert default.retries is None

    for cfg in (_DATA_CONFIG, _HEALTH_CONFIG):
        assert cfg.connect_timeout < default.connect_timeout
        assert cfg.connect_timeout <= 5
        assert cfg.retries is not None
        # `legacy` is the mode that leaves max_attempts unset; both configs
        # must name a mode and a bound explicitly.
        assert cfg.retries["mode"] == "standard"
        assert cfg.retries["max_attempts"] >= 1

    # The health path must not sit through a data-sized read timeout.
    assert _HEALTH_CONFIG.read_timeout < _DATA_CONFIG.read_timeout


def test_health_config_fits_the_probe_budget_with_margin():
    # Wall time is NOT the configured timeout. Measured against a blackholed
    # endpoint, the client makes two connection attempts per botocore attempt
    # plus ~0.9s of fixed setup: wall ~= 2 * connect_timeout + 0.9
    # (1 -> 2.83s, 2 -> 4.58s, 3 -> 6.42s). The health path must clear the 5s
    # healthcheck timeout_seconds with real margin, not squeak under it.
    PROBE_BUDGET = 5.0
    worst_case = 2 * _HEALTH_CONFIG.connect_timeout + 1
    assert worst_case < PROBE_BUDGET * 0.7, (
        f"health config would take ~{worst_case}s against an unreachable "
        f"MinIO, too close to the {PROBE_BUDGET}s probe budget"
    )


def test_data_read_timeout_stays_generous_because_it_is_per_read():
    # botocore's read_timeout is per socket read, not per request, so a large
    # streaming upload is unaffected while bytes keep moving. Clamping this to
    # a few seconds would break archive uploads on a slow link — the guard is
    # against someone "tightening" it in a later pass.
    assert _DATA_CONFIG.read_timeout >= 30
