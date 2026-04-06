"""Background service that polls the Registry for runtime API keys.

Non-Registry services use this to pick up newly created/revoked keys
without requiring a restart. Polling interval defaults to 30 seconds.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from .models import APIKeyRecord
from .providers.api_key import APIKeyProvider

logger = logging.getLogger("wip_auth.key_sync")


class KeySyncService:
    """Polls Registry /api-keys/sync and updates the local APIKeyProvider."""

    def __init__(
        self,
        registry_url: str,
        api_key: str,
        provider: APIKeyProvider,
        config_key_names: set[str],
        interval_seconds: int = 30,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.api_key = api_key
        self.provider = provider
        self.config_key_names = config_key_names
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Key sync started: registry=%s interval=%ds",
            self.registry_url,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Key sync stopped.")

    async def _poll_loop(self) -> None:
        """Polling loop — runs until stopped."""
        # Initial sync immediately
        await self._sync_once()
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                if self._running:
                    await self._sync_once()
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

            self.provider.replace_runtime_keys(records, self.config_key_names)
            logger.debug("Key sync complete: %d runtime key(s)", len(records))

        except httpx.HTTPStatusError as e:
            logger.warning("Key sync HTTP error: %s %s", e.response.status_code, url)
        except httpx.ConnectError:
            logger.warning("Key sync connection failed: %s", url)
        except Exception:
            logger.exception("Key sync unexpected error")
