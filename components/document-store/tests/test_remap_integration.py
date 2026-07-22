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
