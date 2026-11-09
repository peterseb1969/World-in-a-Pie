"""A relation event without the term values keeps the stored ones.

A relation reactivated by def-store used to arrive without
source_term_value / target_term_value, and the upsert overwrote the stored
values with NULL. The upsert now keeps a stored value the event does not
carry (def-store also sends them on reactivation now).
"""

from unittest.mock import MagicMock

import pytest

from reporting_sync.models import SyncStatus
from reporting_sync.worker import SyncWorker

from .conftest import requires_postgres

NS = "probe1180"


def _rel(**extra):
    return {"event_type": "term_relation.created", "term_relation": {
        "namespace": NS, "source_term_id": "S", "target_term_id": "T",
        "relation_type": "is_a", **extra,
    }}


@requires_postgres
@pytest.mark.asyncio
async def test_event_without_values_keeps_the_stored_ones(pg_pool):
    w = SyncWorker(MagicMock(), MagicMock(), pg_pool,
                   SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True))
    w.closure.mark_dirty = MagicMock()
    await w._process_term_relation_event(_rel(source_term_value="alpha", target_term_value="beta"))

    await w._process_term_relation_event(_rel(status="active"))

    table = await w.schema_manager.ensure_term_relations_table(NS)
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(f'SELECT source_term_value, target_term_value FROM {table}')  # nosec B608
    assert (row["source_term_value"], row["target_term_value"]) == ("alpha", "beta")


@requires_postgres
@pytest.mark.asyncio
async def test_event_with_values_still_updates_them(pg_pool):
    w = SyncWorker(MagicMock(), MagicMock(), pg_pool,
                   SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True))
    w.closure.mark_dirty = MagicMock()
    await w._process_term_relation_event(_rel(source_term_value="alpha", target_term_value="beta"))

    await w._process_term_relation_event(_rel(source_term_value="ALPHA", target_term_value="BETA"))

    table = await w.schema_manager.ensure_term_relations_table(NS)
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(f'SELECT source_term_value, target_term_value FROM {table}')  # nosec B608
    assert (row["source_term_value"], row["target_term_value"]) == ("ALPHA", "BETA")
