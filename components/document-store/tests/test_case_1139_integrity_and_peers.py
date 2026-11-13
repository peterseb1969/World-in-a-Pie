"""An integrity check that could not ask def-store or the template store is
incomplete, never healthy; a peer whose read permission the Registry could
not check fails the request, never reads as not_found.

- The template-reference, term-reference and identity-hash checks used to
  skip a document silently when their store did not answer, and the result
  was graded from the issues alone — healthy during an outage. Each skip is
  now counted in summary.checks_not_run ('<check>:<service>'), and a result
  with no finding but skipped checks is incomplete. Findings still decide
  error / warning.
- ?include=peers turned a Registry outage on the peer namespace's read
  check into peer_error_code not_found. It now raises
  RegistryUnavailableError (503).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.services import integrity_service
from document_store.services.def_store_client import DefStoreError
from document_store.services.template_store_client import TemplateStoreError
from wip_auth.registry_client import RegistryUnavailableError

NS = "integ1139"


@pytest_asyncio.fixture
async def docs(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": NS}).delete()
    for doc_id in ("D1", "D2"):
        await Document(
            namespace=NS, document_id=doc_id, template_id="T1", template_version=1,
            identity_hash="", version=1, data={}, status=DocumentStatus.ACTIVE,
            term_references=[{"field_path": "g", "term_id": "TERM-1"}],
        ).insert()
    yield
    await Document.find({"namespace": NS}).delete()


def _stores(template: object, term: object):
    async def get_template(template_id=None, version=None, **_kw):
        if isinstance(template, Exception):
            raise template
        return template

    async def get_term(term_id):
        if isinstance(term, Exception):
            raise term
        return term

    return (
        patch.object(integrity_service, "get_template_store_client",
                     return_value=SimpleNamespace(get_template=get_template)),
        patch.object(integrity_service, "get_def_store_client",
                     return_value=SimpleNamespace(get_term=get_term)),
    )


async def _check(template: object, term: object):
    tpl, defs = _stores(template, term)
    with tpl, defs:
        return await integrity_service.check_all_documents(
            namespace=NS, check_term_refs=True, check_identity=True,
        )


ACTIVE = {"status": "active", "identity_fields": [], "fields": []}


@pytest.mark.asyncio
async def test_every_check_running_is_healthy(docs):
    result = await _check(ACTIVE, {"term_id": "TERM-1"})
    assert result.status == "healthy"
    assert result.summary.checks_not_run == {}


@pytest.mark.asyncio
async def test_a_def_store_outage_makes_the_result_incomplete(docs):
    result = await _check(ACTIVE, DefStoreError("down", unavailable=True))
    assert result.status == "incomplete"
    assert result.summary.checks_not_run == {"term_reference:def-store": 2}  # one per document
    assert result.issues == []


@pytest.mark.asyncio
async def test_a_template_store_outage_makes_the_result_incomplete(docs):
    result = await _check(TemplateStoreError("down", unavailable=True), {"term_id": "TERM-1"})
    assert result.status == "incomplete"
    # The reference check is cached per (template, version) only on an
    # answer, so each document asks again; identity and the mandatory-field
    # check, which read the same pinned version, likewise.
    assert result.summary.checks_not_run == {
        "template_reference:template-store": 2,
        "identity_hash:template-store": 2,
        "mandatory_fields:template-store": 2,
    }


@pytest.mark.asyncio
async def test_a_finding_still_decides_the_status(docs):
    result = await _check(ACTIVE, None)  # the term is gone: a finding
    assert result.status == "error"


@pytest.mark.asyncio
async def test_each_run_starts_with_nothing_skipped(docs):
    await _check(ACTIVE, DefStoreError("down", unavailable=True))
    result = await _check(ACTIVE, {"term_id": "TERM-1"})
    assert result.summary.checks_not_run == {}


# -- peers ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_peer_permission_outage_fails_the_request(monkeypatch):
    from document_store.services import document_service as ds

    async def unavailable(identity, namespace):
        return "unavailable"

    monkeypatch.setattr("wip_auth.permissions.resolve_permission", unavailable)
    with pytest.raises(RegistryUnavailableError):
        await ds._can_read_namespace(SimpleNamespace(user_id="u"), "other", {})


@pytest.mark.asyncio
async def test_a_peer_namespace_without_read_is_still_unreadable(monkeypatch):
    from document_store.services import document_service as ds

    async def none(identity, namespace):
        return "none"

    monkeypatch.setattr("wip_auth.permissions.resolve_permission", none)
    assert await ds._can_read_namespace(SimpleNamespace(user_id="u"), "other", {}) is False
