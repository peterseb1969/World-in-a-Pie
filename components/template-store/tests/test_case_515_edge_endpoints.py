"""CASE-515 — additive edge-type endpoint widening (POST /templates/{id}/endpoints).

An edge type's allowed endpoints (the template-level two-half entries, from
which the source_ref/target_ref field constraint is projected at serve) were
frozen — the only way to add a legal endpoint was delete+recreate, which
strands every existing edge. `add_edge_type_endpoints` widens the declaration
IN PLACE, additively:

- adds an endpoint entry to the declaration, on the same version (no new
  version); the served field constraint follows because it is projected;
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
    _lookups,
    _post_template,
    _relationship_template,
    _resolved,
    _template_id,
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
    # The addition keeps its submitted form as the lookup half and carries
    # the canonical id in the resolved half.
    compound_id = await _template_id(client, auth_headers, "COMPOUND")
    assert compound_id in _resolved(body["target_templates"])
    assert "COMPOUND" in _lookups(body["target_templates"])
    assert len(body["target_templates"]) == 2
    assert len(body["source_templates"]) == 1
    assert body["version"] == edge["version"]  # no new version

    # mirror invariant: the target_ref field's target_templates widened too.
    target_ref = next(f for f in body["fields"] if f["name"] == "target_ref")
    assert compound_id in target_ref["target_templates"]
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert compound_id not in (source_ref["target_templates"] or [])


@pytest.mark.asyncio
async def test_add_source_endpoint_widens_source_and_its_field(client: AsyncClient, auth_headers: dict):
    edge = await _create_edge_type(client, auth_headers, "EDGE_515_SRC")
    assay = await _post_template(client, auth_headers, _entity_template("ASSAY"))
    assert assay["status"] == "created", assay

    resp = await _widen(client, auth_headers, edge["id"], source=["ASSAY"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assay_id = await _template_id(client, auth_headers, "ASSAY")
    assert assay_id in _resolved(body["source_templates"])
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert assay_id in source_ref["target_templates"]


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
    assert sorted(_resolved(v2["target_templates"]), key=str) == sorted(
        _resolved(v1["target_templates"]), key=str
    )
    assert _lookups(v2["target_templates"]) == _lookups(v1["target_templates"])


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
    # EXPERIMENT (source) and MOLECULE (target) are the ORIGINAL endpoints.
    # Re-adding them by VALUE must not grow the lists: dedup keys on the
    # resolved half, so a value-form re-add matches the entry already present
    # rather than being appended as a second copy.
    resp = await _widen(
        client, auth_headers, edge["id"], source=["EXPERIMENT"], target=["MOLECULE"]
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The core guard: the template-level lists did NOT grow (the bug appended a
    # second value↔UUID copy). They stay length 1, anchors intact.
    assert _resolved(body["target_templates"]) == [
        await _template_id(client, auth_headers, "MOLECULE")
    ], body["target_templates"]
    assert _lookups(body["target_templates"]) == ["MOLECULE"]
    assert _resolved(body["source_templates"]) == [
        await _template_id(client, auth_headers, "EXPERIMENT")
    ], body["source_templates"]
    assert body["version"] == edge["version"]  # true no-op, no new version
    # The served field constraint (projected from the declaration) also did
    # not grow — no UUID duplicate appended.
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
