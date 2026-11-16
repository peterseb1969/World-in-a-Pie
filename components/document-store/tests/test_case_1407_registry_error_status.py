"""A Registry that answers 5xx is an outage, not a refusal.

document-store's Registry client raised a plain RegistryError for every
status but 200, so a Registry answering 500/503 (its own restart, overload,
an unhandled error) was reported like a Registry that refused the item: no
``details.rule: registry_unavailable``. An import, which stops on an outage,
did not see it: it failed every remaining row as refused. A 5xx now raises
RegistryUnreachableError, as an unreachable Registry does, so the item reads
as an outage and the import stops; a 4xx stays a refusal. The auto-synonym
registration, which turned even an unreachable Registry into a plain refusal,
draws the same line.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from document_store.services.errors import error_fields
from document_store.services.registry_client import (
    RegistryClient,
    RegistryError,
    RegistryUnreachableError,
)
from wip_auth.registry_client import clear_registry_transport, set_registry_transport

from .test_case_1140_import import finish_job, start_import


def _client() -> RegistryClient:
    return RegistryClient(base_url="http://registry.test", api_key="k")


def _answering(status: int):
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="answered")
    return httpx.MockTransport(answer)


class _RegisterAnswers(httpx.AsyncBaseTransport):
    """The real Registry, except that id registration answers ``status``."""

    def __init__(self, inner: httpx.AsyncBaseTransport, status: int) -> None:
        self.inner, self.status = inner, status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/entries/register"):
            return httpx.Response(self.status, text="restarting")
        return await self.inner.handle_async_request(request)


def _swap_transport(transport: httpx.AsyncBaseTransport | None):
    import wip_auth.registry_client as base

    previous = base._registry_transport
    set_registry_transport(transport)
    return previous


def _restore_transport(previous) -> None:
    if previous is None:
        clear_registry_transport()
    else:
        set_registry_transport(previous)


CALLS: list[tuple[str, Any]] = [
    ("generate_document_id", lambda c: c.generate_document_id("T1", "ns", {"a": 1})),
    ("generate_document_ids_bulk", lambda c: c.generate_document_ids_bulk([{"template_id": "T1"}], "ns")),
    ("rekey_identity", lambda c: c.rekey_identity([{"entry_id": "d1", "identity_hash": "h"}])),
    ("add_synonyms", lambda c: c.add_synonyms("d1", "ns", "documents", [{"k": "v"}])),
    ("remove_synonyms", lambda c: c.remove_synonyms("d1", "ns", "documents", [{"k": "v"}])),
    ("register_auto_synonym", lambda c: c.register_auto_synonym("d1", "ns", "T", "h", True)),
    ("register_auto_synonyms_per_item", lambda c: c.register_auto_synonyms_per_item(
        [{"document_id": "d1", "template_value": "T", "identity_hash": "h", "has_identity_fields": True}],
        "ns",
    )),
    ("get_namespace_status", lambda c: c.get_namespace_status("ns")),
    ("get_namespace_deletion_journal", lambda c: c.get_namespace_deletion_journal("ns")),
    ("get_namespace_deletion_step_keys", lambda c: c.get_namespace_deletion_step_keys("ns", 1)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503])
@pytest.mark.parametrize("call", [c for _, c in CALLS], ids=[n for n, _ in CALLS])
async def test_a_5xx_is_an_outage_with_its_rule(call, status):
    previous = _swap_transport(_answering(status))
    try:
        with pytest.raises(RegistryUnreachableError) as raised:
            await call(_client())
    finally:
        _restore_transport(previous)
    fields = error_fields(raised.value)
    assert fields["error_code"] == "registry_error"
    assert fields["details"] == {"rule": "registry_unavailable"}, fields


WRITES = [c for c in CALLS if c[0] not in ("register_auto_synonyms_per_item",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [c for _, c in WRITES], ids=[n for n, _ in WRITES])
async def test_a_4xx_stays_a_refusal(call):
    previous = _swap_transport(_answering(422))
    try:
        with pytest.raises(RegistryError) as raised:
            await call(_client())
    finally:
        _restore_transport(previous)
    assert not isinstance(raised.value, RegistryUnreachableError)
    assert "details" not in error_fields(raised.value)


@pytest.mark.asyncio
async def test_an_unreachable_registry_is_an_outage_for_the_auto_synonym_too():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    previous = _swap_transport(httpx.MockTransport(refuse))
    try:
        with pytest.raises(RegistryUnreachableError):
            await _client().register_auto_synonyms_per_item(
                [{"document_id": "d1", "template_value": "T", "identity_hash": "h",
                  "has_identity_fields": True}], "ns",
            )
    finally:
        _restore_transport(previous)


ROWS = [
    ("140710001", "Ann", "Lee"),
    ("140710002", "Bob", "Lee"),
    ("140710003", "Cat", "Lee"),
]


@pytest.mark.asyncio
async def test_an_import_stops_on_a_registry_answering_503(client: AsyncClient, auth_headers):
    """Every id registration answers 503: the first row meets the outage and
    the import stops there, the other rows skipped. Before, each row failed
    as refused and the job completed."""
    import wip_auth.registry_client as base

    previous = _swap_transport(_RegisterAnswers(base._registry_transport, 503))
    try:
        job_id = (await start_import(client, auth_headers, ROWS))["job_id"]
        job = await finish_job(client, auth_headers, job_id, status="failed")
    finally:
        _restore_transport(previous)
    assert "stopped at row 2" in job["error"], job
    report = job["result"]
    assert (report["succeeded"], report["failed"], report["skipped"]) == (0, 1, 2), report
    [error] = report["errors"]
    assert error["error_code"] == "registry_error"
    assert (error.get("details") or {}).get("rule") == "registry_unavailable", error


@pytest.mark.asyncio
async def test_a_bulk_create_whose_auto_synonyms_meet_a_503_reports_the_outage(
    client: AsyncClient, auth_headers, monkeypatch,
):
    """The bulk path's auto-synonym call answered 503: every new document is
    rolled back and its item carries the outage's rule."""
    async def outage(self, documents, namespace, created_by=None):
        raise RegistryUnreachableError("Failed to register auto-synonym: 503 - restarting")

    monkeypatch.setattr(RegistryClient, "register_auto_synonyms_per_item", outage)
    items = [
        {"template_id": "PERSON", "namespace": "wip",
         "data": {"national_id": f"14071010{i}", "first_name": "A", "last_name": "B", "gender": "M"}}
        for i in range(2)
    ]
    response = await client.post("/api/document-store/documents", headers=auth_headers, json=items)
    assert response.status_code == 200, response.text
    for result in response.json()["results"]:
        assert result["status"] == "error", result
        assert result["error_code"] == "registry_error"
        assert (result.get("details") or {}).get("rule") == "registry_unavailable", result

    from document_store.models.document import Document

    assert await Document.find({"data.national_id": {"$in": ["140710100", "140710101"]}}).count() == 0
