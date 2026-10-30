"""A restore says which documents point at entities it could not find here,
and can leave out the edges among them.

An archive's references to entities it does not carry are resolved on the
target install through its referent table. One that is not found keeps its
archived id: a plain reference field keeps its value, as archived. The job
result and the restore provenance list, per unresolved referent, how many
documents reference it, with samples. With ``drop_unresolved_edges`` an
edge document whose source_ref or target_ref points at a missing document
is not restored, with all its versions and its registry entry, and every
dropped edge is listed in full.

Against a real Registry, mounted in process (see test_case_981).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, ProgressEvent

from document_store.services.backup_engine import (
    CLAIMS_COLLECTION,
    COLLECTION_MAP,
    PROVENANCE_COLLECTION,
    DirectRestoreEngine,
)
from registry.main import app as registry_app
from registry.models.entry import RegistryEntry
from registry.models.namespace import Namespace
from wip_auth.composite_key import compute_composite_key_hash

from .conftest import setup_registry_and_app

SOURCE = "unres-991-ns"
FRESH = "unres-991-fresh"
OTHER = "unres-991-other"
NAMESPACES = [SOURCE, FRESH, OTHER]

MISSING = "DOC-MISSING"  # referenced, carried by no archive and no install


@pytest_asyncio.fixture
async def live_registry():
    mongo = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await setup_registry_and_app(mongo)

    async def clear():
        for db_name, coll_name in (*COLLECTION_MAP.values(), CLAIMS_COLLECTION,
                                   PROVENANCE_COLLECTION):
            await mongo[db_name][coll_name].delete_many({"namespace": {"$in": NAMESPACES}})
        await RegistryEntry.find({"namespace": {"$in": NAMESPACES}}).delete()
        await Namespace.find({"prefix": {"$in": NAMESPACES}}).delete()

    await clear()
    yield mongo
    await clear()


def _key(entry_id: str, entity_type: str) -> dict:
    return {"ns": SOURCE, "type": entity_type, "value": entry_id}


def _registry(entry_id: str, entity_type: str) -> dict:
    key = _key(entry_id, entity_type)
    return {"entry_id": entry_id, "namespace": SOURCE, "entity_type": entity_type,
            "primary_composite_key": key,
            "primary_composite_key_hash": compute_composite_key_hash(key),
            "synonyms": [], "status": "active"}


def _doc_ref(field_path: str, document_id: str, namespace: str) -> dict:
    return {"field_path": field_path, "reference_type": "document",
            "lookup_value": document_id, "version_strategy": "latest",
            "resolved": {"document_id": document_id, "namespace": namespace,
                         "template_id": "TPL-NODE", "identity_hash": f"h-{document_id}",
                         "version": 1}}


def _document(document_id, template_id, version, data, references, identity_hash):
    return {"document_id": document_id, "namespace": SOURCE, "template_id": template_id,
            "template_version": 1, "version": version,
            "status": "active", "identity_hash": identity_hash,
            "data": data, "references": references, "term_references": [],
            "file_references": []}


def _entities() -> dict:
    """A node, an edge from it to a document this install does not hold (two
    versions), and a plain holder pointing at the same missing document."""
    edge_refs = [_doc_ref("source_ref", "DOC-NODE", SOURCE),
                 _doc_ref("target_ref", MISSING, OTHER)]
    return {
        "templates": [
            {"template_id": "TPL-NODE", "namespace": SOURCE, "value": "NODE",
             "version": 1, "status": "active", "identity_fields": ["name"],
             "fields": [{"name": "name", "type": "string", "mandatory": True}]},
            {"template_id": "TPL-LINK", "namespace": SOURCE, "value": "LINK",
             "version": 1, "status": "active", "usage": "relationship",
             "identity_fields": ["source_ref", "target_ref"],
             "source_templates": ["NODE"], "target_templates": ["NODE"],
             "fields": [
                 {"name": "source_ref", "type": "reference", "reference_type": "document",
                  "target_templates": ["NODE"], "mandatory": True},
                 {"name": "target_ref", "type": "reference", "reference_type": "document",
                  "target_templates": ["NODE"], "mandatory": True},
                 {"name": "weight", "type": "number"}]},
            {"template_id": "TPL-HOLDER", "namespace": SOURCE, "value": "HOLDER",
             "version": 1, "status": "active", "identity_fields": ["name"],
             "fields": [{"name": "name", "type": "string", "mandatory": True},
                        {"name": "ref", "type": "reference", "reference_type": "document",
                         "target_templates": ["NODE"]}]},
        ],
        "documents": [
            _document("DOC-EDGE", "TPL-LINK", 1,
                      {"source_ref": "DOC-NODE", "target_ref": MISSING, "weight": 1},
                      edge_refs, "h-edge"),
            _document("DOC-EDGE", "TPL-LINK", 2,
                      {"source_ref": "DOC-NODE", "target_ref": MISSING, "weight": 2},
                      edge_refs, "h-edge"),
            _document("DOC-HOLDER", "TPL-HOLDER", 1, {"name": "h", "ref": MISSING},
                      [_doc_ref("ref", MISSING, OTHER)], "h-holder"),
            _document("DOC-NODE", "TPL-NODE", 1, {"name": "n"}, [], "h-node"),
        ],
        "registry_entries": [
            _registry("TPL-NODE", "templates"), _registry("TPL-LINK", "templates"),
            _registry("TPL-HOLDER", "templates"), _registry("DOC-EDGE", "documents"),
            _registry("DOC-HOLDER", "documents"), _registry("DOC-NODE", "documents"),
        ],
    }


REFERENTS = [{
    "entry_id": MISSING, "namespace": OTHER, "entity_type": "documents",
    "primary_composite_key": {"ns": OTHER, "template_id": "TPL-NODE", "identity_hash": "h-x"},
    "synonyms": [],
}]


def _archive(entities):
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=Manifest(
        format_version="3.0", namespace=SOURCE,
        namespace_config=NamespaceConfig(prefix=SOURCE, isolation_mode="open"),
        counts=EntityCounts(), referents=len(REFERENTS),
    ))
    reader.read_manifest_raw = MagicMock(return_value={})
    reader.read_referents = MagicMock(side_effect=lambda: [dict(r) for r in REFERENTS])
    reader.list_namespaces = MagicMock(return_value=[SOURCE])
    reader.list_blobs = MagicMock(return_value=[])
    reader.entity_count = MagicMock(
        side_effect=lambda et, namespace=None: len(entities.get(et, []))
    )
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [dict(e) for e in entities.get(et, [])]
    )
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=None)
    return reader


async def _run(mongo, method: str, *, entities=None, **kwargs):
    transport = ASGITransport(app=registry_app)
    real_client = httpx.AsyncClient

    def _client(*_args, **kw):
        kw.pop("timeout", None)
        kw.pop("transport", None)
        kw.setdefault("base_url", "http://registry")
        return real_client(transport=transport, **kw)

    events: list[ProgressEvent] = []
    engine = DirectRestoreEngine(
        mongo, None, events.append, registry_base_url="http://registry",
        registry_api_key=os.environ["MASTER_API_KEY"],
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader",
        return_value=_archive(entities or _entities()),
    ), patch("httpx.AsyncClient", _client):
        await getattr(engine, method)(MagicMock(), **kwargs)
    return engine, [e.message for e in events if e.phase == "warning"]


async def _documents(mongo, namespace):
    db_name, coll_name = COLLECTION_MAP["documents"]
    return await mongo[db_name][coll_name].find(
        {"namespace": namespace}, {"_id": 0}
    ).to_list(length=None)


async def _provenance(mongo, namespace):
    db_name, coll_name = PROVENANCE_COLLECTION
    rows = await mongo[db_name][coll_name].find(
        {"namespace": namespace, "inherited": False}, {"_id": 0}
    ).to_list(length=None)
    assert len(rows) == 1, rows
    return rows[0]


def _the_reference(referents, documents: int) -> dict:
    (entry,) = referents["unresolved_references"]
    assert (entry["referent"], entry["entity_type"], entry["namespace"]) == (
        MISSING, "documents", SOURCE)
    assert entry["documents"] == documents
    return entry


@pytest.mark.asyncio
async def test_a_restore_lists_the_documents_that_keep_an_archived_id(live_registry):
    engine, warnings = await _run(live_registry, "run_restore", target_namespace=SOURCE)

    referents = engine.result["referents"]
    entry = _the_reference(referents, documents=2)
    assert {(s["document_id"], s["field_path"]) for s in entry["samples"]} == {
        ("DOC-EDGE", "target_ref"), ("DOC-HOLDER", "ref")}
    assert "dropped_edges" not in referents
    (warning,) = [w for w in warnings if MISSING in w]
    assert "2 archived document(s) reference it" in warning

    # Kept as archived: the edge, both versions, and the holder's value.
    rows = await _documents(live_registry, SOURCE)
    assert sorted((r["document_id"], r["version"]) for r in rows) == [
        ("DOC-EDGE", 1), ("DOC-EDGE", 2), ("DOC-HOLDER", 1), ("DOC-NODE", 1)]
    holder = next(r for r in rows if r["document_id"] == "DOC-HOLDER")
    assert holder["references"][0]["resolved"]["document_id"] == MISSING

    record = await _provenance(live_registry, SOURCE)
    assert record["referents"]["unresolved"] == 1
    assert record["referents"]["unresolved_references"][0]["documents"] == 2


@pytest.mark.asyncio
async def test_a_restore_drops_edges_to_a_missing_document_when_asked(live_registry):
    engine, warnings = await _run(
        live_registry, "run_restore", target_namespace=SOURCE, drop_unresolved_edges=True,
    )

    referents = engine.result["referents"]
    (edge,) = referents["dropped_edges"]
    assert edge == {"namespace": SOURCE, "document_id": "DOC-EDGE", "template_id": "TPL-LINK",
                    "source_ref": "DOC-NODE", "target_ref": MISSING,
                    "unresolved_endpoints": [MISSING]}
    # The holder keeps its value and is still listed; the edge is the drop.
    entry = _the_reference(referents, documents=1)
    assert entry["samples"] == [{"document_id": "DOC-HOLDER", "field_path": "ref"}]
    assert any("1 edge document(s) not restored" in w for w in warnings)

    rows = await _documents(live_registry, SOURCE)
    assert sorted(r["document_id"] for r in rows) == ["DOC-HOLDER", "DOC-NODE"]
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    assert await live_registry[db_name][coll_name].find_one({"entry_id": "DOC-EDGE"}) is None
    assert await live_registry[db_name][coll_name].find_one({"entry_id": "DOC-HOLDER"})

    record = await _provenance(live_registry, SOURCE)
    assert record["referents"]["dropped_edges"] == [edge]


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_same_and_writes_nothing(live_registry):
    engine, _ = await _run(
        live_registry, "run_restore", target_namespace=SOURCE, dry_run=True,
        drop_unresolved_edges=True,
    )

    referents = engine.result["referents"]
    assert [e["document_id"] for e in referents["dropped_edges"]] == ["DOC-EDGE"]
    _the_reference(referents, documents=1)
    assert await _documents(live_registry, SOURCE) == []


@pytest.mark.asyncio
async def test_a_fresh_restore_drops_the_edge_and_keeps_the_holder(live_registry):
    engine, _ = await _run(
        live_registry, "run_remap", target_namespace=FRESH, drop_unresolved_edges=True,
    )

    referents = engine.result["referents"]
    assert [e["document_id"] for e in referents["dropped_edges"]] == ["DOC-EDGE"]
    assert engine.result["planned"][SOURCE]["documents"] == 2

    rows = await _documents(live_registry, FRESH)
    assert len(rows) == 2
    assert {r["template_id"] for r in rows}.isdisjoint({"TPL-LINK"})
    holder = next(r for r in rows if r["data"].get("ref"))
    # Re-minted ids everywhere, but the missing referent keeps its archived id.
    assert holder["document_id"] != "DOC-HOLDER"
    assert holder["references"][0]["resolved"]["document_id"] == MISSING

    record = await _provenance(live_registry, FRESH)
    assert [e["document_id"] for e in record["referents"]["dropped_edges"]] == ["DOC-EDGE"]


def _without_holder() -> dict:
    """A merge refuses any reference that resolves to nothing; the holder's
    plain reference would refuse it whatever happens to the edge."""
    entities = _entities()
    entities["documents"] = [d for d in entities["documents"] if d["document_id"] != "DOC-HOLDER"]
    entities["registry_entries"] = [
        r for r in entities["registry_entries"] if r["entry_id"] != "DOC-HOLDER"]
    return entities


@pytest_asyncio.fixture
async def merge_target(live_registry):
    """An existing namespace for the merge to land in."""
    from document_store.services.backup_engine import _DB_REGISTRY

    await live_registry[_DB_REGISTRY]["namespaces"].insert_one({
        "prefix": SOURCE, "description": "", "isolation_mode": "open",
        "deletion_mode": "retain", "status": "active", "allowed_external_refs": [],
    })
    yield live_registry
    await live_registry[_DB_REGISTRY]["namespaces"].delete_many({"prefix": SOURCE})


@pytest.mark.asyncio
async def test_a_merge_refuses_an_edge_to_a_missing_document(merge_target):
    from document_store.services.backup_engine import RestoreEngineError

    with pytest.raises(RestoreEngineError, match="resolve to nothing"):
        await _run(merge_target, "run_merge", target_namespace=SOURCE,
                   entities=_without_holder(), add_missing=True)
    assert await _documents(merge_target, SOURCE) == []


@pytest.mark.asyncio
async def test_a_merge_leaves_the_edge_out_when_asked(merge_target):
    engine, _ = await _run(merge_target, "run_merge", target_namespace=SOURCE,
                           entities=_without_holder(), add_missing=True,
                           drop_unresolved_edges=True)

    referents = engine.result["namespaces"][SOURCE]["referents"]
    assert [e["document_id"] for e in referents["dropped_edges"]] == ["DOC-EDGE"]
    assert referents["unresolved_references"] == []
    rows = await _documents(merge_target, SOURCE)
    assert [r["document_id"] for r in rows] == ["DOC-NODE"]
    record = await _provenance(merge_target, SOURCE)
    assert [e["document_id"] for e in record["referents"]["dropped_edges"]] == ["DOC-EDGE"]
