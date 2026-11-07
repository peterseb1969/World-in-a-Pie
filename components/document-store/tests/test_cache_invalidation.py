"""A deleted namespace drops document-store's own cached namespace data
(reference policy, templates) when the Registry announces it."""

import time

from document_store.services.nats_client import close_nats_client, configure_nats_client
from document_store.services.reference_validator import get_reference_validator
from document_store.services.template_store_client import get_template_store_client
from wip_auth.cache_events import flush_namespace


async def test_a_deleted_namespace_is_forgotten():
    await configure_nats_client("")  # no NATS: the flushers are registered anyway
    try:
        validator = get_reference_validator()
        validator._namespace_cache["gone"] = (time.monotonic(), {"isolation_mode": "strict"})
        validator._namespace_cache["kept"] = (time.monotonic(), {"isolation_mode": "open"})
        templates = get_template_store_client()
        templates._template_cache["T-gone"] = {"namespace": "gone"}
        templates._template_cache["T-kept"] = {"namespace": "kept"}
        templates._ns_stamp["gone"] = ("s1", time.monotonic())

        flush_namespace("gone")

        assert "gone" not in validator._namespace_cache and "kept" in validator._namespace_cache
        assert "T-gone" not in templates._template_cache and "T-kept" in templates._template_cache
        assert "gone" not in templates._ns_stamp
    finally:
        await close_nats_client()
