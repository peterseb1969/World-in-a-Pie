"""A merge redirects every namespace of a multi-namespace archive (CASE-1299).

``namespace_map`` was fresh's parameter. A merge could redirect only the one
namespace of a single-namespace archive, through ``target_namespace``; with two
namespaces in the archive the request was refused, so folding ``kb`` +
``library`` into ``kb2`` + ``library2`` meant splitting the archive and merging
each part, or a fresh restore, which re-mints the ids the caller wanted kept.

An accepted parameter is not the feature. These pin what a redirected merge has
to hold: every pair planned and checked before anything is written, the arriving
rows and the references between them carrying the target's name (a reference
left naming ``library`` points at a namespace this install does not have), and
the job record naming the namespaces it WRITES — post-restore sync and
validation derive their scope from it — beside the sources its input holds.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, NamespaceEntry

from document_store.models.backup_job import BackupJob, BackupJobKind, BackupJobStatus
from document_store.services.backup_engine import (
    _DB_REGISTRY,
    CLAIMS_COLLECTION,
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)
from tests.test_case_748 import _make_v3_archive, _post_merge

SRC_A, SRC_B = "c1299-kb", "c1299-library"
TGT_A, TGT_B = "c1299-kb2", "c1299-library2"
ALL_NS = [SRC_A, SRC_B, TGT_A, TGT_B]
MAP = {SRC_A: TGT_A, SRC_B: TGT_B}


# --- engine: pairs, refusals, and what lands ----------------------------------


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in [*COLLECTION_MAP.values(), CLAIMS_COLLECTION, (_DB_REGISTRY, "namespaces")]:
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: {"$in": ALL_NS}})

    await _clear()
    for ns in ALL_NS:
        await client[_DB_REGISTRY]["namespaces"].insert_one({"prefix": ns, "isolation_mode": "open"})
    yield client
    await _clear()
    client.close()


def _template(ns: str, tid: str) -> dict:
    return {
        "template_id": tid, "namespace": ns, "value": "NOTE", "label": "Note",
        "version": 1, "fields": [{"name": "n", "type": "integer"}],
    }


def _doc(ns: str, doc_id: str, tid: str, *, refers_to: tuple[str, str] | None = None) -> dict:
    row = {
        "document_id": doc_id, "namespace": ns, "template_id": tid,
        "template_version": 1, "identity_hash": f"h-{doc_id}", "version": 1,
        "data": {"n": 1}, "status": "active",
    }
    if refers_to:
        refers_id, refers_ns = refers_to
        row["references"] = [
            {"field_path": "other", "resolved": {"document_id": refers_id, "namespace": refers_ns}}
        ]
    return row


def _archive(per_namespace: dict[str, dict[str, list[dict]]]) -> MagicMock:
    """A stand-in reader for a v3 archive carrying ``per_namespace``."""
    manifest = Manifest(
        format_version="3.0",
        namespaces=[
            NamespaceEntry(
                prefix=ns,
                namespace_config=NamespaceConfig(
                    namespace_id="019a0000-0000-7000-8000-0000000c1084",
                    prefix=ns, isolation_mode="open",
                ),
                counts=EntityCounts(),
            )
            for ns in per_namespace
        ],
        counts=EntityCounts(),
    )
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=manifest)
    reader.read_manifest_raw = MagicMock(return_value={})
    reader.list_namespaces = MagicMock(return_value=list(per_namespace))
    reader.list_blobs = MagicMock(return_value=[])
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [
            dict(e) for e in per_namespace.get(namespace, {}).get(et, [])
        ]
    )
    reader.read_referents = MagicMock(return_value=[])
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=None)
    return reader


def _cross_namespace_archive() -> MagicMock:
    """Each namespace's document references the other's."""
    return _archive({
        SRC_A: {
            "templates": [_template(SRC_A, "TA")],
            "documents": [_doc(SRC_A, "DA", "TA", refers_to=("DB", SRC_B))],
        },
        SRC_B: {
            "templates": [_template(SRC_B, "TB")],
            "documents": [_doc(SRC_B, "DB", "TB", refers_to=("DA", SRC_A))],
        },
    })


async def _merge(mongo, reader, **kwargs) -> None:
    engine = DirectRestoreEngine(mongo, None, lambda event: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=reader):
        await engine.run_merge(MagicMock(), "", add_missing=True, **kwargs)


async def _rows(mongo, ns: str) -> list[dict]:
    db_name, coll_name = COLLECTION_MAP["documents"]
    return await mongo[db_name][coll_name].find(
        {"namespace": ns}, {"document_id": 1, "namespace": 1, "references": 1}
    ).to_list(None)


class TestEveryPairIsWritten:
    @pytest.mark.asyncio
    async def test_two_namespaces_land_in_their_own_targets(self, mongo):
        await _merge(mongo, _cross_namespace_archive(), namespace_map=MAP)

        assert [r["document_id"] for r in await _rows(mongo, TGT_A)] == ["DA"]
        assert [r["document_id"] for r in await _rows(mongo, TGT_B)] == ["DB"]
        assert await _rows(mongo, SRC_A) == [] and await _rows(mongo, SRC_B) == []

    @pytest.mark.asyncio
    async def test_a_reference_into_a_moved_namespace_follows_it_to_its_target(
        self, mongo,
    ):
        """The pairwise rewrite would leave DA naming 'library', which this
        install does not have: every moved namespace's name has to be
        rewritten in the same pass, whoever the row came from."""
        await _merge(mongo, _cross_namespace_archive(), namespace_map=MAP)

        (da,) = await _rows(mongo, TGT_A)
        (db,) = await _rows(mongo, TGT_B)
        assert da["references"][0]["resolved"]["namespace"] == TGT_B
        assert db["references"][0]["resolved"]["namespace"] == TGT_A

    @pytest.mark.asyncio
    async def test_a_later_pair_refusing_leaves_the_earlier_one_untouched(
        self, mongo,
    ):
        """As for a self-merge: nothing is written until every pair planned
        and passed its gates."""
        reader = _archive({
            SRC_A: {"templates": [_template(SRC_A, "TA")], "documents": [_doc(SRC_A, "DA", "TA")]},
            SRC_B: {
                "templates": [_template(SRC_B, "TB")],
                "documents": [_doc(SRC_B, "DB", "TB", refers_to=("NOPE", SRC_B))],
            },
        })
        with pytest.raises(RestoreEngineError):
            await _merge(mongo, reader, namespace_map=MAP)
        assert await _rows(mongo, TGT_A) == [] and await _rows(mongo, TGT_B) == []

    @pytest.mark.asyncio
    async def test_a_target_that_does_not_exist_refuses(self, mongo):
        """A merge never creates a namespace, and one pair's missing target
        stops the other pair too: the targets are all checked up front."""
        reader = _archive({
            SRC_A: {"templates": [_template(SRC_A, "TA")], "documents": [_doc(SRC_A, "DA", "TA")]},
            SRC_B: {"templates": [_template(SRC_B, "TB")], "documents": [_doc(SRC_B, "DB", "TB")]},
        })
        with pytest.raises(RestoreEngineError, match="does not exist"):
            await _merge(mongo, reader, namespace_map={SRC_A: TGT_A, SRC_B: "c1299-absent"})
        assert await _rows(mongo, TGT_A) == []


class TestTheMapIsCheckedBeforeThePlan:
    def _engine(self) -> DirectRestoreEngine:
        return DirectRestoreEngine(MagicMock(), None, lambda _e: None)

    def _targets(
        self, sources: list[str], *, target_namespace: str = "", **kwargs: Any
    ) -> list[tuple[str, str]]:
        reader = MagicMock()
        reader.list_namespaces = MagicMock(return_value=sources)
        manifest = MagicMock()
        manifest.namespace_prefixes = MagicMock(return_value=sources)
        return self._engine()._resolve_targets(
            reader, manifest, target_namespace, **kwargs
        )

    def test_each_namespace_pairs_with_its_own_target(self):
        assert self._targets(
            [SRC_A, SRC_B], allow_redirect=True, namespace_map=MAP
        ) == [(SRC_A, TGT_A), (SRC_B, TGT_B)]

    def test_pairs_keep_archive_order_not_caller_order(self):
        assert self._targets(
            [SRC_B, SRC_A], allow_redirect=True,
            namespace_map={SRC_A: TGT_A, SRC_B: TGT_B},
        ) == [(SRC_B, TGT_B), (SRC_A, TGT_A)]

    def test_two_sources_sharing_a_target_are_refused(self):
        with pytest.raises(RestoreEngineError, match="same target"):
            self._targets(
                [SRC_A, SRC_B], allow_redirect=True,
                namespace_map={SRC_A: "one", SRC_B: "one"},
            )

    @pytest.mark.parametrize(
        "namespace_map, phrase",
        [
            ({SRC_A: TGT_A}, "unmapped archive namespace"),
            ({SRC_A: TGT_A, SRC_B: TGT_B, "c1299-stranger": "x"}, "not in the archive"),
        ],
    )
    def test_a_map_that_does_not_cover_the_archive_exactly_is_refused(
        self, namespace_map, phrase
    ):
        with pytest.raises(RestoreEngineError, match=phrase):
            self._targets(
                [SRC_A, SRC_B], allow_redirect=True, namespace_map=namespace_map
            )

    def test_a_map_and_a_target_namespace_compete(self):
        with pytest.raises(RestoreEngineError, match="one or the other"):
            self._targets(
                [SRC_A], allow_redirect=True, target_namespace=TGT_A,
                namespace_map={SRC_A: TGT_A},
            )

    def test_a_mode_that_keeps_ids_cannot_be_mapped(self):
        with pytest.raises(RestoreEngineError, match="id-preserving"):
            self._targets([SRC_A, SRC_B], namespace_map=MAP)

    def test_a_map_is_not_needed_when_the_targets_are_the_sources(self):
        assert self._targets([SRC_A, SRC_B], allow_redirect=True) == [
            (SRC_A, SRC_A), (SRC_B, SRC_B)
        ]


class TestTheRewriteTakesTheWholeMap:
    def test_a_row_is_renamed_out_of_every_namespace_the_merge_moves(self):
        engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        entity = {
            "namespace": SRC_A,
            "primary_composite_key": {"ns": SRC_B, "value": "X"},
            "synonyms": [{"namespace": SRC_B, "composite_key": {"ns": SRC_A, "ext": "1"}}],
        }
        moved = engine._rewrite_namespace("registry_entries", entity, MAP)

        assert moved["namespace"] == TGT_A
        assert moved["primary_composite_key"]["ns"] == TGT_B
        assert moved["synonyms"][0]["namespace"] == TGT_B
        assert moved["synonyms"][0]["composite_key"]["ns"] == TGT_A
        # Rehashing the rewritten key is what makes the claim name the target.
        assert moved["primary_composite_key_hash"] != entity.get(
            "primary_composite_key_hash"
        )

    def test_a_namespace_the_merge_leaves_alone_keeps_its_own(self):
        engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        kept = engine._rewrite_namespace(
            "registry_entries",
            {"namespace": "wip", "primary_composite_key": {"ns": "wip"}},
            MAP,
        )
        assert kept["namespace"] == "wip"
        assert kept["primary_composite_key"]["ns"] == "wip"


# --- the upload route: the map is merge's, and the job says where it went ---


@pytest.mark.asyncio
async def test_a_mapped_merge_is_accepted_and_records_its_targets(
    client, auth_headers: dict, tmp_path,
):
    archive = _make_v3_archive(tmp_path, [SRC_A, SRC_B])

    resp = await _post_merge(
        client, auth_headers, archive, {"namespace_map": json.dumps(MAP)}
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["options"]["namespace_map"] == {SRC_A: TGT_A, SRC_B: TGT_B}
    # Sync and validation scope from the job record: it names the namespaces
    # the merge writes, and the sources its retained input still holds.
    assert body["namespaces"] == [TGT_A, TGT_B]
    assert body["source_namespaces"] == [SRC_A, SRC_B]

    stored = await BackupJob.find_one(BackupJob.job_id == body["job_id"])
    assert stored is not None
    assert stored.namespaces == [TGT_A, TGT_B]
    assert stored.source_namespaces == [SRC_A, SRC_B]


@pytest.mark.parametrize(
    ("namespace_map", "phrase"),
    [
        (json.dumps({SRC_A: "one", SRC_B: "one"}), "same target"),
        (json.dumps({SRC_A: TGT_A}), "unmapped archive namespace"),
        (json.dumps({"c1299-stranger": "x"}), "not in the archive"),
    ],
)
async def test_a_map_the_archive_cannot_answer_is_a_400_before_a_job_exists(
    client, auth_headers: dict, tmp_path, namespace_map, phrase,
):
    archive = _make_v3_archive(tmp_path, [SRC_A, SRC_B])

    resp = await _post_merge(
        client, auth_headers, archive, {"namespace_map": namespace_map}
    )

    assert resp.status_code == 400, resp.text
    assert phrase in resp.json()["detail"]
    assert await BackupJob.find_one() is None


async def test_a_target_namespace_and_a_map_compete(client, auth_headers, tmp_path):
    archive = _make_v3_archive(tmp_path, [SRC_A])

    resp = await _post_merge(
        client, auth_headers, archive,
        {"target_namespace": TGT_A, "namespace_map": json.dumps({SRC_A: TGT_A})},
    )

    assert resp.status_code == 400, resp.text
    assert "one or the other" in resp.json()["detail"]
    assert await BackupJob.find_one() is None


@pytest.mark.asyncio
async def test_an_unmapped_merge_still_writes_the_archives_own_namespaces(
    client, auth_headers: dict, tmp_path,
):
    archive = _make_v3_archive(tmp_path, [SRC_A, SRC_B])

    resp = await _post_merge(client, auth_headers, archive, {})

    assert resp.status_code == 202, resp.text
    assert resp.json()["namespaces"] == [SRC_A, SRC_B]
    assert resp.json()["source_namespaces"] == [SRC_A, SRC_B]


# --- restore-from-job: the same redirect, without re-uploading ---------------


@pytest.mark.asyncio
async def test_a_mapped_merge_from_a_retained_archive_records_its_targets(
    client, auth_headers: dict, tmp_path,
):
    from document_store.services.archive_store import scratch_path_for

    src = BackupJob(
        job_id=f"bkp-{uuid.uuid4().hex[:12]}", kind=BackupJobKind.BACKUP,
        namespace=SRC_A, namespaces=[SRC_A, SRC_B], status=BackupJobStatus.COMPLETE,
        archive_path=None, created_by="test",
    )
    await src.insert()
    scratch = scratch_path_for(src.job_id)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(_make_v3_archive(tmp_path, [SRC_A, SRC_B]).read_bytes())
    src.archive_path = str(scratch)
    await src.save()

    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    with patch(
        "document_store.api.backup.backup_service.make_direct_restore_runner",
        return_value=AsyncMock(),
    ), patch(
        "document_store.api.backup.backup_service.start_async_job",
        new=AsyncMock(return_value=done),
    ):
        resp = await client.post(
            f"/api/document-store/backup/jobs/{src.job_id}/restore",
            headers=auth_headers,
            json={"mode": "merge", "namespace_map": MAP},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["namespaces"] == [TGT_A, TGT_B]
    assert body["source_namespaces"] == [SRC_A, SRC_B]
    assert body["options"]["namespace_map"] == MAP
    assert body["options"]["restored_from_job"] == src.job_id
