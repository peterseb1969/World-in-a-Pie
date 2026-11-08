"""Business-key reference resolution: the branches the suite did not reach,
pinned before _resolve_business_key_reference's refactor.

Provenance: CASE-1161. A dict reference names a target template's identity
fields; each target template is tried in turn. Pinned here: no target
templates is refused; a target template the template store does not find is
skipped, one it cannot answer fails the reference (template_error); a key
whose terms or references do not resolve, or that yields no identity, names
no document under that template (later stages are not run); and a Registry
that cannot answer the primary-key lookup fails the write
(RegistryUnavailableError), never counting as no match.
"""

from __future__ import annotations

from typing import Any

import pytest

from document_store.services import registry_client as rc
from document_store.services import validation_service as vs
from document_store.services.template_store_client import TemplateStoreError
from document_store.services.validation_service import ValidationResult, ValidationService
from wip_auth.registry_client import RegistryUnavailableError

KEY = {"code": "X1"}
TEMPLATE = {
    "template_id": "T1", "value": "THING", "identity_fields": ["code"],
    "fields": [{"name": "code", "type": "string"}, {"name": "other", "type": "string"}],
}


class _Templates:
    def __init__(self, answer: Any):
        self.answer = answer

    async def get_template(self, template_id):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Registry:
    def __init__(self, answer: Any = None):
        self.answer = answer
        self.asked: list[tuple] = []

    async def lookup_document_by_primary_key(self, namespace, template_id, identity_hash):
        self.asked.append((namespace, template_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def stages(monkeypatch):
    """Records which validation stages ran; a test sets which one fails."""
    state: dict[str, Any] = {"ran": [], "fail": None, "no_identity": False}

    async def terms(self, data, template, result, namespace=None):
        state["ran"].append("terms")
        if state["fail"] == "terms":
            result.add_error(code="invalid_term", message="no", field="code")

    async def references(self, data, template, result, namespace=None, doc_ref_cache=None):
        state["ran"].append("references")
        if state["fail"] == "references":
            result.add_error(code="reference_not_found", message="no", field="code")

    def identity(self, data, template, result):
        state["ran"].append("identity")
        result.identity_values = {} if state["no_identity"] else {"code": data["code"]}

    async def allowed(self, target_templates, namespace):
        return {"T1"}

    monkeypatch.setattr(ValidationService, "_validate_terms", terms)
    monkeypatch.setattr(ValidationService, "_validate_references", references)
    monkeypatch.setattr(ValidationService, "_compute_identity", identity)
    monkeypatch.setattr(ValidationService, "_allowed_template_ids", allowed)
    return state


def _wire(monkeypatch, templates: Any, registry: _Registry) -> None:
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Templates(templates))
    monkeypatch.setattr(rc, "get_registry_client", lambda: registry)


async def _resolve(targets=("THING",)):
    return await ValidationService()._resolve_business_key_reference(KEY, list(targets), "ns", None)


@pytest.mark.asyncio
async def test_no_target_templates_is_refused():
    doc, error = await _resolve(targets=())
    assert doc is None
    assert error["code"] == "invalid_reference_value"
    assert "needs the field's target_templates" in error["message"]
    assert error["details"] == {"value": KEY}


@pytest.mark.asyncio
async def test_a_target_template_the_store_cannot_answer_fails_the_reference(monkeypatch, stages):
    registry = _Registry()
    _wire(monkeypatch, TemplateStoreError("down"), registry)
    doc, error = await _resolve()
    assert doc is None
    assert error["code"] == "template_error"
    assert error["message"] == (
        "Could not read a target template of the business key: target template 'T1': down"
    )
    assert error["details"] == {"value": KEY}
    assert stages["ran"] == [] and registry.asked == []


@pytest.mark.asyncio
async def test_a_target_template_the_store_does_not_find_is_skipped(monkeypatch, stages):
    registry = _Registry()
    _wire(monkeypatch, None, registry)
    doc, error = await _resolve()
    assert doc is None
    # Skipped templates leave no identity on record, so the key names none.
    assert error["code"] == "invalid_reference_value"
    assert error["details"] == {"value": KEY, "identity_fields": {}}
    assert stages["ran"] == [] and registry.asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("fail", "no_identity", "ran"), [
    ("terms", False, ["terms"]),
    ("references", False, ["terms", "references"]),
    (None, True, ["terms", "references", "identity"]),
])
async def test_a_key_that_does_not_resolve_names_no_document(monkeypatch, stages, fail, no_identity, ran):
    stages["fail"], stages["no_identity"] = fail, no_identity
    registry = _Registry("doc-1")
    _wire(monkeypatch, TEMPLATE, registry)
    assert await _resolve() == (None, None)
    assert stages["ran"] == ran
    assert registry.asked == []


@pytest.mark.asyncio
async def test_the_identity_template_holds_only_the_identity_fields(monkeypatch, stages):
    seen: list[list[str]] = []

    async def terms(self, data, template, result, namespace=None):
        seen.append([f["name"] for f in template["fields"]])
        result.add_error(code="invalid_term", message="stop", field="code")

    monkeypatch.setattr(ValidationService, "_validate_terms", terms)
    _wire(monkeypatch, TEMPLATE, _Registry())
    await _resolve()
    assert seen == [["code"]]


@pytest.mark.asyncio
async def test_a_registry_that_cannot_answer_the_lookup_fails_the_write(monkeypatch, stages):
    registry = _Registry(RegistryUnavailableError("down"))
    _wire(monkeypatch, TEMPLATE, registry)
    with pytest.raises(RegistryUnavailableError):
        await _resolve()
    assert registry.asked == [("ns", "T1")]


def test_scratch_result_starts_valid():
    # The stages above rely on a fresh ValidationResult being valid.
    assert ValidationResult().valid is True
