"""Edges across namespaces, end to end.

An edge lives in its source's namespace; its target may be in another
namespace, gated like any reference. Here the EXPERIMENT (source) and the
edge are in 'wip' and the MOLECULE (target) is in 'xns-closed', a namespace
the Registry has no settings for, so it reads as closed. Two non-admin keys
may write 'wip'; only the reader can also read 'xns-closed'.
"""

import pytest
from httpx import AsyncClient

API = "/api/document-store"
READER = {"X-API-Key": "test_xns_reader_key"}
WRITER = {"X-API-Key": "test_xns_writer_key"}


async def _create(client: AsyncClient, headers: dict, namespace: str, template: str, data: dict) -> dict:
    resp = await client.post(f"{API}/documents", headers=headers, json=[{
        "namespace": namespace, "template_id": template, "data": data,
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.fixture
async def endpoints(client: AsyncClient, auth_headers: dict) -> tuple[str, str]:
    exp = await _create(client, auth_headers, "wip", "EXPERIMENT", {"experiment_id": "EXP-X", "name": "x"})
    mol = await _create(client, auth_headers, "xns-closed", "MOLECULE", {"molecule_id": "MOL-X", "name": "x"})
    assert exp["status"] == "created", exp
    assert mol["status"] == "created", mol
    return exp["document_id"], mol["document_id"]


async def _edge(client: AsyncClient, headers: dict, exp_id: str, mol_id: str) -> dict:
    return await _create(client, headers, "wip", "EXPERIMENT_INPUT",
                         {"source_ref": exp_id, "target_ref": mol_id, "role": "input"})


@pytest.mark.asyncio
async def test_a_target_in_another_namespace_needs_the_writers_read(client, endpoints):
    exp_id, mol_id = endpoints
    refused = await _edge(client, WRITER, exp_id, mol_id)
    assert refused["status"] == "error"
    assert refused["error_code"] == "validation_failed"
    assert refused["details"]["errors"][0]["code"] == "reference_not_found"
    assert "xns-closed" not in str(refused)

    created = await _edge(client, READER, exp_id, mol_id)
    assert created["status"] == "created", created


@pytest.mark.asyncio
async def test_incoming_edges_from_another_namespace_are_listed(client, auth_headers, endpoints):
    exp_id, mol_id = endpoints
    edge = await _edge(client, READER, exp_id, mol_id)
    assert edge["status"] == "created", edge

    # The edge lives in 'wip'; the molecule is in 'xns-closed'. Its default
    # listing spans the namespaces the caller can read, so the edge shows.
    for headers in (auth_headers, READER):
        resp = await client.get(f"{API}/documents/{mol_id}/relationships", headers=headers,
                                params={"direction": "incoming"})
        assert resp.status_code == 200, resp.text
        assert [i["document_id"] for i in resp.json()["items"]] == [edge["document_id"]]


@pytest.mark.asyncio
async def test_a_peer_the_caller_cannot_read_reports_not_found(client, endpoints):
    exp_id, mol_id = endpoints
    assert (await _edge(client, READER, exp_id, mol_id))["status"] == "created"

    resp = await client.get(f"{API}/documents/{exp_id}/relationships", headers=WRITER,
                            params={"include": "peers"})
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["data"]["target_ref"] == mol_id  # the id is the edge's own data
    assert item["peer"] is None
    assert item["peer_error_code"] == "not_found"
    assert "xns-closed" not in item["peer_error"]

    readable = await client.get(f"{API}/documents/{exp_id}/relationships", headers=READER,
                                params={"include": "peers"})
    assert readable.json()["items"][0]["peer"]["document_id"] == mol_id


@pytest.mark.asyncio
async def test_traversal_stops_at_a_namespace_the_caller_cannot_read(client, endpoints):
    exp_id, mol_id = endpoints
    assert (await _edge(client, READER, exp_id, mol_id))["status"] == "created"

    as_reader = await client.get(f"{API}/documents/{exp_id}/traverse", headers=READER)
    assert as_reader.status_code == 200, as_reader.text
    assert [(n["document_id"], n["namespace"]) for n in as_reader.json()["nodes"]] == [
        (mol_id, "xns-closed"),
    ]

    as_writer = await client.get(f"{API}/documents/{exp_id}/traverse", headers=WRITER)
    assert as_writer.status_code == 200, as_writer.text
    assert as_writer.json()["nodes"] == []


@pytest.mark.asyncio
async def test_an_unreadable_namespace_cannot_be_listed_explicitly(client, endpoints):
    exp_id, _ = endpoints
    resp = await client.get(f"{API}/documents/{exp_id}/relationships", headers=WRITER,
                            params={"namespace": "xns-closed"})
    assert resp.status_code == 404
