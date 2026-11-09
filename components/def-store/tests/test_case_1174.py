"""A by-value read sees only namespaces the caller can read; a term pointer
into another namespace is refused like one that names nothing; the
ontology import keeps its permission refusal's status code.

A terminology value is unique only within a namespace. Looked up without a
namespace, it is searched in the namespaces the caller can read, so a key
neither receives another namespace's record nor learns, from an ambiguity
error, which namespaces carry the value.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

API = "/api/def-store"


async def _terminology(client, auth_headers, value, namespace):
    response = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"value": value, "label": value.title(), "namespace": namespace}],
    )
    data = response.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _terms(client, auth_headers, terminology_id, *values):
    response = await client.post(
        f"{API}/terminologies/{terminology_id}/terms", headers=auth_headers,
        json=[{"value": v} for v in values],
    )
    data = response.json()
    assert data["succeeded"] == len(values), data
    return [r["id"] for r in data["results"]]


def _readable(*namespaces):
    """Patch every door's accessible-namespace lookup to `namespaces`."""
    fake = AsyncMock(return_value=list(namespaces))
    return (
        patch("def_store.api.terminologies.resolve_accessible_namespaces", fake, create=True),
        patch("def_store.api.terms.resolve_accessible_namespaces", fake, create=True),
    )


@pytest.mark.asyncio
async def test_by_value_without_namespace_does_not_return_an_unreadable_record(
    client: AsyncClient, auth_headers: dict
):
    await _terminology(client, auth_headers, "PROBE_1174_ONLY_THERE", "test-ns")
    p1, p2 = _readable("wip")
    with p1, p2:
        response = await client.get(
            f"{API}/terminologies/by-value/PROBE_1174_ONLY_THERE", headers=auth_headers,
        )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_by_value_without_namespace_picks_the_readable_one_and_names_no_other(
    client: AsyncClient, auth_headers: dict
):
    mine = await _terminology(client, auth_headers, "PROBE_1174_TWICE", "wip")
    await _terminology(client, auth_headers, "PROBE_1174_TWICE", "test-ns")
    p1, p2 = _readable("wip")
    with p1, p2:
        response = await client.get(
            f"{API}/terminologies/by-value/PROBE_1174_TWICE", headers=auth_headers,
        )
    assert response.status_code == 200, response.text
    assert response.json()["terminology_id"] == mine


@pytest.mark.asyncio
async def test_validate_by_value_does_not_reveal_an_unreadable_terminology(
    client: AsyncClient, auth_headers: dict
):
    await _terminology(client, auth_headers, "PROBE_1174_VALIDATE", "wip")
    await _terminology(client, auth_headers, "PROBE_1174_VALIDATE", "test-ns")
    p1, p2 = _readable("wip")
    with p1, p2:
        response = await client.post(
            f"{API}/validate", headers=auth_headers,
            json={"terminology_value": "PROBE_1174_VALIDATE", "value": "x"},
        )
    body = response.json()
    assert response.status_code == 200, response.text
    assert "test-ns" not in str(body), body
    assert body["error_code"] != "ambiguous_terminology", body


@pytest.mark.asyncio
async def test_bulk_validate_by_value_does_not_reveal_an_unreadable_terminology(
    client: AsyncClient, auth_headers: dict
):
    await _terminology(client, auth_headers, "PROBE_1174_BULK", "test-ns")
    p1, p2 = _readable("wip")
    with p1, p2:
        response = await client.post(
            f"{API}/validate/bulk", headers=auth_headers,
            json={"items": [{"terminology_value": "PROBE_1174_BULK", "value": "x"}]},
        )
    assert response.status_code == 200, response.text
    (item,) = response.json()["results"]
    assert item["error_code"] == "not_found", item


@pytest.mark.asyncio
async def test_a_pointer_into_another_namespace_reads_as_naming_nothing(
    client: AsyncClient, auth_headers: dict
):
    """parent_term_id naming a term of another namespace gets the refusal a
    nonexistent id gets, so the refusal confirms nothing about it."""
    foreign_tid = await _terminology(client, auth_headers, "PROBE_1174_FOREIGN", "test-ns")
    (foreign_term,) = await _terms(client, auth_headers, foreign_tid, "elsewhere")
    tid = await _terminology(client, auth_headers, "PROBE_1174_HOME", "wip")

    answers = []
    for pointer in (foreign_term, "019a0000-0000-7000-8000-000000000000"):
        response = await client.post(
            f"{API}/terminologies/{tid}/terms", headers=auth_headers,
            json=[{"value": f"child-{len(answers)}", "parent_term_id": pointer}],
        )
        (item,) = response.json()["results"]
        assert item["status"] == "error", item
        answers.append((item["error_code"], item["error"].replace(pointer, "<id>")))
    assert answers[0] == answers[1], answers


@pytest.mark.asyncio
async def test_import_ontology_keeps_the_permission_refusal(
    client: AsyncClient, auth_headers: dict
):
    async def deny(*_args, **_kwargs):
        raise HTTPException(403, "Requires write")

    with patch("def_store.api.import_export.check_namespace_permission", side_effect=deny):
        response = await client.post(
            f"{API}/import-export/import-ontology", headers=auth_headers,
            params={"namespace": "wip"}, json={"graphs": [{"nodes": [], "edges": []}]},
        )
    assert response.status_code == 403, response.text
