"""Tests for the platform-maintained term closure.

The rebuild SQL runs against a real PostgreSQL (the shared test
container): transitivity, shortest-path depth under multiple parents,
reflexive rows, per-relation-type separation, parent_term_id links
counting as is_a, cycle termination, and soft-deleted relations falling
out. The debounce is tested with mocks.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from reporting_sync.schema_manager import SchemaManager
from reporting_sync.term_closure import TermClosureMaintainer

from .conftest import requires_postgres

NS = "closurens"


async def _seed(pool, sm: SchemaManager):
    """A: root; B, C: children of A; D: child of BOTH B and C (multi-parent);
    P: child of A via parent_term_id only; X<->Y: an is_a cycle;
    B part_of A (second relation type); one soft-deleted edge D->X."""
    terms_table = await sm.ensure_terms_table(NS)
    rel_table = await sm.ensure_term_relations_table(NS)
    async with pool.acquire() as conn:
        for term_id, parent in [
            ("A", None), ("B", None), ("C", None), ("D", None),
            ("P", "A"), ("X", None), ("Y", None),
        ]:
            await conn.execute(
                f"""INSERT INTO {terms_table}
                ("term_id", "namespace", "terminology_id", "value",
                 "parent_term_id", "status")
                VALUES ($1, $2, 'TRM-1', $1, $3, 'active')""",
                term_id, NS, parent,
            )
        # An inactive term never appears in the closure
        await conn.execute(
            f"""INSERT INTO {terms_table}
            ("term_id", "namespace", "terminology_id", "value", "status")
            VALUES ('GONE', $1, 'TRM-1', 'GONE', 'inactive')""",
            NS,
        )
        edges = [
            ("B", "A", "is_a", "active"),
            ("C", "A", "is_a", "active"),
            ("D", "B", "is_a", "active"),
            ("D", "C", "is_a", "active"),
            ("X", "Y", "is_a", "active"),
            ("Y", "X", "is_a", "active"),
            ("B", "A", "part_of", "active"),
            ("D", "X", "is_a", "inactive"),
        ]
        for src, tgt, rtype, status in edges:
            await conn.execute(
                f"""INSERT INTO {rel_table}
                ("namespace", "source_term_id", "target_term_id",
                 "relation_type", "status")
                VALUES ($1, $2, $3, $4, $5)""",
                NS, src, tgt, rtype, status,
            )


@requires_postgres
@pytest.mark.asyncio
async def test_rebuild_computes_the_closure(pg_pool):
    sm = SchemaManager(pg_pool)
    await _seed(pg_pool, sm)
    maintainer = TermClosureMaintainer(pg_pool, sm)
    rows = await maintainer.rebuild(NS)
    assert rows > 0

    closure = await sm.ensure_term_closure_table(NS)
    async with pg_pool.acquire() as conn:
        is_a = {
            (r["descendant_id"], r["ancestor_id"]): r["depth"]
            for r in await conn.fetch(
                f"SELECT * FROM {closure} WHERE \"relation_type\" = 'is_a'"
            )
        }
        part_of = {
            (r["descendant_id"], r["ancestor_id"]): r["depth"]
            for r in await conn.fetch(
                f"SELECT * FROM {closure} WHERE \"relation_type\" = 'part_of'"
            )
        }

    # Transitive: D reaches A through B or C, shortest path = 2
    assert is_a[("D", "A")] == 2
    assert is_a[("D", "B")] == 1 and is_a[("D", "C")] == 1
    # parent_term_id counts as is_a: P under A
    assert is_a[("P", "A")] == 1
    # Reflexive rows, depth 0
    assert is_a[("A", "A")] == 0 and is_a[("D", "D")] == 0
    # The cycle terminated and produced finite pairs
    assert is_a[("X", "Y")] == 1 and is_a[("Y", "X")] == 1
    # Soft-deleted edge fell out: D never reaches X
    assert ("D", "X") not in is_a
    # Per-type separation: part_of holds only its own subtree
    assert part_of[("B", "A")] == 1
    assert ("D", "A") not in part_of
    # Inactive terms get no reflexive row
    assert ("GONE", "GONE") not in is_a

    # "X or anything below it" — the case's target query shape
    async with pg_pool.acquire() as conn:
        subtree = {
            r["descendant_id"]
            for r in await conn.fetch(
                f"""SELECT "descendant_id" FROM {closure}
                WHERE "namespace" = $1 AND "ancestor_id" = 'A'
                  AND "relation_type" = 'is_a'""",
                NS,
            )
        }
    assert subtree == {"A", "B", "C", "D", "P"}


@requires_postgres
@pytest.mark.asyncio
async def test_rebuild_is_wholesale(pg_pool):
    """A removed edge disappears on the next rebuild — no stale pairs."""
    sm = SchemaManager(pg_pool)
    await _seed(pg_pool, sm)
    maintainer = TermClosureMaintainer(pg_pool, sm)
    await maintainer.rebuild(NS)

    rel_table = await sm.ensure_term_relations_table(NS)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            f"""UPDATE {rel_table} SET "status" = 'inactive'
            WHERE "source_term_id" = 'D' AND "target_term_id" = 'B'""",
        )
    await maintainer.rebuild(NS)

    closure = await sm.ensure_term_closure_table(NS)
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT "depth" FROM {closure}
            WHERE "descendant_id" = 'D' AND "ancestor_id" = 'A'
              AND "relation_type" = 'is_a'""",
        )
    # D still reaches A — through C only, still depth 2
    assert [r["depth"] for r in rows] == [2]


@pytest.mark.asyncio
async def test_mark_dirty_debounces_bursts(monkeypatch):
    """A burst of marks coalesces into one rebuild; a mark landing
    mid-rebuild triggers exactly one more."""
    import reporting_sync.term_closure as mod

    monkeypatch.setattr(mod, "DEBOUNCE_SECONDS", 0.01)
    maintainer = TermClosureMaintainer(None, None)
    maintainer.rebuild = AsyncMock(return_value=0)

    for _ in range(50):
        maintainer.mark_dirty("ns1")
    await asyncio.sleep(0.05)
    assert maintainer.rebuild.await_count == 1

    # Re-dirty during the rebuild itself
    async def slow_rebuild(ns):
        maintainer.mark_dirty("ns1")
        maintainer.rebuild = AsyncMock(return_value=0)  # swap for the rerun
        return 0

    maintainer.rebuild = AsyncMock(side_effect=slow_rebuild)
    maintainer.mark_dirty("ns1")
    await asyncio.sleep(0.08)
    # The swapped-in mock received the follow-up rebuild
    assert maintainer.rebuild.await_count == 1
