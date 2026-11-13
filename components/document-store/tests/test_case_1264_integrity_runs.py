"""Integrity runs in flight at once keep their own record of skipped checks.

Provenance: CASE-1264 (commit-review backfill, 47a17edd R2). The checks a run
could not perform (a store did not answer) were counted in a module global
that every run cleared at its start. A restore validates each namespace in a
job of its own, so a second run starting mid-scan erased the first's record,
and the first then reported `healthy` with its term references never
checked. Each run now keeps its own record.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from document_store.services import integrity_service as svc


class _Find:
    def __init__(self, docs: list[Any]):
        self.docs = docs

    async def count(self) -> int:
        return len(self.docs)

    def sort(self, *a: Any) -> _Find:
        return self

    def limit(self, n: int) -> _Find:
        return self

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield d
        return gen()


class _Documents:
    @staticmethod
    def find(query: dict[str, Any]) -> _Find:
        return _Find([f"doc-in-{query['namespace']}"])


@pytest.mark.asyncio
async def test_a_run_starting_mid_scan_does_not_erase_another_runs_skips(monkeypatch):
    a_scanning, b_done = asyncio.Event(), asyncio.Event()

    async def batch(docs: list[str], check_term_refs: bool, check_identity: bool) -> list:
        if docs == ["doc-in-A"]:
            svc._not_run("term_reference", "def-store")  # def-store did not answer
            a_scanning.set()
            await b_done.wait()  # B runs whole while A is mid-scan
        return []

    monkeypatch.setattr(svc, "Document", _Documents)
    monkeypatch.setattr(svc, "_check_document_batch", batch)

    async def run_b():
        await a_scanning.wait()
        result = await svc.check_all_documents(namespace="B")
        b_done.set()
        return result

    a, b = await asyncio.gather(svc.check_all_documents(namespace="A"), run_b())
    assert a.status == "incomplete"
    assert a.summary.checks_not_run == {"term_reference:def-store": 1}
    assert b.status == "healthy" and b.summary.checks_not_run == {}


def test_a_check_called_outside_a_run_records_nothing():
    svc._not_run("term_reference", "def-store")  # must not raise
