"""CASE-629 — write-time field invariants must not break reads of stored data.

The CASE-493 (a nested `template_ref` must pin a version) and CASE-550 (an array
of references must declare `reference_type`) guards used to be pydantic
`@model_validator`s on `FieldDefinition`. `FieldDefinition` is embedded in the
Beanie `Template` document, so Beanie re-ran them on hydration
(`model_validate` of the stored Mongo dict) — a template legally stored before a
guard existed then 500'd on every read once the guard shipped (CASE-627). The
guards now live at the write seam (`TemplateService.validate_fields_for_write`),
so stored history always hydrates while new/updated definitions are still gated.

These tests pin both halves of the class, for both guards.
"""

import os

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from template_store.models.field import FieldDefinition
from template_store.services.template_service import TemplateService

TEMPLATES = "/api/template-store/templates"

# Raw stored shapes as a pre-guard archive / restore would land them.
PRE_550 = {"name": "kb_refs", "label": "Refs", "type": "array", "array_item_type": "reference"}
PRE_493 = {"name": "nested", "label": "N", "type": "reference", "reference_type": "template", "template_ref": "SOME_TPL"}


def _templates_collection():
    c = AsyncIOMotorClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)
    return c[os.environ["DATABASE_NAME"]]["templates"]


class TestPersistenceModelIsPermissive:
    """The core fix: hydrating a stored pre-guard field must not raise."""

    def test_pre_550_shape_hydrates(self):
        # Would have raised while the guard lived on FieldDefinition.
        FieldDefinition.model_validate(PRE_550)

    def test_pre_493_shape_hydrates(self):
        FieldDefinition.model_validate(PRE_493)


class TestWriteSeamStillRejects:
    """The integrity win is preserved — new definitions are gated at the seam."""

    def test_rejects_pre_550(self):
        with pytest.raises(ValueError, match="reference_type is required"):
            TemplateService.validate_fields_for_write([FieldDefinition.model_validate(PRE_550)])

    def test_rejects_pre_493(self):
        with pytest.raises(ValueError, match="template_ref_version is required"):
            TemplateService.validate_fields_for_write([FieldDefinition.model_validate(PRE_493)])

    def test_accepts_valid_array_reference(self):
        ok = FieldDefinition.model_validate({**PRE_550, "reference_type": "document"})
        TemplateService.validate_fields_for_write([ok])  # no raise


class TestStoredPreGuardTemplateReadsThrough:
    """End-to-end: a stored template carrying the pre-550 shape (as a restore of
    an old archive lands it) must read 200, not 500."""

    @pytest.mark.asyncio
    async def test_read_endpoints_hydrate_pre_550_template(
        self, client: AsyncClient, auth_headers: dict
    ):
        # Create a valid array-of-references template through the API...
        resp = await client.post(
            TEMPLATES,
            headers=auth_headers,
            json=[{
                "namespace": "wip",
                "value": "PRE550_TPL",
                "label": "pre-550",
                "fields": [{
                    "name": "refs", "label": "Refs", "type": "array",
                    "array_item_type": "reference", "reference_type": "document",
                }],
                "identity_fields": ["refs"],
            }],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["status"] == "created", resp.text

        # ...then strip reference_type directly in Mongo, reproducing the
        # pre-guard stored shape (a store-level write, exactly what restore does).
        coll = _templates_collection()
        upd = await coll.update_one(
            {"value": "PRE550_TPL"}, {"$unset": {"fields.0.reference_type": ""}}
        )
        assert upd.modified_count == 1

        # The read endpoints reconstruct the Template model — they must hydrate
        # the now-pre-guard shape, not 500.
        by_value = await client.get(
            f"{TEMPLATES}/by-value/PRE550_TPL?namespace=wip", headers=auth_headers
        )
        assert by_value.status_code == 200, by_value.text

        listing = await client.get(
            f"{TEMPLATES}?namespace=wip&page_size=100", headers=auth_headers
        )
        assert listing.status_code == 200, listing.text

    @pytest.mark.asyncio
    async def test_write_endpoint_rejects_pre_550_per_item(
        self, client: AsyncClient, auth_headers: dict
    ):
        # The guard moved to the service seam, so the rejection now surfaces as
        # a per-item bulk error (200 + status=error), matching WIP's bulk-first
        # convention and the CASE-478 precedent — not a parse-time 422.
        resp = await client.post(
            TEMPLATES,
            headers=auth_headers,
            json=[{
                "namespace": "wip",
                "value": "BAD550_TPL",
                "label": "bad",
                "fields": [{"name": "refs", "label": "Refs", "type": "array", "array_item_type": "reference"}],
                "identity_fields": ["refs"],
            }],
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["results"][0]
        assert item["status"] == "error"
        assert "reference_type is required" in item["error"]
