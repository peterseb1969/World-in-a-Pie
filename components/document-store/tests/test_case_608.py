"""CASE-608 — qualified NS:VALUE form for document references.

Value-form doc refs resolve through the Registry scoped to a namespace.
Pre-fix that namespace was always the caller's — value-form references
could never cross namespaces, and the platform's qualified form
(bare never crosses; explicit NS:VALUE names the namespace, CASE-540/589)
was not parsed on this path. These tests pin the routing: which
(value, namespace) pair reaches the Registry lookup for each input shape.
Isolation enforcement on the resolved reference is CASE-566's layer and
is pinned in test_case_566.py.
"""

from unittest.mock import AsyncMock

import pytest

from document_store.services.validation_service import (
    ValidationResult,
    ValidationService,
)


def _service_with_registry_spy():
    svc = ValidationService()
    spy = AsyncMock(return_value=None)  # resolution outcome irrelevant; routing is the subject
    svc._resolve_via_registry = spy
    return svc, spy


async def _resolve(svc, value, caller_ns="appns"):
    result = ValidationResult()
    await svc._resolve_document_reference(
        value, [], result, "linked", namespace=caller_ns
    )
    return result


@pytest.mark.asyncio
async def test_bare_value_resolves_in_caller_namespace():
    svc, spy = _service_with_registry_spy()
    await _resolve(svc, "INV-001")
    spy.assert_awaited_once_with("INV-001", "appns", "documents")


@pytest.mark.asyncio
async def test_qualified_value_resolves_in_named_namespace():
    svc, spy = _service_with_registry_spy()
    await _resolve(svc, "otherns:INV-001")
    spy.assert_awaited_once_with("INV-001", "otherns", "documents")


@pytest.mark.asyncio
async def test_first_colon_wins():
    svc, spy = _service_with_registry_spy()
    await _resolve(svc, "otherns:a:b")
    spy.assert_awaited_once_with("a:b", "otherns", "documents")


@pytest.mark.asyncio
async def test_empty_prefix_falls_back_to_caller_namespace():
    svc, spy = _service_with_registry_spy()
    await _resolve(svc, ":INV-001")
    spy.assert_awaited_once_with("INV-001", "appns", "documents")


@pytest.mark.asyncio
async def test_hash_form_takes_precedence_over_qualified_parse():
    """hash:<identity_hash> is an existing colon form — it must keep routing
    through the hash branch untouched (raw value, caller namespace)."""
    svc, spy = _service_with_registry_spy()
    await _resolve(svc, "hash:abc123")
    spy.assert_awaited_once_with("hash:abc123", "appns", "documents")


@pytest.mark.asyncio
async def test_unresolved_qualified_value_is_reference_not_found():
    """A qualified value the Registry doesn't know stays a hard error."""
    svc, _spy = _service_with_registry_spy()
    result = await _resolve(svc, "otherns:NOPE")
    assert result.valid is False
    assert result.errors[0]["code"] == "reference_not_found"
