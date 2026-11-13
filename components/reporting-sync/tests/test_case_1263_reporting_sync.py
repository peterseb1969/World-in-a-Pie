"""CASE-1263 batch 2 (review of 8dca5042): reporting-sync answers what it
could not check instead of reading an outage as "nothing there".

- Parity's shared-template sweep: a failed facet listing leaves the
  namespace's own templates checked and says the sweep did not run; a
  template it cannot read is a row with the reason; same-valued shared
  templates are one row whose count covers all their documents.
- The parity monitor repairs a shared template's gap by finding the
  template in its own namespace.
- Entity references: a store that does not answer is a 503, a 404 is a
  broken reference.
- Recent activity names the sources it could not read.
- A force batch sync with nothing to rebuild drops nothing.
- rows_written counts rows the upsert actually wrote.
- A manual alert check does not move the error-rate window.
- A dotted field name gets the underscored column its rows carry.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync import parity
from reporting_sync.main import app, state
from reporting_sync.metrics import MetricsCollector
from reporting_sync.parity import TemplateParity
from reporting_sync.parity_monitor import ParityMonitor, _gaps
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.search_service import SearchService, UpstreamError

from .conftest import requires_postgres

NS = "rs1263"


# --- parity: the shared-template sweep --------------------------------------


def _store(facets, templates):
    """A transport answering the facet listing and template reads."""
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/template-facets"):
            if isinstance(facets, int):
                return httpx.Response(facets)
            return httpx.Response(200, json={"facets": facets})
        tid = request.url.path.rsplit("/", 1)[-1]
        status, body = templates[tid]
        return httpx.Response(status, json=body)
    return handle


def _with_transport(monkeypatch, handle):
    real = httpx.AsyncClient
    monkeypatch.setattr(parity.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handle), **k))


@pytest.mark.asyncio
async def test_an_unreadable_shared_template_is_a_row_not_an_abort(monkeypatch):
    _with_transport(monkeypatch, _store(
        [{"template_id": "t-gone", "template_value": "GONE", "template_namespace": "wip", "document_count": 2},
         {"template_id": "t-down", "template_value": "DOWN", "template_namespace": "wip", "document_count": 1},
         {"template_id": "t-ok", "template_value": "OK", "template_namespace": "wip", "document_count": 1}],
        {"t-gone": (404, {}), "t-down": (503, {}), "t-ok": (200, {"template_id": "t-ok", "namespace": "wip"})},
    ))
    foreign, unreadable = await parity._fetch_foreign_templates(NS, [])
    assert [t["template_id"] for t in foreign] == ["t-ok"]
    errors = {r.template_value: r.error for r in unreadable}
    assert "not found, though live documents use it" in errors["GONE"]
    assert "template-store unavailable" in errors["DOWN"]


@pytest.mark.asyncio
async def test_same_valued_shared_templates_are_one_row_counting_all_their_documents(monkeypatch):
    _with_transport(monkeypatch, _store(
        [{"template_id": "t-wip", "template_value": "ORDER", "template_namespace": "wip", "document_count": 10},
         {"template_id": "t-other", "template_value": "order", "template_namespace": "other", "document_count": 4}],
        {"t-wip": (200, {"template_id": "t-wip", "namespace": "wip", "value": "ORDER"})},
    ))
    foreign, _ = await parity._fetch_foreign_templates(NS, [])
    assert len(foreign) == 1
    assert foreign[0]["_count_template_ids"] == ["t-wip", "t-other"]


@requires_postgres
@pytest.mark.asyncio
async def test_a_failed_sweep_still_checks_the_namespaces_own_templates(pg_pool):
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    await SchemaManager(pg_pool).ensure_schema(NS)
    own = {"template_id": "t-own", "namespace": NS, "value": "own_t", "version": 1,
           "fields": [{"name": "title", "type": "string"}], "reporting": {"sync_enabled": True}}
    refused = httpx.HTTPStatusError("503", request=httpx.Request("GET", "http://x"),
                                    response=httpx.Response(503))
    with ExitStack() as stack:
        for target, mock in (
            ("_fetch_namespace_templates", AsyncMock(return_value=[own])),
            ("_fetch_foreign_templates", AsyncMock(side_effect=refused)),
            ("_expected_document_total", AsyncMock(return_value=0)),
            ("_expected_active_terms", AsyncMock(return_value=0)),
            ("_expected_active_relations", AsyncMock(return_value=0)),
        ):
            stack.enter_context(patch(f"reporting_sync.parity.{target}", new=mock))
        result = await parity.check_namespace_parity(pg_pool, NS)
    assert [t.template_value for t in result.templates] == ["own_t"]
    assert "shared templates not checked" in (result.bookkeeping_error or "")
    assert result.ok is False


# --- parity monitor: a shared template's repair -----------------------------


def test_a_shared_templates_gap_is_keyed_by_its_own_namespace():
    from reporting_sync.parity import NamespaceParityResult

    row = TemplateParity(template_value="shared_visit", template_namespace="wip")
    result = NamespaceParityResult(namespace=NS, schema_name=NS, schema_present=True,
                                   table_count=0, templates=[row])
    assert _gaps(result)[0] == {"shared_template:wip:shared_visit"}


@pytest.mark.asyncio
async def test_a_shared_templates_repair_reads_it_in_its_own_namespace():
    batch = MagicMock()
    tpl = {"template_id": "t-wip", "namespace": "wip", "value": "shared_visit"}
    batch._fetch_template_by_value = AsyncMock(return_value=tpl)
    batch.start_batch_sync = AsyncMock(return_value=MagicMock(status="running"))
    monitor = ParityMonitor(MagicMock(), batch, list_namespaces=AsyncMock(return_value=[]))

    await monitor._repair(NS, "shared_template:wip:shared_visit", 1)

    batch._fetch_template_by_value.assert_awaited_once_with("shared_visit", "wip")
    batch.start_batch_sync.assert_awaited_once_with("shared_visit", namespace=NS, template=tpl)


# --- entity references --------------------------------------------------------


def _answers(statuses: dict[str, int]):
    """A transport answering each path suffix with its status; the document
    references one template and one term."""
    def handle(request: httpx.Request) -> httpx.Response:
        for suffix, status in statuses.items():
            if request.url.path.endswith(suffix):
                body = {"document_id": "d1", "namespace": NS, "template_id": "t1",
                        "term_references": [{"field_path": "c", "term_id": "term1"}]}
                return httpx.Response(status, json=body if status == 200 else {})
        return httpx.Response(500)
    return handle


@pytest.mark.asyncio
async def test_a_store_that_does_not_answer_is_an_error_not_a_broken_reference(monkeypatch):
    import reporting_sync.search_service as ss

    real = httpx.AsyncClient
    monkeypatch.setattr(ss.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.MockTransport(_answers({"/documents/d1": 200, "/templates/t1": 200, "/terms/term1": 503})),
        **k))
    with pytest.raises(UpstreamError):
        await SearchService().get_entity_references("document", "d1")


@pytest.mark.asyncio
async def test_a_reference_the_store_does_not_know_is_broken(monkeypatch):
    import reporting_sync.search_service as ss

    real = httpx.AsyncClient
    monkeypatch.setattr(ss.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.MockTransport(_answers({"/documents/d1": 200, "/templates/t1": 200, "/terms/term1": 404})),
        **k))
    result = await SearchService().get_entity_references("document", "d1")
    term = next(r for r in result.entity.references if r.ref_type == "term")
    assert term.status == "broken" and result.entity.broken_refs == 1


@pytest.mark.asyncio
async def test_the_references_route_answers_503_when_a_store_does_not_answer(monkeypatch):
    service = MagicMock()
    service.get_entity_references = AsyncMock(side_effect=UpstreamError("def-store answered HTTP 503"))
    monkeypatch.setattr(state, "search_service", service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-API-Key": os.environ["WIP_AUTH_LEGACY_API_KEY"]}) as client:
        resp = await client.get("/api/reporting-sync/entity/document/d1/references")
    assert resp.status_code == 503


# --- recent activity ----------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_names_the_sources_it_could_not_read():
    svc = SearchService()
    svc._get_recent_terminologies = AsyncMock(side_effect=UpstreamError("def-store answered HTTP 503"))
    svc._get_recent_templates = AsyncMock(side_effect=httpx.ConnectError("refused"))
    svc._get_recent_documents = AsyncMock(return_value=[])
    result = await svc.get_recent_activity(types=["terminology", "template", "document"])
    assert result.incomplete_sources == ["terminology", "template"]
    assert result.activities == []


@pytest.mark.asyncio
async def test_a_reader_answering_an_error_raises_instead_of_returning_nothing(monkeypatch):
    import reporting_sync.search_service as ss

    real = httpx.AsyncClient
    monkeypatch.setattr(ss.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.MockTransport(lambda r: httpx.Response(503)), **k))
    with pytest.raises(UpstreamError):
        await SearchService()._get_recent_templates(10)


# --- batch sync -----------------------------------------------------------------


def test_rows_written_counts_what_the_command_tag_reports():
    from reporting_sync.batch_sync import _rows_in_tag

    assert _rows_in_tag("INSERT 0 1") == 1
    assert _rows_in_tag("INSERT 0 0") == 0  # the write-counter WHERE kept the row
    assert _rows_in_tag("garbage") == 0


@pytest.mark.asyncio
async def test_a_force_run_with_nothing_to_rebuild_drops_nothing():
    from reporting_sync.batch_sync import BatchSyncService, _BatchRun
    from reporting_sync.models import BatchSyncJob, BatchSyncStatus, ReportingConfig

    svc = BatchSyncService(MagicMock())
    svc.schema_manager.drop_relations_for_template = AsyncMock(return_value=["x"])
    svc._fetch_latest_active_version = AsyncMock(return_value=None)
    job = BatchSyncJob(job_id="j", template_value="PATIENT", namespace=NS, status=BatchSyncStatus.RUNNING)
    run = _BatchRun(job=job, template={"namespace": NS, "status": "inactive"},
                    config=ReportingConfig(), latest_version=1, strategy="latest_only")

    assert await svc._prepare_template_tables(run, force=True, total=0) is None
    svc.schema_manager.drop_relations_for_template.assert_not_awaited()
    assert "nothing dropped" in job.error_message

    # With documents to rebuild from, the drop still runs.
    run.job.error_message = None
    await svc._prepare_template_tables(run, force=True, total=3)
    svc.schema_manager.drop_relations_for_template.assert_awaited_once()


# --- alerts -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_manual_alert_check_leaves_the_error_rate_window_alone():
    m = MetricsCollector()
    m.events_processed, m.events_failed = 10, 2
    await m.check_alerts(None, True, True, advance_window=False)
    assert m._rate_window == (0, 0)
    await m.check_alerts(None, True, True)
    assert m._rate_window == (10, 2)


# --- dotted field names ---------------------------------------------------------


def test_a_dotted_field_name_gets_the_column_its_rows_carry():
    [field] = SchemaManager.parse_template_fields([{"name": "patient.id", "type": "string"}])
    assert field.name == "patient_id"


@requires_postgres
@pytest.mark.asyncio
async def test_the_catalog_says_which_tables_a_view_reads(pg_pool):
    from reporting_sync.main import _tables_a_view_reads

    schema = "rs1263_views"
    async with pg_pool.acquire() as conn:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        for v in (1, 2):
            await conn.execute(f'CREATE TABLE "{schema}"."doc_x__v{v}" (document_id text)')
        await conn.execute(f'CREATE VIEW "{schema}"."doc_x" AS SELECT document_id FROM "{schema}"."doc_x__v2"')
        try:
            assert await _tables_a_view_reads(conn, schema, "doc_x") == {"doc_x__v2"}
        finally:
            await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
