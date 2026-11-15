"""How the integrity scan selects, batches, limits and grades, pinned before
check_all_documents' refactor.

Provenance: CASE-1161. The status and template filters, recent_first, the
progress callback on full batches, the limit stopping the scan, and the
warning-only verdict had no test, nor did the mapping from an issue type to
the summary counter it lands on. Documents are written to the test MongoDB
directly; the template store is a stand-in, and BATCH_SIZE is shrunk so a
handful of documents spans several batches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.services import integrity_service

NS = "integ1161"


@pytest_asyncio.fixture
async def docs(session_mongo_client, monkeypatch):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": NS}).delete()
    base = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [
        ("D1", "T-ACTIVE", DocumentStatus.ACTIVE),
        ("D2", "T-ACTIVE", DocumentStatus.ACTIVE),
        ("D3", "T-ACTIVE", DocumentStatus.INACTIVE),
        ("D4", "T-ACTIVE", DocumentStatus.ACTIVE),
        ("D5", "T-RETIRED", DocumentStatus.ACTIVE),  # the newest
    ]
    for i, (doc_id, template, status) in enumerate(rows):
        await Document(
            namespace=NS, document_id=doc_id, template_id=template, template_version=1,
            identity_hash="", version=1, data={}, status=status,
            created_at=base + timedelta(minutes=i),
        ).insert()
    monkeypatch.setattr(integrity_service, "BATCH_SIZE", 2)
    yield
    await Document.find({"namespace": NS}).delete()


def _templates():
    async def get_template(template_id=None, version=None, **_kw):
        return {"status": "inactive" if template_id == "T-RETIRED" else "active"}
    return patch.object(integrity_service, "get_template_store_client",
                        return_value=SimpleNamespace(get_template=get_template))


async def _scan(**kwargs):
    calls: list[tuple[int, int]] = []
    with _templates():
        result = await integrity_service.check_all_documents(
            namespace=NS, check_term_refs=False, check_identity=False,
            progress=lambda checked, total: calls.append((checked, total)), **kwargs,
        )
    return result, calls


@pytest.mark.asyncio
async def test_the_filters_narrow_the_set(docs):
    result, _ = await _scan(status_filter="active")
    assert (result.summary.total_documents, result.summary.documents_checked) == (4, 4)
    result, _ = await _scan(template_id_filter="T-RETIRED")
    assert (result.summary.total_documents, result.summary.documents_checked) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "checked", "progress"), [
    (0, 5, [(2, 5), (4, 5), (5, 5)]),
    (4, 4, [(2, 4), (4, 4)]),   # the limit ends the scan on a batch boundary
    (3, 3, [(2, 3), (3, 3)]),   # the last batch is cut at the limit
])
async def test_batches_report_progress_and_stop_at_the_limit(docs, limit, checked, progress):
    result, calls = await _scan(limit=limit)
    assert result.summary.documents_checked == checked
    assert calls == progress


@pytest.mark.asyncio
async def test_recent_first_checks_the_newest_and_a_retired_template_only_warns(docs):
    result, _ = await _scan(recent_first=True, limit=1)
    assert result.summary.documents_checked == 1
    assert [i.document_id for i in result.issues] == ["D5"]
    assert result.summary.inactive_template_refs == 1
    assert result.status == "warning"
    result, _ = await _scan(template_id_filter="T-ACTIVE")
    assert result.status == "healthy" and result.issues == []


# Every issue type the scan can raise, one each: a type added to
# _SUMMARY_COUNTERS without a line here leaves its counter at 0 and fails.
ISSUE_TYPES = [
    "orphaned_template_ref", "orphaned_term_ref", "inactive_template_ref",
    "orphaned_document_ref", "misplaced_document_ref", "orphaned_file_ref",
    "identity_hash_mismatch", "identity_field_missing",
    "identity_resolution_missing", "template_version_not_found",
    "mandatory_field_missing", "nested_template_not_found",
]

# What the summary counts besides the per-type counters.
_NON_COUNTER_FIELDS = {
    "total_documents", "documents_checked", "documents_with_issues", "checks_not_run",
}


def test_every_issue_type_lands_on_its_own_counter():
    issues = [
        integrity_service.IntegrityIssue(
            type=issue_type, severity="error", document_id=f"D{i % 2 + 1}",
            template_id="T", version=1, reference="r", message="m",
        )
        for i, issue_type in enumerate(ISSUE_TYPES)
    ]
    summary = integrity_service._summarize(issues, 9, 7, {"term_reference:def-store": 1})

    # The mapping covers the issue types and the counters exactly: nothing the
    # scan can raise falls through, and nothing the summary carries is unfed.
    counters = set(integrity_service.IntegritySummary.model_fields) - _NON_COUNTER_FIELDS
    assert set(integrity_service._SUMMARY_COUNTERS) == set(ISSUE_TYPES)
    assert set(integrity_service._SUMMARY_COUNTERS.values()) == counters
    # One issue of each type moves each counter by one, except the three
    # identity types, which identity_unverified gathers.
    assert {name: getattr(summary, name) for name in counters} == {
        name: 3 if name == "identity_unverified" else 1 for name in counters
    }
    # The rest keep their own units: version rows counted, and the distinct
    # documents among issues raised by any of a document's versions.
    assert (summary.total_documents, summary.documents_checked) == (9, 7)
    assert summary.documents_with_issues == 2
    assert summary.checks_not_run == {"term_reference:def-store": 1}
