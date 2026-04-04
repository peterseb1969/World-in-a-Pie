"""Tests for template draft mode and activation flow."""

from httpx import AsyncClient
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_one(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    """Create a single template via the bulk-first POST and return the
    BulkResponse JSON.  Asserts 200 and succeeded == 1."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1, f"Create failed: {data}"
    return data


async def _create_one_id(client: AsyncClient, auth_headers: dict, payload: dict) -> str:
    """Create a single template and return its template_id."""
    data = await _create_one(client, auth_headers, payload)
    return data["results"][0]["id"]


# ---------------------------------------------------------------------------
# Tests: Draft Creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_draft_template(client: AsyncClient, auth_headers: dict):
    """Test creating a template with status='draft'."""
    data = await _create_one(client, auth_headers, {
        "namespace": "wip",
        "value": "DRAFT_BASIC",
        "label": "Draft Basic Template",
        "status": "draft",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })
    result = data["results"][0]
    assert result["status"] == "created"
    assert result["id"]  # Real Registry assigns the ID format

    # Verify the template is stored with draft status
    get_resp = await client.get(
        f"/api/template-store/templates/{result['id']}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    full = get_resp.json()
    assert full["status"] == "draft"


@pytest.mark.asyncio
async def test_draft_template_skips_reference_validation(client: AsyncClient, auth_headers: dict):
    """Test that draft templates skip reference validation at creation time.

    A draft template can reference a non-existent parent or terminology
    without errors. Validation is deferred to activation.
    """
    # This would fail for an active template because extends target does not exist
    data = await _create_one(client, auth_headers, {
        "namespace": "wip",
        "value": "DRAFT_BAD_EXTENDS",
        "label": "Draft Bad Extends",
        "status": "draft",
        "extends": "NONEXISTENT_PARENT",
        "fields": [
            {
                "name": "field1",
                "label": "Field 1",
                "type": "term",
                "terminology_ref": "NONEXISTENT_TERMINOLOGY",
            },
        ],
    })
    result = data["results"][0]
    assert result["status"] == "created"


@pytest.mark.asyncio
async def test_draft_templates_not_listed_as_active(client: AsyncClient, auth_headers: dict):
    """Test that draft templates are visible when filtering by status=draft
    and excluded when filtering by status=active."""
    await _create_one(client, auth_headers, {
        "namespace": "wip",
        "value": "DRAFT_FILTER",
        "label": "Draft Filter",
        "status": "draft",
        "fields": [],
    })
    await _create_one(client, auth_headers, {
        "namespace": "wip",
        "value": "ACTIVE_FILTER",
        "label": "Active Filter",
        "fields": [],
    })

    # Filter by draft
    resp_draft = await client.get(
        "/api/template-store/templates?status=draft",
        headers=auth_headers,
    )
    assert resp_draft.status_code == 200
    draft_data = resp_draft.json()
    assert draft_data["total"] == 1
    assert draft_data["items"][0]["value"] == "DRAFT_FILTER"

    # Filter by active
    resp_active = await client.get(
        "/api/template-store/templates?status=active",
        headers=auth_headers,
    )
    assert resp_active.status_code == 200
    active_data = resp_active.json()
    assert active_data["total"] == 1
    assert active_data["items"][0]["value"] == "ACTIVE_FILTER"


# ---------------------------------------------------------------------------
# Tests: Activate Single Draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_single_draft_template(client: AsyncClient, auth_headers: dict):
    """Test activating a single draft template with valid references."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ACTIVATE_SINGLE",
        "label": "Activate Single",
        "status": "draft",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Activate the draft
    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 1
    assert template_id in data["activated"]
    assert len(data["errors"]) == 0

    # Verify the template is now active
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_activate_draft_with_valid_terminology_ref(client: AsyncClient, auth_headers: dict):
    """Test activating a draft template that has valid terminology references."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ACTIVATE_TERM_REF",
        "label": "Activate Term Ref",
        "status": "draft",
        "fields": [
            {
                "name": "gender",
                "label": "Gender",
                "type": "term",
                "terminology_ref": "GENDER",  # Mocked to exist
            },
        ],
    })

    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 1
    assert len(data["errors"]) == 0


# ---------------------------------------------------------------------------
# Tests: Cascading Activation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_cascading_two_drafts(client: AsyncClient, auth_headers: dict):
    """Test that activating a draft that references another draft activates both.

    Create two draft templates where draft A has a field referencing draft B
    by value. Activating A should cascade and activate B too.
    """
    # Create draft B first (referenced by A)
    draft_b_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "CASCADE_B",
        "label": "Cascade B",
        "status": "draft",
        "fields": [
            {"name": "street", "label": "Street", "type": "string"},
        ],
    })

    # Create draft A that references draft B via template_ref
    draft_a_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "CASCADE_A",
        "label": "Cascade A",
        "status": "draft",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
            {
                "name": "address",
                "label": "Address",
                "type": "object",
                "template_ref": "CASCADE_B",
            },
        ],
    })

    # Activate A -- should cascade to B
    resp = await client.post(
        f"/api/template-store/templates/{draft_a_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 2
    assert draft_a_id in data["activated"]
    assert draft_b_id in data["activated"]
    assert len(data["errors"]) == 0

    # Verify both templates are now active
    for tid in [draft_a_id, draft_b_id]:
        get_resp = await client.get(
            f"/api/template-store/templates/{tid}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_activate_cascading_with_extends(client: AsyncClient, auth_headers: dict):
    """Test cascading activation through extends (inheritance) references."""
    # Create draft parent
    parent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "CASCADE_PARENT",
        "label": "Cascade Parent",
        "status": "draft",
        "fields": [
            {"name": "base_field", "label": "Base Field", "type": "string"},
        ],
    })

    # Create draft child that extends draft parent (by value)
    child_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "CASCADE_CHILD",
        "label": "Cascade Child",
        "status": "draft",
        "extends": "CASCADE_PARENT",
        "fields": [
            {"name": "child_field", "label": "Child Field", "type": "string"},
        ],
    })

    # Activate child -- should cascade to parent
    resp = await client.post(
        f"/api/template-store/templates/{child_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 2
    assert child_id in data["activated"]
    assert parent_id in data["activated"]
    assert len(data["errors"]) == 0


@pytest.mark.asyncio
async def test_activate_cascading_does_not_reactivate_already_active(
    client: AsyncClient, auth_headers: dict
):
    """Test that cascading activation does not include templates that are
    already active. Only drafts are collected in the activation set."""
    # Create an active template
    active_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ALREADY_ACTIVE",
        "label": "Already Active",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
        ],
    })

    # Create a draft that references the active template
    draft_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DRAFT_REFS_ACTIVE",
        "label": "Draft Refs Active",
        "status": "draft",
        "fields": [
            {
                "name": "nested",
                "label": "Nested",
                "type": "object",
                "template_ref": active_id,
            },
        ],
    })

    # Activate draft -- should only activate the draft, not the already-active one
    resp = await client.post(
        f"/api/template-store/templates/{draft_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 1
    assert draft_id in data["activated"]
    assert active_id not in data["activated"]


# ---------------------------------------------------------------------------
# Tests: Dry-Run Mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_dry_run_preview(client: AsyncClient, auth_headers: dict):
    """Test dry_run=true returns a preview without making changes."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DRY_RUN",
        "label": "Dry Run",
        "status": "draft",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Dry run
    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip", "dry_run": "true"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Preview results: total_activated is 0 (nothing changed)
    assert data["total_activated"] == 0
    assert len(data["activated"]) == 0
    assert len(data["errors"]) == 0

    # activation_details should show what would be activated
    assert len(data["activation_details"]) == 1
    assert data["activation_details"][0]["template_id"] == template_id
    assert data["activation_details"][0]["status"] == "would_activate"

    # Verify the template is still draft
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_activate_dry_run_cascading(client: AsyncClient, auth_headers: dict):
    """Test dry_run with cascading shows all templates that would be activated."""
    draft_b_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DRYRUN_B",
        "label": "Dry Run B",
        "status": "draft",
        "fields": [
            {"name": "b_field", "label": "B Field", "type": "string"},
        ],
    })

    draft_a_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DRYRUN_A",
        "label": "Dry Run A",
        "status": "draft",
        "fields": [
            {
                "name": "ref",
                "label": "Ref",
                "type": "object",
                "template_ref": "DRYRUN_B",
            },
        ],
    })

    # Dry run on A
    resp = await client.post(
        f"/api/template-store/templates/{draft_a_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip", "dry_run": "true"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 0
    assert len(data["activation_details"]) == 2

    detail_ids = {d["template_id"] for d in data["activation_details"]}
    assert draft_a_id in detail_ids
    assert draft_b_id in detail_ids

    for detail in data["activation_details"]:
        assert detail["status"] == "would_activate"

    # Verify both are still draft
    for tid in [draft_a_id, draft_b_id]:
        get_resp = await client.get(
            f"/api/template-store/templates/{tid}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "draft"


# ---------------------------------------------------------------------------
# Tests: Activate Non-Draft (Should Fail)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_non_draft_fails(client: AsyncClient, auth_headers: dict):
    """Test that activating a template that is already active returns 400."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ALREADY_ACTIVE_FAIL",
        "label": "Already Active Fail",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Template is active; trying to activate should fail
    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "not 'draft'" in detail or "not draft" in detail.lower()


@pytest.mark.asyncio
async def test_activate_nonexistent_template_fails(client: AsyncClient, auth_headers: dict):
    """Test that activating a nonexistent template returns an error."""
    resp = await client.post(
        "/api/template-store/templates/TPL-999999/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    # With real resolution, unknown IDs fail at resolution (404) or service (400)
    assert resp.status_code in (400, 404)
    assert "not found" in resp.json()["detail"].lower() or "resolve" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: Activation Failure on Invalid References
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_fails_with_invalid_terminology_ref(
    client: AsyncClient, auth_headers: dict
):
    """Test that activation fails when a draft has an invalid terminology reference."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ACTIVATE_BAD_TERM",
        "label": "Activate Bad Term",
        "status": "draft",
        "fields": [
            {
                "name": "field1",
                "label": "Field 1",
                "type": "term",
                "terminology_ref": "NONEXISTENT_TERMINOLOGY",
            },
        ],
    })

    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 0
    assert len(data["errors"]) > 0
    assert any("not found" in e["message"].lower() or "terminology" in e["message"].lower()
               for e in data["errors"])

    # Verify template is still draft
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_activate_fails_with_invalid_template_ref(
    client: AsyncClient, auth_headers: dict
):
    """Test that activation fails when a draft has an invalid template reference."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ACTIVATE_BAD_TPLREF",
        "label": "Activate Bad Template Ref",
        "status": "draft",
        "fields": [
            {
                "name": "nested",
                "label": "Nested",
                "type": "object",
                "template_ref": "NONEXISTENT_TEMPLATE",
            },
        ],
    })

    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 0
    assert len(data["errors"]) > 0
    assert any("not found" in e["message"].lower() for e in data["errors"])

    # Verify template is still draft (all-or-nothing)
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_activate_all_or_nothing_on_cascading_failure(
    client: AsyncClient, auth_headers: dict
):
    """Test all-or-nothing behavior: if one template in the cascade has
    invalid references, NONE are activated."""
    # Create draft B with an invalid terminology ref
    draft_b_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "AON_B",
        "label": "AON B",
        "status": "draft",
        "fields": [
            {
                "name": "bad_field",
                "label": "Bad Field",
                "type": "term",
                "terminology_ref": "NONEXISTENT_TERM",
            },
        ],
    })

    # Create draft A that references draft B
    draft_a_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "AON_A",
        "label": "AON A",
        "status": "draft",
        "fields": [
            {
                "name": "ref",
                "label": "Ref",
                "type": "object",
                "template_ref": "AON_B",
            },
        ],
    })

    # Try to activate A -- should fail because B has invalid ref
    resp = await client.post(
        f"/api/template-store/templates/{draft_a_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 0
    assert len(data["errors"]) > 0

    # Both templates should still be draft
    for tid in [draft_a_id, draft_b_id]:
        get_resp = await client.get(
            f"/api/template-store/templates/{tid}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_activate_fails_with_invalid_extends_reference(
    client: AsyncClient, auth_headers: dict
):
    """Test that activation fails when a draft extends a non-existent template
    that is not another draft in the activation set."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ACTIVATE_BAD_EXTENDS",
        "label": "Activate Bad Extends",
        "status": "draft",
        "extends": "NONEXISTENT_PARENT",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
        ],
    })

    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_activated"] == 0
    assert len(data["errors"]) > 0
    assert any("extends" in e["field"].lower() or "parent" in e["message"].lower()
               for e in data["errors"])


@pytest.mark.asyncio
async def test_activation_details_show_status(client: AsyncClient, auth_headers: dict):
    """Test that activation_details include correct status for each template."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DETAIL_STATUS",
        "label": "Detail Status",
        "status": "draft",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Actual activation (not dry run)
    resp = await client.post(
        f"/api/template-store/templates/{template_id}/activate",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["activation_details"]) == 1
    assert data["activation_details"][0]["status"] == "activated"
    assert data["activation_details"][0]["template_id"] == template_id
    assert data["activation_details"][0]["value"] == "DETAIL_STATUS"
