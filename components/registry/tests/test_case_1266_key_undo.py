"""A key create that does not store its key, and a widen that does not
land, keep no reservation they made (CASE-1266, 2d4fe859 R3)."""

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


@pytest.mark.asyncio
async def test_two_writes_claiming_one_id_prefix_leave_only_the_first(client):
    """Two requests can both pass check_new_reservation for one ID prefix;
    the later write yields (2d4fe859 R6)."""
    from registry.services import reservation_store
    from registry.services.reservation_store import ReservationRefused
    from registry.services.reservations import ReservationSettings

    id_prefix = f"R6{uuid.uuid4().hex[:4].upper()}-"
    settings = ReservationSettings.model_validate({
        "id_config": {"documents": {"algorithm": "prefixed", "prefix": id_prefix, "pad": 6}},
    })
    first, second = _p("first"), _p("second")
    await reservation_store.save_reservation(first, settings, _p("key-a"), "test")
    with pytest.raises(ReservationRefused, match="was reserved for namespace"):
        await reservation_store.save_reservation(second, settings, _p("key-b"), "test")
    assert await _reservation(first) is not None
    assert await _reservation(second) is None
