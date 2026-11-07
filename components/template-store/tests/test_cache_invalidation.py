"""A deleted namespace drops template-store's cached reference policy when
the Registry announces it."""

import time

from template_store.services.nats_client import close_nats_client, configure_nats_client
from template_store.services.reference_validator import get_reference_validator
from wip_auth.cache_events import flush_namespace


async def test_a_deleted_namespace_is_forgotten():
    await configure_nats_client("")
    try:
        validator = get_reference_validator()
        validator._namespace_cache["gone"] = (time.monotonic(), {"isolation_mode": "strict"})
        validator._namespace_cache["kept"] = (time.monotonic(), {"isolation_mode": "open"})

        flush_namespace("gone")

        assert "gone" not in validator._namespace_cache and "kept" in validator._namespace_cache
    finally:
        await close_nats_client()
