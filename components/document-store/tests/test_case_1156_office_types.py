"""A file field and the file list use the allowlist's matching rule (CASE-1156).

The field-level allowed_types check and the list filter each had their own
copy of the matcher, which read a pattern ending in ".*" as an exact type.
"""

import pytest

from document_store.models.file import FileStatus
from document_store.services.file_service import get_file_service
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
OOXML = "application/vnd.openxmlformats-officedocument.*"


@pytest.mark.asyncio
async def test_a_field_allowing_the_ooxml_family_accepts_a_docx(file_client):  # noqa: F811
    doc = await _insert_file(content_type=DOCX, status=FileStatus.ORPHAN)

    ok, error, _ref = await get_file_service().validate_file_for_field(
        doc.file_id, allowed_types=[OOXML], max_size_mb=10, template_id="any",
    )

    assert ok, error


@pytest.mark.asyncio
async def test_listing_by_a_dotted_prefix_finds_the_family(file_client, auth_headers):  # noqa: F811
    await _insert_file(content_type=DOCX)
    await _insert_file(content_type=XLSX)
    await _insert_file(content_type="application/pdf")

    resp = await file_client.get(
        "/api/document-store/files", headers=auth_headers, params={"content_type": OOXML},
    )

    assert resp.status_code == 200, resp.text
    assert sorted(f["content_type"] for f in resp.json()["items"]) == [XLSX, DOCX]
