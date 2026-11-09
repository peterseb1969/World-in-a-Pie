"""A restore does not read a Registry outage as an answer, carries synonyms
in their own entry's scope, and a merge checks every reference kind.

- An edge-type endpoint that cannot be checked because the Registry did not
  answer fails the restore; only "not found" or "ambiguous" (both answers)
  clear the endpoint's resolved half, and the warning says which.
- A carried-over synonym is written without scope fields, so it takes its
  entry's; an archived key that names a foreign namespace is refused before
  anything is written.
- The merge's pre-write gate checks a document's template, term and
  terminology references, not only its document references.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wip_archive.models import ProgressEvent

from document_store.services.backup_engine import (
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)
from registry.models.entry import RegistryEntry
from wip_auth.registry_client import RegistryUnavailableError
from wip_auth.resolve import AmbiguousEntityError

from .test_remap_integration import (  # noqa: F401
    SOURCE,
    TARGET,
    _rows,
    _run_remap,
    live_registry,
)

# --------------------------------------------------------------------------
# Edge-type endpoints
# --------------------------------------------------------------------------


def _endpoint_setup(events: list[ProgressEvent]):
    from wip_archive.remap import IDRemapper

    from document_store.services.remap_restore import RemapPlan

    edge = {
        "template_id": "new-edge-id", "value": "EDGE", "usage": "relationship",
        "source_templates": [{"lookup_value": "FAR_TPL", "resolved": "old-foreign-id"}],
        "target_templates": [],
    }
    plan = RemapPlan(id_map={"templates": {}}, rows={"templates": [edge]})
    engine = DirectRestoreEngine(MagicMock(), None, events.append)
    return engine, edge, plan, IDRemapper()


@pytest.mark.asyncio
async def test_an_unanswered_endpoint_check_fails_the_restore_and_clears_nothing():
    events: list[ProgressEvent] = []
    engine, edge, plan, remapper = _endpoint_setup(events)
    outage = AsyncMock(side_effect=RegistryUnavailableError("registry unreachable"))
    with patch("wip_auth.resolve.resolve_entity_id", outage), \
            pytest.raises(RestoreEngineError, match="did not answer"):
        await engine._repair_endpoint_declarations({"src": "tgt"}, {"src": plan}, remapper)
    assert edge["source_templates"] == [{"lookup_value": "FAR_TPL", "resolved": "old-foreign-id"}]
    assert not [e for e in events if e.phase == "warning"]


@pytest.mark.asyncio
async def test_an_ambiguous_endpoint_is_cleared_and_called_ambiguous():
    events: list[ProgressEvent] = []
    engine, edge, plan, remapper = _endpoint_setup(events)
    ambiguous = AmbiguousEntityError("FAR_TPL", "template", candidates=[{}, {}], total=2)
    with patch("wip_auth.resolve.resolve_entity_id", AsyncMock(side_effect=ambiguous)):
        await engine._repair_endpoint_declarations({"src": "tgt"}, {"src": plan}, remapper)
    assert edge["source_templates"] == [{"lookup_value": "FAR_TPL", "resolved": None}]
    (warning,) = [e.message for e in events if e.phase == "warning"]
    assert "ambiguous" in warning and "2 templates" in warning


# --------------------------------------------------------------------------
# Synonym scope
# --------------------------------------------------------------------------


def _entities_with_synonyms(*synonyms: dict) -> dict:
    return {
        "templates": [
            {"template_id": "OLD-TPL", "namespace": SOURCE,
             "value": "ITEM", "version": 1, "identity_fields": ["code"]},
        ],
        "documents": [
            {"document_id": "OLD-DOC", "namespace": SOURCE,
             "template_id": "OLD-TPL", "template_value": "ITEM",
             "template_version": 1, "identity_hash": "h-1189", "version": 1,
             "status": "active", "data": {"code": "X-1"}},
        ],
        "registry_entries": [
            {"entry_id": "OLD-DOC", "namespace": SOURCE, "entity_type": "documents",
             "primary_composite_key": {"ns": SOURCE, "template_id": "OLD-TPL",
                                       "identity_hash": "h-1189"},
             "synonyms": [
                 {"namespace": SOURCE, "entity_type": "documents",
                  "composite_key": {"@template": "ITEM", "code": "X-1"}},
                 *synonyms,
             ]},
        ],
    }


def test_a_rewritten_synonym_carries_no_scope_fields():
    item = DirectRestoreEngine._rewrite_synonym_for_restore(
        {"namespace": "elsewhere", "entity_type": "documents", "composite_key": {"erp_id": "E-9"}},
        SOURCE, TARGET, {}, None,
    )
    assert item == {"synonym_composite_key": {"erp_id": "E-9"}}


@pytest.mark.asyncio
async def test_a_synonym_stored_with_a_foreign_scope_restores_in_its_entrys_scope(live_registry):  # noqa: F811
    """An archive from before scope was enforced: the synonym record names a
    foreign namespace. It is restored on its own entry, in the target."""
    await _run_remap(live_registry, _entities_with_synonyms(
        {"namespace": "some-other-ns", "entity_type": "documents", "composite_key": {"erp_id": "E-9"}},
    ))
    (doc,) = await _rows(live_registry, "documents")
    entry = await RegistryEntry.find_one({"entry_id": doc["document_id"]})
    keys = [s.composite_key for s in entry.synonyms]
    assert {"erp_id": "E-9"} in keys


@pytest.mark.asyncio
async def test_a_key_naming_a_foreign_namespace_is_refused_before_writing(live_registry):  # noqa: F811
    with pytest.raises(RestoreEngineError, match="name a namespace other than"):
        await _run_remap(live_registry, _entities_with_synonyms(
            {"namespace": SOURCE, "entity_type": "documents",
             "composite_key": {"ns": "some-other-ns", "value": "X"}},
        ))
    assert await _rows(live_registry, "documents") == []
    assert await RegistryEntry.find({"namespace": TARGET}).count() == 0


# --------------------------------------------------------------------------
# Merge reference gate
# --------------------------------------------------------------------------


def test_the_merge_collector_reads_every_resolved_kind():
    wanted = DirectRestoreEngine._referenced_ids([{
        "template_id": "TPL",
        "references": [
            {"resolved": {"document_id": "DOC"}},
            {"resolved": {"template_id": "REF-TPL"}},
            {"resolved": {"term_id": "TERM"}},
            {"resolved": {"terminology_id": "LOV"}},
        ],
    }])
    assert wanted["documents"] == {"DOC"}
    assert wanted["templates"] == {"TPL", "REF-TPL"}
    assert wanted["terms"] == {"TERM"}
    assert wanted["terminologies"] == {"LOV"}


@pytest.mark.asyncio
async def test_a_merge_with_a_dangling_terminology_reference_is_refused(live_registry):  # noqa: F811
    db_name, coll_name = COLLECTION_MAP["terminologies"]
    await live_registry[db_name][coll_name].insert_one(
        {"terminology_id": "HELD-LOV", "namespace": TARGET, "value": "HELD"}
    )
    engine = DirectRestoreEngine(live_registry, None, lambda e: None)

    await engine._check_merge_wanted(TARGET, {"terminologies": {"HELD-LOV"}}, {})
    await engine._check_merge_wanted(TARGET, {"terminologies": {"COMING"}}, {"terminologies": {"COMING"}})
    with pytest.raises(RestoreEngineError, match="terminology 'GONE-LOV'"):
        await engine._check_merge_wanted(TARGET, {"terminologies": {"GONE-LOV"}}, {})
