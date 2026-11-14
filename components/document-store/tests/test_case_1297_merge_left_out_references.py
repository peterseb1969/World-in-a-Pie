"""A reference to a document align_templates left out is refused for that reason.

With align_templates, a document no target template version can take is
left out of the merge as unplaceable. A document that is merged and
references it then failed the reference check with "Include the missing
entities in the archive" — but the document is in the archive. The refusal
now names the left-out documents and the real cause, across every namespace
of the merge.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    RestoreEngineError,
    _StagedMerge,
)


def _stage(tgt: str, *, wanted=None, left_out=None) -> _StagedMerge:
    return _StagedMerge(
        src=tgt, tgt=tgt, definitions=MagicMock(), remapper=MagicMock(), built={},
        doc_groups={}, streamed=None, arriving={}, wanted=wanted or {},
        left_out=left_out or set(),
    )


def _engine() -> DirectRestoreEngine:
    eng = DirectRestoreEngine(MagicMock(), None, lambda e: None)
    eng._resolvable_ids = AsyncMock(return_value=set())  # the target holds none
    return eng


@pytest.mark.asyncio
async def test_a_reference_to_a_left_out_document_names_the_real_cause():
    eng = _engine()
    stages = [
        _stage("a", left_out={"DOC-GONE"}),
        _stage("b", wanted={"documents": {"DOC-GONE"}}),
    ]
    with pytest.raises(RestoreEngineError) as refused:
        await eng._check_staged_references(stages)
    message = str(refused.value)
    assert "align_templates left out as unplaceable: 'DOC-GONE'" in message
    assert "Include the missing entities in the archive" not in message
    assert refused.value.details == {
        "result_kind": "unplaceable_referenced", "documents": ["DOC-GONE"],
    }


@pytest.mark.asyncio
async def test_a_reference_the_archive_does_not_carry_keeps_its_advice():
    eng = _engine()
    with pytest.raises(RestoreEngineError) as refused:
        await eng._check_staged_references([
            _stage("a", wanted={"documents": {"DOC-ELSEWHERE"}}, left_out={"DOC-GONE"}),
        ])
    assert "Include the missing entities in the archive" in str(refused.value)
