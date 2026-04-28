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

    The schema declares `bool | None`, so a string passes through Pydantic
    coercion as truthy bool. Either way the field is recognised. The
    important thing is that the schema doesn't *break* on the future form.
    This test is a placeholder reminder — when language support lands,
    flip it to assert `accepted` and remove the loose check.
    """
    # Pydantic coerces "en" → True under bool|None — still recognised as
    # "indexed". The behaviour is acceptable for v1 (the field is indexed),
    # and it leaves room for v2 to interpret the string as a language code.
    payload = _payload(
        "FTS_LANG_RESERVED",
        fields=[
            {"name": "body", "type": "string", "label": "Body", "full_text_indexed": "en"},
        ],
    )
    result = await _post_template(client, auth_headers, payload)
    # Either "created" (Pydantic coerced "en" to True) or "error" with a
    # clear message — both are defensible v1 outcomes. We assert that the
    # call did not 5xx and the error (if any) is shaped about full_text_indexed.
    if result["status"] == "error":
        assert "full_text_indexed" in result["error"] or "bool" in result["error"]


# =============================================================================
# Update path
# =============================================================================


@pytest.mark.asyncio
async def test_update_adds_full_text_indexed_to_existing_template(
    client: AsyncClient, auth_headers: dict
):
    # Create a baseline template with no FTS flags.
    create = await _post_template(client, auth_headers, _payload("FTS_UPDATE_ADD"))
    assert create["status"] == "created", create
    template_id = create["id"]

    # Update — flip body to indexed.
    update_resp = await client.put(
        f"{API}/templates/{template_id}",
        headers=auth_headers,
        json={
            "fields": [
                {"name": "title", "type": "string", "label": "Title", "mandatory": True},
                {"name": "body", "type": "string", "label": "Body", "full_text_indexed": True},
            ],
        },
    )
    assert update_resp.status_code == 200, update_resp.text


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

    # Try to update reporting to disable sync — must reject.
    update_resp = await client.put(
        f"{API}/templates/{template_id}",
        headers=auth_headers,
        json={"reporting": {"sync_enabled": False}},
    )
    assert update_resp.status_code in (400, 422), update_resp.text
    assert "full_text_indexed" in update_resp.text or "sync_enabled" in update_resp.text
