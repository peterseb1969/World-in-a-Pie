"""An N:1 fresh restore refuses key collisions before it writes anything.

Provenance: CASE-1096 part 3. plan_multi checked each type's Registry keys
when it reached the type — after the earlier types were provisioned and the
target namespace created. A collision found at documents left a created,
half-reserved target that a retry then refused as not empty. The same check
now runs for every type first, before any namespace is created.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wip_archive.remap import IDRemapper

from document_store.services.remap_restore import (
    RemapCollisionError,
    RemapSource,
    check_collisions_before_writing,
    plan_multi,
)


def _template(tid: str, value: str) -> dict:
    return {"template_id": tid, "value": value, "version": 1}


def _doc(did: str, tid: str, identity_hash: str) -> dict:
    return {"document_id": did, "template_id": tid, "identity_hash": identity_hash, "version": 1}


def _documents_only_collision() -> list[RemapSource]:
    """ns-a's documents are pinned to ns-b's template (legal), and one of them
    carries the same identity as ns-b's own. Nothing collides before documents."""
    return [
        RemapSource("ns-a", "one", {"documents": [_doc("D-A", "T-B", "same")]}),
        RemapSource("ns-b", "one", {
            "templates": [_template("T-B", "TRIAL")],
            "documents": [_doc("D-B", "T-B", "same")],
        }),
    ]


def _recording_provisioner():
    calls: list[str] = []

    def for_target(target: str):
        async def provision(entity_type, keys):
            calls.append(entity_type)
            return [f"NEW-{entity_type}-{i}" for i in range(len(keys))]
        return provision

    return for_target, calls


@pytest.mark.asyncio
async def test_plan_multi_finds_it_only_after_provisioning_earlier_types():
    """The behaviour this guards against: the per-type check refuses at
    documents, after templates were already provisioned."""
    for_target, calls = _recording_provisioner()
    with pytest.raises(RemapCollisionError):
        await plan_multi(_documents_only_collision(), IDRemapper(), for_target)
    assert calls == ["templates"]


def test_the_up_front_check_refuses_the_same_collision():
    with pytest.raises(RemapCollisionError) as refused:
        check_collisions_before_writing(_documents_only_collision())
    assert "ns-a" in str(refused.value) and "ns-b" in str(refused.value)


def test_documents_on_different_templates_do_not_collide():
    sources = [
        RemapSource("ns-a", "one", {
            "templates": [_template("T-A", "ALPHA")],
            "documents": [_doc("D-A", "T-A", "same")],
        }),
        RemapSource("ns-b", "one", {
            "templates": [_template("T-B", "BETA")],
            "documents": [_doc("D-B", "T-B", "same")],
        }),
    ]
    check_collisions_before_writing(sources)


def test_a_term_collision_is_seen_through_its_terminology():
    """A term's key embeds its terminology's NEW id: two same-valued terms
    under two colliding terminologies are refused at terminologies already."""
    sources = [
        RemapSource("ns-a", "one", {"terminologies": [{"terminology_id": "L-A", "value": "S", "label": "S"}]}),
        RemapSource("ns-b", "one", {"terminologies": [{"terminology_id": "L-B", "value": "S", "label": "S"}]}),
    ]
    with pytest.raises(RemapCollisionError) as refused:
        check_collisions_before_writing(sources)
    assert refused.value.collisions[0][0] == "terminologies"


def test_a_one_to_one_restore_is_never_checked():
    """Only targets several sources fold into can collide."""
    sources = [
        RemapSource("ns-a", "one", {"documents": [_doc("D1", "T", "same"), _doc("D2", "T", "same")]}),
    ]
    check_collisions_before_writing(sources)


@pytest.mark.asyncio
async def test_the_engine_refuses_before_creating_the_target(tmp_path):
    """Run end to end up to the refusal: no namespace upsert, no provisioning."""
    from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, NamespaceEntry

    from document_store.services.backup_engine import DirectRestoreEngine

    per_ns = {
        "ns-a": {"documents": [{**_doc("D-A", "T-B", "same"), "namespace": "ns-a", "data": {}}]},
        "ns-b": {
            "templates": [{**_template("T-B", "TRIAL"), "namespace": "ns-b", "fields": [], "identity_fields": ["k"]}],
            "documents": [{**_doc("D-B", "T-B", "same"), "namespace": "ns-b", "data": {}}],
        },
    }
    manifest = Manifest(
        format_version="3.0",
        namespaces=[NamespaceEntry(prefix=ns, namespace_config=NamespaceConfig(namespace_id="019a0000-0000-7000-8000-0000000c1084", prefix=ns), counts=EntityCounts())
                    for ns in per_ns],
        counts=EntityCounts(),
    )
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=manifest)
    reader.read_manifest_raw = MagicMock(return_value={})
    reader.list_namespaces = MagicMock(return_value=list(per_ns))
    reader.list_blobs = MagicMock(return_value=[])
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [dict(e) for e in per_ns.get(namespace, {}).get(et, [])]
    )
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=None)

    engine = DirectRestoreEngine(MagicMock(), None, lambda event: None)
    engine._check_namespace_empty = AsyncMock()
    upsert = AsyncMock()
    engine._upsert_namespace = upsert
    engine._registry_provisioner = MagicMock(side_effect=AssertionError("provisioned"))
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=reader), \
            pytest.raises(RemapCollisionError):
        await engine.run_remap(tmp_path / "x.zip", "", namespace_map={"ns-a": "one", "ns-b": "one"})
    upsert.assert_not_awaited()
