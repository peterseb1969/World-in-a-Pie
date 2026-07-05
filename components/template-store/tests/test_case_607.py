"""CASE-607 — the validator's namespace cache expires on a TTL (template-store copy).

template-store carries its own copy of ReferenceValidator with the same
unbounded per-process namespace cache; same fix, same pins as the
document-store twin: fresh entries served without a Registry hit, expired
entries — including the cached 404-negative — refetched within 5 s.
"""

import time
from unittest.mock import patch

import pytest

from template_store.services.reference_validator import (
    NAMESPACE_CACHE_TTL_SECONDS,
    ReferenceValidationError,
    ReferenceValidator,
)

_STALE = -(NAMESPACE_CACHE_TTL_SECONDS + 1.0)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClientFactory:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls = 0

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self.calls += 1
        return self._response


def _seed(v: ReferenceValidator, namespace: str, data: dict, age_offset: float = 0.0):
    v._namespace_cache[namespace] = (time.monotonic() + age_offset, data)


@pytest.mark.asyncio
async def test_fresh_cache_entry_served_without_registry_hit():
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    _seed(v, "appns", {"isolation_mode": "open", "allowed_external_refs": []})
    factory = _FakeClientFactory(_FakeResponse(500))
    with patch("httpx.AsyncClient", factory):
        got = await v._get_namespace("appns")
    assert got == {"isolation_mode": "open", "allowed_external_refs": []}
    assert factory.calls == 0


@pytest.mark.asyncio
async def test_expired_entry_is_refetched():
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    _seed(v, "appns", {"isolation_mode": "open", "allowed_external_refs": []}, age_offset=_STALE)
    fresh = {"isolation_mode": "open", "allowed_external_refs": ["otherns"]}
    factory = _FakeClientFactory(_FakeResponse(200, fresh))
    with patch("httpx.AsyncClient", factory):
        got = await v._get_namespace("appns")
    assert factory.calls == 1
    assert got == fresh


@pytest.mark.asyncio
async def test_allow_list_change_takes_effect_after_ttl():
    """Extends-check honours an allow-list added after first touch, post-TTL."""
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    _seed(v, "appns", {"isolation_mode": "strict", "allowed_external_refs": []}, age_offset=_STALE)
    updated = {"isolation_mode": "strict", "allowed_external_refs": ["parentns"]}
    factory = _FakeClientFactory(_FakeResponse(200, updated))
    with patch("httpx.AsyncClient", factory):
        await v.validate_template_references(
            template_namespace="appns",
            extends_template_namespace="parentns",
        )  # would raise pre-fix: stale empty allow-list


@pytest.mark.asyncio
async def test_cached_404_negative_expires_too():
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    _seed(v, "appns", {"isolation_mode": "open"}, age_offset=_STALE)
    strict = {"isolation_mode": "strict", "allowed_external_refs": []}
    factory = _FakeClientFactory(_FakeResponse(200, strict))
    with patch("httpx.AsyncClient", factory):
        with pytest.raises(ReferenceValidationError):
            await v.validate_template_references(
                template_namespace="appns",
                extends_template_namespace="parentns",
            )


def test_ttl_matches_platform_convention():
    assert NAMESPACE_CACHE_TTL_SECONDS == 5.0
