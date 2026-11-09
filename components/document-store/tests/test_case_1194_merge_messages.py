"""Merge messages that said less, or other, than what happened.

Provenance: CASE-1194 (3-R11, 3-R12).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from document_store.services.backup_engine import DirectRestoreEngine, RestoreEngineError


@pytest.mark.asyncio
async def test_the_reference_refusal_counts_every_missing_entity():
    engine = DirectRestoreEngine(None, None, lambda _e: None)
    engine._resolvable_ids = AsyncMock(return_value=set())
    missing = {f"T{i}" for i in range(7)}

    with pytest.raises(RestoreEngineError) as refused:
        await engine._check_merge_wanted("ns", {"terms": missing}, {})

    assert "reference 7 entit(ies)" in str(refused.value)
    assert "and 2 more" in str(refused.value)


def test_the_definitions_plan_line_shows_retirements():
    events: list = []
    engine = DirectRestoreEngine(None, None, events.append)
    plan = MagicMock()
    plan.summary.return_value = {"terms": {"unchanged": 0, "add": 0, "mapped": 0, "retire": 3}}
    plan.target_wins = []
    plan.retirements_not_carried = []

    engine._emit_definitions_plan("ns", plan, dry_run=True)

    [line] = [e.message for e in events]
    assert "3 to retire" in line
