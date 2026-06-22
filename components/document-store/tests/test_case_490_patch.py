"""CASE-490 Piece 1 — a PATCH whose pinned template version is inactive returns
a distinct, branchable ``template_inactive`` error_code instead of being buried
in the generic ``validation_failed``, so a caller (and the kb gateway, which
should map it to a 4xx rather than a 502) can detect a "frozen template" cleanly.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient


class _InactiveTemplateClient:
    """Stub whose pinned-version resolution reports the template as inactive."""

    async def get_template_resolved(self, template_id, version=None):
        return {
            "template_id": template_id,
            "version": version or 1,
            "status": "inactive",
            "namespace": "wip",
            "fields": [],
            "identity_fields": ["national_id"],
        }


async def _create_person(client: AsyncClient, auth_headers: dict, data: dict) -> str:
    resp = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": "PERSON", "data": data}],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["document_id"]


async def _patch(client: AsyncClient, auth_headers: dict, doc_id: str, patch_body: dict) -> dict:
    resp = await client.patch(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"document_id": doc_id, "patch": patch_body}],
    )
    assert resp.status_code == 200, resp.text  # bulk-first: always 200
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_patch_inactive_pinned_template_yields_template_inactive_code(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    doc_id = await _create_person(client, auth_headers, sample_person_data)

    # Re-validation now sees the pinned template version as inactive.
    with patch(
        "document_store.services.validation_service.get_template_store_client",
        return_value=_InactiveTemplateClient(),
    ):
        item = await _patch(client, auth_headers, doc_id, {"first_name": "Jane"})

    assert item["status"] == "error"
    assert item["error_code"] == "template_inactive"
    assert "not active" in item["error"].lower()


@pytest.mark.asyncio
async def test_patch_other_validation_failure_stays_validation_failed(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Regression guard: a non-inactive validation failure (here an unknown
    field, against the still-active template) keeps the generic code."""
    doc_id = await _create_person(client, auth_headers, sample_person_data)

    item = await _patch(client, auth_headers, doc_id, {"bogus_field": "x"})

    assert item["status"] == "error"
    assert item["error_code"] == "validation_failed"
