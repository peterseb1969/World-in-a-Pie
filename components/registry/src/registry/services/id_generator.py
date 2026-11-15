"""ID generation service using IdAlgorithmConfig.

A `prefixed` ID is its prefix followed by a sequence number, and every
entry_id is unique install-wide. A prefix names one sequence on the install
(services/id_prefixes.py), so the sequence is counted per prefix, not per
namespace or entity type: a prefix that moves to another namespace or entity
type continues its sequence instead of restarting at 1, and two namespaces
still sharing a prefix from before that rule draw from one counter.

The counter's only input would otherwise be its own increments, while IDs
also arrive already set: a restore or merge writes the IDs it backed up, and
a caller may supply one. So:

- an entry written with its own prefixed ID raises its prefix's counter to at
  least that number (`advance_for_ids`, called by register and reserve);
- an ID written straight to the database (a restore or merge writes the
  entries it carries itself) is sent to /entries/advance-counters, which
  raises the counters the same way;
- the first time a process mints from a prefix, the counter is raised above
  the highest ID that prefix already has (`_ensure_floor`), which also repairs
  a counter that fell behind before this rule.

Raising is a MongoDB `$max`, so it never moves a counter back. A number above
MAX_SEQUENCE never moves a counter: a counter is a 64-bit integer, and one
raised to the edge of that range could not mint again; no mint reaches such a
number, so it cannot collide either.
"""

import re
from typing import cast

from bson.decimal128 import Decimal128

from ..models.entry import RegistryEntry
from ..models.id_algorithm import GENERATING_ALGORITHMS, IdAlgorithmConfig, IdGenerator
from ..models.id_counter import IdCounter
from ..models.namespace import Namespace

# Counter keys of the install-wide prefix sequences. The "@" keeps them apart
# from the older "{namespace}:{entity_type}:{prefix}" keys, which namespace
# deletion clears by the "^{namespace}:" pattern.
PREFIX_COUNTER = "@prefix:"
_DIGITS = re.compile("[0-9]+")
# The highest number a counter is raised to (see the module docstring).
MAX_SEQUENCE = 2**62


def prefix_counter_key(prefix: str) -> str:
    return f"{PREFIX_COUNTER}{prefix}"


async def _highest_existing(prefix: str) -> int:
    """The highest sequence number already used with ``prefix``: by an entry
    whose ID is the prefix plus digits, or by an older per-namespace counter."""
    highest = 0
    rows = RegistryEntry.get_motor_collection().aggregate([
        {"$match": {"entry_id": {"$regex": f"^{re.escape(prefix)}[0-9]+$"}}},
        # By number, not by spelling: IDs minted under different pad widths
        # are ordered neither by length nor as strings.
        {"$project": {"n": {"$convert": {
            "input": {"$substrCP": ["$entry_id", len(prefix), {"$strLenCP": "$entry_id"}]},
            "to": "decimal", "onError": None,
        }}}},
        {"$match": {"n": {"$ne": None, "$lte": Decimal128(str(MAX_SEQUENCE))}}},
        {"$sort": {"n": -1}},
        {"$limit": 1},
    ])
    async for row in rows:
        highest = int(row["n"].to_decimal())
    legacy = IdCounter.get_motor_collection().find(
        {"counter_key": {"$regex": f"^[^@][^:]*:[^:]*:{re.escape(prefix)}$"}},
        {"seq": 1},
    )
    async for row in legacy:
        seq = int(row.get("seq") or 0)
        if seq <= MAX_SEQUENCE:
            highest = max(highest, seq)
    return highest


async def _sequence_prefixes() -> set[str]:
    """Every prefix with a sequence: one a namespace's id_config mints with,
    and one that has a counter. A prefix taken out of every id_config keeps
    its sequence (its IDs still number it, and it may be configured again),
    so an ID written with it must still raise its counter."""
    prefixes: set[str] = set()
    async for ns in Namespace.get_motor_collection().find({}, {"id_config": 1}):
        for cfg in (ns.get("id_config") or {}).values():
            if isinstance(cfg, dict) and cfg.get("algorithm") == "prefixed" and cfg.get("prefix"):
                prefixes.add(cfg["prefix"])
    async for row in IdCounter.get_motor_collection().find(
        {"counter_key": {"$regex": f"^{re.escape(PREFIX_COUNTER)}"}}, {"counter_key": 1},
    ):
        prefix = str(row.get("counter_key") or "")[len(PREFIX_COUNTER):]
        if prefix:
            prefixes.add(prefix)
    return prefixes


class IdGeneratorService:
    """Service for generating IDs based on namespace configuration."""

    # Prefixes whose counter this process has raised to the existing IDs.
    _floored: set[str] = set()  # noqa: RUF012 — process-wide by design

    @staticmethod
    def check_config(config: IdAlgorithmConfig, namespace: str, entity_type: str) -> None:
        """Raise ValueError when ``config`` cannot mint an ID: a validation-only
        algorithm (``pattern``, ``any``), which accepts IDs a caller supplies
        and generates none, or ``prefixed`` without a prefix. Every minting
        path calls this before it mints, so such a namespace's request gets a
        per-item error or a 422, never the generator's own ValueError as a
        500."""
        if config.algorithm not in GENERATING_ALGORITHMS:
            raise ValueError(
                f"id_config.{entity_type} of namespace '{namespace}' uses the "
                f"validation-only algorithm '{config.algorithm}', which generates no "
                "IDs; supply an explicit entry_id, or configure a generating "
                f"algorithm ({', '.join(sorted(GENERATING_ALGORITHMS))})"
            )
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
    async def advance_for_ids(cls, entry_ids: list[str]) -> dict[str, str | None]:
        """Raise each prefix's counter to the highest of ``entry_ids`` it
        numbers, so a later mint never hands out an ID written here. Returns
        the IDs that number a prefixed sequence: each with its prefix, or
        None when its number is above MAX_SEQUENCE.

        Every prefix with a sequence is considered, not only the writing
        namespace's: one any namespace configures, and one that has a counter
        (_sequence_prefixes). An ID numbers a prefix's sequence wherever it is
        stored. Raising a counter too far only skips numbers, up to
        MAX_SEQUENCE; a number above it is left out.
        """
        numbered: dict[str, str | None] = {}
        if not entry_ids:
            return numbered
        prefixes = await _sequence_prefixes()
        highest: dict[str, int] = {}
        for entry_id in entry_ids:
            for prefix in prefixes:
                rest = entry_id[len(prefix):]
                if entry_id.startswith(prefix) and _DIGITS.fullmatch(rest):
                    n = int(rest)
                    if n > MAX_SEQUENCE:
                        numbered.setdefault(entry_id, None)
                        continue
                    numbered[entry_id] = prefix
                    highest[prefix] = max(highest.get(prefix, 0), n)
        for prefix, n in highest.items():
            await IdCounter.set_if_higher(prefix_counter_key(prefix), n)
        return numbered
