"""Tests for `full_text_indexed` field flag (Phase 1).

Phase 1 only adds the schema flag and the structural validator. The
reporting-sync DDL plumbing and search-endpoint behaviour are Phase 2/3.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/template-store"


async def _post_template(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    """POST a single template; return the per-item BulkResultItem."""
    resp = await client.post(
        f"{API}/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert resp.status_code == 200
    return resp.json()["results"][0]


def _payload(value: str, *, fields: list[dict] | None = None, reporting: dict | None = None) -> dict:
    body: dict = {
        "namespace": "wip",
        "value": value,
        "label": value.title(),
        "fields": fields or [
            {"name": "title", "type": "string", "label": "Title", "mandatory": True},
            {"name": "body", "type": "string", "label": "Body"},
        ],
    }
    if reporting is not None:
        body["reporting"] = reporting
    return body


# =============================================================================
# Backwards compatibility
# =============================================================================


@pytest.mark.asyncio
async def test_template_without_full_text_indexed_still_works(
    client: AsyncClient, auth_headers: dict
):
    result = await _post_template(client, auth_headers, _payload("FTS_BACKCOMPAT"))
    assert result["status"] == "created", result


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.asyncio
async def test_full_text_indexed_on_string_field_accepted(
    client: AsyncClient, auth_headers: dict
):
    payload = _payload(
        "FTS_HAPPY",
        fields=[
            {"name": "title", "type": "string", "label": "Title", "full_text_indexed": True},
            {"name": "body", "type": "string", "label": "Body", "full_text_indexed": True},
        ],
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "created", result

    # Round-trip — flag persists on read.
    resp = await client.get(
        f"{API}/templates/by-value/FTS_HAPPY?namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    flags = {f["name"]: f.get("full_text_indexed") for f in body["fields"]}
    assert flags == {"title": True, "body": True}


@pytest.mark.asyncio
async def test_full_text_indexed_with_explicit_sync_enabled_true(
    client: AsyncClient, auth_headers: dict
):
    payload = _payload(
        "FTS_SYNC_ON",
        fields=[
            {"name": "body", "type": "string", "label": "Body", "full_text_indexed": True},
        ],
        reporting={"sync_enabled": True},
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "created", result


# =============================================================================
# Rejections
# =============================================================================


@pytest.mark.asyncio
async def test_full_text_indexed_on_non_string_field_rejected(
    client: AsyncClient, auth_headers: dict
):
    payload = _payload(
        "FTS_REJECT_TYPE",
        fields=[
            {"name": "title", "type": "string", "label": "Title", "mandatory": True},
            {"name": "score", "type": "number", "label": "Score", "full_text_indexed": True},
        ],
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "full_text_indexed" in result["error"]
    assert "string" in result["error"]
    assert "score" in result["error"]


@pytest.mark.asyncio
async def test_full_text_indexed_with_sync_disabled_rejected(
    client: AsyncClient, auth_headers: dict
):
    payload = _payload(
        "FTS_REJECT_SYNC",
        fields=[
            {"name": "body", "type": "string", "label": "Body", "full_text_indexed": True},
        ],
        reporting={"sync_enabled": False},
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "full_text_indexed" in result["error"]
    assert "sync_enabled" in result["error"]


@pytest.mark.asyncio
async def test_full_text_indexed_string_value_rejected_today(
    client: AsyncClient, auth_headers: dict
):
    """Language-code form ('en', 'de', ...) is reserved for v2; v1 accepts bool only.

    Pydantic at the request boundary rejects a non-bool value for a
    `bool | None` field with a 422 ValidationError before our service
    validator runs. The schema *reserves space* for the future
    language-tag form (the FieldDefinition field exists), but a
    concrete string value gets shut down at parse time. When v2 lands
    with language support, flip the field type to `bool | str | None`
    and update this test to assert acceptance.
    """
    payload = _payload(
        "FTS_LANG_RESERVED",
        fields=[
            {"name": "body", "type": "string", "label": "Body", "full_text_indexed": "en"},
        ],
    )
    resp = await client.post(
        f"{API}/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert resp.status_code == 422, resp.text
    body = resp.text
    assert "full_text_indexed" in body
    # Pydantic's default error message for a bad bool mentions the type.
    assert "bool" in body or "boolean" in body


# =============================================================================
# Update path
# =============================================================================


async def _put_template(
    client: AsyncClient, auth_headers: dict, template_id: str, patch: dict,
) -> dict:
    """PUT a single update via the bulk endpoint; return the per-item result."""
    resp = await client.put(
        f"{API}/templates",
        headers=auth_headers,
        json=[{"template_id": template_id, **patch}],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_update_adds_full_text_indexed_to_existing_template(
    client: AsyncClient, auth_headers: dict
):
    # Create a baseline template with no FTS flags.
    create = await _post_template(client, auth_headers, _payload("FTS_UPDATE_ADD"))
    assert create["status"] == "created", create
    template_id = create["id"]

    # Update — flip body to indexed.
    result = await _put_template(
        client, auth_headers, template_id,
        {
            "fields": [
                {"name": "title", "type": "string", "label": "Title", "mandatory": True},
                {"name": "body", "type": "string", "label": "Body", "full_text_indexed": True},
            ],
        },
    )
    assert result["status"] == "updated", result


@pytest.mark.asyncio
async def test_update_breaking_invariant_rejected(
    client: AsyncClient, auth_headers: dict
):
    # Create a template with FTS enabled.
    create = await _post_template(
        client,
        auth_headers,
        _payload(
            "FTS_UPDATE_BREAK",
            fields=[
                {"name": "body", "type": "string", "label": "Body", "full_text_indexed": True},
            ],
        ),
    )
    assert create["status"] == "created", create
    template_id = create["id"]

    # Try to update reporting to disable sync — bulk PUT returns 200,
    # error surfaces in the per-item result body.
    result = await _put_template(
        client, auth_headers, template_id,
        {"reporting": {"sync_enabled": False}},
    )
    assert result["status"] == "error", result
    assert (
        "full_text_indexed" in result["error"]
        or "sync_enabled" in result["error"]
    )
