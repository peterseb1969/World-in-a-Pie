"""Tests for describe_data_model tool and wip://query-assistant-prompt resource."""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("yaml", MagicMock())

from wip_mcp.client import WipClient  # noqa: E402
from wip_mcp.server import describe_data_model, get_query_assistant_prompt  # noqa: E402


def _mock_client():
    """Create a mock WipClient with template and terminology data."""
    mock = AsyncMock(spec=WipClient)

    mock.list_templates.return_value = {
        "items": [
            {
                "template_id": "TPL-001",
                "value": "PATIENT",
                "label": "Patient Record",
                "namespace": "clinic",
                "version": 2,
                "identity_fields": ["email"],
                "fields": [
                    {"name": "name", "field_type": "text", "mandatory": True, "description": "Full name"},
                    {"name": "email", "field_type": "text", "mandatory": True, "description": "Contact email"},
                    {
                        "name": "condition",
                        "field_type": "term",
                        "mandatory": False,
                        "term_terminology_value": "CONDITION",
                        "description": "Primary condition",
                    },
                ],
                "rules": [],
                "status": "active",
            },
            {
                "template_id": "TPL-002",
                "value": "VISIT",
                "label": "Visit Log",
                "namespace": "clinic",
                "version": 1,
                "identity_fields": ["patient_id", "visit_date"],
                "fields": [
                    {"name": "patient_id", "field_type": "reference", "mandatory": True, "description": "Patient ref"},
                    {"name": "visit_date", "field_type": "date", "mandatory": True, "description": "Date of visit"},
                    {"name": "notes", "field_type": "text", "mandatory": False},
                ],
                "rules": [],
                "status": "active",
            },
        ],
        "total": 2,
        "page": 1,
        "page_size": 100,
        "pages": 1,
    }

    mock.list_terminologies.return_value = {
        "items": [
            {
                "terminology_id": "TRM-001",
                "value": "CONDITION",
                "label": "Medical Conditions",
                "namespace": "clinic",
                "term_count": 42,
                "mutable": False,
                "status": "active",
            },
            {
                "terminology_id": "TRM-002",
                "value": "DEPARTMENT",
                "label": "Hospital Departments",
                "namespace": "clinic",
                "active_term_count": 8,
                "mutable": True,
                "status": "active",
            },
            {
                "terminology_id": "TRM-003",
                "value": "OLD_CODES",
                "label": "Deprecated",
                "namespace": "clinic",
                "term_count": 5,
                "mutable": False,
                "status": "inactive",
            },
        ],
        "total": 3,
        "page": 1,
        "page_size": 100,
        "pages": 1,
    }

    return mock


# =========================================================================
# describe_data_model
# =========================================================================


@pytest.mark.asyncio
async def test_describe_data_model_basic():
    """describe_data_model returns markdown with templates and terminologies."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    assert "# WIP Data Model" in result
    assert "PATIENT" in result
    assert "VISIT" in result
    assert "Patient Record" in result
    assert "CONDITION" in result
    assert "DEPARTMENT" in result
    # Inactive terminology should be filtered out
    assert "OLD_CODES" not in result


@pytest.mark.asyncio
async def test_describe_data_model_fields_per_template():
    """describe_data_model includes field details for each template."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    # PATIENT template fields
    assert "name" in result
    assert "email" in result
    assert "condition" in result
    assert "term" in result  # field_type for condition
    assert "CONDITION" in result  # term_terminology_value

    # VISIT template fields
    assert "patient_id" in result
    assert "visit_date" in result
    assert "reference" in result  # field_type


@pytest.mark.asyncio
async def test_describe_data_model_with_namespace():
    """describe_data_model shows namespace when filtered."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model(namespace="clinic")

    assert "clinic" in result
    mock.list_templates.assert_awaited_once_with(
        namespace="clinic", status="active", latest_only=True,
        page=1, page_size=100,
    )
    mock.list_terminologies.assert_awaited_once_with(
        namespace="clinic", page=1, page_size=100,
    )


@pytest.mark.asyncio
async def test_describe_data_model_empty():
    """describe_data_model handles empty data gracefully."""
    mock = AsyncMock(spec=WipClient)
    mock.list_templates.return_value = {
        "items": [], "total": 0, "page": 1, "page_size": 100, "pages": 1,
    }
    mock.list_terminologies.return_value = {
        "items": [], "total": 0, "page": 1, "page_size": 100, "pages": 1,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    assert "No active templates found" in result
    assert "No active terminologies found" in result


@pytest.mark.asyncio
async def test_describe_data_model_pagination():
    """describe_data_model paginates through all templates."""
    mock = AsyncMock(spec=WipClient)

    # Page 1 of 2
    page1 = {
        "items": [
            {
                "template_id": "TPL-001", "value": "ALPHA", "label": "Alpha",
                "namespace": "test", "version": 1, "identity_fields": [],
                "fields": [], "rules": [], "status": "active",
            },
        ],
        "total": 2, "page": 1, "page_size": 1, "pages": 2,
    }
    # Page 2 of 2
    page2 = {
        "items": [
            {
                "template_id": "TPL-002", "value": "BETA", "label": "Beta",
                "namespace": "test", "version": 1, "identity_fields": [],
                "fields": [], "rules": [], "status": "active",
            },
        ],
        "total": 2, "page": 2, "page_size": 1, "pages": 2,
    }
    mock.list_templates.side_effect = [page1, page2]
    mock.list_terminologies.return_value = {
        "items": [], "total": 0, "page": 1, "page_size": 100, "pages": 1,
    }

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    assert "ALPHA" in result
    assert "BETA" in result
    assert mock.list_templates.await_count == 2


@pytest.mark.asyncio
async def test_describe_data_model_query_conventions():
    """describe_data_model includes query conventions section."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    assert "Query Conventions" in result
    assert "query_by_template" in result
    assert "run_report_query" in result


@pytest.mark.asyncio
async def test_describe_data_model_identity_fields():
    """describe_data_model shows identity fields in template overview."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    # PATIENT has identity_field "email"
    assert "email" in result
    # VISIT has identity_fields "patient_id, visit_date"
    assert "patient_id, visit_date" in result


@pytest.mark.asyncio
async def test_describe_data_model_error():
    """describe_data_model returns error message on failure."""
    mock = AsyncMock(spec=WipClient)
    mock.list_templates.side_effect = ConnectionError("Service unavailable")

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await describe_data_model()

    assert "Error" in result


# =========================================================================
# wip://query-assistant-prompt resource
# =========================================================================


@pytest.mark.asyncio
async def test_query_assistant_prompt_basic():
    """Resource returns a complete system prompt with data model embedded."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_query_assistant_prompt()

    # Check key sections of the system prompt
    assert "WIP query assistant" in result
    assert "read-only" in result.lower()
    assert "How to Answer" in result
    assert "Query Strategy" in result
    assert "Response Style" in result
    assert "What NOT to Do" in result

    # Check data model is embedded
    assert "PATIENT" in result
    assert "VISIT" in result
    assert "CONDITION" in result


@pytest.mark.asyncio
async def test_query_assistant_prompt_data_model_failure():
    """Resource returns fallback when data model can't be fetched."""
    mock = AsyncMock(spec=WipClient)
    mock.list_templates.side_effect = ConnectionError("down")

    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_query_assistant_prompt()

    # Should still return a usable prompt
    assert "WIP query assistant" in result
    assert "Data model unavailable" in result
    assert "describe_data_model" in result


@pytest.mark.asyncio
async def test_query_assistant_prompt_has_all_sections():
    """Resource covers all critical instruction sections."""
    mock = _mock_client()
    with patch("wip_mcp.server.get_client", return_value=mock):
        result = await get_query_assistant_prompt()

    # Verify all key guidance is present
    assert "search" in result.lower()
    assert "query_by_template" in result
    assert "run_report_query" in result
    assert "list_report_tables" in result
    assert "UPPERCASE" in result
    assert "500 words" in result
    assert "cannot" in result.lower()  # read-only reminder
