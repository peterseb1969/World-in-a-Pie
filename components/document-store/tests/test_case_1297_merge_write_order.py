"""A multi-namespace merge writes in three steps across all its namespaces.

A merge wrote one namespace completely (definitions, reporting gate,
documents) before it started the next, so namespace B's reporting gate could
refuse after A had been fully merged, and the job reported a plain refusal
with nothing saying what A had received. Now every namespace's definitions
are written, then every gate runs, then every namespace's documents, files
and remaining entries; a gate refusal therefore lands before any document is
written anywhere, and any failure after the first write carries, per target,
what was written (details.written).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    RestoreEngineError,
    _StagedMerge,
)
from document_store.services.merge_plan import EntityPlan

POLICY = {"on_clash": "skip", "older_versions": "retire"}


def _stage(tgt: str) -> _StagedMerge:
    return _StagedMerge(
        src=tgt, tgt=tgt, definitions=MagicMock(), remapper=MagicMock(),
        built={"documents": EntityPlan("documents", to_insert=[{"template_id": f"T-{tgt}"}])},
        doc_groups={}, streamed=None, arriving={}, wanted={},
    )


def _engine(calls: list[tuple], *, fail_at: tuple | None = None) -> DirectRestoreEngine:
    eng = DirectRestoreEngine(MagicMock(), None, lambda e: None)

    def step(name):
        async def record(namespace, *args, **kwargs):
            call = (name, namespace)
            calls.append(call)
            if call == fail_at:
                raise RestoreEngineError(f"{name} refused in {namespace}", details={"cause": name})
        return record

    eng._apply_definitions = step("definitions")
    eng._apply_merge_definitions = step("definition rows")
    eng._reporting_phase_structure = step("gate")
    eng._apply_merge = step("documents")
    eng._release_retired_synonyms = step("synonyms")
    eng._reporting_phase_counts = step("counts")

    async def provenance(*args, target, **kwargs):
        calls.append(("provenance", target))

    eng._write_restore_provenance = provenance
    eng.result = {"namespaces": {"a": {}, "b": {}}}
    return eng


async def _write(eng: DirectRestoreEngine) -> None:
    await eng._write_staged_merges(
        [_stage("a"), _stage("b")], MagicMock(), MagicMock(), MagicMock(),
        policy=POLICY, batch_size=10, skip_documents=False,
    )


@pytest.mark.asyncio
async def test_definitions_everywhere_then_every_gate_then_the_rest():
    calls: list[tuple] = []
    await _write(_engine(calls))
    order = [c for c in calls if c[0] in ("definitions", "gate", "documents")]
    assert order == [
        ("definitions", "a"), ("definitions", "b"),
        ("gate", "a"), ("gate", "b"),
        ("documents", "a"), ("documents", "b"),
    ]


@pytest.mark.asyncio
async def test_a_later_gate_refuses_before_any_document_and_says_what_was_written():
    calls: list[tuple] = []
    eng = _engine(calls, fail_at=("gate", "b"))
    with pytest.raises(RestoreEngineError) as refused:
        await _write(eng)
    assert not any(c[0] == "documents" for c in calls)
    err = refused.value
    assert err.details["result_kind"] == "merge_partial"
    assert err.details["written"] == {"a": ["definitions"], "b": ["definitions"]}
    assert err.details["cause"] == "gate"
    assert "gate refused in b" in str(err)
    assert "'a': definitions and their Registry entries" in str(err)


@pytest.mark.asyncio
async def test_a_failure_in_a_later_namespace_names_the_earlier_one_complete():
    calls: list[tuple] = []
    with pytest.raises(RestoreEngineError) as refused:
        await _write(_engine(calls, fail_at=("documents", "b")))
    assert refused.value.details["written"] == {
        "a": ["definitions", "documents"], "b": ["definitions"],
    }
    assert "'a': definitions and their Registry entries and documents" in str(refused.value)


@pytest.mark.asyncio
async def test_a_failure_before_the_first_write_is_raised_as_it_is():
    calls: list[tuple] = []
    with pytest.raises(RestoreEngineError) as refused:
        await _write(_engine(calls, fail_at=("definitions", "a")))
    assert refused.value.details == {"cause": "definitions"}
    assert "already written" not in str(refused.value)


@pytest.mark.asyncio
async def test_definition_entries_are_claimed_with_the_definitions():
    """A gate that refuses later leaves no definition entry unclaimed; the
    claims are reported once, with the remaining entries'."""
    events: list[tuple] = []
    eng = DirectRestoreEngine(MagicMock(), None, lambda e: None)

    async def insert_rows(namespace, collection, entity_type, rows, batch_size):
        events.append(("insert", entity_type, [r["entry_id"] for r in rows if "entry_id" in r]))

    async def claim(namespace, rows):
        events.append(("claim", [r["entry_id"] for r in rows]))
        return len(rows), 0

    async def recount(namespace):
        return None

    eng._merge_insert_rows = insert_rows
    eng._claim_inserted_entries = claim
    eng._recount_file_references = recount
    eng._report_claims = lambda ns, claimed, theirs: events.append(("report", claimed, theirs))
    entries = [
        {"entry_id": "TPL-1", "entity_type": "templates"},
        {"entry_id": "DOC-1", "entity_type": "documents"},
    ]
    plans: dict[str, Any] = {"registry_entries": EntityPlan("registry_entries", to_insert=entries)}

    await eng._apply_merge_definitions("ns", plans, batch_size=10)
    assert events == [("insert", "registry_entries", ["TPL-1"]), ("claim", ["TPL-1"])]

    await eng._apply_merge("ns", plans, {}, on_clash="skip", batch_size=10)
    assert events[2:] == [
        ("insert", "registry_entries", ["DOC-1"]), ("claim", ["DOC-1"]), ("report", 2, 0),
    ]
