"""Unit tests for WipClient methods.

Tests the new client methods for file upload, import, replay, and reporting.
Uses unittest.mock to mock the underlying httpx.AsyncClient.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wip_mcp.client import BulkError, WipClient


def _make_client(**kwargs) -> WipClient:
    """Create a WipClient with test defaults."""
    defaults = {
        "registry_url": "http://test:8001",
        "def_store_url": "http://test:8002",
        "template_store_url": "http://test:8003",
        "document_store_url": "http://test:8004",
        "reporting_sync_url": "http://test:8005",
        "api_key": "test_key",
    }
    defaults.update(kwargs)
    return WipClient(**defaults)


def _mock_response(json_data=None, status_code=200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def _mock_http(response=None):
    """Create a mock httpx.AsyncClient."""
    if response is None:
        response = _mock_response()
    mock = AsyncMock()
    mock.get.return_value = response
    mock.post.return_value = response
    mock.put.return_value = response
    mock.request.return_value = response  # used by _delete
    return mock


# =========================================================================
# upload_file — multipart POST
# =========================================================================


@pytest.mark.asyncio
async def test_upload_file_basic():
    """upload_file sends multipart POST with correct URL and form data."""
    expected = {"file_id": "FILE-001", "filename": "test.txt", "size": 5}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.upload_file(
            file_content=b"hello",
            filename="test.txt",
            content_type="text/plain",
            namespace="wip",
        )

    assert result == expected
    mock_http.post.assert_awaited_once()
    call_kwargs = mock_http.post.call_args
    assert "/api/document-store/files" in call_kwargs.args[0]
    assert call_kwargs.kwargs["files"]["file"][0] == "test.txt"
    assert call_kwargs.kwargs["data"]["namespace"] == "wip"


@pytest.mark.asyncio
async def test_upload_file_with_optional_fields():
    """upload_file includes description, tags, and category when provided."""
    mock_http = _mock_http(_mock_response({"file_id": "FILE-002"}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.upload_file(
            file_content=b"data",
            filename="report.pdf",
            content_type="application/pdf",
            namespace="wip",
            description="Annual report",
            tags=["finance", "2025"],
            category="reports",
        )

    data = mock_http.post.call_args.kwargs["data"]
    assert data["description"] == "Annual report"
    assert data["tags"] == "finance,2025"
    assert data["category"] == "reports"


@pytest.mark.asyncio
async def test_upload_file_uses_api_key_header():
    """upload_file overrides Content-Type header with just X-API-Key."""
    mock_http = _mock_http(_mock_response({"file_id": "FILE-003"}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.upload_file(
            file_content=b"x",
            filename="x.bin",
            content_type="application/octet-stream",
            namespace="wip",
        )

    headers = mock_http.post.call_args.kwargs["headers"]
    assert headers == {"X-API-Key": "test_key"}
    # Should NOT have Content-Type (multipart sets its own)
    assert "Content-Type" not in headers


# =========================================================================
# preview_import — multipart POST
# =========================================================================


@pytest.mark.asyncio
async def test_preview_import():
    """preview_import sends multipart POST with file content."""
    expected = {
        "headers": ["name", "email"],
        "sample_rows": [{"name": "Alice", "email": "a@b.com"}],
        "total_rows": 1,
        "format": "csv",
    }
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.preview_import(
            file_content=b"name,email\nAlice,a@b.com\n",
            filename="data.csv",
        )

    assert result == expected
    mock_http.post.assert_awaited_once()
    call_kwargs = mock_http.post.call_args
    assert "/api/document-store/import/preview" in call_kwargs.args[0]
    assert call_kwargs.kwargs["files"]["file"][0] == "data.csv"


# =========================================================================
# import_documents — multipart POST
# =========================================================================


@pytest.mark.asyncio
async def test_import_documents():
    """import_documents sends file, template_id, column_mapping, and options."""
    expected = {"total_rows": 2, "succeeded": 2, "failed": 0}
    mock_http = _mock_http(_mock_response(expected))

    mapping = {"name": "name", "email": "email"}
    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.import_documents(
            file_content=b"csv-data",
            filename="import.csv",
            template_id="0190c000-0000-7000-0000-000000000001",
            column_mapping=mapping,
            namespace="test-ns",
            skip_errors=True,
        )

    assert result == expected
    mock_http.post.assert_awaited_once()
    call_kwargs = mock_http.post.call_args
    assert "/api/document-store/import" in call_kwargs.args[0]

    data = call_kwargs.kwargs["data"]
    assert data["template_id"] == "0190c000-0000-7000-0000-000000000001"
    assert json.loads(data["column_mapping"]) == mapping
    assert data["namespace"] == "test-ns"
    assert data["skip_errors"] == "true"


@pytest.mark.asyncio
async def test_import_documents_default_options():
    """import_documents uses default namespace and skip_errors=false."""
    mock_http = _mock_http(_mock_response({"total_rows": 0}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.import_documents(
            file_content=b"data",
            filename="f.csv",
            template_id="0190c000-0000-7000-0000-000000000002",
            column_mapping={},
            namespace="wip",
        )

    data = mock_http.post.call_args.kwargs["data"]
    assert data["namespace"] == "wip"
    assert data["skip_errors"] == "false"


# =========================================================================
# start_replay — POST
# =========================================================================


@pytest.mark.asyncio
async def test_start_replay():
    """start_replay sends POST with filter, throttle, and batch_size."""
    expected = {"session_id": "sess-001", "status": "running", "total_count": 100}
    resp = _mock_response(expected)
    mock_http = _mock_http(resp)

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.start_replay(
            filter_config={"template_value": "PATIENT"},
            throttle_ms=50,
            batch_size=200,
        )

    assert result == expected
    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert "/api/document-store/replay/start" in call_args.args[0]
    body = call_args.kwargs["json"]
    assert body["filter"] == {"template_value": "PATIENT"}
    assert body["throttle_ms"] == 50
    assert body["batch_size"] == 200


@pytest.mark.asyncio
async def test_start_replay_defaults():
    """start_replay uses empty filter and default throttle/batch when not provided."""
    mock_http = _mock_http(_mock_response({"session_id": "s1"}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.start_replay()

    body = mock_http.post.call_args.kwargs["json"]
    assert body["filter"] == {}
    assert body["throttle_ms"] == 10
    assert body["batch_size"] == 100


# =========================================================================
# get_replay_session — GET
# =========================================================================


@pytest.mark.asyncio
async def test_get_replay_session():
    """get_replay_session sends GET with session_id in URL."""
    expected = {"session_id": "sess-001", "status": "completed", "published": 100}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.get_replay_session("sess-001")

    assert result == expected
    mock_http.get.assert_awaited_once()
    url = mock_http.get.call_args.args[0]
    assert url == "http://test:8004/api/document-store/replay/sess-001"


# =========================================================================
# pause_replay — POST
# =========================================================================


@pytest.mark.asyncio
async def test_pause_replay():
    """pause_replay sends POST to session_id/pause."""
    expected = {"session_id": "sess-001", "status": "paused"}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.pause_replay("sess-001")

    assert result == expected
    mock_http.post.assert_awaited_once()
    url = mock_http.post.call_args.args[0]
    assert url == "http://test:8004/api/document-store/replay/sess-001/pause"


# =========================================================================
# resume_replay — POST
# =========================================================================


@pytest.mark.asyncio
async def test_resume_replay():
    """resume_replay sends POST to session_id/resume."""
    expected = {"session_id": "sess-001", "status": "running"}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.resume_replay("sess-001")

    assert result == expected
    url = mock_http.post.call_args.args[0]
    assert url == "http://test:8004/api/document-store/replay/sess-001/resume"


# =========================================================================
# cancel_replay — DELETE
# =========================================================================


@pytest.mark.asyncio
async def test_cancel_replay():
    """cancel_replay sends DELETE to session_id URL."""
    expected = {"session_id": "sess-001", "status": "cancelled"}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.cancel_replay("sess-001")

    assert result == expected
    # _delete uses client.request("DELETE", ...)
    mock_http.request.assert_awaited_once()
    args = mock_http.request.call_args.args
    assert args[0] == "DELETE"
    assert args[1] == "http://test:8004/api/document-store/replay/sess-001"


# =========================================================================
# list_report_tables — GET
# =========================================================================


@pytest.mark.asyncio
async def test_list_report_tables():
    """list_report_tables sends GET to /tables endpoint."""
    expected = {
        "tables": [
            {"name": "doc_patient", "columns": ["id", "name"], "row_count": 42}
        ]
    }
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.list_report_tables()

    assert result == expected
    mock_http.get.assert_awaited_once()
    url = mock_http.get.call_args.args[0]
    assert url == "http://test:8005/api/reporting-sync/tables"


# =========================================================================
# run_report_query — POST
# =========================================================================


@pytest.mark.asyncio
async def test_run_report_query():
    """run_report_query sends POST with SQL, params, timeout, and max_rows."""
    expected = {
        "columns": ["name"],
        "rows": [{"name": "Alice"}],
        "row_count": 1,
        "truncated": False,
    }
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.run_report_query(
            sql="SELECT name FROM doc_patient WHERE id = $1",
            params=["0190d000-0000-7000-0000-000000000001"],
            timeout_seconds=15,
            max_rows=500,
        )

    assert result == expected
    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert "/api/reporting-sync/query" in call_args.args[0]
    body = call_args.kwargs["json"]
    assert body["sql"] == "SELECT name FROM doc_patient WHERE id = $1"
    assert body["params"] == ["0190d000-0000-7000-0000-000000000001"]
    assert body["timeout_seconds"] == 15
    assert body["max_rows"] == 500


@pytest.mark.asyncio
async def test_run_report_query_defaults():
    """run_report_query uses default params, timeout, and max_rows."""
    mock_http = _mock_http(_mock_response({"rows": []}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.run_report_query(sql="SELECT 1")

    body = mock_http.post.call_args.kwargs["json"]
    assert body["params"] == []
    assert body["timeout_seconds"] == 30
    assert body["max_rows"] == 1000


# =========================================================================
# get_template_by_value — GET
# =========================================================================


@pytest.mark.asyncio
async def test_get_template_by_value():
    """get_template_by_value sends GET with value in URL path."""
    expected = {"template_id": "0190c000-0000-7000-0000-000000000001", "value": "PATIENT", "version": 1}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.get_template_by_value("PATIENT")

    assert result == expected
    mock_http.get.assert_awaited_once()
    url = mock_http.get.call_args.args[0]
    assert url == "http://test:8003/api/template-store/templates/by-value/PATIENT"


@pytest.mark.asyncio
async def test_get_template_by_value_with_namespace():
    """get_template_by_value passes namespace as query param."""
    mock_http = _mock_http(_mock_response({"template_id": "0190c000-0000-7000-0000-000000000002"}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.get_template_by_value("PATIENT", namespace="custom-ns")

    params = mock_http.get.call_args.kwargs["params"]
    assert params == {"namespace": "custom-ns"}


# =========================================================================
# Edge cases / helpers
# =========================================================================


@pytest.mark.asyncio
async def test_unwrap_single_raises_on_error():
    """_unwrap_single raises BulkError when result status is error."""
    client = _make_client()
    with pytest.raises(BulkError, match="Duplicate entry"):
        client._unwrap_single(
            {"results": [{"index": 0, "status": "error", "error": "Duplicate entry"}]}
        )


@pytest.mark.asyncio
async def test_unwrap_bulk_returns_summary():
    """_unwrap_bulk returns structured summary."""
    client = _make_client()
    result = client._unwrap_bulk(
        {"results": [{"status": "created"}], "total": 1, "succeeded": 1, "failed": 0}
    )
    assert result["total"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert len(result["results"]) == 1
