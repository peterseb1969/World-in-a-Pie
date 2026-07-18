"""The validation endpoints' namespace read-gate is unconditional (CASE-647).

`namespace` is a required field on both validation request models, so a
conditional `if request.namespace:` gate guarded nothing legitimate — its
only reachable effect was that an empty-string namespace passed pydantic,
evaluated falsy, and skipped `check_namespace_permission` entirely.
Validation reveals template structure + term-reference resolution, which is
exactly what the gate exists to protect.

Pins the contract on both routes (/validation/validate, /validate-bulk):

1. `namespace: ""` is rejected at parse time (422, min_length=1) — it never
   reaches permission or service logic.
2. A non-admin key without access to the target namespace gets 404 (the
   invisible-namespace convention), proving the gate runs unconditionally.
3. An authorized caller still validates normally (no regression).
"""

import pytest
from httpx import AsyncClient

VALIDATE = "/api/document-store/validation/validate"
VALIDATE_BULK = "/api/document-store/validation/validate-bulk"

SCOPED_KEY_HEADERS = {"X-API-Key": "test_scoped_key"}


@pytest.mark.asyncio
async def test_empty_namespace_rejected_at_parse_singular(
    client: AsyncClient, auth_headers: dict
):
    """namespace='' fails model validation (422) instead of skipping the gate."""
    response = await client.post(
        VALIDATE,
        headers=auth_headers,
        json={"template_id": "PERSON", "namespace": "", "data": {}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_empty_namespace_rejected_at_parse_bulk(
    client: AsyncClient, auth_headers: dict
):
    """Bulk mirror of the empty-namespace 422."""
    response = await client.post(
        VALIDATE_BULK,
        headers=auth_headers,
        json={"template_id": "PERSON", "namespace": "", "items": [{}]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unauthorized_namespace_gated_singular(client: AsyncClient):
    """A non-admin key with no access to 'wip' gets 404 (invisible namespace),
    proving the read gate runs before any template/validation logic."""
    response = await client.post(
        VALIDATE,
        headers=SCOPED_KEY_HEADERS,
        json={"template_id": "PERSON", "namespace": "wip", "data": {}},
    )
    assert response.status_code == 404
    assert "Namespace not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unauthorized_namespace_gated_bulk(client: AsyncClient):
    """Bulk mirror of the unauthorized-namespace 404."""
    response = await client.post(
        VALIDATE_BULK,
        headers=SCOPED_KEY_HEADERS,
        json={"template_id": "PERSON", "namespace": "wip", "items": [{}]},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_authorized_validation_still_works(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """No regression: an authorized caller validates normally."""
    response = await client.post(
        VALIDATE,
        headers=auth_headers,
        json={
            "template_id": "PERSON",
            "namespace": "wip",
            "data": sample_person_data,
        },
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True
