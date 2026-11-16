"""Reporting-sync's reference and naming paths (commit review of b9cf612e).

- /table-name answers a long template value (50-59 bytes) instead of a 500,
  and every name of the identity-core view is the one PostgreSQL stores:
  the view is created, found and listed under the truncated name, and an
  existing one is not rebuilt on every event.
- A template's references report the canonical id each one resolved to.
- Document text that spells the HTML snippet marks never becomes <b>.
- Every document table has a GIN index on term_references_json (tables
  created before it get it on their next migration pass), and the
  reference scans are bounded like the search path.
- Term and edge lookups find tables named by a reporting.table_name override.
- Template values are read concurrently, a bounded number at a time.
- A batch job's table lock is dropped once no job holds or waits on it.
- /references/term/{id}/documents answers a term the caller cannot read
  like a term that does not exist.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync import main, search_service
from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.main import (
    _entity_of_relation,
    _group_relation,
    app,
    init_postgres_schema,
    state,
)
from reporting_sync.models import ReportingConfig
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.search_service import (
    DocumentReference,
    SearchService,
    TermDocumentsResponse,
)
from reporting_sync.transformer import DocumentTransformer
from wip_auth import clear_permission_cache

from .conftest import requires_postgres

NS = "rs1295"
ADMIN = "rs_test_admin_key"
READER = "rs_reader_key"  # read on ns-a
P = "/api/reporting-sync"

LONG = "v" * 52  # doc_ + 52 = 56 bytes; + __entities = 66: truncated to 63


def _template(value, fields=None, reporting=None, usage="entity"):
    return {
        "template_id": f"tpl-{value[:8]}", "value": value, "version": 1,
        "usage": usage,
        "fields": fields or [{"name": "title", "type": "string"}],
        "identity_fields": ["title"],
        "reporting": {"sync_enabled": True, **(reporting or {})},
    }


async def _get(key, path):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": key}) as c:
        return await c.get(path)


# ---------------------------------------------------------------------------
# Long names: /table-name and the identity-core view
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_table_name_answers_a_long_value_with_the_stored_view_name(pg_pool, monkeypatch):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template(NS, _template(LONG))

    async def no_config(namespace, template_value):
        return None

    monkeypatch.setattr(main, "_reporting_config", no_config)
    monkeypatch.setattr(state, "postgres_pool", pg_pool)
    r = await _get(ADMIN, f"{P}/table-name?namespace={NS}&template_value={LONG}")

    assert r.status_code == 200, r.text
    view = r.json()["entities_view"]
    assert view == f"doc_{LONG}__entities".encode()[:63].decode()
    assert await sm.relation_kind(NS, view) == "view"


@requires_postgres
@pytest.mark.asyncio
async def test_an_existing_long_view_is_not_rebuilt_on_every_event(pg_pool, monkeypatch):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template(NS, _template(LONG))

    rebuilds: list[str] = []
    original = sm.ensure_views_for_template

    async def counting(namespace, template_value, config=None):
        rebuilds.append(template_value)
        return await original(namespace, template_value, config)

    monkeypatch.setattr(sm, "ensure_views_for_template", counting)
    for _ in range(2):
        await sm.ensure_table_for_template(NS, _template(LONG), cached=False)

    assert rebuilds == []


def test_the_listing_groups_a_truncated_view_under_its_entity():
    base = f"doc_{LONG}"
    view = SchemaManager.entities_view_for_base(base)
    assert not view.endswith("__entities")
    tables = {f"{base}__v1"}
    entities: dict = {}

    _group_relation(entities, NS, f"{base}__v1", False, 3, tables)
    _group_relation(entities, NS, view, True, None, tables)
    _group_relation(entities, NS, base, True, None, tables)

    assert list(entities) == [(NS, base)]
    entity = entities[(NS, base)]
    assert entity["entities_view"] == view
    assert entity["default_view_present"] is True
    assert _entity_of_relation(view, tables) == LONG


# ---------------------------------------------------------------------------
# A template's references carry the canonical id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_references_report_the_resolved_canonical_id(monkeypatch):
    template = {
        "template_id": "tpl-1", "value": "PATIENT", "namespace": "ns-a",
        "status": "draft", "extends": "BASE",
        "fields": [{"name": "country", "type": "term", "terminology_ref": "COUNTRY"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/templates/tpl-1"):
            return httpx.Response(200, json=template)
        if path.endswith("/templates/BASE"):
            return httpx.Response(200, json={"template_id": "tpl-base", "value": "BASE",
                                             "status": "active"})
        if path.endswith("/terminologies/COUNTRY"):
            return httpx.Response(200, json={"terminology_id": "term-uuid", "value": "COUNTRY",
                                             "status": "active"})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(search_service.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handle), **k))

    result = await SearchService(None).get_entity_references("template", "tpl-1")

    by_path = {ref.field_path: ref for ref in result.entity.references}
    assert by_path["fields.country.terminology_ref"].ref_id == "term-uuid"
    assert by_path["fields.country.terminology_ref"].ref_value == "COUNTRY"
    assert by_path["extends"].ref_id == "tpl-base"


# ---------------------------------------------------------------------------
# Snippets
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_text_spelling_the_snippet_marks_is_not_bold(pg_pool, monkeypatch):
    await init_postgres_schema(pg_pool)
    template = _template("Note", [{"name": "title", "type": "string", "full_text_indexed": True}],
                         reporting={"include_metadata": False})
    table = await SchemaManager(pg_pool).ensure_table_for_template(NS, template)
    start, stop = "\u2063wip-b\u2063", "\u2063/wip-b\u2063"  # the HTML snippet marks
    text = f"hot soup is {start}good{stop} food"
    doc = {
        "document_id": "n-1", "namespace": NS, "template_id": template["template_id"],
        "template_version": 1, "version": 1, "status": "active", "identity_hash": "h1",
        "created_at": "2026-11-16T10:00:00Z", "data": {"title": text},
    }
    transformer = DocumentTransformer(ReportingConfig(**template["reporting"]))
    async with pg_pool.acquire() as conn:
        for row in transformer.transform(doc, template):
            sql, params = transformer.generate_upsert_sql(table, row, "latest_only")
            await conn.execute(sql, *params)

    async def values(client, ids):
        return {}

    monkeypatch.setattr(search_service, "_template_values", values)
    hits = await SearchService(pg_pool)._search_documents("soup", NS, None, mode="fts")

    assert [h.id for h in hits] == ["n-1"]
    snippet = hits[0].snippet
    # Only the real match is bold: the text that spelled the marks loses its
    # invisible U+2063 and reads as plain text, not as <b>good</b>.
    assert "<b>soup</b>" in snippet, snippet
    assert "<b>good</b>" not in snippet, snippet
    assert "\u2063" not in snippet


# ---------------------------------------------------------------------------
# term_references_json index, bounded scans, table_name overrides
# ---------------------------------------------------------------------------


async def _indexes(pool, table):
    async with pool.acquire() as conn:
        return {r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND tablename = $2",
            NS, table,
        )}


@requires_postgres
@pytest.mark.asyncio
async def test_a_table_created_before_the_index_gets_it(pg_pool):
    await init_postgres_schema(pg_pool)
    await SchemaManager(pg_pool).ensure_table_for_template(NS, _template("keyed"))
    assert "doc_keyed__v1_term_refs_idx" in await _indexes(pg_pool, "doc_keyed__v1")
    async with pg_pool.acquire() as conn:
        await conn.execute(f'DROP INDEX "{NS}"."doc_keyed__v1_term_refs_idx"')

    # A new process (a fresh SchemaManager) catches the table up.
    await SchemaManager(pg_pool).ensure_table_for_template(NS, _template("keyed"), cached=False)

    assert "doc_keyed__v1_term_refs_idx" in await _indexes(pg_pool, "doc_keyed__v1")


@requires_postgres
@pytest.mark.asyncio
async def test_term_and_edge_lookups_find_an_overridden_table_name(pg_pool, monkeypatch):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    docs = await sm.ensure_table_for_template(
        NS, _template("Visit", reporting={"table_name": "visits", "include_metadata": False}),
    )
    edges = await sm.ensure_table_for_template(
        NS, _template("Link", reporting={"table_name": "links", "include_metadata": False},
                  usage="relationship"),
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            f"INSERT INTO {docs} (document_id, namespace, template_id, template_version, "
            "version, status, identity_hash, term_references_json) VALUES ('d-1', $1, "
            "'tpl-Visit', 1, 1, 'active', 'h1', $2::jsonb)",
            NS, json.dumps([{"term_id": "term-1", "field_path": "country"}]),
        )
        await conn.execute(
            f"INSERT INTO {edges} (document_id, namespace, template_id, template_version, "
            "version, status, identity_hash, source_ref_id, target_ref_id) VALUES ('e-1', $1, "
            "'tpl-Link', 1, 1, 'active', 'h2', 'd-1', 'd-2')",
            NS,
        )

    svc = SearchService(pg_pool)
    terms = await svc._documents_referencing_term("term-1", NS)
    touching = await svc._edges_touching("d-1", NS)

    assert [(r["document_id"], r["table_name"], r["field_path"]) for r in terms] == [
        ("d-1", "visits__v1", "country"),
    ]
    assert [(r["document_id"], r["field_path"]) for r in touching] == [("e-1", "source_ref")]


class _Conn:
    def __init__(self, timeouts):
        self.timeouts = timeouts

    async def fetch(self, sql, *args, timeout=None):
        self.timeouts.append(timeout)
        if "information_schema" in sql:
            return [{"table_schema": NS, "table_name": "t", "has_created_at": True}]
        return []


class _Pool:
    def __init__(self, timeouts):
        self.timeouts = timeouts

    def acquire(self, timeout=None):
        self.timeouts.append(("acquire", timeout))
        conn = _Conn(self.timeouts)

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
@pytest.mark.parametrize("scan", ["_documents_referencing_term", "_edges_touching"])
async def test_reference_scans_are_bounded_like_search(scan):
    timeouts: list = []
    await getattr(SearchService(_Pool(timeouts)), scan)("x", NS)
    bound = search_service.SEARCH_QUERY_TIMEOUT
    assert timeouts == [("acquire", bound), bound, bound]


# ---------------------------------------------------------------------------
# Template values: concurrent, bounded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_values_are_read_concurrently_and_bounded(monkeypatch):
    in_flight = peak = 0

    async def fetch(client, url):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        template_id = url.rsplit("/", 1)[1]
        return None if template_id == "gone" else {"value": template_id.upper()}

    monkeypatch.setattr(search_service, "_fetch_target", fetch)
    ids = {f"t{i}" for i in range(20)} | {"gone"}

    values = await search_service._template_values(None, ids)

    assert values == {f"t{i}": f"T{i}" for i in range(20)}
    assert 1 < peak <= search_service._TEMPLATE_VALUE_READS


# ---------------------------------------------------------------------------
# Batch table lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_table_lock_is_dropped_once_unused_and_keeps_arrival_order():
    svc = BatchSyncService.__new__(BatchSyncService)
    svc._table_locks = {}
    order: list[str] = []
    first_in = asyncio.Event()
    release = asyncio.Event()

    async def job(name, hold=False):
        async with svc._table_lock("Patient"):
            order.append(name)
            if hold:
                first_in.set()
                await release.wait()

    first = asyncio.create_task(job("a", hold=True))
    await first_in.wait()
    waiters = [asyncio.create_task(job(n)) for n in ("b", "c")]
    await asyncio.sleep(0)
    cancelled = asyncio.create_task(job("never"))
    await asyncio.sleep(0)
    cancelled.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancelled
    assert svc._table_locks["patient"].users == 3  # a holds; b and c wait
    release.set()
    await asyncio.gather(first, *waiters)

    assert order == ["a", "b", "c"]
    assert svc._table_locks == {}


# ---------------------------------------------------------------------------
# /references/term/{id}/documents authorizes the term
# ---------------------------------------------------------------------------


@pytest.fixture
def _fresh_permission_cache():
    clear_permission_cache()
    yield
    clear_permission_cache()


class _TermIn:
    def __init__(self, owner, found=True):
        self.owner, self.found = owner, found

    async def get_term_documents(self, term_id, limit, namespace, within):
        r = TermDocumentsResponse(term_id=term_id, total=1, documents=[DocumentReference(
            document_id="d-1", template_id="t-1", field_path="country", status="active",
        )])
        r._term_found = self.found
        r._term_namespace = self.owner
        return r


@pytest.mark.asyncio
@pytest.mark.usefixtures("_fresh_permission_cache")
@pytest.mark.parametrize(
    "term,key,documents",
    [
        (_TermIn("ns-a"), READER, 1),
        (_TermIn("ns-c"), READER, 0),  # answered as a term that does not exist
        (_TermIn(None), READER, 0),  # namespace unknown: administrators only
        (_TermIn("ns-c"), ADMIN, 1),
        (_TermIn(None), ADMIN, 1),
    ],
)
async def test_term_documents_answer_an_unreadable_term_as_unknown(monkeypatch, term, key, documents):
    monkeypatch.setattr(state, "search_service", term)
    r = await _get(key, f"{P}/references/term/term-1/documents?namespace=ns-a"
                   if key == READER else f"{P}/references/term/term-1/documents")

    assert r.status_code == 200, r.text
    body = r.json()
    assert (len(body["documents"]), body["total"]) == (documents, documents)
