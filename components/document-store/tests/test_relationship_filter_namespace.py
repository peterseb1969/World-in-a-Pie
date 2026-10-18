"""The edge-type filters on /relationships (`template=`) and /traverse
(`types=`) resolve through the Registry in the request's namespace.

A bare value names the edge type in the namespace the request is about (the
`namespace` parameter, else the seed document's namespace), never one that
merely shares the value somewhere else; `NS:VALUE` names another namespace
explicitly. A value that resolves to nothing is a caller error and fails
loudly (404) instead of answering with an empty page that looks like "no
edges".
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app
from tests.conftest import SAMPLE_TEMPLATES
from wip_auth.resolve import clear_resolution_cache

API = "/api/document-store"


async def _create_doc(client: AsyncClient, auth_headers: dict, template: str, data: dict) -> str:
    resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": template, "data": data}],
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] == "created", result
    return result["document_id"]


async def _seed_edge(client: AsyncClient, auth_headers: dict) -> tuple[str, str]:
    """One EXPERIMENT --EXPERIMENT_INPUT--> one MOLECULE, all in 'wip'."""
    exp_id = await _create_doc(client, auth_headers, "EXPERIMENT", {"experiment_id": "EXP-NS"})
    mol_id = await _create_doc(client, auth_headers, "MOLECULE", {"molecule_id": "MOL-NS"})
    await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })
    return exp_id, mol_id


@pytest.fixture
async def same_value_in_other_ns(client: AsyncClient):
    """Declare an EXPERIMENT_INPUT edge type in 'other-ns' too, at a higher
    version than the one in 'wip'.

    Template-store's value lookup without a namespace searches every
    namespace and returns the highest version, so the mock's value-keyed
    entry is pointed at the 'other-ns' template for the duration — the shape
    that made a bare filter value select the wrong namespace's edge type.
    """
    headers = {"X-API-Key": os.environ["MASTER_API_KEY"]}
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.post("/api/registry/entries/register", headers=headers, json=[{
            "namespace": "other-ns",
            "entity_type": "templates",
            "composite_key": {"value": "EXPERIMENT_INPUT", "label": "Experiment Input"},
        }])
        assert resp.status_code == 200, resp.text
        other_id = resp.json()["results"][0]["registry_id"]
        resp = await reg.post("/api/registry/synonyms/add", headers=headers, json=[{
            "target_id": other_id,
            "synonym_namespace": "other-ns",
            "synonym_entity_type": "templates",
            "synonym_composite_key": {"ns": "other-ns", "type": "template", "value": "EXPERIMENT_INPUT"},
        }])
        assert resp.status_code == 200, resp.text

    wip_template = SAMPLE_TEMPLATES["EXPERIMENT_INPUT"]
    other_template = {**wip_template, "template_id": other_id, "namespace": "other-ns", "version": 5}
    SAMPLE_TEMPLATES[other_id] = other_template
    SAMPLE_TEMPLATES["EXPERIMENT_INPUT"] = other_template
    clear_resolution_cache()
    try:
        yield other_id
    finally:
        SAMPLE_TEMPLATES["EXPERIMENT_INPUT"] = wip_template
        SAMPLE_TEMPLATES.pop(other_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "template=EXPERIMENT_INPUT&namespace=wip",
    "template=EXPERIMENT_INPUT",  # namespace defaults to the seed document's
    "template=wip:EXPERIMENT_INPUT",
])
async def test_relationships_bare_value_resolves_in_request_namespace(
    client: AsyncClient, auth_headers: dict, same_value_in_other_ns: str, query: str,
):
    exp_id, _ = await _seed_edge(client, auth_headers)
    resp = await client.get(f"{API}/documents/{exp_id}/relationships?{query}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_relationships_qualified_value_names_the_other_namespace(
    client: AsyncClient, auth_headers: dict, same_value_in_other_ns: str,
):
    """`other-ns:EXPERIMENT_INPUT` resolves — to a different edge type, which
    has no edges on this 'wip' document."""
    exp_id, _ = await _seed_edge(client, auth_headers)
    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships?template=other-ns:EXPERIMENT_INPUT",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "types=EXPERIMENT_INPUT&namespace=wip",
    "types=EXPERIMENT_INPUT",
])
async def test_traverse_bare_value_resolves_in_request_namespace(
    client: AsyncClient, auth_headers: dict, same_value_in_other_ns: str, query: str,
):
    exp_id, mol_id = await _seed_edge(client, auth_headers)
    resp = await client.get(f"{API}/documents/{exp_id}/traverse?depth=1&{query}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_nodes"] == 1
    assert [n["document_id"] for n in body["nodes"]] == [mol_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "relationships?template=NO_SUCH_EDGE_TYPE&namespace=wip",
    "relationships?template=EXPERIMENT_INPUT,NO_SUCH_EDGE_TYPE",
    "traverse?depth=1&types=NO_SUCH_EDGE_TYPE&namespace=wip",
])
async def test_unresolvable_filter_value_is_404(
    client: AsyncClient, auth_headers: dict, path: str,
):
    exp_id, _ = await _seed_edge(client, auth_headers)
    resp = await client.get(f"{API}/documents/{exp_id}/{path}", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    assert "NO_SUCH_EDGE_TYPE" in resp.json()["detail"]
