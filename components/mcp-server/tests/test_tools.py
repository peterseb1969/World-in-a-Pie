"""Tests for MCP server tools — new functionality.

Tests the tool functions directly by mocking the WipClient.
"""

import json
import os

# Mock yaml before importing server (it may not be installed in test env)
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("yaml", MagicMock())

from wip_mcp.client import WipClient  # noqa: E402
from wip_mcp.server import (  # noqa: E402
    cancel_replay,
    get_replay_status,
    get_template_fields,
    import_documents_csv,
    list_report_tables,
    query_by_template,
    run_report_query,
    start_replay,
    upload_file,
)


def _mock_client():
    """Create a mock WipClient."""
    return AsyncMock(spec=WipClient)


# =========================================================================
# P1: File Upload
# =========================================================================


@pytest.mark.asyncio
async def test_upload_file_success():
    """Upload a local file via MCP tool."""
    mock = _mock_client()
    mock.upload_file.return_value = {
        "file_id": "FILE-000001",
        "filename": "test.txt",
        "content_type": "text/plain",
        "size": 13,
    }

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"Hello, World!")
        tmp_path = f.name

    try:
        with patch("wip_mcp.server.get_client", return_value=mock):
            result = await upload_file(file_path=tmp_path, namespace="wip")

        data = json.loads(result)
        assert data["file_id"] == "FILE-000001"
        mock.upload_file.assert_awaited_once()
        call_kwargs = mock.upload_file.call_args.kwargs
        assert call_kwargs["filename"] == os.path.basename(tmp_path)
        assert call_kwargs["content_type"] == "text/plain"
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_upload_file_not_found():
    """Upload non-existent file returns error."""
    with patch("wip_mcp.server.get_client", return_value=_mock_client()):
        result = await upload_file(file_path="/nonexistent/file.txt")

    assert "not found" in result.lower()


# =========================================================================
# P3: Template-Aware Query
# =========================================================================


@pytest.mark.asyncio
async def test_get_template_fields():
    """Get template fields returns clean summary."""
    mock = _mock_client()
    mock.get_template_by_value.return_value = {
        "template_id": "TPL-001",
        "value": "PATIENT",
        "version": 1,
        "namespace": "wip",
        "identity_fields": ["email"],
        "fields": [
            {"name": "name", "type": "string", "mandatory": True},
            {"name": "email", "type": "string", "mandatory": True, "semantic_type": "email"},
            {"name": "country", "type": "term", "mandatory": False, "terminology_ref": "COUNTRY"},
        ],
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_template_fields(template_value="PATIENT")

    data = json.loads(result)
    assert data["template_id"] == "TPL-001"
    assert len(data["fields"]) == 3
    # Check that optional keys are only present when needed
    country_field = next(f for f in data["fields"] if f["name"] == "country")
    assert "terminology_ref" in country_field
    name_field = next(f for f in data["fields"] if f["name"] == "name")
    assert "terminology_ref" not in name_field


@pytest.mark.asyncio
async def test_query_by_template_auto_prefix():
    """query_by_template auto-prefixes field names with data."""
    mock = _mock_client()
    mock.get_template_by_value.return_value = {"template_id": "TPL-001"}
    mock.query_documents.return_value = {"items": [], "total": 0}

    with patch("wip_mcp.server.get_client", return_value=mock):
        await query_by_template(
            template_value="PATIENT",
            field_filters=[{"field": "country", "operator": "eq", "value": "CH"}],
        )

    # Verify the filter was auto-prefixed
    call_args = mock.query_documents.call_args
    query = call_args.args[0] if call_args.args else call_args.kwargs.get("filters", {})
    filters = query.get("filters", [])
    assert len(filters) == 1
    assert filters[0]["field"] == "data.country"


@pytest.mark.asyncio
async def test_query_by_template_no_double_prefix():
    """query_by_template doesn't double-prefix data.* fields."""
    mock = _mock_client()
    mock.get_template_by_value.return_value = {"template_id": "TPL-001"}
    mock.query_documents.return_value = {"items": [], "total": 0}

    with patch("wip_mcp.server.get_client", return_value=mock):
        await query_by_template(
            template_value="PATIENT",
            field_filters=[{"field": "data.country", "operator": "eq", "value": "CH"}],
        )

    call_args = mock.query_documents.call_args
    query = call_args.args[0] if call_args.args else call_args.kwargs.get("filters", {})
    filters = query.get("filters", [])
    assert filters[0]["field"] == "data.country"  # not data.data.country


# =========================================================================
# P4: Reporting SQL Query
# =========================================================================


@pytest.mark.asyncio
async def test_list_report_tables():
    """list_report_tables returns table info."""
    mock = _mock_client()
    mock.list_report_tables.return_value = {
        "tables": [
            {"name": "doc_patient", "columns": [{"name": "id", "type": "text"}], "row_count": 10}
        ]
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await list_report_tables()

    data = json.loads(result)
    assert len(data["tables"]) == 1
    assert data["tables"][0]["name"] == "doc_patient"


@pytest.mark.asyncio
async def test_run_report_query():
    """run_report_query returns SQL results."""
    mock = _mock_client()
    mock.run_report_query.return_value = {
        "columns": ["name", "country"],
        "rows": [{"name": "Alice", "country": "CH"}],
        "row_count": 1,
        "truncated": False,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await run_report_query(
            sql="SELECT name, country FROM doc_patient WHERE country = $1",
            params=["CH"],
        )

    data = json.loads(result)
    assert data["row_count"] == 1
    assert data["rows"][0]["country"] == "CH"
    mock.run_report_query.assert_awaited_once_with(
        sql="SELECT name, country FROM doc_patient WHERE country = $1",
        params=["CH"],
        max_rows=1000,
    )


# =========================================================================
# P5: Replay
# =========================================================================


@pytest.mark.asyncio
async def test_start_replay():
    """start_replay passes filter to client."""
    mock = _mock_client()
    mock.start_replay.return_value = {
        "session_id": "abc123",
        "status": "running",
        "total_count": 50,
        "published": 0,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await start_replay(template_value="PATIENT", namespace="wip")

    data = json.loads(result)
    assert data["session_id"] == "abc123"
    call_kwargs = mock.start_replay.call_args.kwargs
    assert call_kwargs["filter_config"]["template_value"] == "PATIENT"


@pytest.mark.asyncio
async def test_get_replay_status():
    """get_replay_status returns session info."""
    mock = _mock_client()
    mock.get_replay_session.return_value = {
        "session_id": "abc123",
        "status": "completed",
        "total_count": 50,
        "published": 50,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_replay_status(session_id="abc123")

    data = json.loads(result)
    assert data["status"] == "completed"
    assert data["published"] == 50


@pytest.mark.asyncio
async def test_cancel_replay():
    """cancel_replay calls client cancel."""
    mock = _mock_client()
    mock.cancel_replay.return_value = {
        "session_id": "abc123",
        "status": "cancelled",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await cancel_replay(session_id="abc123")

    data = json.loads(result)
    assert data["status"] == "cancelled"
    mock.cancel_replay.assert_awaited_once_with("abc123")


# =========================================================================
# P2: CSV/XLSX Import
# =========================================================================


@pytest.mark.asyncio
async def test_import_documents_csv_success():
    """import_documents_csv reads file and calls client."""
    mock = _mock_client()
    mock.get_template_by_value.return_value = {"template_id": "TPL-001"}
    mock.import_documents.return_value = {
        "total_rows": 2,
        "succeeded": 2,
        "failed": 0,
        "skipped": 0,
        "results": [
            {"row": 2, "document_id": "DOC-001", "version": 1, "is_new": True},
            {"row": 3, "document_id": "DOC-002", "version": 1, "is_new": True},
        ],
        "errors": [],
    }

    csv_content = "name,email\nAlice,alice@test.com\nBob,bob@test.com\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        f.write(csv_content)
        tmp_path = f.name

    try:
        with patch("wip_mcp.server.get_client", return_value=mock):
            result = await import_documents_csv(
                file_path=tmp_path,
                template_value="PATIENT",
                column_mapping={"name": "name", "email": "email"},
            )

        data = json.loads(result)
        assert data["succeeded"] == 2
        mock.import_documents.assert_awaited_once()
        call_kwargs = mock.import_documents.call_args.kwargs
        assert call_kwargs["template_id"] == "TPL-001"
        assert call_kwargs["column_mapping"] == {"name": "name", "email": "email"}
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_import_documents_csv_not_found():
    """import_documents_csv returns error for missing file."""
    mock = _mock_client()
    mock.get_template_by_value.return_value = {"template_id": "TPL-001"}

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await import_documents_csv(
            file_path="/nonexistent/data.csv",
            template_value="PATIENT",
        )

    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_import_documents_csv_auto_mapping():
    """import_documents_csv with no mapping auto-maps columns to fields."""
    mock = _mock_client()
    mock.get_template_by_value.return_value = {
        "template_id": "TPL-001",
        "fields": [{"name": "name", "type": "string"}],
    }
    mock.preview_import.return_value = {
        "headers": ["name"],
        "sample_rows": [{"name": "Alice"}],
        "total_rows": 1,
        "format": "csv",
    }
    mock.import_documents.return_value = {
        "total_rows": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "results": [],
        "errors": [],
    }

    csv_content = "name\nAlice\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        f.write(csv_content)
        tmp_path = f.name

    try:
        with patch("wip_mcp.server.get_client", return_value=mock):
            result = await import_documents_csv(
                file_path=tmp_path,
                template_value="PATIENT",
                # No column_mapping — should auto-map
            )

        data = json.loads(result)
        assert data["succeeded"] == 1
    finally:
        os.unlink(tmp_path)
