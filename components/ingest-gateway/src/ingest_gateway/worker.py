"""
Ingest Worker - Consumes messages from NATS WIP_INGEST stream.

Responsibilities:
- Subscribe to NATS JetStream for ingest messages
- Route messages to appropriate REST APIs via HTTP client
- Publish results to WIP_INGEST_RESULTS stream
- Handle retries and error logging

The producer's only feedback is the outcome it reads back by correlation_id,
so the worker holds to one rule: a message is acknowledged only once its
outcome is stored where its producer can read it. Every terminal path
publishes one; the only messages consumed without one are those that carry
no correlation_id to file an outcome under (they are counted).
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from pydantic import ValidationError

from wip_auth.providers.api_key import KeysUnverifiable

from .config import settings
from .http_client import IngestHTTPClient
from .models import (
    INGEST_API_KEY_HEADER,
    SUBJECT_TO_ACTION,
    IngestAction,
    IngestMessage,
    IngestResult,
    IngestResultStatus,
)
from .result_publisher import ResultPublisher

logger = logging.getLogger(__name__)

# Consecutive failed fetches (timeouts excluded) after which the consumer loop
# gives up and lets its supervisor re-subscribe.
MAX_CONSECUTIVE_FETCH_ERRORS = 30

# How long the server waits for an ack before redelivering a message. A
# fetched batch is processed one message at a time, so the messages still
# queued behind a slow one are kept alive (in_progress) every third of it.
ACK_WAIT_SECONDS = 60

# In-process attempts to store an outcome before the message is handed back.
PUBLISH_ATTEMPTS = 3
PUBLISH_RETRY_DELAYS = (0.5, 1.0)

# Outcomes of forwarded messages whose publish failed, by stream sequence: a
# redelivery publishes the stored outcome instead of writing the item again.
# Bounded and per process — after a restart the item is forwarded again.
UNPUBLISHED_MAX = 10_000


class EnvelopeRefused(Exception):
    """A message that cannot be forwarded as it is: redelivery cannot help."""

    def __init__(
        self, reason: str, correlation_id: str | None, action: IngestAction | None
    ):
        super().__init__(reason)
        self.reason = reason
        self.correlation_id = correlation_id
        self.action = action


def _decode(raw: bytes, action: IngestAction | None) -> Any:
    """The JSON value of a message body."""
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise EnvelopeRefused("the message body is not UTF-8 text", None, action) from None
    except json.JSONDecodeError as e:
        raise EnvelopeRefused(f"the message body is not JSON: {e}", None, action) from None


def _correlation_id(data: Any) -> str | None:
    """A body's correlation_id, when it has a usable one."""
    candidate = data.get("correlation_id") if isinstance(data, dict) else None
    return candidate if isinstance(candidate, str) and candidate else None


def _validated(data: Any, action: IngestAction) -> IngestMessage:
    """The envelope, validated against IngestMessage."""
    if not isinstance(data, dict):
        raise EnvelopeRefused(
            'the message body must be a JSON object {"correlation_id": ..., "payload": {...}}',
            None, action,
        )
    try:
        return IngestMessage.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'body'}: {err['msg']}"
            for err in e.errors()
        )
        raise EnvelopeRefused(
            f'invalid envelope (expected {{"correlation_id": ..., "payload": {{...}}}}): {problems}',
            _correlation_id(data), action,
        ) from None


def read_envelope(subject: str, raw: bytes) -> tuple[IngestAction, IngestMessage]:
    """The action and the validated {correlation_id, payload} envelope of a
    message, or EnvelopeRefused carrying whatever correlation_id it has."""
    action = SUBJECT_TO_ACTION.get(subject)
    data = _decode(raw, action)
    if action is None:
        raise EnvelopeRefused(
            f"unknown ingest subject {subject!r}: one of "
            f"{', '.join(sorted(SUBJECT_TO_ACTION))} is expected",
            _correlation_id(data), None,
        )
    return action, _validated(data, action)


class IngestWorker:
    """Processes ingest messages from NATS and forwards to REST APIs."""

    def __init__(
        self,
        nats_client: NATS | None,
        jetstream: JetStreamContext | None,
        http_client: IngestHTTPClient,
        result_publisher: ResultPublisher,
        *,
        identify_producer: Callable[[str], str | None],
    ):
        # Rebound to the live connection before every supervised run.
        self.nc = nats_client
        self.js = jetstream
        self.http_client = http_client
        self.result_publisher = result_publisher
        # Maps a producer's API key to its principal ("apikey:<name>"), which
        # owns the item's outcome; None for a key the platform does not know.
        # Raises KeysUnverifiable while the keys cannot be verified at all.
        self.identify_producer = identify_producer
        self._running = False
        self._messages_processed = 0
        self._messages_failed = 0
        self._messages_retried = 0
        self._messages_gave_up = 0
        self._messages_without_outcome = 0
        self._by_action: dict[str, dict[str, int]] = {}
        self._duration_ms_total = 0.0
        self._unpublished: OrderedDict[int, IngestResult] = OrderedDict()

    @property
    def messages_processed(self) -> int:
        """Messages finished: an outcome was published, or none could be."""
        return self._messages_processed

    @property
    def messages_failed(self) -> int:
        """Finished messages whose outcome is FAILED (or that have none)."""
        return self._messages_failed

    @property
    def messages_retried(self) -> int:
        """Redeliveries requested after a transient failure."""
        return self._messages_retried

    @property
    def messages_gave_up(self) -> int:
        """Messages whose forward still failed transiently on the last allowed delivery."""
        return self._messages_gave_up

    @property
    def messages_without_outcome(self) -> int:
        """Messages consumed with no outcome their producer can read: no
        correlation_id to file one under, or the results stream refused it
        through the last allowed delivery."""
        return self._messages_without_outcome

    @property
    def by_action(self) -> dict[str, dict[str, int]]:
        """Finished messages per action and outcome status."""
        return {action: dict(counts) for action, counts in self._by_action.items()}

    @property
    def avg_duration_ms(self) -> float:
        """Mean processing time of the messages whose outcome was published."""
        published = sum(sum(c.values()) for c in self._by_action.values())
        return self._duration_ms_total / published if published else 0.0

    @staticmethod
    def _delivery_count(msg: Any) -> int:
        try:
            return int(msg.metadata.num_delivered)
        except (AttributeError, TypeError, ValueError):
            return 1

    @staticmethod
    def _stream_sequence(msg: Any) -> int | None:
        try:
            seq = msg.metadata.sequence.stream
        except AttributeError:
            return None
        return seq if isinstance(seq, int) else None

    @staticmethod
    def _retry_delay(delivery: int) -> float:
        """Seconds to wait before the next delivery after delivery `delivery` failed."""
        schedule = settings.retry_backoff_seconds or [5.0]
        return float(schedule[min(delivery, len(schedule)) - 1])

    @property
    def is_running(self) -> bool:
        """Whether the worker is currently running."""
        return self._running

    async def _retry_later(self, msg: Any, delivery: int, why: str) -> None:
        delay = self._retry_delay(delivery)
        self._messages_retried += 1
        logger.warning(
            f"{why} on delivery {delivery}/{settings.retry_attempts} "
            f"— redelivering in {delay:.0f}s"
        )
        await msg.nak(delay=delay)

    async def _publish(self, result: IngestResult) -> bool:
        """Store an outcome, retrying briefly in process."""
        for attempt in range(PUBLISH_ATTEMPTS):
            if await self.result_publisher.publish(result):
                return True
            if attempt < len(PUBLISH_RETRY_DELAYS):
                await asyncio.sleep(PUBLISH_RETRY_DELAYS[attempt])
        return False

    def _count(self, result: IngestResult | None) -> None:
        """Count a finished message (None: it has no readable outcome)."""
        self._messages_processed += 1
        if result is None:
            self._messages_failed += 1
            self._messages_without_outcome += 1
            return
        if result.status == IngestResultStatus.FAILED:
            self._messages_failed += 1
        action = result.action.value if result.action else "unknown"
        counts = self._by_action.setdefault(action, {})
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
        self._duration_ms_total += result.duration_ms

    async def _settle(
        self, msg: Any, result: IngestResult, *, delivery: int, give_up: bool = False
    ) -> None:
        """Publish a message's final outcome, then ack it (term it when
        giving up). If the outcome cannot be stored the message is handed
        back with the outcome kept, so the redelivery publishes it without
        forwarding the item again."""
        seq = self._stream_sequence(msg)
        if await self._publish(result):
            if seq is not None:
                self._unpublished.pop(seq, None)
            self._count(result)
            if give_up:
                self._messages_gave_up += 1
                await msg.term()
            else:
                await msg.ack()
            logger.info(
                f"Settled {result.action.value if result.action else 'unknown'} "
                f"correlation_id={result.correlation_id} status={result.status.value} "
                f"duration={result.duration_ms:.1f}ms"
            )
            return

        if delivery >= settings.retry_attempts:
            if seq is not None:
                self._unpublished.pop(seq, None)
            self._count(None)
            logger.error(
                f"Outcome of correlation_id={result.correlation_id} could not be "
                f"stored on the last allowed delivery; its producer will not find it"
            )
            await msg.term()
            return

        if seq is not None:
            self._unpublished[seq] = result
            self._unpublished.move_to_end(seq)
            while len(self._unpublished) > UNPUBLISHED_MAX:
                self._unpublished.popitem(last=False)
        await self._retry_later(
            msg, delivery,
            f"Outcome of correlation_id={result.correlation_id} not stored",
        )

    async def _refuse(
        self,
        msg: Any,
        *,
        delivery: int,
        correlation_id: str | None,
        action: IngestAction | None,
        error: str,
        http_status: int | None,
        submitted_by: str | None,
        started: float,
    ) -> None:
        """Finish a message that is not forwarded, with a FAILED outcome when
        it has a correlation_id to file one under."""
        logger.warning(f"Refused ingest message on {msg.subject}: {error}")
        if correlation_id is None:
            self._count(None)
            await msg.ack()
            return
        await self._settle(msg, IngestResult(
            correlation_id=correlation_id,
            action=action,
            status=IngestResultStatus.FAILED,
            http_status_code=http_status,
            error=error,
            duration_ms=(time.perf_counter() - started) * 1000,
            submitted_by=submitted_by,
        ), delivery=delivery)

    def _identify(self, api_key: str) -> tuple[str | None, tuple[int, str] | None]:
        """The producer's principal, or the status and reason it is not identified."""
        try:
            submitted_by = self.identify_producer(api_key)
        except KeysUnverifiable as e:
            return None, (503, f"the message's API key could not be verified: {e}")
        if not submitted_by:
            return None, (
                401,
                "unauthenticated: the message's API key is not a key of this "
                "platform (or is disabled or expired)",
            )
        return submitted_by, None

    async def _finish_forward(self, msg: Any, result: IngestResult, *, delivery: int) -> None:
        """Settle a forwarded item, or hand it back on a transient failure.

        A transient failure (target unreachable, timeout, 5xx, 429) must not
        consume the message: it is redelivered with a growing delay, so a
        target that is starting or restarting gets the request once it is
        back. Only the final outcome is published: on the last delivery the
        message is given up on (term(), instead of the server dropping it
        silently at max_deliver). Anything else — success, per-item results
        in a 200, a 4xx redelivery cannot change — is definitive.
        """
        if not result.retryable:
            await self._settle(msg, result, delivery=delivery)
            return
        if delivery < settings.retry_attempts:
            await self._retry_later(
                msg, delivery,
                f"Transient failure for {result.action} "
                f"correlation_id={result.correlation_id}: {result.error}",
            )
            return
        result.error = f"{result.error} (gave up after {delivery} deliveries)"
        await self._settle(msg, result, delivery=delivery, give_up=True)

    async def _process_message(self, msg: Any) -> None:
        """Process a single ingest message."""
        started = time.perf_counter()
        delivery = self._delivery_count(msg)
        last_delivery = delivery >= settings.retry_attempts

        # The item was forwarded on an earlier delivery and only its outcome
        # failed to store: publish that, never write the item twice.
        seq = self._stream_sequence(msg)
        if seq is not None and seq in self._unpublished:
            await self._settle(msg, self._unpublished[seq], delivery=delivery)
            return

        envelope: IngestMessage | None = None
        action: IngestAction | None = None
        submitted_by: str | None = None
        try:
            try:
                action, envelope = read_envelope(msg.subject, msg.data)
            except EnvelopeRefused as refusal:
                await self._refuse(
                    msg, delivery=delivery, correlation_id=refusal.correlation_id,
                    action=refusal.action, error=refusal.reason, http_status=400,
                    submitted_by=None, started=started,
                )
                return
            correlation_id = envelope.correlation_id

            # Without a key the item is refused: forwarding it with a key of
            # the gateway's would turn anything that can publish to NATS into
            # an admin writer.
            api_key = (msg.headers or {}).get(INGEST_API_KEY_HEADER, "").strip()
            if not api_key:
                await self._refuse(
                    msg, delivery=delivery, correlation_id=correlation_id, action=action,
                    error=(
                        f"unauthenticated: an ingest message must carry the "
                        f"producer's WIP API key in its {INGEST_API_KEY_HEADER} header"
                    ),
                    http_status=401, submitted_by=None, started=started,
                )
                return

            # The write is made with the producer's own key, so the target
            # applies the producer's grants; the key's principal owns the
            # outcome, so only the producer reads it back. An item whose
            # producer is not identified is not forwarded: its outcome would
            # be filed where the producer cannot read it. A key that cannot be
            # verified now (the Registry has not confirmed the keys) or that is
            # not known yet (created a moment ago) is retried; refused only
            # once the deliveries run out.
            submitted_by, unidentified = self._identify(api_key)
            if unidentified is not None:
                status, error = unidentified
                if not last_delivery:
                    await self._retry_later(
                        msg, delivery, f"Producer of correlation_id={correlation_id} not identified ({error})"
                    )
                    return
                await self._refuse(
                    msg, delivery=delivery, correlation_id=correlation_id, action=action,
                    error=f"{error} (gave up after {delivery} deliveries)",
                    http_status=status, submitted_by=None, started=started,
                )
                return

            result = await self.http_client.forward_request(
                action, envelope.payload, correlation_id, api_key
            )
            result.submitted_by = submitted_by
            result.duration_ms = (time.perf_counter() - started) * 1000

            await self._finish_forward(msg, result, delivery=delivery)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            if not last_delivery:
                await self._retry_later(msg, delivery, "Unexpected error")
                return
            # The server would drop it silently at max_deliver: finish it.
            await self._refuse(
                msg, delivery=delivery,
                correlation_id=envelope.correlation_id if envelope else None, action=action,
                error=f"internal error (gave up after {delivery} deliveries): {e}",
                http_status=500, submitted_by=submitted_by, started=started,
            )

    async def _process_batch(self, messages: list[Any]) -> None:
        """Process a fetched batch in order, keeping the not-yet-finished
        messages alive so the server does not redeliver one still queued here."""
        pending = list(messages)

        async def keep_alive() -> None:
            while True:
                await asyncio.sleep(ACK_WAIT_SECONDS / 3)
                for queued in list(pending):
                    with suppress(Exception):
                        await queued.in_progress()

        heartbeat = asyncio.create_task(keep_alive())
        try:
            while pending:
                await self._process_message(pending[0])
                pending.pop(0)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def start(self) -> None:
        """Start the ingest worker."""
        logger.info("Starting ingest worker...")
        if self.js is None:
            raise RuntimeError("NATS not connected")
        self._running = True

        try:
            # Create durable consumer configuration
            consumer_config = ConsumerConfig(
                durable_name=settings.nats_ingest_durable_name,
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT,
                max_deliver=settings.retry_attempts,
                ack_wait=ACK_WAIT_SECONDS,
                # The ingest stream holds only the four ingest subject trees;
                # results live in a stream of their own.
                filter_subject="wip.ingest.>",
            )

            # pull_subscribe only applies `config` when it creates the
            # consumer; an existing durable keeps whatever it was created
            # with. add_consumer on the existing name updates it in place
            # (acks and position are kept), so a changed retry policy
            # reaches installs whose consumer already exists.
            try:
                await self.js.add_consumer(
                    settings.nats_ingest_stream_name, config=consumer_config
                )
            except Exception as e:
                logger.error(
                    f"Could not apply consumer config to "
                    f"{settings.nats_ingest_durable_name} "
                    f"(continuing with its existing config): {e}"
                )

            # Subscribe to all ingest subjects
            sub = await self.js.pull_subscribe(
                subject="wip.ingest.>",
                durable=settings.nats_ingest_durable_name,
                stream=settings.nats_ingest_stream_name,
                config=consumer_config,
            )

            logger.info(
                f"Subscribed to stream {settings.nats_ingest_stream_name} "
                f"with consumer {settings.nats_ingest_durable_name}"
            )

            # Main processing loop
            fetch_errors = 0
            while self._running:
                try:
                    # Fetch batch of messages with timeout
                    messages = await sub.fetch(
                        batch=settings.batch_size,
                        timeout=5
                    )
                except TimeoutError:
                    # No messages available: a healthy, empty poll
                    fetch_errors = 0
                    continue
                except asyncio.CancelledError:
                    logger.info("Worker cancelled, stopping...")
                    break
                except Exception as e:
                    # A subscription whose stream or consumer is gone fails
                    # every fetch. Give up after a run of failures so the
                    # supervisor re-subscribes, instead of spinning forever.
                    fetch_errors += 1
                    if fetch_errors >= MAX_CONSECUTIVE_FETCH_ERRORS:
                        raise RuntimeError(
                            f"{fetch_errors} consecutive fetch failures: {e}"
                        ) from e
                    logger.error(f"Error fetching messages: {e}")
                    await asyncio.sleep(1)  # Brief pause before retry
                    continue
                fetch_errors = 0
                try:
                    await self._process_batch(messages)
                except asyncio.CancelledError:
                    logger.info("Worker cancelled, stopping...")
                    break
                except Exception as e:
                    logger.error(f"Error processing messages: {e}")
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Worker task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in ingest worker: {e}", exc_info=True)
            self._running = False
            raise
        finally:
            self._running = False
            logger.info("Ingest worker stopped")

    async def stop(self) -> None:
        """Stop the ingest worker."""
        logger.info("Stopping ingest worker...")
        self._running = False
