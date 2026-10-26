"""Reporting rows of the removed `archived` document status become `deleted`.

Document archive was removed: an archived document was a soft-deleted one
under another name. document-store rewrites its own rows to `inactive`; here,
at startup, every physical document table's `archived` rows become `deleted`,
the status a soft delete writes. Views are derived and not touched, and a
failure never blocks startup.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import reporting_sync.main as rs


def _pool(conn: MagicMock) -> MagicMock:
    pool = MagicMock()

    @asynccontextmanager
    async def acquire():
        yield conn

    pool.acquire = acquire
    return pool


@pytest.mark.asyncio
async def test_every_document_table_is_rewritten():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"table_schema": "wip", "table_name": "doc_person__v1"},
        {"table_schema": "kb", "table_name": "doc_case__v2"},
    ])
    conn.execute = AsyncMock(side_effect=["UPDATE 2", "UPDATE 0"])

    await rs._retire_archived_status(_pool(conn))

    # Only physical tables are listed: views are derived from them.
    assert "BASE TABLE" in conn.fetch.call_args[0][0]
    calls = conn.execute.call_args_list
    assert [c[0][0] for c in calls] == [
        'UPDATE "wip"."doc_person__v1" SET status = $1 WHERE status = $2',
        'UPDATE "kb"."doc_case__v2" SET status = $1 WHERE status = $2',
    ]
    assert all(c[0][1:] == ("deleted", "archived") for c in calls)


@pytest.mark.asyncio
async def test_a_failure_never_blocks_startup():
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=RuntimeError("database gone"))

    await rs._retire_archived_status(_pool(conn))  # logs, does not raise
