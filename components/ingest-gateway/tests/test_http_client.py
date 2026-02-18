"""Tests for ingest gateway HTTP client.

Covers the bulk-first payload wrapping, path parameter extraction,
BulkResponse status detection, and error handling.
"""

import pytest
from unittest.mock import AsyncMock

import httpx

from ingest_gateway.http_client import IngestHTTPClient, ACTION_ENDPOINTS
from ingest_gateway.models import IngestAction, IngestResultStatus

from conftest import make_mock_response, make_bulk_response


# ---- Payload wrapping: single-entity actions ----

class TestSingleEntityWrapping:
    """All single-entity actions wrap the payload dict as [dict]."""

    @pytest.mark.asyncio
    async def test_terminology_create_wraps_in_list(self, http_client):
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "T-001"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        payload = {"value": "GENDER", "label": "Gender"}
        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE, payload, "corr-1"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == [payload]
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_template_create_wraps_in_list(self, http_client):
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "TPL-001"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        payload = {"value": "MY_TPL", "label": "My Template", "fields": []}
        result = await http_client.forward_request(
            IngestAction.TEMPLATES_CREATE, payload, "corr-2"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == [payload]
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_document_create_wraps_in_list(self, http_client):
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "DOC-001"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        payload = {"template_id": "TPL-001", "data": {"name": "Test"}}
        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_CREATE, payload, "corr-3"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == [payload]
        assert result.status == IngestResultStatus.SUCCESS


# ---- Payload wrapping: bulk actions with payload_key ----

class TestBulkPayloadExtraction:
    """Bulk actions extract the list from payload[key]."""

    @pytest.mark.asyncio
    async def test_terms_bulk_extracts_terms_list(self, http_client):
        terms = [
            {"value": "active", "label": "Active"},
            {"value": "pending", "label": "Pending"},
        ]
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "T-001"},
            {"index": 1, "status": "created", "id": "T-002"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        payload = {"terminology_id": "TERM-ABC", "terms": terms}
        result = await http_client.forward_request(
            IngestAction.TERMS_BULK, payload, "corr-4"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == terms
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_templates_bulk_extracts_templates_list(self, http_client):
        templates = [
            {"value": "TPL_1", "label": "Template 1", "fields": []},
            {"value": "TPL_2", "label": "Template 2", "fields": []},
        ]
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "TPL-001"},
            {"index": 1, "status": "created", "id": "TPL-002"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.TEMPLATES_BULK, {"templates": templates}, "corr-5"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == templates
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_documents_bulk_extracts_documents_list(self, http_client):
        docs = [
            {"template_id": "TPL-001", "data": {"name": "Doc 1"}},
            {"template_id": "TPL-001", "data": {"name": "Doc 2"}},
        ]
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "DOC-001"},
            {"index": 1, "status": "created", "id": "DOC-002"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_BULK, {"documents": docs}, "corr-6"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == docs
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_missing_payload_key_sends_empty_list(self, http_client):
        """If the expected key is absent, sends empty list."""
        bulk_resp = make_bulk_response([], total=0, succeeded=0, failed=0)
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        # TEMPLATES_BULK expects "templates" key, but we provide "items"
        result = await http_client.forward_request(
            IngestAction.TEMPLATES_BULK, {"items": [{"value": "X"}]}, "corr-7"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        assert sent_body == []


# ---- Path parameter extraction ----

class TestPathParameters:

    @pytest.mark.asyncio
    async def test_terminology_id_in_url(self, http_client):
        """terminology_id is extracted from payload and substituted in URL."""
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "T-001"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        payload = {
            "terminology_id": "my-term-id-123",
            "terms": [{"value": "test", "label": "Test"}],
        }
        await http_client.forward_request(
            IngestAction.TERMS_BULK, payload, "corr-8"
        )

        url = http_client._client.request.call_args.kwargs["url"]
        assert "/terminologies/my-term-id-123/terms" in url

    @pytest.mark.asyncio
    async def test_missing_path_param_returns_error(self, http_client):
        """Missing required path parameter returns FAILED."""
        payload = {"terms": [{"value": "test"}]}
        result = await http_client.forward_request(
            IngestAction.TERMS_BULK, payload, "corr-9"
        )

        assert result.status == IngestResultStatus.FAILED
        assert "Missing required path parameter" in result.error
        assert "terminology_id" in result.error

    @pytest.mark.asyncio
    async def test_path_param_not_in_request_body(self, http_client):
        """Extracted path params must not leak into the request body."""
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "T-001"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        payload = {
            "terminology_id": "TERM-XYZ",
            "terms": [{"value": "a", "label": "A"}],
        }
        await http_client.forward_request(
            IngestAction.TERMS_BULK, payload, "corr-10"
        )

        sent_body = http_client._client.request.call_args.kwargs["json"]
        # terms list should not contain the extracted path param
        for item in sent_body:
            assert "terminology_id" not in item

    @pytest.mark.asyncio
    async def test_original_payload_not_mutated(self, http_client):
        """Path param extraction must not mutate the caller's dict."""
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "T-001"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        original = {
            "terminology_id": "TERM-123",
            "terms": [{"value": "x", "label": "X"}],
        }
        snapshot = original.copy()

        await http_client.forward_request(
            IngestAction.TERMS_BULK, original, "corr-11"
        )

        assert original == snapshot


# ---- BulkResponse status detection ----

class TestBulkResponseStatus:

    @pytest.mark.asyncio
    async def test_all_succeeded_is_success(self, http_client):
        bulk_resp = make_bulk_response(
            [{"index": 0, "status": "created", "id": "X"}],
            total=1, succeeded=1, failed=0,
        )
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "TEST", "label": "Test"},
            "corr-20",
        )
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_all_failed_is_failed(self, http_client):
        """When all items fail in BulkResponse → FAILED (not PARTIAL)."""
        bulk_resp = make_bulk_response(
            [{"index": 0, "status": "error", "error": "duplicate"}],
            total=1, succeeded=0, failed=1,
        )
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "DUPE", "label": "Duplicate"},
            "corr-21",
        )
        assert result.status == IngestResultStatus.FAILED

    @pytest.mark.asyncio
    async def test_partial_failure_is_partial(self, http_client):
        """When some items fail in BulkResponse → PARTIAL."""
        bulk_resp = make_bulk_response(
            [
                {"index": 0, "status": "created", "id": "T-1"},
                {"index": 1, "status": "error", "error": "duplicate"},
            ],
            total=2, succeeded=1, failed=1,
        )
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_BULK,
            {"documents": [{"data": {}}, {"data": {}}]},
            "corr-22",
        )
        assert result.status == IngestResultStatus.PARTIAL

    @pytest.mark.asyncio
    async def test_single_create_all_failed_is_failed(self, http_client):
        """Single-entity create that fails in BulkResponse → FAILED.

        This is the key scenario: duplicates and bad references return
        HTTP 200 but with failed=1. The client must detect this.
        """
        bulk_resp = make_bulk_response(
            [{"index": 0, "status": "error", "error": "already exists"}],
            total=1, succeeded=0, failed=1,
        )
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "EXISTING", "label": "Exists"},
            "corr-23",
        )
        assert result.status == IngestResultStatus.FAILED
        assert result.http_status_code == 200  # HTTP was fine, error is per-item

    @pytest.mark.asyncio
    async def test_empty_bulk_response_is_success(self, http_client):
        """Empty results (0 total, 0 failed) → SUCCESS."""
        bulk_resp = make_bulk_response([], total=0, succeeded=0, failed=0)
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.TEMPLATES_BULK,
            {"templates": []},
            "corr-24",
        )
        assert result.status == IngestResultStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_non_json_response_is_success(self, http_client):
        """Non-JSON 200 response doesn't crash status detection."""
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, None, text="OK")
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "X", "label": "X"},
            "corr-25",
        )
        # response_data = {"raw": "OK"} which has no "failed" key
        assert result.status == IngestResultStatus.SUCCESS


# ---- HTTP error handling ----

class TestHTTPErrors:

    @pytest.mark.asyncio
    async def test_422_validation_error(self, http_client):
        error_detail = [{"msg": "field required", "loc": ["body", "value"]}]
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(422, {"detail": error_detail})
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"label": "Missing Value"},
            "corr-30",
        )
        assert result.status == IngestResultStatus.FAILED
        assert result.http_status_code == 422
        assert "422" in result.error

    @pytest.mark.asyncio
    async def test_401_auth_error(self, http_client):
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(401, {"detail": "Invalid API key"})
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "X", "label": "X"},
            "corr-31",
        )
        assert result.status == IngestResultStatus.FAILED
        assert result.http_status_code == 401

    @pytest.mark.asyncio
    async def test_500_server_error(self, http_client):
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(500, {"detail": "Internal error"})
        )

        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_CREATE,
            {"template_id": "X", "data": {}},
            "corr-32",
        )
        assert result.status == IngestResultStatus.FAILED
        assert result.http_status_code == 500

    @pytest.mark.asyncio
    async def test_timeout(self, http_client):
        http_client._client.request = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_CREATE,
            {"template_id": "X", "data": {}},
            "corr-33",
        )
        assert result.status == IngestResultStatus.FAILED
        assert "Timeout" in result.error
        assert result.http_status_code is None

    @pytest.mark.asyncio
    async def test_connection_error(self, http_client):
        http_client._client.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_CREATE,
            {"template_id": "X", "data": {}},
            "corr-34",
        )
        assert result.status == IngestResultStatus.FAILED
        assert "Connection error" in result.error

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, http_client):
        http_client._client.request = AsyncMock(
            side_effect=RuntimeError("something unexpected")
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "X", "label": "X"},
            "corr-35",
        )
        assert result.status == IngestResultStatus.FAILED
        assert "something unexpected" in result.error


# ---- Response data passthrough ----

class TestResponseData:

    @pytest.mark.asyncio
    async def test_response_data_included_on_success(self, http_client):
        bulk_resp = make_bulk_response([
            {"index": 0, "status": "created", "id": "ABC-123"},
        ])
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(200, bulk_resp)
        )

        result = await http_client.forward_request(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "X", "label": "X"},
            "corr-40",
        )
        assert result.response == bulk_resp
        assert result.response["results"][0]["id"] == "ABC-123"

    @pytest.mark.asyncio
    async def test_response_data_included_on_error(self, http_client):
        error_data = {"detail": "Not found"}
        http_client._client.request = AsyncMock(
            return_value=make_mock_response(404, error_data)
        )

        result = await http_client.forward_request(
            IngestAction.DOCUMENTS_CREATE,
            {"template_id": "X", "data": {}},
            "corr-41",
        )
        assert result.response == error_data


# ---- Endpoint config consistency ----

class TestEndpointConfig:

    def test_all_configs_have_required_keys(self):
        """Every endpoint config must have base_url_attr, path, and method."""
        for action, config in ACTION_ENDPOINTS.items():
            assert "base_url_attr" in config, f"{action}: missing base_url_attr"
            assert "path" in config, f"{action}: missing path"
            assert "method" in config, f"{action}: missing method"

    def test_create_actions_have_no_payload_key(self):
        """Single-entity create actions should NOT have payload_key."""
        for action in [
            IngestAction.TERMINOLOGIES_CREATE,
            IngestAction.TEMPLATES_CREATE,
            IngestAction.DOCUMENTS_CREATE,
        ]:
            assert "payload_key" not in ACTION_ENDPOINTS[action]

    def test_bulk_actions_have_payload_key(self):
        """Bulk actions must have payload_key to extract the list."""
        for action in [
            IngestAction.TERMS_BULK,
            IngestAction.TEMPLATES_BULK,
            IngestAction.DOCUMENTS_BULK,
        ]:
            assert "payload_key" in ACTION_ENDPOINTS[action]

    def test_create_and_bulk_share_same_path(self):
        """CREATE and BULK variants should target the same endpoint."""
        assert (
            ACTION_ENDPOINTS[IngestAction.TEMPLATES_CREATE]["path"]
            == ACTION_ENDPOINTS[IngestAction.TEMPLATES_BULK]["path"]
        )
        assert (
            ACTION_ENDPOINTS[IngestAction.DOCUMENTS_CREATE]["path"]
            == ACTION_ENDPOINTS[IngestAction.DOCUMENTS_BULK]["path"]
        )
