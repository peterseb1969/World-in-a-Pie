"""A fresh restore writes each document's Registry entry once.

A document's archived synonyms (its template-qualified identity values,
any custom ones) travel with its activation item, so the Registry claims and
writes them together with the final key. Only the synonyms of entities that
activate keyless (terminologies, terms, templates) still go through
/synonyms/add.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

from document_store.services.backup_engine import DirectRestoreEngine
from registry.main import app as registry_app
from registry.models.entry import RegistryEntry

from .test_remap_integration import SOURCE, TARGET, _rows, _run_remap, live_registry  # noqa: F401

_IDENTITY_SYNONYM = {"@template": "ITEM", "code": "X-1"}


@pytest.mark.asyncio
async def test_document_synonyms_ride_on_activation(live_registry, monkeypatch):  # noqa: F811
    added: list[dict] = []
    real_add = DirectRestoreEngine._add_synonyms

    async def recording_add(self, namespace, items):
        added.extend(items)
        return await real_add(self, namespace, items)

    monkeypatch.setattr(DirectRestoreEngine, "_add_synonyms", recording_add)

    await _run_remap(live_registry, {
        "terminologies": [
            {"terminology_id": "OLD-LOV", "namespace": SOURCE,
             "value": "COLOUR", "label": "Colour"},
        ],
        "templates": [
            {"template_id": "OLD-TPL", "namespace": SOURCE,
             "value": "ITEM", "version": 1, "identity_fields": ["code"]},
        ],
        "documents": [
            {"document_id": "OLD-DOC", "namespace": SOURCE,
             "template_id": "OLD-TPL", "template_value": "ITEM",
             "template_version": 1, "identity_hash": "h-1020", "version": 1,
             "status": "active", "data": {"code": "X-1"}},
        ],
        "registry_entries": [
            {"entry_id": "OLD-LOV", "namespace": SOURCE, "entity_type": "terminologies",
             "primary_composite_key": {"ns": SOURCE, "value": "COLOUR", "label": "Colour"},
             "synonyms": [{"namespace": SOURCE, "entity_type": "terminologies",
                           "composite_key": {"ns": SOURCE, "type": "terminology",
                                             "value": "COLOUR"}}]},
            {"entry_id": "OLD-DOC", "namespace": SOURCE, "entity_type": "documents",
             "primary_composite_key": {"ns": SOURCE, "template_id": "OLD-TPL",
                                       "identity_hash": "h-1020"},
             "synonyms": [{"namespace": SOURCE, "entity_type": "documents",
                           "composite_key": _IDENTITY_SYNONYM}]},
        ],
    })

    (document,) = await _rows(live_registry, "documents")
    doc_id = document["document_id"]
    (terminology,) = await _rows(live_registry, "terminologies")

    # The document's synonym was not added afterwards; the terminology's was.
    assert all(item["target_id"] != doc_id for item in added)
    assert any(item["target_id"] == terminology["terminology_id"] for item in added)

    # The document's entry holds its identity synonym, and resolves by it.
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == doc_id)
    assert entry.status == "active"
    carried = [s for s in entry.synonyms if s.composite_key == _IDENTITY_SYNONYM]
    assert len(carried) == 1 and carried[0].status != "inactive"
    async with httpx.AsyncClient(transport=ASGITransport(app=registry_app)) as client:
        resp = await client.post(
            "http://registry/api/registry/entries/lookup/by-key",
            json=[{"namespace": TARGET, "entity_type": "documents",
                   "composite_key": _IDENTITY_SYNONYM, "search_synonyms": True}],
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
        )
    result = resp.json()["results"][0]
    assert result["status"] == "found" and result["entry_id"] == doc_id
