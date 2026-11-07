"""Atomic ID counter backed by MongoDB.

Each document holds a sequence value for a specific counter_key. A
`prefixed` ID sequence is counted per prefix, install-wide ("@prefix:TERM-",
see services/id_generator.py); keys of the older per-namespace form
("wip:terminologies:TERM-") remain only as a floor for those counters.  The next value is obtained via
findOneAndUpdate with $inc, which is atomic even under concurrent access.
"""

from typing import ClassVar, cast

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ReturnDocument


class IdCounter(Document):
    """Persistent, atomic sequence counter."""

    counter_key: str = Field(
        ..., description="Unique key: '@prefix:{prefix}' (older: '{namespace}:{entity_type}:{prefix}')"
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

        A $max upsert: it never moves a counter back. Used to keep a prefix's
        counter above IDs that were written with their number already set.
        """
        await cls.get_motor_collection().update_one(
            {"counter_key": counter_key},
            {"$max": {"seq": value}},
            upsert=True,
        )
