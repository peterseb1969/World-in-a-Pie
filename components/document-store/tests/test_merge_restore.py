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
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_toolkit.models import EntityCounts, Manifest, NamespaceConfig, ProgressEvent

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
        chain = [_document(v, data={"n": v}) for v in (1, 2, 3)]

        await _run_merge(mongo, _archive({"documents": chain}))

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
