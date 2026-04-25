"""Tests for the `usage: relationship` template annotation (Phase 1).

Phase 1 only adds the template-model layer: structural validation that a
relationship template has the required shape (source/target_templates +
source_ref/target_ref reference fields). Document-level validation that
the actual referenced documents exist comes in Phase 2.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/template-store"


async def _post_template(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    """POST a single template; return the per-item BulkResultItem."""
    resp = await client.post(
        f"{API}/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert resp.status_code == 200
    data = resp.json()
    return data["results"][0]


def _entity_template(value: str = "PERSON") -> dict:
    """Minimal entity template payload for endpoint fixtures."""
    return {
        "namespace": "wip",
        "value": value,
        "label": value.title(),
        "fields": [
            {"name": "id", "type": "string", "label": "Id", "mandatory": True},
        ],
    }


async def _ensure_endpoint_templates(
    client: AsyncClient,
    auth_headers: dict,
    *values: str,
) -> None:
    """Create entity templates that a relationship template will reference.

    The platform's existing reference-field validation (Phase 0) resolves
    `target_templates` against the Registry at template-create time, so
    EXPERIMENT and MOLECULE must exist before a relationship template
    that points at them can be created. Phase 1 doesn't change that.
    """
    for value in values:
        result = await _post_template(client, auth_headers, _entity_template(value))
        # 'created' is the happy path; 'error' with "already exists" is fine
        # if a previous test in the same DB created it.
        if result["status"] == "error" and "already exists" not in (result.get("error") or ""):
            raise AssertionError(f"Failed to create endpoint template {value}: {result}")


def _relationship_template(
    *,
    value: str = "EXPERIMENT_INPUT",
    source_templates: list[str] | None = None,
    target_templates: list[str] | None = None,
    extra_fields: list[dict] | None = None,
    omit_source_ref: bool = False,
    omit_target_ref: bool = False,
    source_ref_targets: list[str] | None = None,
    target_ref_targets: list[str] | None = None,
    source_ref_type: str = "document",
) -> dict:
    """Build a relationship-template payload with sensible defaults.

    Each `omit_*` / override knob lets a test poke a single
    constraint without re-declaring the whole payload.
    """
    src_templates = source_templates if source_templates is not None else ["EXPERIMENT"]
    tgt_templates = target_templates if target_templates is not None else ["MOLECULE"]

    fields: list[dict] = []
    if not omit_source_ref:
        fields.append({
            "name": "source_ref",
            "type": "reference",
            "label": "Source Ref",
            "reference_type": source_ref_type,
            "target_templates": (
                source_ref_targets if source_ref_targets is not None else src_templates
            ),
            "mandatory": True,
        })
    if not omit_target_ref:
        fields.append({
            "name": "target_ref",
            "type": "reference",
            "label": "Target Ref",
            "reference_type": "document",
            "target_templates": (
                target_ref_targets if target_ref_targets is not None else tgt_templates
            ),
            "mandatory": True,
        })
    if extra_fields:
        fields.extend(extra_fields)

    return {
        "namespace": "wip",
        "value": value,
        "label": value.title(),
        "usage": "relationship",
        "source_templates": src_templates,
        "target_templates": tgt_templates,
        "fields": fields,
    }


# =============================================================================
# Defaults / back-compat
# =============================================================================


@pytest.mark.asyncio
async def test_default_usage_is_entity(client: AsyncClient, auth_headers: dict):
    """A template without an explicit `usage` field defaults to 'entity'."""
    result = await _post_template(client, auth_headers, _entity_template("DEFAULT_USAGE_PERSON"))
    assert result["status"] == "created", result

    # Round-trip via list to confirm the persisted usage.
    resp = await client.get(
        f"{API}/templates/by-value/DEFAULT_USAGE_PERSON?namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"] == "entity"
    assert body["versioned"] is True
    assert body["source_templates"] == []
    assert body["target_templates"] == []


@pytest.mark.asyncio
async def test_entity_template_with_source_templates_rejected(
    client: AsyncClient, auth_headers: dict,
):
    """source_templates only makes sense for usage=relationship."""
    payload = _entity_template("WRONG_USAGE")
    payload["source_templates"] = ["PERSON"]
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "source_templates" in result["error"]
    assert "relationship" in result["error"]


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.asyncio
async def test_create_relationship_template_happy_path(
    client: AsyncClient, auth_headers: dict,
):
    """Valid relationship template round-trips with all new fields preserved."""
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    payload = _relationship_template(
        extra_fields=[
            {"name": "role", "type": "string", "label": "Role"},
            {"name": "quantity", "type": "string", "label": "Quantity"},
        ],
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "created", result

    resp = await client.get(
        f"{API}/templates/by-value/EXPERIMENT_INPUT?namespace=wip",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"] == "relationship"
    assert body["source_templates"] == ["EXPERIMENT"]
    assert body["target_templates"] == ["MOLECULE"]
    assert body["versioned"] is True


@pytest.mark.asyncio
async def test_relationship_template_with_versioned_false(
    client: AsyncClient, auth_headers: dict,
):
    """`versioned: false` is honoured at create time."""
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    payload = _relationship_template(value="LATEST_ONLY_REL")
    payload["versioned"] = False
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "created", result

    resp = await client.get(
        f"{API}/templates/by-value/LATEST_ONLY_REL?namespace=wip",
        headers=auth_headers,
    )
    assert resp.json()["versioned"] is False


# =============================================================================
# Structural rejections
# =============================================================================


@pytest.mark.asyncio
async def test_relationship_missing_source_templates_rejected(
    client: AsyncClient, auth_headers: dict,
):
    payload = _relationship_template(value="REL_NO_SRC", source_templates=[])
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "source_templates" in result["error"]


@pytest.mark.asyncio
async def test_relationship_missing_target_templates_rejected(
    client: AsyncClient, auth_headers: dict,
):
    payload = _relationship_template(value="REL_NO_TGT", target_templates=[])
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "target_templates" in result["error"]


@pytest.mark.asyncio
async def test_relationship_missing_source_ref_field_rejected(
    client: AsyncClient, auth_headers: dict,
):
    payload = _relationship_template(value="REL_NO_SRC_REF", omit_source_ref=True)
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "source_ref" in result["error"]


@pytest.mark.asyncio
async def test_relationship_missing_target_ref_field_rejected(
    client: AsyncClient, auth_headers: dict,
):
    payload = _relationship_template(value="REL_NO_TGT_REF", omit_target_ref=True)
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "target_ref" in result["error"]


@pytest.mark.asyncio
async def test_relationship_source_ref_must_be_document_type(
    client: AsyncClient, auth_headers: dict,
):
    """source_ref must have reference_type='document', not term/template/etc."""
    payload = _relationship_template(value="REL_TERM_REF", source_ref_type="term")
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "source_ref" in result["error"]
    assert "document" in result["error"]


@pytest.mark.asyncio
async def test_relationship_field_targets_must_match_template_level(
    client: AsyncClient, auth_headers: dict,
):
    """The source_ref field's target_templates must equal source_templates."""
    payload = _relationship_template(
        value="REL_MISMATCH",
        source_templates=["EXPERIMENT", "ASSAY"],
        source_ref_targets=["EXPERIMENT"],  # missing ASSAY
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "error", result
    assert "target_templates" in result["error"]
    assert "source_ref" in result["error"]


# =============================================================================
# Immutability of usage / versioned across versions
# =============================================================================


@pytest.mark.asyncio
async def test_usage_and_versioned_preserved_across_update(
    client: AsyncClient, auth_headers: dict,
):
    """Updating a relationship template (which creates a new version)
    must preserve usage / versioned / source_templates / target_templates
    from the original — they are immutable after creation."""
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    payload = _relationship_template(value="REL_IMMUT")
    payload["versioned"] = False
    create_result = await _post_template(client, auth_headers, payload)
    assert create_result["status"] == "created"
    template_id = create_result["id"]

    # Update only the label — should bump version and preserve all
    # immutable template-level fields.
    update_resp = await client.put(
        f"{API}/templates",
        headers=auth_headers,
        json=[{"template_id": template_id, "label": "Renamed Edge"}],
    )
    assert update_resp.status_code == 200
    update_data = update_resp.json()
    assert update_data["succeeded"] == 1, update_data

    # Read back the latest version and confirm immutables.
    latest = await client.get(
        f"{API}/templates/by-value/REL_IMMUT?namespace=wip",
        headers=auth_headers,
    )
    body = latest.json()
    assert body["label"] == "Renamed Edge"
    assert body["usage"] == "relationship"
    assert body["versioned"] is False
    assert body["source_templates"] == ["EXPERIMENT"]
    assert body["target_templates"] == ["MOLECULE"]
    assert body["version"] >= 2  # new version was created
