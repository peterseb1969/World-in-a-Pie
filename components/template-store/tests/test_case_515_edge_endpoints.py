"""CASE-515 — additive edge-type endpoint widening (POST /templates/{id}/endpoints).

An edge type's allowed endpoints (template-level source_templates/target_templates,
mirror-locked to the source_ref/target_ref field target_templates) were frozen —
the only way to add a legal endpoint was delete+recreate, which strands every
existing edge. `add_edge_type_endpoints` widens them IN PLACE, additively:

- adds an endpoint template to both the template-level list and the mirrored
  field target_templates, on the same version (no new version);
- referential integrity preserved (the new endpoint must be a real template);
- idempotent; rejects non-existent endpoints and non-relationship templates.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from .test_relationship_templates import (
    API,
    _ensure_endpoint_templates,
    _entity_template,
    _post_template,
    _relationship_template,
)


async def _create_edge_type(client, auth_headers, value="EDGE_515") -> dict:
    """EXPERIMENT --(value)--> MOLECULE edge type; returns its BulkResultItem."""
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    item = await _post_template(
        client, auth_headers,
        _relationship_template(value=value, source_templates=["EXPERIMENT"],
                               target_templates=["MOLECULE"]),
    )
    assert item["status"] in ("created", "updated"), item
    return item


async def _widen(client, auth_headers, template_id, *, source=None, target=None):
    return await client.post(
        f"{API}/templates/{template_id}/endpoints",
        headers=auth_headers,
        params={"namespace": "wip"},
        json={"add_source_templates": source or [], "add_target_templates": target or []},
    )


@pytest.mark.asyncio
async def test_add_target_endpoint_widens_in_place_with_field_mirror(client: AsyncClient, auth_headers: dict):
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_WIDEN")
    # COMPOUND is a brand-new allowed target, not in the original set.
    await _post_template(client, auth_headers, _entity_template("COMPOUND"))

    resp = await _widen(client, auth_headers, edge["id"], target=["COMPOUND"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # template-level list widened, in place (same version), source untouched.
    # New endpoints store value-form to match the seed convention (CASE-515 #4).
    assert "COMPOUND" in body["target_templates"]
    assert len(body["target_templates"]) == 2
    assert len(body["source_templates"]) == 1
    assert body["version"] == edge["version"]  # no new version

    # mirror invariant: the target_ref field's target_templates widened too.
    target_ref = next(f for f in body["fields"] if f["name"] == "target_ref")
    assert "COMPOUND" in target_ref["target_templates"]
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert "COMPOUND" not in (source_ref["target_templates"] or [])


@pytest.mark.asyncio
async def test_add_source_endpoint_widens_source_and_its_field(client: AsyncClient, auth_headers: dict):
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_SRC")
    assay = await _post_template(client, auth_headers, _entity_template("ASSAY"))
    assert assay["status"] == "created", assay

    resp = await _widen(client, auth_headers, edge["id"], source=["ASSAY"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "ASSAY" in body["source_templates"]
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert "ASSAY" in source_ref["target_templates"]


@pytest.mark.asyncio
async def test_add_endpoint_is_idempotent(client: AsyncClient, auth_headers: dict):
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_IDEM")
    await _post_template(client, auth_headers, _entity_template("COMPOUND"))

    first = await _widen(client, auth_headers, edge["id"], target=["COMPOUND"])
    assert first.status_code == 200
    v1 = first.json()
    # Re-adding the same endpoint is a no-op — same version, same set.
    second = await _widen(client, auth_headers, edge["id"], target=["COMPOUND"])
    assert second.status_code == 200
    v2 = second.json()
    assert v2["version"] == v1["version"]
    assert sorted(v2["target_templates"]) == sorted(v1["target_templates"])


@pytest.mark.asyncio
async def test_readd_value_form_endpoint_is_idempotent(client: AsyncClient, auth_headers: dict):
    """Re-adding an endpoint ALREADY present in value-form is a true no-op.

    CASE-515 defect (response #4, APP-KB): edge types store endpoints in
    value-form (the seed convention — "MOLECULE", not a UUID; create persists
    source/target_templates as submitted). The widen op resolved the incoming
    value to its canonical UUID and string-compared it against the stored
    value-form list, so an already-allowed endpoint never matched and was
    appended a SECOND time as its UUID — a value↔UUID duplicate, unremovable
    since removal is unsupported. The natural "is it deployed?" idempotent
    re-add was the trigger. The fix resolves BOTH sides through the Registry
    before dedup. This guards the regression the original idempotency test
    missed (it re-added a NEW endpoint, already UUID-form after the first add).
    """
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_VALUEFORM")
    # EXPERIMENT (source) and MOLECULE (target) are the ORIGINAL endpoints,
    # stored value-form. Re-adding them must not grow the lists or append a UUID.
    resp = await _widen(
        client, auth_headers, edge["id"], source=["EXPERIMENT"], target=["MOLECULE"]
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The core guard: the template-level lists did NOT grow (the bug appended a
    # second value↔UUID copy). They stay the original value-form, length 1.
    assert body["target_templates"] == ["MOLECULE"], body["target_templates"]
    assert body["source_templates"] == ["EXPERIMENT"], body["source_templates"]
    assert body["version"] == edge["version"]  # true no-op, no new version
    # The mirrored field lists also did not grow (no UUID duplicate appended).
    # Form note: create stores field-level target_templates UUID-form while the
    # template-level list is value-form, so assert no-growth, not exact form.
    target_ref = next(f for f in body["fields"] if f["name"] == "target_ref")
    assert len(target_ref["target_templates"]) == 1, target_ref["target_templates"]
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert len(source_ref["target_templates"]) == 1, source_ref["target_templates"]


@pytest.mark.asyncio
async def test_add_nonexistent_endpoint_rejected(client: AsyncClient, auth_headers: dict):
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_BADREF")
    resp = await _widen(client, auth_headers, edge["id"], target=["NO_SUCH_TEMPLATE_515"])
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_add_endpoint_on_entity_template_rejected(client: AsyncClient, auth_headers: dict):
    item = await _post_template(client, auth_headers, _entity_template("PLAIN_ENTITY_515"))
    resp = await _widen(client, auth_headers, item["id"], target=["MOLECULE"])
    assert resp.status_code == 400, resp.text
    assert "relationship" in resp.text.lower()


@pytest.mark.asyncio
async def test_empty_add_rejected(client: AsyncClient, auth_headers: dict):
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_EMPTY")
    resp = await _widen(client, auth_headers, edge["id"])
    assert resp.status_code == 400, resp.text
