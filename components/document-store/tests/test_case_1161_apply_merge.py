"""A merge's write step, pinned before _apply_merge's refactor.

Provenance: CASE-1161. The suite never reached the streamed path's mid-stream
batch flush, nor a plan holding neither documents nor files (no file
reference recount). Pinned here with the engine's collaborators recorded:
the order and batches of inserts, the reporting structure gate before
documents, the clash overwrite on the streamed and in-memory paths, the
recount, and the claim of inserted Registry entries.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from document_store.services.backup_engine import DirectRestoreEngine
from document_store.services.merge_plan import EntityPlan


class _Streamed:
    def __init__(self, chains):
        self.chains = chains

    def inserted_chains(self):
        return iter(self.chains)

    def clash_sources(self):
        return "SOURCES"


@pytest.fixture
def engine(monkeypatch):
    calls: list[tuple] = []
    eng = DirectRestoreEngine(MagicMock(), None, lambda e: None)

    async def insert_batch(collection, batch, entity_type):
        calls.append(("insert", entity_type, [r["id"] for r in batch]))

    async def structure(namespace, **kw):
        calls.append(("structure", namespace))

    async def overwrite(namespace, plan, doc_groups, *, on_clash, older_versions, sources=None, batch_size=None):
        calls.append(("overwrite", on_clash, older_versions, sources, batch_size))

    async def recount(namespace):
        calls.append(("recount", namespace))

    async def claim(namespace, rows):
        calls.append(("claim", [r["id"] for r in rows]))
        return 1, 0

    def report(namespace, claimed, taken):
        calls.append(("report", claimed, taken))

    def emit(phase, message, **kw):
        calls.append(("emit", phase, message))

    monkeypatch.setattr(eng, "_insert_batch", insert_batch)
    monkeypatch.setattr(eng, "_reporting_phase_structure", structure)
    monkeypatch.setattr(eng, "_overwrite_documents", overwrite)
    monkeypatch.setattr(eng, "_recount_file_references", recount)
    monkeypatch.setattr(eng, "_claim_inserted_entries", claim)
    monkeypatch.setattr(eng, "_report_claims", report)
    monkeypatch.setattr(eng, "_emit", emit)
    eng.calls = calls  # type: ignore[attr-defined]
    return eng


def _rows(*ids: str) -> list[dict[str, Any]]:
    return [{"id": i, "document_id": i[0]} for i in ids]


@pytest.mark.asyncio
@pytest.mark.parametrize("on_clash", ["overwrite", "skip"])
async def test_streamed_documents_flush_in_batches_then_overwrite_clashes(engine, on_clash):
    plans = {"documents": EntityPlan("documents")}
    streamed = _Streamed([_rows("a1", "a2"), _rows("b1"), _rows("c1", "c2", "c3"), _rows("d1")])
    await engine._apply_merge("ns", plans, {}, on_clash=on_clash, batch_size=3, streamed=streamed)
    expected = [
        ("structure", "ns"),
        ("insert", "documents", ["a1", "a2", "b1"]),
        ("insert", "documents", ["c1", "c2", "c3"]),
        ("insert", "documents", ["d1"]),
        ("emit", "phase_documents", "[ns] inserted 7 documents"),
    ]
    if on_clash == "overwrite":
        expected.append(("overwrite", "overwrite", "retire", "SOURCES", 3))
    expected.append(("recount", "ns"))
    assert engine.calls == expected


@pytest.mark.asyncio
async def test_in_memory_documents_bring_their_chains_and_overwrite_without_sources(engine):
    plans = {"documents": EntityPlan("documents", to_insert=[{"id": "x1", "document_id": "x"}])}
    groups = {"x": _rows("x1", "x2", "x3")}
    await engine._apply_merge("ns", plans, groups, on_clash="newer", older_versions="keep", batch_size=2)
    assert engine.calls == [
        ("structure", "ns"),
        ("insert", "documents", ["x1", "x2"]),
        ("insert", "documents", ["x3"]),
        ("emit", "phase_documents", "[ns] inserted 3 documents"),
        ("overwrite", "newer", "keep", None, None),
        ("recount", "ns"),
    ]


@pytest.mark.asyncio
async def test_a_plan_without_documents_or_files_inserts_in_order_and_claims_entries(engine):
    plans = {
        "registry_entries": EntityPlan("registry_entries", to_insert=_rows("r1")),
        "terms": EntityPlan("terms", to_insert=_rows("t1", "t2", "t3")),
        "terminologies": EntityPlan("terminologies"),
    }
    await engine._apply_merge("ns", plans, {}, on_clash="skip", batch_size=2)
    inserts = [c for c in engine.calls if c[0] == "insert"]
    assert inserts == [
        ("insert", "terms", ["t1", "t2"]),
        ("insert", "terms", ["t3"]),
        ("insert", "registry_entries", ["r1"]),
    ]
    assert ("emit", "phase_terminologies", "[ns] inserted 0 terminologies") not in engine.calls
    assert not any(c[0] in ("structure", "recount", "overwrite") for c in engine.calls)
    assert engine.calls[-2:] == [("claim", ["r1"]), ("report", 1, 0)]


@pytest.mark.asyncio
async def test_a_files_plan_alone_still_recounts_file_references(engine):
    await engine._apply_merge("ns", {"files": EntityPlan("files")}, {}, on_clash="skip", batch_size=2)
    assert engine.calls == [("recount", "ns")]
