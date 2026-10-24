"""Namespace isolation fails closed when the Registry cannot be asked.

The validator used to skip the isolation check whenever the namespace's
settings could not be fetched, so a strict namespace accepted references
it forbids during any Registry outage. It now distinguishes "the namespace
does not exist" (treated as open) from "the Registry could not answer"
(RegistryUnavailableError, which the write path turns into a refusal).
"""

from unittest.mock import patch

import httpx
import pytest

from document_store.services.reference_validator import (
    ReferenceValidator,
    RegistryUnavailableError,
)


class _Unreachable:
    """An httpx.AsyncClient stand-in whose every request fails to connect."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        raise httpx.ConnectError("connection refused")


class _Answer:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def _answering(response: _Answer):
    class _Client(_Unreachable):
        async def get(self, url, headers=None):
            return response
    return _Client


@pytest.mark.asyncio
async def test_an_unreachable_registry_raises_instead_of_skipping():
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    with patch("httpx.AsyncClient", _Unreachable), pytest.raises(RegistryUnavailableError):
        await v._get_namespace("appns")


@pytest.mark.asyncio
async def test_an_unknown_namespace_is_open():
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    with patch("httpx.AsyncClient", _answering(_Answer(404))):
        assert await v._get_namespace("appns") == {"isolation_mode": "open"}


@pytest.mark.asyncio
async def test_a_strict_namespace_still_refuses_a_foreign_reference():
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    strict = _Answer(200, {"isolation_mode": "strict", "allowed_external_refs": []})
    with patch("httpx.AsyncClient", _answering(strict)), pytest.raises(Exception) as exc:
        await v.validate_template_references(
            template_namespace="appns", terminology_namespaces=["other"],
        )
    assert type(exc.value).__name__ == "ReferenceValidationError"
