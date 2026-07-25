"""CASE-810 — search must speak logical types, not physical table names.

Reporting tables are per template version (``doc_<value>__v<N>``); the bare
name is an entity view. Search has to read the physical tables because the
tsvector columns FTS matches on exist only there. Two consequences leaked to
callers before this fix:

- ``template=CASE_RECORD`` built the exact name ``doc_case_record`` and matched
  no BASE TABLE post-split, so a type-filtered search returned zero hits —
  silently, indistinguishable from "nothing matches";
- results reported their type as ``CASE_RECORD__V1``, a string that changes on
  every template version event, so no caller could filter on it stably.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from reporting_sync.search_service import SearchService, entity_base

from .test_search_endpoint import _mock_pool_with_conn, _RecordingConn


class TestEntityBase:
    def test_strips_prefix_and_version(self):
        assert entity_base("doc_case_record__v2") == "case_record"

    def test_strips_prefix_when_unversioned(self):
        assert entity_base("doc_case_record") == "case_record"

    def test_is_idempotent_on_a_bare_logical_name(self):
        # The filter path feeds a caller-supplied template value through the
        # same helper, so it must be a no-op on an already-logical name.
        assert entity_base("case_record") == "case_record"

    def test_only_a_trailing_version_is_stripped(self):
        # __v appears mid-name in a legitimate value; only the suffix goes.
        assert entity_base("doc_doc__v2_notes") == "doc__v2_notes"

    def test_multi_digit_versions(self):
        assert entity_base("doc_case_record__v10") == "case_record"

    def test_underscored_values_survive(self):
        assert entity_base("doc_git_stats_snapshot__v1") == "git_stats_snapshot"


class TestTemplateFilterDrivesTheRealSearch:
    """These drive `_search_documents` itself.

    An earlier draft of this file asserted against a filter reimplemented in
    the test, which passed with the production fix reverted — a tautology that
    proved nothing. Everything here goes through the service.

    Known limit of the double: `_RecordingConn` returns `conn.tables` verbatim
    and does not apply the discovery query's WHERE clause. So the tests that
    only assert "the requested table was searched" hold with or without the
    fix — the load-bearing ones are those asserting **exclusion** and the
    **reported type**, which is where the reverted-fix run actually fails.
    """

    _COLS: ClassVar[list[tuple[str, str]]] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]

    def _svc(self, tables: list[str], rows: list[dict] | None = None):
        conn = _RecordingConn()
        conn.tables = tables
        for t in tables:
            conn.columns[t] = self._COLS
        conn.rows_per_query = [list(rows or [])] * max(len(tables), 1)
        return conn, SearchService(_mock_pool_with_conn(conn))

    def _searched(self, conn) -> set[str]:
        return {
            p[1] for s, p in conn.executed_sql
            if "information_schema.columns" in s
        }

    @pytest.mark.asyncio
    async def test_filter_covers_every_version_and_excludes_others(self):
        conn, svc = self._svc(
            ["doc_lesson__v1", "doc_lesson__v2", "doc_session__v1"]
        )
        await svc._search_documents("q", None, None, template="LESSON")
        assert self._searched(conn) == {"doc_lesson__v1", "doc_lesson__v2"}

    @pytest.mark.asyncio
    async def test_bare_logical_name_reaches_a_versioned_table(self):
        # Documents intent: a caller passing the logical name must reach the
        # per-version table. Against the real database, pre-fix this matched
        # no BASE TABLE and returned zero hits; the double cannot reproduce
        # that (see class docstring), so this does not fail on the old code.
        conn, svc = self._svc(["doc_lesson__v1"])
        await svc._search_documents("q", None, None, template="LESSON")
        assert self._searched(conn) == {"doc_lesson__v1"}

    @pytest.mark.asyncio
    async def test_suffixed_form_still_works_for_existing_callers(self):
        # The retracted workaround told callers to pass LESSON__V1.
        conn, svc = self._svc(["doc_lesson__v1", "doc_lesson__v2"])
        await svc._search_documents("q", None, None, template="LESSON__V1")
        assert self._searched(conn) == {"doc_lesson__v1", "doc_lesson__v2"}

    @pytest.mark.asyncio
    async def test_underscore_is_not_a_wildcard(self):
        # A LIKE 'doc_lesson__v%' pattern would also match doc_lessonX__v1,
        # because '_' is a single-character wildcard in LIKE.
        conn, svc = self._svc(["doc_lessonX__v1", "doc_lesson__v1"])
        await svc._search_documents("q", None, None, template="LESSON")
        assert self._searched(conn) == {"doc_lesson__v1"}

    @pytest.mark.asyncio
    async def test_result_reports_the_logical_type_not_the_table(self):
        _conn, svc = self._svc(
            ["doc_lesson__v2"],
            rows=[{"doc_id": "DOC-1", "status": "active", "updated_at": None,
                   "score": 0.5, "snippet": "x"}],
        )
        results = await svc._search_documents("q", None, None)
        assert results, "expected a hit"
        assert results[0].value == "LESSON"
        assert results[0].label == "LESSON document"
        # Provenance is preserved, just not in the type field.
        assert results[0].description == "Matched in doc_lesson__v2"
