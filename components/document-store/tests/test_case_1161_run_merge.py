"""A merge's dropped rows and its redirect of held documents, pinned before
run_merge's refactor.

Provenance: CASE-1161. The suite never reached run_merge with documents held
in memory (an archive not sorted by document_id) on the paths that rewrite or
filter them: a redirected merge re-scoping held documents to the target, and
rows dropped as unresolved removed from the held groups as well as from the
plan. These pin both, for streamed and held documents alike.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from document_store.services.backup_engine import DirectRestoreEngine
from tests.test_case_959_merge_streams import _chain, mongo  # noqa: F401  (fixture)
from tests.test_merge_restore import (
    NAMESPACE,
    OTHER_NAMESPACE,
    _archive,
    _archive_from_other,
    _clear_other,
    _rows,
    _seed,
    _seed_namespace,
    _template,
)


def _docs(sorted_by_id: bool) -> list[dict]:
    docs = [*_chain("D1", 2, "a", "h1"), *_chain("D2", 1, "a", "h2"), *_chain("D3", 2, "a", "h3")]
    # The archive's document order decides the path: sorted streams, unsorted is held.
    return docs if sorted_by_id else [*docs[-2:], *docs[:-2]]


def _versions(rows: list[dict]) -> list[tuple[str, int]]:
    return sorted((r["document_id"], r["version"]) for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("sorted_by_id", [True, False], ids=["streamed", "held"])
async def test_a_dropped_document_is_left_out_streamed_or_held(mongo, sorted_by_id):  # noqa: F811
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    engine = DirectRestoreEngine(mongo, None, lambda e: None)
    engine._report_unresolved_references = AsyncMock(return_value={"D2"})
    with patch("document_store.services.backup_engine.ArchiveReader",
               return_value=_archive({"documents": _docs(sorted_by_id)})):
        await engine.run_merge(MagicMock(), NAMESPACE)
    assert _versions(await _rows(mongo, "documents")) == [("D1", 1), ("D1", 2), ("D3", 1), ("D3", 2)]


@pytest.mark.asyncio
async def test_a_redirected_merge_rescopes_held_documents(mongo):  # noqa: F811
    await _clear_other(mongo)
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    docs = [{**row, "namespace": OTHER_NAMESPACE} for row in _docs(sorted_by_id=False)]
    template = {**_template(), "namespace": OTHER_NAMESPACE}
    engine = DirectRestoreEngine(mongo, None, lambda e: None)
    with patch("document_store.services.backup_engine.ArchiveReader",
               return_value=_archive_from_other({"documents": docs, "templates": [template]})):
        await engine.run_merge(MagicMock(), NAMESPACE)
    rows = await _rows(mongo, "documents")
    assert _versions(rows) == [("D1", 1), ("D1", 2), ("D2", 1), ("D3", 1), ("D3", 2)]
    assert {r["namespace"] for r in rows} == {NAMESPACE}
    assert engine.result["namespaces"][NAMESPACE]["source_namespace"] == OTHER_NAMESPACE
    await _clear_other(mongo)
