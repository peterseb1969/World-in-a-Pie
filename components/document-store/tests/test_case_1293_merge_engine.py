"""Merge engine: the identity rehash, the names union, the re-plan bound, the
refusal's record.

- A document whose identity field holds an id the restore rewrote in its data
  (a file field's file_id) is rehashed, though no resolution list changed.
- extend_terminologies writes the archive's names onto the target's term.
- Planning documents again until no new mapping appears stops, with a
  refusal, when the mappings never settle.
- A refusal with more incompatibilities than the record keeps says so.
- A failure whose structured result the job record cannot take is still
  recorded as a failure, with its message.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import ProgressEvent

from document_store.models.backup_job import BackupJob, BackupJobKind, BackupJobStatus
from document_store.services import backup_engine, backup_service
from document_store.services.backup_engine import (
    COLLECTION_MAP,
    MERGE_REFUSAL_INCOMPATIBILITY_CAP,
    DirectRestoreEngine,
    RestoreEngineError,
    _AligningRemapper,
    _IdentityRehash,
)
from document_store.services.merge_definitions import DefinitionsPlan, Incompatibility
from wip_auth.document_identity import compute_hash, resolved_identity_values

NAMESPACE = "merge-1293-engine"


def _engine(mongo: Any = None) -> DirectRestoreEngine:
    return DirectRestoreEngine(mongo or MagicMock(), None, lambda event: None)


# --- the rehash follows ids rewritten in the data ----------------------------------


def test_a_file_identity_rewritten_in_the_data_is_rehashed():
    fields = [{"name": "attachment", "type": "file"}]
    template = {"template_id": "T", "version": 1, "identity_fields": ["attachment"],
                "fields": fields}
    reader = MagicMock()
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [dict(template)] if et == "templates" else []
    )

    def doc(file_id: str, hash_: str) -> dict:
        return {"document_id": "D", "template_id": "T", "template_version": 1,
                "identity_hash": hash_, "data": {"attachment": file_id},
                "file_references": [{"field_path": "attachment", "file_id": file_id}]}

    def expected(file_id: str) -> str:
        return compute_hash(resolved_identity_values(
            {"attachment": file_id}, ["attachment"], fields, [], [], None,
        ))

    rehash = _IdentityRehash(reader, ["src"])
    row = rehash.rehash(doc("F-OLD", expected("F-OLD")), doc("F-NEW", expected("F-OLD")))

    assert expected("F-OLD") != expected("F-NEW")
    assert row["identity_hash"] == expected("F-NEW")


# --- extend_terminologies adds the names -------------------------------------------


@pytest_asyncio.fixture
async def mongo():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
    db_name, coll_name = COLLECTION_MAP["terms"]
    await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})
    yield client
    await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})
    client.close()


@pytest.mark.asyncio
async def test_the_archives_names_are_added_to_the_targets_term(mongo):
    db_name, coll_name = COLLECTION_MAP["terms"]
    terms = mongo[db_name][coll_name]
    await terms.insert_one({"term_id": "T-term", "namespace": NAMESPACE,
                            "terminology_id": "T-lov", "value": "AuthorAssist",
                            "names": [{"value": "wip-aa"}]})
    plan = DefinitionsPlan()
    plan.extend_names.append({"term_id": "T-term", "terminology_id": "T-lov",
                              "names": [{"value": "authorassist"}], "name": "AuthorAssist"})

    await _engine(mongo)._extend_term_names(NAMESPACE, plan)

    row = await terms.find_one({"term_id": "T-term"})
    assert [n["value"] for n in row["names"]] == ["wip-aa", "authorassist"]
    assert row["updated_by"] == "restore-merge"


# --- the re-plan rounds are bounded -------------------------------------------------


@pytest.mark.asyncio
async def test_mappings_that_never_settle_refuse_instead_of_looping():
    engine = _engine()
    engine._external_template_definitions = AsyncMock(return_value={})
    engine._rewrite_entities = lambda et, rows, remapper: (rows, 0)
    engine._rehash_merge_round = AsyncMock(
        side_effect=lambda et, reader, src, tgt, raw, entities, *a: (entities, None, None)
    )
    answers = iter([{"D1": "X"}, {"D1": "Y"}] * 10)

    class Planner:
        def __init__(self, *a):
            pass

        async def plan(self, entity_type, entities, tgt):
            return MagicMock(mapping=next(answers))

    archive = {"documents": [{"document_id": "D1"}, {"document_id": "D2"}]}
    with patch.object(backup_engine, "MergePlanner", Planner), \
            patch.object(backup_engine, "_IdentityRehash", MagicMock()), \
            patch.object(backup_engine, "_archived_template_ids", lambda *a: set()), \
            pytest.raises(RestoreEngineError, match="did not settle"):
        await engine._plan_merge_entities(
            MagicMock(), MagicMock(), [("src", "tgt")], "src", "tgt",
            archive, None, _AligningRemapper(),
        )


@pytest.mark.asyncio
async def test_mappings_that_settle_are_planned_once_more_and_kept():
    engine = _engine()
    engine._external_template_definitions = AsyncMock(return_value={})
    engine._rewrite_entities = lambda et, rows, remapper: (rows, 0)
    engine._rehash_merge_round = AsyncMock(
        side_effect=lambda et, reader, src, tgt, raw, entities, *a: (entities, None, None)
    )
    answers = iter([{"D1": "X"}, {"D1": "X", "D2": "Z"}, {"D1": "X", "D2": "Z"}])

    class Planner:
        def __init__(self, *a):
            pass

        async def plan(self, entity_type, entities, tgt):
            return MagicMock(mapping=next(answers))

    remapper = _AligningRemapper()
    archive = {"documents": [{"document_id": "D1"}, {"document_id": "D2"}]}
    with patch.object(backup_engine, "MergePlanner", Planner), \
            patch.object(backup_engine, "_IdentityRehash", MagicMock()), \
            patch.object(backup_engine, "_archived_template_ids", lambda *a: set()):
        await engine._plan_merge_entities(
            MagicMock(), MagicMock(), [("src", "tgt")], "src", "tgt",
            archive, None, remapper,
        )

    assert remapper.document_map == {"D1": "X", "D2": "Z"}


# --- the refusal says what its record holds ----------------------------------------


def test_a_refusal_past_the_cap_says_the_record_holds_only_the_first():
    plan = DefinitionsPlan()
    plan.incompatibilities = [
        Incompatibility("templates", f"T{n}", "differs")
        for n in range(MERGE_REFUSAL_INCOMPATIBILITY_CAP + 2)
    ]

    with pytest.raises(RestoreEngineError) as refused:
        _engine()._enforce_definitions_gate(NAMESPACE, plan)

    message = str(refused.value)
    assert f"the first {MERGE_REFUSAL_INCOMPATIBILITY_CAP}" in message
    assert "every one" not in message
    assert refused.value.details["incompatibilities_truncated"] == 2


def test_a_refusal_within_the_cap_says_every_one_is_recorded():
    plan = DefinitionsPlan()
    plan.incompatibilities = [Incompatibility("templates", f"T{n}", "differs") for n in range(7)]

    with pytest.raises(RestoreEngineError) as refused:
        _engine()._enforce_definitions_gate(NAMESPACE, plan)

    assert "every one, with the two sides' values, is on the job record" in str(refused.value)


# --- a failure the record cannot take whole ----------------------------------------


@pytest_asyncio.fixture
async def _beanie(session_mongo_client):
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)


@pytest.mark.asyncio
async def test_a_failure_whose_result_cannot_be_stored_is_still_recorded(_beanie, monkeypatch):
    job = BackupJob(job_id=f"job-{uuid.uuid4().hex[:8]}", kind=BackupJobKind.RESTORE,
                    namespace=NAMESPACE, created_by="test")
    await job.insert()
    real = backup_service._persist_event

    async def too_big(job_id, event):
        if event.details:
            raise RuntimeError("BSON document too large")
        await real(job_id, event)

    monkeypatch.setattr(backup_service, "_persist_event", too_big)
    monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", ())
    event = ProgressEvent(
        phase="error", message="Merge into 'x' refused — 5000 definition(s) …",
        details={"result_kind": "merge_refused", "incompatibilities": ["…"]},
    )

    assert await backup_service._persist_with_retry(job.job_id, event)

    found = await BackupJob.find_one(BackupJob.job_id == job.job_id)
    assert found.status == BackupJobStatus.FAILED
    assert found.error.startswith("Merge into 'x' refused")
    assert "could not be stored on the job record" in found.error
    await found.delete()
