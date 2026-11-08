"""An import goes on past a failing row by default and says when it cut a list.

POST /import stopped at the first failing row unless skip_errors was sent
true, unlike every bulk write (and unlike the MCP tool, which defaults it to
true). It now reports every row by default; skip_errors=false keeps the old
stop-at-first. Its results and errors lists are capped, and the response now
says when a list was cut instead of looking complete.
"""

import json

import pytest
from httpx import AsyncClient

from document_store.api import import_api

URL = "/api/document-store/import"
MAPPING = json.dumps({"national_id": "national_id", "first_name": "first_name",
                      "last_name": "last_name"})


def _csv(rows: list[tuple[str, str, str]]) -> bytes:
    lines = ["national_id,first_name,last_name", *(",".join(r) for r in rows)]
    return "\n".join(lines).encode()


async def _import(client, headers, rows, **form):
    resp = await client.post(
        URL, headers=headers,
        files={"file": ("data.csv", _csv(rows), "text/csv")},
        data={"template_id": "PERSON", "column_mapping": MAPPING, "namespace": "wip", **form},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


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
