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
    assert resp.status_code == 200, resp.text
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


async def _template_id(client: AsyncClient, auth_headers: dict, value: str) -> str:
    """The canonical template_id for a template value.

    An endpoint declaration entry's resolved half is a canonical id — callers
    write values, the write path fills the resolved half — so a test that
    asserts on the resolved side has to compare ids, not the value it
    submitted.
    """
    resp = await client.get(
        f"{API}/templates/by-value/{value}?namespace=wip", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["template_id"]


def _lookups(entries: list[dict]) -> list[str]:
    """The lookup halves of served endpoint entries — the anchors as submitted."""
    return [e["lookup_value"] for e in entries]


def _resolved(entries: list[dict]) -> list[str | None]:
    """The resolved halves of served endpoint entries — canonical ids."""
    return [e["resolved"] for e in entries]


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
    # Declared by value: the entry keeps the submitted anchor verbatim and
    # carries the canonical id the platform resolved it to.
    assert _lookups(body["source_templates"]) == ["EXPERIMENT"]
    assert _resolved(body["source_templates"]) == [
        await _template_id(client, auth_headers, "EXPERIMENT")
    ]
    assert _lookups(body["target_templates"]) == ["MOLECULE"]
    assert _resolved(body["target_templates"]) == [
        await _template_id(client, auth_headers, "MOLECULE")
    ]
    assert body["versioned"] is True


@pytest.mark.asyncio
async def test_relationship_template_with_versioned_false(
    client: AsyncClient, auth_headers: dict,
):
    """`versioned: false` is honoured at create time."""
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    payload = _relationship_template(value="LATEST_ONLY_REL")
    payload["versioned"] = False
    # versioned:false requires explicit identity_fields (CASE-478). The edge
    # identity is (source_ref, target_ref); it must be declared, not implied.
    payload["identity_fields"] = ["source_ref", "target_ref"]
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
async def test_include_subtypes_does_not_apply_to_edge_endpoints(
    client: AsyncClient, auth_headers: dict,
):
    """An edge type's endpoints are exactly the templates it declares.

    A caller may set include_subtypes on source_ref/target_ref — it is an
    ordinary reference field — but for an edge endpoint it is neither stored
    nor served. Without this, "subtypes are not admitted" would be an
    intention rather than a rule: the generic reference validator honours the
    flag it is given, so leaving it on the served field would silently widen
    the accepted endpoint set beyond the declaration.
    """
    await _ensure_endpoint_templates(
        client, auth_headers, "EXPERIMENT", "MOLECULE"
    )
    payload = _relationship_template(value="REL_SUBTYPES")
    for field in payload["fields"]:
        if field["name"] in ("source_ref", "target_ref"):
            field["include_subtypes"] = True

    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "created", result

    resp = await client.get(
        f"{API}/templates/by-value/REL_SUBTYPES?namespace=wip", headers=auth_headers
    )
    body = resp.json()
    for name in ("source_ref", "target_ref"):
        field = next(f for f in body["fields"] if f["name"] == name)
        assert not field["include_subtypes"], (name, field)


@pytest.mark.asyncio
async def test_endpoint_constraint_is_projected_not_supplied(
    client: AsyncClient, auth_headers: dict,
):
    """A caller-supplied endpoint constraint is ignored, not rejected.

    source_ref.target_templates used to be a second declaration that had to
    agree with the template-level lists, policed by a cross-copy check. It is
    now projected from the declaration when the template is served and
    discarded on write, so disagreement is not an error state — it is not
    representable. This is the stronger guarantee: an invariant that cannot be
    violated beats one that is checked.
    """
    await _ensure_endpoint_templates(
        client, auth_headers, "EXPERIMENT", "ASSAY", "MOLECULE"
    )
    payload = _relationship_template(
        value="REL_MISMATCH",
        source_templates=["EXPERIMENT", "ASSAY"],
        source_ref_targets=["EXPERIMENT"],  # deliberately disagrees — ignored
    )
    result = await _post_template(client, auth_headers, payload)
    assert result["status"] == "created", result

    resp = await client.get(
        f"{API}/templates/by-value/REL_MISMATCH?namespace=wip", headers=auth_headers
    )
    body = resp.json()
    declared = [
        await _template_id(client, auth_headers, "EXPERIMENT"),
        await _template_id(client, auth_headers, "ASSAY"),
    ]
    assert _resolved(body["source_templates"]) == declared
    assert _lookups(body["source_templates"]) == ["EXPERIMENT", "ASSAY"]
    # The served field follows the declaration's resolved halves, not what
    # the caller sent.
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert source_ref["target_templates"] == declared


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
    # versioned:false requires explicit identity_fields (CASE-478).
    payload["identity_fields"] = ["source_ref", "target_ref"]
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
    assert _resolved(body["source_templates"]) == [
        await _template_id(client, auth_headers, "EXPERIMENT")
    ]
    assert _lookups(body["source_templates"]) == ["EXPERIMENT"]
    assert _resolved(body["target_templates"]) == [
        await _template_id(client, auth_headers, "MOLECULE")
    ]
    assert body["version"] >= 2  # new version was created


# ---------------------------------------------------------------------------
# Endpoint-list equivalence resolves through the Registry
# ---------------------------------------------------------------------------


class TestEndpointListSynonymEquivalence:
    """Endpoint declarations compare by canonical entity, not by string:
    value-form and ID-form of the same template are equivalent (the universal
    synonym rule for reference comparisons). Each stored entry carries the
    submitted anchor verbatim in lookup_value and the canonical id in
    resolved, so mixed submissions — including archived edge types re-created
    by a backup import — land as the same declared identity."""

    @pytest.mark.asyncio
    async def test_mixed_value_and_id_forms_accepted(self, client, auth_headers):
        src = await _post_template(client, auth_headers, _entity_template("EQ_SRC"))
        tgt = await _post_template(client, auth_headers, _entity_template("EQ_TGT"))
        assert src["status"] == "created" and tgt["status"] == "created"

        payload = _relationship_template(
            value="EQ_LINK",
            source_templates=["EQ_SRC"],          # value form
            target_templates=["EQ_TGT"],          # value form
            source_ref_targets=[src["id"]],       # canonical-ID form
            target_ref_targets=[tgt["id"]],       # canonical-ID form
        )
        result = await _post_template(client, auth_headers, payload)
        assert result["status"] == "created", result

    @pytest.mark.asyncio
    async def test_every_accepted_form_keeps_its_anchor_and_resolves(self, client, auth_headers):
        """Value, canonical id and ns:VALUE each keep their submitted anchor
        and resolve to the same canonical id.

        Both halves of the entry are load-bearing: the lookup half is the
        submitted anchor kept verbatim (the synonym that survives a fresh
        restore's re-anchoring), the resolved half is the canonical id every
        comparing or rewriting site uses. Four shipped defects came from a
        single-slot form every consumer had to guess.
        """
        src = await _post_template(client, auth_headers, _entity_template("FORM_SRC"))
        tgt = await _post_template(client, auth_headers, _entity_template("FORM_TGT"))
        assert src["status"] == "created" and tgt["status"] == "created"

        for suffix, source_ref, target_ref in (
            ("VALUE", "FORM_SRC", "FORM_TGT"),
            ("ID", src["id"], tgt["id"]),
            ("QUALIFIED", "wip:FORM_SRC", "wip:FORM_TGT"),
        ):
            payload = _relationship_template(
                value=f"FORM_LINK_{suffix}",
                source_templates=[source_ref],
                target_templates=[target_ref],
                source_ref_targets=[source_ref],
                target_ref_targets=[target_ref],
            )
            result = await _post_template(client, auth_headers, payload)
            assert result["status"] == "created", result

            resp = await client.get(
                f"{API}/templates/by-value/FORM_LINK_{suffix}?namespace=wip",
                headers=auth_headers,
            )
            body = resp.json()
            assert _lookups(body["source_templates"]) == [source_ref], (suffix, body)
            assert _resolved(body["source_templates"]) == [src["id"]], (suffix, body)
            assert _lookups(body["target_templates"]) == [target_ref], (suffix, body)
            assert _resolved(body["target_templates"]) == [tgt["id"]], (suffix, body)

    @pytest.mark.asyncio
    async def test_caller_supplied_resolved_half_is_ignored(self, client, auth_headers):
        """The resolved half is server-owned: a full entry round-trips by its
        lookup, and a caller-pinned resolved id (stale, foreign, or plain
        wrong) is recomputed rather than stored."""
        src = await _post_template(client, auth_headers, _entity_template("OWN_SRC"))
        tgt = await _post_template(client, auth_headers, _entity_template("OWN_TGT"))
        assert src["status"] == "created" and tgt["status"] == "created"

        payload = _relationship_template(
            value="OWN_LINK",
            source_templates=[
                {"lookup_value": "OWN_SRC", "resolved": "019f0000-dead-7000-8000-000000000000"}
            ],
            target_templates=[{"lookup_value": "OWN_TGT"}],
            # The field-level constraint is a strings-only surface (and is
            # discarded on write — the projection is derived); the entry
            # shape belongs to the template-level declaration only.
            source_ref_targets=["OWN_SRC"],
            target_ref_targets=["OWN_TGT"],
        )
        result = await _post_template(client, auth_headers, payload)
        assert result["status"] == "created", result

        resp = await client.get(
            f"{API}/templates/by-value/OWN_LINK?namespace=wip", headers=auth_headers
        )
        body = resp.json()
        assert _resolved(body["source_templates"]) == [src["id"]], body
        assert _lookups(body["source_templates"]) == ["OWN_SRC"]
        assert _resolved(body["target_templates"]) == [tgt["id"]]

    @pytest.mark.asyncio
    async def test_genuinely_different_entities_still_rejected(self, client, auth_headers):
        for v in ("EQ_A", "EQ_B", "EQ_C"):
            await _post_template(client, auth_headers, _entity_template(v))

        payload = _relationship_template(
            value="EQ_BAD_LINK",
            source_templates=["EQ_A"],
            target_templates=["EQ_B"],
            source_ref_targets=["EQ_C"],  # names a DIFFERENT entity — ignored
        )
        result = await _post_template(client, auth_headers, payload)
        assert result["status"] == "created", result

        # EQ_C never becomes an allowed endpoint: the declaration is the only
        # place endpoints are declared, so a wrong field value cannot widen it.
        resp = await client.get(
            f"{API}/templates/by-value/EQ_BAD_LINK?namespace=wip", headers=auth_headers
        )
        body = resp.json()
        eq_c = await _template_id(client, auth_headers, "EQ_C")
        source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
        assert eq_c not in source_ref["target_templates"]
        assert source_ref["target_templates"] == [
            await _template_id(client, auth_headers, "EQ_A")
        ]


# ---------------------------------------------------------------------------
# Legacy single-string rows hydrate tolerantly and heal on the next write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_string_endpoints_hydrate_and_heal(client, auth_headers):
    """Rows written before the two-half shape hold bare strings.

    They must (a) read as lookup-only entries — never 500 a read path over
    stored history — and (b) heal in place on the next write that touches
    the declaration (here: an idempotent widen fills the resolved halves).
    """
    from template_store.models.template import Template

    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    created = await _post_template(
        client, auth_headers, _relationship_template(value="LEGACY_REL")
    )
    assert created["status"] == "created", created

    # Rewrite the stored row to the pre-entry shape, bypassing the API —
    # exactly what a row written by an older build looks like in Mongo.
    coll = Template.get_motor_collection()
    await coll.update_one(
        {"template_id": created["id"], "version": created["version"]},
        {"$set": {
            "source_templates": ["EXPERIMENT"],
            "target_templates": ["MOLECULE"],
        }},
    )

    resp = await client.get(
        f"{API}/templates/by-value/LEGACY_REL?namespace=wip", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _lookups(body["source_templates"]) == ["EXPERIMENT"]
    assert _resolved(body["source_templates"]) == [None]
    # The projection falls back to the lookup half, so enforcement input
    # stays non-empty even for a legacy row.
    source_ref = next(f for f in body["fields"] if f["name"] == "source_ref")
    assert source_ref["target_templates"] == ["EXPERIMENT"]

    # An idempotent widen heals the resolved halves in place (same version).
    widen = await client.post(
        f"{API}/templates/{created['id']}/endpoints",
        headers=auth_headers,
        params={"namespace": "wip"},
        json={"add_source_templates": ["EXPERIMENT"], "add_target_templates": []},
    )
    assert widen.status_code == 200, widen.text
    healed = widen.json()
    assert healed["version"] == created["version"]
    assert _lookups(healed["source_templates"]) == ["EXPERIMENT"]
    assert _resolved(healed["source_templates"]) == [
        await _template_id(client, auth_headers, "EXPERIMENT")
    ]
    # target side was not addressed by the widen call but rides the same
    # save; it resolves lazily on ITS next touch, so it may stay lookup-only.
    assert _lookups(healed["target_templates"]) == ["MOLECULE"]
