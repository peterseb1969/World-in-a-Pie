"""align_templates: a merge maps template versions by schema, not by number.

Provenance: CASE-1043 defect 2 (design: docs/design/merge-template-version-
alignment.md). Version numbers are per install: an archive whose template
history runs to v3 never lines up with a fresh bootstrap's v1 when templates
are matched by (value, version). With align_templates, each archive version
maps onto the target version whose schema can take its documents, documents
are re-pinned to it, and documents that fit no target version are listed and
not written. Runs the real engine against the test MongoDB, like
test_merge_restore.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException

from document_store.api.backup import _validate_merge_options
from document_store.services.backup_engine import RestoreEngineError
from tests.test_merge_restore import (
    NAMESPACE,
    _all_collections,
    _archive,
    _rows,
    _run_merge,
    _seed,
    _seed_namespace,
)


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    import os

    from motor.motor_asyncio import AsyncIOMotorClient

    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


AGE_INT = {"name": "age", "type": "integer"}
AGE_STR = {"name": "age", "type": "string"}
NOTE = {"name": "note", "type": "string"}
CODE = {"name": "code", "type": "string", "mandatory": True}


def _tpl(template_id: str, version: int, fields: list[dict], **extra) -> dict:
    return {
        "template_id": template_id, "namespace": NAMESPACE, "value": "PATIENT",
        "label": "Patient", "version": version, "status": "active",
        "identity_fields": ["age"], "fields": fields, **extra,
    }


def _doc(doc_id: str, template_version: int, data: dict, *, version: int = 1, ident: str = "") -> dict:
    return {
        "document_id": doc_id, "namespace": NAMESPACE, "template_id": "A-tpl",
        "template_version": template_version, "identity_hash": ident or f"h-{doc_id}",
        "version": version, "data": data, "status": "active",
    }


async def _target(mongo, *versions: dict) -> None:
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", list(versions))


def _result(events) -> dict:
    complete = next(e for e in events if e.phase == "complete")
    return complete.details["namespaces"][NAMESPACE]["template_alignment"]


class TestAlignment:
    @pytest.mark.asyncio
    async def test_without_the_option_version_skew_refuses(self, mongo):
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 1, [AGE_STR]), _tpl("A-tpl", 2, [AGE_INT])],
            "documents": [_doc("D1", 2, {"age": 4})],
        })
        with pytest.raises(RestoreEngineError, match=r"not\s+compatible"):
            await _run_merge(mongo, archive)

    @pytest.mark.asyncio
    async def test_a_later_archive_version_lands_on_the_matching_target_version(self, mongo):
        """The reported shape: the archive's current schema is its v2, the
        fresh target's is v1. Its documents land re-pinned to v1; the archive's
        history v1 is pinned by nothing and is not a problem."""
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 1, [AGE_STR], status="inactive"), _tpl("A-tpl", 2, [AGE_INT])],
            "documents": [_doc("D1", 2, {"age": 4})],
        })
        events: list = []

        await _run_merge(mongo, archive, events, align_templates=True, dry_run=True)
        report = _result(events)
        [template] = report["templates"]
        by_version = {v["archive_version"]: v for v in template["versions"]}
        assert by_version[2]["target_version"] == 1 and by_version[2]["rows"] == 1
        assert by_version[1]["history"] is True
        assert report["unplaceable_count"] == 0

        await _run_merge(mongo, archive, align_templates=True)
        [doc] = await _rows(mongo, "documents")
        assert (doc["template_id"], doc["template_version"]) == ("B-tpl", 1)
        assert [t["template_id"] for t in await _rows(mongo, "templates")] == ["B-tpl"]

    @pytest.mark.asyncio
    async def test_a_target_that_adds_an_optional_field_takes_the_documents(self, mongo):
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT, NOTE]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 3, [AGE_INT])],
            "documents": [_doc("D1", 3, {"age": 4})],
        })
        await _run_merge(mongo, archive, align_templates=True)
        [doc] = await _rows(mongo, "documents")
        assert doc["template_version"] == 1

    @pytest.mark.asyncio
    async def test_a_target_that_requires_a_new_field_does_not(self, mongo):
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT, CODE]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 2, [AGE_INT])],
            "documents": [_doc("D1", 2, {"age": 4})],
        })
        events: list = []
        await _run_merge(mongo, archive, events, align_templates=True)

        assert await _rows(mongo, "documents") == []
        warning = next(e for e in events if e.phase == "warning" and "not merged" in e.message)
        assert "1 document(s)" in warning.message
        report = _result(events)
        [entry] = report["unplaceable_documents"]
        assert entry["document_id"] == "D1"
        assert "requires new field(s) code" in entry["versions"]["2"]

    @pytest.mark.asyncio
    async def test_a_new_required_field_with_a_default_is_still_required(self, mongo):
        """A default_value is never applied, so it cannot stand in for the
        field: a document re-pinned without it would hold data its own
        template version refuses (CASE-1200)."""
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT, {**CODE, "default_value": "X"}]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 2, [AGE_INT])],
            "documents": [_doc("D1", 2, {"age": 4})],
        })
        events: list = []
        await _run_merge(mongo, archive, events, align_templates=True)

        assert await _rows(mongo, "documents") == []
        [entry] = _result(events)["unplaceable_documents"]
        assert "requires new field(s) code" in entry["versions"]["2"]

    @pytest.mark.asyncio
    async def test_a_removed_field_is_fine_while_the_documents_leave_it_empty(self, mongo):
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 2, [AGE_INT, NOTE])],
            "documents": [_doc("D1", 2, {"age": 4}), _doc("D2", 2, {"age": 5, "note": ""})],
        })
        await _run_merge(mongo, archive, align_templates=True)
        assert sorted(d["document_id"] for d in await _rows(mongo, "documents")) == ["D1", "D2"]

    @pytest.mark.asyncio
    async def test_a_removed_field_some_document_fills_places_none_of_that_version(self, mongo):
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 2, [AGE_INT, NOTE])],
            "documents": [_doc("D1", 2, {"age": 4}), _doc("D2", 2, {"age": 5, "note": "kept"})],
        })
        events: list = []
        await _run_merge(mongo, archive, events, align_templates=True)

        assert await _rows(mongo, "documents") == []
        report = _result(events)
        assert report["unplaceable_count"] == 2
        assert "note (1)" in report["templates"][0]["versions"][0]["reason"]

    @pytest.mark.asyncio
    async def test_a_document_is_placed_whole_or_not_at_all(self, mongo):
        """Its old row pins a version no target version takes, so the whole
        document stays out — never written without its history."""
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
        archive = _archive({
            "templates": [_tpl("A-tpl", 1, [AGE_STR], status="inactive"), _tpl("A-tpl", 2, [AGE_INT])],
            "documents": [
                _doc("D1", 1, {"age": "4"}, version=1, ident="h-D1"),
                _doc("D1", 2, {"age": 4}, version=2, ident="h-D1"),
                _doc("D2", 2, {"age": 5}),
            ],
        })
        events: list = []
        await _run_merge(mongo, archive, events, align_templates=True)

        assert [d["document_id"] for d in await _rows(mongo, "documents")] == ["D2"]
        [entry] = _result(events)["unplaceable_documents"]
        assert entry["document_id"] == "D1" and list(entry["versions"]) == ["1"]


class TestRefusals:
    @pytest.mark.asyncio
    async def test_a_template_both_sides_share_is_refused(self, mongo):
        await _target(mongo, _tpl("A-tpl", 1, [AGE_INT]))
        archive = _archive({"templates": [_tpl("A-tpl", 1, [AGE_INT])]})
        with pytest.raises(RestoreEngineError) as refused:
            await _run_merge(mongo, archive, align_templates=True)
        assert "merge without align_templates" in str(refused.value.details)

    @pytest.mark.asyncio
    async def test_different_identity_fields_are_a_fork(self, mongo):
        await _target(mongo, _tpl("B-tpl", 1, [AGE_INT, NOTE]))
        archive = _archive({"templates": [_tpl("A-tpl", 1, [AGE_INT, NOTE], identity_fields=["note"])]})
        with pytest.raises(RestoreEngineError) as refused:
            await _run_merge(mongo, archive, align_templates=True)
        [issue] = refused.value.details["incompatibilities"]
        assert "identity_fields differs" in issue["reason"]

    def test_the_option_is_merge_only(self):
        with pytest.raises(HTTPException) as refused:
            _validate_merge_options(
                "restore", on_clash="skip", older_versions="retire",
                add_missing=False, extend_terminologies=False,
                drop_stale_reporting=False, align_templates=True,
            )
        assert "align_templates" in refused.value.detail


class TestVersionReferences:
    @pytest.mark.asyncio
    async def test_a_nested_template_version_is_compared_through_its_alignment(self, mongo):
        """ADDRESS is v4 in the archive and v1 on the target; PATIENT pins it
        by number. Compared raw (4 vs 1) the two PATIENTs differ; through
        ADDRESS's alignment they are the same schema."""
        address = {"name": "city", "type": "string"}
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [
            {**_tpl("B-addr", 1, [address]), "value": "ADDRESS", "identity_fields": []},
            _tpl("B-tpl", 1, [AGE_INT, {"name": "home", "type": "object",
                                         "template_ref": "B-addr", "template_ref_version": 1}]),
        ])
        archive = _archive({
            "templates": [
                {**_tpl("A-addr", 4, [address]), "value": "ADDRESS", "identity_fields": []},
                _tpl("A-tpl", 2, [AGE_INT, {"name": "home", "type": "object",
                                             "template_ref": "A-addr", "template_ref_version": 4}]),
            ],
            "documents": [_doc("D1", 2, {"age": 4, "home": {"city": "Bern"}})],
        })
        await _run_merge(mongo, archive, align_templates=True)
        [doc] = await _rows(mongo, "documents")
        assert (doc["template_id"], doc["template_version"]) == ("B-tpl", 1)
