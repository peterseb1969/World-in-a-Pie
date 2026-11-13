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
DOCS_ONLY_READER = {"X-API-Key": "test_xns_only_key"}  # reads 'xns-closed', not 'wip'


async def _create(client: AsyncClient, headers: dict, namespace: str, national_id: str) -> str:
    resp = await client.post(f"{API}/documents", headers=headers, json=[{
        "namespace": namespace, "template_id": "wip:PERSON",
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

    # PERSON lives in 'wip': in another namespace it is named wip:PERSON.
    for ns, doc_id in two_namespaces.items():
        narrowed = await client.get(f"{API}/table/wip:PERSON", headers=BOTH_READER, params={"namespace": ns})
        assert narrowed.status_code == 200, narrowed.text
        assert [r["_document_id"] for r in narrowed.json()["rows"]] == [doc_id]
        assert narrowed.json()["total_documents"] == 1

        narrowed_csv = await client.get(
            f"{API}/table/wip:PERSON/csv", headers=BOTH_READER, params={"namespace": ns}
        )
        assert narrowed_csv.status_code == 200, narrowed_csv.text
        assert _csv_ids(narrowed_csv.text) == {doc_id}


@pytest.mark.asyncio
async def test_naming_a_namespace_the_caller_cannot_read_is_refused(client, two_namespaces):
    for path in ("table/PERSON", "table/PERSON/csv"):
        resp = await client.get(f"{API}/{path}", headers=WIP_READER, params={"namespace": "xns-closed"})
        assert resp.status_code in (403, 404), (path, resp.text)
        assert two_namespaces["xns-closed"] not in resp.text


@pytest.mark.asyncio
async def test_reading_documents_does_not_open_the_templates_layout(client, two_namespaces):
    """The table needs read on the template's namespace, not only on the
    documents': the columns are the template's whole declared layout, which
    the template store shows only to readers of its namespace. Provenance:
    CASE-1149 (decided: the gate stays)."""
    from .conftest import SAMPLE_TEMPLATES

    template_id = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    docs = await client.get(
        f"{API}/documents", headers=DOCS_ONLY_READER,
        params={"template_id": template_id, "namespace": "xns-closed"},
    )
    assert docs.status_code == 200, docs.text
    assert [d["document_id"] for d in docs.json()["items"]] == [two_namespaces["xns-closed"]]

    for path in (f"table/{template_id}", f"table/{template_id}/csv"):
        resp = await client.get(
            f"{API}/{path}", headers=DOCS_ONLY_READER, params={"namespace": "xns-closed"},
        )
        assert resp.status_code == 404, (path, resp.text)
        assert two_namespaces["xns-closed"] not in resp.text
        # Exactly what an id naming nothing answers: the gate does not
        # confirm the template exists.
        absent = "0190ffff-0000-7000-8000-000000001279"
        missing = await client.get(
            f"{API}/{path.replace(template_id, absent)}",
            headers=DOCS_ONLY_READER, params={"namespace": "xns-closed"},
        )
        assert missing.status_code == 404
        # Each echoes the id it was asked for, and nothing else differs.
        assert resp.json()["detail"].replace(template_id, absent) == missing.json()["detail"], (
            resp.text, missing.text,
        )


@pytest.mark.asyncio
async def test_with_a_namespace_a_bare_value_is_that_namespaces_template(client, two_namespaces):
    """namespace narrows the documents and is the context a bare template
    value resolves in, as on GET /documents. PERSON lives in 'wip', so in
    'xns-closed' it is named wip:PERSON; a bare value never falls back."""
    for path in ("table/PERSON", "table/PERSON/csv"):
        found = await client.get(f"{API}/{path}", headers=BOTH_READER, params={"namespace": "wip"})
        assert found.status_code == 200, (path, found.text)
        missing = await client.get(f"{API}/{path}", headers=BOTH_READER, params={"namespace": "xns-closed"})
        assert missing.status_code == 404, (path, missing.text)
        qualified = await client.get(
            f"{API}/{path.replace('PERSON', 'wip:PERSON')}", headers=BOTH_READER,
            params={"namespace": "xns-closed"},
        )
        assert qualified.status_code == 200, (path, qualified.text)

    rows = (await client.get(f"{API}/table/wip:PERSON", headers=BOTH_READER,
                             params={"namespace": "xns-closed"})).json()["rows"]
    assert [r["_document_id"] for r in rows] == [two_namespaces["xns-closed"]]


@pytest.mark.asyncio
async def test_a_canonical_template_id_crosses_namespaces(client, two_namespaces):
    """A template id needs no namespace and is not bound to one: the id of
    wip's PERSON with namespace=xns-closed shows xns-closed's documents."""
    head = (await client.get(f"{API}/table/wip:PERSON", headers=BOTH_READER,
                             params={"namespace": "wip"})).json()
    template_id = head["template_id"]
    resp = await client.get(f"{API}/table/{template_id}", headers=BOTH_READER,
                            params={"namespace": "xns-closed"})
    assert resp.status_code == 200, resp.text
    assert [r["_document_id"] for r in resp.json()["rows"]] == [two_namespaces["xns-closed"]]
