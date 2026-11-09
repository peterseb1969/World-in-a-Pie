"""The smaller reporting-sync findings, each pinned where it was fixed.

Observability that reported what it did not measure, routes that could not
reach what they named, identifiers PostgreSQL would silently truncate,
bookkeeping that outlived what it described, and failures that read as
success or as the caller's fault.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync import main
from reporting_sync.main import _DangerousSql, _entity_of_relation, app, state
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.scoped_reads import ScopedReads, ScopedReadsUnavailable

from .conftest import requires_postgres

ADMIN = "rs_test_admin_key"
P = "/api/reporting-sync"


async def _call(method, path, key=ADMIN):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": key} if key else {}) as c:
        return await c.request(method, path)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_health_names_no_internal_hosts():
    r = await _call("GET", "/health", key=None)
    assert r.json()["details"] == {}


@pytest.mark.asyncio
async def test_unreadable_consumer_is_503_not_null(monkeypatch):
    monkeypatch.setattr(main, "get_consumer_info", AsyncMock(return_value=None))
    r = await _call("GET", f"{P}/metrics/consumer")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_consumer_info_reports_counts_and_last_delivery(monkeypatch):
    from datetime import UTC, datetime

    info = MagicMock(num_pending=3, num_ack_pending=1, num_redelivered=2)
    info.delivered.consumer_seq = 40
    info.delivered.stream_seq = 9000  # a stream position, not a count
    info.delivered.last_active = datetime(2026, 11, 9, tzinfo=UTC)
    js = MagicMock()
    js.consumer_info = AsyncMock(return_value=info)
    monkeypatch.setattr(type(state), "jetstream", property(lambda self: js))
    got = await main.get_consumer_info()
    assert got.delivered_messages == 40
    assert got.last_delivered == datetime(2026, 11, 9, tzinfo=UTC)
    assert got.pending_bytes is None


@pytest.mark.asyncio
async def test_alert_test_says_when_nothing_was_checked(monkeypatch):
    from reporting_sync.metrics import metrics
    from reporting_sync.models import AlertConfig

    monkeypatch.setattr(main, "get_consumer_info", AsyncMock(return_value=None))
    monkeypatch.setattr(metrics, "_alert_config", AlertConfig(enabled=False))
    r = await _call("POST", f"{P}/alerts/test")
    body = r.json()
    assert (body["checked"], body["alerting_enabled"], body["consumer_checked"]) == (
        False, False, False,
    )


def _integrity_upstream(monkeypatch, template_store: dict, document_store: dict):
    def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(main.settings.template_store_url):
            return httpx.Response(200, json=template_store)
        return httpx.Response(200, json=document_store)

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tpl,doc,status,incomplete",
    [
        ({"status": "healthy", "summary": {}},
         {"status": "healthy", "summary": {"total_documents": 5, "documents_checked": 5}},
         "healthy", []),
        # the limit cut the template scan short
        ({"status": "healthy", "summary": {"truncated": True}},
         {"status": "healthy", "summary": {"total_documents": 5, "documents_checked": 5}},
         "incomplete", ["template-store"]),
        # checks that could not run
        ({"status": "unknown", "summary": {}},
         {"status": "incomplete", "summary": {"total_documents": 5, "documents_checked": 5}},
         "incomplete", ["template-store", "document-store"]),
        # a limited document scan
        ({"status": "healthy", "summary": {}},
         {"status": "healthy", "summary": {"total_documents": 50, "documents_checked": 10}},
         "incomplete", ["document-store"]),
    ],
)
async def test_integrity_is_healthy_only_when_everything_was_checked(
    monkeypatch, tpl, doc, status, incomplete,
):
    _integrity_upstream(monkeypatch, tpl, doc)
    r = await _call("GET", f"{P}/health/integrity")
    body = r.json()
    assert (body["status"], body["services_incomplete"]) == (status, incomplete)


# ---------------------------------------------------------------------------
# Raw SQL refusal reads code, not data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,refused",
    [
        ("SELECT * FROM t WHERE note = 'please UPDATE this'", False),
        ("SELECT 'it''s a delete' AS x", False),
        ("SELECT E'can\\'t drop'", False),
        ('SELECT "update" FROM t', False),
        ("SELECT 1 -- delete me later", False),
        ("SELECT 1 /* create */", False),
        ("SELECT $q$ insert $q$", False),
        ("DELETE FROM t", True),
        ("SELECT 1; DROP TABLE t", True),
        ("SELECT 'x'; UPDATE t SET a = 1", True),
    ],
)
def test_write_keywords_are_looked_for_in_code_only(sql, refused):
    assert bool(_DangerousSql.search(sql)) is refused


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_a_version_suffix_cannot_push_a_name_past_63_bytes():
    sm = SchemaManager(None)
    value = "x" * 56  # doc_ + 56 = 60 bytes: the base fits
    assert sm.get_table_name(value) == f"doc_{value}"
    with pytest.raises(ValueError):
        sm.get_table_name(value, version=1)  # 64 bytes: v1 and v2 would truncate alike
    with pytest.raises(ValueError):
        sm.entities_view_name(value)


@pytest.mark.parametrize(
    "relation,entity",
    [("doc_patient", "patient"), ("doc_patient__v3", "patient"),
     ("doc_patient__entities", "patient"), ("terms", None)],
)
def test_tables_listing_names_the_entity_of_every_relation(relation, entity):
    assert _entity_of_relation(relation) == entity


@pytest.mark.asyncio
async def test_export_refusal_lists_every_allowed_table(monkeypatch):
    monkeypatch.setattr(state, "read_pool", MagicMock())
    r = await _call("GET", f"{P}/export/csv?namespace=ns-a&table=pg_user")
    assert r.status_code == 400
    for name in main._EXPORT_ALLOWED_EXACT:
        assert name in r.json()["detail"]


@pytest.mark.asyncio
async def test_reporting_config_follows_the_template(monkeypatch):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["namespace"] == "ns-a"
        if request.url.path.endswith("/by-value/Visit"):
            return httpx.Response(200, json={"reporting": {"table_name": "visits"}})
        return httpx.Response(404, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handle), **k))
    config = await main._reporting_config("ns-a", "Visit")
    assert SchemaManager(None).get_table_name("Visit", config, 2) == "visits__v2"
    assert await main._reporting_config("ns-a", "Other") is None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dropped_relation_event_names_its_namespace():
    from reporting_sync.models import SyncStatus
    from reporting_sync.worker import SyncWorker

    status = SyncStatus(running=True, connected_to_nats=True, connected_to_postgres=True)
    worker = SyncWorker(MagicMock(), MagicMock(), MagicMock(), status)
    msg = MagicMock()
    msg.metadata.num_delivered = 99
    msg.term = AsyncMock()
    msg.nak = AsyncMock()
    event = {"event_type": "term_relation.created", "term_relation": {
        "namespace": "ns-a", "source_term_id": "S", "target_term_id": "T",
    }}
    await worker._nak_or_record_drop(msg, event, "boom")
    drop = status.recent_drops[-1]
    assert (drop.entity_id, drop.namespace) == ("S", "ns-a")


# ---------------------------------------------------------------------------
# Scoped reads: setup failures are 503 material, never a raw 500
# ---------------------------------------------------------------------------


def _scoped(**kw) -> ScopedReads:
    return ScopedReads(endpoint={}, owner_pool=MagicMock(), manager_user="m",
                       manager_password="p", **kw)


@pytest.mark.asyncio
async def test_a_failing_owner_side_step_is_scoped_reads_unavailable():
    reads = _scoped()
    reads.owner_pool.fetch = AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError("no"))
    with pytest.raises(ScopedReadsUnavailable):
        async with reads.connection(["ns_a"]):
            pass


@pytest.mark.asyncio
async def test_busy_slots_time_out_as_unavailable(monkeypatch):
    reads = _scoped(max_connections=1, slot_timeout=0.05)
    monkeypatch.setattr(reads, "_missing", AsyncMock(return_value=(False, False)))
    conn = MagicMock(close=AsyncMock())
    monkeypatch.setattr(reads, "_connect", AsyncMock(return_value=conn))
    async with reads.connection(["ns_a"]):
        with pytest.raises(ScopedReadsUnavailable):
            async with reads.connection(["ns_a"]):
                pass
    # the slot is released again
    async with reads.connection(["ns_a"]):
        pass


@pytest.mark.asyncio
async def test_errors_from_the_callers_sql_are_not_converted(monkeypatch):
    reads = _scoped()
    monkeypatch.setattr(reads, "_missing", AsyncMock(return_value=(False, False)))
    monkeypatch.setattr(reads, "_connect", AsyncMock(return_value=MagicMock(close=AsyncMock())))
    with pytest.raises(asyncpg.UndefinedTableError):
        async with reads.connection(["ns_a"]):
            raise asyncpg.UndefinedTableError("caller's SQL")


# ---------------------------------------------------------------------------
# Real PostgreSQL: column-name truncation, view bookkeeping
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_a_long_field_name_neither_drifts_nor_re_alters(pg_pool):
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    long_name = "f" * 70
    template = {"template_id": "t-long", "value": "longcols", "version": 1,
                "fields": [{"name": long_name, "type": "string"}],
                "identity_fields": [long_name], "reporting": {"sync_enabled": True}}
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template("rs1213", template)
    fields = sm.parse_template_fields(template["fields"])
    existing = await sm.get_existing_columns("rs1213", "doc_longcols__v1")
    assert sm.expected_columns_for_template(fields) <= existing  # parity: no phantom drift
    # A second ensure finds the column it made instead of adding it again.
    await sm.ensure_table_for_template("rs1213", template, cached=False)


@requires_postgres
@pytest.mark.asyncio
async def test_dropping_a_namespace_drops_the_registrations_of_views_it_took(pg_pool):
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    async with pg_pool.acquire() as conn:
        for ns in ("rs_src", "rs_reader"):
            await sm.ensure_schema(ns)
        await conn.execute('CREATE TABLE "rs_src"."doc_x__v1" (document_id text)')
        await conn.execute('CREATE VIEW "rs_reader"."v_over_src" AS SELECT * FROM "rs_src"."doc_x__v1"')
        await conn.execute('CREATE VIEW "rs_reader"."v_own" AS SELECT 1 AS one')
        await conn.execute(
            "INSERT INTO public._wip_views (namespace, view_name, view_sql) VALUES"
            " ('rs_reader', 'v_over_src', 'x'), ('rs_reader', 'v_own', 'y')"
        )
    await sm.drop_namespace_schema("rs_src")
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT view_name FROM public._wip_views WHERE namespace = 'rs_reader'")
    assert [r["view_name"] for r in rows] == ["v_own"]


@requires_postgres
@pytest.mark.asyncio
async def test_a_template_rebuild_puts_back_unrelated_registered_views(pg_pool):
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_schema("rs_rb")
    async with pg_pool.acquire() as conn:
        await conn.execute('CREATE TABLE "rs_rb"."doc_a__v1" (document_id text)')
        await conn.execute('CREATE TABLE "rs_rb"."doc_b__v1" (document_id text)')
        await conn.execute(
            "INSERT INTO public._wip_views (namespace, view_name, view_sql) VALUES"
            " ('rs_rb', 'v_b', 'SELECT * FROM doc_b__v1')"
        )
        await conn.execute('CREATE VIEW "rs_rb"."v_b" AS SELECT * FROM "rs_rb"."doc_b__v1"')
    await sm.drop_relations_for_template("rs_rb", "a")
    assert await sm.relation_kind("rs_rb", "v_b") == "view"


@requires_postgres
@pytest.mark.asyncio
async def test_a_view_registration_split_into_the_role_schema_is_repaired(pg_pool):
    from reporting_sync.bookkeeping import repair_bookkeeping_location
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    async with pg_pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{role}"')
        await conn.execute(
            f'CREATE TABLE "{role}"._wip_views (LIKE public._wip_views INCLUDING ALL)'
        )
        await conn.execute(
            f"INSERT INTO \"{role}\"._wip_views (namespace, view_name, view_sql)"
            " VALUES ('ns1', 'v1', 'SELECT 1')"
        )
        actions = await repair_bookkeeping_location(conn)
        moved = await conn.fetchval(
            "SELECT view_sql FROM public._wip_views WHERE namespace = 'ns1' AND view_name = 'v1'"
        )
    assert any("_wip_views" in a for a in actions)
    assert moved == "SELECT 1"


@pytest.mark.asyncio
async def test_slot_release_survives_cancellation(monkeypatch):
    reads = _scoped(max_connections=1, slot_timeout=0.05)
    monkeypatch.setattr(reads, "_missing", AsyncMock(return_value=(False, False)))
    monkeypatch.setattr(reads, "_connect", AsyncMock(return_value=MagicMock(close=AsyncMock())))

    async def hold():
        async with reads.connection(["ns_a"]):
            await asyncio.sleep(10)

    task = asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with reads.connection(["ns_a"]):
        pass


# ---------------------------------------------------------------------------
# CASE-1209: integer columns are int8, and existing int4 ones are widened
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_int4_integer_columns_are_widened_on_every_version_table(pg_pool):
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)

    def tpl(version):
        return {"template_id": "t-int", "value": "counts", "version": version,
                "fields": [{"name": "k", "type": "string"}, {"name": "n", "type": "integer"}],
                "identity_fields": ["k"], "reporting": {"sync_enabled": True}}

    await sm.ensure_table_for_template("rs1209", tpl(1))
    await sm.ensure_table_for_template("rs1209", tpl(2))
    async with pg_pool.acquire() as conn:
        # As tables created before int8: int4 columns under the entity views.
        await conn.execute('DROP VIEW "rs1209"."doc_counts", "rs1209"."doc_counts__entities"')
        for t in ("doc_counts__v1", "doc_counts__v2"):
            await conn.execute(f'ALTER TABLE "rs1209"."{t}" ALTER COLUMN "n" TYPE INTEGER')
    await sm.ensure_views_for_template("rs1209", "counts")
    fields = sm.parse_template_fields(tpl(2)["fields"])
    widened = await sm._widen_integer_columns("rs1209", "counts", fields, None, "entity")
    assert len(widened) == 2
    async with pg_pool.acquire() as conn:
        types = await conn.fetch(
            "SELECT table_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'rs1209' AND column_name = 'n' ORDER BY table_name"
        )
        await conn.execute('INSERT INTO "rs1209"."doc_counts__v2" (document_id, namespace, template_id, '
                           "template_version, version, status, identity_hash, created_at, k, n) "
                           "VALUES ('d', 'rs1209', 't', 2, 1, 'active', 'h', now(), 'k', 3000000000)")
        through_view = await conn.fetchval('SELECT n FROM "rs1209"."doc_counts" WHERE document_id = $1', "d")
    assert {r["data_type"] for r in types} == {"bigint"}  # tables and views alike
    assert through_view == 3_000_000_000
