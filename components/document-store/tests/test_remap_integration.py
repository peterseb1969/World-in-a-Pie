"""Remap restore against a REAL Registry, in process.

The unit tests stub provisioning, so they pin the engine's orchestration and
nothing about whether the Registry agrees with it. That leaves the mode's
sharpest risk untested: composite keys are what the Registry deduplicates on,
and a key of the wrong shape does not fail — it silently creates an identity
that nothing else will ever match.

These tests mount the Registry in process (the conftest already does it for
everything else) and let the engine's own HTTP calls reach it. Real ID
generation, the real two-phase claim protocol, the real reserved→active
transition. No extra infrastructure: CI already provisions MongoDB for this
component, which is all the Registry needs.

The decisive test is `test_a_later_registration_deduplicates_against_the_copy`:
it registers the same terminology the way def-store would, afterwards, and
asserts the Registry hands back the id the remap minted. If the key shapes
were wrong that call would mint a second identity for one entity, and every
cheaper assertion here would still pass.
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
    COLLECTION_MAP,
    DirectRestoreEngine,
)
from registry.main import app as registry_app
from registry.models.entry import RegistryEntry
from registry.models.namespace import Namespace

from .conftest import setup_registry_and_app

SOURCE = "remap-src-ns"
TARGET = "remap-live-ns"


@pytest_asyncio.fixture
async def live_registry():
    """Beanie up, the Registry mounted, and the target namespace registered."""
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await setup_registry_and_app(mongo_client)

    for db_name, coll_name in COLLECTION_MAP.values():
        await mongo_client[db_name][coll_name].delete_many({"namespace": TARGET})
    await RegistryEntry.find({"namespace": TARGET}).delete()
    await Namespace.find({"prefix": TARGET}).delete()
    await Namespace(prefix=TARGET, description="").insert()

    yield mongo_client

    for db_name, coll_name in COLLECTION_MAP.values():
        await mongo_client[db_name][coll_name].delete_many({"namespace": TARGET})
    await RegistryEntry.find({"namespace": TARGET}).delete()
    await Namespace.find({"prefix": TARGET}).delete()


def _archive(entities):
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=Manifest(
        format_version="3.0",
        namespace=SOURCE,
        namespace_config=NamespaceConfig(prefix=SOURCE, isolation_mode="open"),
        counts=EntityCounts(),
    ))
    reader.list_namespaces = MagicMock(return_value=[SOURCE])
    reader.list_blobs = MagicMock(return_value=[])
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [
            dict(e) for e in entities.get(et, [])
        ]
    )
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=None)
    return reader


async def _run_remap(mongo, entities, events=None):
    """Run a remap whose Registry calls reach the mounted Registry app."""
    transport = ASGITransport(app=registry_app)
    # Captured before patching: the factory below replaces this very symbol,
    # so calling it by name would recurse into itself.
    real_client = httpx.AsyncClient

    def _client(*_args, **kwargs):
        # The engine builds its own client; swap in one that speaks to the
        # mounted Registry instead of the network.
        kwargs.pop("timeout", None)
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    def callback(event: ProgressEvent) -> None:
        if events is not None:
            events.append(event)

    engine = DirectRestoreEngine(mongo, None, callback)
    with patch(
        "document_store.services.backup_engine.ArchiveReader",
        return_value=_archive(entities),
    ), patch("httpx.AsyncClient", _client):
        await engine.run_remap(MagicMock(), TARGET)


async def _rows(mongo, entity_type):
    db_name, coll_name = COLLECTION_MAP[entity_type]
    return await mongo[db_name][coll_name].find(
        {"namespace": TARGET}, {"_id": 0}
    ).to_list(length=None)


class TestRemapAgainstARealRegistry:
    @pytest.mark.asyncio
    async def test_ids_come_from_the_registry_and_are_active(self, live_registry):
        await _run_remap(live_registry, {
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": SOURCE,
                 "value": "GENDER", "label": "Gender"},
            ],
        })

        (row,) = await _rows(live_registry, "terminologies")
        assert row["terminology_id"] != "OLD-LOV"

        entry = await RegistryEntry.find_one(
            RegistryEntry.entry_id == row["terminology_id"]
        )
        assert entry is not None, "the Registry has no entry for the minted id"
        # Activated at the end of the run: reserved entries do not resolve, so
        # a namespace left reserved would be invisible.
        assert entry.status == "active"
        assert entry.namespace == TARGET

    @pytest.mark.asyncio
    async def test_a_later_registration_deduplicates_against_the_copy(
        self, live_registry
    ):
        # The decisive one. Composite keys are what the Registry dedups on, so
        # a wrong shape creates an identity nothing matches — and every
        # cheaper assertion still passes. Registering the same terminology the
        # way def-store does must return the id the remap minted.
        await _run_remap(live_registry, {
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": SOURCE,
                 "value": "GENDER", "label": "Gender"},
            ],
        })
        (row,) = await _rows(live_registry, "terminologies")

        transport = ASGITransport(app=registry_app)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post(
                "http://registry/api/registry/entries/register",
                json=[{
                    "namespace": TARGET,
                    "entity_type": "terminologies",
                    # Exactly what def-store's registry client sends.
                    "composite_key": {
                        "ns": TARGET, "value": "GENDER", "label": "Gender",
                    },
                }],
                headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            )

        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["status"] == "already_exists"
        assert result["registry_id"] == row["terminology_id"]

    @pytest.mark.asyncio
    async def test_a_terms_key_resolves_through_its_new_terminology(
        self, live_registry
    ):
        # A term's key embeds its terminology's id, so this fails unless the
        # parent was provisioned first and its NEW id used in the child's key.
        await _run_remap(live_registry, {
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": SOURCE,
                 "value": "GENDER", "label": "Gender"},
            ],
            "terms": [
                {"term_id": "OLD-T", "namespace": SOURCE,
                 "terminology_id": "OLD-LOV", "value": "M"},
            ],
        })

        (terminology,) = await _rows(live_registry, "terminologies")
        (term,) = await _rows(live_registry, "terms")
        assert term["terminology_id"] == terminology["terminology_id"]

        transport = ASGITransport(app=registry_app)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post(
                "http://registry/api/registry/entries/register",
                json=[{
                    "namespace": TARGET,
                    "entity_type": "terms",
                    "composite_key": {
                        "ns": TARGET,
                        "terminology_id": terminology["terminology_id"],
                        "value": "M",
                    },
                }],
                headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            )

        result = resp.json()["results"][0]
        assert result["status"] == "already_exists"
        assert result["registry_id"] == term["term_id"]

    @pytest.mark.asyncio
    async def test_every_minted_identity_is_claimed(self, live_registry):
        # The claim is the uniqueness gate. Provisioning claims two-phase, so
        # a remapped namespace whose keys were unclaimed would let the next
        # registration mint a duplicate.
        from registry.models.composite_key_claim import CompositeKeyClaim

        await _run_remap(live_registry, {
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": SOURCE,
                 "value": "GENDER", "label": "Gender"},
            ],
        })
        (row,) = await _rows(live_registry, "terminologies")

        claim = await CompositeKeyClaim.find_one(
            CompositeKeyClaim.owner_entry_id == row["terminology_id"]
        )
        assert claim is not None
        assert claim.state == "confirmed"

    @pytest.mark.asyncio
    async def test_an_id_identity_document_claims_its_recomputed_key(
        self, live_registry
    ):
        # CASE-787: a document whose identity field holds another document's id
        # (an edge's source_ref/target_ref — here a "ref" field) is re-minted
        # and its referenced id rewritten, so its identity_hash changes. The
        # remap recomputes the STORED hash; the claim must carry the SAME hash,
        # or the document is registered under a key that no longer describes it
        # and the next write on that identity duplicates instead of updating.
        # Before the fix the stored hash and the claim's hash diverged.
        from wip_auth.document_identity import compute_hash

        pre_remap_hash = compute_hash({"ref": "OLD-REF"})
        await _run_remap(live_registry, {
            "templates": [
                {"template_id": "OLD-ENT", "namespace": SOURCE,
                 "value": "THING", "version": 1, "identity_fields": []},
                {"template_id": "OLD-LINK", "namespace": SOURCE,
                 "value": "LINK", "version": 1, "identity_fields": ["ref"]},
            ],
            "documents": [
                {"document_id": "OLD-REF", "namespace": SOURCE,
                 "template_id": "OLD-ENT", "template_value": "THING",
                 "template_version": 1, "identity_hash": "", "version": 1,
                 "data": {"note": "the referenced entity"}},
                {"document_id": "OLD-EDGE", "namespace": SOURCE,
                 "template_id": "OLD-LINK", "template_value": "LINK",
                 "template_version": 1, "identity_hash": pre_remap_hash,
                 "version": 1, "data": {"ref": "OLD-REF"}},
            ],
        })

        docs = await _rows(live_registry, "documents")
        ref_doc = next(d for d in docs if d["data"].get("note"))
        edge = next(d for d in docs if "ref" in d["data"])

        # The reference was rewritten to the referenced doc's NEW id, and the
        # stored identity_hash was recomputed from it.
        assert edge["data"]["ref"] == ref_doc["document_id"]
        expected_hash = compute_hash({"ref": ref_doc["document_id"]})
        assert edge["identity_hash"] == expected_hash
        assert edge["identity_hash"] != pre_remap_hash

        # THE FIX: the Registry claim carries the SAME (recomputed) hash the
        # document stores — not the stale pre-remap one.
        entry = await RegistryEntry.find_one(
            RegistryEntry.entry_id == edge["document_id"]
        )
        assert entry is not None and entry.status == "active"
        assert entry.primary_composite_key["identity_hash"] == expected_hash

        # And a later write of the same identity dedups against the restored
        # edge instead of minting a second one.
        transport = ASGITransport(app=registry_app)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post(
                "http://registry/api/registry/entries/register",
                json=[{
                    "namespace": TARGET,
                    "entity_type": "documents",
                    "composite_key": {
                        "ns": TARGET,
                        "template_id": edge["template_id"],
                        "identity_hash": expected_hash,
                    },
                }],
                headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            )
        result = resp.json()["results"][0]
        assert result["status"] == "already_exists"
        assert result["registry_id"] == edge["document_id"]

    @pytest.mark.asyncio
    async def test_edge_type_endpoints_follow_the_restore_and_stay_addressable(
        self, live_registry
    ):
        # R-13 (CASE-773 backup/restore matrix): the id-identity claim above
        # proved the mechanism with one generic ref field; an edge type is the
        # real shape it exists for — usage="relationship", versioned=False, and
        # TWO id-valued identity fields (source_ref, target_ref). A fresh restore
        # must (a) re-point BOTH endpoints to the restored documents' NEW ids,
        # (b) recompute the identity_hash over the rewritten pair, and (c) claim
        # that recomputed hash so a later overwrite re-addresses the SAME edge
        # instead of forking a second one — the re-addressability that
        # versioned:false overwrite-in-place depends on. Live-validated against a
        # deployed backend via tools/backup-matrix/probe_backup_restore.py; this
        # pins it as a CI regression guard.
        from wip_auth.document_identity import compute_hash

        pre_remap_hash = compute_hash(
            {"source_ref": "OLD-SRC", "target_ref": "OLD-TGT"}
        )
        await _run_remap(live_registry, {
            "templates": [
                {"template_id": "OLD-THING", "namespace": SOURCE,
                 "value": "THING", "version": 1, "identity_fields": []},
                {"template_id": "OLD-LINK", "namespace": SOURCE,
                 "value": "LINKS", "version": 1, "usage": "relationship",
                 "versioned": False,
                 "identity_fields": ["source_ref", "target_ref"]},
            ],
            "documents": [
                {"document_id": "OLD-SRC", "namespace": SOURCE,
                 "template_id": "OLD-THING", "template_value": "THING",
                 "template_version": 1, "identity_hash": "", "version": 1,
                 "data": {"note": "source endpoint"}},
                {"document_id": "OLD-TGT", "namespace": SOURCE,
                 "template_id": "OLD-THING", "template_value": "THING",
                 "template_version": 1, "identity_hash": "", "version": 1,
                 "data": {"note": "target endpoint"}},
                {"document_id": "OLD-REL", "namespace": SOURCE,
                 "template_id": "OLD-LINK", "template_value": "LINKS",
                 "template_version": 1, "identity_hash": pre_remap_hash,
                 "version": 1,
                 "data": {"source_ref": "OLD-SRC", "target_ref": "OLD-TGT"}},
            ],
        })

        docs = await _rows(live_registry, "documents")
        endpoints = {d["data"].get("note"): d["document_id"]
                     for d in docs if d["data"].get("note")}
        edge = next(d for d in docs if "source_ref" in d["data"])
        new_src = endpoints["source endpoint"]
        new_tgt = endpoints["target endpoint"]

        # The restored edge type is still an edge type — usage/versioned survive.
        (link_tpl,) = [
            t for t in await _rows(live_registry, "templates")
            if t["value"] == "LINKS"
        ]
        assert link_tpl["usage"] == "relationship"
        assert link_tpl["versioned"] is False

        # (a) BOTH endpoints re-pointed to the restored documents' NEW ids.
        assert edge["data"]["source_ref"] == new_src
        assert edge["data"]["target_ref"] == new_tgt
        assert new_src not in ("OLD-SRC", "") and new_tgt not in ("OLD-TGT", "")

        # (b) the stored identity_hash recomputed over the rewritten pair.
        expected_hash = compute_hash(
            {"source_ref": new_src, "target_ref": new_tgt}
        )
        assert edge["identity_hash"] == expected_hash
        assert edge["identity_hash"] != pre_remap_hash

        # (c) the Registry claim carries the recomputed hash, so a later write of
        # the same edge dedups against the restored one (overwrite-in-place)
        # rather than minting a fork.
        entry = await RegistryEntry.find_one(
            RegistryEntry.entry_id == edge["document_id"]
        )
        assert entry is not None and entry.status == "active"
        assert entry.primary_composite_key["identity_hash"] == expected_hash

        transport = ASGITransport(app=registry_app)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post(
                "http://registry/api/registry/entries/register",
                json=[{
                    "namespace": TARGET,
                    "entity_type": "documents",
                    "composite_key": {
                        "ns": TARGET,
                        "template_id": edge["template_id"],
                        "identity_hash": expected_hash,
                    },
                }],
                headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            )
        result = resp.json()["results"][0]
        assert result["status"] == "already_exists"
        assert result["registry_id"] == edge["document_id"]

    @pytest.mark.asyncio
    async def test_documents_land_under_new_ids_pointing_at_new_templates(
        self, live_registry
    ):
        await _run_remap(live_registry, {
            "templates": [
                {"template_id": "OLD-TPL", "namespace": SOURCE,
                 "value": "PATIENT", "version": 1, "identity_fields": []},
            ],
            "documents": [
                {"document_id": "OLD-DOC", "namespace": SOURCE,
                 "template_id": "OLD-TPL", "template_version": 1,
                 "identity_hash": "", "version": 1, "data": {"age": 3}},
            ],
        })

        (template,) = await _rows(live_registry, "templates")
        (document,) = await _rows(live_registry, "documents")
        assert document["template_id"] == template["template_id"]
        assert document["document_id"] not in ("OLD-DOC", "")
        assert (
            await RegistryEntry.find_one(
                RegistryEntry.entry_id == document["document_id"]
            )
        ).status == "active"

    @pytest.mark.asyncio
    async def test_an_identity_less_document_stays_append_only_after_restore(
        self, live_registry
    ):
        # R-12 (CASE-773 matrix): an identity-less template (empty
        # identity_fields) declares append-only — its documents are addressed
        # only by a surrogate document_id and cannot be PATCHed (error_code
        # append_only, guarded in document_service by
        # `not validation_result.identity_fields`). A fresh restore must
        # preserve that contract: the restored template keeps its empty
        # identity_fields and the restored document keeps an empty
        # identity_hash, so it stays un-PATCHable. The guard keys purely on the
        # template's identity_fields regardless of how the document was
        # created, so preserving them across the restore is exactly what keeps
        # the restored document append-only — the rejection itself is pinned by
        # test_documents_patch::test_patch_no_identity_template_rejected_append_only.
        await _run_remap(live_registry, {
            "templates": [
                {"template_id": "OLD-LOG", "namespace": SOURCE,
                 "value": "EVENT_LOG", "version": 1, "identity_fields": []},
            ],
            "documents": [
                {"document_id": "OLD-EV", "namespace": SOURCE,
                 "template_id": "OLD-LOG", "template_value": "EVENT_LOG",
                 "template_version": 1, "identity_hash": "", "version": 1,
                 "data": {"event": "boot"}},
            ],
        })

        (template,) = await _rows(live_registry, "templates")
        (document,) = await _rows(live_registry, "documents")

        # The append-only contract survived the restore: no identity_fields on
        # the template and no identity_hash on the document — the exact
        # conditions the append_only PATCH guard keys on.
        assert template["identity_fields"] == []
        assert document["identity_hash"] == ""

        # And it landed under a fresh surrogate id, still active.
        assert document["document_id"] not in ("OLD-EV", "")
        entry = await RegistryEntry.find_one(
            RegistryEntry.entry_id == document["document_id"]
        )
        assert entry is not None and entry.status == "active"

    @pytest.mark.asyncio
    async def test_value_form_synonyms_survive_the_fresh_restore(self, live_registry):
        # CASE-792: a fresh restore re-mints each entity with its PRIMARY
        # composite key only. The value-form lookup synonym ({ns, type, value})
        # the create services auto-register lives in the archive's
        # registry_entries; without carrying it over a restored terminology
        # resolves only by canonical id, never by value (the reported bug). The
        # fix (_restore_synonyms) rewrites archived synonyms onto the re-minted
        # entities. Here the source terminology's archived registry entry carries
        # its value-form synonym; after remap, value-form lookup on the TARGET
        # must resolve to the re-minted id.
        await _run_remap(live_registry, {
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": SOURCE,
                 "value": "GENDER", "label": "Gender"},
            ],
            "registry_entries": [
                {"entry_id": "OLD-LOV", "namespace": SOURCE,
                 "entity_type": "terminologies",
                 "primary_composite_key": {"ns": SOURCE, "value": "GENDER",
                                           "label": "Gender"},
                 "synonyms": [{
                     "namespace": SOURCE,
                     "entity_type": "terminologies",
                     "composite_key": {"ns": SOURCE, "type": "terminology",
                                       "value": "GENDER"},
                 }]},
            ],
        })

        (row,) = await _rows(live_registry, "terminologies")
        new_id = row["terminology_id"]

        # The re-minted entry carries the rewritten value-form synonym...
        entry = await RegistryEntry.find_one(RegistryEntry.entry_id == new_id)
        assert entry is not None
        syn_keys = [s.composite_key for s in entry.synonyms]
        assert {"ns": TARGET, "type": "terminology", "value": "GENDER"} in syn_keys

        # ...and value-form lookup on the TARGET resolves to it — the exact
        # resolution that failed on a fresh-restored copy before the fix.
        transport = ASGITransport(app=registry_app)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post(
                "http://registry/api/registry/entries/lookup/by-key",
                json=[{
                    "namespace": TARGET,
                    "entity_type": "terminologies",
                    "composite_key": {"ns": TARGET, "type": "terminology",
                                      "value": "GENDER"},
                    "search_synonyms": True,
                }],
                headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            )
        result = resp.json()["results"][0]
        assert result["status"] == "found"
        assert result["entry_id"] == new_id
