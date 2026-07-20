"""Namespace integrity verification.

This is where a restore gets checked. The restore engine writes documents with
`insert_many` and validates nothing — deliberately, for speed — so these checks
are the only thing standing between a bad archive (or a bad ID re-mint) and a
namespace that looks fine until something downstream breaks.

Runs against the real test MongoDB with Beanie initialised, since every check
either queries a collection or compares against stored state.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.models.document import Document
from document_store.models.file import File
from document_store.services import integrity_service
from document_store.services.integrity_service import (
    check_all_documents,
    clear_integrity_cache,
)
from wip_auth.document_identity import compute_hash

from .conftest import setup_registry_and_app

NAMESPACE = "integrity-ns"
OTHER_NAMESPACE = "integrity-other-ns"
TEMPLATE_ID = "TPL-1"
IDENTITY_FIELDS = ["patient_id"]


@pytest_asyncio.fixture
async def db(monkeypatch):
    """Beanie-initialised Document + File, with the service clients stubbed.

    Template-store and def-store are separate services; the integrity checks
    reach them through their clients, so the fixture answers as a healthy
    instance would and each test overrides only what it is about.
    """
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await setup_registry_and_app(mongo_client, document_models=[Document, File])
    await Document.delete_all()
    await File.delete_all()
    clear_integrity_cache()

    template_store = AsyncMock()
    template_store.get_template = AsyncMock(
        return_value={"status": "active", "identity_fields": IDENTITY_FIELDS}
    )
    def_store = AsyncMock()
    def_store.get_term = AsyncMock(return_value={"term_id": "T-1"})

    monkeypatch.setattr(
        integrity_service, "get_template_store_client", lambda: template_store
    )
    monkeypatch.setattr(
        integrity_service, "get_def_store_client", lambda: def_store
    )

    yield {"template_store": template_store, "def_store": def_store}

    await Document.delete_all()
    await File.delete_all()
    clear_integrity_cache()


async def _document(
    doc_id="D1",
    *,
    namespace=NAMESPACE,
    data=None,
    identity_hash=None,
    references=None,
    file_references=None,
    term_references=None,
):
    data = data or {"patient_id": "P-1"}
    document = Document(
        document_id=doc_id,
        namespace=namespace,
        template_id=TEMPLATE_ID,
        template_version=1,
        identity_hash=(
            compute_hash({"patient_id": data["patient_id"]})
            if identity_hash is None
            else identity_hash
        ),
        version=1,
        data=data,
        references=references or [],
        file_references=file_references or [],
        term_references=term_references or [],
    )
    await document.insert()
    return document


def _types(result):
    return sorted(issue.type for issue in result.issues)


# ---------------------------------------------------------------------------
# Namespace scoping
# ---------------------------------------------------------------------------


class TestNamespaceScope:
    @pytest.mark.asyncio
    async def test_only_the_named_namespace_is_checked(self, db):
        # A post-restore verification asks about one namespace; issues next
        # door are somebody else's problem and would make the result unusable.
        await _document("D1")
        await _document("D2", namespace=OTHER_NAMESPACE, identity_hash="wrong")

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"
        assert result.summary.documents_checked == 1

    @pytest.mark.asyncio
    async def test_without_a_namespace_everything_is_checked(self, db):
        await _document("D1")
        await _document("D2", namespace=OTHER_NAMESPACE)

        result = await check_all_documents()

        assert result.summary.documents_checked == 2


# ---------------------------------------------------------------------------
# Identity hash verification
# ---------------------------------------------------------------------------


class TestIdentityHash:
    @pytest.mark.asyncio
    async def test_a_matching_hash_is_healthy(self, db):
        await _document("D1")

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_a_hash_that_drifted_from_its_data_is_an_error(self, db):
        # The check nothing else performs. The hash decides whether a write is
        # a new version or a new document, is written once at create time, and
        # a drifted one stays wrong silently.
        await _document("D1", identity_hash="not-the-real-hash")

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "error"
        assert _types(result) == ["identity_hash_mismatch"]
        assert result.summary.identity_hash_mismatches == 1

    @pytest.mark.asyncio
    async def test_a_missing_identity_field_is_reported_not_raised(self, db):
        await _document("D1", data={"other": "value"}, identity_hash="x")

        result = await check_all_documents(namespace=NAMESPACE)

        assert _types(result) == ["identity_field_missing"]

    @pytest.mark.asyncio
    async def test_an_append_only_template_must_carry_an_empty_hash(self, db):
        # A template with no identity_fields is append-only: its documents
        # have no logical identity, so a hash on one means something computed
        # an identity that the template says does not exist.
        db["template_store"].get_template = AsyncMock(
            return_value={"status": "active", "identity_fields": []}
        )
        await _document("D1", identity_hash="should-not-be-here")

        result = await check_all_documents(namespace=NAMESPACE)

        assert _types(result) == ["identity_hash_mismatch"]

    @pytest.mark.asyncio
    async def test_an_append_only_document_with_no_hash_is_healthy(self, db):
        db["template_store"].get_template = AsyncMock(
            return_value={"status": "active", "identity_fields": []}
        )
        await _document("D1", identity_hash="")

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"


# ---------------------------------------------------------------------------
# Reference checks
# ---------------------------------------------------------------------------


class TestReferences:
    @pytest.mark.asyncio
    async def test_a_resolved_document_reference_that_is_gone_is_an_error(self, db):
        await _document("D1", references=[{
            "field_path": "supervisor",
            "reference_type": "document",
            "resolved": {"document_id": "MISSING"},
        }])

        result = await check_all_documents(namespace=NAMESPACE)

        assert _types(result) == ["orphaned_document_ref"]
        assert result.issues[0].field_path == "supervisor"

    @pytest.mark.asyncio
    async def test_a_document_reference_across_namespaces_resolves(self, db):
        # Referencing a document in another namespace is legitimate — shared
        # vocabularies live that way — so the probe must not be scoped.
        await _document("TARGET", namespace=OTHER_NAMESPACE)
        await _document("D1", references=[{
            "field_path": "supervisor",
            "reference_type": "document",
            "resolved": {"document_id": "TARGET"},
        }])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_a_missing_file_reference_is_an_error(self, db):
        await _document("D1", file_references=[
            {"field_path": "scan", "file_id": "F-MISSING"},
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert _types(result) == ["orphaned_file_ref"]

    @pytest.mark.asyncio
    async def test_a_present_file_reference_resolves(self, db):
        await File(
            file_id="F-1", namespace=NAMESPACE, filename="scan.pdf",
            content_type="application/pdf", size_bytes=10,
            checksum="abc", storage_key="F-1",
        ).insert()
        await _document("D1", file_references=[
            {"field_path": "scan", "file_id": "F-1"},
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_a_missing_template_is_still_reported(self, db):
        db["template_store"].get_template = AsyncMock(return_value=None)
        await _document("D1")

        result = await check_all_documents(namespace=NAMESPACE)

        assert "orphaned_template_ref" in _types(result)

    @pytest.mark.asyncio
    async def test_a_missing_term_is_still_reported(self, db):
        db["def_store"].get_term = AsyncMock(return_value=None)
        await _document("D1", term_references=[
            {"field_path": "gender", "term_id": "TERM-GONE"},
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert "orphaned_term_ref" in _types(result)


# ---------------------------------------------------------------------------
# Scanning behaviour
# ---------------------------------------------------------------------------


class TestScanning:
    @pytest.mark.asyncio
    async def test_every_document_is_checked_across_batch_boundaries(self, db):
        # The scan is cursor-based; a batching bug would silently check a
        # prefix and report the namespace healthy.
        total = integrity_service.BATCH_SIZE + 7
        for index in range(total):
            await _document(f"D{index}", data={"patient_id": f"P-{index}"})

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.summary.documents_checked == total

    @pytest.mark.asyncio
    async def test_limit_stops_early(self, db):
        for index in range(5):
            await _document(f"D{index}", data={"patient_id": f"P-{index}"})

        result = await check_all_documents(namespace=NAMESPACE, limit=3)

        assert result.summary.documents_checked == 3
        assert result.summary.total_documents == 5

    @pytest.mark.asyncio
    async def test_progress_is_reported_per_batch(self, db):
        seen: list[tuple[int, int]] = []
        for index in range(3):
            await _document(f"D{index}", data={"patient_id": f"P-{index}"})

        await check_all_documents(
            namespace=NAMESPACE, progress=lambda done, total: seen.append((done, total))
        )

        assert seen and seen[-1] == (3, 3)

    @pytest.mark.asyncio
    async def test_identity_checking_can_be_turned_off(self, db):
        await _document("D1", identity_hash="not-the-real-hash")

        result = await check_all_documents(namespace=NAMESPACE, check_identity=False)

        assert result.status == "healthy"
