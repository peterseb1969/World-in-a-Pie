"""PATCH's refusals and its race exits, pinned before _patch_one's refactor.

Provenance: CASE-1161. These branches of DocumentService._patch_one had no
test: a soft-deleted document, a Registry that cannot say what the caller may
do, no permission and read-only permission, a reference violation and an
unreachable reference check, losing the version race on every retry, and an
in-place (versioned:false) write losing every race until the retries run
out. Documents are created through the API against the real in-process
Registry; each test breaks one step.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from pymongo.errors import DuplicateKeyError

from document_store.services.document_service import DocumentService
from document_store.services.reference_validator import (
    ReferenceValidationError,
    ReferenceValidator,
    RegistryUnavailableError,
)

URL = "/api/document-store/documents"


async def _person(client, headers, nid: str) -> str:
    resp = await client.post(URL, headers=headers, json=[{
        "template_id": "PERSON", "namespace": "wip",
        "data": {"national_id": nid, "first_name": "Ann", "last_name": "Lee"},
    }])
    item = resp.json()["results"][0]
    assert item["status"] == "created", item
    return item["document_id"]


async def _patch(client, headers, doc_id: str, **extra) -> dict:
    resp = await client.patch(URL, headers=headers, json=[{
        "document_id": doc_id, "patch": {"first_name": "Bea"}, **extra,
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_a_soft_deleted_document_is_not_found(client: AsyncClient, auth_headers: dict):
    doc_id = await _person(client, auth_headers, "116110001")
    await client.request("DELETE", URL, headers=auth_headers, json=[{"id": doc_id}])
    result = await _patch(client, auth_headers, doc_id)
    assert (result["status"], result["error_code"]) == ("error", "not_found")


@pytest.mark.asyncio
@pytest.mark.parametrize(("permission", "code", "nid"), [
    ("__unavailable__", "registry_error", "116110011"),
    ("none", "not_found", "116110012"),
    ("read", "forbidden", "116110013"),
])
async def test_the_callers_permission_decides(client: AsyncClient, auth_headers: dict, permission, code, nid):
    from wip_auth.permissions import PERMISSION_UNAVAILABLE

    doc_id = await _person(client, auth_headers, nid)
    answer = PERMISSION_UNAVAILABLE if permission == "__unavailable__" else permission
    with patch("wip_auth.resolve_permission", AsyncMock(return_value=answer)):
        result = await _patch(client, auth_headers, doc_id)
    assert (result["status"], result["error_code"]) == ("error", code)


@pytest.mark.asyncio
@pytest.mark.parametrize(("raised", "code", "nid"), [
    (ReferenceValidationError("refused", [{"field": "x"}]), "reference_violation", "116110021"),
    (RegistryUnavailableError("registry down"), "registry_error", "116110022"),
])
async def test_a_refused_reference_check_fails_the_patch(
    client: AsyncClient, auth_headers: dict, raised, code, nid,
):
    doc_id = await _person(client, auth_headers, nid)
    with patch.object(ReferenceValidator, "validate_document_references", AsyncMock(side_effect=raised)):
        result = await _patch(client, auth_headers, doc_id)
    assert (result["status"], result["error_code"]) == ("error", code)


@pytest.mark.asyncio
async def test_losing_the_version_race_on_every_retry_is_a_conflict(
    client: AsyncClient, auth_headers: dict,
):
    doc_id = await _person(client, auth_headers, "116110031")
    # The insert under _insert_or_restore loses; the restore around it puts
    # the deactivated version back, so each retry finds the document again.
    with patch.object(DocumentService, "_insert_with_retry",
                      AsyncMock(side_effect=DuplicateKeyError("dup"))):
        result = await _patch(client, auth_headers, doc_id)
    assert (result["status"], result["error_code"]) == ("error", "concurrency_conflict")
    assert "Failed after 3 retries" in result["error"]
    # And the document is left as it was: active, at version 1.
    got = (await client.get(f"{URL}/{doc_id}", headers=auth_headers)).json()
    assert (got["status"], got["version"], got["data"]["first_name"]) == ("active", 1, "Ann")


@pytest.mark.asyncio
async def test_an_in_place_write_that_keeps_losing_is_a_conflict(
    client: AsyncClient, auth_headers: dict,
):
    doc_id = await _person(client, auth_headers, "116110041")
    lost = AsyncMock(return_value=False)
    with patch.object(DocumentService, "_is_template_versioned", AsyncMock(return_value=False)), \
            patch.object(DocumentService, "_write_in_place", lost):
        result = await _patch(client, auth_headers, doc_id)
        with_token = await _patch(client, auth_headers, doc_id, if_match=1)
    # Without a token it re-reads and tries again, three times in all.
    assert (result["status"], result["error_code"]) == ("error", "concurrency_conflict")
    assert lost.await_count == 4  # three without a token, then one with it
    assert (with_token["status"], with_token["error_code"]) == ("error", "concurrency_conflict")
    assert "written concurrently" in with_token["error"]
