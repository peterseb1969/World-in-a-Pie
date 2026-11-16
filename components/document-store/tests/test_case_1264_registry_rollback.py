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


# ---- Every registry_error an outage produces carries the outage rule (review
# of 8e060f79, R1): results built by hand, not by error_fields, left it out.


def _unreachable(what: str) -> RegistryUnreachableError:
    return RegistryUnreachableError(f"Registry unreachable ({what}): ConnectError")


@pytest.mark.asyncio
@pytest.mark.parametrize("titles", [["Syn 1264 a"], ["Syn 1264 b", "Syn 1264 c"]])
async def test_a_synonym_outage_carries_the_outage_rule(
    client: AsyncClient, auth_headers: dict, monkeypatch, released, titles,
):
    """One item takes the single-document path, two the bulk per-item path."""
    async def down(self, **kwargs):
        raise _unreachable("synonym registration")

    monkeypatch.setattr(RegistryClient, "add_synonyms", down)
    items = [_item(t, synonyms=[{"orig_id": t}]) for t in titles]
    response = await client.post(DOCS, headers=auth_headers, json=items)
    for result in response.json()["results"]:
        assert result["error_code"] == "registry_error", result
        assert result["details"] == {"rule": "registry_unavailable"}, result


@pytest.mark.asyncio
async def test_an_id_generation_outage_carries_the_outage_rule(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    async def down(self, *args, **kwargs):
        raise _unreachable("document id")

    monkeypatch.setattr(RegistryClient, "generate_document_id", down)
    response = await client.post(DOCS, headers=auth_headers, json=[_item("Gen 1264")])
    result = response.json()["results"][0]
    assert result["error_code"] == "registry_error", result
    assert result["details"] == {"rule": "registry_unavailable"}, result


@pytest.mark.asyncio
async def test_a_patch_whose_write_check_meets_an_outage_carries_the_outage_rule(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    from wip_auth import permissions
    from wip_auth.permissions import PERMISSION_UNAVAILABLE

    created = (await client.post(DOCS, headers=auth_headers, json=[_item("Patch 1264")])).json()
    document_id = created["results"][0]["document_id"]

    import sys

    real = permissions.resolve_permission

    async def unavailable(identity, namespace):
        # The door's own check answers as usual; the per-item write check
        # (_check_patch_permission) meets the outage.
        if sys._getframe(1).f_code.co_name == "_check_patch_permission":
            return PERMISSION_UNAVAILABLE
        return await real(identity, namespace)

    import wip_auth

    monkeypatch.setattr(permissions, "resolve_permission", unavailable)
    # _check_patch_permission imports it from the package at call time.
    monkeypatch.setattr(wip_auth, "resolve_permission", unavailable)
    response = await client.patch(
        DOCS, headers=auth_headers,
        json=[{"document_id": document_id, "patch": {}, "metadata_patch": {"note": "x"}}],
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["error_code"] == "registry_error", result
    assert result["details"] == {"rule": "registry_unavailable"}, result


@pytest.mark.asyncio
async def test_a_refused_insert_releases_the_entry_it_minted(
    client: AsyncClient, auth_headers: dict, monkeypatch, released,
):
    """Review of 8e060f79, R2: a DocumentStoreError from the insert returned
    without releasing the entry this request minted."""
    from document_store.services.errors import DocumentStoreError

    async def refused(self, prior, **insert):
        raise DocumentStoreError("version taken", "already_exists", rule="version_taken")

    monkeypatch.setattr(DocumentService, "_insert_or_restore", refused)
    response = await client.post(DOCS, headers=auth_headers, json=[_item("Refused 1264")])
    result = response.json()["results"][0]
    assert result["error_code"] == "already_exists", result
    assert len(released) == 1, released


@pytest.mark.asyncio
async def test_one_items_outage_in_bulk_validation_is_that_items_answer(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    """Review of 8e060f79, R6: an outage while one item validated answered
    503 for the whole batch. It is that item's invalid result now; the
    others keep theirs."""
    from wip_auth.registry_client import DependencyUnavailableError

    real = DocumentService._validate_as_writer

    async def outage_on_second(self, template_id, data, *args, **kwargs):
        if data.get("title") == "second":
            raise DependencyUnavailableError("def-store did not answer", "def-store")
        return await real(self, template_id, data, *args, **kwargs)

    monkeypatch.setattr(DocumentService, "_validate_as_writer", outage_on_second)
    response = await client.post(
        "/api/document-store/validation/validate-bulk", headers=auth_headers,
        json={"template_id": "CHAPTER_Q", "namespace": "wip",
              "items": [{"title": "first"}, {"title": "second"}]},
    )
    assert response.status_code == 200, response.text
    first, second = response.json()["results"]
    assert first["valid"] is True, first
    assert second["valid"] is False
    assert second["errors"][0]["code"] == "dependency_unavailable", second
