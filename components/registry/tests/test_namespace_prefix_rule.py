"""A new namespace's prefix must be one token wherever it is embedded.

The prefix appears in NATS event subjects, PostgreSQL schema names and URL
paths. A dot, a NATS wildcard or whitespace would shift or widen a subject
token, so a prefix is lowercase letters, digits, '-' and '_', starting with
a letter or digit. The rule applies when a namespace is created, on every
path that creates one; a namespace that already exists keeps its name and
stays writable.
"""

import json
import zipfile

import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace, prefix_error
from registry.services.import_service import ImportService

BAD = ["a.b", "UPPER", "with space", "star*", "tail>", "-leading", "_leading", "", "ns/x"]
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


@pytest.mark.asyncio
async def test_import_refuses_to_create_an_invalid_prefix(client, tmp_path):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "prefix": "source", "description": "copy",
            "isolation_mode": "open", "allowed_external_refs": [], "id_config": {},
        }))
    svc = ImportService(
        def_store_url="http://def", template_store_url="http://tpl",
        document_store_url="http://doc", api_key="k",
    )

    with pytest.raises(ValueError, match="Invalid namespace prefix"):
        await svc.import_namespace(str(archive), target_prefix="bad.prefix", mode="create")
    assert await Namespace.find_one({"prefix": "bad.prefix"}) is None
