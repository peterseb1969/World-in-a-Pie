"""CASE-752 — the template-namespace isolation check is armed.

The reference validator always implemented a template-isolation rule
(cross-namespace template use follows the same rules as term references:
own + wip + allowed_external_refs under open; own + list under strict),
but both call sites passed the DOCUMENT's namespace as template_namespace,
so the guard `template_namespace != document_namespace` was constant-false
for its whole life. Root cause one level deeper: the template's real
namespace was never in scope at the call sites — ValidationResult carried
template_version and template_value but not template_namespace.

Three layers pinned here:
1. The validator's template branch fires correctly once a REAL foreign
   namespace reaches it (rule shapes, mirroring test_case_566's structure).
2. ValidationResult.template_namespace is populated from the resolved
   template (the missing plumbing).
3. The create path end-to-end: a document create against a foreign-namespace
   template in an isolation-configured namespace is rejected — the full
   route → service → validator chain, with the validator's namespace cache
   seeded (the app fixture has no live Registry HTTP for the validator, so
   an unseeded cache means allow-all and the test would pass vacuously).
"""

import time
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from document_store.services.reference_validator import (
    ReferenceValidationError,
    ReferenceValidator,
    get_reference_validator,
)
from document_store.services.validation_service import ValidationService

DOCS_URL = "/api/document-store/documents"


def _validator(namespace: str, isolation_mode: str, allowed: list[str] | None = None):
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="test")
    v._namespace_cache[namespace] = (
        time.monotonic(),
        {
            "isolation_mode": isolation_mode,
            "allowed_external_refs": allowed or [],
        },
    )
    return v


# =========================================================================
# Layer 1 — the validator's template branch, fed a REAL foreign namespace
# =========================================================================


@pytest.mark.asyncio
async def test_open_mode_rejects_undeclared_foreign_template():
    v = _validator("appns", "open")
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="strangerns",
        )
    violations = exc_info.value.violations
    assert len(violations) == 1
    assert violations[0]["type"] == "template"
    assert violations[0]["namespace"] == "strangerns"


@pytest.mark.asyncio
async def test_open_mode_allows_wip_template():
    v = _validator("appns", "open")
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="wip",
    )


@pytest.mark.asyncio
async def test_open_mode_allows_declared_external_template():
    v = _validator("appns", "open", allowed=["strangerns"])
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="strangerns",
    )


@pytest.mark.asyncio
async def test_strict_mode_does_not_get_implicit_wip_template():
    v = _validator("appns", "strict")
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="wip",
        )
    assert exc_info.value.violations[0]["type"] == "template"


@pytest.mark.asyncio
async def test_strict_mode_allows_declared_external_template():
    v = _validator("appns", "strict", allowed=["strangerns"])
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="strangerns",
    )


@pytest.mark.asyncio
async def test_own_namespace_template_always_allowed():
    v = _validator("appns", "strict")
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
    )


# =========================================================================
# Layer 2 — ValidationResult carries the template's real namespace
# =========================================================================


def _minimal_template(namespace: str | None) -> dict:
    template = {
        "template_id": "0190c752-0000-7000-0000-000000000001",
        "value": "ISO_PROBE",
        "version": 1,
        "status": "active",
        "identity_fields": [],
        "fields": [
            {"name": "note", "type": "string", "label": "Note", "mandatory": False},
        ],
        "rules": [],
    }
    if namespace is not None:
        template["namespace"] = namespace
    return template


@pytest.mark.asyncio
async def test_validate_populates_template_namespace():
    svc = ValidationService()
    svc._resolve_template = AsyncMock(return_value=_minimal_template("tplns"))

    result = await svc.validate(
        "0190c752-0000-7000-0000-000000000001", {"note": "x"}, namespace="appns"
    )

    assert result.template_namespace == "tplns"


@pytest.mark.asyncio
async def test_template_without_namespace_yields_none():
    # The call sites fall back to the document's namespace on None — which
    # skips the template branch instead of false-positive against None.
    svc = ValidationService()
    svc._resolve_template = AsyncMock(return_value=_minimal_template(None))

    result = await svc.validate(
        "0190c752-0000-7000-0000-000000000001", {"note": "x"}, namespace="appns"
    )

    assert result.template_namespace is None


# =========================================================================
# Layer 3 — the create path rejects end-to-end (route → service → validator)
# =========================================================================


@pytest.fixture
def foreign_template_in_strict_namespace():
    """A template owned by 'foreigntpl', visible to the mocked template
    store, plus a seeded strict-isolation cache entry for the document
    namespace 'isotest'. Seeding the singleton validator's cache is what
    makes the check decidable in the app fixture — its Registry HTTP is not
    wired here, and an unknown namespace means allow-all."""
    from .conftest import SAMPLE_TEMPLATES

    template_id = "0190c752-0000-7000-0000-00000000e2e1"
    template = {
        "template_id": template_id,
        "value": "FOREIGN_ISO_PROBE",
        "label": "Foreign isolation probe",
        "version": 1,
        "status": "active",
        "namespace": "foreigntpl",
        "identity_fields": [],
        "header_fields": [],
        "fields": [
            {"name": "note", "type": "string", "label": "Note", "mandatory": False},
        ],
        "rules": [],
        "usage": "entity",
        "source_templates": [],
        "target_templates": [],
        "versioned": True,
    }
    SAMPLE_TEMPLATES[template_id] = template

    validator = get_reference_validator()
    validator._namespace_cache["isotest"] = (
        time.monotonic(),
        {"isolation_mode": "strict", "allowed_external_refs": []},
    )
    yield template_id
    SAMPLE_TEMPLATES.pop(template_id, None)
    validator._namespace_cache.pop("isotest", None)


@pytest.mark.asyncio
async def test_create_against_foreign_template_is_rejected(
    client: AsyncClient, auth_headers: dict, foreign_template_in_strict_namespace: str
):
    response = await client.post(
        DOCS_URL,
        headers=auth_headers,
        json=[{
            "namespace": "isotest",
            "template_id": foreign_template_in_strict_namespace,
            "data": {"note": "should not land"},
        }],
    )

    assert response.status_code == 200  # bulk-first: the error is per-item
    result = response.json()["results"][0]
    assert result["status"] == "error"
    assert "foreigntpl" in result["error"]
    assert "isotest" in result["error"]
