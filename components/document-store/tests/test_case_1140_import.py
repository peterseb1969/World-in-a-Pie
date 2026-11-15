"""An import goes on past a failing row by default and says when it cut a list.

POST /import stopped at the first failing row unless skip_errors was sent
true, unlike every bulk write (and unlike the MCP tool, which defaults it to
true). It now reports every row by default; skip_errors=false keeps the old
stop-at-first. Its results and errors lists are capped, and the response now
says when a list was cut instead of looking complete.

The route answers 202 with a job and writes the rows in the background, so
these helpers do what a caller does: start the job, wait for the run, and read
the report off the job record.
"""

import json

import pytest
from httpx import AsyncClient

from document_store.api import import_api
from document_store.services import backup_service

URL = "/api/document-store/import"
JOBS = "/api/document-store/backup/jobs"
MAPPING = json.dumps({"national_id": "national_id", "first_name": "first_name",
                      "last_name": "last_name"})


def _csv(rows: list[tuple[str, str, str]]) -> bytes:
    lines = ["national_id,first_name,last_name", *(",".join(r) for r in rows)]
    return "\n".join(lines).encode()


async def post_import(
    client: AsyncClient, headers: dict, rows: list[tuple[str, str, str]], **form,
):
    """The import request, answered as it answers: 202 and the job, or the 422
    that refuses an unusable file before any job exists."""
    return await client.post(
        URL, headers=headers,
        files={"file": ("data.csv", _csv(rows), "text/csv")},
        data={"template_id": "PERSON", "column_mapping": MAPPING, "namespace": "wip", **form},
    )


async def start_import(client: AsyncClient, headers: dict, rows, **form) -> dict:
    """The job an import starts, as the route returned it."""
    resp = await post_import(client, headers, rows, **form)
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert (job["kind"], job["status"]) == ("import", "pending"), job
    assert job["job_id"].startswith("imp-"), job
    return job


async def finish_job(
    client: AsyncClient, headers: dict, job_id: str, status: str = "complete",
) -> dict:
    """A job's record once its run has ended, read over HTTP as a caller polls
    it. The wait is on the run itself; the read is the polling endpoint."""
    await backup_service.wait_for_job(job_id, timeout=30)
    resp = await client.get(f"{JOBS}/{job_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["status"] == status, job
    return job


async def report_of(
    client: AsyncClient, headers: dict, resp, status: str = "complete",
) -> dict:
    """The report behind an import request's answer: that it started a job, and
    what the run did once it ended."""
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["kind"] == "import", job
    return (await finish_job(client, headers, job["job_id"], status))["result"]


async def _import(client: AsyncClient, headers: dict, rows, **form) -> dict:
    """An import through to its report: start the job, wait for the run, and
    read what it did off the record."""
    return await report_of(client, headers, await post_import(client, headers, rows, **form))


ROWS = [
    ("114200001", "Ann", "Lee"),
    ("114200002", "", "Lee"),  # first_name is mandatory: this row fails
    ("114200003", "Cat", "Lee"),
]


@pytest.mark.asyncio
async def test_default_reports_every_row(client: AsyncClient, auth_headers):
    data = await _import(client, auth_headers, ROWS)
    assert (data["succeeded"], data["failed"], data["skipped"]) == (2, 1, 0)
    assert [e["row"] for e in data["errors"]] == [3]
    assert (data["results_truncated"], data["errors_truncated"]) == (False, False)


@pytest.mark.asyncio
async def test_skip_errors_false_still_stops(client: AsyncClient, auth_headers):
    data = await _import(client, auth_headers, ROWS, skip_errors="false")
    assert (data["succeeded"], data["failed"], data["skipped"]) == (1, 1, 1)


@pytest.mark.asyncio
async def test_a_cut_list_says_so(client: AsyncClient, auth_headers, monkeypatch):
    monkeypatch.setattr(import_api, "_MAX_LISTED_RESULTS", 1)
    monkeypatch.setattr(import_api, "_MAX_LISTED_ERRORS", 1)
    rows = [*ROWS, ("114200004", "", "Lee")]
    data = await _import(client, auth_headers, rows)
    assert (data["succeeded"], data["failed"]) == (2, 2)
    assert (len(data["results"]), len(data["errors"])) == (1, 1)
    assert (data["results_truncated"], data["errors_truncated"]) == (True, True)
