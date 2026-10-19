"""Publishes ingest results to NATS WIP_INGEST_RESULTS stream."""

import hashlib
import json
import logging

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
            payload = json.dumps(result.model_dump_json_safe()).encode()

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
