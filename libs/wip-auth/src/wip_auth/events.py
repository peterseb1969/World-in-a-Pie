"""Self-healing NATS JetStream connection shared by every WIP service.

WIP starts its services in parallel, so NATS may come up after a service,
go away for minutes, or come back with a stream that a different service
created. Every service that publishes or consumes events therefore needs
the same four guarantees, and they live here, once:

* **The connection heals itself.** ``EventBus.start()`` connects in the
  background and never blocks the caller's startup. Reconnect is
  unlimited, and a client that closes anyway is rebuilt. There is no
  "connect once, else disabled for the life of the process" latch.
* **Streams are declared in one place.** Each ``StreamSpec`` is ensured
  idempotently after every (re)connect: created if missing, otherwise
  missing subjects are merged in and the declared limits applied. Subjects
  someone else added are never removed. ``WIP_EVENTS_STREAM`` is the one
  canonical definition of the platform event stream.
* **Publishing has a visible outcome.** ``publish()`` returns a
  ``PublishOutcome``. While NATS is unreachable, events wait in a bounded,
  in-memory FIFO retry queue and are drained in order once the connection
  is back. The queue is bounded by count and by age; overflow drops the
  oldest event, and every drop is counted by reason. The queue lives in
  process memory, so a restart loses what is still in it — batch sync is
  the repair for that and for anything counted as dropped. Each event
  carries a ``Nats-Msg-Id`` header that stays the same across retries, so
  JetStream discards the duplicate when a publish that timed out had in
  fact been stored.
* **Consumers are supervised.** ``supervise()`` runs a consumer loop and
  restarts it, with backoff, whenever it raises or returns, after waiting
  for a live connection.

``stats()`` exposes connection state and the counters for ``/health``.

**Credentials.** NATS requires each service to authenticate as its own
user, restricted to the subjects that service needs. The bus reads
``NATS_USER`` / ``NATS_PASSWORD`` (the deployer injects both) unless they
are passed explicitly, and hands them to ``nats.connect`` as arguments —
never inside the URL, which is logged. A user also gets its own reply
inbox prefix (``_INBOX_<user>``): JetStream acks and pulled messages
arrive on reply inboxes, and the server lets each user subscribe only to
its own, so one service cannot read another's replies.

nats-py is imported lazily, so services that never touch NATS do not need
it installed (install ``wip-auth[events]`` otherwise).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 60 * 60 * 24


@dataclass(frozen=True)
class StreamSpec:
    """The declared shape of one JetStream stream.

    ``max_msgs`` / ``max_bytes`` of -1 and ``max_age_seconds`` of 0 mean
    "unlimited", matching the JetStream defaults.
    """

    name: str
    subjects: tuple[str, ...]
    max_msgs: int = -1
    max_bytes: int = -1
    max_age_seconds: float = 0


WIP_EVENTS_STREAM = StreamSpec(
    name="WIP_EVENTS",
    # Explicit subjects rather than "wip.>": the ingest streams own
    # wip.ingest.*, and JetStream rejects overlapping subjects.
    subjects=(
        "wip.documents.>",
        "wip.files.>",
        "wip.templates.>",
        "wip.terminologies.>",
        "wip.terms.>",
        "wip.term_relations.>",
    ),
    max_msgs=1_000_000,
    max_bytes=1024 * 1024 * 1024,
    max_age_seconds=7 * _SECONDS_PER_DAY,
)
"""The platform event stream. Every publisher and consumer of WIP events
ensures exactly this definition."""


class ConnectionState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


class PublishOutcome(StrEnum):
    PUBLISHED = "published"
    """Stored by JetStream (acknowledged)."""
    QUEUED = "queued"
    """Not yet stored; waiting in the retry queue for the connection."""
    DISABLED = "disabled"
    """NATS is not configured for this service; nothing was attempted."""


class DropReason(StrEnum):
    OVERFLOW = "overflow"
    """The retry queue was full; the oldest queued event made room."""
    EXPIRED = "expired"
    """The event waited longer than the queue's age bound."""
    REJECTED = "rejected"
    """JetStream kept refusing the event while connected (e.g. no stream
    covers its subject); retrying it further would block the queue."""
    STOPPED = "stopped"
    """The service shut down with the event still queued."""


@dataclass
class _QueuedEvent:
    subject: str
    payload: bytes
    msg_id: str
    enqueued_at: float
    attempts: int = 0


@dataclass
class _ConsumerState:
    running: bool = False
    restarts: int = 0
    last_error: str | None = None


@dataclass
class _Counters:
    published: int = 0
    dropped: dict[str, int] = field(default_factory=lambda: {r.value: 0 for r in DropReason})


class EventBus:
    """One NATS connection per process, healed in the background.

    Lifecycle: construct, ``await start()`` (returns immediately), use
    ``publish()`` / ``supervise()`` / ``jetstream``, ``await stop()``.
    A falsy ``url`` builds a disabled bus: ``publish()`` returns
    ``DISABLED`` and ``state`` is ``not_configured``. ``user`` / ``password``
    default to ``NATS_USER`` / ``NATS_PASSWORD``; without a user the bus
    connects anonymously (a server without authorization, e.g. tests).
    """

    def __init__(
        self,
        url: str | None,
        *,
        name: str,
        user: str | None = None,
        password: str | None = None,
        streams: Sequence[StreamSpec] = (),
        queue_max_events: int = 10_000,
        queue_max_age_seconds: float = 3600,
        publish_timeout_seconds: float = 5.0,
        reconnect_wait_seconds: float = 2.0,
        connect_timeout_seconds: float = 2.0,
        max_rejections: int = 5,
    ) -> None:
        self._url = url or None
        self._name = name
        self._user = (user if user is not None else os.environ.get("NATS_USER")) or None
        self._password = (
            password if password is not None else os.environ.get("NATS_PASSWORD")
        ) or None
        self._streams = tuple(streams)
        self._queue_max_events = queue_max_events
        self._queue_max_age = queue_max_age_seconds
        self._publish_timeout = publish_timeout_seconds
        self._reconnect_wait = reconnect_wait_seconds
        self._connect_timeout = connect_timeout_seconds
        self._max_rejections = max_rejections

        self._state = ConnectionState.NOT_CONFIGURED if self._url is None else ConnectionState.STOPPED
        self._nc: Any = None
        self._js: Any = None
        self._connected = asyncio.Event()
        self._streams_ok = False
        self._stopping = False
        self._ever_connected = False
        self._last_error: str | None = None
        self._connect_errors = 0

        self._queue: deque[_QueuedEvent] = deque()
        self._drain_wake = asyncio.Event()
        self._counters = _Counters()
        self._consumers: dict[str, _ConsumerState] = {}

        self._tasks: list[asyncio.Task[Any]] = []
        # Set when the current client closes; replaced for every new client.
        # Supervised consumers watch it: a subscription does not survive its
        # client, so a consumer bound to a closed one is restarted.
        self._closed: asyncio.Event = asyncio.Event()
        self._ensure_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state is ConnectionState.CONNECTED and self._nc is not None and self._nc.is_connected

    @property
    def client(self) -> Any:
        """The live ``nats.NATS`` client, or None while not connected."""
        return self._nc if self.connected else None

    @property
    def jetstream(self) -> Any:
        """The live JetStream context, or None while not connected."""
        return self._js if self.connected else None

    def stats(self) -> dict[str, Any]:
        """Connection state and counters, cheap enough for every ``/health``."""
        return {
            "state": self._state.value,
            "streams_ok": self._streams_ok,
            "published": self._counters.published,
            "queued": len(self._queue),
            "dropped": sum(self._counters.dropped.values()),
            "dropped_by_reason": dict(self._counters.dropped),
            "last_error": self._last_error,
            "consumers": {
                name: {"running": c.running, "restarts": c.restarts, "last_error": c.last_error}
                for name, c in self._consumers.items()
            },
        }

    async def wait_connected(self, timeout: float | None = None) -> bool:
        """Wait until connected; False on timeout or when not configured."""
        if not self.configured:
            return False
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            return False
        return self.connected

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin connecting in the background. Never blocks on NATS."""
        if not self.configured:
            logger.info("%s: NATS URL not configured, event publishing disabled", self._name)
            return
        self._stopping = False
        self._state = ConnectionState.CONNECTING
        self._tasks.append(asyncio.create_task(self._supervise_connection(), name=f"{self._name}-nats"))
        self._tasks.append(asyncio.create_task(self._drain_loop(), name=f"{self._name}-nats-drain"))

    async def stop(self) -> None:
        """Stop consumers, the drainer and the connection.

        Events still queued are dropped and counted (``stopped``).
        """
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self._queue:
            self._drop(len(self._queue), DropReason.STOPPED)
            self._queue.clear()
        nc, self._nc, self._js = self._nc, None, None
        self._connected.clear()
        if nc is not None and not nc.is_closed:
            with contextlib.suppress(Exception):
                await nc.close()
        if self.configured:
            self._state = ConnectionState.STOPPED

    # ------------------------------------------------------------------
    # Connection supervision
    # ------------------------------------------------------------------

    async def _supervise_connection(self) -> None:
        import nats

        url = self._url
        if url is None:  # start() only supervises a configured bus
            return
        while not self._stopping:
            self._state = ConnectionState.RECONNECTING if self._ever_connected else ConnectionState.CONNECTING
            self._closed = asyncio.Event()
            try:
                # max_reconnect_attempts=-1: nats-py then retries the initial
                # connect and every later reconnect forever. Its default (60
                # attempts x 2 s) closes the client for good after ~2 minutes.
                nc = await nats.connect(
                    url,
                    name=self._name,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=self._reconnect_wait,
                    connect_timeout=self._connect_timeout,
                    error_cb=self._on_error,
                    disconnected_cb=self._on_disconnected,
                    reconnected_cb=self._on_reconnected,
                    closed_cb=self._on_closed,
                    **self._credential_options(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # e.g. a malformed URL; keep trying
                self._note_error(f"connect failed: {e}")
                await asyncio.sleep(self._reconnect_wait)
                continue

            self._nc, self._js = nc, nc.jetstream()
            self._connect_errors = 0
            logger.info(
                "%s: connected to NATS at %s as %s",
                self._name, self._url, self._user or "anonymous",
            )
            await self._on_connected()
            await self._closed.wait()
            if not self._stopping:
                # nats-py only closes on its own after giving up, or on a
                # fatal protocol error. Either way: rebuild the client.
                logger.warning("%s: NATS client closed unexpectedly; rebuilding", self._name)
                self._connected.clear()
                await asyncio.sleep(self._reconnect_wait)

    def _credential_options(self) -> dict[str, Any]:
        """``nats.connect`` keyword arguments for this bus's NATS user."""
        if self._user is None:
            return {}
        return {
            "user": self._user,
            "password": self._password,
            "inbox_prefix": f"_INBOX_{self._user}",
        }

    async def _on_connected(self) -> None:
        self._ever_connected = True
        self._last_error = None
        # Streams first, so nothing that waits for "connected" (a publish, a
        # supervised consumer) can race a stream that does not exist yet.
        await self.ensure_streams()
        self._state = ConnectionState.CONNECTED
        self._connected.set()
        self._drain_wake.set()

    async def ensure_streams(self) -> bool:
        """Create or converge every declared stream. Safe to call any time."""
        js = self._js
        if js is None:
            return False
        async with self._ensure_lock:
            ok = True
            for spec in self._streams:
                try:
                    await ensure_stream(js, spec)
                except Exception as e:
                    ok = False
                    self._note_error(f"ensure stream {spec.name} failed: {e}")
            self._streams_ok = ok
            return ok

    async def _on_error(self, e: Exception) -> None:
        self._note_error(str(e) or type(e).__name__)

    async def _on_disconnected(self) -> None:
        if not self._stopping:
            self._state = ConnectionState.RECONNECTING
            self._streams_ok = False  # re-verified on reconnect
            self._connected.clear()
            logger.warning("%s: NATS connection lost; reconnecting", self._name)

    async def _on_reconnected(self) -> None:
        logger.info("%s: NATS reconnected", self._name)
        self._connect_errors = 0
        # Re-ensure streams and resume draining off the callback path.
        self._spawn(self._on_connected())

    async def _on_closed(self) -> None:
        self._connected.clear()
        self._closed.set()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        task.add_done_callback(self._forget_task)

    def _forget_task(self, task: asyncio.Task[Any]) -> None:
        if task in self._tasks:
            self._tasks.remove(task)

    def _note_error(self, message: str) -> None:
        self._last_error = message
        self._connect_errors += 1
        # A down NATS produces one error per reconnect attempt; log the first
        # and then every 30th so the log stays readable.
        if self._connect_errors == 1 or self._connect_errors % 30 == 0:
            logger.warning("%s: NATS error (#%d): %s", self._name, self._connect_errors, message)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, subject: str, payload: bytes) -> PublishOutcome:
        """Publish to JetStream, or queue the event for retry.

        Events are kept in order: while anything is queued, new events join
        the back of the queue instead of overtaking it.
        """
        if not self.configured:
            return PublishOutcome.DISABLED
        event = _QueuedEvent(subject, payload, uuid.uuid4().hex, time.monotonic())
        if not self._queue and self.connected:
            try:
                await self._js.publish(
                    subject, payload, timeout=self._publish_timeout,
                    headers={"Nats-Msg-Id": event.msg_id},
                )
                self._counters.published += 1
                return PublishOutcome.PUBLISHED
            except Exception as e:
                event.attempts = 1
                self._last_error = f"publish {subject} failed: {e}"
                logger.warning("%s: publish to %s failed, queued for retry: %s", self._name, subject, e)
        self._enqueue(event)
        return PublishOutcome.QUEUED

    def _enqueue(self, event: _QueuedEvent) -> None:
        self._expire_queue()
        while len(self._queue) >= self._queue_max_events:
            self._queue.popleft()
            self._drop(1, DropReason.OVERFLOW)
        self._queue.append(event)
        self._drain_wake.set()

    def _expire_queue(self) -> None:
        cutoff = time.monotonic() - self._queue_max_age
        while self._queue and self._queue[0].enqueued_at < cutoff:
            self._queue.popleft()
            self._drop(1, DropReason.EXPIRED)

    def _drop(self, n: int, reason: DropReason) -> None:
        self._counters.dropped[reason.value] += n
        logger.error("%s: dropped %d event(s) (%s); batch sync repairs reporting", self._name, n, reason.value)

    async def _drain_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._drain_wake.wait(), timeout=5)
            self._drain_wake.clear()
            self._expire_queue()
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let the drainer die: a dead drainer strands the queue.
                logger.exception("%s: retry-queue drain failed", self._name)

    async def _drain_once(self) -> None:
        backoff = 0.5
        while self._queue and self.connected:
            event = self._queue[0]
            try:
                await self._js.publish(
                    event.subject, event.payload, timeout=self._publish_timeout,
                    headers={"Nats-Msg-Id": event.msg_id},
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                event.attempts += 1
                self._last_error = f"publish {event.subject} failed: {e}"
                if not self.connected:
                    return  # wait for the reconnect callback to wake us
                if event.attempts >= self._max_rejections:
                    self._queue.popleft()
                    self._drop(1, DropReason.REJECTED)
                    continue
                # Connected but refused: maybe the stream is being repaired.
                await self.ensure_streams()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
                continue
            self._queue.popleft()
            self._counters.published += 1
            backoff = 0.5

    # ------------------------------------------------------------------
    # Consumer supervision
    # ------------------------------------------------------------------

    def supervise(
        self,
        name: str,
        run: Callable[[Any], Awaitable[None]],
        *,
        max_backoff_seconds: float = 30.0,
    ) -> asyncio.Task[None]:
        """Run ``run(jetstream)`` for as long as the bus lives.

        Waits for a live connection before each run, and restarts the loop
        (exponential backoff, capped) whenever it raises or returns. The
        task is cancelled by ``stop()``.
        """
        consumer = self._consumers.setdefault(name, _ConsumerState())

        async def _loop() -> None:
            backoff = 1.0
            while not self._stopping:
                await self._connected.wait()
                js = self._js
                if js is None:
                    await asyncio.sleep(self._reconnect_wait)
                    continue
                if consumer.restarts:
                    # A consumer usually dies because its stream went away.
                    await self.ensure_streams()
                started = time.monotonic()
                consumer.running = True
                client_closed = self._closed
                run_task = asyncio.ensure_future(run(js))
                closed_task = asyncio.ensure_future(client_closed.wait())
                try:
                    await asyncio.wait({run_task, closed_task}, return_when=asyncio.FIRST_COMPLETED)
                    if not run_task.done():
                        # The client this run was bound to is gone.
                        run_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await run_task
                        consumer.last_error = "client closed"
                    else:
                        run_task.result()
                        consumer.last_error = "returned"
                except asyncio.CancelledError:
                    run_task.cancel()
                    # Let the consumer unwind before the connection closes.
                    await asyncio.wait({run_task}, timeout=2)
                    consumer.running = False
                    raise
                except Exception as e:
                    consumer.last_error = f"{type(e).__name__}: {e}"
                    logger.error("%s: consumer %s failed: %s", self._name, name, e)
                finally:
                    closed_task.cancel()
                consumer.running = False
                if self._stopping:
                    return
                consumer.restarts += 1
                if time.monotonic() - started > 60:
                    backoff = 1.0  # it ran for a while; this is a new failure
                logger.warning("%s: restarting consumer %s in %.0fs", self._name, name, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff_seconds)

        task = asyncio.create_task(_loop(), name=f"{self._name}-consumer-{name}")
        self._tasks.append(task)
        return task


async def ensure_stream(js: Any, spec: StreamSpec) -> None:
    """Create ``spec`` if missing, otherwise converge it in place.

    Convergence adds missing subjects (never removes any) and applies the
    declared limits, so whichever service starts first, every service ends
    up with the same stream.
    """
    from nats.js.api import RetentionPolicy, StreamConfig
    from nats.js.errors import NotFoundError

    try:
        info = await js.stream_info(spec.name)
    except NotFoundError:
        await js.add_stream(StreamConfig(
            name=spec.name,
            subjects=list(spec.subjects),
            retention=RetentionPolicy.LIMITS,
            max_msgs=spec.max_msgs,
            max_bytes=spec.max_bytes,
            max_age=spec.max_age_seconds,
        ))
        logger.info("Created stream %s", spec.name)
        return

    config = info.config
    current = list(config.subjects or [])
    missing = [s for s in spec.subjects if s not in current]
    limits_differ = (
        config.max_msgs != spec.max_msgs
        or config.max_bytes != spec.max_bytes
        or float(config.max_age or 0) != float(spec.max_age_seconds)
    )
    if not missing and not limits_differ:
        return
    config.subjects = current + missing
    config.max_msgs = spec.max_msgs
    config.max_bytes = spec.max_bytes
    config.max_age = spec.max_age_seconds
    await js.update_stream(config)
    logger.info(
        "Converged stream %s (added subjects: %s, limits updated: %s)",
        spec.name, missing or "none", limits_differ,
    )
