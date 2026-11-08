"""Namespace deletion finds what other namespaces hold into the doomed one.

Deleting a namespace removes every entity in it. Anything another namespace
points at in it is left dangling: a template whose parent or field
vocabulary is gone, a document whose reference target is gone, a relation
into a vanished term. The dry run reports these, and a real deletion refuses
without ``force`` while they exist.

The rows below are planted in the other services' collections in the shape
those services store them: references hold canonical ids (a template field's
``terminology_ref`` is the terminology's UUID, ``extends`` the parent's
template_id), and a document reference records the namespace of its target.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from registry.models.namespace import Namespace

_SERVICE_COLLECTIONS = (
    ("wip_template_store", "templates"),
    ("wip_def_store", "terminologies"),
    ("wip_def_store", "terms"),
    ("wip_def_store", "term_relations"),
    ("wip_document_store", "documents"),
    ("wip_document_store", "files"),
)


def _coll(db: str, name: str):
    return Namespace.get_motor_collection().database.client[db][name]


@pytest_asyncio.fixture
async def spaces(client: AsyncClient, auth_headers: dict):
    """A doomed namespace and a surviving one, unique per test."""
    tag = uuid.uuid4().hex[:8]
    doomed, survivor = f"inb-doomed-{tag}", f"inb-survivor-{tag}"
    for prefix in (doomed, survivor):
        resp = await client.post(
            "/api/registry/namespaces",
            json={"prefix": prefix, "deletion_mode": "full"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
    yield doomed, survivor, lambda name: f"{name}-{tag}"
    for db, name in _SERVICE_COLLECTIONS:
        await _coll(db, name).delete_many({"namespace": {"$in": [doomed, survivor]}})


async def _template(namespace, template_id, value, **extra):
    row = {"namespace": namespace, "template_id": template_id, "value": value,
           "version": 1, "status": "active", "fields": [], **extra}
    await _coll("wip_template_store", "templates").insert_one(row)


async def _terminology(namespace, terminology_id, value):
    await _coll("wip_def_store", "terminologies").insert_one(
        {"namespace": namespace, "terminology_id": terminology_id, "value": value}
    )


async def _dry_run(client, auth_headers, prefix):
    resp = await client.delete(
        f"/api/registry/namespaces/{prefix}",
        params={"dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bind(params, target, i):
    """Fill a parametrized row's placeholders with this test's unique ids."""
    text = json.dumps(params).replace("@TARGET", target).replace("@OTHER", i("TPL-OTHER"))
    return json.loads(text)


def _types(report):
    return sorted(r["type"] for r in report["inbound_references"])


class TestTemplatesIntoTheDoomedNamespace:
    @pytest.mark.asyncio
    async def test_a_child_template_extending_a_doomed_parent(self, client, auth_headers, spaces):
        doomed, survivor, i = spaces
        await _template(doomed, i("TPL-PARENT"), "PARENT")
        await _template(survivor, i("TPL-CHILD"), "CHILD", extends=i("TPL-PARENT"), extends_version=1)

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["template_extends"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", [
        {"name": "f", "type": "term", "terminology_ref": "@TARGET"},
        {"name": "f", "type": "array", "array_terminology_ref": "@TARGET"},
        {"name": "f", "type": "reference", "reference_type": "term",
         "target_terminologies": ["@TARGET"]},
    ])
    async def test_a_field_drawing_on_a_doomed_terminology(
        self, client, auth_headers, spaces, field
    ):
        doomed, survivor, i = spaces
        await _terminology(doomed, i("TY-DOOMED"), "COLOURS")
        await _template(survivor, i("TPL-USER"), "USER", fields=[_bind(field, i("TY-DOOMED"), i)])

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["terminology_reference"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [
        {"fields": [{"name": "f", "type": "object", "template_ref": "@TARGET"}]},
        {"fields": [{"name": "f", "type": "array", "array_template_ref": "@TARGET"}]},
        {"fields": [{"name": "f", "type": "reference", "reference_type": "document",
                     "target_templates": ["@TARGET"]}]},
        {"usage": "relationship",
         "source_templates": [{"lookup_value": "x:THING", "resolved": "@TARGET"}],
         "target_templates": [{"lookup_value": "OTHER", "resolved": "@OTHER"}]},
    ])
    async def test_a_template_referencing_a_doomed_template(
        self, client, auth_headers, spaces, extra
    ):
        doomed, survivor, i = spaces
        await _template(doomed, i("TPL-DOOMED"), "THING")
        await _template(survivor, i("TPL-USER"), "USER", **_bind(extra, i("TPL-DOOMED"), i))

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["template_reference"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    async def test_references_inside_the_doomed_namespace_are_not_inbound(
        self, client, auth_headers, spaces
    ):
        doomed, _, i = spaces
        await _terminology(doomed, i("TY-DOOMED"), "COLOURS")
        await _template(doomed, i("TPL-PARENT"), "PARENT")
        await _template(doomed, i("TPL-CHILD"), "CHILD", extends=i("TPL-PARENT"),
                        fields=[{"name": "f", "terminology_ref": i("TY-DOOMED")}])

        report = await _dry_run(client, auth_headers, doomed)

        assert report["inbound_references"] == []
        assert report["requires_force"] is False


class TestDataIntoTheDoomedNamespace:
    @pytest.mark.asyncio
    async def test_documents_referencing_a_doomed_document(self, client, auth_headers, spaces):
        doomed, survivor, i = spaces
        await _template(survivor, i("TPL-USER"), "USER")
        for n in range(3):
            await _coll("wip_document_store", "documents").insert_one({
                "namespace": survivor, "document_id": i(f"D-{n}"), "version": 1,
                "template_id": i("TPL-USER"),
                "references": [{
                    "field_path": "owner", "reference_type": "document",
                    "resolved": {"document_id": i("D-DOOMED"), "namespace": doomed},
                }],
            })

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["document_reference"]
        (ref,) = report["inbound_references"]
        assert ref["source_namespace"] == survivor
        assert "3 document version(s)" in ref["source_entity"]
        assert "USER" in ref["source_entity"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    async def test_a_relation_into_a_doomed_term(self, client, auth_headers, spaces):
        doomed, survivor, i = spaces
        await _coll("wip_def_store", "terms").insert_one(
            {"namespace": doomed, "term_id": i("TERM-DOOMED"), "value": "red"}
        )
        await _coll("wip_def_store", "term_relations").insert_one({
            "namespace": survivor, "source_term_id": i("TERM-MINE"),
            "target_term_id": i("TERM-DOOMED"), "relation_type": "is_a",
        })

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["term_relation"]
        assert report["requires_force"] is True


async def _document(namespace, document_id, template_id, **extra):
    await _coll("wip_document_store", "documents").insert_one({
        "namespace": namespace, "document_id": document_id, "version": 1,
        "template_id": template_id, **extra,
    })


class TestDocumentsUsingTheDoomedNamespace:
    """A document points at its template, its terms, its files and the
    templates/terminologies of its reference fields by bare id, recording no
    namespace. Templates, terms and files are usable from other namespaces,
    so another namespace's documents can depend on the doomed one without
    any template there saying so."""

    @pytest.mark.asyncio
    async def test_documents_built_on_a_doomed_template(self, client, auth_headers, spaces):
        doomed, survivor, i = spaces
        await _template(doomed, i("TPL-LIB"), "LIBRARY_THING")
        for n in range(2):
            await _document(survivor, i(f"D-{n}"), i("TPL-LIB"))

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["template_use"]
        (ref,) = report["inbound_references"]
        assert ref["source_namespace"] == survivor
        assert "2 document version(s)" in ref["source_entity"]
        assert "LIBRARY_THING" in ref["target_entity"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("refs", [
        {"term_references": [{"field_path": "colour", "term_id": "@TARGET"}]},
        {"references": [{"field_path": "colour", "reference_type": "term",
                         "resolved": {"term_id": "@TARGET"}}]},
    ])
    async def test_documents_holding_a_doomed_term(self, client, auth_headers, spaces, refs):
        doomed, survivor, i = spaces
        await _coll("wip_def_store", "terms").insert_one(
            {"namespace": doomed, "term_id": i("TERM-DOOMED"), "value": "red"}
        )
        await _template(survivor, i("TPL-USER"), "USER")
        await _document(survivor, i("D-1"), i("TPL-USER"), **_bind(refs, i("TERM-DOOMED"), i))

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["term_reference"]
        assert "USER" in report["inbound_references"][0]["source_entity"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    async def test_documents_referencing_a_doomed_template_or_terminology(
        self, client, auth_headers, spaces
    ):
        doomed, survivor, i = spaces
        await _template(doomed, i("TPL-DOOMED"), "THING")
        await _terminology(doomed, i("TY-DOOMED"), "COLOURS")
        await _template(survivor, i("TPL-USER"), "USER")
        await _document(survivor, i("D-1"), i("TPL-USER"), references=[
            {"field_path": "schema", "reference_type": "template",
             "resolved": {"template_id": i("TPL-DOOMED")}},
            {"field_path": "vocab", "reference_type": "terminology",
             "resolved": {"terminology_id": i("TY-DOOMED")}},
        ])

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["template_reference", "terminology_reference"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    async def test_documents_holding_a_doomed_file(self, client, auth_headers, spaces):
        doomed, survivor, i = spaces
        await _coll("wip_document_store", "files").insert_one(
            {"namespace": doomed, "file_id": i("FILE-DOOMED"), "filename": "scan.png"}
        )
        await _template(survivor, i("TPL-USER"), "USER")
        await _document(survivor, i("D-1"), i("TPL-USER"), file_references=[
            {"field_path": "scan", "file_id": i("FILE-DOOMED"), "filename": "scan.png"},
        ])

        report = await _dry_run(client, auth_headers, doomed)

        assert _types(report) == ["file_reference"]
        assert report["requires_force"] is True

    @pytest.mark.asyncio
    async def test_the_doomed_namespaces_own_documents_are_not_inbound(
        self, client, auth_headers, spaces
    ):
        doomed, _, i = spaces
        await _template(doomed, i("TPL-OWN"), "OWN")
        await _coll("wip_def_store", "terms").insert_one(
            {"namespace": doomed, "term_id": i("TERM-OWN"), "value": "red"}
        )
        await _coll("wip_document_store", "files").insert_one(
            {"namespace": doomed, "file_id": i("FILE-OWN"), "filename": "a"}
        )
        await _document(doomed, i("D-1"), i("TPL-OWN"),
                        term_references=[{"field_path": "c", "term_id": i("TERM-OWN")}],
                        file_references=[{"field_path": "f", "file_id": i("FILE-OWN")}])

        report = await _dry_run(client, auth_headers, doomed)

        assert report["inbound_references"] == []
        assert report["requires_force"] is False


class TestDeletionGate:
    @pytest.mark.asyncio
    async def test_a_namespace_whose_template_others_use_is_not_deleted_without_force(
        self, client, auth_headers, spaces
    ):
        doomed, survivor, i = spaces
        await _template(doomed, i("TPL-LIB"), "LIBRARY_THING")
        await _document(survivor, i("D-1"), i("TPL-LIB"))

        resp = await client.delete(f"/api/registry/namespaces/{doomed}", headers=auth_headers)

        assert resp.status_code != 200 or resp.json().get("status") != "completed"
        ns = await Namespace.find_one({"prefix": doomed})
        assert ns is not None and ns.status == "active"

    @pytest.mark.asyncio
    async def test_a_referenced_namespace_is_not_deleted_without_force(
        self, client, auth_headers, spaces
    ):
        doomed, survivor, i = spaces
        await _template(doomed, i("TPL-PARENT"), "PARENT")
        await _template(survivor, i("TPL-CHILD"), "CHILD", extends=i("TPL-PARENT"))

        resp = await client.delete(f"/api/registry/namespaces/{doomed}", headers=auth_headers)

        assert resp.status_code != 200 or resp.json().get("status") != "completed"
        ns = await Namespace.find_one({"prefix": doomed})
        assert ns is not None and ns.status == "active"
