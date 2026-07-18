"""Tests for the Phase-4 relationship-query APIs.

Two endpoints:
  GET /documents/{id}/relationships  — direct neighbours
  GET /documents/{id}/traverse        — N-hop BFS expansion

Fixtures: EXPERIMENT and MOLECULE entity templates plus an
EXPERIMENT_INPUT relationship template (declared in conftest.py for
Phase 2). Tests build small graphs and assert reachability.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/document-store"


async def _create_doc(client, auth_headers, template_value: str, data: dict, **extra) -> dict:
    resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": template_value, "data": data, **extra}],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _build_chain(client, auth_headers, n: int) -> tuple[list[str], list[str]]:
    """Build a linear chain of n EXPERIMENTs connected by n-1
    EXPERIMENT_INPUT relationships pointing from EXPERIMENT[i] to
    a fresh MOLECULE, with the MOLECULE then used as source via a
    second relationship to the next EXPERIMENT.

    Actually simpler: make a chain where each EXPERIMENT points
    (via EXPERIMENT_INPUT) to a unique MOLECULE; then a separate
    EXPERIMENT_INPUT points the MOLECULE back as source... no, the
    template-level source/target_templates pin it: only EXPERIMENT
    can be source, only MOLECULE can be target.

    So instead: build a *fan* — one root EXPERIMENT linked to N
    distinct MOLECULEs, all in one hop. depth=1 finds N nodes.
    Returns (experiment_ids, molecule_ids).
    """
    exp_ids: list[str] = []
    mol_ids: list[str] = []
    root = await _create_doc(client, auth_headers, "EXPERIMENT", {
        "experiment_id": "ROOT", "name": "Root experiment",
    })
    exp_ids.append(root["document_id"])
    for i in range(n):
        mol = await _create_doc(client, auth_headers, "MOLECULE", {
            "molecule_id": f"MOL-{i:03d}",
        })
        mol_ids.append(mol["document_id"])
        rel = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
            "source_ref": root["document_id"],
            "target_ref": mol["document_id"],
            "role": f"role-{i}",
        })
        assert rel["status"] == "created", rel
    return exp_ids, mol_ids


# =============================================================================
# /relationships
# =============================================================================


@pytest.mark.asyncio
async def test_relationships_outgoing_returns_only_outgoing(
    client: AsyncClient, auth_headers: dict,
):
    exp_ids, _mol_ids = await _build_chain(client, auth_headers, n=3)
    root = exp_ids[0]

    resp = await client.get(
        f"{API}/documents/{root}/relationships?direction=outgoing&namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3, body
    # Every returned doc has source_ref == root
    for item in body["items"]:
        assert item["data"]["source_ref"] == root


@pytest.mark.asyncio
async def test_relationships_incoming_for_target_doc(
    client: AsyncClient, auth_headers: dict,
):
    _exp_ids, mol_ids = await _build_chain(client, auth_headers, n=2)
    target = mol_ids[0]

    resp = await client.get(
        f"{API}/documents/{target}/relationships?direction=incoming&namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1, body
    assert body["items"][0]["data"]["target_ref"] == target


@pytest.mark.asyncio
async def test_relationships_both_returns_both_directions(
    client: AsyncClient, auth_headers: dict,
):
    exp_ids, _mol_ids = await _build_chain(client, auth_headers, n=2)
    root = exp_ids[0]

    resp = await client.get(
        f"{API}/documents/{root}/relationships?direction=both&namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    # root has 2 outgoing, 0 incoming → total 2
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_relationships_404_for_unknown_seed(
    client: AsyncClient, auth_headers: dict,
):
    resp = await client.get(
        f"{API}/documents/0190ffff-ffff-7fff-8fff-ffffffffffff/relationships",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# =============================================================================
# /traverse
# =============================================================================


@pytest.mark.asyncio
async def test_traverse_depth_1_outgoing_finds_direct_neighbours(
    client: AsyncClient, auth_headers: dict,
):
    exp_ids, mol_ids = await _build_chain(client, auth_headers, n=3)
    root = exp_ids[0]

    resp = await client.get(
        f"{API}/documents/{root}/traverse?depth=1&direction=outgoing&namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["seed_document_id"] == root
    assert body["depth"] == 1
    assert body["total_nodes"] == 3, body
    assert {n["document_id"] for n in body["nodes"]} == set(mol_ids)
    # All at depth 1, all reached via a relationship doc, path length = 1.
    for node in body["nodes"]:
        assert node["depth"] == 1
        assert node["via_relationship"] is not None
        assert len(node["path"]) == 1
        assert node["path"][0] == node["document_id"]


@pytest.mark.asyncio
async def test_traverse_depth_2_does_not_revisit(
    client: AsyncClient, auth_headers: dict,
):
    """Outgoing depth=2 from root: hop 1 reaches 3 molecules, hop 2
    reaches nothing (molecules have no outgoing rels). Visited set
    should keep the result the same as depth=1."""
    exp_ids, _mol_ids = await _build_chain(client, auth_headers, n=3)
    root = exp_ids[0]

    resp = await client.get(
        f"{API}/documents/{root}/traverse?depth=2&direction=outgoing&namespace=wip",
        headers=auth_headers,
    )
    body = resp.json()
    assert body["total_nodes"] == 3
    assert all(n["depth"] == 1 for n in body["nodes"])


@pytest.mark.asyncio
async def test_traverse_depth_validation(
    client: AsyncClient, auth_headers: dict,
):
    """depth must be 1..10."""
    exp_ids, _ = await _build_chain(client, auth_headers, n=1)
    resp = await client.get(
        f"{API}/documents/{exp_ids[0]}/traverse?depth=11&namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 422  # FastAPI Query validation


@pytest.mark.asyncio
async def test_traverse_404_for_unknown_seed(
    client: AsyncClient, auth_headers: dict,
):
    resp = await client.get(
        f"{API}/documents/0190ffff-ffff-7fff-8fff-ffffffffffff/traverse?depth=1",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_traverse_types_filter_unknown_template_returns_empty(
    client: AsyncClient, auth_headers: dict,
):
    """An unknown template value in `types` resolves to no template_ids,
    so the traversal returns zero nodes (not 400)."""
    exp_ids, _ = await _build_chain(client, auth_headers, n=2)
    resp = await client.get(
        f"{API}/documents/{exp_ids[0]}/traverse?depth=2&types=NO_SUCH_TEMPLATE&namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total_nodes"] == 0


@pytest.mark.asyncio
async def test_traverse_incoming_from_molecule_finds_root(
    client: AsyncClient, auth_headers: dict,
):
    """From a molecule, traversing direction=incoming at depth=1 should
    reach the EXPERIMENT root via the EXPERIMENT_INPUT edge."""
    exp_ids, mol_ids = await _build_chain(client, auth_headers, n=1)
    root = exp_ids[0]
    mol = mol_ids[0]

    resp = await client.get(
        f"{API}/documents/{mol}/traverse?depth=1&direction=incoming&namespace=wip",
        headers=auth_headers,
    )
    body = resp.json()
    assert body["total_nodes"] == 1, body
    node = body["nodes"][0]
    assert node["document_id"] == root
    assert node["depth"] == 1
