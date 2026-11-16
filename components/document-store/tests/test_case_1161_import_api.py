"""The import route's answers for a file it cannot read.

The import tests cover every branch of POST /import except a file that fails
to parse. Both outcomes are pinned here: a parser refusal (ValueError) is
answered with its own message, and any other parse failure is answered as
"Failed to parse file: ..." — in both cases a 422, after the template id is
resolved (the route's own order) and before its fields are fetched or any
document is written.
"""

import io
import json

import openpyxl
import pytest
from httpx import AsyncClient


def _empty_xlsx() -> bytes:
    """A workbook whose only sheet has no rows."""
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _import(client: AsyncClient, headers: dict, name: str, content: bytes) -> str:
    response = await client.post(
        "/api/document-store/import",
        headers=headers,
        files={"file": (name, content, "application/octet-stream")},
        data={
            "template_id": "PERSON",
            "column_mapping": json.dumps({"a": "first_name"}),
            "namespace": "wip",
        },
    )
    assert response.status_code == 422, response.text
    return response.json()["detail"]


@pytest.mark.asyncio
async def test_parser_refusal_is_answered_with_its_message(client: AsyncClient, auth_headers: dict):
    assert await _import(client, auth_headers, "empty.xlsx", _empty_xlsx()) == "Empty spreadsheet"


@pytest.mark.asyncio
async def test_unreadable_file_is_answered_as_a_parse_failure(client: AsyncClient, auth_headers: dict):
    detail = await _import(client, auth_headers, "broken.xlsx", b"this is not a zip archive")
    assert detail.startswith("Failed to parse file: ")


def _zip_that_is_no_workbook() -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "not a workbook")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_a_zip_that_is_no_workbook_is_a_parse_failure(client: AsyncClient, auth_headers: dict):
    """openpyxl raises KeyError for a zip without the workbook's parts."""
    detail = await _import(client, auth_headers, "other.xlsx", _zip_that_is_no_workbook())
    assert detail.startswith("Failed to parse file: ")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/document-store/import", "/api/document-store/import/preview"])
async def test_a_fault_of_ours_while_parsing_is_a_500_not_a_bad_file(
    client: AsyncClient, auth_headers: dict, monkeypatch, path,
):
    """Only what a bad upload raises is a 422 (CASE-1251, review of 7754542d,
    R1): a bug in the parser must not read as the caller's file."""
    from document_store.services.import_service import ImportService

    def broken(*_args, **_kwargs):
        raise AttributeError("a bug in the parser")

    monkeypatch.setattr(ImportService, "preview", staticmethod(broken))
    monkeypatch.setattr(ImportService, "parse_rows", staticmethod(broken))
    # The test client re-raises what the app does not handle; served, it is
    # a 500. Before, it was caught and answered 422 "Failed to parse file".
    with pytest.raises(AttributeError, match="a bug in the parser"):
        await client.post(
            path,
            headers=auth_headers,
            files={"file": ("ok.csv", b"a\n1\n", "text/csv")},
            data={"template_id": "PERSON", "column_mapping": json.dumps({"a": "first_name"}),
                  "namespace": "wip"},
        )
