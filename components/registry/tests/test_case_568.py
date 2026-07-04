"""CASE-568 — registry cleanup regressions.

Pins the two behaviour-relevant fixes:
1. browse_entries escapes its query — regex metacharacters are literal search
   text, not patterns (and never a 500).
2. /search/by-term serves results through the regex path directly — the dead
   $text primary (no text index existed) is gone, so this pins the regex
   semantics as the contract.
"""

import pytest
from httpx import AsyncClient

REGISTER = "/api/registry/entries/register"


class TestBrowseRegexEscape:
    @pytest.mark.asyncio
    async def test_browse_with_regex_metacharacters(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Malformed-regex input (unbalanced bracket) is treated as literal
        text: 200 + empty result, not a Mongo $regex error."""
        response = await client.get(
            "/api/registry/entries",
            params={"q": "[unbalanced"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    @pytest.mark.asyncio
    async def test_browse_metacharacters_match_literally(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A value containing regex metacharacters is found by searching for
        it verbatim — and a pattern that would regex-match it does not."""
        await client.post(
            REGISTER,
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"value": "A.B+C"},
            }],
            headers=auth_headers,
        )

        literal = await client.get(
            "/api/registry/entries",
            params={"q": "A.B+C"},
            headers=auth_headers,
        )
        assert literal.status_code == 200
        assert len(literal.json()["items"]) == 1

        # Unescaped, "A.B+CX" as a regex would never match "A.B+C"; escaped,
        # it's a literal non-substring — either way 0 hits, but "AXB+C" WOULD
        # regex-match via the unescaped dot. Pin that it no longer does.
        pattern = await client.get(
            "/api/registry/entries",
            params={"q": "AXB+C"},
            headers=auth_headers,
        )
        assert pattern.status_code == 200
        assert pattern.json()["items"] == []


class TestSearchByTermRegexPath:
    @pytest.mark.asyncio
    async def test_by_term_returns_results_without_text_index(
        self, client: AsyncClient, auth_headers: dict
    ):
        """by-term search works with no text index — the regex path IS the
        primary path (substring, case-insensitive)."""
        await client.post(
            REGISTER,
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"value": "SearchTarget-568"},
            }],
            headers=auth_headers,
        )

        response = await client.post(
            "/api/registry/search/by-term",
            json=[{"term": "searchtarget"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        result = response.json()["results"][0]
        assert result["total_matches"] >= 1
        assert any(
            "SearchTarget-568" in str(r["matched_composite_key"])
            for r in result["results"]
        )


class TestSearchByTermLimit:
    """CASE-572 — by-term accepts a limit; total_matches stays the full count."""

    @pytest.mark.asyncio
    async def test_limit_bounds_results_total_reports_all(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post(
            REGISTER,
            json=[
                {
                    "namespace": "default",
                    "entity_type": "terms",
                    "composite_key": {"value": f"LimitTarget-572-{n}"},
                }
                for n in range(3)
            ],
            headers=auth_headers,
        )

        limited = await client.post(
            "/api/registry/search/by-term",
            json=[{"term": "limittarget-572", "limit": 2}],
            headers=auth_headers,
        )
        assert limited.status_code == 200
        result = limited.json()["results"][0]
        assert len(result["results"]) == 2
        assert result["total_matches"] == 3

        unlimited = await client.post(
            "/api/registry/search/by-term",
            json=[{"term": "limittarget-572"}],
            headers=auth_headers,
        )
        assert unlimited.status_code == 200
        result = unlimited.json()["results"][0]
        assert len(result["results"]) == 3
        assert result["total_matches"] == 3

    @pytest.mark.asyncio
    async def test_limit_zero_rejected(
        self, client: AsyncClient, auth_headers: dict
    ):
        """limit has ge=1 — 0 is a validation error, not 'no results'."""
        response = await client.post(
            "/api/registry/search/by-term",
            json=[{"term": "anything", "limit": 0}],
            headers=auth_headers,
        )
        assert response.status_code == 422
