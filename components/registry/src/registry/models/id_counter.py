"""Atomic ID counter backed by MongoDB.

Each document holds a sequence value for a specific counter_key
(e.g. "wip-terminologies:TERM-").  The next value is obtained via
findOneAndUpdate with $inc, which is atomic even under concurrent access.
"""

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ReturnDocument


class IdCounter(Document):
    """Persistent, atomic sequence counter."""

    counter_key: str = Field(
        ..., description="Unique key, typically '{pool_id}:{prefix}'"
    )
    seq: int = Field(default=0, description="Current sequence value")

    class Settings:
        name = "id_counters"
        indexes = [
            IndexModel([("counter_key", 1)], unique=True, name="counter_key_unique_idx"),
        ]

    @classmethod
    async def next_val(cls, counter_key: str) -> int:
        """Atomically increment and return the next sequence value.

        Creates the counter document on first use (upsert).
        """
        result = await cls.get_pymongo_collection().find_one_and_update(
            {"counter_key": counter_key},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return result["seq"]

    @classmethod
    async def set_if_higher(cls, counter_key: str, value: int) -> None:
        """Set counter to *value* only if it is currently lower (or missing).

        Used during startup migration so we never roll back an
        already-incremented counter.
        """
        await cls.get_pymongo_collection().update_one(
            {"counter_key": counter_key},
            {"$max": {"seq": value}},
            upsert=True,
        )
