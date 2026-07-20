"""Merge-mode restore (restore modes, Phases 1 and 2).

Merge takes an archive as a delta against a namespace that already holds data.
Phase 1 is the same-install case (IDs preserved); Phase 2 adds the
cross-install case, where the two sides never shared an ID space.
These tests run the real engine against the real test MongoDB, so each case
asserts the namespace's actual end state — the difference matters for a mode
whose whole job is deciding what to write, and a mocked collection would
happily accept a query that matches nothing in practice.

Only the archive is stubbed (the engine's ZIP reader), since building real
archives would test wip_toolkit's writer rather than the merge. The reporting
client is left out (None), so the reporting phases degrade to no-ops exactly
as they do on a core-preset deploy.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_toolkit.models import (
    EntityCounts,
    Manifest,
    NamespaceConfig,
    NamespaceEntry,
    ProgressEvent,
)

from document_store.services.backup_engine import (
    _DB_REGISTRY,
    CLAIMS_COLLECTION,
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)

# A namespace of this suite's own, so a merge test never sees (or deletes)
# another suite's rows in the shared test databases.
NAMESPACE = "merge-test-ns"

NAMESPACES_COLLECTION = (_DB_REGISTRY, "namespaces")


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


def _all_collections():
    return [*COLLECTION_MAP.values(), CLAIMS_COLLECTION, NAMESPACES_COLLECTION]


@pytest_asyncio.fixture
async def mongo():
    """A real client, with this namespace's rows cleared before and after."""
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    )

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


async def _seed(mongo, entity_type, rows):
    if not rows:
        return
    db_name, coll_name = COLLECTION_MAP[entity_type]
    await mongo[db_name][coll_name].insert_many([dict(r) for r in rows])


async def _seed_namespace(mongo, **config):
    db_name, coll_name = NAMESPACES_COLLECTION
    await mongo[db_name][coll_name].insert_one(
        {"prefix": NAMESPACE, "isolation_mode": "open", **config}
    )


async def _rows(mongo, entity_type, *, sort_key=None):
    db_name, coll_name = COLLECTION_MAP[entity_type]
    found = await mongo[db_name][coll_name].find(
        {"namespace": NAMESPACE}, {"_id": 0}
    ).to_list(length=None)
    return sorted(found, key=sort_key) if sort_key else found


async def _claims(mongo):
    db_name, coll_name = CLAIMS_COLLECTION
    return await mongo[db_name][coll_name].find(
        {"namespace": NAMESPACE}, {"_id": 0}
    ).to_list(length=None)


# ---------------------------------------------------------------------------
# Archive + engine helpers
# ---------------------------------------------------------------------------


def _archive(entities=None, *, blobs=(), ns_config=None):
    """A stand-in ArchiveReader over the given per-type entity lists."""
    entities = entities or {}
    manifest = Manifest(
        format_version="3.0",
        namespace=NAMESPACE,
        namespace_config=ns_config
        or NamespaceConfig(prefix=NAMESPACE, isolation_mode="open"),
        counts=EntityCounts(),
    )
    reader = MagicMock()
    reader.read_manifest = MagicMock(return_value=manifest)
    reader.list_namespaces = MagicMock(return_value=[NAMESPACE])
    reader.list_blobs = MagicMock(return_value=list(blobs))
    reader.read_blob = MagicMock(return_value=b"blob-bytes")
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [
            dict(e) for e in entities.get(et, [])
        ]
    )
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=None)
    return reader


async def _run_merge(mongo, reader, events=None, *, storage=None, **kwargs):
    def callback(event: ProgressEvent) -> None:
        if events is not None:
            events.append(event)

    engine = DirectRestoreEngine(mongo, storage, callback)
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=reader
    ):
        await engine.run_merge(MagicMock(), NAMESPACE, **kwargs)


def _document(version, *, doc_id="D1", data=None, template="TPL"):
    return {
        "document_id": doc_id,
        "namespace": NAMESPACE,
        "template_id": template,
        "template_version": 1,
        "identity_hash": "h1",
        "version": version,
        "data": data or {"n": version},
        "status": "active",
    }


def _template(version=1, *, label="Patient", versioned=True):
    return {
        "template_id": "TPL",
        "namespace": NAMESPACE,
        "value": "PATIENT",
        "version": version,
        "label": label,
        "versioned": versioned,
        "fields": [{"name": "age", "type": "integer"}],
    }


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


class TestMergePreconditions:
    @pytest.mark.asyncio
    async def test_missing_namespace_is_refused(self, mongo):
        # Merge inverts the plain restore's precondition: there must be
        # something to merge into. Creating the namespace would be a restore.
        with pytest.raises(RestoreEngineError, match="does not exist"):
            await _run_merge(mongo, _archive())

    @pytest.mark.asyncio
    async def test_non_empty_namespace_is_fine(self, mongo):
        # The exact condition a plain restore refuses on.
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"},
        ])

        await _run_merge(mongo, _archive())

        assert len(await _rows(mongo, "terminologies")) == 1

    @pytest.mark.asyncio
    async def test_unknown_clash_policy_is_refused(self, mongo):
        await _seed_namespace(mongo)
        with pytest.raises(RestoreEngineError, match="Invalid on_clash"):
            await _run_merge(mongo, _archive(), on_clash="clobber")

    @pytest.mark.asyncio
    async def test_namespace_config_drift_is_reported_not_applied(self, mongo):
        # The target namespace is live; its configuration belongs to whoever
        # runs it, not to an archive taken earlier. Naming the drift still
        # matters — isolation_mode changes how merged data behaves.
        await _seed_namespace(mongo, isolation_mode="strict")
        events: list[ProgressEvent] = []

        await _run_merge(
            mongo,
            _archive(ns_config=NamespaceConfig(
                prefix=NAMESPACE, isolation_mode="open"
            )),
            events,
        )

        warning = next(e for e in events if e.phase == "warning")
        assert "isolation_mode" in warning.message
        live = await mongo[_DB_REGISTRY]["namespaces"].find_one({"prefix": NAMESPACE})
        assert live["isolation_mode"] == "strict"


# ---------------------------------------------------------------------------
# Inserting what the target lacks
# ---------------------------------------------------------------------------


class TestMergeInserts:
    @pytest.mark.asyncio
    async def test_entities_absent_from_the_target_are_inserted(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"},
        ])

        await _run_merge(
            mongo,
            _archive({"terminologies": [
                {"terminology_id": "T2", "namespace": NAMESPACE,
                 "value": "COUNTRY"},
            ]}),
            add_missing=True,
        )

        values = sorted(r["value"] for r in await _rows(mongo, "terminologies"))
        assert values == ["COUNTRY", "GENDER"]

    @pytest.mark.asyncio
    async def test_an_inserted_document_brings_its_whole_version_chain(self, mongo):
        # Planning happens at the logical level (one archived document is a
        # chain of version rows), but inserting must not drop the history.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        chain = [_document(v, data={"n": v}) for v in (1, 2, 3)]

        await _run_merge(
            mongo, _archive({"templates": [_template()], "documents": chain})
        )

        docs = await _rows(mongo, "documents")
        assert sorted(d["version"] for d in docs) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_claims_are_rebuilt_for_inserted_entries_only(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "registry_entries", [
            {"entry_id": "E-OLD", "namespace": NAMESPACE,
             "entity_type": "terminologies",
             "primary_composite_key_hash": "hash-old", "synonyms": []},
        ])
        db_name, coll_name = CLAIMS_COLLECTION
        await mongo[db_name][coll_name].insert_one({
            "namespace": NAMESPACE, "entity_type": "terminologies",
            "composite_key_hash": "hash-old", "owner_entry_id": "E-OLD",
            "kind": "primary", "state": "confirmed",
        })

        await _run_merge(mongo, _archive({
            "registry_entries": [
                {"entry_id": "E-NEW", "namespace": NAMESPACE,
                 "entity_type": "terminologies",
                 "primary_composite_key_hash": "hash-new", "synonyms": []},
            ],
        }))

        # The pre-existing claim is untouched and the new entry gained one.
        owners = sorted(c["owner_entry_id"] for c in await _claims(mongo))
        assert owners == ["E-NEW", "E-OLD"]

    @pytest.mark.asyncio
    async def test_only_inserted_files_get_their_blobs_uploaded(self, mongo):
        # A file the target already had keeps its bytes; re-uploading them
        # would be needless work and, on a checksum match under a different
        # id, an orphan object.
        await _seed_namespace(mongo)
        await _seed(mongo, "files", [
            {"file_id": "F-EXISTING", "namespace": NAMESPACE, "checksum": "c1"},
        ])
        uploaded: list[str] = []
        storage = MagicMock()

        async def _upload(storage_key, content, content_type):
            uploaded.append(storage_key)

        storage.upload = _upload

        await _run_merge(
            mongo,
            _archive(
                {"files": [
                    {"file_id": "F-EXISTING", "namespace": NAMESPACE,
                     "checksum": "c1"},
                    {"file_id": "F-NEW", "namespace": NAMESPACE, "checksum": "c2"},
                ]},
                blobs=["F-EXISTING", "F-NEW"],
            ),
            storage=storage,
        )

        assert uploaded == ["F-NEW"]


# ---------------------------------------------------------------------------
# Document clash policy
# ---------------------------------------------------------------------------


class TestDocumentClashPolicy:
    @pytest.mark.asyncio
    async def test_skip_leaves_the_target_document_alone(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "documents", [_document(1, data={"n": "target"})])

        await _run_merge(
            mongo,
            _archive({"documents": [_document(1, data={"n": "archive"})]}),
            on_clash="skip",
        )

        docs = await _rows(mongo, "documents")
        assert len(docs) == 1 and docs[0]["data"]["n"] == "target"

    @pytest.mark.asyncio
    async def test_overwrite_appends_the_archives_latest_on_top_of_the_head(
        self, mongo
    ):
        # The target's history is preserved and the archive's is not spliced
        # in: interleaving two independent version chains has no defined order
        # and would corrupt the (document_id, version) contract.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [
            _document(1, data={"n": "target-1"}),
            _document(2, data={"n": "target-2"}),
        ])

        await _run_merge(
            mongo,
            _archive({"documents": [
                _document(1, data={"n": "archive-1"}),
                _document(2, data={"n": "archive-2"}),
            ]}),
            on_clash="overwrite",
        )

        docs = await _rows(mongo, "documents", sort_key=lambda d: d["version"])
        assert [d["version"] for d in docs] == [1, 2, 3]
        assert [d["data"]["n"] for d in docs] == [
            "target-1", "target-2", "archive-2",
        ]
        assert {d["document_id"] for d in docs} == {"D1"}

    @pytest.mark.asyncio
    async def test_overwrite_replaces_in_place_on_a_versioned_false_template(
        self, mongo
    ):
        # A versioned:false template's lifecycle IS overwrite-in-place: there
        # is no version contract to protect, and appending would invent a
        # history the template says does not exist.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template(versioned=False)])
        await _seed(mongo, "documents", [_document(1, data={"n": "target"})])

        await _run_merge(
            mongo,
            _archive({"documents": [_document(1, data={"n": "archive"})]}),
            on_clash="overwrite",
        )

        docs = await _rows(mongo, "documents")
        assert len(docs) == 1
        assert docs[0]["version"] == 1
        assert docs[0]["data"]["n"] == "archive"

    @pytest.mark.asyncio
    async def test_overwrite_adopts_the_targets_document_id(self, mongo):
        # Matching may have been by identity hash rather than by id; the
        # appended version belongs to the target's chain either way.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_document(1, doc_id="D-TARGET")])

        await _run_merge(
            mongo,
            _archive({"documents": [
                _document(1, doc_id="D-TARGET", data={"n": "archive"}),
            ]}),
            on_clash="overwrite",
        )

        docs = await _rows(mongo, "documents")
        assert {d["document_id"] for d in docs} == {"D-TARGET"}


# ---------------------------------------------------------------------------
# Definitions precondition (pass 1)
# ---------------------------------------------------------------------------


class TestDefinitionsPrecondition:
    """Definitions are checked before any document moves.

    A document merged under a template whose schema differs between the two
    sides is a document validated against the wrong contract, so this refuses
    rather than resolving item by item while writing.
    """

    @pytest.mark.asyncio
    async def test_same_name_different_schema_refuses(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        with pytest.raises(RestoreEngineError, match=r"not\s+compatible"):
            await _run_merge(
                mongo,
                _archive({"templates": [
                    {**_template(), "fields": [{"name": "age", "type": "string"}]},
                ]}),
            )

    @pytest.mark.asyncio
    async def test_a_definition_the_target_lacks_refuses_by_default(self, mongo):
        await _seed_namespace(mongo)

        with pytest.raises(RestoreEngineError, match="does not have it"):
            await _run_merge(mongo, _archive({"templates": [_template()]}))

    @pytest.mark.asyncio
    async def test_add_missing_inserts_the_definition(self, mongo):
        await _seed_namespace(mongo)

        await _run_merge(
            mongo, _archive({"templates": [_template()]}), add_missing=True
        )

        assert [t["value"] for t in await _rows(mongo, "templates")] == ["PATIENT"]

    @pytest.mark.asyncio
    async def test_identical_definitions_pass_untouched(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        await _run_merge(mongo, _archive({"templates": [_template()]}))

        assert len(await _rows(mongo, "templates")) == 1

    @pytest.mark.asyncio
    async def test_cosmetic_difference_keeps_the_targets_and_reports_it(self, mongo):
        # Never silent: losing a label to the target's copy is the kind of
        # change an operator otherwise discovers from a UI that moved.
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "B-lov", "namespace": NAMESPACE,
             "value": "GENDER", "label": "Gender"},
        ])
        events: list[ProgressEvent] = []

        await _run_merge(
            mongo,
            _archive({"terminologies": [
                {"terminology_id": "A-lov", "namespace": NAMESPACE,
                 "value": "GENDER", "label": "Sex"},
            ]}),
            events,
        )

        rows = await _rows(mongo, "terminologies")
        assert [r["label"] for r in rows] == ["Gender"]
        note = next(e for e in events if "target's label kept" in (e.message or ""))
        assert "Sex" in note.message

    @pytest.mark.asyncio
    async def test_definitions_map_ids_for_the_document_pass(self, mongo):
        # The archive's template lives under another ID; its documents must
        # land pointing at the target's.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [
            {**_template(), "template_id": "B-tpl"},
        ])

        await _run_merge(
            mongo,
            _archive({
                "templates": [{**_template(), "template_id": "A-tpl"}],
                "documents": [{**_document(1), "template_id": "A-tpl",
                               "identity_hash": "h-new"}],
            }),
        )

        (doc,) = await _rows(mongo, "documents")
        assert doc["template_id"] == "B-tpl"


# ---------------------------------------------------------------------------
# Identity conflicts
# ---------------------------------------------------------------------------


class TestIdentityConflicts:
    """One ID naming two different entities is the one thing no policy covers.

    Matching is by content, so the same entity under two IDs is a match. The
    reverse cannot be resolved: picking either side destroys an identity.
    """

    @pytest.mark.asyncio
    async def test_one_id_naming_two_entities_refuses_the_whole_merge(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_document(1, doc_id="D1")])

        with pytest.raises(RestoreEngineError, match="identity conflict"):
            await _run_merge(mongo, _archive({
                "templates": [_template()],
                # Same document_id, a different logical identity.
                "documents": [{**_document(1, doc_id="D1"),
                               "identity_hash": "a-different-thing"}],
            }))

    @pytest.mark.asyncio
    async def test_the_refusal_explains_why_it_cannot_be_resolved(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_document(1, doc_id="D1")])

        with pytest.raises(RestoreEngineError) as excinfo:
            await _run_merge(mongo, _archive({
                "templates": [_template()],
                "documents": [{**_document(1, doc_id="D1"),
                               "identity_hash": "a-different-thing"}],
            }))

        assert "two different entities" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestMergeDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_reports_both_passes_and_writes_nothing(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"},
        ])
        events: list[ProgressEvent] = []

        await _run_merge(
            mongo,
            _archive({"terminologies": [
                {"terminology_id": "T1", "namespace": NAMESPACE,
                 "value": "GENDER"},
                {"terminology_id": "T2", "namespace": NAMESPACE,
                 "value": "COUNTRY"},
            ]}),
            events,
            add_missing=True,
            dry_run=True,
        )

        assert len(await _rows(mongo, "terminologies")) == 1
        report = next(
            e for e in events
            if e.phase == "phase_dry_run" and "terminologies" in (e.message or "")
        )
        assert "1 already identical" in report.message
        assert "1 to add" in report.message
        assert events[-1].phase == "complete"
        assert "no changes made" in events[-1].message

    @pytest.mark.asyncio
    async def test_dry_run_still_fails_on_what_a_real_run_would_refuse(self, mongo):
        # Surfacing the refusal is the whole point of asking first.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        with pytest.raises(RestoreEngineError, match=r"not\s+compatible"):
            await _run_merge(
                mongo,
                _archive({"templates": [
                    {**_template(), "fields": [{"name": "age", "type": "string"}]},
                ]}),
                dry_run=True,
            )

    @pytest.mark.asyncio
    async def test_real_run_reports_the_same_plan_it_acts_on(self, mongo):
        await _seed_namespace(mongo)
        events: list[ProgressEvent] = []

        await _run_merge(
            mongo,
            _archive({"terminologies": [
                {"terminology_id": "T2", "namespace": NAMESPACE,
                 "value": "COUNTRY"},
            ]}),
            events,
            add_missing=True,
        )

        plan = next(
            e for e in events
            if e.phase == "phase_definitions" and "to add" in (e.message or "")
        )
        assert "1 to add" in plan.message


# ---------------------------------------------------------------------------
# on_clash = newer
# ---------------------------------------------------------------------------


def _dated(version, when, *, doc_id="D1", data=None):
    """A document row carrying an explicit updated_at."""
    return {**_document(version, doc_id=doc_id, data=data), "updated_at": when}


class TestNewerPolicy:
    """`newer` takes the archive's copy only when it is genuinely fresher.

    The comparison is on updated_at — when the content last changed — not on
    the document_id's embedded UUID7 time, which records creation.
    """

    @staticmethod
    async def _merge(mongo, target_row, archive_row, events=None):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [target_row])
        await _run_merge(
            mongo,
            _archive({"templates": [_template()], "documents": [archive_row]}),
            events,
            on_clash="newer",
        )
        return await _rows(mongo, "documents", sort_key=lambda d: d["version"])

    @pytest.mark.asyncio
    async def test_a_newer_archive_copy_is_taken(self, mongo):
        docs = await self._merge(
            mongo,
            _dated(1, datetime(2026, 1, 1, tzinfo=UTC), data={"n": "target"}),
            _dated(1, "2026-07-01T00:00:00+00:00", data={"n": "archive"}),
        )

        assert [d["version"] for d in docs] == [1, 2]
        assert docs[-1]["data"]["n"] == "archive"

    @pytest.mark.asyncio
    async def test_an_older_archive_copy_is_left_alone(self, mongo):
        docs = await self._merge(
            mongo,
            _dated(1, datetime(2026, 7, 1, tzinfo=UTC), data={"n": "target"}),
            _dated(1, "2026-01-01T00:00:00+00:00", data={"n": "archive"}),
        )

        assert len(docs) == 1 and docs[0]["data"]["n"] == "target"

    @pytest.mark.asyncio
    async def test_a_tie_keeps_the_target(self, mongo):
        # Equal timestamps carry no information about which side to prefer,
        # and writing on no information is worse than leaving a live namespace
        # alone.
        docs = await self._merge(
            mongo,
            _dated(1, datetime(2026, 7, 1, tzinfo=UTC), data={"n": "target"}),
            _dated(1, "2026-07-01T00:00:00+00:00", data={"n": "archive"}),
        )

        assert len(docs) == 1 and docs[0]["data"]["n"] == "target"

    @pytest.mark.asyncio
    async def test_naive_stored_datetimes_compare_as_utc(self, mongo):
        # MongoDB hands back naive datetimes that are UTC by convention, while
        # the archive's are ISO strings. Comparing them raw would raise rather
        # than answer.
        docs = await self._merge(
            mongo,
            _dated(1, datetime(2026, 1, 1), data={"n": "target"}),
            _dated(1, "2026-07-01T00:00:00", data={"n": "archive"}),
        )

        assert [d["data"]["n"] for d in docs] == ["target", "archive"]

    @pytest.mark.asyncio
    async def test_an_unusable_timestamp_keeps_the_target_and_warns(self, mongo):
        events: list[ProgressEvent] = []
        docs = await self._merge(
            mongo,
            _dated(1, datetime(2026, 1, 1, tzinfo=UTC), data={"n": "target"}),
            _dated(1, "not-a-date", data={"n": "archive"}),
            events,
        )

        assert len(docs) == 1 and docs[0]["data"]["n"] == "target"
        warning = next(e for e in events if e.phase == "warning")
        assert "could not be compared by update time" in warning.message

    @pytest.mark.asyncio
    async def test_a_missing_timestamp_keeps_the_target(self, mongo):
        docs = await self._merge(
            mongo,
            _dated(1, datetime(2026, 1, 1, tzinfo=UTC), data={"n": "target"}),
            _document(1, data={"n": "archive"}),
        )

        assert len(docs) == 1 and docs[0]["data"]["n"] == "target"

    @pytest.mark.asyncio
    async def test_the_report_says_how_many_were_kept(self, mongo):
        events: list[ProgressEvent] = []
        await self._merge(
            mongo,
            _dated(1, datetime(2026, 7, 1, tzinfo=UTC), data={"n": "target"}),
            _dated(1, "2026-01-01T00:00:00+00:00", data={"n": "archive"}),
            events,
        )

        report = next(
            e for e in events if "not older" in (e.message or "")
        )
        assert "1 kept" in report.message

    @pytest.mark.asyncio
    async def test_newer_is_accepted_as_a_policy(self, mongo):
        await _seed_namespace(mongo)
        await _run_merge(mongo, _archive(), on_clash="newer")


# ---------------------------------------------------------------------------
# Merging into a different namespace
# ---------------------------------------------------------------------------


OTHER_NAMESPACE = "merge-source-ns"


async def _clear_other(mongo):
    for db_name, coll_name in _all_collections():
        key = "prefix" if coll_name == "namespaces" else "namespace"
        await mongo[db_name][coll_name].delete_many({key: OTHER_NAMESPACE})


def _archive_from_other(entities=None):
    """An archive whose namespace differs from the merge target."""
    reader = _archive(entities)
    reader.read_manifest = MagicMock(return_value=Manifest(
        format_version="3.0",
        namespace=OTHER_NAMESPACE,
        namespace_config=NamespaceConfig(
            prefix=OTHER_NAMESPACE, isolation_mode="open"
        ),
        counts=EntityCounts(),
    ))
    reader.list_namespaces = MagicMock(return_value=[OTHER_NAMESPACE])
    return reader


class TestMergeIntoADifferentNamespace:
    """Folding NS2's archive into NS1 — the second driving use case.

    Works because a canonical UUID carries no namespace: what has to move is
    the `namespace` field and the composite keys that embed it. It requires
    the source's entities to be gone from this instance, since one ID cannot
    name an entity in two namespaces.
    """

    @pytest.mark.asyncio
    async def test_entities_land_in_the_target_namespace(self, mongo):
        await _clear_other(mongo)
        await _seed_namespace(mongo)

        await _run_merge(
            mongo,
            _archive_from_other({"terminologies": [
                {"terminology_id": "T1", "namespace": OTHER_NAMESPACE,
                 "value": "GENDER"},
            ]}),
            add_missing=True,
        )

        rows = await _rows(mongo, "terminologies")
        assert [r["terminology_id"] for r in rows] == ["T1"]
        assert rows[0]["namespace"] == NAMESPACE
        await _clear_other(mongo)

    @pytest.mark.asyncio
    async def test_composite_keys_are_re_scoped_and_rehashed(self, mongo):
        # The key embeds the namespace and its hash is what the uniqueness
        # gate is built on. Moved without rehashing, the entry would claim a
        # key naming the old namespace and collide with nothing.
        from wip_auth.composite_key import compute_composite_key_hash

        await _clear_other(mongo)
        await _seed_namespace(mongo)
        source_key = {"ns": OTHER_NAMESPACE, "value": "GENDER", "label": "Gender"}

        await _run_merge(
            mongo,
            _archive_from_other({"registry_entries": [{
                "entry_id": "E1",
                "namespace": OTHER_NAMESPACE,
                "entity_type": "terminologies",
                "primary_composite_key": source_key,
                "primary_composite_key_hash": compute_composite_key_hash(source_key),
                "synonyms": [],
            }]}),
            add_missing=True,
        )

        (entry,) = await _rows(mongo, "registry_entries")
        expected_key = {"ns": NAMESPACE, "value": "GENDER", "label": "Gender"}
        assert entry["primary_composite_key"] == expected_key
        assert entry["primary_composite_key_hash"] == compute_composite_key_hash(
            expected_key
        )
        await _clear_other(mongo)

    @pytest.mark.asyncio
    async def test_a_synonym_scoped_elsewhere_keeps_its_own_namespace(self, mongo):
        # Only components naming the SOURCE move. A synonym deliberately
        # scoped to a third namespace is not part of this migration.
        await _clear_other(mongo)
        await _seed_namespace(mongo)

        await _run_merge(
            mongo,
            _archive_from_other({"registry_entries": [{
                "entry_id": "E1",
                "namespace": OTHER_NAMESPACE,
                "entity_type": "terminologies",
                "primary_composite_key": {"ns": OTHER_NAMESPACE, "value": "GENDER"},
                "primary_composite_key_hash": "h1",
                "synonyms": [{
                    "namespace": "third-party",
                    "entity_type": "terminologies",
                    "composite_key": {"ns": "third-party", "value": "SEX"},
                    "composite_key_hash": "h2",
                }],
            }]}),
            add_missing=True,
        )

        (entry,) = await _rows(mongo, "registry_entries")
        assert entry["synonyms"][0]["namespace"] == "third-party"
        assert entry["synonyms"][0]["composite_key"]["ns"] == "third-party"
        await _clear_other(mongo)

    @pytest.mark.asyncio
    async def test_ids_still_registered_here_refuse_the_merge(self, mongo):
        # One canonical ID cannot name an entity in two namespaces. This is
        # not a policy question — it is a request the identity model cannot
        # represent.
        await _clear_other(mongo)
        await _seed_namespace(mongo)
        await _seed(mongo, "registry_entries", [{
            "entry_id": "E1", "namespace": NAMESPACE,
            "entity_type": "terminologies",
            "primary_composite_key_hash": "h", "synonyms": [],
        }])

        with pytest.raises(RestoreEngineError, match="still registered"):
            await _run_merge(
                mongo,
                _archive_from_other({"registry_entries": [{
                    "entry_id": "E1", "namespace": OTHER_NAMESPACE,
                    "entity_type": "terminologies",
                    "primary_composite_key_hash": "h", "synonyms": [],
                }]}),
                add_missing=True,
            )
        await _clear_other(mongo)

    @pytest.mark.asyncio
    async def test_the_refusal_names_where_the_id_already_lives(self, mongo):
        await _clear_other(mongo)
        await _seed_namespace(mongo)
        await _seed(mongo, "registry_entries", [{
            "entry_id": "E1", "namespace": NAMESPACE,
            "entity_type": "terminologies",
            "primary_composite_key_hash": "h", "synonyms": [],
        }])

        with pytest.raises(RestoreEngineError) as excinfo:
            await _run_merge(
                mongo,
                _archive_from_other({"registry_entries": [{
                    "entry_id": "E1", "namespace": OTHER_NAMESPACE,
                    "entity_type": "terminologies",
                    "primary_composite_key_hash": "h", "synonyms": [],
                }]}),
                add_missing=True,
            )

        assert NAMESPACE in str(excinfo.value)
        await _clear_other(mongo)

    @pytest.mark.asyncio
    async def test_nothing_is_written_when_the_ids_are_taken(self, mongo):
        # The check runs before any write; left to the unique index, the same
        # collision would surface partway through an applied merge.
        await _clear_other(mongo)
        await _seed_namespace(mongo)
        await _seed(mongo, "registry_entries", [{
            "entry_id": "E1", "namespace": NAMESPACE,
            "entity_type": "terminologies",
            "primary_composite_key_hash": "h", "synonyms": [],
        }])

        with pytest.raises(RestoreEngineError):
            await _run_merge(
                mongo,
                _archive_from_other({
                    "registry_entries": [{
                        "entry_id": "E1", "namespace": OTHER_NAMESPACE,
                        "entity_type": "terminologies",
                        "primary_composite_key_hash": "h", "synonyms": [],
                    }],
                    "terminologies": [
                        {"terminology_id": "T-NEW", "namespace": OTHER_NAMESPACE,
                         "value": "COUNTRY"},
                    ],
                }),
                add_missing=True,
            )

        assert await _rows(mongo, "terminologies") == []
        await _clear_other(mongo)

    @pytest.mark.asyncio
    async def test_a_plain_restore_still_refuses_to_re_namespace(self, mongo):
        # Only merge redirects. A restore preserves everything verbatim.
        engine = DirectRestoreEngine(mongo, None, lambda _e: None)
        reader = _archive_from_other()

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=reader,
        ), pytest.raises(RestoreEngineError, match="cannot re-namespace"):
            await engine.run_restore(MagicMock(), NAMESPACE)


# ---------------------------------------------------------------------------
# Pre-write reference check
# ---------------------------------------------------------------------------


class TestMergeReferenceCheck:
    """A merge lands in a live namespace that cannot be deleted to recover.

    A restore into a fresh namespace can be thrown away and retried, so it
    verifies afterwards. A merge cannot, so it verifies first.
    """

    @pytest.mark.asyncio
    async def test_a_reference_nothing_supplies_refuses_the_merge(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        with pytest.raises(RestoreEngineError, match="resolve to nothing"):
            await _run_merge(mongo, _archive({
                "templates": [_template()],
                "documents": [{
                    **_document(1),
                    "references": [{
                        "field_path": "supervisor",
                        "reference_type": "document",
                        "resolved": {"document_id": "NOT-HERE"},
                    }],
                }],
            }))

    @pytest.mark.asyncio
    async def test_nothing_is_written_when_a_reference_is_unresolvable(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        with pytest.raises(RestoreEngineError):
            await _run_merge(mongo, _archive({
                "templates": [_template()],
                "documents": [{
                    **_document(1),
                    "file_references": [{"field_path": "scan", "file_id": "GONE"}],
                }],
            }))

        assert await _rows(mongo, "documents") == []

    @pytest.mark.asyncio
    async def test_a_reference_the_target_already_holds_is_fine(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_document(1, doc_id="EXISTING")])

        await _run_merge(mongo, _archive({
            "templates": [_template()],
            "documents": [{
                **_document(1, doc_id="D-NEW"),
                "identity_hash": "h-new",
                "references": [{
                    "field_path": "supervisor",
                    "reference_type": "document",
                    "resolved": {"document_id": "EXISTING"},
                }],
            }],
        }))

        assert len(await _rows(mongo, "documents")) == 2

    @pytest.mark.asyncio
    async def test_a_reference_this_merge_supplies_is_fine(self, mongo):
        # Two documents arriving together, one pointing at the other: the
        # target has neither, and the merge is still coherent.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        await _run_merge(mongo, _archive({
            "templates": [_template()],
            "documents": [
                {**_document(1, doc_id="D-A"), "identity_hash": "h-a"},
                {
                    **_document(1, doc_id="D-B"), "identity_hash": "h-b",
                    "references": [{
                        "field_path": "supervisor",
                        "reference_type": "document",
                        "resolved": {"document_id": "D-A"},
                    }],
                },
            ],
        }))

        assert len(await _rows(mongo, "documents")) == 2

    @pytest.mark.asyncio
    async def test_skipped_clashes_are_not_reference_checked(self, mongo):
        # Under skip the archive's copy is never written, so its references
        # are irrelevant — refusing on them would block a merge over data the
        # target was always going to keep.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])
        await _seed(mongo, "documents", [_document(1)])

        await _run_merge(
            mongo,
            _archive({
                "templates": [_template()],
                "documents": [{
                    **_document(1),
                    "references": [{
                        "field_path": "supervisor",
                        "reference_type": "document",
                        "resolved": {"document_id": "NEVER-EXISTED"},
                    }],
                }],
            }),
            on_clash="skip",
        )

        assert len(await _rows(mongo, "documents")) == 1

    @pytest.mark.asyncio
    async def test_the_dry_run_refuses_too(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        with pytest.raises(RestoreEngineError, match="resolve to nothing"):
            await _run_merge(
                mongo,
                _archive({
                    "templates": [_template()],
                    "documents": [{
                        **_document(1),
                        "term_references": [
                            {"field_path": "gender", "term_id": "T-GONE"},
                        ],
                    }],
                }),
                dry_run=True,
            )


# ---------------------------------------------------------------------------
# Remap restore (mode 3)
# ---------------------------------------------------------------------------


REMAP_TARGET = "remap-target-ns"


async def _clear_remap_target(mongo):
    for db_name, coll_name in _all_collections():
        key = "prefix" if coll_name == "namespaces" else "namespace"
        await mongo[db_name][coll_name].delete_many({key: REMAP_TARGET})


async def _remap_rows(mongo, entity_type):
    db_name, coll_name = COLLECTION_MAP[entity_type]
    return await mongo[db_name][coll_name].find(
        {"namespace": REMAP_TARGET}, {"_id": 0}
    ).to_list(length=None)


class _FakeRegistry:
    """Stands in for the Registry's provision and activate endpoints.

    The endpoints' own behaviour is pinned in the registry component's
    reservation-lifecycle tests; what matters here is that the engine calls
    them in the right order with the right payloads, and writes what comes
    back.
    """

    def __init__(self):
        self.provisioned: list[tuple[str, list[dict]]] = []
        self.activated: list[str] = []
        self._n = 0

    async def post(self, url, json=None, headers=None):
        if url.endswith("/entries/provision"):
            self.provisioned.append((json["entity_type"], json["composite_keys"]))
            ids = []
            for _ in range(json["count"]):
                self._n += 1
                ids.append(f"MINT-{self._n}")
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "ids": [{"entry_id": i, "status": "reserved"} for i in ids]
                }),
            )
        if url.endswith("/entries/activate"):
            self.activated.extend(item["entry_id"] for item in json)
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value={"activated": len(json), "errors": 0}),
            )
        # Namespace upsert
        return MagicMock(status_code=200, text="ok")


async def _run_remap(mongo, reader, events=None, *, registry=None, **kwargs):
    def callback(event: ProgressEvent) -> None:
        if events is not None:
            events.append(event)

    registry = registry or _FakeRegistry()
    http = MagicMock()
    http.post = registry.post
    http.put = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)

    engine = DirectRestoreEngine(mongo, None, callback)
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=reader
    ), patch("httpx.AsyncClient", return_value=http):
        await engine.run_remap(MagicMock(), REMAP_TARGET, **kwargs)
    return registry


class TestRemapRestore:
    """Everything is registered afresh; nothing keeps its old identity."""

    @pytest.mark.asyncio
    async def test_entities_land_under_new_ids_in_the_target(self, mongo):
        await _clear_remap_target(mongo)

        await _run_remap(mongo, _archive({
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": NAMESPACE,
                 "value": "GENDER"},
            ],
        }))

        (row,) = await _remap_rows(mongo, "terminologies")
        assert row["terminology_id"].startswith("MINT-")
        assert row["namespace"] == REMAP_TARGET
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_references_follow_the_new_ids(self, mongo):
        await _clear_remap_target(mongo)

        await _run_remap(mongo, _archive({
            "templates": [
                {"template_id": "OLD-TPL", "namespace": NAMESPACE,
                 "value": "PATIENT", "identity_fields": []},
            ],
            "documents": [
                {"document_id": "OLD-DOC", "namespace": NAMESPACE,
                 "template_id": "OLD-TPL", "template_version": 1,
                 "identity_hash": "h1", "version": 1, "data": {"age": 3}},
            ],
        }))

        (template,) = await _remap_rows(mongo, "templates")
        (document,) = await _remap_rows(mongo, "documents")
        assert document["template_id"] == template["template_id"]
        assert document["document_id"] != "OLD-DOC"
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_the_archives_registry_entries_are_not_written(self, mongo):
        # Provisioning creates the entries. Importing the archived ones would
        # duplicate every identity under its old id.
        await _clear_remap_target(mongo)

        await _run_remap(mongo, _archive({
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": NAMESPACE,
                 "value": "GENDER"},
            ],
            "registry_entries": [
                {"entry_id": "OLD-LOV", "namespace": NAMESPACE,
                 "entity_type": "terminologies",
                 "primary_composite_key_hash": "h", "synonyms": []},
            ],
        }))

        assert await _remap_rows(mongo, "registry_entries") == []
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_composite_keys_are_built_for_the_target_namespace(self, mongo):
        await _clear_remap_target(mongo)

        registry = await _run_remap(mongo, _archive({
            "terminologies": [
                {"terminology_id": "OLD-LOV", "namespace": NAMESPACE,
                 "value": "GENDER", "label": "Gender"},
            ],
        }))

        (entity_type, keys) = registry.provisioned[0]
        assert entity_type == "terminologies"
        assert keys[0] == {
            "ns": REMAP_TARGET, "value": "GENDER", "label": "Gender",
        }
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_everything_is_activated_at_the_end(self, mongo):
        # Until activation the namespace is invisible; that is what makes a
        # failed remap recoverable rather than half-live.
        await _clear_remap_target(mongo)

        registry = await _run_remap(mongo, _archive({
            "terminologies": [
                {"terminology_id": "L1", "namespace": NAMESPACE, "value": "A"},
                {"terminology_id": "L2", "namespace": NAMESPACE, "value": "B"},
            ],
        }))

        assert sorted(registry.activated) == ["MINT-1", "MINT-2"]
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_provenance_is_recorded_on_the_namespace(self, mongo):
        await _clear_remap_target(mongo)
        # The namespace upsert creates this row through the Registry; the
        # Registry is stubbed here, so stand it up directly.
        await mongo[_DB_REGISTRY]["namespaces"].insert_one(
            {"prefix": REMAP_TARGET, "description": "", "isolation_mode": "open"}
        )

        await _run_remap(mongo, _archive({
            "terminologies": [
                {"terminology_id": "L1", "namespace": NAMESPACE, "value": "A"},
            ],
        }))

        live = await mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": REMAP_TARGET}
        )
        assert live is not None
        assert f"of '{NAMESPACE}'" in live["description"]
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_a_non_empty_target_is_refused(self, mongo):
        await _clear_remap_target(mongo)
        db_name, coll_name = COLLECTION_MAP["terminologies"]
        await mongo[db_name][coll_name].insert_one(
            {"terminology_id": "SQUATTER", "namespace": REMAP_TARGET, "value": "X"}
        )

        with pytest.raises(RestoreEngineError, match="not empty"):
            await _run_remap(mongo, _archive())
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_a_dry_run_provisions_nothing_and_writes_nothing(self, mongo):
        # Provisioning writes reserved entries, so a preview that called the
        # Registry would leave rows behind and not be a preview.
        await _clear_remap_target(mongo)
        events: list[ProgressEvent] = []

        registry = await _run_remap(
            mongo,
            _archive({"terminologies": [
                {"terminology_id": "L1", "namespace": NAMESPACE, "value": "A"},
            ]}),
            events,
            dry_run=True,
        )

        assert registry.provisioned == [] and registry.activated == []
        assert await _remap_rows(mongo, "terminologies") == []
        assert "nothing provisioned" in events[-1].message
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_a_multi_namespace_archive_is_refused_for_now(self, mongo):
        await _clear_remap_target(mongo)
        reader = _archive()
        reader.list_namespaces = MagicMock(return_value=["kb", "library"])
        reader.read_manifest = MagicMock(return_value=Manifest(
            format_version="3.0",
            namespaces=[
                NamespaceEntry(prefix="kb", counts=EntityCounts()),
                NamespaceEntry(prefix="library", counts=EntityCounts()),
            ],
            counts=EntityCounts(),
        ))

        with pytest.raises(RestoreEngineError, match="explicit source-to-target"):
            await _run_remap(mongo, reader)
        await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_a_target_namespace_is_required(self, mongo):
        engine = DirectRestoreEngine(mongo, None, lambda _e: None)
        with pytest.raises(RestoreEngineError, match="needs a target namespace"):
            await engine.run_remap(MagicMock(), "")
