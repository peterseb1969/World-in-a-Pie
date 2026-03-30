"""Tests for MCP server terminology and term tools.

Tests the tool functions directly by mocking the WipClient.
Follows the pattern from test_tools.py.
"""

import json

# Mock yaml before importing server (it may not be installed in test env)
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("yaml", MagicMock())

from wip_mcp.client import WipClient  # noqa: E402
from wip_mcp.server import (  # noqa: E402
    create_terminology,
    create_terms,
    delete_terminology,
    get_terminology,
    list_terms,
    update_terminology,
    validate_term_value,
)


def _mock_client():
    """Create a mock WipClient."""
    mock = AsyncMock(spec=WipClient)
    mock._ns = lambda ns: ns or "wip"
    return mock


# =========================================================================
# create_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_create_terminology_basic():
    """Create terminology with value, label, namespace passes correct args."""
    mock = _mock_client()
    mock.create_terminology.return_value = {
        "terminology_id": "T-001",
        "value": "COUNTRY",
        "label": "Country",
        "namespace": "wip",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await create_terminology(
            value="COUNTRY", label="Country", namespace="wip"
        )

    data = json.loads(result)
    assert data["terminology_id"] == "T-001"
    assert data["value"] == "COUNTRY"
    mock.create_terminology.assert_awaited_once_with(
        value="COUNTRY", label="Country", namespace="wip"
    )


@pytest.mark.asyncio
async def test_create_terminology_mutable_true():
    """Create terminology with mutable=True passes mutable=True to client."""
    mock = _mock_client()
    mock.create_terminology.return_value = {
        "terminology_id": "T-002",
        "value": "TEST_MUT",
        "mutable": True,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await create_terminology(
            value="TEST_MUT", label="Test Mutable", namespace="wip", mutable=True
        )

    data = json.loads(result)
    assert data["mutable"] is True
    mock.create_terminology.assert_awaited_once_with(
        value="TEST_MUT", label="Test Mutable", namespace="wip", mutable=True
    )


@pytest.mark.asyncio
async def test_create_terminology_mutable_false_default():
    """Create terminology with mutable=False (default) does not pass mutable kwarg."""
    mock = _mock_client()
    mock.create_terminology.return_value = {
        "terminology_id": "T-003",
        "value": "IMMUTABLE",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        await create_terminology(
            value="IMMUTABLE", label="Immutable", namespace="wip", mutable=False
        )

    # mutable=False is the default — should NOT be passed as kwarg
    call_kwargs = mock.create_terminology.call_args.kwargs
    assert "mutable" not in call_kwargs


@pytest.mark.asyncio
async def test_create_terminology_with_description():
    """Create terminology with description passes description to client."""
    mock = _mock_client()
    mock.create_terminology.return_value = {
        "terminology_id": "T-004",
        "value": "GENDER",
        "description": "Gender identity codes",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await create_terminology(
            value="GENDER",
            label="Gender",
            namespace="wip",
            description="Gender identity codes",
        )

    data = json.loads(result)
    assert data["description"] == "Gender identity codes"
    mock.create_terminology.assert_awaited_once_with(
        value="GENDER",
        label="Gender",
        namespace="wip",
        description="Gender identity codes",
    )


# =========================================================================
# update_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_update_terminology_label_only():
    """Update terminology with label only passes {label: ...} to client."""
    mock = _mock_client()
    mock.update_terminology.return_value = {
        "terminology_id": "T-001",
        "label": "Countries",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await update_terminology(terminology_id="T-001", label="Countries")

    data = json.loads(result)
    assert data["label"] == "Countries"
    mock.update_terminology.assert_awaited_once_with("T-001", {"label": "Countries"})


@pytest.mark.asyncio
async def test_update_terminology_mutable_true():
    """Update terminology with mutable=True includes mutable in updates."""
    mock = _mock_client()
    mock.update_terminology.return_value = {
        "terminology_id": "T-001",
        "mutable": True,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await update_terminology(terminology_id="T-001", mutable=True)

    data = json.loads(result)
    assert data["mutable"] is True
    mock.update_terminology.assert_awaited_once_with("T-001", {"mutable": True})


@pytest.mark.asyncio
async def test_update_terminology_no_fields_returns_error():
    """Update terminology with no fields returns error message."""
    mock = _mock_client()

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await update_terminology(terminology_id="T-001")

    assert "error" in result.lower()
    assert "at least one field" in result.lower()
    mock.update_terminology.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_terminology_multiple_fields():
    """Update terminology with multiple fields includes all in updates dict."""
    mock = _mock_client()
    mock.update_terminology.return_value = {
        "terminology_id": "T-001",
        "label": "Countries (updated)",
        "description": "ISO country codes",
        "mutable": True,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await update_terminology(
            terminology_id="T-001",
            label="Countries (updated)",
            description="ISO country codes",
            mutable=True,
        )

    data = json.loads(result)
    assert data["label"] == "Countries (updated)"
    mock.update_terminology.assert_awaited_once_with(
        "T-001",
        {
            "label": "Countries (updated)",
            "description": "ISO country codes",
            "mutable": True,
        },
    )


# =========================================================================
# get_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_get_terminology_success():
    """Successful get_terminology returns JSON response."""
    mock = _mock_client()
    mock.get_terminology.return_value = {
        "terminology_id": "T-001",
        "value": "COUNTRY",
        "label": "Country",
        "term_count": 195,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_terminology(terminology_id="T-001")

    data = json.loads(result)
    assert data["terminology_id"] == "T-001"
    assert data["value"] == "COUNTRY"
    assert data["term_count"] == 195
    mock.get_terminology.assert_awaited_once_with("T-001")


@pytest.mark.asyncio
async def test_get_terminology_not_found():
    """Get terminology for non-existent ID returns error message."""
    mock = _mock_client()
    mock.get_terminology.side_effect = Exception("Not found")

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_terminology(terminology_id="T-NONEXISTENT")

    assert "error" in result.lower()
    assert "not found" in result.lower()


# =========================================================================
# delete_terminology
# =========================================================================


@pytest.mark.asyncio
async def test_delete_terminology_without_force():
    """Delete terminology without force calls client correctly."""
    mock = _mock_client()
    mock.delete_terminology.return_value = {
        "terminology_id": "T-001",
        "status": "deleted",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await delete_terminology(terminology_id="T-001")

    data = json.loads(result)
    assert data["status"] == "deleted"
    mock.delete_terminology.assert_awaited_once_with("T-001", force=False)


@pytest.mark.asyncio
async def test_delete_terminology_with_force():
    """Delete terminology with force=True passes force=True to client."""
    mock = _mock_client()
    mock.delete_terminology.return_value = {
        "terminology_id": "T-001",
        "status": "deleted",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await delete_terminology(terminology_id="T-001", force=True)

    data = json.loads(result)
    assert data["status"] == "deleted"
    mock.delete_terminology.assert_awaited_once_with("T-001", force=True)


# =========================================================================
# list_terms
# =========================================================================


@pytest.mark.asyncio
async def test_list_terms_by_terminology_id():
    """List terms with terminology_id verifies correct API call."""
    mock = _mock_client()
    mock.list_terms.return_value = {
        "items": [
            {"term_id": "TRM-001", "value": "CH", "label": "Switzerland"},
            {"term_id": "TRM-002", "value": "GB", "label": "United Kingdom"},
        ],
        "total": 2,
        "page": 1,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await list_terms(terminology_id="T-001")

    data = json.loads(result)
    assert data["total"] == 2
    assert len(data["items"]) == 2
    mock.list_terms.assert_awaited_once_with(
        terminology_id="T-001", search=None, page=1, page_size=50
    )


@pytest.mark.asyncio
async def test_list_terms_with_search_filter():
    """List terms with search filter passes search param to client."""
    mock = _mock_client()
    mock.list_terms.return_value = {
        "items": [{"term_id": "TRM-001", "value": "CH", "label": "Switzerland"}],
        "total": 1,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await list_terms(terminology_id="T-001", search="switz")

    data = json.loads(result)
    assert data["total"] == 1
    mock.list_terms.assert_awaited_once_with(
        terminology_id="T-001", search="switz", page=1, page_size=50
    )


# =========================================================================
# create_terms
# =========================================================================


@pytest.mark.asyncio
async def test_create_terms_with_terminology_id_and_terms():
    """Create terms with terminology_id and terms list verifies client called correctly."""
    mock = _mock_client()
    mock.create_terms.return_value = {
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "results": [
            {"status": "created", "term_id": "TRM-001"},
            {"status": "created", "term_id": "TRM-002"},
        ],
    }

    terms = [
        {"value": "CH", "label": "Switzerland"},
        {"value": "GB", "label": "United Kingdom", "aliases": ["UK", "Britain"]},
    ]

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await create_terms(terminology_id="T-001", terms=terms)

    data = json.loads(result)
    assert data["succeeded"] == 2
    mock.create_terms.assert_awaited_once_with(
        terminology_id="T-001", terms=terms
    )


# =========================================================================
# validate_term_value (important — recently fixed)
# =========================================================================


@pytest.mark.asyncio
async def test_validate_term_with_uuid_terminology():
    """Validate term with UUID terminology passes terminology=UUID to client."""
    mock = _mock_client()
    uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    mock.validate_term.return_value = {
        "valid": True,
        "terminology_id": uuid,
        "value": "CH",
        "term_id": "TRM-001",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await validate_term_value(terminology=uuid, value="CH")

    data = json.loads(result)
    assert data["valid"] is True
    mock.validate_term.assert_awaited_once_with(terminology=uuid, value="CH")


@pytest.mark.asyncio
async def test_validate_term_with_terminology_value():
    """Validate term with terminology VALUE (e.g., 'COUNTRY') passes it correctly."""
    mock = _mock_client()
    mock.validate_term.return_value = {
        "valid": True,
        "terminology_value": "COUNTRY",
        "value": "CH",
        "term_id": "TRM-001",
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await validate_term_value(terminology="COUNTRY", value="CH")

    data = json.loads(result)
    assert data["valid"] is True
    mock.validate_term.assert_awaited_once_with(terminology="COUNTRY", value="CH")


@pytest.mark.asyncio
async def test_validate_term_valid_result_json_format():
    """Validate term returns valid result formatted as JSON."""
    mock = _mock_client()
    expected = {
        "valid": True,
        "terminology_value": "GENDER",
        "value": "M",
        "term_id": "TRM-100",
        "label": "Male",
    }
    mock.validate_term.return_value = expected

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await validate_term_value(terminology="GENDER", value="M")

    data = json.loads(result)
    assert data["valid"] is True
    assert data["term_id"] == "TRM-100"
    assert data["label"] == "Male"


@pytest.mark.asyncio
async def test_validate_term_error_formatted():
    """Validate term returns error when client raises exception."""
    mock = _mock_client()
    mock.validate_term.side_effect = Exception("Terminology 'NONEXISTENT' not found")

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await validate_term_value(terminology="NONEXISTENT", value="X")

    assert "error" in result.lower()
    assert "not found" in result.lower()
