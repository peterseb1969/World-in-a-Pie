"""Merge-mode restore (restore modes, Phase 1).

Merge takes an archive as a delta against a namespace that already holds data.
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
    async def test_unknown_schema_clash_policy_is_refused(self, mongo):
        await _seed_namespace(mongo)
        with pytest.raises(RestoreEngineError, match="Invalid on_schema_clash"):
            await _run_merge(mongo, _archive(), on_schema_clash="merge-please")

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

        await _run_merge(mongo, _archive({
            "terminologies": [
                {"terminology_id": "T2", "namespace": NAMESPACE, "value": "COUNTRY"},
            ],
        }))

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
# Schema clash policy
# ---------------------------------------------------------------------------


class TestSchemaClashPolicy:
    @pytest.mark.asyncio
    async def test_fail_is_the_default_and_names_the_difference(self, mongo):
        # Merging data into a namespace whose schema diverged from the archive
        # is a migration someone should look at — loud is the safe default,
        # and deliberately NOT what the platform's write-path upsert does.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template(label="Patient")])

        with pytest.raises(RestoreEngineError, match="schema entit"):
            await _run_merge(
                mongo, _archive({"templates": [_template(label="Subject")]})
            )

    @pytest.mark.asyncio
    async def test_fail_ignores_audit_only_differences(self, mongo):
        # Two faithful copies differ in updated_at; failing on that would make
        # every merge of an unchanged schema refuse.
        await _seed_namespace(mongo)
        target = {**_template(), "updated_at": "2026-07-01T00:00:00"}
        archived = {**_template(), "updated_at": "2026-01-01T00:00:00"}
        await _seed(mongo, "templates", [target])

        await _run_merge(mongo, _archive({"templates": [archived]}))

        assert len(await _rows(mongo, "templates")) == 1

    @pytest.mark.asyncio
    async def test_skip_keeps_the_targets_schema(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template(label="Patient")])

        await _run_merge(
            mongo,
            _archive({"templates": [_template(label="Subject")]}),
            on_schema_clash="skip",
        )

        assert [t["label"] for t in await _rows(mongo, "templates")] == ["Patient"]

    @pytest.mark.asyncio
    async def test_upsert_lands_the_archives_template_as_a_new_version(self, mongo):
        # Matches the platform's create-as-upsert shape: nothing is
        # overwritten, the archive's definition becomes version N+1.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [
            _template(1, label="Patient"), _template(2, label="Patient v2"),
        ])

        await _run_merge(
            mongo,
            _archive({"templates": [_template(2, label="Subject")]}),
            on_schema_clash="upsert",
        )

        templates = await _rows(mongo, "templates", sort_key=lambda t: t["version"])
        assert [t["version"] for t in templates] == [1, 2, 3]
        assert templates[-1]["label"] == "Subject"
        assert templates[-1]["template_id"] == "TPL"

    @pytest.mark.asyncio
    async def test_upsert_updates_terminologies_in_place(self, mongo):
        # Terminologies have no version axis, so "the archive wins" can only
        # mean updating the target row — which is what upsert asks for.
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER",
             "label": "Gender"},
        ])

        await _run_merge(
            mongo,
            _archive({"terminologies": [
                {"terminology_id": "T1", "namespace": NAMESPACE,
                 "value": "GENDER", "label": "Sex"},
            ]}),
            on_schema_clash="upsert",
        )

        rows = await _rows(mongo, "terminologies")
        assert len(rows) == 1 and rows[0]["label"] == "Sex"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("policy", ["fail", "skip", "upsert"])
    async def test_identical_schema_is_unchanged_under_every_policy(
        self, mongo, policy
    ):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template()])

        await _run_merge(
            mongo,
            _archive({"templates": [_template()]}),
            on_schema_clash=policy,
        )

        assert len(await _rows(mongo, "templates")) == 1


# ---------------------------------------------------------------------------
# Identity conflicts
# ---------------------------------------------------------------------------


class TestIdentityConflicts:
    @pytest.mark.asyncio
    async def test_conflicting_identity_refuses_the_whole_merge(self, mongo):
        # The target reused T1 for a different terminology. Merge preserves
        # IDs, so there is no honest resolution — and nothing is written, not
        # even the entity types that would have merged cleanly.
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "COUNTRY"},
        ])

        with pytest.raises(RestoreEngineError, match="identity conflict"):
            await _run_merge(mongo, _archive({
                "terminologies": [
                    {"terminology_id": "T1", "namespace": NAMESPACE,
                     "value": "GENDER"},
                ],
                "documents": [_document(1)],
            }))

        assert await _rows(mongo, "documents") == []

    @pytest.mark.asyncio
    async def test_conflict_message_points_at_the_reminting_mode(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "COUNTRY"},
        ])

        with pytest.raises(RestoreEngineError) as excinfo:
            await _run_merge(mongo, _archive({"terminologies": [
                {"terminology_id": "T1", "namespace": NAMESPACE,
                 "value": "GENDER"},
            ]}))

        assert "new-namespace" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestMergeDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_reports_the_plan_and_writes_nothing(self, mongo):
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
            dry_run=True,
        )

        assert len(await _rows(mongo, "terminologies")) == 1
        report = next(e for e in events if e.phase == "phase_dry_run")
        assert "insert=1" in report.message and "unchanged=1" in report.message
        assert events[-1].phase == "complete"
        assert "no changes made" in events[-1].message

    @pytest.mark.asyncio
    async def test_dry_run_still_fails_on_what_a_real_run_would_refuse(self, mongo):
        # Surfacing the refusal is the whole point of asking first.
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template(label="Patient")])

        with pytest.raises(RestoreEngineError, match="schema entit"):
            await _run_merge(
                mongo,
                _archive({"templates": [_template(label="Subject")]}),
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
        )

        plan = next(e for e in events if e.phase == "phase_merge_plan")
        assert "insert=1" in plan.message
