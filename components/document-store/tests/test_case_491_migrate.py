"""CASE-491 — validated, identity-preserving template-version migrate primitive.

Exercises POST /api/document-store/documents/migrate end-to-end against the
real Registry + Mongo, with version-aware template overrides (the migrate op
resolves a source and a target version that differ — something the single
-version SAMPLE_TEMPLATES mock cannot express on its own).

Covers the spike's required guards:
- dry-run readiness report (pass + fail)
- apply creates a new version pinned to the target (incl. identical-data,
  which the data-only change-detection would otherwise skip)
- identity_fields mismatch is rejected as a fork
- a frozen (inactive) source version still migrates
- target-must-be-active and same-version guards
"""

import copy

import pytest

from document_store.models.document import Document, DocumentStatus

from .conftest import SAMPLE_TEMPLATES, VERSIONED_TEMPLATE_OVERRIDES

MIGRATE_URL = "/api/document-store/documents/migrate"


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Keep version overrides hermetic — clear before and after each test."""
    VERSIONED_TEMPLATE_OVERRIDES.clear()
    yield
    VERSIONED_TEMPLATE_OVERRIDES.clear()


async def _create_person(client, auth_headers, **overrides):
    """Create a PERSON document (template version 1) and return its bulk result item."""
    data = {
        "national_id": "123456789",
        "first_name": "John",
        "last_name": "Doe",
        "gender": "M",
        "age": 34,
    }
    data.update(overrides)
    resp = await client.post(
        "/api/document-store/documents",
        json=[{"template_id": "PERSON", "namespace": "wip", "data": data}],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated"), item
    return item


def _person_v2(template_id: str, *, version: int = 2, status: str = "active",
               add_optional: str | None = None, add_mandatory: str | None = None,
               identity_fields: list[str] | None = None) -> dict:
    """Build a v2 PERSON template by deriving from the registered v1."""
    tpl = copy.deepcopy(SAMPLE_TEMPLATES[template_id])
    tpl["version"] = version
    tpl["status"] = status
    if identity_fields is not None:
        tpl["identity_fields"] = identity_fields
    if add_optional:
        tpl["fields"].append({"name": add_optional, "label": add_optional,
                              "type": "string", "mandatory": False})
    if add_mandatory:
        tpl["fields"].append({"name": add_mandatory, "label": add_mandatory,
                              "type": "string", "mandatory": True})
    return tpl


async def _doc_in_db(document_id: str) -> Document:
    return await Document.find_one({
        "document_id": document_id, "status": DocumentStatus.ACTIVE.value,
    })


@pytest.mark.asyncio
async def test_migrate_dry_run_reports_ready_without_writing(client, auth_headers):
    item = await _create_person(client, auth_headers)
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    doc_id = item["document_id"]
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2(tid, add_optional="phone")

    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["from_version"] == 1 and body["to_version"] == 2
    assert body["total"] == 1 and body["succeeded"] == 1 and body["failed"] == 0
    assert body["results"][0]["status"] == "updated"

    # Nothing was written — the document is still on version 1.
    doc = await _doc_in_db(doc_id)
    assert doc.version == 1 and doc.template_version == 1


@pytest.mark.asyncio
async def test_migrate_apply_pins_new_version_to_target_even_with_identical_data(client, auth_headers):
    item = await _create_person(client, auth_headers)
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    doc_id = item["document_id"]
    original_hash = item["identity_hash"]
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2(tid, add_optional="phone")

    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["succeeded"] == 1 and body["failed"] == 0
    result = body["results"][0]
    assert result["status"] == "updated"
    assert result["version"] == 2
    assert result["document_id"] == doc_id
    assert result["identity_hash"] == original_hash

    # The new version is pinned to the target template version; identity stable.
    doc = await _doc_in_db(doc_id)
    assert doc.version == 2
    assert doc.template_version == 2
    assert doc.identity_hash == original_hash


@pytest.mark.asyncio
async def test_migrate_dry_run_flags_doc_failing_target_validation(client, auth_headers):
    item = await _create_person(client, auth_headers)
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    doc_id = item["document_id"]
    # v2 adds a NEW mandatory field the existing doc lacks → not migratable as-is.
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2(tid, add_mandatory="ssn")

    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failed"] == 1 and body["succeeded"] == 0
    result = body["results"][0]
    assert result["status"] == "error"
    assert result["error_code"] == "validation_failed"
    assert result["details"] and result["details"]["errors"]

    # Still untouched.
    doc = await _doc_in_db(doc_id)
    assert doc.version == 1 and doc.template_version == 1


@pytest.mark.asyncio
async def test_migrate_rejects_identity_fields_change_as_fork(client, auth_headers):
    item = await _create_person(client, auth_headers)
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2(
        tid, identity_fields=["national_id", "first_name"],
    )

    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error_code"] == "identity_fields_changed"


@pytest.mark.asyncio
async def test_migrate_rejects_inactive_target(client, auth_headers):
    item = await _create_person(client, auth_headers)
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2(tid, status="inactive")

    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error_code"] == "target_inactive"


@pytest.mark.asyncio
async def test_migrate_works_when_source_version_is_frozen(client, auth_headers):
    item = await _create_person(client, auth_headers)
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    doc_id = item["document_id"]
    # Source version frozen (deactivated) — the migration lock. Target active.
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 1)] = _person_v2(tid, version=1, status="inactive")
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2(tid, add_optional="phone")

    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["succeeded"] == 1 and body["failed"] == 0
    assert body["results"][0]["status"] == "updated"

    doc = await _doc_in_db(doc_id)
    assert doc.version == 2 and doc.template_version == 2


@pytest.mark.asyncio
async def test_migrate_rejects_same_source_and_target(client, auth_headers):
    await _create_person(client, auth_headers)
    resp = await client.post(
        MIGRATE_URL,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 1, "dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error_code"] == "invalid_migration"
