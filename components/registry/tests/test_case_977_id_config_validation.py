"""Namespace writes validate id_config; one bad stored value cannot fail the list.

id_config used to be a free dict on create, upsert and import. A misspelt
entity type or field was stored and silently ignored, an unknown algorithm
failed every later mint with a misleading error, a prefixed config without a
prefix minted bare numbers, and a non-object value was stored even though the
upsert answered 500, after which every namespace listing on the install
failed. These tests pin the refusals, the stored shape a valid config still
mints from, and the tolerant read for values stored before validation.
"""

import json
import zipfile

import pytest
from httpx import AsyncClient

from registry.models.id_algorithm import VALID_ENTITY_TYPES, IdEntityType
from registry.models.namespace import Namespace
from registry.services.import_service import ImportService

BAD_ID_CONFIGS = [
    pytest.param({"terminology": {"algorithm": "prefixed", "prefix": "T-"}}, id="misspelt-entity-type"),
    pytest.param({"terminologies": {"algorithm": "prefix", "prefix": "T-"}}, id="unknown-algorithm"),
    pytest.param({"terminologies": {"algorithm": "prefixed"}}, id="prefixed-without-prefix"),
    pytest.param({"terminologies": {"algorithm": "prefixed", "prefix": ""}}, id="prefixed-empty-prefix"),
    pytest.param(
        {"terminologies": {"algorithm": "prefixed", "prefix": "T-", "padding": 3}}, id="unknown-field"
    ),
    pytest.param({"terminologies": "prefixed"}, id="not-an-object"),
    pytest.param({"terminologies": {"algorithm": "pattern"}}, id="pattern-without-pattern"),
    pytest.param({"terminologies": {"algorithm": "pattern", "pattern": "("}}, id="pattern-bad-regex"),
    pytest.param({"terminologies": {"algorithm": "nanoid", "length": 0}}, id="nanoid-zero-length"),
]


def test_write_entity_types_match_the_read_side() -> None:
    assert set(IdEntityType.__args__) == VALID_ENTITY_TYPES


@pytest.mark.asyncio
@pytest.mark.parametrize("id_config", BAD_ID_CONFIGS)
async def test_create_refuses_bad_id_config(client: AsyncClient, auth_headers: dict, id_config) -> None:
    resp = await client.post(
        "/api/registry/namespaces",
        json={"prefix": "idc-create", "id_config": id_config},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert await Namespace.find_one({"prefix": "idc-create"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("id_config", BAD_ID_CONFIGS)
async def test_upsert_create_refuses_bad_id_config(client: AsyncClient, auth_headers: dict, id_config) -> None:
    resp = await client.put(
        "/api/registry/namespaces/idc-upsert",
        json={"id_config": id_config},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert await Namespace.find_one({"prefix": "idc-upsert"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("id_config", BAD_ID_CONFIGS)
async def test_upsert_update_refuses_bad_id_config_and_keeps_the_old_one(
    client: AsyncClient, auth_headers: dict, id_config
) -> None:
    good = {"terminologies": {"algorithm": "prefixed", "prefix": "KEEP-"}}
    resp = await client.put(
        "/api/registry/namespaces/idc-update", json={"id_config": good}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text

    resp = await client.put(
        "/api/registry/namespaces/idc-update", json={"id_config": id_config}, headers=auth_headers
    )
    assert resp.status_code == 422, resp.text
    stored = await Namespace.find_one({"prefix": "idc-update"})
    assert stored is not None and stored.id_config == good


@pytest.mark.asyncio
async def test_upserted_prefixed_config_mints_from_the_stored_shape(
    client: AsyncClient, auth_headers: dict
) -> None:
    resp = await client.put(
        "/api/registry/namespaces/idc-mint",
        json={"id_config": {"terminologies": {"algorithm": "prefixed", "prefix": "T-", "pad": 3}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/registry/entries/provision",
        json={"namespace": "idc-mint", "entity_type": "terminologies", "count": 1, "created_by": "test"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "T-001" in resp.text


@pytest.mark.asyncio
async def test_archived_response_form_is_accepted(client: AsyncClient, auth_headers: dict) -> None:
    """A same-name restore re-sends the namespace's served id_config: every
    field filled, unused ones null. That form must stay writable."""
    resp = await client.put(
        "/api/registry/namespaces/idc-src",
        json={"id_config": {"terms": {"algorithm": "prefixed", "prefix": "TERM-", "pad": 8}}},
        headers=auth_headers,
    )
    served = resp.json()["id_config"]
    resp = await client.put(
        "/api/registry/namespaces/idc-src", json={"id_config": served}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id_config"] == served


@pytest.mark.asyncio
async def test_a_legacy_value_re_sent_unchanged_is_accepted(client: AsyncClient, auth_headers: dict) -> None:
    """A value stored before validation, re-sent exactly as stored (a resume,
    a same-name restore), is not refused; changing it to another bad value is."""
    legacy = {"terms": {"algorithm": "prefix"}}
    await Namespace(prefix="idc-grandfathered", id_config=legacy).create()
    resp = await client.put(
        "/api/registry/namespaces/idc-grandfathered", json={"id_config": legacy}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    resp = await client.put(
        "/api/registry/namespaces/idc-grandfathered",
        json={"id_config": {"terms": {"algorithm": "prefixd"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_a_bad_stored_value_does_not_fail_the_listing(client: AsyncClient, auth_headers: dict) -> None:
    """A value stored before writes were validated is served as invalid, and
    every other namespace still lists."""
    await Namespace(prefix="idc-legacy", id_config={"terms": "prefixed"}).create()

    resp = await client.get("/api/registry/namespaces", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    legacy = next(n for n in resp.json() if n["prefix"] == "idc-legacy")
    assert "not an object" in legacy["id_config"]["terms"]["invalid"]
    assert legacy["id_config"]["documents"]["algorithm"] == "uuid7"
    assert any(n["prefix"] == "default" for n in resp.json())

    resp = await client.get("/api/registry/namespaces/idc-legacy", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.get("/api/registry/namespaces/idc-legacy/id-config", headers=auth_headers)
    assert resp.status_code == 200 and "invalid" in resp.json()["terms"]


@pytest.mark.asyncio
async def test_import_refuses_a_manifest_with_bad_id_config(client: AsyncClient, tmp_path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({
                "version": "2.0",
                "prefix": "idc-import",
                "id_config": {"terms": {"algorithm": "prefixed"}},
            }),
        )
    svc = ImportService(
        def_store_url="http://def", template_store_url="http://tpl",
        document_store_url="http://doc", api_key="k",
    )
    with pytest.raises(ValueError, match="manifest id_config"):
        await svc.import_namespace(str(archive), mode="create", imported_by="tester")
    assert await Namespace.find_one({"prefix": "idc-import"}) is None


@pytest.mark.asyncio
async def test_a_bad_stored_value_does_not_fail_an_export(client: AsyncClient) -> None:
    # Export builds the manifest's id_config the same way the listing does,
    # so a value stored before validation must be served as the marker there
    # too — otherwise exporting (backing up) that namespace fails outright.
    from unittest.mock import AsyncMock, MagicMock, patch

    from registry.services.export_service import ExportService

    await Namespace(prefix="legacy-export", id_config={"terms": "prefixed"}).insert()
    ns = await Namespace.find_one({"prefix": "legacy-export"})

    empty = MagicMock(status_code=200)
    empty.json.return_value = {"items": [], "total": 0, "pages": 1}
    http = MagicMock()
    http.get = AsyncMock(return_value=empty)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    svc = ExportService(
        def_store_url="http://def", template_store_url="http://tpl",
        document_store_url="http://doc", api_key="k",
    )
    with patch("httpx.AsyncClient", return_value=http):
        zip_path, _stats = await svc.export_namespace(ns)

    with zipfile.ZipFile(zip_path) as zf, zf.open("manifest.json") as m:
        manifest = json.load(m)
    assert "invalid" in manifest["id_config"]["terms"]
    assert manifest["id_config"]["documents"]["algorithm"] == "uuid7"
