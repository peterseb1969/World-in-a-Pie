"""An import runs as a job the caller polls, instead of holding the request.

POST /import wrote every row before it answered — 25-50 rows/s, so a 30,000-row
file held a request for about eleven minutes — and a caller that gave up did not
stop the import; nothing noticed it had left. It now answers 202 with a
BackupJob of kind `import` and writes the rows in the background, as backup,
restore and validate_namespace do: leaving is safe, because the job record is
where the answer lives and it keeps its counts as the rows go in.

What still refuses the request itself is what the caller could get wrong before
any row is written: 422 for an unreadable file, a bad mapping or an unusable
template, 413 for an oversized upload. Those leave no job.
"""

import json

import pytest
from httpx import AsyncClient

from document_store.api import import_api
from document_store.models.backup_job import BackupJob, BackupJobKind, BackupJobStatus
from document_store.services import backup_service
from document_store.services.document_service import DocumentService

from .test_case_1140_import import MAPPING, finish_job, post_import, start_import

JOBS = "/api/document-store/backup/jobs"
ROWS = [("132610001", "Ann", "Lee"), ("132610002", "Bob", "Lee"), ("132610003", "Cat", "Lee")]


@pytest.mark.asyncio
async def test_the_import_is_a_job_the_caller_polls(client: AsyncClient, auth_headers):
    """The answer is the job, and the report the route used to return is on the
    record once the run ends."""
    job = await start_import(client, auth_headers, ROWS)
    assert job["namespace"] == "wip"
    assert job["namespaces"] == ["wip"]
    assert job["options"]["total_rows"] == 3
    assert job["options"]["filename"] == "data.csv"
    assert job["options"]["skip_errors"] is True
    assert job["options"]["column_mapping"] == json.loads(MAPPING)
    assert job["created_by"]

    finished = await finish_job(client, auth_headers, job["job_id"])
    assert (finished["kind"], finished["phase"], finished["percent"]) == (
        "import", "complete", 100.0,
    )
    report = finished["result"]
    assert report["result_kind"] == "import_report"
    assert (report["succeeded"], report["failed"], report["skipped"]) == (3, 0, 0)
    assert [r["row"] for r in report["results"]] == [2, 3, 4]


@pytest.mark.asyncio
async def test_the_rows_are_written_as_the_job_runs(client: AsyncClient, auth_headers):
    """Answering early does not mean writing early: the documents exist by the
    time the job is complete, one per row."""
    rows = [(f"132{i:06d}", "Ada", "Imp") for i in range(60)]
    job_id = (await start_import(client, auth_headers, rows))["job_id"]
    await finish_job(client, auth_headers, job_id)

    listed = await client.get(
        "/api/document-store/documents", headers=auth_headers,
        params={"template_id": "PERSON", "namespace": "wip", "page_size": 200},
    )
    ids = {
        (d.get("data") or {}).get("national_id") for d in listed.json()["items"]
    }
    assert {r[0] for r in rows} <= ids


@pytest.mark.asyncio
async def test_the_record_carries_the_counts_while_the_rows_go_in(
    client: AsyncClient, auth_headers, monkeypatch,
):
    """The counts are persisted between batches, not only at the end.

    An import keeps its parsed rows in memory, so a run whose process died
    cannot resume — the counts it wrote as it went are the only account of how
    far it got, and they have to be current when the process is lost, not
    written once at a finish that may never come.
    """
    seen: dict = {}
    real = DocumentService.create_document
    calls = {"n": 0}

    async def create(self, req, **kwargs):
        calls["n"] += 1
        # The first batch's write lands after row 50 and before row 51, so the
        # record read here says what is already committed.
        if calls["n"] == import_api._IMPORT_BATCH_SIZE + 1:
            midrun = await BackupJob.find_one({"kind": BackupJobKind.IMPORT})
            assert midrun is not None
            seen["status"] = midrun.status
            seen["result"] = midrun.result
        return await real(self, req, **kwargs)

    monkeypatch.setattr(DocumentService, "create_document", create)
    rows = [(f"131{i:06d}", "Bo", "Lee") for i in range(import_api._IMPORT_BATCH_SIZE + 10)]
    job_id = (await start_import(client, auth_headers, rows))["job_id"]

    finished = await finish_job(client, auth_headers, job_id)
    assert seen["status"] == BackupJobStatus.RUNNING
    assert seen["result"]["succeeded"] == import_api._IMPORT_BATCH_SIZE
    assert seen["result"]["total_rows"] == 60
    assert finished["result"]["succeeded"] == 60


@pytest.mark.asyncio
async def test_the_job_shows_up_where_the_other_jobs_are(client: AsyncClient, auth_headers):
    """It rides the shared job surface: the same list endpoint, and the same
    read permission rule as a backup or a restore."""
    job_id = (await start_import(client, auth_headers, ROWS))["job_id"]
    await finish_job(client, auth_headers, job_id)

    listed = await client.get(f"{JOBS}?namespace=wip", headers=auth_headers)
    assert [j["job_id"] for j in listed.json()] == [job_id]
    assert listed.json()[0]["kind"] == "import"


@pytest.mark.asyncio
async def test_a_key_that_cannot_read_the_namespace_cannot_read_its_import_job(
    client: AsyncClient, auth_headers,
):
    """The job holds the namespace's row ids and failures, so its record is not
    a public receipt: polling it takes read on the namespace it wrote."""
    job_id = (await start_import(client, auth_headers, ROWS))["job_id"]
    await finish_job(client, auth_headers, job_id)

    outsider = {"X-API-Key": "test_xns_only_key"}  # reads 'xns-closed', not 'wip'
    resp = await client.get(f"{JOBS}/{job_id}", headers=outsider)
    # A namespace the key cannot read answers as one that is not there, and
    # the job id with it.
    assert resp.status_code == 404, resp.text
    assert job_id not in resp.text


@pytest.mark.asyncio
async def test_a_refused_upload_leaves_no_job(client: AsyncClient, auth_headers):
    """What the caller gets wrong before any row is written stays a refusal on
    the request: a 422 says so on its own, with no job to go and poll."""
    resp = await post_import(
        client, auth_headers, ROWS, column_mapping=json.dumps({"nope": "first_name"}),
    )
    assert resp.status_code == 422, resp.text

    listed = await client.get(f"{JOBS}?namespace=wip", headers=auth_headers)
    assert listed.json() == []


@pytest.mark.asyncio
async def test_a_terminated_import_reports_the_rows_it_had_written(client: AsyncClient):
    """A process lost mid-import leaves a RUNNING job no worker holds. Failing
    it is not enough: the error has to say how far it got, because the rows it
    wrote are committed documents and the rest were never attempted."""
    await BackupJob(
        job_id="imp-1326terminated", kind=BackupJobKind.IMPORT, namespace="wip",
        namespaces=["wip"], status=BackupJobStatus.RUNNING, created_by="apikey:test",
        result={"result_kind": "import_report", "total_rows": 30000, "succeeded": 12000,
                "failed": 3, "skipped": 0},
    ).insert()

    assert await backup_service.reconcile_orphaned_jobs() == 1

    job = await BackupJob.find_one(BackupJob.job_id == "imp-1326terminated")
    assert job is not None
    assert job.status == BackupJobStatus.FAILED
    assert "12000 of 30000 row(s)" in job.error
    assert "not attempted" in job.error


@pytest.mark.asyncio
async def test_a_terminated_import_without_counts_says_so(client: AsyncClient):
    """A job killed before its first batch has no counts. The error says what
    is missing and where to look, rather than going quiet on how far it got."""
    await BackupJob(
        job_id="imp-1326blind", kind=BackupJobKind.IMPORT, namespace="wip",
        namespaces=["wip"], status=BackupJobStatus.RUNNING, created_by="apikey:test",
    ).insert()

    await backup_service.reconcile_orphaned_jobs()

    job = await BackupJob.find_one(BackupJob.job_id == "imp-1326blind")
    assert "not recorded" in job.error
    assert "count the documents" in job.error


@pytest.mark.asyncio
async def test_the_job_is_streamable_on_the_shared_event_stream(
    client: AsyncClient, auth_headers,
):
    """The SSE surface the other jobs use serves an import job in the same
    envelope: a caller watching progress reads one endpoint shape whatever the
    job kind is."""
    from document_store.models.backup_job import BackupProgressMessage

    job_id = (await start_import(client, auth_headers, ROWS))["job_id"]
    await finish_job(client, auth_headers, job_id)

    async with client.stream("GET", f"{JOBS}/{job_id}/events", headers=auth_headers) as resp:
        assert resp.status_code == 200
        buf = b""
        async for chunk in resp.aiter_bytes():
            buf += chunk
            if b"progress" in buf:
                break
    [data_line] = [ln for ln in buf.decode().splitlines() if ln.startswith("data: ")]
    envelope = BackupProgressMessage.model_validate(json.loads(data_line[6:]))
    assert (envelope.job_id, envelope.status, envelope.phase) == (
        job_id, BackupJobStatus.COMPLETE, "complete",
    )
