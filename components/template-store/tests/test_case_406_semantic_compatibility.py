"""Tests for CASE-406 — semantic field equality in compute_template_compatibility.

Before CASE-406 the comparator did `json.dumps(field.model_dump()) != ...`
which silently flagged stored-canonical-UUID ↔ resubmitted-value-form as
`modified_existing`. The fix resolves reference-typed properties through
Registry before comparison so synonyms ≡ canonical IDs at every site
(Peter's directive: "synonyms MUST work identically to canonical IDs").

Test surface:
- Field-level reference properties (terminology_ref, template_ref,
  target_templates, target_terminologies, array_*_ref)
- Template-level relationship-refs (source_templates / target_templates
  at the template level on edge types)
- Inherited-fields exclusion (resolution-time only, never stored)
- Plain-property changes still surface as `modified_existing`
- The smoke reproduction of CASE-404 Step 1's payload shape
"""

from copy import deepcopy

import pytest
from httpx import AsyncClient

from template_store.models.api_models import CreateTemplateRequest
from template_store.models.field import FieldDefinition, FieldType
from template_store.models.template import Template
from template_store.services.template_service import TemplateService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_field(
    name: str,
    type_: str = "string",
    mandatory: bool = False,
    **kwargs,
) -> FieldDefinition:
    return FieldDefinition(name=name, label=name.replace("_", " ").title(), type=FieldType(type_), mandatory=mandatory, **kwargs)


def _case_record_seed_payload() -> dict:
    """Approximation of CASE-404 Step 1's seed payload — value-form
    terminology_refs that the platform canonicalizes on store."""
    return {
        "namespace": "wip",
        "value": "CASE_RECORD_TEST",
        "label": "Case Record (test)",
        "identity_fields": ["doc_id"],
        "fields": [
            {"name": "doc_id", "label": "Doc ID", "type": "string", "mandatory": True},
            {"name": "title", "label": "Title", "type": "string", "mandatory": True},
            {"name": "doc_status", "label": "Status", "type": "term",
             "terminology_ref": "DOC_STATUS"},
            {"name": "body", "label": "Body", "type": "string"},
        ],
    }


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict], on_conflict: str | None = None) -> dict:
    url = "/api/template-store/templates"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    response = await client.post(url, headers=auth_headers, json=items)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# HTTP-driven: reproduces CASE-406 + CASE-404 Step 1's failure shape end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_value_form_resubmit_with_added_optional_lands_as_updated(
    client: AsyncClient, auth_headers: dict,
):
    """The core CASE-406 reproduction: submit value-form terminology_ref;
    resubmit same value-form + a new optional field. Expected status:
    'updated' (added_optional populated, no phantom modified_existing).

    Before the fix this was status='error', error_code='incompatible_schema',
    modified_existing=['doc_status'] because stored UUID != submitted value."""
    payload = _case_record_seed_payload()
    payload["value"] = "CASE406_VALUE_ADD"

    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert first["results"][0]["status"] == "created"

    # Same payload + one added optional field
    payload_v2 = deepcopy(payload)
    payload_v2["fields"].append(
        {"name": "new_optional", "label": "New Optional", "type": "string"}
    )

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    result = second["results"][0]
    assert result["status"] == "updated", (
        f"expected updated, got {result['status']} with details={result.get('details')}"
    )
    assert result["is_new_version"] is True
    diff = result["details"]
    assert diff["modified_existing"] == [], (
        f"phantom modified_existing leaked: {diff['modified_existing']}"
    )
    assert "new_optional" in diff["added_optional"]


@pytest.mark.asyncio
async def test_value_form_resubmit_no_change_returns_unchanged(
    client: AsyncClient, auth_headers: dict,
):
    """Re-submitting the exact same value-form payload returns 'unchanged'.
    Before the fix this returned 'error' / incompatible_schema."""
    payload = _case_record_seed_payload()
    payload["value"] = "CASE406_UNCHANGED"

    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert first["results"][0]["status"] == "created"

    second = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert second["results"][0]["status"] == "unchanged"
    assert second["results"][0]["details"]["modified_existing"] == []


@pytest.mark.asyncio
async def test_actual_terminology_change_still_incompatible(
    client: AsyncClient, auth_headers: dict,
):
    """An *actual* change to a different terminology must still be caught.
    The fix doesn't loosen modification detection; it only fixes the
    value↔UUID asymmetry on equivalent references."""
    payload = _case_record_seed_payload()
    payload["value"] = "CASE406_REAL_CHANGE"

    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert first["results"][0]["status"] == "created"

    payload_v2 = deepcopy(payload)
    # Switch DOC_STATUS -> GENDER (different terminology entirely)
    for f in payload_v2["fields"]:
        if f["name"] == "doc_status":
            f["terminology_ref"] = "GENDER"

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    result = second["results"][0]
    assert result["status"] == "error"
    assert result["error_code"] == "incompatible_schema"
    assert "doc_status" in result["details"]["modified_existing"]


@pytest.mark.asyncio
async def test_smoke_case404_step1_pattern(
    client: AsyncClient, auth_headers: dict,
):
    """The exact shape of CASE-404 Step 1's production failure: existing
    template has terminology-ref fields; resubmission adds N purely
    additive optional fields, no other changes. Pre-fix: error,
    modified_existing populated with phantom entries. Post-fix: updated,
    added_optional=[N], modified_existing=[]."""
    payload = _case_record_seed_payload()
    payload["value"] = "CASE406_SMOKE_404"

    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert first["results"][0]["status"] == "created"

    payload_v2 = deepcopy(payload)
    payload_v2["fields"].extend([
        {"name": f"new_field_{i}", "label": f"New Field {i}", "type": "string"}
        for i in range(6)
    ])

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    result = second["results"][0]
    assert result["status"] == "updated", (
        f"CASE-404 smoke failed: {result.get('error', result.get('details'))}"
    )
    assert result["is_new_version"] is True
    diff = result["details"]
    assert diff["modified_existing"] == []
    assert sorted(diff["added_optional"]) == sorted(
        [f"new_field_{i}" for i in range(6)]
    )


@pytest.mark.asyncio
async def test_array_terminology_ref_value_form_resubmit_lands_as_unchanged(
    client: AsyncClient, auth_headers: dict,
):
    """Same shape for array_terminology_ref: stored canonical UUID, resubmit
    value-form; comparator must treat them as equivalent."""
    payload = {
        "namespace": "wip",
        "value": "CASE406_ARRAY_REF",
        "label": "Array Ref Test",
        "identity_fields": ["doc_id"],
        "fields": [
            {"name": "doc_id", "label": "Doc ID", "type": "string", "mandatory": True},
            {"name": "tags", "label": "Tags", "type": "array",
             "array_item_type": "term", "array_terminology_ref": "DOC_STATUS"},
        ],
    }

    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert first["results"][0]["status"] == "created"

    second = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert second["results"][0]["status"] == "unchanged"


# ---------------------------------------------------------------------------
# Unit-level: direct comparator calls for properties hard to drive via HTTP
# (the conftest's `client` fixture sets up registry + resolution machinery)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inherited_properties_excluded_from_comparison(
    client: AsyncClient, auth_headers: dict,
):
    """`inherited` / `inherited_from` are populated during resolution and
    never stored. The comparator must exclude them — otherwise a stored
    field (inherited=False or None) would always look 'modified' against
    a freshly-resolved field with inherited=True from an extends chain."""
    base = _make_field("first_name")
    with_inheritance = _make_field("first_name")
    with_inheritance.inherited = True
    with_inheritance.inherited_from = "some-parent-template-id"

    assert await TemplateService._compare_field_definitions(
        base, with_inheritance, namespace="wip",
    ), "inherited / inherited_from should not affect semantic equality"


@pytest.mark.asyncio
async def test_compare_field_definitions_plain_property_change_surfaces(
    client: AsyncClient, auth_headers: dict,
):
    """A real change to a non-reference property (description) must still
    return False — the fix doesn't loosen the comparator, only canonicalizes
    references before comparison."""
    a = _make_field("first_name")
    a.metadata = {"note": "original"}
    b = _make_field("first_name")
    b.metadata = {"note": "changed"}

    assert not await TemplateService._compare_field_definitions(
        a, b, namespace="wip",
    )


@pytest.mark.asyncio
async def test_compare_field_definitions_value_and_uuid_compare_equal_via_registry(
    client: AsyncClient, auth_headers: dict,
):
    """Direct comparator: value-form terminology_ref vs UUID-form for the
    SAME terminology resolves through Registry and compares equal.

    Uses DOC_STATUS which the conftest pre-registers + auto-synonyms."""
    # Resolve DOC_STATUS to get the canonical UUID
    from wip_auth.resolve import resolve_entity_id
    canonical_uuid = await resolve_entity_id("DOC_STATUS", "terminology", "wip")
    assert canonical_uuid != "DOC_STATUS"  # sanity: they're different strings

    a = _make_field("status", type_="term", terminology_ref="DOC_STATUS")
    b = _make_field("status", type_="term", terminology_ref=canonical_uuid)

    assert await TemplateService._compare_field_definitions(
        a, b, namespace="wip",
    ), "value-form and UUID-form must compare equal at the comparator boundary"

    # And the reverse direction:
    assert await TemplateService._compare_field_definitions(
        b, a, namespace="wip",
    ), "comparator must be symmetric"


@pytest.mark.asyncio
async def test_ref_lists_equivalent_order_insensitive(
    client: AsyncClient, auth_headers: dict,
):
    """target_templates / target_terminologies are semantically sets, not
    sequences. Order should not affect equality."""
    from wip_auth.resolve import resolve_entity_id
    canonical = await resolve_entity_id("DOC_STATUS", "terminology", "wip")
    canonical_gender = await resolve_entity_id("GENDER", "terminology", "wip")

    # Same set, different order, mixed value/UUID forms
    assert await TemplateService._ref_lists_equivalent(
        ["DOC_STATUS", canonical_gender],
        [canonical, "GENDER"],
        "terminology",
        "wip",
    )


@pytest.mark.asyncio
async def test_ref_lists_equivalent_none_and_empty_are_equal(
    client: AsyncClient, auth_headers: dict,
):
    """None and [] both mean 'no constraint' — must compare equal."""
    assert await TemplateService._ref_lists_equivalent(
        None, [], "terminology", "wip",
    )
    assert await TemplateService._ref_lists_equivalent(
        [], None, "terminology", "wip",
    )
    assert await TemplateService._ref_lists_equivalent(
        None, None, "terminology", "wip",
    )


@pytest.mark.asyncio
async def test_refs_equivalent_one_none_one_set_unequal(
    client: AsyncClient, auth_headers: dict,
):
    """None and a real reference are NOT equal — the comparator must not
    silently treat 'no ref' and 'some ref' as the same."""
    assert not await TemplateService._refs_equivalent(
        None, "DOC_STATUS", "terminology", "wip",
    )
    assert not await TemplateService._refs_equivalent(
        "DOC_STATUS", None, "terminology", "wip",
    )


# ---------------------------------------------------------------------------
# Template-level relationship-refs (source_templates / target_templates)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relationship_refs_byte_equal_short_circuit(
    client: AsyncClient, auth_headers: dict,
):
    """When source/target_templates byte-match on both sides, the
    comparator short-circuits without resolving."""
    existing = Template(
        namespace="wip", value="EDGE_TEST", label="Edge",
        template_id="tpl-x", version=1, status="active",
        fields=[], identity_fields=[],
        source_templates=["A_TEMPLATE", "B_TEMPLATE"],
        target_templates=["C_TEMPLATE"],
    )
    proposed = CreateTemplateRequest(
        namespace="wip", value="EDGE_TEST", label="Edge",
        identity_fields=[], fields=[],
        source_templates=["A_TEMPLATE", "B_TEMPLATE"],
        target_templates=["C_TEMPLATE"],
    )
    # Trivial-equal short-circuit: even refs that don't exist in Registry
    # produce a None diff because set-equality wins before resolution runs.
    diff = await TemplateService._compare_template_relationship_refs(
        existing, proposed, namespace="wip",
    )
    assert diff is None
