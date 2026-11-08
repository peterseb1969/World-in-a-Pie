"""A fresh (remap) restore's skips, multi-target activation and rehash warning,
pinned before its refactor.

Provenance: CASE-1161. run_remap's skip_documents / skip_files branches, the
activation of an archive mapped onto two targets (each target activates only
its own sources' entries), and the warning for documents whose identity hash
cannot be recomputed had no test. These pin them against the real test
MongoDB with the fake Registry the other remap tests use.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, NamespaceEntry

from document_store.services.backup_engine import COLLECTION_MAP, DirectRestoreEngine
from tests.test_merge_restore import (
    NAMESPACE,
    REMAP_TARGET,
    _all_collections,
    _archive,
    _FakeRegistry,
    _run_remap,
)

SOURCES = ("rm1161-src-a", "rm1161-src-b")
TARGETS = ("rm1161-tgt-a", "rm1161-tgt-b")
CLEARED = (NAMESPACE, REMAP_TARGET, *SOURCES, *TARGETS)


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: {"$in": list(CLEARED)}})

    await _clear()
    yield client
    await _clear()
    client.close()


async def _rows(mongo, entity_type: str, namespace: str) -> list[dict]:
    db_name, coll_name = COLLECTION_MAP[entity_type]
    return await mongo[db_name][coll_name].find({"namespace": namespace}).to_list(None)


def _content(ns: str, tag: str) -> dict[str, list[dict]]:
    return {
        "terminologies": [{"terminology_id": f"OLD-LOV-{tag}", "namespace": ns, "value": f"V{tag}"}],
        "templates": [{"template_id": f"OLD-TPL-{tag}", "namespace": ns, "value": f"T{tag}",
                       "version": 1, "identity_fields": [], "fields": []}],
        "documents": [{"document_id": f"OLD-DOC-{tag}", "namespace": ns, "template_id": f"OLD-TPL-{tag}",
                       "template_version": 1, "identity_hash": "", "version": 1, "data": {}}],
        "files": [{"file_id": f"OLD-F-{tag}", "namespace": ns}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("skip_documents", "skip_files"), [(True, False), (False, True), (True, True)])
async def test_the_skips_leave_out_only_their_own_rows(mongo, skip_documents, skip_files):
    await _run_remap(mongo, _archive(_content(NAMESPACE, "x")),
                     skip_documents=skip_documents, skip_files=skip_files)
    assert len(await _rows(mongo, "terminologies", REMAP_TARGET)) == 1
    assert len(await _rows(mongo, "templates", REMAP_TARGET)) == 1
    assert len(await _rows(mongo, "documents", REMAP_TARGET)) == (0 if skip_documents else 1)
    assert len(await _rows(mongo, "files", REMAP_TARGET)) == (0 if skip_files else 1)


def _two_namespace_reader():
    per_ns = {src: _content(src, src[-1]) for src in SOURCES}
    manifest = Manifest(
        format_version="3.0",
        namespaces=[NamespaceEntry(
            prefix=ns,
            namespace_config=NamespaceConfig(namespace_id=f"019a0000-0000-7000-8000-00000000116{i}", prefix=ns),
            counts=EntityCounts(),
        ) for i, ns in enumerate(per_ns)],
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
    return reader


@pytest.mark.asyncio
async def test_two_targets_each_activate_only_their_own_entries(mongo):
    registry = _FakeRegistry()
    activations: list[list[str]] = []
    original_post = registry.post

    async def post(url, json=None, headers=None):
        if url.endswith("/entries/activate"):
            activations.append(sorted(item["entry_id"] for item in json))
        return await original_post(url, json=json, headers=headers)

    http = MagicMock()
    http.post = post
    http.put = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    engine = DirectRestoreEngine(mongo, None, lambda event: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_two_namespace_reader()), \
            patch("httpx.AsyncClient", return_value=http):
        await engine.run_remap(MagicMock(), "", namespace_map=dict(zip(SOURCES, TARGETS, strict=True)))

    written = {}
    for target in TARGETS:
        ids = []
        for entity_type in ("terminologies", "templates", "documents", "files"):
            key = {"terminologies": "terminology_id", "templates": "template_id",
                   "documents": "document_id", "files": "file_id"}[entity_type]
            ids += [r[key] for r in await _rows(mongo, entity_type, target)]
        written[target] = sorted(ids)
        assert len(ids) == 4, (target, ids)
    # One activation per target, carrying exactly that target's entries.
    assert activations == [written[TARGETS[0]], written[TARGETS[1]]]


@pytest.mark.asyncio
async def test_a_document_whose_template_is_unknown_is_reported(mongo):
    events: list = []
    content = _content(NAMESPACE, "x")
    content["templates"] = []  # the document's template is in neither the archive nor the store
    content["documents"][0]["identity_hash"] = "h1"  # a keyed row: its hash cannot be recomputed
    with patch("document_store.services.backup_engine.DirectRestoreEngine._external_template_definitions",
               AsyncMock(return_value={})):
        await _run_remap(mongo, _archive(content), events)
    warnings = [e.message for e in events if e.phase == "warning"]
    assert any("keep their archived identity hash" in w for w in warnings), warnings
