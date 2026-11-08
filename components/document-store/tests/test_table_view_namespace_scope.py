"""The table view and its CSV export show only documents the caller can read.

A document's namespace is independent of its template's: documents in
'xns-closed' can be written on a template that lives in 'wip'. Both routes
checked read on the TEMPLATE's namespace and then queried every document on
the template, so a key that reads only 'wip' got another namespace's rows,
identity hashes and document count. They now apply the caller's namespace
filter to the count and the fetch, optionally narrowed by `namespace`.
"""

import csv
import io

import pytest
from httpx import AsyncClient

API = "/api/document-store"
WIP_READER = {"X-API-Key": "test_xns_writer_key"}  # reads 'wip' only
BOTH_READER = {"X-API-Key": "test_xns_reader_key"}  # reads 'wip' and 'xns-closed'


async def _create(client: AsyncClient, headers: dict, namespace: str, national_id: str) -> str:
    resp = await client.post(f"{API}/documents", headers=headers, json=[{
        "namespace": namespace, "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": namespace, "last_name": "Scope"},
    }])
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] == "created", result
    return result["document_id"]


@pytest.fixture
async def two_namespaces(client: AsyncClient, auth_headers: dict) -> dict[str, str]:
    return {
        "wip": await _create(client, auth_headers, "wip", "113500001"),
        "xns-closed": await _create(client, auth_headers, "xns-closed", "113500002"),
    }


def _csv_ids(text: str) -> set[str]:
    return {row["_document_id"] for row in csv.DictReader(io.StringIO(text))}


@pytest.mark.asyncio
async def test_a_reader_of_the_template_namespace_sees_only_its_own_documents(client, two_namespaces):
    resp = await client.get(f"{API}/table/PERSON", headers=WIP_READER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r["_document_id"] for r in body["rows"]} == {two_namespaces["wip"]}
    assert body["total_documents"] == 1

    csv_resp = await client.get(f"{API}/table/PERSON/csv", headers=WIP_READER)
    assert csv_resp.status_code == 200, csv_resp.text
    assert _csv_ids(csv_resp.text) == {two_namespaces["wip"]}


@pytest.mark.asyncio
async def test_a_reader_of_both_sees_both_and_namespace_narrows(client, two_namespaces):
    resp = await client.get(f"{API}/table/PERSON", headers=BOTH_READER)
    assert resp.status_code == 200, resp.text
    assert {r["_document_id"] for r in resp.json()["rows"]} == set(two_namespaces.values())

    for ns, doc_id in two_namespaces.items():
        narrowed = await client.get(f"{API}/table/PERSON", headers=BOTH_READER, params={"namespace": ns})
        assert narrowed.status_code == 200, narrowed.text
        assert [r["_document_id"] for r in narrowed.json()["rows"]] == [doc_id]
        assert narrowed.json()["total_documents"] == 1

        narrowed_csv = await client.get(
            f"{API}/table/PERSON/csv", headers=BOTH_READER, params={"namespace": ns}
        )
        assert narrowed_csv.status_code == 200, narrowed_csv.text
        assert _csv_ids(narrowed_csv.text) == {doc_id}


@pytest.mark.asyncio
async def test_naming_a_namespace_the_caller_cannot_read_is_refused(client, two_namespaces):
    for path in ("table/PERSON", "table/PERSON/csv"):
        resp = await client.get(f"{API}/{path}", headers=WIP_READER, params={"namespace": "xns-closed"})
        assert resp.status_code in (403, 404), (path, resp.text)
        assert two_namespaces["xns-closed"] not in resp.text
