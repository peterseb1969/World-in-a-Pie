"""Template facets: which templates are a namespace's documents instances of?

A document's namespace is independent of its template's namespace, so an
owner-based template listing (`GET /templates?namespace=X`) cannot answer
this — a namespace whose documents sit on shared or foreign templates looks
empty to it. `/documents/template-facets` groups the answer from the
documents themselves: distinct logical documents per template (version rows
collapse on document_id), labeled with the template's own namespace.
"""

import uuid

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus

from .conftest import SAMPLE_TEMPLATES

DOCUMENTS = "/api/document-store/documents"
FACETS = "/api/document-store/documents/template-facets"


async def _create_person(client: AsyncClient, auth_headers: dict, national_id: str, **extra) -> dict:
    data = {
        "national_id": national_id,
        "first_name": "Ada",
        "last_name": "Facet",
    }
    data.update(extra)
    resp = await client.post(
        DOCUMENTS,
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": "PERSON", "data": data}],
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated"), item
    return item


def _facet_for(body: dict, template_value: str) -> dict | None:
    return next(
        (f for f in body["facets"] if f["template_value"] == template_value),
        None,
    )


@pytest.mark.asyncio
async def test_facets_count_logical_documents_not_version_rows(
    client: AsyncClient, auth_headers: dict
):
    """Three logical PERSON documents, one updated to version 2: the facet
    counts 3, not 4 — version rows collapse on document_id."""
    await _create_person(client, auth_headers, "739000001")
    await _create_person(client, auth_headers, "739000002")
    await _create_person(client, auth_headers, "739000003")
    # Same identity, changed payload → new VERSION of doc 1, not a new doc.
    updated = await _create_person(
        client, auth_headers, "739000001", first_name="Grace"
    )
    assert updated["version"] == 2, updated

    resp = await client.get(FACETS, headers=auth_headers, params={"namespace": "wip"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["namespace"] == "wip"

    person = _facet_for(body, "PERSON")
    assert person is not None, body
    assert person["document_count"] == 3
    assert person["template_namespace"] == "wip"
    assert person["template_id"] == SAMPLE_TEMPLATES["PERSON"]["template_id"]


@pytest.mark.asyncio
async def test_facets_status_filter_and_all(
    client: AsyncClient, auth_headers: dict
):
    """Default counts active only; status=all disables the filter."""
    a = await _create_person(client, auth_headers, "739000011")
    await _create_person(client, auth_headers, "739000012")

    doc_a = await Document.find_one({
        "document_id": a["document_id"], "status": DocumentStatus.ACTIVE.value,
    })
    doc_a.status = DocumentStatus.ARCHIVED
    await doc_a.save()

    active = (await client.get(
        FACETS, headers=auth_headers, params={"namespace": "wip"},
    )).json()
    assert _facet_for(active, "PERSON")["document_count"] == 1

    everything = (await client.get(
        FACETS, headers=auth_headers, params={"namespace": "wip", "status": "all"},
    )).json()
    assert _facet_for(everything, "PERSON")["document_count"] == 2

    archived = (await client.get(
        FACETS, headers=auth_headers,
        params={"namespace": "wip", "status": "archived"},
    )).json()
    assert _facet_for(archived, "PERSON")["document_count"] == 1


@pytest.mark.asyncio
async def test_facets_label_foreign_template_and_degrade_unknown(
    client: AsyncClient, auth_headers: dict
):
    """A document sitting on another namespace's template is faceted with
    that template's own namespace; a template the template-store cannot
    return degrades to template_namespace=None instead of failing."""
    foreign_id = str(uuid.uuid4())
    SAMPLE_TEMPLATES[foreign_id] = {
        "template_id": foreign_id,
        "value": "XNS_SHARED",
        "namespace": "other-ns",
        "status": "active",
        "version": 1,
        "identity_fields": [],
        "fields": [],
        "rules": [],
    }
    try:
        rows = [
            Document(
                namespace="wip",
                document_id=str(uuid.uuid4()),
                template_id=foreign_id,
                template_version=1,
                template_value="XNS_SHARED",
                data={"n": 1},
            ),
            Document(
                namespace="wip",
                document_id=str(uuid.uuid4()),
                template_id="TPL-NOWHERE",
                template_version=1,
                template_value="GHOST",
                data={"n": 2},
            ),
        ]
        for row in rows:
            await row.insert()

        resp = await client.get(
            FACETS, headers=auth_headers, params={"namespace": "wip"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        shared = _facet_for(body, "XNS_SHARED")
        assert shared is not None, body
        assert shared["template_namespace"] == "other-ns"
        assert shared["document_count"] == 1

        ghost = _facet_for(body, "GHOST")
        assert ghost is not None, body
        assert ghost["template_namespace"] is None
        assert ghost["document_count"] == 1
    finally:
        SAMPLE_TEMPLATES.pop(foreign_id, None)


@pytest.mark.asyncio
async def test_facets_reject_bad_status(client: AsyncClient, auth_headers: dict):
    resp = await client.get(
        FACETS, headers=auth_headers,
        params={"namespace": "wip", "status": "bogus"},
    )
    assert resp.status_code == 422
