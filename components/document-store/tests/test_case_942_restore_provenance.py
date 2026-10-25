"""Every restore mode leaves a structured provenance record on the namespace.

Operators must be able to tell restored data from data written through the
platform, and say where it came from. Each restore — id-preserving, remap
(fresh) and merge — inserts one record per namespace it wrote. The archive's
own records arrive first, marked inherited, so lineage survives any number
of backup/restore hops. A backup carries the records into its manifest; the
API serves them; the validation job a restore starts is linked from the
record; namespace deletion removes them (the registry's lists are pinned by
its own role-parity test).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import (
    EntityCounts,
    Manifest,
    NamespaceConfig,
    NamespaceEntry,
)

from document_store.models.backup_job import BackupJob, BackupJobKind
from document_store.models.restore_provenance import RestoreProvenance
from document_store.services import backup_service
from document_store.services.backup_engine import (
    _DB_REGISTRY,
    COLLECTION_MAP,
    PROVENANCE_COLLECTION,
    DirectBackupEngine,
    DirectRestoreEngine,
)
from tests.test_merge_restore import (
    REMAP_TARGET,
    _clear_remap_target,
    _run_remap,
)

NAMESPACE = "prov-942-ns"

HOP = {
    "transform": "filter",
    "toolkit_version": "0.7.0",
    "at": "2026-10-25T14:03:11Z",
    "params": {"templates": ["MONSTER"]},
    "params_redacted": False,
    "input": {"sha256": "9f2c", "format_version": "3.1", "derived": False},
    "verify": {"verdict": "pass", "findings": {"error": 0, "warning": 2, "info": 5}},
    "unknown_future_key": "kept",
}

EARLIER = {
    "namespace": "elsewhere",
    "restored_into": "prov-942-original",
    "restore_job_id": "job-earlier",
    "mode": "remap",
    "at": "2026-01-02T03:04:05+00:00",
    "actor": "someone",
    "archive": {"sha256": "abc"},
    "source": {"namespace": "older"},
    "derived_from": [],
    "merge": None,
    "validation_job_id": "val-earlier",
    "inherited": False,
}


@pytest_asyncio.fixture(autouse=True)
async def _beanie(session_mongo_client):
    # The remap path re-qualifies synonyms through Beanie models.
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)


@pytest_asyncio.fixture
async def mongo():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        db, coll = PROVENANCE_COLLECTION
        await client[db][coll].delete_many({"namespace": {"$in": [NAMESPACE, REMAP_TARGET]}})
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


async def _records(mongo, namespace):
    db, coll = PROVENANCE_COLLECTION
    return await mongo[db][coll].find(
        {"namespace": namespace}, {"_id": 0}
    ).sort([("at", 1), ("_id", 1)]).to_list(length=None)


def _reader(entities=None, *, provenance=None, raw=None):
    """A stand-in ArchiveReader carrying one namespace."""
    entities = entities or {}
    manifest = Manifest(
        format_version="3.1",
        source_host="source-host",
        exported_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        namespaces=[NamespaceEntry(
            prefix=NAMESPACE,
            namespace_config=NamespaceConfig(prefix=NAMESPACE),
            counts=EntityCounts(),
            provenance=provenance,
        )],
        namespace=NAMESPACE,
        namespace_config=NamespaceConfig(prefix=NAMESPACE),
    )
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=manifest)
    reader.read_manifest_raw = MagicMock(return_value=raw if raw is not None else {})
    reader.list_namespaces = MagicMock(return_value=[NAMESPACE])
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


def _http():
    http = MagicMock()
    http.put = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    return http


async def _restore(mongo, reader, archive_path, **engine_kw):
    engine = DirectRestoreEngine(mongo, None, lambda _e: None, **engine_kw)
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=reader
    ), patch("httpx.AsyncClient", return_value=_http()):
        # A hand-built archive carries no registry rows.
        await engine.run_restore(archive_path, NAMESPACE, allow_missing_identity=True)


TERMINOLOGY = {"terminology_id": "T942", "namespace": NAMESPACE, "value": "KIND", "label": "Kind"}


class TestIdPreservingRestore:
    @pytest.mark.asyncio
    async def test_writes_one_structured_record(self, mongo, tmp_path):
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"archive bytes")

        await _restore(
            mongo, _reader({"terminologies": [TERMINOLOGY]}, raw={"derived_from": [HOP]}),
            archive, job_id="job-942", actor="apikey:tester",
        )

        [record] = await _records(mongo, NAMESPACE)
        assert record["mode"] == "restore"
        assert record["restore_job_id"] == "job-942"
        assert record["actor"] == "apikey:tester"
        assert record["restored_into"] == NAMESPACE
        assert record["inherited"] is False
        assert record["archive"]["sha256"] == hashlib.sha256(b"archive bytes").hexdigest()
        assert record["archive"]["format_version"] == "3.1"
        assert record["source"]["namespace"] == NAMESPACE
        assert record["source"]["host"] == "source-host"
        assert record["source"]["exported_at"].startswith("2026-10-01T12:00")
        # The chain is copied verbatim, keys it does not know included.
        assert record["derived_from"] == [HOP]
        assert record["merge"] is None

    @pytest.mark.asyncio
    async def test_the_restored_entities_stay_exact_copies(self, mongo, tmp_path):
        # The record is a row about the restore; no restored entity gains a
        # stamp, so the exact-copy contract holds.
        await _restore(mongo, _reader({"terminologies": [TERMINOLOGY]}), tmp_path / "missing.zip")

        db_name, coll_name = COLLECTION_MAP["terminologies"]
        rows = await mongo[db_name][coll_name].find(
            {"namespace": NAMESPACE}, {"_id": 0}
        ).to_list(length=None)
        assert rows == [TERMINOLOGY]

    @pytest.mark.asyncio
    async def test_a_single_hop_object_is_a_one_hop_chain(self, mongo, tmp_path):
        await _restore(mongo, _reader(raw={"derived_from": HOP}), tmp_path / "x.zip")

        [record] = await _records(mongo, NAMESPACE)
        assert record["derived_from"] == [HOP]
        # No archive file on disk: the digest is absent, not invented.
        assert record["archive"]["sha256"] is None

    @pytest.mark.asyncio
    async def test_archived_records_are_inherited_before_the_new_one(self, mongo, tmp_path):
        await _restore(mongo, _reader(provenance=[EARLIER]), tmp_path / "x.zip", job_id="job-new")

        inherited, own = await _records(mongo, NAMESPACE)
        assert inherited["inherited"] is True
        assert inherited["restore_job_id"] == "job-earlier"
        # The record belongs to this namespace now, but still says where its
        # restore happened.
        assert inherited["namespace"] == NAMESPACE
        assert inherited["restored_into"] == "prov-942-original"
        assert isinstance(inherited["at"], datetime)
        assert own["restore_job_id"] == "job-new"
        assert own["inherited"] is False


class TestRemapRestore:
    @pytest.mark.asyncio
    async def test_writes_a_record_on_the_target(self, mongo):
        await _clear_remap_target(mongo)
        await mongo[_DB_REGISTRY]["namespaces"].insert_one(
            {"prefix": REMAP_TARGET, "description": "", "isolation_mode": "open"}
        )
        reader = _reader({"terminologies": [TERMINOLOGY]})

        await _run_remap(mongo, reader)

        [record] = await _records(mongo, REMAP_TARGET)
        assert record["mode"] == "remap"
        assert record["restored_into"] == REMAP_TARGET
        assert record["source"]["namespace"] == NAMESPACE
        # The human-readable echo in the description stays.
        live = await mongo[_DB_REGISTRY]["namespaces"].find_one({"prefix": REMAP_TARGET})
        assert f"of '{NAMESPACE}'" in live["description"]
        await _clear_remap_target(mongo)


class TestMerge:
    @pytest.mark.asyncio
    async def test_records_what_arrived_and_the_policy(self, mongo, tmp_path):
        await mongo[_DB_REGISTRY]["namespaces"].insert_one(
            {"prefix": NAMESPACE, "isolation_mode": "open"}
        )
        try:
            engine = DirectRestoreEngine(mongo, None, lambda _e: None, job_id="job-merge")
            with patch(
                "document_store.services.backup_engine.ArchiveReader",
                return_value=_reader({"terminologies": [TERMINOLOGY]}),
            ):
                await engine.run_merge(
                    tmp_path / "m.zip", NAMESPACE, on_clash="overwrite", add_missing=True
                )
        finally:
            await mongo[_DB_REGISTRY]["namespaces"].delete_many({"prefix": NAMESPACE})

        [record] = await _records(mongo, NAMESPACE)
        assert record["mode"] == "merge"
        assert record["merge"]["on_clash"] == "overwrite"
        assert record["merge"]["add_missing"] is True
        assert "definitions" in record["merge"]
        assert record["restore_job_id"] == "job-merge"

    @pytest.mark.asyncio
    async def test_a_record_the_target_already_holds_is_not_copied_again(self, mongo, tmp_path):
        # Merging a backup of a namespace back into itself brings the
        # namespace's own history along; it must not duplicate.
        db, coll = PROVENANCE_COLLECTION
        held = dict(EARLIER, namespace=NAMESPACE)
        await mongo[db][coll].insert_one(dict(held))
        await mongo[_DB_REGISTRY]["namespaces"].insert_one(
            {"prefix": NAMESPACE, "isolation_mode": "open"}
        )
        try:
            engine = DirectRestoreEngine(mongo, None, lambda _e: None, job_id="job-merge-2")
            with patch(
                "document_store.services.backup_engine.ArchiveReader",
                return_value=_reader(provenance=[held]),
            ):
                await engine.run_merge(tmp_path / "m.zip", NAMESPACE)
        finally:
            await mongo[_DB_REGISTRY]["namespaces"].delete_many({"prefix": NAMESPACE})

        records = await _records(mongo, NAMESPACE)
        assert [r["restore_job_id"] for r in records] == ["job-earlier", "job-merge-2"]


class TestBackupCarriesProvenance:
    @pytest.mark.asyncio
    async def test_read_provenance_returns_the_namespace_history(self, mongo):
        db, coll = PROVENANCE_COLLECTION
        await mongo[db][coll].insert_one(dict(EARLIER, namespace=NAMESPACE))
        engine = DirectBackupEngine(mongo, None, lambda _e: None)

        rows = await engine._read_provenance(NAMESPACE)

        assert [r["restore_job_id"] for r in rows] == ["job-earlier"]
        assert "_id" not in rows[0]

    @pytest.mark.asyncio
    async def test_a_namespace_without_history_archives_none(self, mongo):
        engine = DirectBackupEngine(mongo, None, lambda _e: None)
        assert await engine._read_provenance(NAMESPACE) is None

    def test_the_manifest_field_round_trips_and_old_manifests_parse(self):
        entry = NamespaceEntry(prefix=NAMESPACE, provenance=[EARLIER])
        assert NamespaceEntry.model_validate_json(entry.model_dump_json()).provenance == [EARLIER]
        assert NamespaceEntry.model_validate({"prefix": NAMESPACE}).provenance is None


@pytest.mark.asyncio
async def test_the_validation_job_is_linked_from_the_record(mongo):
    job = BackupJob(
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        kind=BackupJobKind.RESTORE,
        namespace=NAMESPACE,
        namespaces=[NAMESPACE],
        created_by="test-admin",
    )
    await job.insert()
    db, coll = PROVENANCE_COLLECTION
    await mongo[db][coll].insert_many([
        dict(EARLIER, namespace=NAMESPACE, inherited=True),
        dict(EARLIER, namespace=NAMESPACE, restore_job_id=job.job_id,
             validation_job_id=None, inherited=False),
    ])

    with patch.object(backup_service, "start_async_job", new=AsyncMock()):
        [validation_job_id] = await backup_service.trigger_validation_for(job)

    records = {r["restore_job_id"]: r for r in await _records(mongo, NAMESPACE)}
    assert records[job.job_id]["validation_job_id"] == validation_job_id
    # An inherited record keeps the link its own restore made.
    assert records["job-earlier"]["validation_job_id"] == "val-earlier"
    await job.delete()


@pytest.mark.asyncio
async def test_the_api_serves_the_history_oldest_first(client, auth_headers):
    await RestoreProvenance.find(RestoreProvenance.namespace == "wip").delete()
    for i, at in enumerate(["2026-10-02T00:00:00+00:00", "2026-10-01T00:00:00+00:00"]):
        await RestoreProvenance(
            namespace="wip", restored_into="wip", restore_job_id=f"job-{i}",
            mode="restore", at=datetime.fromisoformat(at),
        ).insert()
    try:
        resp = await client.get(
            "/api/document-store/backup/provenance",
            params={"namespace": "wip"}, headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert body["pages"] == 1
        assert [i["restore_job_id"] for i in body["items"]] == ["job-1", "job-0"]

        empty = await client.get(
            "/api/document-store/backup/provenance",
            params={"namespace": "wip", "page": 2, "page_size": 2},
            headers=auth_headers,
        )
        assert empty.json()["items"] == []
    finally:
        await RestoreProvenance.find(RestoreProvenance.namespace == "wip").delete()
