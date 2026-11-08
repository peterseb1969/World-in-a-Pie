"""Only edge-type documents are edges; traverse reads endpoints in one query.

Provenance: CASE-1146 (relationships slice).

/relationships and /traverse found edges by data.source_ref / data.target_ref
alone, so a document of an ENTITY template that declares a field of that
name was listed — and walked — as an edge. Both now count only documents of
templates with usage='relationship'.
"""

import uuid

import pytest

from document_store.models.document import Document, DocumentStatus

from .conftest import SAMPLE_TEMPLATES

DOCS = "/api/document-store/documents"


async def _person(client, auth_headers, national_id: str) -> str:
    resp = await client.post(DOCS, headers=auth_headers, json=[{
        "namespace": "wip", "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": "Ada", "last_name": "Edge"},
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["document_id"]


async def _entity_row_pointing_at(seed: str, target: str) -> str:
    """A PERSON (entity) document that happens to carry source_ref/target_ref."""
    document_id = str(uuid.uuid4())
    await Document(
        namespace="wip", document_id=document_id,
        template_id=SAMPLE_TEMPLATES["PERSON"]["template_id"], template_value="PERSON",
        template_version=1, identity_hash=f"not-an-edge-{document_id}", version=1,
        data={"source_ref": seed, "target_ref": target}, status=DocumentStatus.ACTIVE,
    ).insert()
    return document_id


@pytest.mark.asyncio
async def test_an_entity_document_with_source_ref_is_not_an_edge(client, auth_headers):
    seed = await _person(client, auth_headers, "114610001")
    other = await _person(client, auth_headers, "114610002")
    impostor = await _entity_row_pointing_at(seed, other)

    rels = await client.get(f"{DOCS}/{seed}/relationships", headers=auth_headers,
                            params={"namespace": "wip"})
    assert rels.status_code == 200, rels.text
    assert impostor not in [item["document_id"] for item in rels.json()["items"]]
    assert rels.json()["total"] == 0

    walk = await client.get(f"{DOCS}/{seed}/traverse", headers=auth_headers,
                            params={"namespace": "wip", "depth": 2})
    assert walk.status_code == 200, walk.text
    assert walk.json()["nodes"] == []


@pytest.mark.asyncio
async def test_traverse_reads_a_hops_endpoints_in_one_query(client, auth_headers, monkeypatch):
    """A real edge is still walked; its endpoint is read with the hop's batch
    query, not one find_one per node (the seed lookup is the only one)."""
    async def create(value, data):
        resp = await client.post(DOCS, headers=auth_headers, json=[{
            "namespace": "wip", "template_id": value, "data": data,
        }])
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["status"] == "created", result
        return result["document_id"]

    exp = await create("EXPERIMENT", {"experiment_id": "EXP-1146", "name": "e"})
    mol = await create("MOLECULE", {"molecule_id": "MOL-1146", "name": "m"})
    edge = await create("EXPERIMENT_INPUT", {"source_ref": exp, "target_ref": mol, "role": "input"})

    real_find_one = Document.find_one
    calls = []

    def counting_find_one(*args, **kwargs):
        calls.append(args)
        return real_find_one(*args, **kwargs)

    monkeypatch.setattr(Document, "find_one", counting_find_one)
    walk = await client.get(f"{DOCS}/{exp}/traverse", headers=auth_headers,
                            params={"namespace": "wip", "depth": 1})
    assert walk.status_code == 200, walk.text
    nodes = walk.json()["nodes"]
    assert [(n["document_id"], n["via_relationship"], n["depth"]) for n in nodes] == [(mol, edge, 1)]
    assert len(calls) == 1, calls
