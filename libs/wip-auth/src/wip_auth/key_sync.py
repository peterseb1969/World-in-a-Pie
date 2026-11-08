"""Background service that polls the Registry for runtime API keys.

Non-Registry services use this to pick up newly created/revoked keys
without requiring a restart. Polling interval defaults to 30 seconds.

The synced copy is a cache of the Registry's keys, and like every cache it
answers only for a bounded time: each successful sync confirms the runtime
keys for ttl_seconds (default three poll intervals, 90 s — two missed polls
are absorbed, including one that times out). Past that, the provider stops
answering for runtime keys and authentication returns 503 until a sync
succeeds again; a failed sync never extends the deadline. Config keys are
not a copy of the Registry and keep working. Before the first successful
sync the runtime keys count as unconfirmed.

Between polls, a service also syncs at once when the Registry announces over
NATS that keys changed (``cache_events.ACCESS_CHANGED_SUBJECT``): a key the
Registry just created, narrowed or revoked reaches the service in about a
second. Announcements that overlap a running sync are folded into one more
sync after it. Without NATS, the poll is the whole mechanism.

The response carries the salt the Registry hashes runtime keys with, in the
RUNTIME_SALT_HEADER header; the provider verifies runtime keys under it from
the same sync on. A response without the header (a Registry older than the
stored salt) leaves the provider's runtime salt as it is.
"""

import asyncio
import contextlib
import logging

import httpx

from .cache_events import off_keys_changed, on_keys_changed
from .models import APIKeyRecord
from .providers.api_key import APIKeyProvider

logger = logging.getLogger("wip_auth.key_sync")

# The response header carrying the Registry's runtime-key salt.
RUNTIME_SALT_HEADER = "X-WIP-Key-Hash-Salt"


class KeySyncService:
    """Polls Registry /api-keys/sync and updates the local APIKeyProvider."""

    def __init__(
        self,
        registry_url: str,
        api_key: str,
        provider: APIKeyProvider,
        config_key_names: set[str] | None = None,
        interval_seconds: int = 30,
        ttl_seconds: float | None = None,
    ):
        # config_key_names is accepted for older callers and not used: the
        # provider knows its current config keys, which a key-file reload
        # may have changed since startup.
        self.registry_url = registry_url.rstrip("/")
        self.api_key = api_key
        self.provider = provider
        self.interval_seconds = interval_seconds
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else 3 * interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self._sync_lock = asyncio.Lock()
        self._again = False
        self._pending: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Start the background polling loop."""
        self.provider.expire_runtime_keys()
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        on_keys_changed(self.request_sync)
        logger.info(
            "Key sync started: registry=%s interval=%ds ttl=%gs",
            self.registry_url,
            self.interval_seconds,
            self.ttl_seconds,
        )

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        off_keys_changed(self.request_sync)
        for task in list(self._pending):
            task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Key sync stopped.")

    def request_sync(self) -> None:
        """Sync now, in the background (the Registry announced a key change)."""
        if not self._running:
            return
        task = asyncio.get_running_loop().create_task(self.sync_now())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def sync_now(self) -> None:
        """Sync, or — when a sync is already running — sync once more after
        it, so a change announced mid-sync is never missed."""
        if self._sync_lock.locked():
            self._again = True
            return
        async with self._sync_lock:
            while True:
                self._again = False
                await self._sync_once()
                if not self._again:
                    break

    async def _poll_loop(self) -> None:
        """Polling loop — runs until stopped."""
        # Initial sync immediately
        await self.sync_now()
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                if self._running:
                    await self.sync_now()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Key sync error (will retry)")

    async def _sync_once(self) -> None:
        """Fetch runtime keys from Registry and replace local copy."""
        url = f"{self.registry_url}/api/registry/api-keys/sync"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url, headers={"X-API-Key": self.api_key}
                )
                resp.raise_for_status()

            records = [
                APIKeyRecord(
                    name=item["name"],
                    key_hash=item["key_hash"],
                    owner=item.get("owner", "system"),
                    groups=item.get("groups", []),
                    description=item.get("description"),
                    created_at=item.get("created_at"),
                    expires_at=item.get("expires_at"),
                    enabled=item.get("enabled", True),
                    namespaces=item.get("namespaces"),
                )
                for item in resp.json()
            ]

            salt = resp.headers.get(RUNTIME_SALT_HEADER)
            if salt:
                self.provider.set_runtime_salt(salt)
            self.provider.replace_runtime_keys(records)
            self.provider.confirm_runtime_keys(self.ttl_seconds)
            logger.debug("Key sync complete: %d runtime key(s)", len(records))

        except httpx.HTTPStatusError as e:
            logger.warning("Key sync HTTP error: %s %s", e.response.status_code, url)
        except (httpx.ConnectError, httpx.TimeoutException):
            # An unreachable or still-starting Registry is routine (parallel
            # start, a Registry restart) and the next poll retries: a warning,
            # not a traceback. A connect timeout is a TimeoutException, not a
            # ConnectError, so both are named.
            logger.warning("Key sync connection failed: %s", url)
        except Exception:
            logger.exception("Key sync unexpected error")
