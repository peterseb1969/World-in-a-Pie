"""Term search treats the search string as literal text (CASE-869).

The search parameter went into Mongo's $regex unescaped: an invalid
pattern (`(`, `[`, a trailing `\\`) made MongoDB reject the query — a 500
mid-typeahead, since drug names contain these characters routinely
(`5-FU (fluorouracil)`, `pembrolizumab + chemo`) — and a valid pattern
changed meaning silently (`a.b` matched `axb`). The fix escapes the input
(the Registry's CASE-568 rule); both halves are asserted here.
"""

from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def test_terminology(client: AsyncClient, auth_headers: dict):
    """A terminology to search in (same shape as test_terms.py's)."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "CASE869_MOLECULE",
            "label": "Molecules",
            "namespace": "wip",
            "case_sensitive": False,
        }],
    )
    terminology_id = response.json()["results"][0]["id"]
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers,
    )
    return get_response.json()


async def _seed(client: AsyncClient, auth_headers: dict, terminology_id: str) -> None:
    resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[
            {
                "value": "fluorouracil",
                "label": "5-FU (fluorouracil)",
                "aliases": ["5-FU (fluorouracil)", "pembrolizumab + chemo"],
            },
            {"value": "axb-compound", "label": "axb"},
        ],
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("needle", ["(", "[", "*", "+", "\\", "a("])
async def test_metacharacters_never_500(
    client: AsyncClient, auth_headers: dict, test_terminology, needle: str
) -> None:
    terminology_id = test_terminology["terminology_id"]
    await _seed(client, auth_headers, terminology_id)
    resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms?search={quote(needle)}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"search={needle!r}: {resp.text}"


@pytest.mark.asyncio
async def test_metacharacters_match_literally(
    client: AsyncClient, auth_headers: dict, test_terminology
) -> None:
    terminology_id = test_terminology["terminology_id"]
    await _seed(client, auth_headers, terminology_id)
    resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms"
        f"?search={quote('(fluorouracil')}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [t["value"] for t in items] == ["fluorouracil"]


@pytest.mark.asyncio
async def test_patterns_lose_their_regex_meaning(
    client: AsyncClient, auth_headers: dict, test_terminology
) -> None:
    """`a.b` used to match `axb` (dot as wildcard) — it must not anymore."""
    terminology_id = test_terminology["terminology_id"]
    await _seed(client, auth_headers, terminology_id)
    resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms?search=a.b",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []
