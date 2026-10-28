"""The maintenance endpoints publish typed responses.

Both passes returned a bare dict, so the OpenAPI snapshot and every client
built from it had no response type. These tests pin the published shape.
"""

import pytest
from httpx import AsyncClient

from document_store.main import app

REHASH = "/api/document-store/maintenance/rehash-identities"
REQUALIFY = "/api/document-store/maintenance/requalify-identity-synonyms"


def _response_schema(path: str) -> dict:
    spec = app.openapi()
    ref = spec["paths"][path]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    return spec["components"]["schemas"][ref.rsplit("/", 1)[1]]


def test_rehash_response_is_typed():
    schema = _response_schema(REHASH)
    assert set(schema["required"]) >= {
        "namespace", "dry_run", "scanned", "changed", "unchanged", "unresolved",
        "collisions", "errors", "applied", "items", "collision_groups", "reporting_note",
    }
    spec = app.openapi()["components"]["schemas"]
    item = spec["RehashItemResponse"]
    assert item["properties"]["status"]["enum"] == [
        "changed", "unresolved", "collision", "error",
    ]
    assert set(spec["RehashCollisionGroup"]["required"]) == {
        "template_id", "identity_hash", "document_ids",
    }


def test_requalify_response_is_typed():
    schema = _response_schema(REQUALIFY)
    assert set(schema["required"]) >= {
        "namespace", "dry_run", "scanned", "requalified", "unchanged",
        "no_entry", "errors", "items",
    }
    item = app.openapi()["components"]["schemas"]["RequalifyItemResponse"]
    assert item["properties"]["status"]["enum"] == ["requalified", "no_entry", "error"]


@pytest.mark.asyncio
async def test_dry_run_responses_validate_against_the_models(
    client: AsyncClient, auth_headers: dict,
):
    rehash = await client.post(
        REHASH, headers=auth_headers, json={"namespace": "wip"},
    )
    assert rehash.status_code == 200, rehash.text
    body = rehash.json()
    assert body["dry_run"] is True
    assert body["applied"] == 0

    requalify = await client.post(
        REQUALIFY, headers=auth_headers, json={"namespace": "wip"},
    )
    assert requalify.status_code == 200, requalify.text
    assert requalify.json()["dry_run"] is True
