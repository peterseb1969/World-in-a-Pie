"""A parity answer cannot claim what it did not look at.

``?include_counts=false`` compares table shape and never counts a row or reads a
term table, yet the answer used to read plain ``verified`` with ``reasons: []``
— against the route's own definition of ``verified`` ("every check ran and found
nothing missing"), and for a namespace whose documents had never synced at all.
A deliberate limit is not an outage, so the verdict stays what it is; what it
covers now rides on the answer as ``counts_included``, and the wording around
``verified`` is scoped by it.

The surfaces the check skips on its own are held to the same standard. An
all_versions template's rows are never compared against its documents, so an
all-clear that leans on one cannot settle a sync still in flight. And
``terminologies`` is compared like the other def-store tables: the vocabulary the
terms live in was the one thing nothing here looked at, so a namespace whose
terminology sweep never completed read as verified.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from reporting_sync.parity import (
    FAILED,
    UNVERIFIED,
    VERIFIED,
    NamespaceParityResult,
    TemplateParity,
    _set_verdict,
    check_namespace_parity,
)

from .conftest import requires_postgres

NS = "structonlyns"

TEMPLATE = {
    "id": "tpl-cheap",
    "value": "CHEAP_T",
    "usage": "entity",
    "version": 1,
    "fields": [{"name": "title", "type": "string"}],
    "reporting": {},
}


def _patch_http(doc_total: int, terminologies: int = 0) -> ExitStack:
    """Parity's sources: one sync-enabled template, no shared ones,
    ``doc_total`` live documents waiting in MongoDB and ``terminologies``
    vocabularies in def-store."""
    stack = ExitStack()
    for target, value in (
        ("_fetch_namespace_templates", [TEMPLATE]),
        ("_fetch_foreign_templates", ([], [])),
        ("_expected_document_total", doc_total),
        ("_expected_active_terminologies", terminologies),
        ("_expected_active_terms", 0),
        ("_expected_active_relations", 0),
    ):
        stack.enter_context(
            patch(f"reporting_sync.parity.{target}", new=AsyncMock(return_value=value))
        )
    return stack


def _row(**overrides: object) -> TemplateParity:
    """A template row whose every surface is there and matches, one field off."""
    base: dict = {
        "template_value": "T1", "table_present": True, "version_tables": [1],
        "view_present": True, "bookkeeping_row": True,
        "counts_match": True, "expected_documents": 0, "actual_rows": 0,
    }
    base.update(overrides)
    return TemplateParity(**base)


def _verdict(row: TemplateParity, **kwargs: bool) -> tuple[str, list[str]]:
    result = NamespaceParityResult(
        namespace=NS, schema_name=NS, schema_present=True, table_count=1,
        templates=[row],
    )
    kwargs.setdefault("sync_running", False)
    kwargs.setdefault("counts_checked", True)
    verdict = _set_verdict(result, **kwargs)
    return verdict.verdict, [r.issue for r in verdict.reasons]


@requires_postgres
@pytest.mark.asyncio
async def test_the_cheap_form_verifies_structure_and_says_it_counted_nothing(pg_pool):
    """The table is built and empty while four documents wait. Structure-only
    may call that clean; it may not imply the rows were compared."""
    from reporting_sync.main import init_postgres_schema
    from reporting_sync.schema_manager import SchemaManager

    await init_postgres_schema(pg_pool)
    with _patch_http(doc_total=4):
        await SchemaManager(pg_pool).ensure_table_for_template(NS, TEMPLATE)
        cheap = await check_namespace_parity(pg_pool, NS, include_counts=False)
        counted = await check_namespace_parity(pg_pool, NS, include_counts=True)

    assert (cheap.verdict, cheap.reasons) == (VERIFIED, [])
    assert cheap.counts_included is False
    assert cheap.count_mismatches == 0
    assert cheap.derived_tables == []
    assert cheap.templates[0].counts_match is None

    assert counted.counts_included is True
    assert counted.verdict == FAILED
    assert [r.issue for r in counted.reasons] == ["count_mismatch"]


@requires_postgres
@pytest.mark.asyncio
async def test_the_counting_form_says_it_counted(pg_pool):
    """The default form's ``verified`` does cover the rows, and the answer says
    so — the two forms are distinguishable on the response, not by memory."""
    from reporting_sync.main import init_postgres_schema
    from reporting_sync.schema_manager import SchemaManager

    await init_postgres_schema(pg_pool)
    with _patch_http(doc_total=0):
        await SchemaManager(pg_pool).ensure_table_for_template(NS, TEMPLATE)
        result = await check_namespace_parity(pg_pool, NS)

    assert result.verdict == VERIFIED
    assert result.counts_included is True
    assert any(d.table == "terminologies" for d in result.derived_tables), (
        "the vocabulary table is one of the surfaces this form looked at"
    )
    assert [d.issue for d in result.derived_tables if d.issue] == []


@requires_postgres
@pytest.mark.asyncio
async def test_a_short_circuited_check_still_names_its_scope(pg_pool):
    """The limit is recorded before any source is asked, so it is on the answer
    even when the check returns early."""
    from reporting_sync.main import init_postgres_schema

    await init_postgres_schema(pg_pool)
    refused = httpx.ConnectError("template-store is not answering")
    with ExitStack() as stack:
        stack.enter_context(patch(
            "reporting_sync.parity._fetch_namespace_templates",
            new=AsyncMock(side_effect=refused),
        ))
        result = await check_namespace_parity(pg_pool, NS, include_counts=False)

    assert result.verdict == UNVERIFIED
    assert result.counts_included is False


def test_the_served_contract_names_the_limit():
    """The field and the sentence explaining ``verified`` ship together: an
    answer cannot start claiming complete checks without saying what ran."""
    from reporting_sync.main import app

    spec = app.openapi()
    props = spec["components"]["schemas"]["NamespaceParityResult"]["properties"]
    assert props["counts_included"]["type"] == "boolean"
    route = spec["paths"]["/api/reporting-sync/parity"]["get"]
    assert "counts_included" in route["description"]
    assert "terminologies" in route["description"]
    param = next(p for p in route["parameters"] if p["name"] == "include_counts")
    assert "counts_included=false" in param["description"]


class TestWhatTheCheckSettles:
    """``verified`` may close a running sync only over what it compared."""

    def test_a_template_whose_rows_cannot_be_counted_leaves_the_sync_open(self):
        """The all-clear is real for the surfaces that ran, but this template's
        rows were never compared against its documents, so nothing says the
        sync in flight has written what it still has to write."""
        verdict, reasons = _verdict(
            _row(counts_match=None, counts_comparable=False,
                 expected_documents=None, actual_rows=None),
            sync_running=True,
        )
        assert verdict == UNVERIFIED
        assert reasons == ["sync_running"]

    def test_a_countable_all_clear_still_settles_the_running_sync(self):
        """CASE-1328's rule stands where the counts ran: a namespace already at
        parity is verified even while its sync rewrites identical rows."""
        assert _verdict(_row(), sync_running=True) == (VERIFIED, [])

    def test_an_uncountable_template_with_nothing_in_flight_is_verified(self):
        """What is unknown here is the count, not a gap: nothing proved missing
        and no sync left to wait for, so the verdict is still clean."""
        assert _verdict(
            _row(counts_match=None, counts_comparable=False,
                 expected_documents=None, actual_rows=None),
            sync_running=False,
        ) == (VERIFIED, [])


@requires_postgres
@pytest.mark.asyncio
async def test_the_terminology_table_is_compared_like_the_term_tables(pg_pool):
    """Three vocabularies live in def-store for this namespace and none of them
    reached reporting: the terminology sweep never ran, or stopped before its
    first page wrote. The term tables say nothing about that — they are filled by
    a different sweep, from a listing this one builds on."""
    from reporting_sync.main import init_postgres_schema
    from reporting_sync.schema_manager import SchemaManager

    await init_postgres_schema(pg_pool)
    await SchemaManager(pg_pool).ensure_table_for_template(NS, TEMPLATE)
    with _patch_http(doc_total=0, terminologies=3):
        result = await check_namespace_parity(pg_pool, NS)

    assert result.verdict == FAILED
    row = next(d for d in result.derived_tables if d.table == "terminologies")
    assert (row.issue, row.expected_rows, row.actual_rows) == ("terminologies_mismatch", 3, 0)
    assert [r.issue for r in result.reasons] == ["terminologies_mismatch"]
    assert result.derived_issues == 1
