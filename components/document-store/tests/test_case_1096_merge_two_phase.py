"""A multi-namespace merge checks every namespace before it writes any.

Provenance: CASE-1096 part 1. run_merge planned, checked and wrote one
namespace at a time, so:

- a refusal in a later namespace came after earlier ones were merged into
  live data;
- a reference from one archived namespace into another resolved in the real
  run (the earlier namespace was already written) but not in the dry run
  (nothing written), so the dry run refused what the real run accepted.

Now every namespace is planned, then every namespace is checked against
everything the whole merge supplies, and only then is anything written.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, NamespaceEntry

from document_store.services.backup_engine import (
    _DB_REGISTRY,
    CLAIMS_COLLECTION,
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)

NS_A = "merge-2p-a"
NS_B = "merge-2p-b"


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in [*COLLECTION_MAP.values(), CLAIMS_COLLECTION, (_DB_REGISTRY, "namespaces")]:
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: {"$in": [NS_A, NS_B]}})

    await _clear()
    for ns in (NS_A, NS_B):
        await client[_DB_REGISTRY]["namespaces"].insert_one({"prefix": ns, "isolation_mode": "open"})
    yield client
    await _clear()
    client.close()


def _template(ns: str, tid: str) -> dict:
    return {
        "template_id": tid, "namespace": ns, "value": "NOTE", "label": "Note",
        "version": 1, "fields": [{"name": "n", "type": "integer"}],
    }


def _doc(ns: str, doc_id: str, tid: str, *, refers_to: str | None = None) -> dict:
    row = {
        "document_id": doc_id, "namespace": ns, "template_id": tid,
        "template_version": 1, "identity_hash": f"h-{doc_id}", "version": 1,
        "data": {"n": 1}, "status": "active",
    }
    if refers_to:
        row["references"] = [{"field_path": "other", "resolved": {"document_id": refers_to}}]
    return row


def _archive(per_namespace: dict[str, dict[str, list[dict]]]):
    """A stand-in reader for a two-namespace v3 archive."""
    manifest = Manifest(
        format_version="3.0",
        namespaces=[
            NamespaceEntry(
                prefix=ns,
                namespace_config=NamespaceConfig(namespace_id="019a0000-0000-7000-8000-0000000c1084", prefix=ns, isolation_mode="open"),
                counts=EntityCounts(),
            )
            for ns in per_namespace
        ],
        counts=EntityCounts(),
    )
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=manifest)
    reader.list_namespaces = MagicMock(return_value=list(per_namespace))
    reader.list_blobs = MagicMock(return_value=[])
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [
            dict(e) for e in per_namespace.get(namespace, {}).get(et, [])
        ]
    )
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=None)
    return reader


async def _merge(mongo, reader, **kwargs) -> None:
    engine = DirectRestoreEngine(mongo, None, lambda event: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=reader):
        await engine.run_merge(MagicMock(), "", **kwargs)


async def _docs(mongo, ns: str) -> list[str]:
    db_name, coll_name = COLLECTION_MAP["documents"]
    rows = await mongo[db_name][coll_name].find({"namespace": ns}, {"document_id": 1}).to_list(None)
    return sorted(r["document_id"] for r in rows)


def _cross_reference_archive() -> MagicMock:
    """Namespace B's document references namespace A's, which only arrives
    with this merge."""
    return _archive({
        NS_A: {"templates": [_template(NS_A, "TA")], "documents": [_doc(NS_A, "DA", "TA")]},
        NS_B: {
            "templates": [_template(NS_B, "TB")],
            "documents": [_doc(NS_B, "DB", "TB", refers_to="DA")],
        },
    })


async def _seed_templates(mongo) -> None:
    db_name, coll_name = COLLECTION_MAP["templates"]
    await mongo[db_name][coll_name].insert_many([_template(NS_A, "TA"), _template(NS_B, "TB")])


class TestReferencesBetweenNamespaces:
    @pytest.mark.asyncio
    async def test_the_dry_run_accepts_what_the_real_run_accepts(self, mongo):
        await _seed_templates(mongo)
        await _merge(mongo, _cross_reference_archive(), dry_run=True)
        assert await _docs(mongo, NS_A) == [] and await _docs(mongo, NS_B) == []

        await _merge(mongo, _cross_reference_archive())
        assert await _docs(mongo, NS_A) == ["DA"]
        assert await _docs(mongo, NS_B) == ["DB"]

    @pytest.mark.asyncio
    async def test_a_reference_into_a_later_namespace_resolves_too(self, mongo):
        """Before, namespace order decided it: an earlier namespace pointing
        at a later one was refused even by the real run."""
        await _seed_templates(mongo)
        reader = _archive({
            NS_A: {
                "templates": [_template(NS_A, "TA")],
                "documents": [_doc(NS_A, "DA", "TA", refers_to="DB")],
            },
            NS_B: {"templates": [_template(NS_B, "TB")], "documents": [_doc(NS_B, "DB", "TB")]},
        })
        await _merge(mongo, reader)
        assert await _docs(mongo, NS_A) == ["DA"] and await _docs(mongo, NS_B) == ["DB"]


class TestARefusalNeverFollowsAWrite:
    @pytest.mark.asyncio
    async def test_a_later_namespace_refusing_leaves_the_earlier_one_untouched(self, mongo):
        await _seed_templates(mongo)
        reader = _archive({
            NS_A: {"templates": [_template(NS_A, "TA")], "documents": [_doc(NS_A, "DA", "TA")]},
            NS_B: {
                "templates": [_template(NS_B, "TB")],
                "documents": [_doc(NS_B, "DB", "TB", refers_to="NOWHERE")],
            },
        })
        with pytest.raises(RestoreEngineError, match=NS_B):
            await _merge(mongo, reader)
        assert await _docs(mongo, NS_A) == []
        assert await _docs(mongo, NS_B) == []
