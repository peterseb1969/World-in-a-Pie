"""How the integrity scan selects, batches, limits and grades, pinned before
check_all_documents' refactor.

Provenance: CASE-1161. The status and template filters, recent_first, the
progress callback on full batches, the limit stopping the scan, and the
warning-only verdict had no test. Documents are written to the test MongoDB
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
