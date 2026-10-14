"""Tests for the ranked term search endpoint (GET /terms/search).

Covers: mode semantics (exact/prefix/word-start/substring), ranking tiers
(specificity, then field, then alphabetical), matched_name payloads,
exact mode returning all candidates, rollup to the root is_a ancestor,
literal (escaped) query input, and the namespace requirement.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from def_store.services.term_search import (
    _TIER_EXACT,
    _TIER_PREFIX,
    _TIER_SUBSTRING,
    _TIER_WORD_START,
    _classify,
)

# =============================================================================
# UNIT: match classification
# =============================================================================

def test_classify_tiers():
    assert _classify("Avastin", "avastin") == _TIER_EXACT
    assert _classify("Avastin", "ava") == _TIER_PREFIX
    assert _classify("5-FU (fluorouracil)", "fluorouracil") == _TIER_WORD_START
    assert _classify("pembrolizumab", "broli") == _TIER_SUBSTRING
    assert _classify("Avastin", "xyz") is None


# =============================================================================
# API
# =============================================================================

@pytest_asyncio.fixture
async def search_fixture(client: AsyncClient, auth_headers: dict):
    """A terminology with typed names and an is_a hierarchy."""
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": "SEARCH_MOLECULES", "label": "Search Molecules", "namespace": "wip"}],
    )
    tid = resp.json()["results"][0]["id"]

    terms = [
        {
            "value": "RO4876646",
            "label": "RO4876646",
            "names": [
                {"value": "bevacizumab", "type": "INN", "preferred": True},
                {"value": "Avastin", "type": "TRADE_GLOBAL"},
                {"value": "5-FU combo (bevacizumab)", "type": "SYNONYM"},
            ],
        },
        {
            "value": "beva-salt",
            "label": "Bevacizumab salt form",
            "names": [{"value": "bevacizumab", "type": "SYNONYM"}],
        },
        {"value": "pembrolizumab", "label": "Pembrolizumab"},
    ]
    create = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers, json=terms,
    )
    assert create.json()["failed"] == 0, create.json()
    created = {r["value"]: r["id"] for r in create.json()["results"]}

    # Hierarchy: beva-salt is_a RO4876646 (so rollup returns the molecule)
    rel = await client.post(
        "/api/def-store/ontology/term-relations",
        headers=auth_headers,
        json=[{
            "source_term_id": created["beva-salt"],
            "target_term_id": created["RO4876646"],
            "relation_type": "is_a",
        }],
        params={"namespace": "wip"},
    )
    assert rel.status_code == 200, rel.text
    return {"terminology_id": tid, "ids": created}


@pytest.mark.asyncio
async def test_search_prefix_ranked(client: AsyncClient, auth_headers: dict, search_fixture):
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={
            "q": "beva", "namespace": "wip",
            "terminology": search_fixture["terminology_id"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "prefix"
    # beva-salt matches on its VALUE (field tier beats name tier);
    # RO4876646 matches via its preferred INN name
    assert [i["term"]["value"] for i in data["items"]] == ["beva-salt", "RO4876646"]
    assert data["items"][0]["matched_via"] == "value"
    assert data["items"][1]["matched_via"] == "alias"
    assert data["items"][1]["matched_name"]["type"] == "INN"
    assert [i["rank"] for i in data["items"]] == [0, 1]


@pytest.mark.asyncio
async def test_search_exact_returns_all_candidates(client: AsyncClient, auth_headers: dict, search_fixture):
    """Exact mode is the ambiguity surface: both bearers of a shared name appear."""
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={
            "q": "Bevacizumab", "mode": "exact", "namespace": "wip",
            "terminology": search_fixture["terminology_id"],
        },
    )
    data = resp.json()
    values = {i["term"]["value"] for i in data["items"]}
    assert values == {"RO4876646", "beva-salt"}
    # The preferred INN outranks the plain synonym
    assert data["items"][0]["term"]["value"] == "RO4876646"


@pytest.mark.asyncio
async def test_search_word_start_vs_substring(client: AsyncClient, auth_headers: dict, search_fixture):
    tid = search_fixture["terminology_id"]
    # word-start: "fu" starts a word inside "5-FU combo (bevacizumab)"
    ws = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={"q": "fu", "mode": "word-start", "namespace": "wip", "terminology": tid},
    )
    assert [i["matched_value"] for i in ws.json()["items"]] == ["5-FU combo (bevacizumab)"]
    # substring-only hit ("broli" inside pembrolizumab) is excluded by word-start
    ws2 = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={"q": "broli", "mode": "word-start", "namespace": "wip", "terminology": tid},
    )
    assert ws2.json()["items"] == []
    sub = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={"q": "broli", "mode": "substring", "namespace": "wip", "terminology": tid},
    )
    assert [i["term"]["value"] for i in sub.json()["items"]] == ["pembrolizumab"]


@pytest.mark.asyncio
async def test_search_rollup_to_root(client: AsyncClient, auth_headers: dict, search_fixture):
    """A hit on the salt form rolls up to the molecule; dedup keeps one row."""
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={
            "q": "bevacizumab", "mode": "exact", "rollup": "root",
            "namespace": "wip", "terminology": search_fixture["terminology_id"],
        },
    )
    data = resp.json()
    assert len(data["items"]) == 1
    hit = data["items"][0]
    assert hit["term"]["value"] == "RO4876646"


@pytest.mark.asyncio
async def test_search_query_is_literal(client: AsyncClient, auth_headers: dict, search_fixture):
    """Regex metacharacters in q are text, not pattern — no 500, no wildcard."""
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={
            "q": "(bevacizumab)", "mode": "substring",
            "namespace": "wip", "terminology": search_fixture["terminology_id"],
        },
    )
    assert resp.status_code == 200
    assert [i["matched_value"] for i in resp.json()["items"]] == ["5-FU combo (bevacizumab)"]
    # A pattern that would match everything as regex matches nothing as text
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={"q": ".*", "namespace": "wip", "terminology": search_fixture["terminology_id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_search_namespace_wide(client: AsyncClient, auth_headers: dict, search_fixture):
    """Omitting terminology searches the whole namespace."""
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={"q": "pembro", "namespace": "wip"},
    )
    assert resp.status_code == 200
    values = [i["term"]["value"] for i in resp.json()["items"]]
    assert "pembrolizumab" in values


@pytest.mark.asyncio
async def test_search_requires_concrete_namespace(client: AsyncClient, auth_headers: dict, search_fixture):
    """An admin key without ?namespace= gets a 400, not an arbitrary scope."""
    resp = await client.get(
        "/api/def-store/terms/search",
        headers=auth_headers,
        params={"q": "beva"},
    )
    assert resp.status_code == 400
    assert "namespace" in resp.json()["detail"].lower()
