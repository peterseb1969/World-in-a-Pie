"""A keyless activation never reports an entry activated that it did not
activate.

The guarded update flips only entries still reserved. When it modifies fewer
than it was given, a re-read decides which items failed; it looked only for
entries still reserved, so an entry another request retired (or otherwise
moved off reserved) in between counted as flipped and was reported
activated while it sat inactive. Only an entry the re-read finds active
counts as activated; anything else is a concurrency conflict.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry
from tests.test_case_1161_activate_entries import _keyless, _summary
from tests.test_case_1161_activate_keyed import _activate, _break_collection, _status


@pytest.mark.asyncio
async def test_an_entry_retired_between_the_read_and_the_update_is_not_reported_activated(
    client: AsyncClient, auth_headers, monkeypatch,
):
    first, second = await _keyless(client, auth_headers, 2)
    real = RegistryEntry.get_motor_collection()

    async def update_many(flt: dict[str, Any], update, *a, **kw):
        await real.update_one({"entry_id": second["entry_id"]}, {"$set": {"status": "inactive"}})
        return await real.update_many(flt, update, *a, **kw)

    _break_collection(monkeypatch, update_many=update_many)
    body = await _activate(client, auth_headers, [first, second])
    assert await _status(second["entry_id"]) == "inactive"
    assert _summary(body) == [("activated", None), ("error", "concurrency_conflict")]
    assert (body["activated"], body["errors"]) == (1, 1)
