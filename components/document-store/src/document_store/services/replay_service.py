"""Event replay service — replays stored documents as NATS events."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ReplayService:
    """Manages replay sessions that publish stored documents as NATS events."""

    def __init__(self):
        self._sessions: dict[str, Any] = {}  # session_id -> ReplaySession
        self._tasks: dict[str, asyncio.Task] = {}
        self._pause_flags: dict[str, asyncio.Event] = {}  # cleared = paused

    async def start_replay(
        self,
        filter_config: dict,
        throttle_ms: int = 10,
        batch_size: int = 100,
    ) -> dict:
        """Start a new replay session."""
        from ..models.replay import ReplaySession, ReplayFilter, ReplayStatus
        from .nats_client import _jetstream, _nats_enabled

        if not _nats_enabled or not _jetstream:
            raise RuntimeError("NATS not connected — cannot start replay")

        session_id = str(uuid.uuid4())[:8]
        replay_filter = ReplayFilter(**filter_config) if isinstance(filter_config, dict) else filter_config

        stream_name = f"WIP_REPLAY_{session_id.upper()}"
        subject_prefix = f"wip.replay.{session_id}"

        # Count documents matching filter
        from ..models.document import Document
        query = {"status": replay_filter.status, "namespace": replay_filter.namespace}
        if replay_filter.template_id:
            query["template_id"] = replay_filter.template_id
        if replay_filter.template_value:
            query["template_value"] = replay_filter.template_value

        # Only count latest versions
        query["is_latest"] = True
        total_count = await Document.find(query).count()

        if total_count == 0:
            raise ValueError("No documents match the replay filter")

        # Create NATS stream for replay
        from nats.js.api import StreamConfig, RetentionPolicy, StorageType
        await _jetstream.add_stream(
            StreamConfig(
                name=stream_name,
                subjects=[f"{subject_prefix}.>"],
                retention=RetentionPolicy.WORK_QUEUE,
                storage=StorageType.MEMORY,
                max_msgs=total_count + 10,
            )
        )

        session = ReplaySession(
            session_id=session_id,
            filter=replay_filter,
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            total_count=total_count,
            throttle_ms=throttle_ms,
            batch_size=batch_size,
        )

        self._sessions[session_id] = session

        # Create pause flag (set = running, cleared = paused)
        pause_event = asyncio.Event()
        pause_event.set()  # Start running
        self._pause_flags[session_id] = pause_event

        # Launch async task
        task = asyncio.create_task(self._publish_replay(session_id))
        self._tasks[session_id] = task

        return session.model_dump(mode="json")

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get replay session state."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        return session.model_dump(mode="json")

    def list_sessions(self) -> list[dict]:
        """List all replay sessions."""
        return [s.model_dump(mode="json") for s in self._sessions.values()]

    async def pause(self, session_id: str) -> bool:
        """Pause a running replay."""
        from ..models.replay import ReplayStatus
        session = self._sessions.get(session_id)
        if not session or session.status != ReplayStatus.RUNNING:
            return False

        self._pause_flags[session_id].clear()  # Clear = paused
        session.status = ReplayStatus.PAUSED
        return True

    async def resume(self, session_id: str) -> bool:
        """Resume a paused replay."""
        from ..models.replay import ReplayStatus
        session = self._sessions.get(session_id)
        if not session or session.status != ReplayStatus.PAUSED:
            return False

        session.status = ReplayStatus.RUNNING
        self._pause_flags[session_id].set()  # Set = running
        return True

    async def cancel(self, session_id: str) -> bool:
        """Cancel a replay session and clean up NATS stream."""
        from ..models.replay import ReplayStatus
        session = self._sessions.get(session_id)
        if not session:
            return False

        if session.status in (ReplayStatus.RUNNING, ReplayStatus.PAUSED):
            # Unblock if paused
            if session_id in self._pause_flags:
                self._pause_flags[session_id].set()

            # Cancel the task
            task = self._tasks.get(session_id)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        session.status = ReplayStatus.CANCELLED

        # Delete NATS stream
        await self._cleanup_stream(session.stream_name)

        return True

    async def _cleanup_stream(self, stream_name: str):
        """Delete a replay NATS stream."""
        from .nats_client import _jetstream
        if _jetstream:
            try:
                await _jetstream.delete_stream(stream_name)
                logger.info(f"Deleted replay stream {stream_name}")
            except Exception as e:
                logger.warning(f"Failed to delete stream {stream_name}: {e}")

    async def _publish_replay(self, session_id: str):
        """Background task that publishes replay events."""
        from ..models.replay import ReplayStatus
        from ..models.document import Document
        from .nats_client import _jetstream

        session = self._sessions[session_id]
        session.status = ReplayStatus.RUNNING
        session.started_at = datetime.now(timezone.utc)

        try:
            query = {
                "status": session.filter.status,
                "namespace": session.filter.namespace,
                "is_latest": True,
            }
            if session.filter.template_id:
                query["template_id"] = session.filter.template_id
            if session.filter.template_value:
                query["template_value"] = session.filter.template_value

            page = 1
            sequence = 0
            batch_size = session.batch_size

            while True:
                # Check pause flag
                await self._pause_flags[session_id].wait()

                # Fetch a batch
                skip = (page - 1) * batch_size
                docs = await Document.find(query).skip(skip).limit(batch_size).to_list()

                if not docs:
                    break

                for doc in docs:
                    # Check pause flag before each event
                    await self._pause_flags[session_id].wait()

                    sequence += 1
                    doc_dict = doc.dict(by_alias=True)
                    # Convert ObjectId and datetime to strings
                    doc_dict.pop("_id", None)
                    doc_dict.pop("id", None)

                    # Serialize dates
                    for key, val in doc_dict.items():
                        if isinstance(val, datetime):
                            doc_dict[key] = val.isoformat()

                    template_value = doc_dict.get("template_value", "unknown")
                    event = {
                        "event_type": "document.created",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "changed_by": "replay",
                        "document": doc_dict,
                        "metadata": {
                            "replay": True,
                            "replay_session_id": session_id,
                            "sequence": sequence,
                            "total": session.total_count,
                        },
                    }

                    subject = f"{session.subject_prefix}.documents.{template_value}"
                    payload = json.dumps(event, default=str).encode()
                    await _jetstream.publish(subject, payload)

                    session.published = sequence

                    # Throttle
                    if session.throttle_ms > 0:
                        await asyncio.sleep(session.throttle_ms / 1000.0)

                page += 1

            # Publish completion event
            complete_event = {
                "event_type": "replay.complete",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "total_published": session.published,
            }
            await _jetstream.publish(
                f"{session.subject_prefix}.complete",
                json.dumps(complete_event).encode(),
            )

            session.status = ReplayStatus.COMPLETED
            session.completed_at = datetime.now(timezone.utc)
            logger.info(f"Replay {session_id} completed: {session.published} events published")

        except asyncio.CancelledError:
            logger.info(f"Replay {session_id} cancelled at {session.published}/{session.total_count}")
            raise
        except Exception as e:
            session.status = ReplayStatus.FAILED
            session.error = str(e)
            logger.error(f"Replay {session_id} failed: {e}")


# Global instance
_replay_service: Optional[ReplayService] = None


def get_replay_service() -> ReplayService:
    """Get or create the global replay service."""
    global _replay_service
    if _replay_service is None:
        _replay_service = ReplayService()
    return _replay_service
