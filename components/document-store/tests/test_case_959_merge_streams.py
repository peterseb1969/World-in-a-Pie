"""CASE-959 — a merge streams the archive's documents.

A merge read every document version row into memory before writing, so its
peak memory was the archive's document payload (measured: ~100 MiB for 20 000
1 KB documents x 2 versions, linear). It now plans from a compact head per
document and reads the chains again, one at a time, where they are checked
and written. An archive whose documents are not sorted by document_id
(exported before the backup sorted them) takes the old in-memory path.

The contract: both paths leave exactly the same target state.
"""

from __future__ import annotations

import os
import tracemalloc

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.document_chains import (
    UnsortedArchive,
    compact_heads,
    iter_chains,
)
from tests.test_merge_restore import (
    NAMESPACE,
    _all_collections,
    _archive,
    _document,
    _rows,
    _run_merge,
    _seed,
    _seed_namespace,
    _template,
)


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    """A real client, with this namespace's rows cleared before and after."""
    # The engine's restore tail reads through Beanie models; bind them once
    # to the session client, so these tests pass on their own and not only
    # after some other test has initialized Beanie.
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


def _chain(doc_id: str, versions: int, tag: str, identity: str) -> list[dict]:
    rows = []
    for v in range(1, versions + 1):
        row = _document(v, doc_id=doc_id, data={"n": f"{tag}-{doc_id}-{v}"})
        row["identity_hash"] = identity
        row["updated_at"] = f"2026-10-{10 + v:02d}T00:00:00Z"
        rows.append(row)
    return rows


def _archive_docs() -> list[dict]:
    """D1..D4, two versions each; D2 and D4 clash with the target."""
    return [
        *_chain("D1", 2, "archive", "h-d1"),
        *_chain("D2", 2, "archive", "h-d2"),
        *_chain("D3", 1, "archive", "h-d3"),
        *_chain("D4", 2, "archive", "h-d4"),
    ]


async def _merge(mongo, docs: list[dict], **kwargs) -> tuple[list[dict], list]:
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    target = [*_chain("D2", 1, "target", "h-d2"), *_chain("D4", 3, "target", "h-d4")]
    for row in target:
        row["updated_at"] = "2026-10-01T00:00:00Z"
    await _seed(mongo, "documents", target)
    events: list = []
    await _run_merge(mongo, _archive({"documents": docs}), events, **kwargs)
    result = await _rows(
        mongo, "documents", sort_key=lambda d: (d["document_id"], d["version"])
    )
    return result, events


def _warned_unsorted(events) -> bool:
    return any("not sorted by document_id" in (e.message or "") for e in events)


@pytest.mark.parametrize("on_clash", ["skip", "overwrite", "newer"])
@pytest.mark.asyncio
async def test_streamed_and_in_memory_merges_agree(mongo, on_clash: str) -> None:
    streamed, s_events = await _merge(mongo, _archive_docs(), on_clash=on_clash)
    assert not _warned_unsorted(s_events)

    # Clear and replay the same merge from an unsorted archive (D4 first).
    for db_name, coll_name in _all_collections():
        key = "prefix" if coll_name == "namespaces" else "namespace"
        await mongo[db_name][coll_name].delete_many({key: NAMESPACE})
    docs = _archive_docs()
    unsorted = [*docs[-2:], *docs[:-2]]
    in_memory, m_events = await _merge(mongo, unsorted, on_clash=on_clash)
    assert _warned_unsorted(m_events)

    def comparable(rows):
        return [{k: v for k, v in r.items() if k not in ("created_at", "updated_at")} for r in rows]

    assert comparable(streamed) == comparable(in_memory)
    # Inserted chains arrive whole; clashes follow the policy.
    ids = [(d["document_id"], d["version"]) for d in streamed]
    assert ("D1", 1) in ids and ("D1", 2) in ids and ("D3", 1) in ids
    if on_clash == "overwrite":
        assert ("D4", 4) in ids and ("D2", 2) in ids


class TestChains:
    def test_chains_follow_document_id(self) -> None:
        reader = _archive({"documents": _archive_docs()})
        chains = list(iter_chains(reader, NAMESPACE))
        assert [c[0]["document_id"] for c in chains] == ["D1", "D2", "D3", "D4"]
        assert [len(c) for c in chains] == [2, 2, 1, 2]

    def test_an_unsorted_archive_is_detected(self) -> None:
        docs = _archive_docs()
        reader = _archive({"documents": [*docs[-2:], *docs[:-2]]})
        with pytest.raises(UnsortedArchive):
            list(iter_chains(reader, NAMESPACE))

    def test_compact_heads_carry_no_data(self) -> None:
        heads = compact_heads(_archive({"documents": _archive_docs()}), NAMESPACE)
        assert [h["version"] for h in heads] == [2, 2, 1, 2]
        assert all("data" not in h for h in heads)


def test_planning_memory_does_not_grow_with_document_payload(tmp_path) -> None:
    """On a real archive, the compact heads cost a fraction of the rows."""
    from wip_archive.archive import ArchiveReader, ArchiveWriter
    from wip_archive.models import Manifest

    path = tmp_path / "big.zip"
    writer = ArchiveWriter(path, default_namespace=NAMESPACE)
    body = "x" * 10_000
    for i in range(2000):
        for v in (1, 2):
            row = _document(v, doc_id=f"D{i:06d}", data={"body": body})
            writer.add_entity("documents", row, namespace=NAMESPACE)
    writer.write(Manifest())

    def peak(fn) -> int:
        tracemalloc.start()
        fn()
        _, top = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return top

    with ArchiveReader(path) as reader:
        heads_peak = peak(lambda: compact_heads(reader, NAMESPACE))
    with ArchiveReader(path) as reader:
        rows_peak = peak(lambda: list(reader.read_entities("documents", namespace=NAMESPACE)))

    # ~40 MB of document payload: holding the rows costs that, the heads not.
    assert rows_peak > 30 * 2**20
    assert heads_peak < rows_peak / 10


# ---------------------------------------------------------------------------
# Fresh restore
# ---------------------------------------------------------------------------


def _fresh_archive_docs() -> list[dict]:
    """D1..D4 under OLD-TPL; D3 references D1."""
    rows = []
    for doc_id, versions in (("D1", 2), ("D2", 1), ("D3", 2), ("D4", 1)):
        for v in range(1, versions + 1):
            row = {
                "document_id": doc_id, "namespace": NAMESPACE,
                "template_id": "OLD-TPL", "template_version": 1,
                "identity_hash": f"h-{doc_id}", "version": v,
                "data": {"label": f"{doc_id}-v{v}"}, "status": "active",
            }
            if doc_id == "D3":
                row["references"] = [
                    {"field_path": "parent", "resolved": {"document_id": "D1"}}
                ]
            rows.append(row)
    return rows


async def _fresh(mongo, docs: list[dict]) -> tuple[list[dict], list]:
    from tests.test_merge_restore import _clear_remap_target, _remap_rows, _run_remap

    await _clear_remap_target(mongo)
    events: list = []
    await _run_remap(mongo, _archive({
        "templates": [
            {"template_id": "OLD-TPL", "namespace": NAMESPACE,
             "value": "PATIENT", "identity_fields": []},
        ],
        "documents": docs,
    }), events)
    rows = await _remap_rows(mongo, "documents")
    await _clear_remap_target(mongo)
    return rows, events


def _by_label(rows: list[dict]) -> dict[str, dict]:
    return {r["data"]["label"]: r for r in rows}


@pytest.mark.asyncio
async def test_streamed_and_in_memory_fresh_restores_agree(mongo) -> None:
    streamed, s_events = await _fresh(mongo, _fresh_archive_docs())
    assert not _warned_unsorted(s_events)
    docs = _fresh_archive_docs()
    in_memory, m_events = await _fresh(mongo, [*docs[-1:], *docs[:-1]])
    assert _warned_unsorted(m_events)

    for rows in (streamed, in_memory):
        by = _by_label(rows)
        assert sorted(by) == ["D1-v1", "D1-v2", "D2-v1", "D3-v1", "D3-v2", "D4-v1"]
        # A chain keeps one new id across its versions; nothing keeps an old id.
        assert by["D1-v1"]["document_id"] == by["D1-v2"]["document_id"]
        assert all(not r["document_id"].startswith("D") for r in rows)
        # The reference follows D1 to its new id.
        assert by["D3-v2"]["references"][0]["resolved"]["document_id"] == by["D1-v1"]["document_id"]
        assert {r["template_id"] for r in rows} != {"OLD-TPL"}

    def shape(rows):
        return sorted((r["data"]["label"], r["version"], r["namespace"]) for r in rows)

    assert shape(streamed) == shape(in_memory)
