"""CASE-607 — the validator's namespace cache expires on a TTL.

Pre-fix, ReferenceValidator._namespace_cache had no TTL and no invalidation:
isolation_mode / allowed_external_refs changes (and namespaces created after
being first probed as 404) were invisible until a service restart. These
tests pin the 5 s TTL: fresh entries are served without a Registry hit,
expired entries — including the cached 404-negative — are refetched.
"""

import time
from unittest.mock import patch

import pytest

from document_store.services.reference_validator import (
    NAMESPACE_CACHE_TTL_SECONDS,
    ReferenceValidationError,
    ReferenceValidator,
)

_STALE = -(NAMESPACE_CACHE_TTL_SECONDS + 1.0)  # offset that puts an entry past the TTL


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClientFactory:
    """Stands in for httpx.AsyncClient; serves one canned response, counts calls."""

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
    factory = _FakeClientFactory(_FakeResponse(500))  # any HTTP call would be visible
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
    """The CASE-566#4 live sequence: violation → operator adds the allow-list →
    retry succeeds once the TTL lapses (pre-fix it failed forever)."""
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    doc_ref = {
        "field_path": "linked",
        "reference_type": "document",
        "resolved": {"document_id": "x", "namespace": "otherns"},
    }
    _seed(v, "appns", {"isolation_mode": "open", "allowed_external_refs": []}, age_offset=_STALE)
    updated = {"isolation_mode": "open", "allowed_external_refs": ["otherns"]}
    factory = _FakeClientFactory(_FakeResponse(200, updated))
    with patch("httpx.AsyncClient", factory):
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="appns",
            document_references=[doc_ref],
        )  # would raise pre-fix: stale empty allow-list


@pytest.mark.asyncio
async def test_cached_404_negative_expires_too():
    """A namespace probed before it existed must not stay 'open' forever."""
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    _seed(v, "appns", {"isolation_mode": "open"}, age_offset=_STALE)  # the 404-negative shape
    strict = {"isolation_mode": "strict", "allowed_external_refs": []}
    factory = _FakeClientFactory(_FakeResponse(200, strict))
    doc_ref = {
        "field_path": "linked",
        "reference_type": "document",
        "resolved": {"document_id": "x", "namespace": "otherns"},
    }
    with patch("httpx.AsyncClient", factory), pytest.raises(ReferenceValidationError):
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="appns",
            document_references=[doc_ref],
        )


def test_ttl_matches_platform_convention():
    """5 s, mirroring the documented template-cache TTL (wip://conventions)."""
    assert NAMESPACE_CACHE_TTL_SECONDS == 5.0
