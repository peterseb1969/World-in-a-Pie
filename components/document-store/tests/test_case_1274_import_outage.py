"""An import stops on an outage with 503, and every failed row has a code.

An import caught every exception of a row into a plain {row, error} entry,
and by default attempted every row: a Registry or store outage became one
"failed row" per row of the file, each without an error code, and the answer
was 200. Now a row that meets an outage stops the import whatever skip_errors
says: the answer is 503 dependency_unavailable, its detail the report so far
with the rows after it counted as skipped. Every error entry carries the
row's error_code.
"""

import pytest
from httpx import AsyncClient
from pymongo.errors import AutoReconnect

from document_store.services.document_service import DocumentService
from wip_auth.registry_client import RegistryUnavailableError

from .test_case_1140_import import MAPPING, URL, _csv, _import

ROWS = [
    ("127410001", "Ann", "Lee"),
    ("127410002", "Bob", "Lee"),
    ("127410003", "Cat", "Lee"),
    ("127410004", "Dan", "Lee"),
]


async def _post(client, headers, rows, **form):
    return await client.post(
        URL, headers=headers,
        files={"file": ("data.csv", _csv(rows), "text/csv")},
        data={"template_id": "PERSON", "column_mapping": MAPPING, "namespace": "wip", **form},
    )


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
async def test_an_outage_stops_the_import_with_503(
    client: AsyncClient, auth_headers, monkeypatch, exc,
):
    monkeypatch.setattr(DocumentService, "create_document", _fail_from_second(exc))
    resp = await _post(client, auth_headers, ROWS)
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert detail["error_code"] == "dependency_unavailable"
    assert (detail["succeeded"], detail["failed"], detail["skipped"]) == (1, 1, 2)
    assert [r["row"] for r in detail["results"]] == [2]
    [error] = detail["errors"]
    assert error["row"] == 3
    assert error["error_code"] in ("registry_error", "dependency_unavailable")


@pytest.mark.asyncio
async def test_an_outage_returned_as_an_error_stops_too(
    client: AsyncClient, auth_headers, monkeypatch,
):
    async def refuse(self, *args, **kwargs):
        return None, {"error": "def-store unavailable", "error_code": "dependency_unavailable"}

    monkeypatch.setattr(DocumentService, "create_document", refuse)
    resp = await _post(client, auth_headers, ROWS)
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert (detail["succeeded"], detail["failed"], detail["skipped"]) == (0, 1, 3)


@pytest.mark.asyncio
async def test_failed_rows_carry_their_error_code(client: AsyncClient, auth_headers):
    data = await _import(client, auth_headers, [
        ("127410011", "Ann", "Lee"),
        ("127410012", "", "Lee"),  # first_name is mandatory
    ])
    [error] = data["errors"]
    assert error["error_code"] == "validation_failed", error
