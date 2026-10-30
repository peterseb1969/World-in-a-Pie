"""CASE-566 — document→document references participate in namespace isolation.

The strict/open + allowed_external_refs rules were enforced for term, file,
and template references but never saw resolved document references, so a
strict-mode namespace could reference any document anywhere by UUID.
These tests pin the enforcement half: entries from
ValidationResult.references with reference_type == "document" are checked
against the same _is_allowed_reference rules as every other kind.

Pure unit tests — the namespace cache is pre-seeded, no Registry HTTP.
"""

import time

import pytest

from document_store.services.reference_validator import (
    ReferenceValidationError,
    ReferenceValidator,
)


class _Placer:
    """Stands in for the Registry's entry-namespace lookup."""

    def __init__(self, placed: dict[str, str]):
        self.placed = placed

    async def lookup_entry_namespaces(self, ids, raise_on_error=False):
        return {i: self.placed[i] for i in ids if i in self.placed}


def _validator(
    namespace: str, isolation_mode: str, allowed: list[str] | None = None,
    open_targets: tuple[str, ...] = (), placed: dict[str, str] | None = None,
):
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="test")
    v._namespace_cache[namespace] = (
        time.monotonic(),
        {
            "isolation_mode": isolation_mode,
            "allowed_external_refs": allowed or [],
        },
    )
    for target in open_targets:
        v._namespace_cache[target] = (time.monotonic(), {"inbound_references": "open"})
    if placed is not None:
        v._registry = _Placer(placed)
    return v


def _doc_ref(namespace: str, field_path: str = "supervisor"):
    """An entry in the ValidationResult.references shape."""
    return {
        "field_path": field_path,
        "reference_type": "document",
        "lookup_value": "019abc00-0000-7000-8000-000000000000",
        "version_strategy": "latest",
        "resolved": {
            "document_id": "019abc00-0000-7000-8000-000000000000",
            "identity_hash": "abc",
            "template_id": "019abc00-0000-7000-8000-000000000001",
            "template_value": "PERSON",
            "version": 1,
            "namespace": namespace,
            "status": "active",
        },
    }


@pytest.mark.asyncio
async def test_strict_mode_rejects_undeclared_foreign_document_ref():
    v = _validator("appns", "strict")
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="appns",
            document_references=[_doc_ref("otherns")],
        )
    violations = exc_info.value.violations
    assert len(violations) == 1
    assert violations[0]["type"] == "document"
    assert violations[0]["namespace"] == "otherns"


@pytest.mark.asyncio
async def test_strict_mode_allows_declared_external_document_ref():
    v = _validator("appns", "strict", allowed=["otherns"], open_targets=("otherns",))
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[_doc_ref("otherns")],
    )


@pytest.mark.asyncio
async def test_open_mode_leaves_a_closed_namespace_to_validation():
    # Open mode may reference any namespace. Whether this writer may name a
    # target in a closed namespace is the inbound gate's question, answered
    # during validation (inbound_gate), where a refusal reads exactly like a
    # missing target; this post-validation check applies the outbound gate only.
    v = _validator("appns", "open")
    v._namespace_cache["otherns"] = (time.monotonic(), {"inbound_references": "closed"})
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[_doc_ref("otherns")],
    )


@pytest.mark.asyncio
async def test_open_mode_reaches_an_open_namespace():
    v = _validator("appns", "open", open_targets=("otherns",))
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[_doc_ref("otherns")],
    )


@pytest.mark.asyncio
async def test_a_reference_the_previous_version_held_is_not_rechecked():
    v = _validator("appns", "strict")
    previous = {"references": [_doc_ref("otherns")]}
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[_doc_ref("otherns")],
        previous=previous,
    )


@pytest.mark.asyncio
async def test_open_mode_allows_wip_namespace_document_ref():
    v = _validator("appns", "open")
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[_doc_ref("wip")],
    )


@pytest.mark.asyncio
async def test_strict_mode_does_not_get_implicit_wip_document_ref():
    v = _validator("appns", "strict")
    with pytest.raises(ReferenceValidationError):
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="appns",
            document_references=[_doc_ref("wip")],
        )


@pytest.mark.asyncio
async def test_own_namespace_document_ref_always_allowed():
    v = _validator("appns", "strict")
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[_doc_ref("appns")],
    )


@pytest.mark.asyncio
async def test_term_references_are_placed_through_the_registry():
    """A term reference carries no namespace; the Registry places it."""
    v = _validator("appns", "strict", placed={"t-1": "otherns"})
    term_entry = {
        "field_path": "gender",
        "reference_type": "term",
        "lookup_value": "M",
        "version_strategy": "latest",
        "resolved": {"term_id": "t-1"},
    }
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="appns",
            document_references=[term_entry],
        )
    assert exc_info.value.violations[0] == {
        "type": "term", "namespace": "otherns",
        "message": "Term namespace 'otherns' is not accessible from 'appns' namespace",
    }


@pytest.mark.asyncio
async def test_entry_without_resolved_is_skipped():
    v = _validator("appns", "strict")
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        document_references=[{"field_path": "x", "reference_type": "document", "resolved": None}],
    )


@pytest.mark.asyncio
async def test_omitting_document_references_keeps_prior_behaviour():
    v = _validator("appns", "strict")
    await v.validate_document_references(
        document_namespace="appns",
        template_namespace="appns",
        term_references=[],
        file_references=[],
    )


@pytest.mark.asyncio
async def test_mixed_kinds_aggregate_violations():
    """A foreign doc ref and a foreign term ref both surface, each typed."""
    v = _validator("appns", "strict", placed={"t-1": "termns"})
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_document_references(
            document_namespace="appns",
            template_namespace="appns",
            term_references=[{"field_path": "gender", "term_id": "t-1"}],
            document_references=[_doc_ref("docns")],
        )
    kinds = {(v_["type"], v_["namespace"]) for v_ in exc_info.value.violations}
    assert kinds == {("term", "termns"), ("document", "docns")}
