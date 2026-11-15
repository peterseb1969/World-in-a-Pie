"""Regression coverage for CASE-763 — ambiguous unscoped terminology-value reads.

A terminology value is unique only within its namespace. Before this fix,
a value-form read with no namespace context fell back to a bare global
find_one: with the same value live in several namespaces the caller got an
arbitrary (natural-order) namespace's copy — plausible wrong data, no
error. The contract under test:

  - Scoped value reads return exactly their namespace's copy.
  - An unscoped value read with the value in >1 namespace is rejected
    loud (HTTP 400 on GET routes; valid=False with an explanatory error
    in the validate response shape), naming the candidate namespaces.
  - An unscoped value read of a globally unique value still resolves —
    the ergonomic single-tenant path keeps working.
"""

import pytest
from httpx import AsyncClient

VALUE = "C763_AMBIG_VOC"
UNIQUE_VALUE = "C763_UNIQUE_VOC"


async def _create(client: AsyncClient, auth_headers: dict, value: str, namespace: str):
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": value, "label": f"{value} ({namespace})", "namespace": namespace}],
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "created", item
    return item["id"]


async def _create_duplicates(client: AsyncClient, auth_headers: dict) -> tuple[str, str]:
    wip_id = await _create(client, auth_headers, VALUE, "wip")
    other_id = await _create(client, auth_headers, VALUE, "test-ns")
    return wip_id, other_id


@pytest.mark.asyncio
async def test_scoped_value_read_returns_the_namespaces_copy(
    client: AsyncClient, auth_headers: dict
):
    wip_id, other_id = await _create_duplicates(client, auth_headers)

    for ns, expected_id in (("wip", wip_id), ("test-ns", other_id)):
        resp = await client.get(
            f"/api/def-store/terminologies/{VALUE}",
            headers=auth_headers,
            params={"namespace": ns},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["namespace"] == ns
        assert body["terminology_id"] == expected_id


@pytest.mark.asyncio
async def test_unscoped_value_read_with_duplicates_is_rejected(
    client: AsyncClient, auth_headers: dict
):
    await _create_duplicates(client, auth_headers)

    for path in (
        f"/api/def-store/terminologies/{VALUE}",
        f"/api/def-store/terminologies/by-value/{VALUE}",
    ):
        resp = await client.get(path, headers=auth_headers)
        assert resp.status_code == 400, (path, resp.text)
        detail = resp.json()["detail"]
        assert "test-ns" in detail and "wip" in detail
        assert VALUE in detail


@pytest.mark.asyncio
async def test_unscoped_unique_value_still_resolves(
    client: AsyncClient, auth_headers: dict
):
    await _create(client, auth_headers, UNIQUE_VALUE, "test-ns")

    resp = await client.get(
        f"/api/def-store/terminologies/{UNIQUE_VALUE}", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["namespace"] == "test-ns"


@pytest.mark.asyncio
async def test_list_terms_value_fallback_scoping(
    client: AsyncClient, auth_headers: dict
):
    await _create_duplicates(client, auth_headers)

    unscoped = await client.get(
        f"/api/def-store/terminologies/{VALUE}/terms", headers=auth_headers
    )
    assert unscoped.status_code == 400
    assert "test-ns" in unscoped.json()["detail"]

    scoped = await client.get(
        f"/api/def-store/terminologies/{VALUE}/terms",
        headers=auth_headers,
        params={"namespace": "test-ns"},
    )
    assert scoped.status_code == 200


@pytest.mark.asyncio
async def test_validate_resolves_a_value_only_in_the_namespace_given(
    client: AsyncClient, auth_headers: dict
):
    # The terminology identifier resolves through the Registry in the
    # request's namespace; it is never searched across namespaces, so a
    # value two namespaces carry is neither ambiguous nor guessed.
    wip_id, other_id = await _create_duplicates(client, auth_headers)

    for ns, expected in (("wip", wip_id), ("test-ns", other_id)):
        single = await client.post(
            "/api/def-store/validate", headers=auth_headers, params={"namespace": ns},
            json={"terminology_id": VALUE, "value": "anything"},
        )
        assert single.status_code == 200
        body = single.json()
        assert body["terminology_id"] == expected and body["error_code"] == "term_not_found", body

    qualified = await client.post(
        "/api/def-store/validate", headers=auth_headers,
        json={"terminology_id": f"test-ns:{VALUE}", "value": "anything"},
    )
    assert qualified.json()["terminology_id"] == other_id, qualified.json()

    # No namespace and a key with several: the value names no namespace.
    single = await client.post(
        "/api/def-store/validate", headers=auth_headers,
        json={"terminology_id": VALUE, "value": "anything"},
    )
    assert single.status_code == 200
    body = single.json()
    assert body["valid"] is False and body["error_code"] == "invalid_request", body
    assert "namespace" in body["error"]

    bulk = await client.post(
        "/api/def-store/validate/bulk", headers=auth_headers,
        json={"items": [{"terminology_id": VALUE, "value": "anything"}]},
    )
    assert bulk.status_code == 200
    item = bulk.json()["results"][0]
    assert item["valid"] is False and item["error_code"] == "invalid_request", item


@pytest.mark.asyncio
async def test_validate_refuses_the_removed_terminology_value_field(
    client: AsyncClient, auth_headers: dict
):
    resp = await client.post(
        "/api/def-store/validate", headers=auth_headers, params={"namespace": "wip"},
        json={"terminology_value": VALUE, "value": "anything"},
    )
    assert resp.status_code == 422, resp.text
