"""Peer projection and graph traversal, pinned before their refactor.

Provenance: CASE-1161. These branches of DocumentService._decorate_with_peers
and traverse_relationships had no test: no items or no peer refs, a caller
with no identity, the per-namespace permission cache, a peer template that
cannot be read or declares neither header nor identity fields, projection
paths that are dotted, missing or name absent metadata.custom keys; and for
the traversal its argument and seed refusals, direction "both", endpoints
already seen, the node cap, and an edge pointing at a missing document.
Documents are written to the test MongoDB directly; the template store and
the permission resolver are stand-ins.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentMetadata, DocumentStatus
from document_store.services.document_service import DocumentService

NS, OTHER = "rel1161", "rel1161-other"
EDGE = "EDGE-1161"


async def _doc(doc_id: str, *, ns: str = NS, template: str = "T-PEER", data=None,
               custom=None, version: int = 1) -> Document:
    doc = Document(
        namespace=ns, document_id=doc_id, template_id=template, template_version=1,
        identity_hash=f"h-{doc_id}", version=version, data=data or {},
        metadata=DocumentMetadata(custom=custom or {}), status=DocumentStatus.ACTIVE,
    )
    await doc.insert()
    return doc


async def _edge(edge_id: str, source: str, target: str, *, ns: str = NS) -> Document:
    return await _doc(edge_id, ns=ns, template=EDGE, data={"source_ref": source, "target_ref": target})


@pytest_asyncio.fixture
async def clean(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": {"$in": [NS, OTHER]}}).delete()
    yield
    await Document.find({"namespace": {"$in": [NS, OTHER]}}).delete()


def _item(data):
    return SimpleNamespace(data=data, peer=None, peer_error_code=None, peer_error=None)


def _templates(by_id: dict, *, failing: tuple = ()):
    async def get_template(template_id=None, **_kw):
        if template_id in failing:
            raise RuntimeError("template store unreachable")
        return by_id.get(template_id)
    client = SimpleNamespace(get_template=get_template)
    return patch("document_store.services.document_service.get_template_store_client",
                 return_value=client)


def _permissions(by_ns: dict):
    calls: list[str] = []

    async def resolve(identity, ns):
        calls.append(ns)
        return by_ns.get(ns, "none")
    return patch("wip_auth.permissions.resolve_permission", side_effect=resolve), calls


# ── _decorate_with_peers ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nothing_to_decorate_leaves_items_alone(clean):
    svc = DocumentService()
    await svc._decorate_with_peers([], "S", identity=object())
    item = _item({"note": "no refs"})
    await svc._decorate_with_peers([item], "S", identity=object())
    assert (item.peer, item.peer_error_code) == (None, None)


@pytest.mark.asyncio
async def test_without_an_identity_every_peer_is_not_found(clean):
    await _doc("P1")
    item = _item({"source_ref": "S", "target_ref": "P1"})
    with _templates({}):
        await DocumentService()._decorate_with_peers([item], "S", identity=None)
    assert item.peer is None and item.peer_error_code == "not_found"


@pytest.mark.asyncio
async def test_peers_are_projected_by_their_templates_tier(clean):
    await _doc("P-HEADER", template="T-HEADER", data={"a": 1, "b": 2, "title": "t"},
               custom={"src": "x"})
    await _doc("P-IDENT", template="T-IDENT", data={"k": "K", "title": "t", "doc_status": "d"})
    await _doc("P-BARE", template="T-BARE", data={"title": "t", "doc_status": "d", "other": 1})
    await _doc("P-FAIL", template="T-FAIL", data={"title": "t", "zzz": 1})
    await _doc("P-OTHER", ns=OTHER, data={"title": "hidden"})
    templates = {
        # Tier 1: explicit header_fields — a dotted data path and a missing
        # field are skipped, a missing metadata.custom key too.
        "T-HEADER": {"header_fields": ["a", "missing", "nested.x", "metadata.custom.src",
                                       "metadata.custom.absent"],
                     "identity_fields": ["b"], "fields": []},
        # Tier 2: identity fields plus the declared title / doc_status.
        "T-IDENT": {"identity_fields": ["k"],
                    "fields": [{"name": "k"}, {"name": "title"}, {"name": "doc_status"}]},
        # Tier 3: neither.
        "T-BARE": {"fields": []},
    }
    items = [_item({"source_ref": "S", "target_ref": p})
             for p in ("P-HEADER", "P-IDENT", "P-BARE", "P-FAIL", "P-OTHER", "P-GONE")]
    items.append(_item({"source_ref": "P-HEADER", "target_ref": "S"}))  # the seed as target
    items.append(_item({"source_ref": "S"}))  # no peer field
    patch_perm, calls = _permissions({NS: "read"})
    with _templates(templates, failing=("T-FAIL",)), patch_perm:
        await DocumentService()._decorate_with_peers(items, "S", identity=object())

    header, ident, bare, fail, other, gone, reverse, no_peer = items
    assert header.peer.data == {"a": 1} and header.peer.metadata == {"custom": {"src": "x"}}
    assert ident.peer.data == {"k": "K", "title": "t", "doc_status": "d"}
    assert bare.peer.data == {"title": "t", "doc_status": "d"}
    assert fail.peer.data == {"title": "t"}  # an unreadable template falls back to the default
    assert (other.peer, other.peer_error_code) == (None, "not_found")
    assert (gone.peer, gone.peer_error_code) == (None, "not_found")
    assert reverse.peer.document_id == "P-HEADER"
    assert (no_peer.peer, no_peer.peer_error_code) == (None, None)
    # Each namespace's permission is resolved once.
    assert sorted(calls) == [NS, OTHER]


# ── traverse_relationships ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("kwargs", "message"), [
    ({"direction": "sideways"}, "direction must be"),
    ({"depth": 0}, "depth must be between 1 and 10"),
    ({"depth": 11}, "depth must be between 1 and 10"),
])
async def test_bad_arguments_are_refused(clean, kwargs, message):
    await _doc("A")
    with pytest.raises(ValueError, match=message):
        await DocumentService().traverse_relationships("A", type_ids=[EDGE], **kwargs)


@pytest.mark.asyncio
async def test_a_missing_seed_is_refused(clean):
    with pytest.raises(ValueError, match="Document not found: NOPE"):
        await DocumentService().traverse_relationships("NOPE", type_ids=[EDGE])


async def _graph():
    """A → B → C, A → C, D → A, and B → GONE (a missing document)."""
    for doc_id in ("A", "B", "C", "D"):
        await _doc(doc_id)
    await _edge("E-AB", "A", "B")
    await _edge("E-BC", "B", "C")
    await _edge("E-AC", "A", "C")
    await _edge("E-DA", "D", "A")
    await _edge("E-BG", "B", "GONE")


def _nodes(resp):
    return sorted((n.document_id, n.depth, tuple(n.path)) for n in resp.nodes)


@pytest.mark.asyncio
async def test_outgoing_walks_each_node_once_and_records_a_missing_one(clean):
    await _graph()
    resp = await DocumentService().traverse_relationships("A", depth=3, type_ids=[EDGE])
    assert _nodes(resp) == [
        ("B", 1, ("B",)), ("C", 1, ("C",)), ("GONE", 2, ("B", "GONE")),
    ]
    gone = next(n for n in resp.nodes if n.document_id == "GONE")
    assert (gone.template_id, gone.namespace, gone.via_relationship) == ("", NS, "E-BG")
    assert resp.truncated is False


@pytest.mark.asyncio
async def test_both_directions_reach_incoming_and_outgoing(clean):
    await _graph()
    resp = await DocumentService().traverse_relationships("A", direction="both", type_ids=[EDGE])
    assert sorted(n.document_id for n in resp.nodes) == ["B", "C", "D"]


@pytest.mark.asyncio
async def test_the_node_cap_truncates_the_walk(clean):
    await _graph()
    resp = await DocumentService().traverse_relationships(
        "A", direction="both", depth=3, type_ids=[EDGE], max_nodes=2,
    )
    assert len(resp.nodes) == 2 and resp.truncated is True
