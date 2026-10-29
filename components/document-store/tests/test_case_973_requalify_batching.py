"""CASE-973 — the identity-synonym re-qualification pass works in batches.

It made two single-item Registry requests per document (add the qualified
synonym, remove the bare one), about 90 documents a second, so a restore of
a large pre-qualification archive spent ~40 minutes in it, silently. Now each
batch of up to 500 is one add request and one remove request; a document
counts as requalified only when both its add and its remove succeeded (a
failed remove used to be counted as a success); progress is reported per
batch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from document_store.services import synonym_migration
from document_store.services.registry_client import RegistryError
from document_store.services.synonym_migration import (
    REQUALIFY_BATCH,
    requalify_identity_synonyms,
)

NS = "ns-973"


class StubRegistry:
    """Records each bulk request; a test can make chosen items fail."""

    def __init__(self, n_docs: int, *, fail_add: set[str] = frozenset(),
                 fail_remove: set[str] = frozenset(), raise_add: bool = False) -> None:
        self.synonyms = {
            f"D{i}": [{"composite_key": {"title": f"t{i}"}}] for i in range(n_docs)
        }
        self.add_requests: list[list[dict[str, Any]]] = []
        self.remove_requests: list[list[dict[str, Any]]] = []
        self.fail_add, self.fail_remove, self.raise_add = fail_add, fail_remove, raise_add

    async def get_entry_synonyms(self, ids: list[str], namespace: str) -> dict:
        return {i: self.synonyms[i] for i in ids if i in self.synonyms}

    async def add_synonyms_bulk(self, items: list[dict]) -> list[dict]:
        self.add_requests.append(items)
        if self.raise_add:
            raise RegistryError("registry down")
        return [
            {"index": n, "status": "error" if it["target_id"] in self.fail_add else "added"}
            for n, it in enumerate(items)
        ]

    async def remove_synonyms_bulk(self, items: list[dict]) -> list[dict]:
        self.remove_requests.append(items)
        return [
            {"index": n, "status": "forbidden" if it["target_id"] in self.fail_remove else "removed"}
            for n, it in enumerate(items)
        ]


def _rows(n: int) -> list[dict]:
    return [{"_id": f"D{i}", "template_id": "TPL"} for i in range(n)]


async def _run(registry: StubRegistry, n: int, **kwargs):
    aggregate = MagicMock()
    aggregate.return_value.to_list = _async(lambda length=None: _rows(n))
    collection = MagicMock(aggregate=aggregate)

    async def template_info(cache, template_id):
        return ("CHAPTER", ["title"], False)

    with (
        patch("document_store.models.document.Document.get_motor_collection",
              return_value=collection),
        patch.object(synonym_migration, "get_registry_client", return_value=registry),
        patch.object(synonym_migration, "_template_info", side_effect=template_info),
    ):
        return await requalify_identity_synonyms(NS, **kwargs)


def _async(fn):
    async def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


@pytest.mark.asyncio
async def test_one_add_and_one_remove_request_per_batch() -> None:
    n = REQUALIFY_BATCH * 2 + 7
    registry = StubRegistry(n)

    report = await _run(registry, n, dry_run=False)

    assert report.requalified == n and report.errors == 0
    assert [len(r) for r in registry.add_requests] == [REQUALIFY_BATCH, REQUALIFY_BATCH, 7]
    assert [len(r) for r in registry.remove_requests] == [REQUALIFY_BATCH, REQUALIFY_BATCH, 7]
    first = registry.add_requests[0][0]
    assert first["synonym_composite_key"] == {"@template": "CHAPTER", "title": "t0"}
    assert registry.remove_requests[0][0]["synonym_composite_key"] == {"title": "t0"}


@pytest.mark.asyncio
async def test_a_failed_add_keeps_its_bare_synonym() -> None:
    registry = StubRegistry(3, fail_add={"D1"})

    report = await _run(registry, 3, dry_run=False)

    assert report.errors == 1 and report.requalified == 2
    removed = {i["target_id"] for i in registry.remove_requests[0]}
    assert removed == {"D0", "D2"}  # D1's bare synonym is not removed


@pytest.mark.asyncio
async def test_a_failed_remove_is_an_error_not_a_success() -> None:
    registry = StubRegistry(3, fail_remove={"D2"})

    report = await _run(registry, 3, dry_run=False)

    assert report.errors == 1 and report.requalified == 2
    bad = next(i for i in report.items if i.document_id == "D2")
    assert bad.status == "error" and "bare not removed" in (bad.detail or "")


@pytest.mark.asyncio
async def test_a_failed_batch_request_marks_its_items() -> None:
    registry = StubRegistry(3, raise_add=True)

    report = await _run(registry, 3, dry_run=False)

    assert report.errors == 3 and report.requalified == 0
    assert registry.remove_requests == []  # nothing removed without its add


@pytest.mark.asyncio
async def test_progress_after_each_batch() -> None:
    n = REQUALIFY_BATCH + 1
    seen: list[tuple[int, int]] = []

    await _run(StubRegistry(n), n, dry_run=False, progress=lambda d, t: seen.append((d, t)))

    assert seen == [(REQUALIFY_BATCH, n), (n, n)]


@pytest.mark.asyncio
async def test_dry_run_sends_nothing() -> None:
    registry = StubRegistry(4)

    report = await _run(registry, 4, dry_run=True)

    assert report.requalified == 4
    assert registry.add_requests == registry.remove_requests == []
