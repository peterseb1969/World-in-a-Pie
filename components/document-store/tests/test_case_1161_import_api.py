"""The import route's answers for a file it cannot read.

The import tests cover every branch of POST /import except a file that fails
to parse. Both outcomes are pinned here: a parser refusal (ValueError) is
answered with its own message, and any other parse failure is answered as
"Failed to parse file: ..." — in both cases a 422, before any template lookup
or document write.
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
