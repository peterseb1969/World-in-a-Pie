"""
Shared fixtures for reporting-sync tests.

Provides a real asyncpg pool when POSTGRES_TEST_URI is set,
enabling integration tests against a live PostgreSQL instance.
"""

import os

import asyncpg
import pytest
import pytest_asyncio

# Connection URI for integration tests (set in CI or locally)
POSTGRES_TEST_URI = os.environ.get(
    "POSTGRES_TEST_URI",
    "postgresql://test:test@localhost:5433/wip_test",
)

# Skip integration tests if PostgreSQL is not reachable
_pg_available = None


def _check_pg_available():
    """Check PostgreSQL availability (cached)."""
    global _pg_available
    if _pg_available is not None:
        return _pg_available
    import asyncio

    async def _try_connect():
        try:
            conn = await asyncpg.connect(POSTGRES_TEST_URI, timeout=3)
            await conn.close()
            return True
        except (OSError, asyncpg.PostgresError, TimeoutError):
            return False

    _pg_available = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _try_connect()
    )
    return _pg_available


requires_postgres = pytest.mark.skipif(
    not _check_pg_available() if os.environ.get("POSTGRES_TEST_URI") else True,
    reason="PostgreSQL not available (set POSTGRES_TEST_URI to enable)",
)


@pytest_asyncio.fixture
async def pg_pool():
    """Create a real asyncpg pool for integration tests.

    Drops all user tables before each test for isolation.
    """
    pool = await asyncpg.create_pool(POSTGRES_TEST_URI, min_size=1, max_size=5)

    # Clean all tables before each test
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        for row in tables:
            await conn.execute(f'DROP TABLE IF EXISTS "{row["table_name"]}" CASCADE')

    yield pool

    await pool.close()
