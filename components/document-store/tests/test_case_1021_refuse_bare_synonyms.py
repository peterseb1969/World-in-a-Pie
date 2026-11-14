"""The old, bare form of document identity synonyms is not restored.

An archive whose document entries carry identity synonyms without
"@template" (the key set is exactly the template's identity_fields) is
refused before anything is written, in every restore mode. The requalify
pass is no longer part of a fresh restore.
"""

from __future__ import annotations

import pytest

from document_store.services import backup_engine
from document_store.services.backup_engine import DirectRestoreEngine, RestoreEngineError
from registry.models.entry import RegistryEntry

from .test_remap_integration import (  # noqa: F401
    SOURCE,
    TARGET,
    _archive,
    _rows,
    _run_remap,
    live_registry,
)


def _entities(identity_synonym: dict) -> dict:
    return {
        "templates": [
            {"template_id": "OLD-TPL", "namespace": SOURCE,
             "value": "ITEM", "version": 1, "identity_fields": ["code"]},
        ],
        "documents": [
            {"document_id": "OLD-DOC", "namespace": SOURCE,
             "template_id": "OLD-TPL", "template_value": "ITEM",
             "template_version": 1, "identity_hash": "h-1021", "version": 1,
             "status": "active", "data": {"code": "X-1"}},
        ],
        "registry_entries": [
            {"entry_id": "OLD-DOC", "namespace": SOURCE, "entity_type": "documents",
             "primary_composite_key": {"ns": SOURCE, "template_id": "OLD-TPL",
                                       "identity_hash": "h-1021"},
             "synonyms": [{"namespace": SOURCE, "entity_type": "documents",
                           "composite_key": identity_synonym}]},
        ],
    }


def _engine() -> DirectRestoreEngine:
    return DirectRestoreEngine(None, None, lambda event: None)


@pytest.mark.asyncio
async def test_bare_identity_synonyms_are_refused_with_a_count_per_namespace():
    reader = _archive(_entities({"code": "X-1"}))

    with pytest.raises(RestoreEngineError) as exc:
        await _engine()._refuse_bare_identity_synonyms(reader, reader.read_manifest())

    assert f"{SOURCE}: 1 document(s)" in str(exc.value)
    assert "earlier WIP release" in str(exc.value)
    # It names the entry and key, and the current-release remedy
    assert "OLD-DOC {'code': 'X-1'}" in str(exc.value)
    assert "remove them" in str(exc.value)


@pytest.mark.asyncio
async def test_qualified_identity_synonyms_and_custom_synonyms_pass():
    entities = _entities({"@template": "ITEM", "code": "X-1"})
    entities["registry_entries"][0]["synonyms"].append(
        {"namespace": SOURCE, "entity_type": "documents", "composite_key": {"erp_id": "E-9"}},
    )
    reader = _archive(entities)

    await _engine()._refuse_bare_identity_synonyms(reader, reader.read_manifest())


@pytest.mark.asyncio
async def test_an_inactive_bare_synonym_is_not_counted():
    """A synonym released when its entity retired is not restored as a key,
    so it does not refuse the restore (as the foreign-scope gate counts)."""
    entities = _entities({"code": "X-1"})
    entities["registry_entries"][0]["synonyms"][0]["status"] = "inactive"
    reader = _archive(entities)

    await _engine()._refuse_bare_identity_synonyms(reader, reader.read_manifest())


@pytest.mark.asyncio
async def test_a_template_that_inherits_its_identity_is_judged_by_its_parents():
    """A child template stores no identity of its own: its documents are
    hashed by the parent's fields, and a synonym of exactly those is bare."""
    entities = _entities({"code": "X-1"})
    parent = entities["templates"][0]
    entities["templates"] = [
        {**parent, "template_id": "PARENT-TPL", "value": "BASE"},
        {**parent, "template_id": "OLD-TPL", "identity_fields": [], "extends": "PARENT-TPL",
         "extends_version": 1},
    ]
    reader = _archive(entities)

    with pytest.raises(RestoreEngineError, match="old bare form"):
        await _engine()._refuse_bare_identity_synonyms(reader, reader.read_manifest())


@pytest.mark.asyncio
async def test_a_template_the_archive_does_not_carry_is_read_from_this_install(
    session_mongo_client,
):
    """The Registry's doors read template-store for a template the archive
    lacks; the restore reads the same, so it refuses up front what the
    Registry would refuse halfway through the restore."""
    entities = _entities({"code": "X-1"})
    entities["templates"] = []
    reader = _archive(entities)
    db, coll = backup_engine.COLLECTION_MAP["templates"]
    await session_mongo_client[db][coll].insert_one(
        {"template_id": "OLD-TPL", "namespace": "elsewhere", "value": "ITEM", "version": 1,
         "identity_fields": ["code"]},
    )
    try:
        engine = DirectRestoreEngine(session_mongo_client, None, lambda event: None)
        with pytest.raises(RestoreEngineError, match="old bare form"):
            await engine._refuse_bare_identity_synonyms(reader, reader.read_manifest())
    finally:
        await session_mongo_client[db][coll].delete_many({"template_id": "OLD-TPL"})


@pytest.mark.asyncio
async def test_a_fresh_restore_of_an_old_archive_writes_nothing(live_registry):  # noqa: F811
    with pytest.raises(RestoreEngineError, match="old bare form"):
        await _run_remap(live_registry, _entities({"code": "X-1"}))

    # The fixture registers the target namespace up front; nothing else of
    # the archive reached it.
    assert await _rows(live_registry, "documents") == []
    assert await RegistryEntry.find({"namespace": TARGET}).count() == 0


def test_fresh_restore_no_longer_runs_the_requalify_pass():
    assert not hasattr(backup_engine, "requalify_identity_synonyms")
