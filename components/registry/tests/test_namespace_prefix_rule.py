"""A new namespace's prefix must be one token wherever it is embedded.

The prefix appears in NATS event subjects, PostgreSQL schema names and URL
paths. A dot, a NATS wildcard or whitespace would shift or widen a subject
token, so a prefix is lowercase letters, digits, '-' and '_', starting with
a letter or digit. The rule applies when a namespace is created, on every
path that creates one; a namespace that already exists keeps its name and
stays writable.
"""


import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace, prefix_error

BAD = ["a.b", "UPPER", "with space", "star*", "tail>", "-leading", "_leading", "", "ns/x",
       # PostgreSQL system schemas: the reporting schema is named after the prefix
       "public", "information_schema", "pg_catalog", "pg_x"]
GOOD = ["a", "dev", "customer-abc", "kb_research", "0day", "p991-src"]


@pytest.mark.parametrize("prefix", GOOD)
def test_valid_prefixes_pass(prefix):
    assert prefix_error(prefix) is None


@pytest.mark.parametrize("prefix", BAD)
def test_invalid_prefixes_are_named(prefix):
    error = prefix_error(prefix)
    assert error is not None and repr(prefix) in error


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["a.b", "Upper", "tail>"])
async def test_create_refuses_an_invalid_prefix(client: AsyncClient, auth_headers: dict, prefix):
    response = await client.post(
        "/api/registry/namespaces", json={"prefix": prefix}, headers=auth_headers,
    )
    assert response.status_code == 400, response.text
    assert "Invalid namespace prefix" in response.json()["detail"]
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
async def test_upsert_refuses_to_create_an_invalid_prefix(client: AsyncClient, auth_headers: dict):
    response = await client.put(
        "/api/registry/namespaces/a.b", json={"description": "x"}, headers=auth_headers,
    )
    assert response.status_code == 400, response.text
    assert await Namespace.find_one({"prefix": "a.b"}) is None


@pytest.mark.asyncio
async def test_an_existing_namespace_keeps_its_name_and_stays_writable(
    client: AsyncClient, auth_headers: dict,
):
    await Namespace(prefix="Legacy.NS", description="before the rule").insert()

    response = await client.put(
        "/api/registry/namespaces/Legacy.NS", json={"description": "updated"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["description"] == "updated"


