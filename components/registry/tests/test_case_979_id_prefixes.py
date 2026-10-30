"""A `prefixed` ID prefix names one ID sequence on the install.

IDs are minted from a counter per (namespace, entity type, prefix), but an
entry_id is unique across the whole Registry. Two namespaces with the prefix
'PROBEPFX-' both minted 'PROBEPFX-000001'; the second write failed with a raw
E11000. Every namespace write now refuses a prefix another (namespace, entity
type) holds, an empty prefix, and a prefix ending in a digit ('DOC-00' and
'DOC-' both mint 'DOC-000010'), before anything is stored. Configs stored
before the rule are grandfathered: re-sending them is never refused, and the
startup check only reports them.
"""

import json
import zipfile

import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace
from registry.services.id_prefixes import IdPrefixError, backfill_id_prefixes
from registry.services.import_service import ImportService


def _cfg(**by_type: str) -> dict:
    return {t: {"algorithm": "prefixed", "prefix": p, "pad": 6} for t, p in by_type.items()}


async def _create(client: AsyncClient, headers: dict, prefix: str, id_config: dict):
    return await client.post(
        "/api/registry/namespaces", headers=headers,
        json={"prefix": prefix, "id_config": id_config},
    )


async def _put(client: AsyncClient, headers: dict, prefix: str, body: dict):
    return await client.put(f"/api/registry/namespaces/{prefix}", headers=headers, json=body)


@pytest.mark.asyncio
async def test_two_namespaces_cannot_share_a_prefix(client: AsyncClient, auth_headers: dict):
    first = await _create(client, auth_headers, "pfx-one", _cfg(terminologies="PFX-"))
    assert first.status_code == 200, first.text

    # Same prefix on another namespace, any entity type: refused, naming the holder.
    second = await _create(client, auth_headers, "pfx-two", _cfg(documents="PFX-"))
    assert second.status_code == 409, second.text
    assert "pfx-one" in second.json()["detail"]
    assert "terminologies" in second.json()["detail"]
    assert await Namespace.find_one({"prefix": "pfx-two"}) is None, "nothing stored"

    # The upsert's create path and update path are refused the same way.
    created = await _put(client, auth_headers, "pfx-three", {"id_config": _cfg(terms="PFX-")})
    assert created.status_code == 409, created.text
    assert await Namespace.find_one({"prefix": "pfx-three"}) is None

    ok = await _put(client, auth_headers, "pfx-four", {"id_config": _cfg(terms="FOUR-")})
    assert ok.status_code == 200, ok.text
    changed = await _put(client, auth_headers, "pfx-four", {"id_config": _cfg(terms="PFX-")})
    assert changed.status_code == 409, changed.text
    stored = await Namespace.find_one({"prefix": "pfx-four"})
    assert stored.id_config["terms"]["prefix"] == "FOUR-", "a refused update stores nothing"


@pytest.mark.asyncio
async def test_two_entity_types_of_one_namespace_cannot_share_a_prefix(
    client: AsyncClient, auth_headers: dict,
):
    r = await _create(client, auth_headers, "pfx-same", _cfg(terminologies="X-", terms="X-"))
    assert r.status_code == 409, r.text
    assert "terminologies" in r.json()["detail"] and "terms" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "DOC0", "DOC-00"])
async def test_empty_or_digit_ending_prefix_is_refused(
    client: AsyncClient, auth_headers: dict, prefix: str,
):
    r = await _create(client, auth_headers, "pfx-bad", _cfg(documents=prefix))
    assert r.status_code == 422, r.text
    no_prefix = {"documents": {"algorithm": "prefixed"}}
    r = await _create(client, auth_headers, "pfx-bad", no_prefix)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_re_sending_the_stored_config_is_never_refused(
    client: AsyncClient, auth_headers: dict,
):
    # A config stored before the rule (here: an empty prefix, inserted raw)
    # is grandfathered — a resume or same-name restore re-sends it unchanged.
    legacy = {"documents": {"algorithm": "prefixed", "prefix": "", "pad": 6}}
    await Namespace(prefix="pfx-legacy", id_config=legacy).insert()
    r = await _put(client, auth_headers, "pfx-legacy", {"id_config": legacy, "description": "x"})
    assert r.status_code == 200, r.text

    ok = await _create(client, auth_headers, "pfx-own", _cfg(files="OWN-"))
    assert ok.status_code == 200
    again = await _put(client, auth_headers, "pfx-own", {"id_config": _cfg(files="OWN-")})
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_import_cannot_take_a_held_prefix(client: AsyncClient, tmp_path):
    held = await Namespace.find_one({"prefix": "default"})
    held.id_config = _cfg(templates="TPL-")
    held.id_prefixes = ["TPL-"]
    await held.save()

    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "prefix": "default", "description": "copy",
            "isolation_mode": "open", "allowed_external_refs": [],
            "id_config": _cfg(templates="TPL-"),
        }))
    svc = ImportService(
        def_store_url="http://def", template_store_url="http://tpl",
        document_store_url="http://doc", api_key="k",
    )
    with pytest.raises(IdPrefixError) as exc:
        await svc.import_namespace(str(archive), target_prefix="default-copy", mode="create")
    assert exc.value.status_code == 409
    assert await Namespace.find_one({"prefix": "default-copy"}) is None


@pytest.mark.asyncio
async def test_backfill_claims_and_reports_existing_conflicts(client: AsyncClient):
    # Two namespaces written before the rule with the same prefix: the first
    # claims it, the second is reported, and startup does not fail.
    await Namespace(prefix="pfx-old-a", id_config=_cfg(documents="OLD-")).insert()
    await Namespace(prefix="pfx-old-b", id_config=_cfg(terms="OLD-")).insert()

    warnings = await backfill_id_prefixes()
    a = await Namespace.find_one({"prefix": "pfx-old-a"})
    b = await Namespace.find_one({"prefix": "pfx-old-b"})
    assert (a.id_prefixes, b.id_prefixes) in ((["OLD-"], []), ([], ["OLD-"]))
    assert any("OLD-" in w for w in warnings), warnings

    # Idempotent, and the claim now blocks a new namespace.
    assert await backfill_id_prefixes() == warnings
    r = await _create(client, {"X-API-Key": "test_master_key"}, "pfx-new", _cfg(files="OLD-"))
    assert r.status_code == 409, r.text
