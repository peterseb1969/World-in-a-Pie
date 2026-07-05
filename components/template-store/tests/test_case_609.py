"""CASE-609 — template-create isolation covers ALL schema references.

Pre-fix, the only isolation call at template create was gated on having a
parent and passed only the extends namespace: parentless templates got zero
checks, the validator's terminology_namespaces parameter was dead at its
sole call site, and template-refs (template_ref / array_template_ref /
target_templates) had no parameter to arrive through at all.

These tests pin the extended validator (template_ref_namespaces) and the
boundary rules for every reference kind. The namespace cache is pre-seeded
(TTL tuple shape, CASE-607) — no Registry HTTP.
"""

import time

import pytest

from template_store.services.reference_validator import (
    ReferenceValidationError,
    ReferenceValidator,
)


def _validator(namespace: str, isolation_mode: str, allowed: list[str] | None = None):
    v = ReferenceValidator(registry_url="http://unused.invalid", api_key="t")
    v._namespace_cache[namespace] = (
        time.monotonic(),
        {"isolation_mode": isolation_mode, "allowed_external_refs": allowed or []},
    )
    return v


@pytest.mark.asyncio
async def test_undeclared_template_ref_rejected():
    v = _validator("appns", "open")
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_template_references(
            template_namespace="appns",
            template_ref_namespaces=["otherns"],
        )
    violations = exc_info.value.violations
    assert violations[0]["type"] == "template"
    assert violations[0]["namespace"] == "otherns"


@pytest.mark.asyncio
async def test_declared_template_ref_allowed():
    v = _validator("appns", "strict", allowed=["otherns"])
    await v.validate_template_references(
        template_namespace="appns",
        template_ref_namespaces=["otherns"],
    )


@pytest.mark.asyncio
async def test_wip_template_ref_allowed_in_open_mode_only():
    open_v = _validator("appns", "open")
    await open_v.validate_template_references(
        template_namespace="appns",
        template_ref_namespaces=["wip"],
    )
    strict_v = _validator("appns", "strict")
    with pytest.raises(ReferenceValidationError):
        await strict_v.validate_template_references(
            template_namespace="appns",
            template_ref_namespaces=["wip"],
        )


@pytest.mark.asyncio
async def test_own_namespace_template_ref_always_allowed():
    v = _validator("appns", "strict")
    await v.validate_template_references(
        template_namespace="appns",
        template_ref_namespaces=["appns", "appns"],
    )


@pytest.mark.asyncio
async def test_terminology_namespaces_no_longer_dead():
    """The parameter existed pre-fix but no call site passed it — pin that
    it enforces when used (the call sites now pass it)."""
    v = _validator("appns", "open")
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_template_references(
            template_namespace="appns",
            terminology_namespaces=["otherns"],
        )
    assert exc_info.value.violations[0]["type"] == "terminology"


@pytest.mark.asyncio
async def test_all_kinds_aggregate():
    """extends + terminology + template violations surface together, typed."""
    v = _validator("appns", "strict")
    with pytest.raises(ReferenceValidationError) as exc_info:
        await v.validate_template_references(
            template_namespace="appns",
            extends_template_namespace="parentns",
            terminology_namespaces=["termns"],
            template_ref_namespaces=["tplns"],
        )
    kinds = {(x["type"], x["namespace"]) for x in exc_info.value.violations}
    assert kinds == {
        ("extends", "parentns"),
        ("terminology", "termns"),
        ("template", "tplns"),
    }


@pytest.mark.asyncio
async def test_no_references_passes():
    """Parentless, ref-less template: the ungated call must be a no-op."""
    v = _validator("appns", "strict")
    await v.validate_template_references(template_namespace="appns")
