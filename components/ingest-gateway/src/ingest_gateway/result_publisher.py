"""Publishes ingest results to NATS WIP_INGEST_RESULTS stream."""

import hashlib
import json
import logging
from typing import Any

from nats.js import JetStreamContext

from .models import IngestResult

logger = logging.getLogger(__name__)

RESULTS_SUBJECT_PREFIX = "wip.ingest.results"

# Owner token of outcomes whose item carried no usable API key. Readable by
# admins only: no caller authenticates as it. Cannot collide with a hashed
# owner, which is hex.
UNAUTHENTICATED_OWNER = "_unauthenticated"


def owner_token(submitted_by: str | None) -> str:
    """The subject token of a principal's outcomes. Hashed: principal names
    are free text and may contain '.', which would split the token."""
    if not submitted_by:
        return UNAUTHENTICATED_OWNER
    return hashlib.sha256(submitted_by.encode()).hexdigest()[:16]


def results_subject(submitted_by: str | None, correlation_id: str) -> str:
    """The subject an outcome is published on and looked up by.

    wip.ingest.results.<owner>.<correlation>: scoping by owner keeps one
    producer's outcomes apart from another's, even when both chose the same
    correlation_id. The correlation_id is producer-chosen free text, so it
    enters the subject hashed (the payload carries it verbatim): whitespace
    makes a subject unpublishable — the server drops the connection — '.'
    splits it into extra tokens, and a '*' or '>' token would turn the
    lookup's last-message-by-subject read into a wildcard match.
    """
    correlation = hashlib.sha256(correlation_id.encode()).hexdigest()[:32]
    return f"{RESULTS_SUBJECT_PREFIX}.{owner_token(submitted_by)}.{correlation}"


# NATS's default max_payload, used when the connection does not report one.
DEFAULT_MAX_PAYLOAD = 1024 * 1024
_COUNT_FIELDS = ("total", "succeeded", "failed")


def _max_payload(js: JetStreamContext | None) -> int:
    """The largest message the connection behind ``js`` may publish, as the
    server announced it."""
    limit = getattr(getattr(js, "_nc", None), "max_payload", None)
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        return limit
    return DEFAULT_MAX_PAYLOAD


def _counts_only(response: dict[str, Any] | None) -> dict[str, Any]:
    """An answer reduced to its counts: a bulk answer keeps total, succeeded
    and failed with no items listed; any other answer is only marked
    truncated."""
    counts = {k: response[k] for k in _COUNT_FIELDS if response and k in response}
    if not counts:
        return {"truncated": True}
    return {**counts, "results_truncated": True, "results": []}


def _fitted_payload(result: IngestResult, limit: int) -> bytes:
    """The outcome as published. One over the connection's limit can never be
    published, and retrying it would only fail again while its producer never
    learns the item's fate, so it goes out with its answer cut to counts."""
    published = result.model_dump_json_safe()
    payload = json.dumps(published).encode()
    if len(payload) <= limit:
        return payload
    logger.warning(
        f"Outcome of correlation_id={result.correlation_id} is {len(payload)} bytes, "
        f"over the bus's limit of {limit}; published with its answer's counts only"
    )
    published["response"] = _counts_only(result.response)
    return json.dumps(published).encode()


class ResultPublisher:
    """Publishes ingest results to NATS results stream."""

    def __init__(self, jetstream: JetStreamContext | None):
        # Rebound by the supervised worker run to the live connection's context.
        self.js = jetstream
        self._published_count = 0
        self._failed_count = 0

    @property
    def published_count(self) -> int:
        """Number of results successfully published."""
        return self._published_count

    @property
    def failed_count(self) -> int:
        """Number of results that failed to publish."""
        return self._failed_count

    async def publish(self, result: IngestResult) -> bool:
        """
        Publish an ingest result to the results stream.

        Args:
            result: The IngestResult to publish

        Returns:
            True if published successfully, False otherwise
        """
        try:
            if self.js is None:
                raise RuntimeError("NATS not connected")
            subject = results_subject(result.submitted_by, result.correlation_id)
            payload = _fitted_payload(result, _max_payload(self.js))

            ack = await self.js.publish(subject, payload)
            logger.debug(
                f"Published result for {result.correlation_id} "
                f"(seq={ack.seq}, status={result.status.value})"
            )
            self._published_count += 1
            return True

        except Exception as e:
            logger.error(f"Failed to publish result for {result.correlation_id}: {e}")
            self._failed_count += 1
            return False
