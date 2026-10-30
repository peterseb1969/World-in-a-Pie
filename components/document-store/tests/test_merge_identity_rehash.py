"""A merge rehashes documents whose rewritten references change their identity.

A term- or reference-typed identity field hashes the canonical id its stored
resolution names. A merge that points such a resolution at this install's
entity (a referent outside the archive, or a definition matched by content)
changes the document's identity. The merged row, its Registry key and its
uniqueness claim must carry the recomputed hash, or the next write of that
identity creates a second document. A merge that maps nothing writes the
rows as archived.

Both document paths are covered: the streamed one (an archive sorted by
document_id, planned from compact heads) and the in-memory one (an unsorted
archive, whose chains are held).

Against a real Registry, mounted in process (see test_case_981).
"""

from __future__ import annotations

import copy
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from document_store.services.backup_engine import (
    _DB_REGISTRY,
    CLAIMS_COLLECTION,
    COLLECTION_MAP,
    DirectRestoreEngine,
)
from registry.main import app as registry_app
from wip_auth.composite_key import compute_composite_key_hash

from .test_case_981_plain_restore_referents import (  # noqa: F401  (live_registry is a fixture)
    KG_KEY,
    OTHER,
    REFERENTS,
    SOURCE,
    TL_KEY,
    _archive,
    _entities,
    _entry,
    _hash_for,
    _live_entry,
    live_registry,
)


async def _merge(mongo, entities):
    await mongo[_DB_REGISTRY]["namespaces"].insert_one({
        "prefix": SOURCE, "description": "", "isolation_mode": "open",
        "deletion_mode": "retain", "status": "active", "allowed_external_refs": [],
    })
    transport = ASGITransport(app=registry_app)
    real_client = httpx.AsyncClient

    def _client(*_args, **kw):
        kw.pop("timeout", None)
        kw.pop("transport", None)
        kw.setdefault("base_url", "http://registry")
        return real_client(transport=transport, **kw)

    events: list = []
    engine = DirectRestoreEngine(
        mongo, None, events.append, registry_base_url="http://registry",
        registry_api_key=os.environ["MASTER_API_KEY"],
    )
    try:
        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=_archive(entities, REFERENTS),
        ), patch("httpx.AsyncClient", _client):
            await engine.run_merge(MagicMock(), SOURCE, add_missing=True)
    finally:
        await mongo[_DB_REGISTRY]["namespaces"].delete_many({"prefix": SOURCE})
    engine.warnings = [e.message for e in events if e.phase == "warning"]
    return engine


async def _term_here(mongo, term_id: str, terminology_id: str) -> None:
    """The def-store row a merge's reference check looks for."""
    db_name, coll_name = COLLECTION_MAP["terms"]
    await mongo[db_name][coll_name].insert_one({
        "term_id": term_id, "terminology_id": terminology_id, "namespace": OTHER,
        "value": "kg", "status": "active",
    })


async def _repointed(mongo) -> None:
    """The archive's term outside it is a different entity here."""
    await _live_entry("LIVE-TL", "terminologies", TL_KEY)
    await _live_entry("LIVE-KG", "terms", KG_KEY)
    await _term_here(mongo, "LIVE-KG", "LIVE-TL")


def _unsorted(entities: dict) -> dict:
    """A second document listed before DOC-1: the archive is not sorted by
    document_id, so the merge holds the chains instead of streaming them."""
    entities = copy.deepcopy(entities)
    doc, entry = copy.deepcopy(entities["documents"][0]), copy.deepcopy(entities["registry_entries"][1])
    doc["document_id"] = entry["entry_id"] = "DOC-2"
    doc["data"] = {"unit": "kg2"}
    doc["identity_hash"] = "h-doc-2"
    entry["primary_composite_key"] = {**entry["primary_composite_key"], "identity_hash": "h-doc-2"}
    entry["primary_composite_key_hash"] = compute_composite_key_hash(entry["primary_composite_key"])
    entities["documents"] = [doc, *entities["documents"]]
    entities["registry_entries"] = [*entities["registry_entries"], entry]
    return entities


async def _doc(mongo, document_id):
    db_name, coll_name = COLLECTION_MAP["documents"]
    return await mongo[db_name][coll_name].find_one(
        {"namespace": SOURCE, "document_id": document_id}, {"_id": 0}
    )


async def _claim(mongo, document_id):
    db_name, coll_name = CLAIMS_COLLECTION
    return await mongo[db_name][coll_name].find_one({
        "namespace": SOURCE, "entity_type": "documents", "kind": "primary",
        "owner_entry_id": document_id,
    })


async def _assert_rekeyed(mongo, new_hash):
    document = await _doc(mongo, "DOC-1")
    assert document["term_references"][0]["term_id"] == "LIVE-KG"
    assert document["identity_hash"] == new_hash
    entry = await _entry(mongo, "DOC-1")
    assert entry["primary_composite_key"]["identity_hash"] == new_hash
    assert entry["primary_composite_key_hash"] == compute_composite_key_hash(
        entry["primary_composite_key"]
    )
    claim = await _claim(mongo, "DOC-1")
    assert claim["composite_key_hash"] == compute_composite_key_hash(
        {"ns": SOURCE, "template_id": "TPL-1", "identity_hash": new_hash}
    )


@pytest.mark.asyncio
async def test_a_streamed_merge_rehashes_a_repointed_identity(live_registry):  # noqa: F811
    await _repointed(live_registry)
    new_hash = _hash_for("LIVE-KG")
    assert new_hash != _hash_for("SRC-KG")

    engine = await _merge(live_registry, _entities())

    assert not any("holds every document in memory" in w for w in engine.warnings)
    await _assert_rekeyed(live_registry, new_hash)
    assert engine.result["namespaces"][SOURCE]["rehashed_documents"] == 1


@pytest.mark.asyncio
async def test_an_in_memory_merge_rehashes_a_repointed_identity(live_registry):  # noqa: F811
    await _repointed(live_registry)

    engine = await _merge(live_registry, _unsorted(_entities()))

    assert any("holds every document in memory" in w for w in engine.warnings)
    await _assert_rekeyed(live_registry, _hash_for("LIVE-KG"))
    # DOC-2 resolves the same term (SRC-KG, repointed to LIVE-KG), so it is
    # rehashed as well.
    assert engine.result["namespaces"][SOURCE]["rehashed_documents"] == 2


@pytest.mark.asyncio
async def test_a_merge_that_maps_nothing_writes_the_rows_as_archived(live_registry):  # noqa: F811
    """On the install the archive came from, every referent resolves to
    itself: the identity feed does not change and nothing is rehashed."""
    await _live_entry("SRC-TL", "terminologies", TL_KEY)
    await _live_entry("SRC-KG", "terms", KG_KEY)
    await _term_here(live_registry, "SRC-KG", "SRC-TL")
    entities = _entities()

    engine = await _merge(live_registry, entities)

    document = await _doc(live_registry, "DOC-1")
    assert document["identity_hash"] == entities["documents"][0]["identity_hash"]
    entry = await _entry(live_registry, "DOC-1")
    assert entry["primary_composite_key"] == entities["registry_entries"][1]["primary_composite_key"]
    assert engine.result["namespaces"][SOURCE]["rehashed_documents"] == 0
