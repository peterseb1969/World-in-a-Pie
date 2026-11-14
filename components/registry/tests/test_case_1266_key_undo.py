"""A key create that does not store its key, and a widen that does not
land, keep no reservation they made, and the widen takes back the scope it
added (CASE-1266, 2d4fe859 R3, a225a5d9 R1). One ID prefix is held by one
reservation, kept by a unique index (2d4fe859 R6, a225a5d9 R2)."""

import uuid
from unittest.mock import patch

import pytest

from registry.api import api_keys
from registry.models.api_key import StoredAPIKey
from registry.models.reservation import RuntimeReservation

KEYS = "/api/registry/api-keys"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def _reservation(prefix: str):
    return await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)


@pytest.mark.asyncio
async def test_a_create_whose_key_is_not_stored_keeps_no_reservation(client, auth_headers):
    name, prefix = _p("k"), _p("res")

    async def fail(self, *args, **kwargs):
        raise RuntimeError("the write failed")

    with patch.object(StoredAPIKey, "insert", fail), pytest.raises(RuntimeError):
        await client.post(KEYS, headers=auth_headers, json={
            "name": name, "namespaces": [prefix], "reserve": [{"prefix": prefix, "settings": {}}],
        })
    assert await _reservation(prefix) is None


@pytest.mark.asyncio
async def test_a_widen_that_does_not_land_keeps_no_reservation(client, auth_headers):
    name, home, prefix = _p("k"), _p("home"), _p("res")
    r = await client.post(KEYS, headers=auth_headers, json={"name": name, "namespaces": [home]})
    assert r.status_code == 201, r.text

    async def fail(*args, **kwargs):
        raise RuntimeError("the grant write failed")

    with patch.object(api_keys, "_set_grant", fail), pytest.raises(RuntimeError):
        await client.put(f"{KEYS}/{name}/namespaces/{prefix}", headers=auth_headers,
                         json={"reserve": {}})
    assert await _reservation(prefix) is None
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc.namespaces == [home], "the widened scope was left behind"


def _id_settings(id_prefix: str):
    from registry.services.reservations import ReservationSettings

    return ReservationSettings.model_validate({
        "id_config": {"documents": {"algorithm": "prefixed", "prefix": id_prefix, "pad": 6}},
    })


@pytest.mark.asyncio
async def test_two_writes_claiming_one_id_prefix_leave_only_the_first(client):
    """Two requests can both pass check_new_reservation for one ID prefix;
    the unique index refuses the second write, whichever process makes it."""
    from registry.services import reservation_store
    from registry.services.reservation_store import ReservationRefused

    settings = _id_settings(f"R6{uuid.uuid4().hex[:4].upper()}-")
    first, second = _p("first"), _p("second")
    await reservation_store.save_reservation(first, settings, _p("key-a"), "test")
    with pytest.raises(ReservationRefused, match="is reserved for namespace"):
        await reservation_store.save_reservation(second, settings, _p("key-b"), "test")
    assert await _reservation(first) is not None
    assert await _reservation(second) is None


@pytest.mark.asyncio
async def test_a_re_reservation_moving_onto_a_held_prefix_is_refused(client):
    """The update path is held by the same index (a225a5d9 R2): a key
    re-reserving its namespace at a prefix another reservation holds keeps
    its old settings."""
    from registry.services import reservation_store
    from registry.services.reservation_store import ReservationRefused

    held, own = f"R2H{uuid.uuid4().hex[:4].upper()}-", f"R2O{uuid.uuid4().hex[:4].upper()}-"
    first, second, key_b = _p("first"), _p("second"), _p("key-b")
    await reservation_store.save_reservation(first, _id_settings(held), _p("key-a"), "test")
    await reservation_store.save_reservation(second, _id_settings(own), key_b, "test")
    with pytest.raises(ReservationRefused, match="is reserved for namespace"):
        await reservation_store.save_reservation(second, _id_settings(held), key_b, "test")
    assert (await _reservation(second)).id_prefixes == [own]


@pytest.mark.asyncio
async def test_records_from_before_the_field_are_backfilled_and_clashes_reported(client):
    from registry.services.reservation_store import backfill_id_prefixes

    id_prefix = f"R2B{uuid.uuid4().hex[:4].upper()}-"
    config = {"id_config": {"documents": {"algorithm": "prefixed", "prefix": id_prefix, "pad": 6}}}
    a, b = _p("old-a"), _p("old-b")
    for prefix in (a, b):  # written before the field: no id_prefixes
        await RuntimeReservation(prefix=prefix, app=_p("k"), settings=config).insert()
    clashing = await backfill_id_prefixes()
    assert (await _reservation(a)).id_prefixes == [id_prefix]
    assert (await _reservation(b)).id_prefixes == []
    assert clashing == [b]


@pytest.mark.asyncio
async def test_a_prefix_the_deployer_holds_is_refused_as_the_deployers(client):
    """a225a5d9 R8: the install's reservation holding the prefix is refused as
    managed_by_deployer, as the check before the write refuses it."""
    from registry.services import reservation_store
    from registry.services.managed import ManagedRecordRefused

    id_prefix = f"R8{uuid.uuid4().hex[:4].upper()}-"
    managed = _p("managed")
    await RuntimeReservation(
        prefix=managed, app="the-app", managed_by="wip-deploy", origin="file",
        settings={"id_config": {"documents": {"algorithm": "prefixed", "prefix": id_prefix, "pad": 6}}},
        id_prefixes=[id_prefix],
    ).insert()
    with pytest.raises(ManagedRecordRefused):
        await reservation_store.save_reservation(_p("api"), _id_settings(id_prefix), _p("k"), "test")
