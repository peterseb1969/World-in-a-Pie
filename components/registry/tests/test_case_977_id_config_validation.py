"""Namespace writes validate id_config; one bad stored value cannot fail the list.

id_config used to be a free dict on create, upsert and import. A misspelt
entity type or field was stored and silently ignored, an unknown algorithm
failed every later mint with a misleading error, a prefixed config without a
prefix minted bare numbers, and a non-object value was stored even though the
upsert answered 500, after which every namespace listing on the install
failed. These tests pin the refusals, the stored shape a valid config still
mints from, and the tolerant read for values stored before validation.
"""


import pytest
from httpx import AsyncClient

from registry.models.id_algorithm import VALID_ENTITY_TYPES, IdEntityType
from registry.models.namespace import Namespace

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
    pytest.param({"terminologies": {"algorithm": "nanoid", "length": 3}}, id="nanoid-collides-fast"),
    pytest.param({"terminologies": {"algorithm": "nanoid", "length": 65}}, id="nanoid-too-long"),
    pytest.param({"terminologies": {"algorithm": "prefixed", "prefix": "T-", "pad": 21}}, id="pad-too-wide"),
    pytest.param({"terminologies": {"algorithm": "prefixed", "prefix": "INV/"}}, id="prefix-slash"),
    pytest.param({"terminologies": {"algorithm": "prefixed", "prefix": "INV:"}}, id="prefix-colon"),
    pytest.param({"terminologies": {"algorithm": "prefixed", "prefix": "IN V-"}}, id="prefix-space"),
    pytest.param({"terminologies": {"algorithm": "prefixed", "prefix": "P" * 33}}, id="prefix-too-long"),
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




LEGACY_BOUNDS = {"documents": {"algorithm": "nanoid", "length": 3}}


@pytest.mark.asyncio
async def test_a_stored_config_read_back_and_re_sent_is_accepted(
    client: AsyncClient, auth_headers: dict
) -> None:
    """GET serves every entity type with defaults filled in; PUTting that back
    mints the same IDs as the stored config, so it is accepted, and the
    stored form is kept."""
    await Namespace(prefix="idc-roundtrip", id_config=LEGACY_BOUNDS).create()
    served = (await client.get(
        "/api/registry/namespaces/idc-roundtrip", headers=auth_headers
    )).json()["id_config"]
    resp = await client.put(
        "/api/registry/namespaces/idc-roundtrip", json={"id_config": served}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    stored = await Namespace.find_one({"prefix": "idc-roundtrip"})
    assert stored is not None and stored.id_config == LEGACY_BOUNDS


@pytest.mark.asyncio
async def test_an_archive_re_create_keeps_its_stored_config(
    client: AsyncClient, auth_headers: dict
) -> None:
    """An ID-preserving restore re-creates the namespace with the archive's
    namespace_id and stored id_config; a config the platform once accepted is
    not refused there, while a plain create with the same config is."""
    resp = await client.put(
        "/api/registry/namespaces/idc-archived",
        json={"id_config": LEGACY_BOUNDS, "namespace_id": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    stored = await Namespace.find_one({"prefix": "idc-archived"})
    assert stored is not None and stored.id_config == LEGACY_BOUNDS

    resp = await client.put(
        "/api/registry/namespaces/idc-plain", json={"id_config": LEGACY_BOUNDS}, headers=auth_headers
    )
    assert resp.status_code == 422, resp.text


def test_only_a_platform_admin_re_creates_from_an_archive() -> None:
    """namespace_id in the body is what marks an archive re-create; a caller
    who is not a platform admin gains nothing by sending it."""
    from fastapi.exceptions import RequestValidationError

    from registry.api.namespaces import _validated_upsert_data
    from registry.models.api_models import NamespaceUpdate

    body = NamespaceUpdate(id_config=LEGACY_BOUNDS, namespace_id=None)
    with pytest.raises(RequestValidationError):
        _validated_upsert_data(body, None, is_admin=False)
    assert _validated_upsert_data(body, None, is_admin=True)["id_config"] == LEGACY_BOUNDS


@pytest.mark.asyncio
async def test_bounds_hold_for_a_field_of_another_algorithm(
    client: AsyncClient, auth_headers: dict
) -> None:
    """The pad and length ranges apply whatever the algorithm; an in-range
    field of another algorithm is stored and has no effect."""
    resp = await client.put(
        "/api/registry/namespaces/idc-xalg-bad",
        json={"id_config": {"documents": {"algorithm": "uuid7", "pad": 50}}},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    resp = await client.put(
        "/api/registry/namespaces/idc-xalg-ok",
        json={"id_config": {"documents": {"algorithm": "uuid7", "pad": 4}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
