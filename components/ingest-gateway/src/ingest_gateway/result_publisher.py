"""Publishes ingest results to NATS WIP_INGEST_RESULTS stream."""

import json
import logging

from nats.js import JetStreamContext

from .models import IngestResult

logger = logging.getLogger(__name__)


class ResultPublisher:
    """Publishes ingest results to NATS results stream."""

    def __init__(self, jetstream: JetStreamContext):
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
            # Subject includes correlation_id for easy filtering
            subject = f"wip.ingest.results.{result.correlation_id}"
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
