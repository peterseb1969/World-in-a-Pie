"""Per-item search failures are visible, not silent (CASE-606).

Pins the contract for both bulk search routes (/search/by-fields,
/search/by-term):

1. A per-item failure inside the search body yields status="error" with the
   failure message in `error` — distinguishable from a genuinely empty
   result set (status="ok", error=None). Before this, any exception was
   swallowed into a successful-looking empty item: a Mongo outage looked
   exactly like "0 matches" to every caller.
2. Per-item isolation still holds: a failing item does not 500 the batch,
   and sibling items in the same request still succeed.
3. The failure is logged server-side (logger.exception) so operators can
   see it at all.
"""

import logging

import pytest
from httpx import AsyncClient

from registry.services.search import SearchService

REGISTER = "/api/registry/entries/register"
BY_FIELDS = "/api/registry/search/by-fields"
BY_TERM = "/api/registry/search/by-term"


@pytest.mark.asyncio
async def test_by_fields_success_carries_ok_status(
    client: AsyncClient, auth_headers: dict
):
    """Happy path: status defaults to "ok" and error stays None — including
    for a genuinely empty result set."""
    response = await client.post(
        BY_FIELDS,
        json=[{"field_criteria": {"value": "no-such-value-606"}}],
        headers=auth_headers,
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "ok"
    assert result["error"] is None
    assert result["total_matches"] == 0


@pytest.mark.asyncio
async def test_by_fields_failure_is_visible_and_isolated(
    client: AsyncClient, auth_headers: dict, monkeypatch, caplog
):
    """A per-item exception surfaces as status="error" + message, the sibling
    item still succeeds, and the exception is logged."""
    await client.post(
        REGISTER,
        json=[{
            "namespace": "default",
            "entity_type": "terms",
            "composite_key": {"value": "FieldsTarget-606"},
        }],
        headers=auth_headers,
    )

    original = SearchService.build_field_query

    def exploding(field_criteria, **kwargs):
        if field_criteria.get("value") == "BOOM-606":
            raise RuntimeError("synthetic search failure 606")
        return original(field_criteria=field_criteria, **kwargs)

    monkeypatch.setattr(SearchService, "build_field_query", staticmethod(exploding))

    with caplog.at_level(logging.ERROR, logger="registry.api.search"):
        response = await client.post(
            BY_FIELDS,
            json=[
                {"field_criteria": {"value": "BOOM-606"}},
                {"field_criteria": {"value": "FieldsTarget-606"}},
            ],
            headers=auth_headers,
        )

    assert response.status_code == 200
    failed, ok = response.json()["results"]

    assert failed["input_index"] == 0
    assert failed["status"] == "error"
    assert "synthetic search failure 606" in failed["error"]
    assert failed["results"] == []
    assert failed["total_matches"] == 0

    assert ok["input_index"] == 1
    assert ok["status"] == "ok"
    assert ok["error"] is None
    assert ok["total_matches"] >= 1

    assert any("search_by_fields failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_by_term_failure_is_visible_and_isolated(
    client: AsyncClient, auth_headers: dict, monkeypatch, caplog
):
    """Same contract on the by-term route."""
    await client.post(
        REGISTER,
        json=[{
            "namespace": "default",
            "entity_type": "terms",
            "composite_key": {"value": "TermTarget-606"},
        }],
        headers=auth_headers,
    )

    original = SearchService.build_regex_search_query

    def exploding(term, **kwargs):
        if term == "BOOM-606":
            raise RuntimeError("synthetic by-term failure 606")
        return original(term=term, **kwargs)

    monkeypatch.setattr(
        SearchService, "build_regex_search_query", staticmethod(exploding)
    )

    with caplog.at_level(logging.ERROR, logger="registry.api.search"):
        response = await client.post(
            BY_TERM,
            json=[{"term": "BOOM-606"}, {"term": "termtarget"}],
            headers=auth_headers,
        )

    assert response.status_code == 200
    failed, ok = response.json()["results"]

    assert failed["status"] == "error"
    assert "synthetic by-term failure 606" in failed["error"]
    assert failed["total_matches"] == 0

    assert ok["status"] == "ok"
    assert ok["error"] is None
    assert ok["total_matches"] >= 1

    assert any("search_by_term failed" in r.message for r in caplog.records)
