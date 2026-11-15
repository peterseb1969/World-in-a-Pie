"""CASE-1237: a namespace's reporting data is closed as its deletion begins,
and a reporting step that does not succeed fails the deletion.

reporting-sync kept a deleted namespace's schema readable to privileged SQL
until the drop step ran — for the whole deletion, and, when the drop waited
for reporting-sync, after the namespace record was gone. The first journal
step now closes the namespace in reporting-sync. A refused close or drop
fails the journal instead of completing with the data left behind.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionStep
from registry.services.namespace_deletion import NamespaceDeletionService

NS = "/api/registry/namespaces"


def _svc(monkeypatch, *, reporting: bool = True) -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "start_recovery", lambda: None)
    svc._postgres_url = "http://reporting-sync" if reporting else None
    monkeypatch.setattr(mod, "_service_api_key", lambda: "k")
    return svc


async def _namespace(client, headers) -> str:
    prefix = f"close-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    return prefix


def _http(status: int, body: dict | None = None) -> MagicMock:
    """An httpx.AsyncClient stand-in whose post and delete answer `status`."""
    response = httpx.Response(status, json=body or {}, request=httpx.Request("POST", "http://x"))
    http = MagicMock()
    http.post = AsyncMock(return_value=response)
    http.delete = AsyncMock(return_value=response)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    return http


@pytest.mark.asyncio
async def test_closing_in_reporting_is_the_first_step(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    journal = await _svc(monkeypatch)._build_journal(prefix, False, "t", [])

    first = journal.steps[0]
    assert (first.store, first.action) == ("postgresql", mod._CLOSE_REPORTING)
    assert [s.order for s in journal.steps] == list(range(1, len(journal.steps) + 1))
    # The drop still comes before the namespace record, which stays last.
    drop = next(s for s in journal.steps[1:] if s.store == "postgresql")
    assert drop.action != mod._CLOSE_REPORTING
    assert journal.steps[-1].collection == "namespaces"
    assert drop.order < journal.steps[-1].order


@pytest.mark.asyncio
async def test_no_close_step_without_reporting(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    journal = await _svc(monkeypatch, reporting=False)._build_journal(prefix, False, "t", [])
    assert all(s.store != "postgresql" for s in journal.steps)
    assert journal.steps[0].order == 1


@pytest.mark.asyncio
async def test_the_close_step_posts_to_the_close_route(client, monkeypatch):
    svc = _svc(monkeypatch)
    http = _http(200, {"closed": True})
    step = DeletionStep(order=1, store="postgresql", action=mod._CLOSE_REPORTING)
    with patch("httpx.AsyncClient", return_value=http):
        assert await svc._exec_postgresql_step(step, "ns-a") == 0
    http.post.assert_awaited_once()
    assert http.post.await_args.args[0] == "http://reporting-sync/api/reporting-sync/namespace/ns-a/close"
    http.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [mod._CLOSE_REPORTING, "delete_namespace_rows"])
@pytest.mark.parametrize("status", [400, 403, 404])
async def test_a_refusal_fails_the_step(client, monkeypatch, action, status):
    svc = _svc(monkeypatch)
    step = DeletionStep(order=1, store="postgresql", action=action)
    with patch("httpx.AsyncClient", return_value=_http(status)), \
            pytest.raises(mod._ReportingRefused, match=str(status)):
        await svc._exec_postgresql_step(step, "ns-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [mod._CLOSE_REPORTING, "delete_namespace_rows"])
async def test_a_5xx_defers_the_step(client, monkeypatch, action):
    svc = _svc(monkeypatch)
    step = DeletionStep(order=1, store="postgresql", action=action)
    with patch("httpx.AsyncClient", return_value=_http(503)), pytest.raises(mod._Unreachable):
        await svc._exec_postgresql_step(step, "ns-a")


@pytest.mark.asyncio
async def test_no_service_key_fails_the_step(client, monkeypatch):
    svc = _svc(monkeypatch)
    monkeypatch.setattr(mod, "_service_api_key", lambda: "")
    step = DeletionStep(order=1, store="postgresql", action=mod._CLOSE_REPORTING)
    with pytest.raises(mod._ReportingRefused, match="no service API key"):
        await svc._exec_postgresql_step(step, "ns-a")


@pytest.mark.asyncio
async def test_a_drop_with_a_null_count_still_completes(client, monkeypatch):
    """The schema is gone; only the audit count is missing."""
    svc = _svc(monkeypatch)
    step = DeletionStep(order=1, store="postgresql", action="delete_namespace_rows")
    with patch("httpx.AsyncClient", return_value=_http(200, {"total_deleted": None})):
        assert await svc._exec_postgresql_step(step, "ns-a") == 0
    assert step.error and "count unavailable" in step.error


@pytest.mark.asyncio
async def test_a_refused_close_deletes_nothing_and_keeps_the_namespace(client, auth_headers, monkeypatch):
    """The close step runs first, so a refused close fails the journal before
    any data is deleted, and the namespace stays (locked) for resume-delete."""
    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    mongo = AsyncMock(return_value=0)
    monkeypatch.setattr(svc, "_exec_mongodb_step", mongo)
    with patch("httpx.AsyncClient", return_value=_http(404)), pytest.raises(mod._ReportingRefused):
        await svc.start_deletion(prefix, requested_by="t")
    mongo.assert_not_awaited()
    got = await client.get(f"{NS}/{prefix}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["status"] == "locked"
