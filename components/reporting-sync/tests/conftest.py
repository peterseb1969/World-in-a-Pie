"""
Shared fixtures for reporting-sync tests.

Provides real asyncpg pool and NATS connections when test infrastructure
is available, enabling integration and E2E tests.
"""

import asyncio
import contextlib
import os

import asyncpg
import nats
import pytest
import pytest_asyncio

# Connection URIs for integration tests (set in CI or locally)
POSTGRES_TEST_URI = os.environ.get(
    "POSTGRES_TEST_URI",
    "postgresql://test:test@localhost:5433/wip_test",
)
NATS_TEST_URL = os.environ.get(
    "NATS_TEST_URL",
    "nats://localhost:4223",
)

# ============================================================================
# Availability checks (cached, run once at import)
# ============================================================================

_pg_available = None
_nats_available = None


def _check_pg_available():
    """Check PostgreSQL availability (cached)."""
    global _pg_available
    if _pg_available is not None:
        return _pg_available

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


def _check_nats_available():
    """Check NATS availability (cached)."""
    global _nats_available
    if _nats_available is not None:
        return _nats_available

    async def _try_connect():
        try:
            nc = await nats.connect(NATS_TEST_URL, connect_timeout=3)
            await nc.close()
            return True
        except Exception:
            return False

    _nats_available = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _try_connect()
    )
    return _nats_available


# ============================================================================
# Skip markers
# ============================================================================

requires_postgres = pytest.mark.skipif(
    not _check_pg_available() if os.environ.get("POSTGRES_TEST_URI") else True,
    reason="PostgreSQL not available (set POSTGRES_TEST_URI to enable)",
)

requires_nats = pytest.mark.skipif(
    not _check_nats_available() if os.environ.get("NATS_TEST_URL") else True,
    reason="NATS not available (set NATS_TEST_URL to enable)",
)

requires_e2e = pytest.mark.skipif(
    not (
        (_check_pg_available() if os.environ.get("POSTGRES_TEST_URI") else False)
        and (_check_nats_available() if os.environ.get("NATS_TEST_URL") else False)
    ),
    reason="E2E requires both POSTGRES_TEST_URI and NATS_TEST_URL",
)


# Service URLs for pipeline tests
_SERVICE_URLS = {
    "registry": os.environ.get("REGISTRY_URL", "http://localhost:8001"),
    "def-store": os.environ.get("DEF_STORE_URL", "http://localhost:8002"),
    "template-store": os.environ.get("TEMPLATE_STORE_URL", "http://localhost:8003"),
    "document-store": os.environ.get("DOCUMENT_STORE_URL", "http://localhost:8004"),
    "reporting-sync": os.environ.get("REPORTING_SYNC_URL", "http://localhost:8005"),
}

_pipeline_available = None


def _check_pipeline_available():
    """Check all 5 WIP services + PostgreSQL are reachable."""
    global _pipeline_available
    if _pipeline_available is not None:
        return _pipeline_available

    import httpx

    async def _try_all():
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                for name, url in _SERVICE_URLS.items():
                    resp = await client.get(f"{url}/health")
                    if resp.status_code != 200:
                        return False
            # Also check production PostgreSQL (not test PG)
            pg_uri = os.environ.get(
                "PIPELINE_POSTGRES_URI",
                "postgresql://wip:wip_dev_password@localhost:5432/wip_reporting",
            )
            conn = await asyncpg.connect(pg_uri, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    _pipeline_available = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _try_all()
    )
    return _pipeline_available


requires_pipeline = pytest.mark.skipif(
    not _check_pipeline_available(),
    reason="Pipeline test requires all 5 WIP services + PostgreSQL to be running",
)


# ============================================================================
# Fixtures
# ============================================================================


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


@pytest_asyncio.fixture
async def nats_client():
    """Create a real NATS connection for E2E tests.

    Creates a test stream, cleans up after each test.
    """
    nc = await nats.connect(NATS_TEST_URL)
    js = nc.jetstream()

    # Create test stream (delete first if exists from previous run)
    stream_name = "WIP_EVENTS_TEST"
    with contextlib.suppress(nats.js.errors.NotFoundError):
        await js.delete_stream(stream_name)

    await js.add_stream(
        name=stream_name,
        subjects=["wip.>"],
    )

    yield nc, js, stream_name

    # Cleanup
    with contextlib.suppress(Exception):
        await js.delete_stream(stream_name)
    await nc.close()
