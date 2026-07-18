"""Atomic ID counter backed by MongoDB.

Each document holds a sequence value for a specific counter_key
(e.g. "wip:terminologies:TERM-").  The next value is obtained via
findOneAndUpdate with $inc, which is atomic even under concurrent access.
"""

from typing import ClassVar, cast

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ReturnDocument


class IdCounter(Document):
    """Persistent, atomic sequence counter."""

    counter_key: str = Field(
        ..., description="Unique key, typically '{namespace}:{entity_type}:{prefix}'"
    )
    seq: int = Field(default=0, description="Current sequence value")

    class Settings:
        name = "id_counters"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("counter_key", 1)], unique=True, name="counter_key_unique_idx"),
        ]

    @classmethod
    async def next_val(cls, counter_key: str) -> int:
        """Atomically increment and return the next sequence value.

        Creates the counter document on first use (upsert).
        """
        result = await cls.get_motor_collection().find_one_and_update(
            {"counter_key": counter_key},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return cast(int, result["seq"])

    @classmethod
    async def set_if_higher(cls, counter_key: str, value: int) -> None:
        """Set counter to *value* only if it is currently lower (or missing).

        No current callers (CASE-568) — kept as the counter-restore
        primitive ($max upsert, never rolls back an already-incremented
        counter) for future migration/restore paths.
        """
        await cls.get_motor_collection().update_one(
            {"counter_key": counter_key},
            {"$max": {"seq": value}},
            upsert=True,
        )
