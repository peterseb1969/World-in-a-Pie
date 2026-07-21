"""Thin client for reporting-sync, used by the restore verification phases.

The restore engine writes directly to MongoDB and bypasses the NATS event
path, so the reporting layer only learns about restored data when told. This
client carries the three verbs the phased restore verification needs:
parity (is postgres structurally/numerically consistent for a namespace),
trigger (kick a namespace batch sync), and drop (clear a stale reporting
schema, gated by the caller's explicit opt-in).

Reporting-sync is an optional module (the core preset deploys without it),
so every method degrades to None/False on unreachability instead of raising —
the engine turns that into a logged warning and skips the reporting phases.
A restore must never fail because the convenience layer is absent; it must
only fail on POSITIVE verification failures.
"""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ReportingSyncClient:
    """Best-effort HTTP client for reporting-sync's restore-facing verbs."""

    def __init__(self) -> None:
        self._base = os.getenv("REPORTING_SYNC_URL", "http://wip-reporting-sync:8005")
        self._api_key = os.getenv("REGISTRY_API_KEY") or os.getenv(
            "WIP_AUTH_LEGACY_API_KEY", ""
        )

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    async def parity(
        self, namespace: str, include_counts: bool = False
    ) -> dict[str, Any] | None:
        """Namespace parity result as a dict, or None when unreachable."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self._base}/api/reporting-sync/parity",
                    params={
                        "namespace": namespace,
                        "include_counts": str(include_counts).lower(),
                    },
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(
                    "reporting parity for %s returned HTTP %s",
                    namespace, resp.status_code,
                )
                return None
        except httpx.HTTPError as e:
            logger.warning("reporting-sync unreachable for parity(%s): %s", namespace, e)
            return None

    async def trigger_batch_sync(self, namespace: str) -> bool:
        """Kick a full namespace batch sync. True when accepted.

        The namespace travels as a query parameter — reporting-sync's
        route reads it from the query string; a JSON body is silently
        ignored by FastAPI and the sync degrades to whole-instance.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base}/api/reporting-sync/sync/batch",
                    params={"namespace": namespace},
                    headers=self._headers(),
                )
                return resp.status_code < 300
        except httpx.HTTPError as e:
            logger.warning("reporting-sync unreachable for batch sync(%s): %s", namespace, e)
            return False

    async def drop_namespace_schema(self, namespace: str) -> bool:
        """Drop the namespace's reporting schema. True on success."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(
                    f"{self._base}/api/reporting-sync/namespace/{namespace}",
                    headers=self._headers(),
                )
                return resp.status_code < 300
        except httpx.HTTPError as e:
            logger.warning("reporting-sync unreachable for drop(%s): %s", namespace, e)
            return False
