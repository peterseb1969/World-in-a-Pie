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


# ── a probe answers inside its budget ────────────────────────────────────────


def test_client_options_bound_server_selection_and_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    from wip_auth.mongo import mongo_client_options

    monkeypatch.setenv("MONGO_USER", "def-store")
    options = mongo_client_options()
    assert options["serverSelectionTimeoutMS"] == 2000
    assert options["connectTimeoutMS"] == 2000
    assert options["username"] == "def-store"


def test_client_options_are_accepted_by_motor(monkeypatch: pytest.MonkeyPatch) -> None:
    from wip_auth.mongo import mongo_client_options

    motor = pytest.importorskip("motor.motor_asyncio")
    monkeypatch.delenv("MONGO_USER", raising=False)
    client = motor.AsyncIOMotorClient("mongodb://localhost:27017/", connect=False, **mongo_client_options())
    assert client.options.server_selection_timeout == 2.0
    assert client.options.pool_options.connect_timeout == 2.0


@pytest.mark.asyncio
async def test_a_ping_that_does_not_answer_fails_within_its_timeout() -> None:
    import asyncio
    import time

    from wip_auth.mongo import mongo_ping

    class Hung:
        class admin:
            @staticmethod
            async def command(name: str) -> None:
                await asyncio.sleep(60)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await mongo_ping(Hung(), timeout=0.05)
    assert time.monotonic() - started < 1
