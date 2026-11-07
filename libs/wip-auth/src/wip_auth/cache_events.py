"""Cache invalidation: the Registry says when cached answers went stale.

Every service process caches what the Registry told it: name resolutions
(``wip_auth.resolve``, 30 s), grants (``wip_auth.permissions``, 30 s) and
service-local namespace data such as reference policies. The caches are keyed
by namespace *name*, so after a namespace is deleted and re-created under the
same name, a read could keep resolving to the deleted namespace's ids until
the entries expired: a 404 for an entity that exists.

The Registry closes that window. When a namespace deletion has run, it
publishes ``NAMESPACE_DELETED_SUBJECT`` on NATS, and every process that
subscribed drops what it cached:

* the whole resolution cache and the whole grant cache. A namespace's ids can
  sit under keys of *other* namespaces (a cross-namespace reference, a UUID
  read from anywhere), so a prefix filter would miss some; a deletion is rare
  and a full clear costs a few Registry round trips;
* whatever a service registered with ``on_namespace_deleted``, for its own
  namespace-keyed caches.

The message is plain NATS publish/subscribe, at most once, and stored by no
stream: a message that is lost (NATS down, a process reconnecting) leaves the
caches to expire on their TTL, which stays the bound. Without NATS — a
deployment that does not run it, or a service not connected to it — nothing
is published or received and the TTL is the whole mechanism, as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from .events import EventBus
from .permissions import clear_permission_cache
from .resolve import clear_resolution_cache

logger = logging.getLogger(__name__)

NAMESPACE_DELETED_SUBJECT = "wip.cache.namespace.deleted"
"""Published by the Registry after a namespace deletion has run; payload
``{"namespace": "<prefix>"}``. Outside every JetStream stream's subjects, so
nothing stores it."""

_namespace_flushers: list[Callable[[str], None]] = []


def on_namespace_deleted(flush: Callable[[str], None]) -> None:
    """Register ``flush(prefix)``, called when a namespace has been deleted.

    For a service's own namespace-keyed caches; the shared wip-auth caches
    are always cleared. Registering the same callable twice is a no-op.
    """
    if flush not in _namespace_flushers:
        _namespace_flushers.append(flush)


def flush_namespace(prefix: str) -> None:
    """Drop everything this process cached that a deleted namespace can
    have made stale."""
    clear_resolution_cache()
    clear_permission_cache()
    for flush in list(_namespace_flushers):
        try:
            flush(prefix)
        except Exception:  # one broken flusher must not keep the others stale
            logger.exception("cache flush for deleted namespace '%s' failed", prefix)
    logger.info("caches flushed for deleted namespace '%s'", prefix)


async def _handle(msg: Any) -> None:
    try:
        prefix = json.loads(msg.data)["namespace"]
    except Exception:
        logger.warning("ignoring malformed %s message: %r", msg.subject, msg.data[:200])
        return
    if isinstance(prefix, str) and prefix:
        flush_namespace(prefix)


def subscribe_cache_invalidation(bus: EventBus) -> asyncio.Task[None] | None:
    """Receive the Registry's invalidation messages on ``bus`` for as long as
    it lives. None, and nothing to do, when the bus is not configured.

    Runs under ``bus.supervise``, so the subscription is made again on every
    new client. Messages published while this process was disconnected are
    not delivered; the caches' TTL covers them.
    """
    if not bus.configured:
        return None

    async def run(_js: Any) -> None:
        nc = bus.client
        if nc is None:
            raise RuntimeError("not connected")
        sub = await nc.subscribe(NAMESPACE_DELETED_SUBJECT, cb=_handle)
        try:
            await asyncio.Event().wait()  # until the supervisor cancels us
        finally:
            if not nc.is_closed:
                try:
                    await sub.unsubscribe()
                except Exception:
                    logger.debug("unsubscribe from %s failed", NAMESPACE_DELETED_SUBJECT)

    return bus.supervise("cache-invalidation", run)


async def announce_namespace_deleted(bus: EventBus, prefix: str) -> bool:
    """Tell every subscribed process that ``prefix`` was deleted. True when
    the message was handed to NATS; False when the bus is not configured or
    not connected (then the caches expire on their TTL)."""
    nc = bus.client
    if nc is None:
        if bus.configured:
            logger.warning(
                "NATS not connected: caches elsewhere keep namespace '%s' until "
                "their TTL", prefix,
            )
        return False
    try:
        await nc.publish(NAMESPACE_DELETED_SUBJECT, json.dumps({"namespace": prefix}).encode())
        await nc.flush(timeout=2)
    except Exception as e:
        logger.warning("announcing deleted namespace '%s' failed: %s", prefix, e)
        return False
    return True
