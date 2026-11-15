"""An import stops on an outage as a failed job, and every failed row has a code.

An import caught every exception of a row into a plain {row, error} entry,
and by default attempted every row: a Registry or store outage became one
"failed row" per row of the file, each without an error code, and the answer
was 200. A row that meets an outage stops the import whatever skip_errors
says: the rows after it are counted as skipped, and since the rows are written
as a job the failure lands on the job — FAILED, its error naming the row it
stopped at and its result the report of what was written before it. Every
error entry carries the row's error_code.
"""

import pytest
from httpx import AsyncClient
from pymongo.errors import AutoReconnect

from document_store.services.document_service import DocumentService
from wip_auth.registry_client import RegistryUnavailableError

from .test_case_1140_import import _import, finish_job, start_import

ROWS = [
    ("127410001", "Ann", "Lee"),
    ("127410002", "Bob", "Lee"),
    ("127410003", "Cat", "Lee"),
    ("127410004", "Dan", "Lee"),
]


def _fail_from_second(exc: Exception):
    real = DocumentService.create_document
    calls = 0

    async def create(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise exc
        return await real(self, *args, **kwargs)

    return create


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    RegistryUnavailableError("registry down"),
    AutoReconnect("store down"),
])
async def test_an_outage_fails_the_job_with_the_report_so_far(
    client: AsyncClient, auth_headers, monkeypatch, exc,
):
    monkeypatch.setattr(DocumentService, "create_document", _fail_from_second(exc))
    job_id = (await start_import(client, auth_headers, ROWS))["job_id"]

    job = await finish_job(client, auth_headers, job_id, status="failed")
    assert "stopped at row 3" in job["error"], job
    assert "not attempted" in job["error"], job
    report = job["result"]
    assert report["result_kind"] == "import_report"
    assert (report["succeeded"], report["failed"], report["skipped"]) == (1, 1, 2)
    assert [r["row"] for r in report["results"]] == [2]
    [error] = report["errors"]
    assert error["row"] == 3
    assert error["error_code"] in ("registry_error", "dependency_unavailable")


@pytest.mark.asyncio
async def test_an_outage_returned_as_an_error_stops_too(
    client: AsyncClient, auth_headers, monkeypatch,
):
    async def refuse(self, *args, **kwargs):
        return None, {"error": "def-store unavailable", "error_code": "dependency_unavailable"}

    monkeypatch.setattr(DocumentService, "create_document", refuse)
    job_id = (await start_import(client, auth_headers, ROWS))["job_id"]

    job = await finish_job(client, auth_headers, job_id, status="failed")
    report = job["result"]
    assert (report["succeeded"], report["failed"], report["skipped"]) == (0, 1, 3)


@pytest.mark.asyncio
async def test_failed_rows_carry_their_error_code(client: AsyncClient, auth_headers):
    data = await _import(client, auth_headers, [
        ("127410011", "Ann", "Lee"),
        ("127410012", "", "Lee"),  # first_name is mandatory
    ])
    [error] = data["errors"]
    assert error["error_code"] == "validation_failed", error


@pytest.mark.asyncio
async def test_rows_written_before_an_outage_stand(client: AsyncClient, auth_headers, monkeypatch):
    """The failed job is not a rollback: the row written before the outage is
    a document, which is why the report of what landed has to survive on the
    record."""
    monkeypatch.setattr(
        DocumentService, "create_document", _fail_from_second(AutoReconnect("store down")),
    )
    job_id = (await start_import(client, auth_headers, ROWS))["job_id"]
    await finish_job(client, auth_headers, job_id, status="failed")

    listed = await client.get(
        "/api/document-store/documents", headers=auth_headers,
        params={"template_id": "PERSON", "namespace": "wip", "page_size": 100},
    )
    written = [
        d for d in listed.json().get("items", [])
        if (d.get("data") or {}).get("national_id") == ROWS[0][0]
    ]
    assert len(written) == 1, listed.text
