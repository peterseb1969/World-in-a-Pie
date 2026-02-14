"""Pytest configuration and fixtures for Registry tests."""

import asyncio
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_registry_test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault("AUTH_ENABLED", "true")

from registry.main import app
from registry.models.id_pool import IdPool
from registry.models.namespace import Namespace
from registry.models.entry import RegistryEntry
from registry.models.id_counter import IdCounter
from registry.services.auth import AuthService


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing the API."""
    # Connect to MongoDB and initialize Beanie
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await init_beanie(
        database=mongo_client[os.environ["DATABASE_NAME"]],
        document_models=[IdPool, Namespace, RegistryEntry, IdCounter]
    )

    # Store client in app state (needed by health check)
    app.state.mongodb_client = mongo_client

    # Initialize auth service
    AuthService.initialize(master_key=os.environ["MASTER_API_KEY"])

    # Create test ID pools
    await _create_test_id_pools()

    # Create test HTTP client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup
    await RegistryEntry.delete_all()
    await IdPool.delete_all()
    await Namespace.delete_all()
    await mongo_client.drop_database(os.environ["DATABASE_NAME"])
    mongo_client.close()


async def _create_test_id_pools():
    """Create ID pools needed for tests."""
    pools = [
        IdPool(
            pool_id="default",
            name="Default Pool",
            description="Default ID pool for testing"
        ),
        IdPool(
            pool_id="vendor1",
            name="Vendor 1",
            description="Vendor 1 ID pool for testing"
        ),
        IdPool(
            pool_id="vendor2",
            name="Vendor 2",
            description="Vendor 2 ID pool for testing"
        ),
    ]

    for pool in pools:
        existing = await IdPool.find_one(IdPool.pool_id == pool.pool_id)
        if not existing:
            await pool.insert()


@pytest.fixture
def api_key() -> str:
    """Return the test API key."""
    return os.environ["MASTER_API_KEY"]


@pytest.fixture
def auth_headers(api_key: str) -> dict:
    """Return headers with API key for authenticated requests."""
    return {"X-API-Key": api_key}
