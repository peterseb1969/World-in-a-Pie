"""A namespace's inbound_references setting: closed by default, wip always open.

'closed' means a reference from another namespace is accepted only when the
writing key can read this namespace; 'open' accepts any writer. The stores
enforce it (wip_auth.reference_policy); the Registry stores and serves it.
"""

import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace
from registry.services.export_service import ExportService
from registry.services.import_service import ImportService


async def _put(client: AsyncClient, headers: dict, prefix: str, body: dict):
    return await client.put(f"/api/registry/namespaces/{prefix}", headers=headers, json=body)


@pytest.mark.asyncio
async def test_a_new_namespace_is_closed(client: AsyncClient, auth_headers: dict):
    created = await client.post(
        "/api/registry/namespaces", headers=auth_headers, json={"prefix": "inb-new"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["inbound_references"] == "closed"

    upserted = await _put(client, auth_headers, "inb-upsert", {})
    assert upserted.json()["inbound_references"] == "closed"


@pytest.mark.asyncio
async def test_a_namespace_can_be_opened_and_closed(client: AsyncClient, auth_headers: dict):
    await _put(client, auth_headers, "inb-flip", {})
    opened = await _put(client, auth_headers, "inb-flip", {"inbound_references": "open"})
    assert opened.json()["inbound_references"] == "open"

    # Another field's update leaves it alone.
    described = await _put(client, auth_headers, "inb-flip", {"description": "x"})
    assert described.json()["inbound_references"] == "open"

    closed = await _put(client, auth_headers, "inb-flip", {"inbound_references": "closed"})
    assert closed.json()["inbound_references"] == "closed"


@pytest.mark.asyncio
async def test_an_unknown_value_is_refused(client: AsyncClient, auth_headers: dict):
    resp = await _put(client, auth_headers, "inb-bad", {"inbound_references": "private"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_wip_cannot_be_closed(client: AsyncClient, auth_headers: dict):
    # Created through the default (closed) like any namespace, served open.
    created = await _put(client, auth_headers, "wip", {})
    assert created.json()["inbound_references"] == "open"

    resp = await _put(client, auth_headers, "wip", {"inbound_references": "closed"})
    assert resp.status_code == 400
    assert "wip" in resp.json()["detail"]

    served = await client.get("/api/registry/namespaces/wip", headers=auth_headers)
    assert served.json()["inbound_references"] == "open"


@pytest.mark.asyncio
async def test_a_namespace_stored_before_the_setting_reads_closed(client: AsyncClient, auth_headers: dict):
    await Namespace(prefix="inb-legacy").insert()
    await Namespace.get_motor_collection().update_one(
        {"prefix": "inb-legacy"}, {"$unset": {"inbound_references": ""}},
    )
    served = await client.get("/api/registry/namespaces/inb-legacy", headers=auth_headers)
    assert served.json()["inbound_references"] == "closed"


@pytest.mark.asyncio
async def test_a_stored_wip_reads_open_whatever_was_stored(client: AsyncClient):
    await Namespace.get_motor_collection().update_one(
        {"prefix": "wip"}, {"$set": {"inbound_references": "closed"}}, upsert=True,
    )
    ns = await Namespace.find_one({"prefix": "wip"})
    assert ns.inbound_references == "open"


def _http_returning_nothing() -> MagicMock:
    empty = MagicMock(status_code=200)
    empty.json.return_value = {"items": [], "total": 0, "pages": 1}
    http = MagicMock()
    http.get = AsyncMock(return_value=empty)
    http.post = AsyncMock(return_value=empty)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    return http


def _services(cls):
    return cls(
        def_store_url="http://def", template_store_url="http://tpl",
        document_store_url="http://doc", api_key="k",
    )


@pytest.mark.asyncio
async def test_export_carries_the_setting(client: AsyncClient):
    await Namespace(prefix="inb-export", inbound_references="open").insert()
    ns = await Namespace.find_one({"prefix": "inb-export"})
    with patch("httpx.AsyncClient", return_value=_http_returning_nothing()):
        zip_path, _stats = await _services(ExportService).export_namespace(ns)
    with zipfile.ZipFile(zip_path) as zf, zf.open("manifest.json") as m:
        assert json.load(m)["inbound_references"] == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize(("manifest_value", "expected"), [("open", "open"), (None, "closed")])
async def test_import_applies_the_setting(client: AsyncClient, tmp_path, manifest_value, expected):
    prefix = f"inb-import-{expected}"
    manifest = {"version": "2.0", "prefix": prefix}
    if manifest_value is not None:
        manifest["inbound_references"] = manifest_value
    archive = tmp_path / "ns.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    with patch("httpx.AsyncClient", return_value=_http_returning_nothing()):
        await _services(ImportService).import_namespace(
            str(archive), mode="create", imported_by="tester",
        )
    ns = await Namespace.find_one({"prefix": prefix})
    assert ns.inbound_references == expected
