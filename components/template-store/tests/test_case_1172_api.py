"""template-store's API surface answers what it was asked.

- GET /templates?extends=<bare value> with no namespace to read it in
  answered 200 with zero rows; it is refused now (strict resolution: 422,
  "no namespace context").
- latest_only=true was ignored when value= was given.
- A template value containing '.', '*', '>' or whitespace is refused: the
  value is a token of the event subject wip.templates.<value>.<event>.
- A sync_strategy reporting-sync does not know is refused at the write.
- An unexpected fault in a bulk item is that item's error (internal_error);
  it was a 500 that lost every other item's result.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

API = "/api/template-store/templates"


def _tpl(value: str, **extra) -> dict:
    return {"namespace": "wip", "value": value, "label": value.title(),
            "fields": [{"name": "id", "type": "string", "label": "Id"}], **extra}


async def _post(client, headers, *payloads):
    resp = await client.post(API, headers=headers, json=list(payloads))
    return resp


@pytest.mark.asyncio
async def test_an_unreadable_extends_filter_is_refused(client: AsyncClient, auth_headers):
    resp = await client.get(API, headers=auth_headers, params={"extends": "NO_NAMESPACE_GIVEN"})
    assert resp.status_code == 422, resp.text
    assert "no namespace context" in resp.text, resp.text


@pytest.mark.asyncio
async def test_latest_only_holds_with_a_value_filter(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _tpl("C1172_LATEST"))
    await _post(client, auth_headers, _tpl("C1172_LATEST", label="Second"))
    resp = await client.get(API, headers=auth_headers,
                            params={"value": "C1172_LATEST", "latest_only": "true"})
    assert resp.status_code == 200, resp.text
    assert [t["version"] for t in resp.json()["items"]] == [2], resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["C1172.DOT", "C1172*", "C1172>", "C1172 SPACE"])
async def test_a_value_that_breaks_its_event_subject_is_refused(
    client: AsyncClient, auth_headers, value,
):
    resp = await _post(client, auth_headers, _tpl(value))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_an_unknown_sync_strategy_is_refused(client: AsyncClient, auth_headers):
    resp = await _post(client, auth_headers, _tpl(
        "C1172_SYNC", reporting={"sync_enabled": True, "sync_strategy": "latest"}))
    item = resp.json()["results"][0]
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "sync_strategy", item


@pytest.mark.asyncio
async def test_an_unexpected_fault_is_the_items_error(client: AsyncClient, auth_headers):
    with patch.object(Template, "insert", side_effect=RuntimeError("mongo went away")):
        resp = await _post(client, auth_headers, _tpl("C1172_FAULT"))
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "error", item
    assert item["error_code"] == "internal_error", item
