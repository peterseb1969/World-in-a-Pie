"""The parity answer carries one verdict, computed where the tables live.

A restore must never stop on reporting, but it still has to say what reporting
looks like afterwards. The verdict is reporting-sync's to compute — it owns the
tables and knows its own jobs — so the parity answer now carries
``verdict`` (verified / failed / unverified) and ``reasons``, and a caller
records them instead of re-deriving a judgement from the raw counters.

The bug this closes: an unreachable template-store left the namespace with no
rows to check and ``structural_issues`` at 0, which a caller reading the
counters took for "clean". "Nobody looked" and "nothing is missing" are
different answers.
"""

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.models import BatchSyncJob, BatchSyncStatus
from reporting_sync.parity import (
    FAILED,
    UNVERIFIED,
    VERIFIED,
    DerivedTableParity,
    NamespaceParityResult,
    ParityReason,
    TemplateParity,
    ViewParity,
    _set_verdict,
    check_namespace_parity,
)

from .conftest import requires_postgres

NS = "verdictns"

TEMPLATE = {
    "id": "tpl-1",
    "value": "VERDICT_T",
    "usage": "entity",
    "version": 1,
    "fields": [{"name": "title", "type": "string"}],
    "reporting": {},
}


def _result(**overrides: object) -> NamespaceParityResult:
    """A result with a schema and one fully built, fully counted template row —
    the shape of a namespace whose sync landed, one field off."""
    base: dict = {
        "namespace": NS,
        "schema_name": NS,
        "schema_present": True,
        "table_count": 1,
        "templates": [TemplateParity(
            template_value="T1", table_present=True, version_tables=[1],
            view_present=True, bookkeeping_row=True, counts_match=True,
            expected_documents=0, actual_rows=0,
        )],
    }
    base.update(overrides)
    return NamespaceParityResult(**base)


def _verdict(result: NamespaceParityResult, **kwargs: bool) -> tuple[str, list[ParityReason]]:
    kwargs.setdefault("sync_running", False)
    verdict = _set_verdict(result, **kwargs)
    return verdict.verdict, verdict.reasons


class TestVerdictRules:
    def test_everything_checked_and_clean_is_verified(self):
        verdict, reasons = _verdict(_result())
        assert verdict == VERIFIED
        assert reasons == []

    def test_a_missing_table_is_failed_and_names_it(self):
        verdict, reasons = _verdict(_result(templates=[
            TemplateParity(template_value="T1", table_present=False),
        ]))
        assert verdict == FAILED
        assert reasons[0].kind == "template"
        assert reasons[0].name == "T1"
        assert reasons[0].issue == "table_missing"
        assert "T1" in reasons[0].detail

    def test_a_count_gap_is_failed_with_its_numbers(self):
        verdict, reasons = _verdict(_result(templates=[TemplateParity(
            template_value="T1", table_present=True, counts_match=False,
            expected_documents=10, actual_rows=3,
        )]))
        assert verdict == FAILED
        assert reasons[0].issue == "count_mismatch"
        assert (reasons[0].expected, reasons[0].actual) == (10, 3)

    def test_every_gap_of_a_template_is_listed(self):
        """Shape and rows are separate findings; one does not hide the other."""
        _, reasons = _verdict(_result(templates=[TemplateParity(
            template_value="T1", table_present=True, missing_columns=["extra"],
            legacy_table=True, counts_match=False, expected_documents=2, actual_rows=1,
        )]))
        assert [r.issue for r in reasons] == ["columns_missing", "legacy_table", "count_mismatch"]

    def test_a_derived_gap_is_failed(self):
        verdict, reasons = _verdict(_result(derived_tables=[
            DerivedTableParity(table="term_relations", issue="term_relations_mismatch",
                              expected_rows=7, actual_rows=0),
        ]))
        assert verdict == FAILED
        assert reasons[0].kind == "table"
        assert (reasons[0].expected, reasons[0].actual) == (7, 0)

    def test_a_gone_view_is_failed_with_its_remedy(self):
        verdict, reasons = _verdict(_result(views=[ViewParity(view_name="v_open", present=False)]))
        assert verdict == FAILED
        assert reasons[0].kind == "view"
        assert "re-register" in reasons[0].detail

    def test_a_versioned_template_without_its_entity_view_is_failed(self):
        verdict, reasons = _verdict(_result(templates=[TemplateParity(
            template_value="T1", table_present=True, version_tables=[1],
            view_present=False, bookkeeping_row=True,
        )]))
        assert verdict == FAILED
        assert reasons[0].issue == "entity_view_missing"

    def test_a_versioned_template_without_its_bookkeeping_row_is_failed(self):
        verdict, reasons = _verdict(_result(templates=[TemplateParity(
            template_value="T1", table_present=True, version_tables=[1],
            view_present=True, bookkeeping_row=False,
        )]))
        assert verdict == FAILED
        assert reasons[0].issue == "bookkeeping_row_missing"

    def test_no_bookkeeping_row_is_blamed_when_the_bookkeeping_is_unusable(self):
        """The unusable table is the finding; every row merely looks unrecorded."""
        verdict, reasons = _verdict(_result(
            bookkeeping_tables_ok=False, bookkeeping_error="predates namespace keying",
            templates=[TemplateParity(
                template_value="T1", table_present=True, version_tables=[1], view_present=True,
            )],
        ))
        assert verdict == UNVERIFIED
        assert [r.issue for r in reasons] == ["bookkeeping_unusable"]

    def test_a_template_of_another_namespace_is_named_by_its_owner(self):
        _, reasons = _verdict(_result(templates=[TemplateParity(
            template_value="SHARED", template_namespace="other", version_tables=[],
        )]))
        assert reasons[0].issue == "table_missing"
        assert "other:SHARED" in reasons[0].detail


class TestUnverified:
    """Nothing could be settled — which is not the same as nothing is wrong."""

    def test_unreadable_templates_are_not_read_as_clean(self):
        """The incident class: the listing failed, so no row was checked, and
        the counters were all zero."""
        result = _result(templates=[])
        result.bookkeeping_error = " template-store unavailable: boom"
        verdict, reasons = _verdict(result, templates_unreadable=True)
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "templates_unreadable"
        assert "template-store unavailable" in reasons[0].detail

    def test_usable_bookkeeping_with_nothing_to_check_is_unverified(self):
        verdict, reasons = _verdict(_result(templates=[], views=[]))
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "nothing_checked"

    def test_a_namespace_still_syncing_is_unverified_although_it_looks_clean(self):
        verdict, reasons = _verdict(_result(), sync_running=True)
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "sync_running"

    def test_a_clean_count_check_is_verified_while_a_sync_is_still_running(self):
        # A merge into a namespace already at parity triggers the batch sync,
        # which registers its jobs at once: the tables already match, so the
        # running sync can only write what is there (forward review of
        # 864cc532, R1). Recorded as unverified, every such merge read as a
        # reporting problem.
        verdict, reasons = _verdict(_result(), sync_running=True, counts_checked=True)
        assert verdict == VERIFIED
        assert reasons == []

    def test_a_count_gap_while_a_sync_is_still_running_stays_open(self):
        # A gap the running sync may still close is not a verdict yet.
        verdict, reasons = _verdict(_result(templates=[TemplateParity(
            template_value="T1", table_present=True, version_tables=[1],
            view_present=True, bookkeeping_row=True, counts_match=False,
            expected_documents=10, actual_rows=3,
        )]), sync_running=True, counts_checked=True)
        assert verdict == UNVERIFIED
        assert [r.issue for r in reasons] == ["sync_running", "count_mismatch"]

    def test_a_structure_only_check_does_not_settle_a_running_sync(self):
        # Without counts nothing says the rows have landed.
        verdict, reasons = _verdict(_result(), sync_running=True, counts_checked=False)
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "sync_running"

    def test_unusable_bookkeeping_is_unverified_not_a_proven_gap(self):
        result = _result(bookkeeping_tables_ok=False, bookkeeping_error="predates namespace keying")
        verdict, reasons = _verdict(result)
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "bookkeeping_unusable"
        assert "predates namespace keying" in reasons[0].detail

    def test_a_check_that_could_not_run_is_unverified(self):
        verdict, reasons = _verdict(_result(derived_tables=[
            DerivedTableParity(table="terms", issue="check_failed", error="def-store down"),
        ]))
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "check_failed"
        assert "def-store down" in reasons[0].detail

    def test_a_template_whose_row_errored_is_unverified(self):
        verdict, reasons = _verdict(_result(templates=[
            TemplateParity(template_value="T1", table_present=True),
            TemplateParity(template_value="T2", error="document-store down"),
        ]))
        assert verdict == UNVERIFIED
        assert [r.issue for r in reasons] == ["check_failed"]
        assert reasons[0].name == "T2"

    def test_a_shared_sweep_that_did_not_run_is_unverified(self):
        """The namespace's own rows came back complete and the counters are
        zero, because the shared templates were never listed at all — which is
        not the same as the namespace using none."""
        result = _result()
        result.bookkeeping_error = (
            " shared templates not checked (document-store unavailable: 503)"
        )
        verdict, reasons = _verdict(result, shared_sweep_error="503 Service Unavailable")
        assert verdict == UNVERIFIED
        assert reasons[0].issue == "shared_templates_unchecked"
        assert reasons[0].name == NS
        assert "shared templates" in reasons[0].detail
        assert "503" in reasons[0].detail

    def test_a_shared_sweep_that_ran_leaves_a_clean_namespace_verified(self):
        verdict, reasons = _verdict(_result(), shared_sweep_error=None)
        assert (verdict, reasons) == (VERIFIED, [])


class TestFailedWinsOverUnverified:
    def test_a_proven_gap_is_reported_even_when_another_check_could_not_run(self):
        """Hiding a proven gap behind "could not tell" would lose the one thing
        the operator can act on; both are listed."""
        verdict, reasons = _verdict(_result(
            templates=[TemplateParity(template_value="T1", table_present=False)],
            derived_tables=[
                DerivedTableParity(table="terms", issue="check_failed", error="def-store down"),
            ],
        ))
        assert verdict == FAILED
        assert [r.issue for r in reasons] == ["table_missing", "check_failed"]

    def test_a_gap_seen_while_the_sync_is_still_running_is_not_a_verdict(self):
        """Rows land page by page: while a sync is in flight every gap is
        provisional, so the answer is "not settled yet" with the gap listed."""
        verdict, reasons = _verdict(_result(
            templates=[TemplateParity(template_value="T1", table_present=False)],
        ), sync_running=True)
        assert verdict == UNVERIFIED
        assert [r.issue for r in reasons] == ["sync_running", "table_missing"]


# ---------------------------------------------------------------------------
# Wiring: the verdict comes out of the check itself
# ---------------------------------------------------------------------------


def _patch_http(templates: list[dict] | Exception, doc_total: int = 0) -> ExitStack:
    """Patch parity's sources: the template listing (which may fail), the
    shared-template sweep, and the document/term totals."""
    listing = (
        AsyncMock(side_effect=templates)
        if isinstance(templates, Exception) else AsyncMock(return_value=templates)
    )
    stack = ExitStack()
    for target, value in (
        ("_fetch_namespace_templates", listing),
        ("_fetch_foreign_templates", ([], [])),
        ("_expected_document_total", doc_total),
        ("_expected_active_terms", 0),
        ("_expected_active_relations", 0),
    ):
        stack.enter_context(
            patch(f"reporting_sync.parity.{target}", new=AsyncMock(return_value=value))
        )
    stack.enter_context(patch("reporting_sync.parity._fetch_namespace_templates", new=listing))
    return stack


@requires_postgres
@pytest.mark.asyncio
async def test_a_namespace_whose_tables_are_built_verifies(pg_pool):
    from reporting_sync.main import init_postgres_schema
    from reporting_sync.schema_manager import SchemaManager

    await init_postgres_schema(pg_pool)
    with _patch_http([TEMPLATE], doc_total=0):
        await SchemaManager(pg_pool).ensure_table_for_template(NS, TEMPLATE)
        result = await check_namespace_parity(pg_pool, NS, include_counts=True)
    assert result.ok is True
    assert (result.verdict, result.reasons) == (VERIFIED, [])


@requires_postgres
@pytest.mark.asyncio
async def test_a_missing_table_is_a_failed_verdict_on_the_answer(pg_pool):
    """The tables were never built: the verdict names the template rather than
    leaving the caller to read structural_issues."""
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    with _patch_http([TEMPLATE], doc_total=0):
        result = await check_namespace_parity(pg_pool, NS, include_counts=True)
    assert result.verdict == FAILED
    assert [(r.name, r.issue) for r in result.reasons if r.kind == "template"] == [
        ("VERDICT_T", "table_missing")
    ]


@requires_postgres
@pytest.mark.asyncio
async def test_an_unreachable_template_store_is_unverified(pg_pool):
    """Not failed (nothing was proved), and not verified (nothing was seen)."""
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    with _patch_http(httpx.ConnectError("template-store down")):
        result = await check_namespace_parity(pg_pool, NS, include_counts=True)
    assert result.ok is False
    assert result.structural_issues == 0
    assert result.verdict == UNVERIFIED
    assert [r.issue for r in result.reasons] == ["templates_unreadable"]


@requires_postgres
@pytest.mark.asyncio
async def test_a_sync_still_running_holds_the_verdict_open(pg_pool):
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    with _patch_http([TEMPLATE], doc_total=0):
        result = await check_namespace_parity(
            pg_pool, NS, include_counts=True, sync_running=True,
        )
    assert result.verdict == UNVERIFIED
    assert "sync_running" in [r.issue for r in result.reasons]


@requires_postgres
@pytest.mark.asyncio
async def test_a_shared_sweep_that_never_ran_is_not_a_verified_verdict(pg_pool):
    """The namespace's own table is built and its rows complete; only the
    facet listing of the shared templates fails. Every counter reads clean, so
    only the verdict can say that part was never looked at."""
    from reporting_sync.main import init_postgres_schema
    from reporting_sync.schema_manager import SchemaManager

    await init_postgres_schema(pg_pool)
    refused = httpx.HTTPStatusError(
        "503", request=httpx.Request("GET", "http://x"), response=httpx.Response(503),
    )
    with _patch_http([TEMPLATE], doc_total=0) as stack:
        await SchemaManager(pg_pool).ensure_table_for_template(NS, TEMPLATE)
        stack.enter_context(patch(
            "reporting_sync.parity._fetch_foreign_templates",
            new=AsyncMock(side_effect=refused),
        ))
        result = await check_namespace_parity(pg_pool, NS, include_counts=True)
    assert [t.template_value for t in result.templates] == ["VERDICT_T"]
    assert result.structural_issues == 0 and result.count_mismatches == 0
    assert result.ok is False
    assert result.verdict == UNVERIFIED
    assert [r.issue for r in result.reasons] == ["shared_templates_unchecked"]


# ---------------------------------------------------------------------------
# Who says the sync is running: the service that runs it
# ---------------------------------------------------------------------------


def _service(monkeypatch: pytest.MonkeyPatch) -> BatchSyncService:
    """A service with no pool, installed as the app's, so the endpoint helper
    and the in-flight bookkeeping can be exercised without a sync."""
    from reporting_sync.main import state

    service = BatchSyncService(None)  # type: ignore[arg-type] — no I/O here
    monkeypatch.setattr(state, "batch_sync_service", service, raising=False)
    return service


def _job(namespace: str | None, status: BatchSyncStatus) -> BatchSyncJob:
    return BatchSyncJob(
        job_id=f"job-{status}-{namespace}", template_value="T", namespace=namespace, status=status,
    )


class TestNamespaceSyncRunning:
    def test_a_running_job_of_the_namespace_counts(self, monkeypatch):
        service = _service(monkeypatch)
        service._jobs["a"] = _job(NS, BatchSyncStatus.RUNNING)
        assert service.is_namespace_sync_running(NS) is True

    def test_a_pending_job_counts_too(self, monkeypatch):
        service = _service(monkeypatch)
        service._jobs["a"] = _job(NS, BatchSyncStatus.PENDING)
        assert service.is_namespace_sync_running(NS) is True

    def test_a_finished_job_does_not(self, monkeypatch):
        service = _service(monkeypatch)
        service._jobs["a"] = _job(NS, BatchSyncStatus.COMPLETED)
        service._jobs["b"] = _job(NS, BatchSyncStatus.FAILED)
        assert service.is_namespace_sync_running(NS) is False

    def test_another_namespaces_job_does_not(self, monkeypatch):
        service = _service(monkeypatch)
        service._jobs["a"] = _job("somewhere-else", BatchSyncStatus.RUNNING)
        assert service.is_namespace_sync_running(NS) is False

    def test_an_instance_wide_job_covers_every_namespace(self, monkeypatch):
        service = _service(monkeypatch)
        service._jobs["a"] = _job(None, BatchSyncStatus.RUNNING)
        assert service.is_namespace_sync_running(NS) is True

    def test_a_definitions_sweep_counts_while_it_runs(self, monkeypatch):
        """The sweep writes the term tables the parity check reads, and it is
        not a job record — so it is tracked on its own."""
        service = _service(monkeypatch)
        seen: list[bool] = []

        async def fake_sweep(**_kwargs: object) -> dict:
            seen.append(service.is_namespace_sync_running(NS))
            return {"synced": 0, "failed": 0, "complete": True}

        monkeypatch.setattr(service, "_sweep_terms", fake_sweep)
        asyncio.run(service.batch_sync_terms(namespace=NS))
        assert seen == [True]
        assert service.is_namespace_sync_running(NS) is False

    def test_a_failed_sweep_still_clears_the_marker(self, monkeypatch):
        service = _service(monkeypatch)

        async def boom(**_kwargs: object) -> dict:
            raise RuntimeError("boom")

        monkeypatch.setattr(service, "_sweep_terms", boom)
        with pytest.raises(RuntimeError):
            asyncio.run(service.batch_sync_terms(namespace=NS))
        assert service.is_namespace_sync_running(NS) is False

    def test_the_endpoint_asks_the_service(self, monkeypatch):
        from reporting_sync.main import _namespace_sync_running

        service = _service(monkeypatch)
        service._jobs["a"] = _job(NS, BatchSyncStatus.RUNNING)
        assert _namespace_sync_running(NS) is True
        assert _namespace_sync_running("other-namespace") is False
