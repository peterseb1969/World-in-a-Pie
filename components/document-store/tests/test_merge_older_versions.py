"""What a merge does to the target's older versions when it appends one.

``on_clash`` 'overwrite' and 'newer' append the archive's latest version on
top of the target's head. ``older_versions`` decides the fate of the versions
underneath: 'retire' (the default) soft-deletes them, exactly as a normal
write retires the version it supersedes, so the document has one active
version; 'keep' leaves them active, so it has several.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import RestoreEngineError
from tests.test_merge_restore import (
    NAMESPACE,
    _all_collections,
    _archive,
    _document,
    _rows,
    _run_merge,
    _seed,
    _seed_namespace,
    _template,
)


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    """A real client, with this suite's namespace cleared before and after."""
    # The engine's restore tail reads through Beanie models; bind them to the
    # session client so this file passes on its own.
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


OLD = "2026-10-01T00:00:00+00:00"
NEW = "2026-10-20T00:00:00+00:00"


def _doc(version: int, when: str, data: dict, *, status: str = "active") -> dict:
    return {**_document(version, data=data), "updated_at": when, "status": status}


async def _seed_target(mongo, *, versioned: bool = True):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template(versioned=versioned)])
    await _seed(mongo, "documents", [_doc(1, OLD, {"n": 1})])


def _newer_archive(*, versioned: bool = True):
    # The archive's own v1 is superseded, as a normal write leaves it.
    return _archive({
        "templates": [_template(versioned=versioned)],
        "documents": [
            _doc(1, OLD, {"n": 1}, status="inactive"),
            _doc(2, NEW, {"n": 2}),
        ],
    })


async def _versions(mongo):
    rows = await _rows(mongo, "documents", sort_key=lambda d: d["version"])
    return [(d["version"], d["status"], d["data"]) for d in rows]


class TestOlderVersions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("on_clash", ["overwrite", "newer"])
    async def test_default_retires_the_target_older_version(self, mongo, on_clash):
        await _seed_target(mongo)

        await _run_merge(mongo, _newer_archive(), on_clash=on_clash)

        assert await _versions(mongo) == [
            (1, "inactive", {"n": 1}),
            (2, "active", {"n": 2}),
        ]
        retired = (await _rows(mongo, "documents", sort_key=lambda d: d["version"]))[0]
        assert retired["updated_by"] == "restore-merge"

    @pytest.mark.asyncio
    async def test_keep_leaves_both_versions_active(self, mongo):
        await _seed_target(mongo)

        await _run_merge(
            mongo, _newer_archive(), on_clash="overwrite", older_versions="keep"
        )

        assert await _versions(mongo) == [
            (1, "active", {"n": 1}),
            (2, "active", {"n": 2}),
        ]

    @pytest.mark.asyncio
    async def test_every_active_older_version_is_retired(self, mongo):
        # A target an earlier 'keep' merge left with two active versions.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_doc(1, OLD, {"n": 1}), _doc(2, OLD, {"n": 5})])
        archive = _archive({
            "templates": [_template()],
            "documents": [_doc(1, NEW, {"n": 9})],
        })

        await _run_merge(mongo, archive, on_clash="overwrite")

        assert [(v, s) for v, s, _ in await _versions(mongo)] == [
            (1, "inactive"), (2, "inactive"), (3, "active"),
        ]

    @pytest.mark.asyncio
    async def test_every_written_batch_retires_what_it_supersedes(self, mongo):
        # batch_size=1 flushes inside the loop; the earlier batches must be
        # retired too, not only the last.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        target = []
        archive_docs = []
        for doc_id in ("D1", "D2", "D3"):
            old = {**_doc(1, OLD, {"n": 1}), "document_id": doc_id, "identity_hash": doc_id}
            new = {**_doc(1, NEW, {"n": 2}), "document_id": doc_id, "identity_hash": doc_id}
            target.append(old)
            archive_docs.append(new)
        await _seed(mongo, "documents", target)
        archive = _archive({"templates": [_template()], "documents": archive_docs})

        await _run_merge(mongo, archive, on_clash="overwrite", batch_size=1)

        rows = await _rows(mongo, "documents", sort_key=lambda d: (d["document_id"], d["version"]))
        assert [(d["document_id"], d["version"], d["status"]) for d in rows] == [
            ("D1", 1, "inactive"), ("D1", 2, "active"),
            ("D2", 1, "inactive"), ("D2", 2, "active"),
            ("D3", 1, "inactive"), ("D3", 2, "active"),
        ]

    @pytest.mark.asyncio
    async def test_a_kept_target_retires_nothing(self, mongo):
        # 'newer' keeps the target when its copy is not older: nothing is
        # appended, so nothing is retired.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_doc(1, NEW, {"n": 1})])
        archive = _archive({
            "templates": [_template()],
            "documents": [_doc(1, OLD, {"n": 7})],
        })

        await _run_merge(mongo, archive, on_clash="newer")

        assert await _versions(mongo) == [(1, "active", {"n": 1})]

    @pytest.mark.asyncio
    async def test_versioned_false_replaces_in_place(self, mongo):
        # One version only: the replaced row stays the active one.
        await _seed_target(mongo, versioned=False)
        archive = _archive({
            "templates": [_template(versioned=False)],
            "documents": [_doc(1, NEW, {"n": 2})],
        })

        await _run_merge(mongo, archive, on_clash="overwrite")

        assert await _versions(mongo) == [(1, "active", {"n": 2})]

    @pytest.mark.asyncio
    async def test_an_unknown_value_is_refused(self, mongo):
        await _seed_target(mongo)

        with pytest.raises(RestoreEngineError, match="older_versions"):
            await _run_merge(
                mongo, _newer_archive(), on_clash="overwrite", older_versions="drop"
            )
