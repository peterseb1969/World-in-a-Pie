"""Contract tests for document↔template-version pin semantics.

These behaviors exist and are relied upon; the tests pin them as designed
contract so they cannot drift silently:

- A document write may pass an explicit template_version to validate
  against (default: latest active). The created document is pinned to
  the version it validated against.
- Pin-to-what-validated: an upsert's NEW document version pins to the
  template version the INCOMING write validated against — a document's
  version history may span template versions. There is no auto-migration;
  moving a cohort is always the explicit migrate operation.
- PATCH validates against the document's pinned version, never latest
  (covered in test_documents_patch.py::test_patch_preserves_template_version;
  the cross-version upsert half lives here).
"""

import copy

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus

from .conftest import SAMPLE_TEMPLATES, VERSIONED_TEMPLATE_OVERRIDES

DOCUMENTS = "/api/document-store/documents"


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Keep version overrides hermetic — clear before and after each test."""
    VERSIONED_TEMPLATE_OVERRIDES.clear()
    yield
    VERSIONED_TEMPLATE_OVERRIDES.clear()


def _register_person_v2(add_optional: str = "phone") -> str:
    """Register a v2 of the PERSON template (adds one optional field) in the
    version-override table and return the template's canonical id."""
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    tpl = copy.deepcopy(SAMPLE_TEMPLATES["PERSON"])
    tpl["version"] = 2
    tpl["fields"].append({
        "name": add_optional, "label": add_optional,
        "type": "string", "mandatory": False,
    })
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = tpl
    # v1 stays reachable under an explicit version request too.
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 1)] = SAMPLE_TEMPLATES["PERSON"]
    return tid


async def _create(client: AsyncClient, auth_headers: dict, data: dict, **extra) -> dict:
    payload = {"namespace": "wip", "template_id": "PERSON", "data": data, **extra}
    resp = await client.post(DOCUMENTS, headers=auth_headers, json=[payload])
    assert resp.status_code == 200, resp.text
    bulk = resp.json()
    assert bulk["succeeded"] == 1, bulk
    return bulk["results"][0]


@pytest.mark.asyncio
async def test_explicit_template_version_pins_the_document(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A create carrying template_version=1 validates against v1 and the
    stored document is pinned to 1 — even with a newer version registered."""
    _register_person_v2()

    item = await _create(client, auth_headers, sample_person_data, template_version=1)
    assert item["version"] == 1

    doc = await Document.find_one({
        "document_id": item["document_id"],
        "status": DocumentStatus.ACTIVE.value,
    })
    assert doc.template_version == 1


@pytest.mark.asyncio
async def test_upsert_pins_to_what_the_write_validated_against(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Cross-version upsert: same identity resubmitted against v2 becomes a
    new document version pinned to v2 — history spans template versions,
    and nothing about the v1-pinned history is rewritten."""
    _register_person_v2()

    first = await _create(client, auth_headers, sample_person_data, template_version=1)
    assert first["version"] == 1

    v2_data = dict(sample_person_data)
    v2_data["phone"] = "+49-40-709709"
    second = await _create(client, auth_headers, v2_data, template_version=2)

    # Same identity, same document — a new version, not a new document.
    assert second["document_id"] == first["document_id"]
    assert second["identity_hash"] == first["identity_hash"]
    assert second["version"] == 2

    latest = await Document.find_one({
        "document_id": first["document_id"],
        "status": DocumentStatus.ACTIVE.value,
    })
    assert latest.version == 2
    assert latest.template_version == 2
    assert latest.data["phone"] == "+49-40-709709"

    # The v1-pinned history row is untouched — no silent migration.
    v1_row = await Document.find_one({
        "document_id": first["document_id"], "version": 1,
    })
    assert v1_row.template_version == 1


@pytest.mark.asyncio
async def test_upsert_without_explicit_version_keeps_working_against_v1(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A resubmission that still passes its pinned version's shape does not
    move the document anywhere: pinning is per-write, and an unversioned
    write against the same (single-version) template stays on it."""
    first = await _create(client, auth_headers, sample_person_data, template_version=1)

    changed = dict(sample_person_data)
    changed["first_name"] = "Janet"
    second = await _create(client, auth_headers, changed, template_version=1)

    assert second["document_id"] == first["document_id"]
    assert second["version"] == 2

    latest = await Document.find_one({
        "document_id": first["document_id"],
        "status": DocumentStatus.ACTIVE.value,
    })
    assert latest.template_version == 1
