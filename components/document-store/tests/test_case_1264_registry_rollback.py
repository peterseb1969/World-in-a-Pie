"""A create that fails after the Registry minted its entry releases the
entry, an unreachable Registry never escapes a rollback, and an outage is
refused even under on_synonym_conflict="warn".

Provenance: CASE-1264 (commit-review backfill, findings on b6b06d64). The
auto-synonym failure branch deleted the document but kept the entry it
minted, with the identity and inline synonyms it claims; the rollback legs
caught only RegistryError; "warn" turned a Registry outage into a warning
and wrote the document; and a write that could not reach the Registry
carried no details.rule, so it read like a refusal.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from document_store.models.document import Document
from document_store.services.document_service import DocumentService
from document_store.services.errors import error_fields
from document_store.services.registry_client import (
    RegistryClient,
    RegistryError,
    RegistryUnreachableError,
)
from wip_auth.registry_client import RegistryUnavailableError

DOCS = "/api/document-store/documents"


def _item(title: str, **extra: Any) -> dict[str, Any]:
    return {"template_id": "CHAPTER_Q", "namespace": "wip", "data": {"title": title}, **extra}


@pytest.fixture
def released(monkeypatch) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    async def hard_delete_entry(self, entry_id, updated_by=None, rollback_uncommitted=False, **kw):
        calls.append((entry_id, rollback_uncommitted))
        return True

    monkeypatch.setattr(RegistryClient, "hard_delete_entry", hard_delete_entry)
    return calls


@pytest.mark.asyncio
async def test_a_single_create_whose_auto_synonym_fails_releases_its_entry(
    client: AsyncClient, auth_headers: dict, monkeypatch, released,
):
    async def refuse(self, **kwargs):
        raise RegistryError("Failed to register auto-synonym: 503 - down")

    monkeypatch.setattr(RegistryClient, "register_auto_synonym", refuse)
    response = await client.post(DOCS, headers=auth_headers, json=[_item("Release 1264")])
    result = response.json()["results"][0]
    assert result["error_code"] == "registry_error", result
    assert await Document.find({"data.title": "Release 1264"}).count() == 0
    assert len(released) == 1 and released[0][1] is True


@pytest.mark.asyncio
async def test_a_bulk_create_whose_auto_synonym_is_refused_releases_that_entry(
    client: AsyncClient, auth_headers: dict, monkeypatch, released,
):
    async def refuse_first(self, items, namespace, created_by):
        return ["refused"] + [None] * (len(items) - 1)

    monkeypatch.setattr(RegistryClient, "register_auto_synonyms_per_item", refuse_first)
    response = await client.post(
        DOCS, headers=auth_headers, json=[_item("Bulk release 1264 a"), _item("Bulk release 1264 b")],
    )
    results = sorted(response.json()["results"], key=lambda r: r["index"])
    assert [r["status"] for r in results] == ["error", "created"], results
    assert await Document.find({"data.title": "Bulk release 1264 a"}).count() == 0
    assert [rollback for _, rollback in released] == [True]


@pytest.mark.asyncio
async def test_a_release_that_cannot_reach_the_registry_does_not_escape(monkeypatch):
    async def unreachable(self, *a, **kw):
        raise RegistryUnavailableError("Registry unavailable (hard delete): ConnectError", "hard delete")

    monkeypatch.setattr(RegistryClient, "hard_delete_entry", unreachable)
    service = DocumentService()
    await service._release_minted_entry("d1")  # logged, not raised
    await service._rollback_synonym_allocation("d1", "wip", [], is_new_entry=True)


@pytest.mark.asyncio
async def test_warn_does_not_turn_a_registry_outage_into_a_warning(
    client: AsyncClient, auth_headers: dict, monkeypatch, released,
):
    async def down(self, **kwargs):
        raise RegistryUnreachableError("Registry unreachable (synonym registration): ConnectError")

    monkeypatch.setattr(RegistryClient, "add_synonyms", down)
    item = _item("Warn 1264", synonyms=[{"orig_id": "W-1264"}], on_synonym_conflict="warn")
    response = await client.post(DOCS, headers=auth_headers, json=[item])
    result = response.json()["results"][0]
    assert result["status"] == "error" and result["error_code"] == "registry_error", result
    assert await Document.find({"data.title": "Warn 1264"}).count() == 0


def test_an_unreachable_registry_write_carries_the_outage_rule():
    fields = error_fields(RegistryUnreachableError("Registry unreachable (x): ConnectError"))
    assert fields["error_code"] == "registry_error"
    assert fields["details"] == {"rule": "registry_unavailable"}
    assert "details" not in error_fields(RegistryError("Registry refused"))
