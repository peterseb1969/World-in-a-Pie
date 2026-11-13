"""A merge reads definitions as their models do, and a refusal lists every cause.

Provenance: CASE-1043. Two defects refused a merge of an older install's
archive into a freshly bootstrapped one:

- The comparison read raw rows, so a field one side's row lacked (written
  before the field existed) read as None and differed from the other side's
  default — five terminologies were refused for `unique_names` absent vs
  false. Both sides are now read with absent fields at the model's default,
  nested models included. A real difference still refuses.
- The refusal named five of its causes and the job recorded none. Every
  incompatibility, with both sides' values, is now on the failed job's
  result.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.models import ProgressEvent

from document_store.models.backup_job import BackupJob, BackupJobKind, BackupJobStatus
from document_store.services import backup_service
from document_store.services.backup_engine import COLLECTION_MAP, RestoreEngineError
from document_store.services.merge_definitions import ABSENT, DefinitionsPlanner, with_defaults
from tests.test_merge_restore import (
    NAMESPACE,
    _all_collections,
    _archive,
    _run_merge,
    _seed,
    _seed_namespace,
)

REPO = Path(__file__).resolve().parents[3]


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
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


def _terminology(tid: str, value: str, **extra) -> dict:
    return {"terminology_id": tid, "namespace": NAMESPACE, "value": value, "label": value, **extra}


def _template(tid: str, value: str, fields: list[dict], **extra) -> dict:
    return {
        "template_id": tid, "namespace": NAMESPACE, "value": value, "label": value,
        "version": 1, "fields": fields, **extra,
    }


async def _plan(mongo, archive: dict, target: dict):
    for entity_type, rows in target.items():
        await _seed(mongo, entity_type, rows)
    return await DefinitionsPlanner(mongo, COLLECTION_MAP).plan(archive, NAMESPACE)


# -- defaults ------------------------------------------------------------------------


class TestAbsentFieldsReadAsDefaults:
    @pytest.mark.asyncio
    async def test_an_absent_flag_equals_the_targets_default(self, mongo):
        """The reported refusal: unique_names absent in the archive, false here."""
        plan = await _plan(
            mongo,
            {"terminologies": [_terminology("A1", "KB_DOC_KIND")]},
            {"terminologies": [_terminology("B1", "KB_DOC_KIND", unique_names=False)]},
        )
        assert plan.compatible, plan.incompatibilities
        assert plan.unchanged["terminologies"] == 1
        assert plan.mapping["terminologies"] == {"A1": "B1"}

    @pytest.mark.asyncio
    async def test_a_real_difference_still_refuses(self, mongo):
        plan = await _plan(
            mongo,
            {"terminologies": [_terminology("A1", "KB_DOC_KIND")]},
            {"terminologies": [_terminology("B1", "KB_DOC_KIND", case_sensitive=True)]},
        )
        [issue] = plan.incompatibilities
        assert "case_sensitive" in issue.reason
        assert issue.detail == {"case_sensitive": {"target": True, "archive": ABSENT}}

    @pytest.mark.asyncio
    async def test_unique_names_is_judged_by_effect_not_compared(self, mongo):
        # Nothing the merge adds collides, so the target's setting stands and
        # the difference is reported (test_case_1293_merge_comparisons has the
        # collision that refuses).
        plan = await _plan(
            mongo,
            {"terminologies": [_terminology("A1", "KB_DOC_KIND")]},
            {"terminologies": [_terminology("B1", "KB_DOC_KIND", unique_names=True)]},
        )
        assert plan.compatible
        assert [w.fields for w in plan.target_wins] == [["unique_names"]]

    @pytest.mark.asyncio
    async def test_nested_metadata_reads_as_its_defaults(self, mongo):
        plan = await _plan(
            mongo,
            {"terminologies": [_terminology("A1", "T", metadata={"source": None})]},
            {"terminologies": [_terminology("B1", "T", metadata={
                "source": None, "source_url": None, "version": None,
                "language": "en", "custom": {},
            })]},
        )
        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_template_field_missing_a_newer_key_matches(self, mongo):
        plan = await _plan(
            mongo,
            {"templates": [_template("A1", "BOOK", [{"name": "title", "type": "string"}])]},
            {"templates": [_template("B1", "BOOK", [
                {"name": "title", "type": "string", "mandatory": False, "metadata": {}},
            ], versioned=True, usage="entity", identity_fields=[])]},
        )
        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_template_field_that_really_differs_still_refuses(self, mongo):
        plan = await _plan(
            mongo,
            {"templates": [_template("A1", "BOOK", [{"name": "title", "type": "string"}])]},
            {"templates": [_template("B1", "BOOK", [
                {"name": "title", "type": "string", "mandatory": True},
            ])]},
        )
        [issue] = plan.incompatibilities
        assert "fields" in issue.reason

    def test_filling_never_changes_the_row(self):
        row = {"terminology_id": "A1", "value": "T", "metadata": {}}
        filled = with_defaults("terminologies", row)
        assert row == {"terminology_id": "A1", "value": "T", "metadata": {}}
        assert filled["unique_names"] is False
        assert filled["metadata"]["language"] == "en"

    def test_a_value_either_side_holds_is_kept(self):
        assert with_defaults("terminologies", {"case_sensitive": True})["case_sensitive"] is True


def test_the_generated_defaults_match_the_models():
    """The defaults module is generated from def-store's and template-store's
    models; it must be regenerated whenever one of them changes."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(REPO / p) for p in (
            "components/def-store/src", "components/template-store/src", "libs/wip-auth/src",
        )),
    }
    result = subprocess.run(
        [sys.executable, str(REPO / "components/document-store/scripts/generate_definition_defaults.py"),
         "--check"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


# -- the refusal ---------------------------------------------------------------------


class TestRefusalCarriesEveryCause:
    @pytest.mark.asyncio
    async def test_more_than_five_causes_are_all_carried(self, mongo):
        await _seed_namespace(mongo)
        names = [f"VOCAB_{n}" for n in range(8)]
        await _seed(mongo, "terminologies", [
            _terminology(f"B{n}", name, case_sensitive=True) for n, name in enumerate(names)
        ])
        archive = _archive({
            "terminologies": [_terminology(f"A{n}", name) for n, name in enumerate(names)],
        })

        with pytest.raises(RestoreEngineError) as refused:
            await _run_merge(mongo, archive)

        message = str(refused.value)
        assert "8 definition(s)" in message
        assert "…and 3 more" in message and "result.incompatibilities" in message
        details = refused.value.details
        assert details["result_kind"] == "merge_refused"
        assert details["incompatibility_count"] == 8
        assert details["incompatibilities_truncated"] == 0
        assert sorted(i["name"] for i in details["incompatibilities"]) == sorted(names)
        first = details["incompatibilities"][0]
        assert first["namespace"] == NAMESPACE
        assert first["detail"] == {"case_sensitive": {"target": True, "archive": ABSENT}}


@pytest_asyncio.fixture
async def restore_job(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    backup_service._job_queues.clear()
    backup_service._job_tasks.clear()
    job = BackupJob(
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        kind=BackupJobKind.RESTORE,
        namespace=NAMESPACE,
        created_by="test-admin",
    )
    await job.insert()
    yield job
    await job.delete()


@pytest.mark.asyncio
async def test_a_refused_merge_keeps_every_cause_on_the_job(restore_job):
    details = {
        "result_kind": "merge_refused",
        "namespace": NAMESPACE,
        "incompatibility_count": 7,
        "incompatibilities": [{"name": f"V{n}"} for n in range(7)],
        "incompatibilities_truncated": 0,
    }

    async def runner(callback):
        callback(ProgressEvent(phase="start", message="go", percent=0.0))
        raise RestoreEngineError("Merge into 'x' refused — 7 definition(s)", details=details)

    task = await backup_service.start_async_job(restore_job.job_id, runner)
    await asyncio.wait_for(task, timeout=5.0)

    job = await BackupJob.find_one(BackupJob.job_id == restore_job.job_id)
    assert job is not None and job.status == BackupJobStatus.FAILED
    assert job.error.startswith("Merge into 'x' refused")
    assert job.result == details


@pytest.mark.asyncio
async def test_a_plain_failure_writes_no_result(restore_job):
    async def runner(callback):
        raise RestoreEngineError("archive unreadable")

    task = await backup_service.start_async_job(restore_job.job_id, runner)
    await asyncio.wait_for(task, timeout=5.0)

    job = await BackupJob.find_one(BackupJob.job_id == restore_job.job_id)
    assert job is not None and job.status == BackupJobStatus.FAILED
    assert job.result is None
