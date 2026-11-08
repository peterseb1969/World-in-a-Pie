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

When single entities lose names, the Registry publishes
``ENTRIES_CHANGED_SUBJECT`` with their ids: an entry hard- or soft-deleted, a
synonym removed or released when its entity was retired, an entry merged into
another (its names, and its id, now resolve to the survivor). Every process
drops the resolutions it cached to those ids, whatever spelling they were
cached under, so a name re-created for another entity, or moved to one,
resolves to it at once.

When a key or a grant changes, the Registry publishes
``ACCESS_CHANGED_SUBJECT``: every process drops its grant cache, and — when
keys changed (created, changed, narrowed, widened, revoked) — re-syncs the
runtime keys at once instead of at its next poll, so a key created a moment
ago is accepted, and a revoked one refused, everywhere within about a second.

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
from collections.abc import Callable, Iterable
from typing import Any

from .events import EventBus
from .permissions import clear_permission_cache
from .resolve import clear_resolution_cache, forget_resolutions

logger = logging.getLogger(__name__)

NAMESPACE_DELETED_SUBJECT = "wip.cache.namespace.deleted"
"""Published by the Registry after a namespace deletion has run; payload
``{"namespace": "<prefix>"}``. Outside every JetStream stream's subjects, so
nothing stores it."""

ENTRIES_CHANGED_SUBJECT = "wip.cache.entries.changed"
"""Published by the Registry when entries lost names or were removed;
payload ``{"entry_ids": [...]}``. Outside every stream, like the above."""

ACCESS_CHANGED_SUBJECT = "wip.cache.access.changed"
"""Published by the Registry when keys or grants changed; payload
``{"keys": true|false}`` (true: the runtime key set changed, re-sync it).
Outside every stream, like the above."""

# Ids per message: a bulk delete of thousands stays far below NATS's payload
# limit (a UUID is ~40 bytes in JSON).
_IDS_PER_MESSAGE = 1000

_namespace_flushers: list[Callable[[str], None]] = []
_key_resyncers: list[Callable[[], None]] = []


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


def on_keys_changed(resync: Callable[[], None]) -> None:
    """Register ``resync()``, called when the Registry says runtime keys
    changed. KeySyncService registers its immediate sync. Registering the
    same callable twice is a no-op."""
    if resync not in _key_resyncers:
        _key_resyncers.append(resync)


def off_keys_changed(resync: Callable[[], None]) -> None:
    """Unregister a callable registered with ``on_keys_changed``."""
    if resync in _key_resyncers:
        _key_resyncers.remove(resync)


def access_changed(keys: bool) -> None:
    """Drop this process's cached grants; when ``keys``, re-sync its keys."""
    clear_permission_cache()
    if not keys:
        return
    for resync in list(_key_resyncers):
        try:
            resync()
        except Exception:  # one broken listener must not keep the others stale
            logger.exception("key re-sync after a key change failed to start")


def forget_entries(entry_ids: Iterable[str]) -> int:
    """Drop this process's cached resolutions to ``entry_ids``."""
    dropped = forget_resolutions(entry_ids)
    if dropped:
        logger.info("dropped %d cached resolution(s) to changed entries", dropped)
    return dropped


async def _handle(msg: Any) -> None:
    try:
        body = json.loads(msg.data)
        if msg.subject == ACCESS_CHANGED_SUBJECT:
            access_changed(bool(body.get("keys")))
            return
        if msg.subject == ENTRIES_CHANGED_SUBJECT:
            ids = body["entry_ids"]
            if not isinstance(ids, list):
                raise ValueError("entry_ids is not a list")
            forget_entries(str(i) for i in ids if i)
            return
        prefix = body["namespace"]
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
        subs = [
            await nc.subscribe(NAMESPACE_DELETED_SUBJECT, cb=_handle),
            await nc.subscribe(ENTRIES_CHANGED_SUBJECT, cb=_handle),
            await nc.subscribe(ACCESS_CHANGED_SUBJECT, cb=_handle),
        ]
        try:
            await asyncio.Event().wait()  # until the supervisor cancels us
        finally:
            if not nc.is_closed:
                for sub in subs:
                    try:
                        await sub.unsubscribe()
                    except Exception:
                        logger.debug("unsubscribe from %s failed", sub.subject)

    return bus.supervise("cache-invalidation", run)


async def _publish(bus: EventBus, subject: str, payloads: list[bytes], what: str) -> bool:
    nc = bus.client
    if nc is None:
        if bus.configured:
            logger.warning("NATS not connected: caches elsewhere keep %s until their TTL", what)
        return False
    try:
        for payload in payloads:
            await nc.publish(subject, payload)
        await nc.flush(timeout=2)
    except Exception as e:
        logger.warning("announcing %s failed: %s", what, e)
        return False
    return True


async def announce_namespace_deleted(bus: EventBus, prefix: str) -> bool:
    """Tell every subscribed process that ``prefix`` was deleted. True when
    the message was handed to NATS; False when the bus is not configured or
    not connected (then the caches expire on their TTL)."""
    payload = json.dumps({"namespace": prefix}).encode()
    return await _publish(
        bus, NAMESPACE_DELETED_SUBJECT, [payload], f"deleted namespace '{prefix}'",
    )


async def announce_entries_changed(bus: EventBus, entry_ids: Iterable[str]) -> bool:
    """Tell every subscribed process that ``entry_ids`` lost names or are
    gone. True when there was nothing to say or it was handed to NATS; False
    when the bus is not configured or not connected (TTL fallback)."""
    ids = sorted({str(i) for i in entry_ids if i})
    if not ids:
        return True
    payloads = [
        json.dumps({"entry_ids": ids[start:start + _IDS_PER_MESSAGE]}).encode()
        for start in range(0, len(ids), _IDS_PER_MESSAGE)
    ]
    return await _publish(bus, ENTRIES_CHANGED_SUBJECT, payloads, f"{len(ids)} changed entr(ies)")


async def announce_access_changed(bus: EventBus, keys: bool) -> bool:
    """Tell every subscribed process that grants changed and, with ``keys``,
    that runtime keys changed too. True when handed to NATS; False when the
    bus is not configured or not connected (then keys wait for the next poll
    and grants for their cache TTL)."""
    payload = json.dumps({"keys": keys}).encode()
    return await _publish(
        bus, ACCESS_CHANGED_SUBJECT, [payload], "changed keys" if keys else "changed grants",
    )
