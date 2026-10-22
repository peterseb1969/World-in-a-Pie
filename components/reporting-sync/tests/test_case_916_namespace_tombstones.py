"""A deleted namespace's reporting schema stays deleted (CASE-916).

Namespace deletion drops the namespace's PostgreSQL schema. Events still in
flight (a late document or term event, a retry, a replay, the debounced
term-closure rebuild) used to re-create it through ``CREATE SCHEMA IF NOT
EXISTS``, leaving an orphan schema holding only the late rows. The drop now
writes a tombstone under an advisory lock, and ``ensure_schema`` will not
create a tombstoned schema unless the Registry says the namespace is active
again.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync import main
from reporting_sync.models import SyncStatus
from reporting_sync.schema_manager import NamespaceDeletedError, SchemaManager
from reporting_sync.term_closure import TermClosureMaintainer
from reporting_sync.worker import SyncWorker
from wip_auth.registry_client import RegistryError

from .conftest import requires_postgres

NS = "ns-916"


def _status(answer):
    """A Registry lookup answering `answer` (a status, None, or an exception)."""
    calls: list[str] = []

    async def lookup(namespace: str):
        calls.append(namespace)
        if isinstance(answer, Exception):
            raise answer
        return answer

    lookup.calls = calls  # type: ignore[attr-defined]
    return lookup


def _never_called():
    async def lookup(namespace: str):
        raise AssertionError(f"Registry consulted for {namespace!r} on the unguarded path")

    return lookup


async def _schema_exists(pool, schema: str = NS) -> bool:
    return bool(await pool.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema))


async def _tombstoned(pool, namespace: str = NS) -> bool:
    return bool(await pool.fetchval(
        "SELECT 1 FROM public._wip_deleted_namespaces WHERE namespace = $1", namespace
    ))


def _template(value: str = "note") -> dict:
    return {
        "value": value,
        "version": 1,
        "fields": [{"name": "title", "type": "string"}],
        "identity_fields": ["title"],
        "reporting": {"sync_enabled": True},
    }


# ── the guard ─────────────────────────────────────────────────────────────


@requires_postgres
@pytest.mark.asyncio
async def test_a_namespace_never_deleted_is_created_without_asking_the_registry(pg_pool):
    sm = SchemaManager(pg_pool, namespace_status=_never_called())
    assert await sm.ensure_schema(NS) == NS
    assert await _schema_exists(pg_pool)
    # An existing schema takes the fast path, again without the Registry.
    assert await sm.ensure_schema(NS) == NS


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("registry_answer", [None, "locked", "archived"])
async def test_a_deleted_namespace_is_not_recreated(pg_pool, registry_answer):
    await SchemaManager(pg_pool, namespace_status=_never_called()).ensure_schema(NS)
    sm = SchemaManager(pg_pool, namespace_status=_status(registry_answer))
    await sm.drop_namespace_schema(NS)

    with pytest.raises(NamespaceDeletedError):
        await sm.ensure_schema(NS)
    assert not await _schema_exists(pg_pool)
    assert await _tombstoned(pg_pool)


@requires_postgres
@pytest.mark.asyncio
async def test_a_recreated_namespace_gets_its_schema_back(pg_pool):
    lookup = _status("active")
    sm = SchemaManager(pg_pool, namespace_status=lookup)
    await sm.drop_namespace_schema(NS)

    assert await sm.ensure_schema(NS) == NS
    assert await _schema_exists(pg_pool)
    assert not await _tombstoned(pg_pool)
    assert lookup.calls == [NS]


@requires_postgres
@pytest.mark.asyncio
async def test_an_unreachable_registry_creates_nothing(pg_pool):
    sm = SchemaManager(pg_pool, namespace_status=_status(RegistryError("down")))
    await sm.drop_namespace_schema(NS)

    with pytest.raises(RegistryError):
        await sm.ensure_schema(NS)
    assert not await _schema_exists(pg_pool)
    assert await _tombstoned(pg_pool)


@requires_postgres
@pytest.mark.asyncio
async def test_dropping_a_namespace_without_a_schema_still_tombstones_it(pg_pool):
    sm = SchemaManager(pg_pool, namespace_status=_status(None))
    assert await sm.drop_namespace_schema(NS) == 0
    assert await _tombstoned(pg_pool)
    with pytest.raises(NamespaceDeletedError):
        await sm.ensure_schema(NS)


@requires_postgres
@pytest.mark.asyncio
async def test_document_and_term_tables_are_guarded(pg_pool):
    """Both paths that left orphans on real installs: the document table and
    the term tables the debounced closure rebuild ensures."""
    sm = SchemaManager(pg_pool, namespace_status=_status(None))
    await sm.drop_namespace_schema(NS)

    with pytest.raises(NamespaceDeletedError):
        await sm.ensure_table_for_template(NS, _template())
    for ensure in (sm.ensure_terms_table, sm.ensure_term_relations_table, sm.ensure_term_closure_table):
        with pytest.raises(NamespaceDeletedError):
            await ensure(NS)
    assert not await _schema_exists(pg_pool)


@requires_postgres
@pytest.mark.asyncio
async def test_a_racing_ensure_never_outlives_the_drop(pg_pool):
    """A late event's ensure (schema missing, no tombstone yet) racing the
    deletion's drop: whichever takes the lock second, the schema is gone and
    the tombstone stays. Without the lock, the ensure could read "no
    tombstone", the drop commit, and the create land behind it."""
    # Creates the tombstone table so every round starts from the same state.
    await SchemaManager(pg_pool, namespace_status=_status(None)).drop_namespace_schema("warmup")
    for _ in range(20):
        await pg_pool.execute("DELETE FROM public._wip_deleted_namespaces WHERE namespace = $1", NS)
        creator = SchemaManager(pg_pool, namespace_status=_status(None))
        dropper = SchemaManager(pg_pool, namespace_status=_status(None))

        results = await asyncio.gather(
            creator.ensure_schema(NS), dropper.drop_namespace_schema(NS), return_exceptions=True
        )
        for r in results:
            assert not isinstance(r, Exception) or isinstance(r, NamespaceDeletedError), r
        assert await _tombstoned(pg_pool)
        assert not await _schema_exists(pg_pool)


# ── callers ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_acknowledges_an_event_for_a_deleted_namespace():
    status = SyncStatus(running=True, connected_to_nats=True, connected_to_postgres=True)
    worker = SyncWorker(MagicMock(), MagicMock(), MagicMock(), status)
    worker._process_document_event = AsyncMock(side_effect=NamespaceDeletedError(NS))

    msg = MagicMock()
    msg.data = json.dumps({"event_type": "document.created", "document": {}}).encode()
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    await worker._process_message(msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_awaited()
    assert status.events_skipped_deleted_namespace == 1
    assert status.events_failed == 0
    assert status.events_dropped == 0


@pytest.mark.asyncio
async def test_the_debounced_closure_rebuild_stops_quietly(monkeypatch):
    monkeypatch.setattr("reporting_sync.term_closure.DEBOUNCE_SECONDS", 0)
    maintainer = TermClosureMaintainer(MagicMock(), MagicMock())
    maintainer.rebuild = AsyncMock(side_effect=NamespaceDeletedError(NS))

    await maintainer._debounced(NS)

    maintainer.rebuild.assert_awaited_once_with(NS)


# ── orphan cleanup ────────────────────────────────────────────────────────


@requires_postgres
@pytest.mark.asyncio
async def test_orphan_cleanup_drops_only_reporting_shaped_schemas_of_unknown_namespaces(
    pg_pool, monkeypatch
):
    await main.init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool, namespace_status=_never_called())
    await sm.ensure_table_for_template("live-ns", _template())
    await sm.ensure_table_for_template("gone-ns", _template())
    await sm.ensure_terms_table("gone-terms")
    await pg_pool.execute('CREATE SCHEMA "foreign-ns"')
    await pg_pool.execute('CREATE TABLE "foreign-ns".something_else (id int)')

    known = {"live-ns": "active"}

    async def lookup(namespace):
        return known.get(namespace)

    monkeypatch.setattr(main, "registry_namespace_status", lookup)
    monkeypatch.setattr(main.state, "postgres_pool", pg_pool)

    preview = await main.clean_orphan_schemas(dry_run=True)
    assert preview["orphans"] == ["gone-ns", "gone-terms"]
    assert "foreign-ns" in preview["skipped"]
    assert preview["rows_dropped"] == {}
    assert await _schema_exists(pg_pool, "gone-ns")

    applied = await main.clean_orphan_schemas(dry_run=False)
    assert set(applied["rows_dropped"]) == {"gone-ns", "gone-terms"}
    for schema, present in (("live-ns", True), ("gone-ns", False), ("gone-terms", False), ("foreign-ns", True)):
        assert await _schema_exists(pg_pool, schema) is present
    assert await _tombstoned(pg_pool, "gone-ns")


@requires_postgres
@pytest.mark.asyncio
async def test_orphan_cleanup_drops_nothing_when_the_registry_cannot_answer(pg_pool, monkeypatch):
    await SchemaManager(pg_pool, namespace_status=_never_called()).ensure_terms_table("gone-ns")

    async def lookup(namespace):
        raise RegistryError("down")

    monkeypatch.setattr(main, "registry_namespace_status", lookup)
    monkeypatch.setattr(main.state, "postgres_pool", pg_pool)

    with pytest.raises(main.HTTPException) as err:
        await main.clean_orphan_schemas(dry_run=False)
    assert err.value.status_code == 502
    assert await _schema_exists(pg_pool, "gone-ns")
