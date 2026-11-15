"""A terminology value containing '/' is refused: no by-value route can take it.

Provenance: CASE-1280.

/terminologies/by-value/<value> addresses the value as one URL path segment,
and %2F is percent-decoded back to '/' before Starlette matches the route. A
value written with '/' therefore had no read: it was created, and every
by-value lookup answered 404. Both doors a value enters through — the create
and a rename on the update — now refuse it, so what the platform accepts stays
addressable. The update answers per item, as the other value refusals on that
door do; the rest of the batch is written.
"""

import pytest
from httpx import AsyncClient

API = "/api/def-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["C1280/X", "C1280/", "/C1280", "A/B/C"])
async def test_a_value_that_is_not_one_path_segment_is_refused(
    client: AsyncClient, auth_headers, value,
):
    resp = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"value": value, "label": value, "namespace": "wip"}],
    )
    assert resp.status_code == 422, resp.text
    assert "read back by value" in resp.text, resp.text


@pytest.mark.asyncio
async def test_a_value_that_survives_a_path_segment_is_still_accepted(
    client: AsyncClient, auth_headers,
):
    """':' and '%' are legal: a single decode returns them, so they address fine."""
    for value in ("C1280:V", "C1280-PCT%s"):
        resp = await client.post(
            f"{API}/terminologies", headers=auth_headers,
            json=[{"value": value, "label": value, "namespace": "wip"}],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["status"] == "created", resp.text
        read = await client.get(
            f"{API}/terminologies/by-value/{value}", headers=auth_headers,
            params={"namespace": "wip"},
        )
        assert read.status_code == 200, read.text
        assert read.json()["value"] == value, read.text


@pytest.mark.asyncio
async def test_a_rename_to_an_unaddressable_value_is_the_items_error(
    client: AsyncClient, auth_headers,
):
    created = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"value": "C1280_RENAME", "label": "Rename", "namespace": "wip"},
              {"value": "C1280_OTHER", "label": "Other", "namespace": "wip"}],
    )
    bad_id, good_id = [r["id"] for r in created.json()["results"]]

    resp = await client.put(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"terminology_id": bad_id, "value": "C1280/RENAMED"},
              {"terminology_id": good_id, "label": "Renamed fine"}],
    )
    assert resp.status_code == 200, resp.text
    first, second = resp.json()["results"]
    assert first["status"] == "error", first
    assert first["error_code"] == "validation_failed", first
    assert first["details"]["rule"] == "unaddressable_value", first
    assert "read back by value" in first["error"], first
    assert second["status"] == "updated", second

    # Nothing moved: the refused rename left the value as it was, addressable.
    read = await client.get(
        f"{API}/terminologies/by-value/C1280_RENAME", headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert read.status_code == 200, read.text
    assert read.json()["terminology_id"] == bad_id, read.text
