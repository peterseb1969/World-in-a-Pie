"""A key's use is logged at INFO once per process and then once an hour, not
on every request: the services log at INFO, and one line per request would
bury everything else in the log."""

from __future__ import annotations

import logging

import pytest
from starlette.requests import Request

from wip_auth import APIKeyProvider, APIKeyRecord, hash_api_key
from wip_auth.providers import api_key as api_key_module

LOGGER = "wip_auth.api_key"


def _request(key: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/api/x",
        "headers": [(b"x-api-key", key.encode())],
    })


def _usage_lines(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == LOGGER and r.levelno == level and r.getMessage().startswith("API key used")
    ]


@pytest.mark.asyncio
async def test_info_once_per_key_then_once_an_hour(monkeypatch, caplog) -> None:
    now = [1000.0]
    monkeypatch.setattr(api_key_module, "_monotonic", lambda: now[0])
    provider = APIKeyProvider([
        APIKeyRecord(name="a", key_hash=hash_api_key("key-a"), owner="o"),
        APIKeyRecord(name="b", key_hash=hash_api_key("key-b"), owner="o"),
    ])
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(3):
            assert await provider.authenticate(_request("key-a")) is not None
        assert await provider.authenticate(_request("key-b")) is not None
        assert _usage_lines(caplog, logging.INFO) == [
            "API key used: name=a owner=o endpoint=GET /api/x",
            "API key used: name=b owner=o endpoint=GET /api/x",
        ]
        # Every use is still there at DEBUG.
        assert len(_usage_lines(caplog, logging.DEBUG)) == 2

        caplog.clear()
        now[0] += 3599
        await provider.authenticate(_request("key-a"))
        assert _usage_lines(caplog, logging.INFO) == []

        now[0] += 1
        await provider.authenticate(_request("key-a"))
        assert _usage_lines(caplog, logging.INFO) == [
            "API key used: name=a owner=o endpoint=GET /api/x",
        ]
