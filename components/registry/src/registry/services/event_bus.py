"""The Registry's NATS connection.

The Registry publishes no change events; it uses NATS only to tell every
other service process when its cached answers went stale
(``wip_auth.cache_events``). Without ``NATS_URL`` the bus is disabled and
those caches expire on their TTL.
"""

from __future__ import annotations

from collections.abc import Iterable

from wip_auth.cache_events import announce_access_changed, announce_entries_changed
from wip_auth.events import EventBus

_bus: EventBus = EventBus(None, name="registry")


async def start_event_bus(nats_url: str | None) -> bool:
    """Start the bus in the background; True when NATS is configured."""
    global _bus
    _bus = EventBus(nats_url or None, name="registry")
    await _bus.start()
    return _bus.configured


async def stop_event_bus() -> None:
    await _bus.stop()


def get_event_bus() -> EventBus:
    return _bus


async def entries_changed(entry_ids: Iterable[str]) -> None:
    """Tell other services' caches that these entries lost names or are gone
    (deleted, synonyms removed or released, merged into another entry)."""
    await announce_entries_changed(_bus, entry_ids)


async def access_changed(keys: bool = True) -> None:
    """Tell other services that grants changed and, with ``keys``, that
    runtime keys did: they drop their cached grants and re-sync their keys at
    once, instead of at their next poll (30 s) and their grant cache's TTL."""
    await announce_access_changed(_bus, keys)
