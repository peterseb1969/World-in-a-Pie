"""The Registry's NATS connection.

The Registry publishes no change events; it uses NATS only to tell every
other service process when its cached answers went stale
(``wip_auth.cache_events``). Without ``NATS_URL`` the bus is disabled and
those caches expire on their TTL.
"""

from __future__ import annotations

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
