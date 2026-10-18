"""CASE-890 — services authenticate to MongoDB as their own user, never via the URI.

Every service prints MONGO_URI at startup, so the credentials travel as
client arguments (username/password, authSource=admin) taken from the
deployer-injected MONGO_USER / MONGO_PASSWORD. Without a user the client is
anonymous, as against the unauthenticated test containers.
"""

import pytest

from wip_auth.mongo import mongo_credentials, mongo_identity


def test_credentials_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_USER", "def-store")
    monkeypatch.setenv("MONGO_PASSWORD", "dummy-password")
    assert mongo_credentials() == {
        "username": "def-store", "password": "dummy-password", "authSource": "admin",
    }
    assert mongo_identity() == "def-store"


def test_no_user_means_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGO_USER", raising=False)
    monkeypatch.setenv("MONGO_PASSWORD", "ignored")
    assert mongo_credentials() == {}
    assert mongo_identity() == "anonymous"


def test_credentials_are_accepted_by_motor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keyword names are Motor's own: constructing a client with them
    (no connection is made) must not raise."""
    motor = pytest.importorskip("motor.motor_asyncio")
    monkeypatch.setenv("MONGO_USER", "def-store")
    monkeypatch.setenv("MONGO_PASSWORD", "dummy-password")
    client = motor.AsyncIOMotorClient("mongodb://localhost:27017/", connect=False, **mongo_credentials())
    # PyMongo only warns on an unknown keyword, so check what it parsed. The
    # parsed credentials are only exposed privately.
    creds = client.options.pool_options._credentials
    assert creds.username == "def-store"
    assert creds.source == "admin"
    client.close()
