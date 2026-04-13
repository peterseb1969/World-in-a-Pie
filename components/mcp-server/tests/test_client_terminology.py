"""Unit tests for WipClient terminology methods.

Tests the client methods directly by mocking the underlying httpx.AsyncClient.
Follows the pattern from test_client.py.
"""

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
    mock.request.return_value = response
    return mock


# =========================================================================
# validate_term — UUID vs non-UUID dispatch
# =========================================================================


@pytest.mark.asyncio
async def test_validate_term_with_uuid_sends_terminology_id():
    """validate_term with UUID sends {"terminology_id": uuid, "value": ...}."""
    uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    expected = {"valid": True, "term_id": "TRM-001"}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.validate_term(terminology=uuid, value="CH")

    assert result == expected
    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert "/api/def-store/validate" in call_args.args[0]
    body = call_args.kwargs["json"]
    assert body["terminology_id"] == uuid
    assert body["value"] == "CH"
    assert "terminology_value" not in body


@pytest.mark.asyncio
async def test_validate_term_with_non_uuid_sends_terminology_value():
    """validate_term with non-UUID value sends {"terminology_value": value, "value": ...}."""
    expected = {"valid": True, "term_id": "TRM-002"}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.validate_term(terminology="COUNTRY", value="GB")

    assert result == expected
    mock_http.post.assert_awaited_once()
    body = mock_http.post.call_args.kwargs["json"]
    assert body["terminology_value"] == "COUNTRY"
    assert body["value"] == "GB"
    assert "terminology_id" not in body


@pytest.mark.asyncio
async def test_validate_term_uppercase_uuid_detected():
    """validate_term with uppercase UUID still uses terminology_id key."""
    uuid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
    mock_http = _mock_http(_mock_response({"valid": True}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.validate_term(terminology=uuid, value="X")

    body = mock_http.post.call_args.kwargs["json"]
    assert body["terminology_id"] == uuid
    assert "terminology_value" not in body


@pytest.mark.asyncio
async def test_validate_term_partial_uuid_treated_as_value():
    """validate_term with partial UUID string uses terminology_value key."""
    not_uuid = "a1b2c3d4-e5f6"
    mock_http = _mock_http(_mock_response({"valid": False}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.validate_term(terminology=not_uuid, value="X")

    body = mock_http.post.call_args.kwargs["json"]
    assert body["terminology_value"] == not_uuid
    assert "terminology_id" not in body


@pytest.mark.asyncio
async def test_validate_term_snake_case_value():
    """validate_term with snake_case terminology name (like CT_AE_TERM_TEST) uses terminology_value."""
    mock_http = _mock_http(_mock_response({"valid": True}))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.validate_term(terminology="CT_AE_TERM_TEST", value="HEADACHE")

    body = mock_http.post.call_args.kwargs["json"]
    assert body["terminology_value"] == "CT_AE_TERM_TEST"
    assert body["value"] == "HEADACHE"


# =========================================================================
# create_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_create_terminology_sends_bulk_request():
    """create_terminology wraps single item in list and posts to /terminologies."""
    bulk_response = {
        "results": [
            {"index": 0, "status": "created", "terminology_id": "0190b000-0000-7000-0000-000000000001", "value": "COUNTRY"}
        ],
        "total": 1,
        "succeeded": 1,
        "failed": 0,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.create_terminology(
            value="COUNTRY", label="Country", namespace="wip"
        )

    assert result["terminology_id"] == "0190b000-0000-7000-0000-000000000001"
    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert "/api/def-store/terminologies" in call_args.args[0]
    body = call_args.kwargs["json"]
    assert body == [{"value": "COUNTRY", "label": "Country", "namespace": "wip"}]


@pytest.mark.asyncio
async def test_create_terminology_with_kwargs():
    """create_terminology passes extra kwargs (description, mutable) in payload."""
    bulk_response = {
        "results": [
            {"index": 0, "status": "created", "terminology_id": "0190b000-0000-7000-0000-000000000002"}
        ],
        "total": 1,
        "succeeded": 1,
        "failed": 0,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.create_terminology(
            value="GENDER",
            label="Gender",
            namespace="wip",
            description="Gender codes",
            mutable=True,
        )

    body = mock_http.post.call_args.kwargs["json"]
    assert body == [{
        "value": "GENDER",
        "label": "Gender",
        "namespace": "wip",
        "description": "Gender codes",
        "mutable": True,
    }]


# =========================================================================
# update_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_update_terminology_sends_bulk_put():
    """update_terminology sends PUT with terminology_id and updates."""
    bulk_response = {
        "results": [
            {"index": 0, "status": "updated", "terminology_id": "0190b000-0000-7000-0000-000000000001"}
        ],
        "total": 1,
        "succeeded": 1,
        "failed": 0,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.update_terminology(
            "0190b000-0000-7000-0000-000000000001", {"label": "Countries", "mutable": True}
        )

    assert result["terminology_id"] == "0190b000-0000-7000-0000-000000000001"
    mock_http.put.assert_awaited_once()
    body = mock_http.put.call_args.kwargs["json"]
    assert body == [{
        "terminology_id": "0190b000-0000-7000-0000-000000000001",
        "label": "Countries",
        "mutable": True,
    }]


# =========================================================================
# delete_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_delete_terminology_sends_delete_with_id():
    """delete_terminology sends DELETE with id in list."""
    bulk_response = {
        "results": [{"index": 0, "status": "deleted"}],
        "total": 1,
        "succeeded": 1,
        "failed": 0,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.delete_terminology("0190b000-0000-7000-0000-000000000001")

    mock_http.request.assert_awaited_once()
    args = mock_http.request.call_args.args
    assert args[0] == "DELETE"
    body = mock_http.request.call_args.kwargs["json"]
    assert body == [{"id": "0190b000-0000-7000-0000-000000000001"}]


@pytest.mark.asyncio
async def test_delete_terminology_with_force():
    """delete_terminology with force=True includes force in payload."""
    bulk_response = {
        "results": [{"index": 0, "status": "deleted"}],
        "total": 1,
        "succeeded": 1,
        "failed": 0,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.delete_terminology("0190b000-0000-7000-0000-000000000001", force=True)

    body = mock_http.request.call_args.kwargs["json"]
    assert body == [{"id": "0190b000-0000-7000-0000-000000000001", "force": True}]


# =========================================================================
# list_terms
# =========================================================================


@pytest.mark.asyncio
async def test_list_terms_sends_get_with_params():
    """list_terms sends GET to /terminologies/{id}/terms with query params."""
    expected = {"items": [{"term_id": "TRM-001"}], "total": 1}
    mock_http = _mock_http(_mock_response(expected))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.list_terms(
            terminology_id="0190b000-0000-7000-0000-000000000001", search="switz", page=2, page_size=25
        )

    assert result == expected
    mock_http.get.assert_awaited_once()
    url = mock_http.get.call_args.args[0]
    assert url == "http://test:8002/api/def-store/terminologies/0190b000-0000-7000-0000-000000000001/terms"


# =========================================================================
# create_terms
# =========================================================================


@pytest.mark.asyncio
async def test_create_terms_sends_post_to_terminology_terms():
    """create_terms sends POST to /terminologies/{id}/terms with terms list."""
    bulk_response = {
        "results": [
            {"index": 0, "status": "created", "term_id": "TRM-001"},
            {"index": 1, "status": "created", "term_id": "TRM-002"},
        ],
        "total": 2,
        "succeeded": 2,
        "failed": 0,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    terms = [
        {"value": "CH", "label": "Switzerland"},
        {"value": "GB", "label": "United Kingdom"},
    ]

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http):
        result = await client.create_terms(terminology_id="0190b000-0000-7000-0000-000000000001", terms=terms)

    assert result["succeeded"] == 2
    mock_http.post.assert_awaited_once()
    url = mock_http.post.call_args.args[0]
    assert "/api/def-store/terminologies/0190b000-0000-7000-0000-000000000001/terms" in url


# =========================================================================
# Error cases
# =========================================================================


@pytest.mark.asyncio
async def test_create_terminology_bulk_error():
    """create_terminology raises BulkError when result is an error."""
    bulk_response = {
        "results": [
            {"index": 0, "status": "error", "error": "Duplicate value 'COUNTRY'"}
        ],
        "total": 1,
        "succeeded": 0,
        "failed": 1,
    }
    mock_http = _mock_http(_mock_response(bulk_response))

    client = _make_client()
    with patch.object(client, "_get_client", return_value=mock_http), pytest.raises(BulkError, match="Duplicate value"):
        await client.create_terminology(
            value="COUNTRY", label="Country", namespace="wip"
        )
