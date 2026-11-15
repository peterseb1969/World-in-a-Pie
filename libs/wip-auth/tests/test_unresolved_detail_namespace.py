"""The 404 text of an identifier that names nothing never names a namespace
that does not exist: with no namespace passed and none the key implies, it
names none ("in namespace 'None'" read as a namespace called None)."""

import pytest

from wip_auth import fastapi_helpers
from wip_auth.fastapi_helpers import unresolved_detail


@pytest.mark.asyncio
async def test_no_namespace_anywhere_names_none(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(fastapi_helpers, "_derive_namespace_from_identity", none)
    text = await unresolved_detail("X-1", "document", None, "document_id")
    assert text == "Could not resolve document_id 'X-1'"
    assert "None" not in text


@pytest.mark.asyncio
async def test_the_keys_namespace_is_named(monkeypatch):
    async def implied():
        return "aa"
    monkeypatch.setattr(fastapi_helpers, "_derive_namespace_from_identity", implied)
    assert await unresolved_detail("X-1", "template", None) == (
        "Could not resolve template 'X-1' in namespace 'aa'"
    )


@pytest.mark.asyncio
async def test_a_passed_namespace_is_named():
    assert await unresolved_detail("X-1", "file", "bb", "file_id") == (
        "Could not resolve file_id 'X-1' in namespace 'bb'"
    )
