"""A template value containing '/' is refused: no by-value route can take it.

Provenance: CASE-1280.

/templates/by-value/<value> addresses the value as one URL path segment, and
%2F is percent-decoded back to '/' before Starlette matches the route. A value
written with '/' therefore had no read: the create answered "created" and every
by-value lookup answered 404 — or, when the tail happened to name a subroute
('A/raw', 'A/versions'), reached that route for a different template. '/' is
refused at the write, as '.', '*', '>' and whitespace already are; existing
records stay readable by their canonical id.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/template-store/templates"


def _tpl(value: str) -> dict:
    return {"namespace": "wip", "value": value, "label": value.title(),
            "fields": [{"name": "id", "type": "string", "label": "Id"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["C1280/X", "C1280/", "/C1280", "C1280/raw"])
async def test_a_value_that_is_not_one_path_segment_is_refused(
    client: AsyncClient, auth_headers, value,
):
    resp = await client.post(API, headers=auth_headers, json=[_tpl(value)])
    assert resp.status_code == 422, resp.text
    assert "read back by value" in resp.text, resp.text


@pytest.mark.asyncio
async def test_a_value_that_survives_a_path_segment_is_still_accepted(
    client: AsyncClient, auth_headers,
):
    """':' and '%' are legal: a single decode returns them, so they address fine."""
    for value in ("C1280:V", "C1280-PCT%s"):
        resp = await client.post(API, headers=auth_headers, json=[_tpl(value)])
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["status"] == "created", resp.text
        read = await client.get(
            f"{API}/by-value/{value}", headers=auth_headers,
            params={"namespace": "wip"},
        )
        assert read.status_code == 200, read.text
        assert read.json()["value"] == value, read.text
