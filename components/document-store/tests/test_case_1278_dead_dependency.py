"""A store that stopped answering is not asked again during the same scan.

Provenance: CASE-1278 (live on tc02 with def-store stopped). The scan asks
def-store once per term reference and the template store once per pinned
version, and only *answers* were cached — so during an outage every reference
paid a fresh failed call, and reporting-sync's aggregate ran out of its budget
before either store could answer (150 s for a 200-document scan). Each run now
stops asking a service that failed once: the remaining references are still
recorded in `checks_not_run`, but no further call is made.

An answer is never an outage: a refusal that carries a code (the store is
talking) does not silence the rest of the scan, and a later run reaches a
service the previous run could not.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.services import integrity_service as svc
from document_store.services.def_store_client import DefStoreError
from document_store.services.template_store_client import TemplateStoreError

NS_DOWN = "integ1278down"
NS_OK = "integ1278ok"

ACTIVE = {"status": "active", "identity_fields": [], "fields": []}


def _docs(namespace: str, templates: list[str], terms_per_doc: int) -> list[Document]:
    """Documents on distinct templates with distinct term references each, so
    no per-ID answer cache is what makes the scan fast."""
    return [
        Document(
            namespace=namespace, document_id=f"{namespace}-D{i}", template_id=template_id,
            template_version=1, identity_hash="", version=1, data={},
            status=DocumentStatus.ACTIVE,
            term_references=[
                {"field_path": f"f{j}", "term_id": f"{namespace}-{template_id}-TERM{j}"}
                for j in range(terms_per_doc)
            ],
        )
        for i, template_id in enumerate(templates)
    ]


@pytest_asyncio.fixture
async def documents(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    rows = _docs(NS_DOWN, ["T1", "T2", "T3"], 2) + _docs(NS_OK, ["T4"], 2)
    for row in rows:
        await row.insert()
    yield
    for namespace in (NS_DOWN, NS_OK):
        await Document.find({"namespace": namespace}).delete()


class _CountingDefStore:
    """Def-Store stub: fails for references naming `fail_prefix`, counts asks."""

    def __init__(self, fail_prefix: str | None = None):
        self.calls: list[str] = []
        self._fail_prefix = fail_prefix

    async def get_term(self, term_id: str) -> dict[str, Any]:
        self.calls.append(term_id)
        if self._fail_prefix and term_id.startswith(self._fail_prefix):
            raise DefStoreError("connection refused", unavailable=True)
        return {"term_id": term_id}


class _CountingTemplateStore:
    """Template-store stub: answers, or refuses every ask with `error`."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, int | None]] = []
        self._error = error

    async def get_template(self, template_id: str | None = None, version: int | None = None,
                          **_kw: Any) -> dict[str, Any]:
        self.calls.append((template_id, version))
        if self._error is not None:
            raise self._error
        return dict(ACTIVE)


async def _scan(namespace: str, def_store: Any, template_store: Any):
    with (
        patch.object(svc, "get_def_store_client", return_value=def_store),
        patch.object(svc, "get_template_store_client", return_value=template_store),
    ):
        return await svc.check_all_documents(namespace=namespace)


@pytest.mark.asyncio
async def test_a_dead_def_store_is_asked_once_for_the_whole_scan(documents):
    """Six term references, one failed ask: the rest are recorded, not waited out."""
    def_store, template_store = _CountingDefStore(NS_DOWN), _CountingTemplateStore()
    result = await _scan(NS_DOWN, def_store, template_store)

    # The scan reads documents unsorted, in whatever order MongoDB returns
    # them, so which reference is asked first is not part of the contract —
    # only that one ask was made.
    assert len(def_store.calls) == 1, f"every reference asked def-store again: {def_store.calls}"
    assert def_store.calls[0].startswith(f"{NS_DOWN}-"), def_store.calls
    assert result.status == "incomplete"
    assert result.summary.checks_not_run == {"term_reference:def-store": 6}
    assert result.issues == []


@pytest.mark.asyncio
async def test_a_dead_template_store_is_asked_once_for_the_whole_scan(documents):
    """The reference check and the pinned-schema checks both stop asking."""
    dead = TemplateStoreError("no route to host", unavailable=True)
    def_store, template_store = _CountingDefStore(), _CountingTemplateStore(dead)
    result = await _scan(NS_DOWN, def_store, template_store)

    assert len(template_store.calls) == 1, f"asked the template store {len(template_store.calls)} times"
    assert result.status == "incomplete"
    assert result.summary.checks_not_run == {
        "template_reference:template-store": 3,
        "identity_hash:template-store": 3,
        "mandatory_fields:template-store": 3,
    }


@pytest.mark.asyncio
async def test_a_refusal_is_an_answer_and_does_not_silence_the_scan(documents):
    """A refusal carrying a code (the store is talking, this reference is
    refused) leaves the rest of the scan asking."""
    refusal = TemplateStoreError("inheritance_unresolved", code="inheritance_unresolved")
    def_store, template_store = _CountingDefStore(), _CountingTemplateStore(refusal)
    result = await _scan(NS_DOWN, def_store, template_store)

    assert len(template_store.calls) == 6, "three reference asks plus three pinned-schema asks"
    assert result.summary.checks_not_run == {
        "template_reference:template-store": 3,
        "identity_hash:template-store": 3,
        "mandatory_fields:template-store": 3,
    }


@pytest.mark.asyncio
async def test_a_later_run_asks_again(documents):
    """The latch belongs to a run: a scan after the outage checks its references."""
    await _scan(NS_DOWN, _CountingDefStore(NS_DOWN), _CountingTemplateStore())

    def_store, template_store = _CountingDefStore(), _CountingTemplateStore()
    result = await _scan(NS_DOWN, def_store, template_store)

    assert len(def_store.calls) == 6
    assert result.status == "healthy"
    assert result.summary.checks_not_run == {}


@pytest.mark.asyncio
async def test_a_run_that_lost_def_store_does_not_latch_the_run_beside_it(documents):
    """Runs in flight at once keep their own view of what is dead — CASE-1264's
    guarantee, extended to the latch: B reaches def-store and checks all of its
    references, though A gave up on def-store before B asked."""
    a_failed = asyncio.Event()

    class _Interleaved:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_term(self, term_id: str) -> dict[str, Any]:
            self.calls.append(term_id)
            if term_id.startswith(NS_DOWN):
                a_failed.set()
                raise DefStoreError("connection refused", unavailable=True)
            await a_failed.wait()  # B asks only once A has seen its outage
            return {"term_id": term_id}

    def_store = _Interleaved()
    with (
        patch.object(svc, "get_def_store_client", return_value=def_store),
        patch.object(svc, "get_template_store_client", return_value=_CountingTemplateStore()),
    ):
        down, ok = await asyncio.gather(
            svc.check_all_documents(namespace=NS_DOWN),
            svc.check_all_documents(namespace=NS_OK),
        )

    assert down.summary.checks_not_run == {"term_reference:def-store": 6}
    assert ok.status == "healthy" and ok.summary.checks_not_run == {}
    assert len([c for c in def_store.calls if c.startswith(NS_OK)]) == 2


def test_nothing_is_latched_outside_a_run():
    """A check called outside check_all_documents keeps asking: only a run
    decides what it stopped asking, and only for itself."""
    assert svc._stopped_asking("def-store") is False
    svc._stop_asking("def-store")
    assert svc._stopped_asking("def-store") is False
