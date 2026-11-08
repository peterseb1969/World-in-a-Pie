"""Two write-path gaps.

1. PATCH (and migrate) run the relationship constraints create runs: an edge
   whose source_ref is re-pointed into another namespace is refused with
   cross_namespace_relationship, as POST refuses it.
2. The outbound reference gate gets a template's or terminology's namespace
   from validation, which resolved it, instead of depending on the Registry
   placing the id: a retired template still resolves but is no longer placed,
   and a strict namespace's allow-list was skipped for it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from document_store.services.reference_validator import ReferenceValidationError, ReferenceValidator

from .conftest import SAMPLE_TEMPLATES
from .test_reference_gate_lookups import _FakeRegistry, _validator

API = "/api/document-store"


async def _create(client, auth_headers, template, data, namespace="wip") -> dict:
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=[{
        "namespace": namespace, "template_id": template, "data": data,
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _patch(client, auth_headers, doc_id, body) -> dict:
    resp = await client.patch(f"{API}/documents", headers=auth_headers,
                              json=[{"document_id": doc_id, "patch": body}])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.fixture
def edge_identity_without_source(monkeypatch):
    """An edge type whose identity does not include source_ref, so PATCH may
    change it (the platform's own edge types make it identity, which hides
    the gap)."""
    tpl = next(t for t in SAMPLE_TEMPLATES.values() if t.get("value") == "EXPERIMENT_INPUT")
    monkeypatch.setitem(tpl, "identity_fields", ["role"])
    return tpl


# ---------------------------------------------------------------------------
# 1. PATCH runs the relationship constraints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_cannot_move_an_edges_source_into_another_namespace(
    client: AsyncClient, auth_headers: dict, edge_identity_without_source,
):
    exp = await _create(client, auth_headers, "EXPERIMENT", {"experiment_id": "EXP-1142", "name": "e"})
    exp2 = await _create(client, auth_headers, "EXPERIMENT", {"experiment_id": "EXP-1142b", "name": "e2"})
    mol = await _create(client, auth_headers, "MOLECULE", {"molecule_id": "MOL-1142", "name": "m"})
    foreign = await _create(client, auth_headers, "EXPERIMENT",
                            {"experiment_id": "EXP-1142-OTHER"}, namespace="other-ns")
    if foreign["status"] != "created":
        pytest.skip(f"test fixture does not support namespace 'other-ns': {foreign}")
    edge = await _create(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp["document_id"], "target_ref": mol["document_id"], "role": "r-1142",
    })
    assert edge["status"] == "created", edge

    moved = await _patch(client, auth_headers, edge["document_id"],
                         {"source_ref": foreign["document_id"]})
    assert moved["status"] == "error", moved
    assert moved["error_code"] == "cross_namespace_relationship"
    assert "source_ref" in moved["error"]

    # Re-pointing to a source in the edge's own namespace is an ordinary update.
    ok = await _patch(client, auth_headers, edge["document_id"], {"source_ref": exp2["document_id"]})
    assert ok["status"] == "updated", ok


# ---------------------------------------------------------------------------
# 2. The outbound gate has the namespace of what validation resolved
# ---------------------------------------------------------------------------

STRICT = {"isolation_mode": "strict", "allowed_external_refs": []}
RETIRED_TEMPLATE = [{"field_path": "kind", "reference_type": "template",
                     "resolved": {"template_id": "T-retired"}}]


@pytest.mark.asyncio
async def test_a_referent_the_registry_no_longer_places_was_never_gated():
    # The Registry does not place a retired template: the gate let it through.
    reg = _FakeRegistry(STRICT, placed={})
    await _validator(reg).validate_document_references(
        document_namespace="appns", template_namespace="appns",
        document_references=RETIRED_TEMPLATE,
    )


@pytest.mark.asyncio
async def test_with_validations_namespace_the_strict_allow_list_applies():
    reg = _FakeRegistry(STRICT, placed={})
    with pytest.raises(ReferenceValidationError) as refused:
        await _validator(reg).validate_document_references(
            document_namespace="appns", template_namespace="appns",
            document_references=RETIRED_TEMPLATE,
            placements={"T-retired": "elsewhere"},
        )
    assert refused.value.violations[0]["namespace"] == "elsewhere"
    assert reg.lookups == []  # nothing left for the Registry to place


@pytest.mark.asyncio
async def test_template_and_terminology_resolution_record_their_namespace():
    from document_store.services.validation_service import ValidationResult, ValidationService

    service = ValidationService()
    result = ValidationResult()
    templates = AsyncMock()
    templates.get_template.return_value = {"template_id": "T-1", "value": "T", "version": 2,
                                           "namespace": "shared", "status": "inactive"}
    terminologies = AsyncMock()
    terminologies.terminology_exists.return_value = True
    terminologies.get_terminology.return_value = {"terminology_id": "TL-1", "value": "TL",
                                                  "namespace": "vocab"}
    with patch("document_store.services.validation_service.get_template_store_client",
               return_value=templates), \
         patch("document_store.services.validation_service.get_def_store_client",
               return_value=terminologies), \
         patch("document_store.services.validation_service.gate_refuses",
               AsyncMock(return_value=False)):
        assert await service._resolve_template_reference("T", result, "kind")
        assert await service._resolve_terminology_reference("TL", result, "vocab")
    assert result.referent_namespaces == {"T-1": "shared", "TL-1": "vocab"}


@pytest.mark.asyncio
async def test_create_hands_validations_namespaces_to_the_gate():
    from types import SimpleNamespace

    from document_store.services.document_service import DocumentService

    captured = {}

    class _Recorder(ReferenceValidator):
        async def validate_document_references(self, **kwargs):
            captured.update(kwargs)

    vr = SimpleNamespace(template_namespace="appns", term_references=[], file_references=[],
                         references=RETIRED_TEMPLATE, identity_values={},
                         referent_namespaces={"T-retired": "elsewhere"})
    with patch("document_store.services.document_service.get_reference_validator",
               return_value=_Recorder()):
        await DocumentService()._check_new_references("appns", "TPL", vr, placements={"x": "y"})
    assert captured["placements"] == {"x": "y", "T-retired": "elsewhere"}


@pytest.mark.asyncio
async def test_migrate_refuses_an_edge_whose_source_lies_in_another_namespace(
    client: AsyncClient, auth_headers: dict,
):
    """An edge carrying a foreign source (written before the rule, or landed
    by a restore) is not re-pinned as if it were valid."""
    import copy

    from document_store.models.document import Document

    from .conftest import VERSIONED_TEMPLATE_OVERRIDES

    exp = await _create(client, auth_headers, "EXPERIMENT", {"experiment_id": "EXP-1142m", "name": "e"})
    mol = await _create(client, auth_headers, "MOLECULE", {"molecule_id": "MOL-1142m", "name": "m"})
    foreign = await _create(client, auth_headers, "EXPERIMENT",
                            {"experiment_id": "EXP-1142m-OTHER"}, namespace="other-ns")
    if foreign["status"] != "created":
        pytest.skip(f"test fixture does not support namespace 'other-ns': {foreign}")
    edge = await _create(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp["document_id"], "target_ref": mol["document_id"], "role": "m",
    })
    assert edge["status"] == "created", edge
    # Plant the foreign source the API now refuses.
    row = await Document.find_one({"document_id": edge["document_id"]})
    await row.set({"data.source_ref": foreign["document_id"]})

    tpl = next(t for t in SAMPLE_TEMPLATES.values() if t.get("value") == "EXPERIMENT_INPUT")
    v2 = copy.deepcopy(tpl)
    v2["version"] = 2
    v2["fields"].append({"name": "note", "label": "Note", "type": "string", "mandatory": False})
    VERSIONED_TEMPLATE_OVERRIDES[(tpl["template_id"], 2)] = v2
    try:
        resp = await client.post(f"{API}/documents/migrate", params={"namespace": "wip"},
                                 headers=auth_headers, json={
            "template_id": "EXPERIMENT_INPUT", "from_version": 1, "to_version": 2, "dry_run": True,
        })
    finally:
        VERSIONED_TEMPLATE_OVERRIDES.clear()
    assert resp.status_code == 200, resp.text
    [item] = [r for r in resp.json()["results"] if r["document_id"] == edge["document_id"]]
    assert item["status"] == "error" and item["error_code"] == "cross_namespace_relationship", item
