"""Edges are edge types' documents, and the node cap counts nodes.

Provenance: CASE-1275 (cba0fb34 R1, R2, R3).

- A template-store outage during the edge-type narrowing was an unhandled 500 on
  /relationships and /traverse; it is dependency_unavailable now.
- The node cap was charged against endpoints that were then dropped for being in
  a namespace the caller cannot read, so the walk stopped below max_nodes by
  exactly that many.
- A caller's type filter was used as given, so a template that carries no edges
  could still have its documents returned as relationships; the filter is cut
  against the edge types among the documents the query matches.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentMetadata, DocumentStatus
from document_store.services.document_service import DocumentService
from document_store.services.template_store_client import TemplateStoreError

NS, OTHER = "rel1275", "rel1275-other"
EDGE, ENTITY = "EDGE-1275", "ENTITY-1275"


async def _doc(doc_id: str, *, ns: str = NS, template: str = "T-PEER", data=None) -> Document:
    doc = Document(
        namespace=ns, document_id=doc_id, template_id=template, template_version=1,
        identity_hash=f"h-{doc_id}", version=1, data=data or {},
        metadata=DocumentMetadata(custom={}), status=DocumentStatus.ACTIVE,
    )
    await doc.insert()
    return doc


async def _edge(edge_id: str, source: str, target: str, *, ns: str = NS,
                template: str = EDGE) -> Document:
    return await _doc(edge_id, ns=ns, template=template,
                     data={"source_ref": source, "target_ref": target})


@pytest_asyncio.fixture
async def clean(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": {"$in": [NS, OTHER]}}).delete()
    yield
    await Document.find({"namespace": {"$in": [NS, OTHER]}}).delete()


def _store(by_id: dict, *, unavailable: tuple = ()):
    async def get_template_resolved(template_id, version=None):
        if template_id in unavailable:
            raise TemplateStoreError("store unreachable", "unavailable", unavailable=True)
        return by_id.get(template_id)

    client = SimpleNamespace(get_template_resolved=get_template_resolved)
    return patch(
        "document_store.services.document_service.get_template_store_client",
        return_value=client,
    )


EDGE_TYPES = {EDGE: {"usage": "relationship", "value": "MEMBER_OF"},
              ENTITY: {"usage": "entity", "value": "NOTE"}}


async def _graph_with_hidden_first():
    """Three edges out of A, the first of them leading to a document in a
    namespace the walk may not read: X, then B, then C."""
    for doc_id in ("A", "B", "C"):
        await _doc(doc_id)
    await _doc("X", ns=OTHER)
    await _edge("E-AX", "A", "X")
    await _edge("E-AB", "A", "B")
    await _edge("E-AC", "A", "C")


@pytest.mark.asyncio
async def test_the_node_cap_counts_nodes_not_endpoints_dropped_by_the_filter(clean):
    await _graph_with_hidden_first()

    with _store(EDGE_TYPES):
        resp = await DocumentService().traverse_relationships(
            "A", depth=1, type_ids=[EDGE], max_nodes=2, allowed_namespaces=[NS],
        )

    assert [n.document_id for n in resp.nodes] == ["B", "C"]
    assert resp.total_nodes == 2
    # Nothing reachable at this depth was left out: the endpoint that could not
    # be read no longer spends a slot.
    assert resp.truncated is False

    # With a cap the graph cannot satisfy, the truncation is still reported.
    with _store(EDGE_TYPES):
        tight = await DocumentService().traverse_relationships(
            "A", depth=1, type_ids=[EDGE], max_nodes=1, allowed_namespaces=[NS],
        )
    assert [n.document_id for n in tight.nodes] == ["B"]
    assert tight.truncated is True


@pytest.mark.asyncio
async def test_a_type_filter_naming_an_entity_template_finds_no_edges(clean):
    await _doc("A")
    await _doc("B")
    # A document of an entity template that carries edge-shaped references.
    await _edge("E-NOTE", "A", "B", template=ENTITY)

    with _store(EDGE_TYPES):
        by_entity = await DocumentService().traverse_relationships("A", type_ids=[ENTITY])
        by_edge = await DocumentService().traverse_relationships("A", type_ids=[EDGE])
        unfiltered = await DocumentService().traverse_relationships("A")

    assert by_entity.nodes == []
    assert by_edge.nodes == []
    # The NOTE document is not an edge, so it is not walked even unfiltered.
    assert unfiltered.nodes == []


@pytest.mark.asyncio
async def test_a_template_store_outage_is_an_outage_not_an_empty_answer(clean):
    await _doc("A")
    await _doc("B")
    await _edge("E-AB", "A", "B")

    with _store({}, unavailable=(EDGE,)), pytest.raises(Exception) as raised:
        await DocumentService().traverse_relationships("A")
    assert type(raised.value).__name__ == "DependencyUnavailableError"
    assert "template-store" in str(raised.value)

    with _store({}, unavailable=(EDGE,)), pytest.raises(Exception) as listed:
        await DocumentService().find_relationships("A")
    assert type(listed.value).__name__ == "DependencyUnavailableError"


@pytest.mark.asyncio
async def test_edges_of_a_confirmed_edge_type_are_still_walked(clean):
    await _doc("A")
    await _doc("B")
    await _edge("E-AB", "A", "B")

    with _store(EDGE_TYPES):
        resp = await DocumentService().traverse_relationships("A")

    assert [(n.document_id, n.depth, n.via_relationship) for n in resp.nodes] == [("B", 1, "E-AB")]
