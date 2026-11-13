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
from typing import Any, cast

import httpx

from wip_auth import service_api_key

logger = logging.getLogger(__name__)

# The key this service presents to reporting-sync: the Registry key, else its
# own service key. Read through service_api_key, so neither set is reported as
# missing rather than sent as an empty X-API-Key.
REPORTING_SYNC_KEY: tuple[str, ...] = ("REGISTRY_API_KEY", "WIP_AUTH_LEGACY_API_KEY")


class ReportingSyncClient:
    """Best-effort HTTP client for reporting-sync's restore-facing verbs."""

    def __init__(self) -> None:
        self._base = os.getenv("REPORTING_SYNC_URL", "http://wip-reporting-sync:8005")
        self._api_key: str = service_api_key(*REPORTING_SYNC_KEY)
        # Per namespace, the batch-sync jobs the last trigger answered with
        # (each {job_id, template_value, status, message}), so a caller
        # whose wait timed out can ask why (failed_jobs).
        self._started: dict[str, list[dict[str, Any]]] = {}

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
                    return cast(dict[str, Any], resp.json())
                logger.warning(
                    "reporting parity for %s returned HTTP %s",
                    namespace, resp.status_code,
                )
                return None
        except httpx.HTTPError as e:
            logger.warning("reporting-sync unreachable for parity(%s): %s", namespace, e)
            return None

    async def trigger_batch_sync(self, namespace: str) -> bool:
        """Kick a full namespace batch sync. True when it queued a job.

        The namespace travels as a query parameter — reporting-sync's
        route reads it from the query string; a JSON body is silently
        ignored by FastAPI and the sync degrades to whole-instance.

        The route only acknowledges: its jobs run after it answers, so their
        outcome is not known here. What the answer does say is which jobs it
        started, one per template; an empty list (no template syncs) started
        nothing, and a job already failed when it answered did not start.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base}/api/reporting-sync/sync/batch",
                    params={"namespace": namespace},
                    headers=self._headers(),
                )
                if 200 <= resp.status_code < 300:
                    return self._queued_any(namespace, resp, self._started)
                logger.warning(
                    "reporting batch sync for %s refused: HTTP %s %s",
                    namespace, resp.status_code, resp.text[:200],
                )
                return False
        except httpx.HTTPError as e:
            logger.warning("reporting-sync unreachable for batch sync(%s): %s", namespace, e)
            return False

    @staticmethod
    def _queued_any(
        namespace: str, resp: httpx.Response,
        started: dict[str, list[dict[str, Any]]] | None = None,
    ) -> bool:
        """Whether a batch-sync answer started at least one job; logs those
        that failed to start and an answer that started none. With
        ``started``, records the answer's jobs under the namespace."""
        try:
            jobs = resp.json()
        except ValueError:
            jobs = None
        if not isinstance(jobs, list):
            logger.warning(
                "reporting batch sync for %s: unexpected answer %s",
                namespace, resp.text[:200],
            )
            return False
        if started is not None:
            started[namespace] = [j for j in jobs if isinstance(j, dict)]
        failed = [j for j in jobs if isinstance(j, dict) and j.get("status") == "failed"]
        for job in failed:
            logger.warning(
                "reporting batch sync for %s: template %s did not start: %s",
                namespace, job.get("template_value"), job.get("message"),
            )
        if len(jobs) == len(failed):
            logger.warning("reporting batch sync for %s queued no job", namespace)
            return False
        return True

    async def failed_jobs(self, namespace: str) -> dict[str, str]:
        """Why the last trigger's jobs for ``namespace`` failed, by template
        value: those that did not start (their answer's message) and those
        that started and then failed (the job record's error_message).

        Best effort like the rest of this client: a job that cannot be read
        is left out, and an unreachable reporting-sync answers {}.
        """
        reasons: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for job in self._started.get(namespace, []):
                    value = str(job.get("template_value") or "?")
                    if job.get("status") == "failed":
                        reasons[value] = str(job.get("message") or "did not start")
                        continue
                    if not job.get("job_id"):
                        continue
                    resp = await client.get(
                        f"{self._base}/api/reporting-sync/sync/batch/jobs/{job['job_id']}",
                        headers=self._headers(),
                    )
                    if resp.status_code != 200:
                        continue
                    record = resp.json()
                    if record.get("status") == "failed":
                        reasons[value] = str(record.get("error_message") or "failed")
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("reporting-sync job status for %s unreadable: %s", namespace, e)
        return reasons

    async def drop_namespace_schema(self, namespace: str) -> bool:
        """Drop the namespace's reporting schema. True on success."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(
                    f"{self._base}/api/reporting-sync/namespace/{namespace}",
                    headers=self._headers(),
                )
                if resp.status_code < 300:
                    return True
                logger.warning(
                    "reporting schema drop for %s refused: HTTP %s %s",
                    namespace, resp.status_code, resp.text[:200],
                )
                return False
        except httpx.HTTPError as e:
            logger.warning("reporting-sync unreachable for drop(%s): %s", namespace, e)
            return False
