"""ID generation service using IdAlgorithmConfig.

A `prefixed` ID is its prefix followed by a sequence number, and every
entry_id is unique install-wide. A prefix names one sequence on the install
(services/id_prefixes.py), so the sequence is counted per prefix, not per
namespace or entity type: a prefix that moves to another namespace or entity
type continues its sequence instead of restarting at 1, and two namespaces
still sharing a prefix from before that rule draw from one counter.

The counter's only input would otherwise be its own increments, while IDs
also arrive already set: a restore re-registers the IDs it backed up, and a
caller may supply one. So:

- an entry written with its own prefixed ID raises its prefix's counter to at
  least that number (`advance_for_ids`, called by register and reserve);
- the first time a process mints from a prefix, the counter is raised above
  the highest ID that prefix already has (`_ensure_floor`), which also repairs
  a counter that fell behind before this rule.

Raising is a MongoDB `$max`, so it never moves a counter back.
"""

import re
from typing import cast

from ..models.entry import RegistryEntry
from ..models.id_algorithm import IdAlgorithmConfig, IdGenerator
from ..models.id_counter import IdCounter
from ..models.namespace import Namespace

# Counter keys of the install-wide prefix sequences. The "@" keeps them apart
# from the older "{namespace}:{entity_type}:{prefix}" keys, which namespace
# deletion clears by the "^{namespace}:" pattern.
PREFIX_COUNTER = "@prefix:"
_DIGITS = re.compile("[0-9]+")


def prefix_counter_key(prefix: str) -> str:
    return f"{PREFIX_COUNTER}{prefix}"


async def _highest_existing(prefix: str) -> int:
    """The highest sequence number already used with ``prefix``: by an entry
    whose ID is the prefix plus digits, or by an older per-namespace counter."""
    highest = 0
    rows = RegistryEntry.get_motor_collection().aggregate([
        {"$match": {"entry_id": {"$regex": f"^{re.escape(prefix)}[0-9]+$"}}},
        # Longest first, then highest: an ID past its pad width still counts.
        {"$project": {"entry_id": 1, "n": {"$strLenCP": "$entry_id"}}},
        {"$sort": {"n": -1, "entry_id": -1}},
        {"$limit": 1},
    ])
    async for row in rows:
        highest = int(row["entry_id"][len(prefix):])
    legacy = IdCounter.get_motor_collection().find(
        {"counter_key": {"$regex": f"^[^@][^:]*:[^:]*:{re.escape(prefix)}$"}},
        {"seq": 1},
    )
    async for row in legacy:
        highest = max(highest, int(row.get("seq") or 0))
    return highest


class IdGeneratorService:
    """Service for generating IDs based on namespace configuration."""

    # Prefixes whose counter this process has raised to the existing IDs.
    _floored: set[str] = set()  # noqa: RUF012 — process-wide by design

    @staticmethod
    def check_config(config: IdAlgorithmConfig, namespace: str, entity_type: str) -> None:
        """Raise ValueError when ``config`` cannot mint an ID."""
        if config.algorithm == "prefixed" and not config.prefix:
            # Stored before writes refused it: bare numbers would be minted
            # by every such sequence alike.
            raise ValueError(
                f"id_config.{entity_type} of namespace '{namespace}' is 'prefixed' "
                "without a prefix; set a prefix before minting IDs"
            )

    @classmethod
    async def generate_many(cls, configs: list[IdAlgorithmConfig]) -> list[str]:
        """One new ID per config, in order. A prefix's IDs are taken with one
        counter round trip however many there are; other algorithms mint
        locally. Every config must pass ``check_config``."""
        ids: list[str | None] = [None] * len(configs)
        by_prefix: dict[str, list[int]] = {}
        for i, config in enumerate(configs):
            if config.algorithm == "prefixed":
                by_prefix.setdefault(config.prefix or "", []).append(i)
            else:
                ids[i] = IdGenerator.generate(config)
        for prefix, positions in by_prefix.items():
            await cls._ensure_floor(prefix)
            last = await IdCounter.reserve(prefix_counter_key(prefix), len(positions))
            for seq, i in enumerate(positions, start=last - len(positions) + 1):
                ids[i] = IdGenerator.generate_prefixed(prefix, seq, configs[i].pad)
        return cast(list[str], ids)

    @classmethod
    async def generate_from_config(
        cls, config: IdAlgorithmConfig, namespace: str, entity_type: str
    ) -> str:
        """Generate one ID from a specific config."""
        cls.check_config(config, namespace, entity_type)
        return (await cls.generate_many([config]))[0]

    @classmethod
    async def _ensure_floor(cls, prefix: str) -> None:
        if prefix in cls._floored:
            return
        highest = await _highest_existing(prefix)
        if highest:
            await IdCounter.set_if_higher(prefix_counter_key(prefix), highest)
        cls._floored.add(prefix)

    @classmethod
    async def advance_for_ids(cls, entry_ids: list[str]) -> None:
        """Raise each prefix's counter to the highest of ``entry_ids`` it
        numbers, so a later mint never hands out an ID written here.

        Every prefix any namespace configures is considered, not only the
        writing namespace's: an ID numbers a prefix's sequence wherever it is
        stored. Raising a counter too far only skips numbers.
        """
        if not entry_ids:
            return
        prefixes: set[str] = set()
        async for ns in Namespace.get_motor_collection().find({}, {"id_config": 1}):
            for cfg in (ns.get("id_config") or {}).values():
                if isinstance(cfg, dict) and cfg.get("algorithm") == "prefixed" and cfg.get("prefix"):
                    prefixes.add(cfg["prefix"])
        highest: dict[str, int] = {}
        for entry_id in entry_ids:
            for prefix in prefixes:
                rest = entry_id[len(prefix):]
                if entry_id.startswith(prefix) and _DIGITS.fullmatch(rest):
                    highest[prefix] = max(highest.get(prefix, 0), int(rest))
        for prefix, n in highest.items():
            await IdCounter.set_if_higher(prefix_counter_key(prefix), n)
