"""Document-reference resolution: the branches the suite did not reach, pinned
before _resolve_document_reference's refactor.

Provenance: CASE-1161. In a bulk write a string reference's lookup is cached
per batch (keyed on the value and the field's target_templates). A cache hit
goes straight to the template check; the inbound gate is asked again on every
hit, since it is per write; a cached miss, or a hit the gate refuses, is
reference_not_found. A value that is neither a string nor a dict is
invalid_reference_value.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from document_store.services import validation_service as vs
from document_store.services.validation_service import ValidationResult, ValidationService

TARGETS = ["THING", "ALPHA"]
CACHE_KEY = "doc:X1|ALPHA,THING"


@pytest.fixture
def verify(monkeypatch):
    seen: list[tuple] = []

    async def fake_verify(self, doc, target_templates, result, field_path, version_strategy, namespace=None):
        seen.append((doc, target_templates, field_path, version_strategy, namespace))
        return {"resolved": doc.document_id}

    monkeypatch.setattr(ValidationService, "_verify_ref_template_and_build_result", fake_verify)
    return seen


def _gate(monkeypatch, refuses: bool) -> list[tuple]:
    asked: list[tuple] = []

    async def gate(ns, doc_id):
        asked.append((ns, doc_id))
        return refuses

    monkeypatch.setattr(vs, "gate_refuses", gate)
    return asked


async def _resolve(value, cache):
    result = ValidationResult()
    out = await ValidationService()._resolve_document_reference(
        value, TARGETS, result, "f", "ns", version_strategy="pinned", doc_ref_cache=cache,
    )
    return out, [(e["code"], e["field"], e.get("details")) for e in result.errors]


def _not_found(value):
    return [("reference_not_found", "f", {"value": value, "target_templates": TARGETS})]


@pytest.mark.asyncio
async def test_a_cache_hit_goes_straight_to_the_template_check(monkeypatch, verify):
    doc = SimpleNamespace(namespace="ns", document_id="d1")
    asked = _gate(monkeypatch, refuses=False)
    out, errors = await _resolve("X1", {CACHE_KEY: doc})
    assert out == {"resolved": "d1"} and errors == []
    assert verify == [(doc, TARGETS, "f", "pinned", "ns")]
    assert asked == [("ns", "d1")]


@pytest.mark.asyncio
async def test_a_cache_hit_the_gate_refuses_is_not_found(monkeypatch, verify):
    _gate(monkeypatch, refuses=True)
    out, errors = await _resolve("X1", {CACHE_KEY: SimpleNamespace(namespace="other", document_id="d1")})
    assert out is None and errors == _not_found("X1") and verify == []


@pytest.mark.asyncio
async def test_a_cached_miss_is_not_found_without_asking_the_gate(monkeypatch, verify):
    asked = _gate(monkeypatch, refuses=False)
    out, errors = await _resolve("X1", {CACHE_KEY: None})
    assert out is None and errors == _not_found("X1")
    assert asked == [] and verify == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [5, None, ["X1"]])
async def test_a_value_that_is_neither_text_nor_a_key_is_invalid(value, verify):
    out, errors = await _resolve(value, {})
    assert out is None
    assert errors == [("invalid_reference_value", "f", None)]
