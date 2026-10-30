"""A plain restore points references outside the archive at this install.

A plain (id-preserving) restore keeps the archive's own ids. References to
entities the archive does not carry (the wip namespace's vocabularies, a
sibling namespace) name the SOURCE install's ids; restored onto another
install they must point at that install's entities, found through the
archive's referent table, as fresh and merge restores already do.

A rewritten term or reference on an identity field changes the document's
identity hash, so the document is rehashed and its Registry row re-keyed:
otherwise the next write of that identity would create a second document.

The Registry is mounted in process for the referent lookups. The rows the
restore writes go straight to the wip_registry collections, which that
in-process Registry does not read, so they are asserted there directly.

Against a real Registry, mounted in process (see test_remap_integration).
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
    DirectRestoreEngine,
)
from document_store.services.remap_restore import rehash_document_row
from registry.main import app as registry_app
from registry.models.entry import RegistryEntry, Synonym
from registry.models.namespace import Namespace
from wip_auth.composite_key import compute_composite_key_hash

from .conftest import setup_registry_and_app

SOURCE = "plain-981-ns"
# The namespace the archive references but does not carry (think "wip").
OTHER = "plain-981-other"

TL_KEY = {"ns": OTHER, "type": "terminologies", "value": "UNITS"}
KG_KEY = {"ns": OTHER, "type": "terms", "terminology": "UNITS", "value": "kg"}

TEMPLATE_FIELDS = [{"name": "unit", "type": "term", "terminology_ref": "SRC-TL"}]


@pytest_asyncio.fixture
async def live_registry():
    mongo = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await setup_registry_and_app(mongo)

    async def clear():
        for db_name, coll_name in COLLECTION_MAP.values():
            await mongo[db_name][coll_name].delete_many(
                {"namespace": {"$in": [SOURCE, OTHER]}}
            )
        await mongo[CLAIMS_COLLECTION[0]][CLAIMS_COLLECTION[1]].delete_many(
            {"namespace": {"$in": [SOURCE, OTHER]}}
        )
        await RegistryEntry.find({"namespace": {"$in": [SOURCE, OTHER]}}).delete()
        await Namespace.find({"prefix": {"$in": [SOURCE, OTHER]}}).delete()

    await clear()
    yield mongo
    await clear()


async def _live_entry(entry_id, entity_type, synonym_key):
    """An entity of this install, findable by its portable synonym only."""
    primary = {"ns": OTHER, "install_specific": entry_id}
    await RegistryEntry(
        entry_id=entry_id, namespace=OTHER, entity_type=entity_type,
        primary_composite_key=primary,
        primary_composite_key_hash=compute_composite_key_hash(primary),
        synonyms=[Synonym(
            namespace=OTHER, entity_type=entity_type, composite_key=synonym_key,
            composite_key_hash=compute_composite_key_hash(synonym_key),
        )],
        status="active",
    ).insert()


def _referent(entry_id, entity_type, synonym_key):
    return {
        "entry_id": entry_id, "namespace": OTHER, "entity_type": entity_type,
        "primary_composite_key": {"ns": OTHER, "install_specific": entry_id},
        "synonyms": [{"namespace": OTHER, "entity_type": entity_type,
                      "composite_key": synonym_key}],
    }


REFERENTS = [
    _referent("SRC-TL", "terminologies", TL_KEY),
    _referent("SRC-KG", "terms", KG_KEY),
]


def _hash_for(term_id: str) -> str:
    row = {"data": {"unit": "kg"},
           "term_references": [{"field_path": "unit", "term_id": term_id}]}
    rehash_document_row(row, ["unit"], TEMPLATE_FIELDS)
    return row["identity_hash"]


def _entities():
    """A namespace whose template draws on a vocabulary it does not carry,
    with a document whose identity field is a term from it."""
    old_hash = _hash_for("SRC-KG")
    doc_key = {"ns": SOURCE, "template_id": "TPL-1", "identity_hash": old_hash}
    tpl_key = {"ns": SOURCE, "value": "MEAL"}
    return {
        "templates": [{
            "template_id": "TPL-1", "namespace": SOURCE, "value": "MEAL",
            "version": 1, "status": "active", "identity_fields": ["unit"],
            "fields": TEMPLATE_FIELDS,
        }],
        "documents": [{
            "document_id": "DOC-1", "namespace": SOURCE, "template_id": "TPL-1",
            "template_value": "MEAL", "template_version": 1, "version": 1,
            "status": "active", "identity_hash": old_hash,
            "data": {"unit": "kg"},
            "term_references": [{"field_path": "unit", "term_id": "SRC-KG",
                                 "terminology_ref": "SRC-TL"}],
        }],
        "registry_entries": [
            {"entry_id": "TPL-1", "namespace": SOURCE, "entity_type": "templates",
             "primary_composite_key": tpl_key,
             "primary_composite_key_hash": compute_composite_key_hash(tpl_key),
             "synonyms": [], "status": "active"},
            {"entry_id": "DOC-1", "namespace": SOURCE, "entity_type": "documents",
             "primary_composite_key": doc_key,
             "primary_composite_key_hash": compute_composite_key_hash(doc_key),
             "synonyms": [], "status": "active"},
        ],
    }


def _archive(entities, referents):
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=Manifest(
        format_version="3.0", namespace=SOURCE,
        namespace_config=NamespaceConfig(prefix=SOURCE, isolation_mode="open"),
        counts=EntityCounts(), referents=len(referents),
    ))
    reader.read_manifest_raw = MagicMock(return_value={})
    reader.read_referents = MagicMock(side_effect=lambda: [dict(r) for r in referents])
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


async def _restore(mongo, entities, referents=REFERENTS, *, dry_run=False):
    transport = ASGITransport(app=registry_app)
    real_client = httpx.AsyncClient

    def _client(*_args, **kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("transport", None)
        return real_client(transport=transport, base_url="http://registry", **kwargs)

    events: list[ProgressEvent] = []
    engine = DirectRestoreEngine(
        mongo, None, events.append, registry_base_url="http://registry",
        registry_api_key=os.environ["MASTER_API_KEY"],
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader",
        return_value=_archive(entities, referents),
    ), patch("httpx.AsyncClient", _client):
        await engine.run_restore(MagicMock(), SOURCE, dry_run=dry_run)
    return engine, events


async def _entry(mongo, entry_id):
    """A Registry row as the restore wrote it."""
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    return await mongo[db_name][coll_name].find_one({"entry_id": entry_id}, {"_id": 0})


async def _one(mongo, entity_type):
    db_name, coll_name = COLLECTION_MAP[entity_type]
    rows = await mongo[db_name][coll_name].find(
        {"namespace": SOURCE}, {"_id": 0}
    ).to_list(length=None)
    assert len(rows) == 1, rows
    return rows[0]


@pytest.mark.asyncio
async def test_references_outside_the_archive_point_at_this_install(live_registry):
    await _live_entry("LIVE-TL", "terminologies", TL_KEY)
    await _live_entry("LIVE-KG", "terms", KG_KEY)

    engine, events = await _restore(live_registry, _entities())

    template = await _one(live_registry, "templates")
    document = await _one(live_registry, "documents")
    # The archive's own ids are kept...
    assert (template["template_id"], document["document_id"]) == ("TPL-1", "DOC-1")
    assert document["template_id"] == "TPL-1"
    # ...and what it does not carry points at this install's entities.
    assert template["fields"][0]["terminology_ref"] == "LIVE-TL"
    (term_ref,) = document["term_references"]
    assert (term_ref["term_id"], term_ref["terminology_ref"]) == ("LIVE-KG", "LIVE-TL")

    assert engine.result["repointed_referents"] == 2
    assert engine.result["rehashed_documents"] == 1
    assert not [e.message for e in events if e.phase == "warning" and "referenced" in e.message]


@pytest.mark.asyncio
async def test_an_identity_on_a_repointed_term_is_rehashed_and_rekeyed(live_registry):
    """The document's identity field is the term: its hash feeds the
    canonical id, which changed. The stored hash and the Registry key both
    follow, so a later write of the same identity versions the restored
    document instead of minting a second one."""
    await _live_entry("LIVE-TL", "terminologies", TL_KEY)
    await _live_entry("LIVE-KG", "terms", KG_KEY)

    await _restore(live_registry, _entities())

    new_hash = _hash_for("LIVE-KG")
    assert new_hash != _hash_for("SRC-KG")
    document = await _one(live_registry, "documents")
    assert document["identity_hash"] == new_hash

    entry = await _entry(live_registry, "DOC-1")
    assert entry["primary_composite_key"]["identity_hash"] == new_hash
    assert entry["primary_composite_key_hash"] == compute_composite_key_hash(
        entry["primary_composite_key"]
    )

    # The uniqueness claim the restore wrote is the new key's, so the next
    # write of this identity (same template, same resolved term) is gated
    # onto DOC-1. (The engine writes Registry rows to wip_registry; the
    # in-process Registry of this suite reads another database, so the
    # register round trip itself cannot be exercised here.)
    claims_db, claims_coll = CLAIMS_COLLECTION
    claim = await live_registry[claims_db][claims_coll].find_one({
        "namespace": SOURCE, "entity_type": "documents", "kind": "primary",
        "owner_entry_id": "DOC-1",
    })
    assert claim["composite_key_hash"] == compute_composite_key_hash(
        {"ns": SOURCE, "template_id": "TPL-1", "identity_hash": new_hash}
    )


@pytest.mark.asyncio
async def test_a_same_install_restore_writes_the_archive_unchanged(live_registry):
    """On the install the archive came from, every referent resolves to
    itself: nothing is mapped and every row lands as archived."""
    await _live_entry("SRC-TL", "terminologies", TL_KEY)
    await _live_entry("SRC-KG", "terms", KG_KEY)
    entities = _entities()

    engine, _ = await _restore(live_registry, entities)

    assert await _one(live_registry, "templates") == entities["templates"][0]
    assert await _one(live_registry, "documents") == entities["documents"][0]
    assert await _entry(live_registry, "DOC-1") == entities["registry_entries"][1]
    assert engine.result["referents"]["resolved"] == 2
    assert (engine.result["repointed_referents"], engine.result["rehashed_documents"]) == (0, 0)


@pytest.mark.asyncio
async def test_an_unresolved_referent_keeps_its_id_and_names_the_order_rule(live_registry):
    await _live_entry("LIVE-TL", "terminologies", TL_KEY)  # the term is missing

    engine, events = await _restore(live_registry, _entities())

    document = await _one(live_registry, "documents")
    assert document["term_references"][0]["term_id"] == "SRC-KG"
    # Its terminology was found; the term was not, so the identity feed did
    # not change and nothing is rehashed.
    assert document["term_references"][0]["terminology_ref"] == "LIVE-TL"
    assert engine.result["rehashed_documents"] == 0
    warnings = [e.message for e in events if e.phase == "warning"]
    assert any("SRC-KG" in w and "restore that namespace first" in w for w in warnings)


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_repointing_and_writes_nothing(live_registry):
    await _live_entry("LIVE-TL", "terminologies", TL_KEY)
    await _live_entry("LIVE-KG", "terms", KG_KEY)

    engine, _ = await _restore(live_registry, _entities(), dry_run=True)

    assert engine.result["dry_run"] is True
    assert engine.result["referents"]["resolved"] == 2
    assert engine.result["repointed_referents"] == 2
    db_name, coll_name = COLLECTION_MAP["documents"]
    assert await live_registry[db_name][coll_name].count_documents({"namespace": SOURCE}) == 0

