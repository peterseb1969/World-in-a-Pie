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
from wip_auth.resolve import EntityNotFoundError

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
    status="active",
    version=1,
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
        version=version,
        status=status,
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
    async def test_a_missing_template_version_is_reported_and_counted_unverified(self, db):
        """A document pinned to a version the template store does not have:
        the template exists (no orphaned-template finding), so nothing else
        reports it. Provenance: CASE-1146."""
        async def get_template(template_id=None, version=None, **_):
            if version is not None:
                return None
            return {"status": "active", "identity_fields": IDENTITY_FIELDS}

        db["template_store"].get_template = AsyncMock(side_effect=get_template)
        await _document("D1")

        result = await check_all_documents(namespace=NAMESPACE)

        assert _types(result) == ["template_version_not_found"]
        assert result.summary.identity_unverified == 1
        assert result.summary.identity_hash_mismatches == 0

    @pytest.mark.asyncio
    async def test_an_unverifiable_identity_is_not_counted_a_mismatch(self, db):
        """identity_hash_mismatches counts hashes compared and found different;
        a document that could not be compared is identity_unverified."""
        await _document("D1", data={"other": "value"}, identity_hash="x")

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.summary.identity_hash_mismatches == 0
        assert result.summary.identity_unverified == 1

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


def _doc_ref(target, *, namespace=None, lookup=None, field_path="supervisor"):
    resolved = {"document_id": target}
    if namespace is not None:
        resolved["namespace"] = namespace
    ref = {"field_path": field_path, "reference_type": "document", "resolved": resolved}
    if lookup is not None:
        ref["lookup_value"] = lookup
    return ref


class TestReferenceHalves:
    """A reference holds the id it resolved to, the namespace that id lives
    in, and the identifier it was written with. A fresh restore re-mints the
    ids it carried and passes the rest through, so a check that asks only
    "does the id exist" misses a reference whose halves disagree."""

    @pytest_asyncio.fixture
    async def resolver(self, db, monkeypatch):
        resolve = AsyncMock(side_effect=EntityNotFoundError("x", "document"))
        monkeypatch.setattr(integrity_service, "resolve_entity_id", resolve)
        return resolve

    @pytest.mark.asyncio
    async def test_an_id_living_in_another_namespace_than_claimed_is_an_error(
        self, resolver
    ):
        # A fresh restore into a new namespace maps the recorded namespace
        # even when the id was not re-minted: new namespace, old id. On the
        # source install the old id exists, so existence alone passes it.
        await _document("OLD-ID", namespace=OTHER_NAMESPACE)
        await _document("D1", references=[_doc_ref("OLD-ID", namespace=NAMESPACE)])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "error"
        assert _types(result) == ["misplaced_document_ref"]
        assert result.summary.misplaced_document_refs == 1
        assert result.summary.orphaned_document_refs == 0
        assert result.issues[0].message == (
            f"Reference names namespace '{NAMESPACE}' but document 'OLD-ID' "
            f"lives in '{OTHER_NAMESPACE}'"
        )

    @pytest.mark.asyncio
    async def test_a_cross_namespace_reference_that_says_so_is_healthy(self, resolver):
        await _document("TARGET", namespace=OTHER_NAMESPACE)
        await _document("D1", references=[_doc_ref("TARGET", namespace=OTHER_NAMESPACE)])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_an_orphan_names_what_its_lookup_resolves_to_here(self, resolver):
        resolver.side_effect = None
        resolver.return_value = "NEW-ID"
        await _document("D1", references=[
            _doc_ref("OLD-ID", namespace=NAMESPACE, lookup="other:P-7"),
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert _types(result) == ["orphaned_document_ref"]
        assert result.issues[0].message == (
            "Referenced document 'OLD-ID' not found; its lookup value "
            "'other:P-7' resolves to 'NEW-ID' here"
        )
        # Resolved where the reference was written: the holder's namespace.
        resolver.assert_awaited_once_with("other:P-7", "document", NAMESPACE)

    @pytest.mark.asyncio
    async def test_a_misplaced_reference_names_its_lookup_too(self, resolver):
        resolver.side_effect = None
        resolver.return_value = "NEW-ID"
        await _document("OLD-ID", namespace=OTHER_NAMESPACE)
        await _document("D1", references=[
            _doc_ref("OLD-ID", namespace=NAMESPACE, lookup="P-7"),
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.issues[0].message.endswith(
            "; its lookup value 'P-7' resolves to 'NEW-ID' here"
        )

    @pytest.mark.asyncio
    async def test_an_unresolvable_lookup_says_so(self, resolver):
        await _document("D1", references=[
            _doc_ref("OLD-ID", namespace=NAMESPACE, lookup="P-7"),
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.issues[0].message == (
            "Referenced document 'OLD-ID' not found; its lookup value "
            "'P-7' could not be resolved here"
        )

    @pytest.mark.asyncio
    async def test_a_lookup_that_is_the_held_id_adds_nothing(self, resolver):
        await _document("D1", references=[
            _doc_ref("OLD-ID", namespace=NAMESPACE, lookup="OLD-ID"),
        ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.issues[0].message == "Referenced document 'OLD-ID' not found"
        resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_lookup_is_resolved_once_per_batch(self, resolver):
        for doc_id in ("D1", "D2", "D3"):
            await _document(doc_id, data={"patient_id": doc_id}, references=[
                _doc_ref("OLD-ID", namespace=NAMESPACE, lookup="P-7"),
            ])

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.summary.orphaned_document_refs == 3
        assert resolver.await_count == 1


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


# ---------------------------------------------------------------------------
# Mandatory fields of the pinned version
# ---------------------------------------------------------------------------

ADDRESS_ID = "TPL-ADDR"
LINE_ID = "TPL-LINE"


def _schemas(versions):
    """A get_template stub serving {(template_id, version): fields}."""
    async def get_template(template_id=None, version=None, **_):
        fields = versions.get((template_id, version))
        if fields is None:
            return None
        return {"status": "active", "identity_fields": IDENTITY_FIELDS, "fields": fields}
    return AsyncMock(side_effect=get_template)


PATIENT_ID = {"name": "patient_id", "type": "string", "mandatory": True}


class TestMandatoryFields:
    """A document is validated once, when written. One placed on a version it
    does not satisfy (a restore, or a merge that re-pinned it under the rule
    before default_value became informational) is found here, not by its next
    PATCH."""

    @pytest.mark.asyncio
    async def test_a_mandatory_field_the_data_lacks_is_an_error(self, db):
        db["template_store"].get_template = _schemas({(TEMPLATE_ID, 1): [
            PATIENT_ID,
            {"name": "status", "type": "string", "mandatory": True, "default_value": "open"},
            {"name": "note", "type": "string"},
        ]})
        await _document("D1")

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "error"
        assert [(i.type, i.field_path, i.reference) for i in result.issues] == [
            ("mandatory_field_missing", "status", f"{TEMPLATE_ID}@v1"),
        ]
        assert result.summary.mandatory_fields_missing == 1

    @pytest.mark.asyncio
    async def test_a_null_mandatory_field_is_missing_and_a_present_one_is_healthy(self, db):
        db["template_store"].get_template = _schemas({(TEMPLATE_ID, 1): [
            PATIENT_ID, {"name": "status", "type": "string", "mandatory": True},
        ]})
        await _document("D1", data={"patient_id": "P-1", "status": None})
        await _document("D2", data={"patient_id": "P-2", "status": "open"})

        result = await check_all_documents(namespace=NAMESPACE)

        assert [(i.document_id, i.field_path) for i in result.issues] == [("D1", "status")]

    @pytest.mark.asyncio
    async def test_nested_objects_and_array_items_are_checked_at_their_pinned_version(self, db):
        db["template_store"].get_template = _schemas({
            (TEMPLATE_ID, 1): [
                PATIENT_ID,
                {"name": "address", "type": "object",
                 "template_ref": ADDRESS_ID, "template_ref_version": 2},
                {"name": "lines", "type": "array", "array_item_type": "object",
                 "array_template_ref": LINE_ID, "array_template_ref_version": 1},
            ],
            (ADDRESS_ID, 2): [{"name": "city", "type": "string", "mandatory": True}],
            (LINE_ID, 1): [{"name": "qty", "type": "integer", "mandatory": True}],
        })
        await _document("D1", data={
            "patient_id": "P-1", "address": {"street": "x"},
            "lines": [{"qty": 1}, {"sku": "A"}],
        })

        result = await check_all_documents(namespace=NAMESPACE)

        assert sorted((i.field_path, i.reference) for i in result.issues) == [
            ("address.city", f"{ADDRESS_ID}@v2"),
            ("lines[1].qty", f"{LINE_ID}@v1"),
        ]
        assert result.summary.mandatory_fields_missing == 2

    @pytest.mark.asyncio
    async def test_a_nested_template_version_not_found_is_reported(self, db):
        db["template_store"].get_template = _schemas({(TEMPLATE_ID, 1): [
            PATIENT_ID,
            {"name": "address", "type": "object",
             "template_ref": ADDRESS_ID, "template_ref_version": 7},
        ]})
        await _document("D1", data={"patient_id": "P-1", "address": {"city": "x"}})

        result = await check_all_documents(namespace=NAMESPACE)

        assert [(i.type, i.field_path) for i in result.issues] == [
            ("nested_template_not_found", "address"),
        ]
        assert result.summary.nested_templates_not_found == 1

    @pytest.mark.asyncio
    async def test_runs_with_identity_checking_off(self, db):
        db["template_store"].get_template = _schemas({(TEMPLATE_ID, 1): [
            PATIENT_ID, {"name": "status", "type": "string", "mandatory": True},
        ]})
        await _document("D1", identity_hash="not-the-real-hash")

        result = await check_all_documents(namespace=NAMESPACE, check_identity=False)

        assert _types(result) == ["mandatory_field_missing"]

    @pytest.mark.asyncio
    async def test_a_missing_pinned_version_is_reported_with_identity_checking_off(self, db):
        db["template_store"].get_template = AsyncMock(
            side_effect=lambda template_id=None, version=None, **_: (
                None if version is not None else {"status": "active"}
            )
        )
        await _document("D1")

        result = await check_all_documents(namespace=NAMESPACE, check_identity=False)

        assert _types(result) == ["template_version_not_found"]

    @pytest.mark.asyncio
    async def test_a_template_store_that_does_not_answer_makes_it_incomplete(self, db):
        from document_store.services.template_store_client import TemplateStoreError

        healthy = _schemas({(TEMPLATE_ID, 1): [
            PATIENT_ID,
            {"name": "address", "type": "object",
             "template_ref": ADDRESS_ID, "template_ref_version": 1},
        ]})

        async def get_template(template_id=None, version=None, **kwargs):
            if template_id == ADDRESS_ID:
                raise TemplateStoreError("down")
            return await healthy(template_id=template_id, version=version, **kwargs)

        db["template_store"].get_template = AsyncMock(side_effect=get_template)
        await _document("D1", data={"patient_id": "P-1", "address": {}})

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "incomplete"
        assert result.summary.checks_not_run == {"mandatory_fields:template-store": 1}

    @pytest.mark.asyncio
    async def test_only_the_current_version_is_checked(self, db):
        """A superseded version is history: a document repaired by PATCH keeps
        its old version row as it was, and must not be reported for ever."""
        db["template_store"].get_template = _schemas({(TEMPLATE_ID, 1): [
            PATIENT_ID, {"name": "status", "type": "string", "mandatory": True},
        ]})
        await _document("D1", status="inactive", version=1)
        await _document("D1", data={"patient_id": "P-1", "status": "open"}, version=2)

        result = await check_all_documents(namespace=NAMESPACE)

        assert result.status == "healthy"
