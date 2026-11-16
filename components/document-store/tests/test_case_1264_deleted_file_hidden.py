"""A deleted file in a namespace the caller cannot read answers its content
door exactly as an id that names nothing (CASE-1264, review of 73b29b96, R1).

The door answered 409 "File has been deleted" before it checked whether the
caller can read the file's namespace, so the 409 told an outsider the file
exists. Readability is checked first now.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from document_store.models.file import FileStatus
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

FILES = "/api/document-store/files"
# Scoped to a namespace no test data lives in: it cannot read "wip".
OUTSIDER = {"X-API-Key": "test_scoped_key"}


@pytest.mark.asyncio
async def test_a_deleted_file_the_caller_cannot_read_answers_as_a_missing_one(
    file_client: AsyncClient,  # noqa: F811
):
    deleted = await _insert_file(namespace="wip", status=FileStatus.INACTIVE)
    hidden = await file_client.get(f"{FILES}/{deleted.file_id}/content", headers=OUTSIDER)
    missing = await file_client.get(f"{FILES}/{uuid.uuid4()}/content", headers=OUTSIDER)
    assert missing.status_code == 404, missing.text
    assert hidden.status_code == 404, hidden.text
    assert "deleted" not in hidden.text


@pytest.mark.asyncio
async def test_a_reader_still_learns_the_file_was_deleted(
    file_client: AsyncClient, auth_headers: dict,  # noqa: F811
):
    deleted = await _insert_file(namespace="wip", status=FileStatus.INACTIVE)
    r = await file_client.get(f"{FILES}/{deleted.file_id}/content", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "File has been deleted"


# ---- Edge-type filters naming another namespace (review of 73b29b96, R2) ----
# The filter is resolved through the Registry with the service's access, so a
# `NS:VALUE` naming a namespace the caller cannot read answered 200 when the
# template existed and 404 when it did not. It now gets the miss's 404 first.


@pytest.mark.asyncio
async def test_a_filter_naming_an_unreadable_namespace_answers_as_a_miss(monkeypatch):
    from fastapi import HTTPException

    from document_store.api import documents
    from wip_auth.models import UserIdentity

    resolved: list[str] = []

    async def exists(value, *_args, **_kwargs):
        resolved.append(value)
        return "T-EXISTS"

    async def cannot_read(_identity, _namespace):
        return False

    monkeypatch.setattr(documents, "resolve_or_404", exists)
    monkeypatch.setattr(documents, "can_read", cannot_read)
    caller = UserIdentity(user_id="u", username="u", auth_method="api_key")

    with pytest.raises(HTTPException) as hidden:
        await documents._resolve_edge_type_filter("secret:EDGE", "mine", "types", caller)
    assert hidden.value.status_code == 404
    assert hidden.value.detail == "Could not resolve types 'secret:EDGE' in namespace 'mine'"
    assert resolved == []  # never asked: the answer must not depend on existence

    # The request's own namespace, a bare value and a UUID are not gated here.
    assert await documents._resolve_edge_type_filter("mine:EDGE,EDGE", "mine", "types", caller) == [
        "T-EXISTS", "T-EXISTS",
    ]


@pytest.mark.asyncio
async def test_a_filter_naming_a_readable_namespace_resolves(monkeypatch):
    from document_store.api import documents
    from wip_auth.models import UserIdentity

    async def exists(*_args, **_kwargs):
        return "T-OTHER"

    async def can(_identity, _namespace):
        return True

    monkeypatch.setattr(documents, "resolve_or_404", exists)
    monkeypatch.setattr(documents, "can_read", can)
    caller = UserIdentity(user_id="u", username="u", auth_method="api_key")
    assert await documents._resolve_edge_type_filter("other:EDGE", "mine", "types", caller) == ["T-OTHER"]
