"""reporting-sync keeps trying to reach PostgreSQL instead of degrading for good.

Provenance: CASE-862 S2 — the connect was one 120 s retry on connection
errors only (not PostgreSQL's "the database system is starting up"); after it
the service stayed degraded until restarted. It now connects in the
background without a deadline and starts the PostgreSQL work once it is up.
"""

import asyncpg
import pytest

import reporting_sync.main as rs


class _Pool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def harness(monkeypatch):
    """Stubs the I/O around _bring_up_postgres; records what it did."""
    rec = {"connects": 0, "started": [], "sleeps": [], "init_failures": 0}

    async def fake_sleep(seconds: float) -> None:
        rec["sleeps"].append(seconds)

    async def noop(_pool) -> None:
        return None

    monkeypatch.setattr(rs, "_postgres_sleep", fake_sleep)
    monkeypatch.setattr(rs, "init_postgres_schema", noop)
    monkeypatch.setattr(rs, "_recreate_registered_views", noop)
    monkeypatch.setattr(rs, "_start_postgres_work", lambda p, r: rec["started"].append((p, r)))
    monkeypatch.setattr(rs.state.sync_status, "connected_to_postgres", False)
    monkeypatch.setattr(rs.state.sync_status, "postgres_service_roles_ok", None)
    return rec


def _connect_sequence(rec, outcomes):
    outcomes = iter(outcomes)

    async def connect():
        rec["connects"] += 1
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return connect


@pytest.mark.asyncio
async def test_retries_through_refusal_and_starting_up_then_starts_work(monkeypatch, harness):
    pools = (_Pool(), _Pool())
    monkeypatch.setattr(rs, "connect_postgres", _connect_sequence(harness, [
        ConnectionRefusedError("refused"),
        asyncpg.CannotConnectNowError("the database system is starting up"),
        pools,
    ]))
    await rs._bring_up_postgres()
    assert harness["connects"] == 3
    assert harness["started"] == [pools]


@pytest.mark.asyncio
async def test_no_deadline(monkeypatch, harness):
    """Far past the old 120 s budget (40 failures with up-to-60 s backoff)."""
    pools = (_Pool(), _Pool())
    monkeypatch.setattr(rs, "connect_postgres", _connect_sequence(
        harness, [OSError("unreachable")] * 40 + [pools]))
    await rs._bring_up_postgres()
    assert harness["started"] == [pools]
    assert sum(harness["sleeps"]) > 120


@pytest.mark.asyncio
async def test_unusable_service_roles_stop_the_loop(monkeypatch, harness):
    monkeypatch.setattr(rs, "connect_postgres", _connect_sequence(
        harness, [rs.ServiceRolesError("passwords not set")]))
    await rs._bring_up_postgres()
    assert harness["connects"] == 1
    assert harness["started"] == []
    assert rs.state.sync_status.postgres_service_roles_ok is False


@pytest.mark.asyncio
async def test_a_failed_schema_init_closes_the_pools_and_retries(monkeypatch, harness):
    first, second = (_Pool(), _Pool()), (_Pool(), _Pool())
    monkeypatch.setattr(rs, "connect_postgres", _connect_sequence(harness, [first, second]))
    calls = []

    async def flaky_init(pool) -> None:
        calls.append(pool)
        if len(calls) == 1:
            raise asyncpg.PostgresError("schema init failed")

    monkeypatch.setattr(rs, "init_postgres_schema", flaky_init)
    await rs._bring_up_postgres()
    assert first[0].closed and first[1].closed
    assert harness["started"] == [second]
