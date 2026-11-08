"""CSV export escapes spreadsheet formulas; CSV import reverses it.

The table view's CSV export writes document values and field names with
wip_auth.csv_cells, and the file import undoes it, so an exported file
imports back as the values it held. wip-archive ships its own copy of the
rule (a wheel without wip-auth); this suite holds the two copies to the
same behaviour.
"""

import csv
import io

import pytest
from wip_archive import csv_cells as archive_cells

from document_store.services.import_service import ImportService
from wip_auth import csv_cells as auth_cells

API = "/api/document-store"
CORPUS = [
    "=1+1", "+1", "-1", "@x", "\tx", "\rx", "'=1", "''", "'", "'90s",
    "plain", "", "a=b", -4, 2.5, None, True,
]


@pytest.mark.parametrize("value", CORPUS)
def test_wip_archive_and_wip_auth_escape_alike(value):
    assert archive_cells.escape_cell(value) == auth_cells.escape_cell(value)
    assert archive_cells.unescape_cell(value) == auth_cells.unescape_cell(value)
    assert archive_cells.FORMULA_LEADERS == auth_cells.FORMULA_LEADERS


@pytest.mark.asyncio
async def test_table_export_escapes_and_import_restores(client, auth_headers):
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=[{
        "namespace": "wip", "template_id": "PERSON",
        "data": {"national_id": "114500001", "first_name": "=HYPERLINK(\"x\")",
                 "last_name": "-Minus", "age": 41},
    }])
    assert resp.json()["results"][0]["status"] == "created", resp.text

    export = await client.get(f"{API}/table/PERSON/csv", headers=auth_headers)
    assert export.status_code == 200, export.text
    rows = list(csv.DictReader(io.StringIO(export.text)))
    row = next(r for r in rows if r.get("national_id") == "114500001")
    assert row["first_name"] == "'=HYPERLINK(\"x\")"
    assert row["last_name"] == "'-Minus"
    assert row["age"] == "41"

    _, imported = ImportService.parse_rows(export.text.encode(), "people.csv")
    back = next(r for r in imported if r.get("national_id") == "114500001")
    assert back["first_name"] == "=HYPERLINK(\"x\")"
    assert back["last_name"] == "-Minus"
