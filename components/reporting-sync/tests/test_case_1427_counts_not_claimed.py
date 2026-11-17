"""A structure-only parity answer cannot claim the rows.

``?include_counts=false`` compares table shape and never counts a row or reads a
term table, yet the answer used to read plain ``verified`` with ``reasons: []``
— against the route's own definition of ``verified`` ("every check ran and found
nothing missing"), and for a namespace whose documents had never synced at all.
A deliberate limit is not an outage, so the verdict stays what it is; what it
covers now rides on the answer as ``counts_included``, and the wording around
``verified`` is scoped by it.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from reporting_sync.parity import (
    FAILED,
    UNVERIFIED,
    VERIFIED,
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


def _patch_http(doc_total: int) -> ExitStack:
    """Parity's sources: one sync-enabled template, no shared ones, and
    ``doc_total`` live documents waiting in MongoDB."""
    stack = ExitStack()
    for target, value in (
        ("_fetch_namespace_templates", [TEMPLATE]),
        ("_fetch_foreign_templates", ([], [])),
        ("_expected_document_total", doc_total),
        ("_expected_active_terms", 0),
        ("_expected_active_relations", 0),
    ):
        stack.enter_context(
            patch(f"reporting_sync.parity.{target}", new=AsyncMock(return_value=value))
        )
    return stack


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
    param = next(p for p in route["parameters"] if p["name"] == "include_counts")
    assert "counts_included=false" in param["description"]
