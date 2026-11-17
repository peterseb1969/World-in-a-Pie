"""align_templates decides only the versions the rows the merge writes use,
under every clash policy.

The held-document probe ran only for on_clash=skip. Under `newer` a document
the target holds a newer copy of writes nothing, and under `overwrite` and
`newer` a document the archive wins writes only its head row, appended on
top of the target's: the rest of its archived chain never arrives. Those
rows still counted as arriving, so a version only they pinned decided the
mapping, the draft refusal, and which documents were left out.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.test_case_1043_align_templates import (  # noqa: F401  (mongo is a fixture)
    AGE_INT,
    AGE_STR,
    NOTE,
    _doc,
    _result,
    _target,
    _tpl,
    mongo,
)
from tests.test_merge_restore import NAMESPACE, _archive, _rows, _run_merge, _seed

OLD = datetime(2026, 1, 1, tzinfo=UTC)
NEW = datetime(2026, 6, 1, tzinfo=UTC)


def _target_d2(updated_at: datetime) -> dict:
    return {
        "document_id": "D2", "namespace": NAMESPACE, "template_id": "B-tpl",
        "template_version": 1, "identity_hash": "h-D2", "version": 1,
        "data": {"age": 5}, "status": "active", "updated_at": updated_at,
    }


def _note_archive(updated_at: datetime):
    # D2's archived copy fills `note`, which the target's version lacks: if
    # its row counts, the version is unplaced and D1 is left out with it.
    return _archive({
        "templates": [_tpl("A-tpl", 2, [AGE_INT, NOTE])],
        "documents": [
            _doc("D1", 2, {"age": 4}),
            {**_doc("D2", 2, {"age": 5, "note": "kept"}), "updated_at": updated_at},
        ],
    })


@pytest.mark.asyncio
async def test_under_newer_a_document_the_target_keeps_does_not_decide_the_version(mongo):  # noqa: F811
    await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
    await _seed(mongo, "documents", [_target_d2(NEW)])
    events: list = []
    await _run_merge(mongo, _note_archive(OLD), events, align_templates=True, on_clash="newer")

    report = _result(events)
    assert report["unplaceable_count"] == 0, report
    assert report["held_by_target_count"] == 1
    rows = {d["document_id"]: d for d in await _rows(mongo, "documents")}
    assert sorted(rows) == ["D1", "D2"]
    assert rows["D2"]["data"] == {"age": 5}  # the target's newer copy stays


@pytest.mark.asyncio
async def test_under_newer_a_document_the_archive_wins_still_decides(mongo):  # noqa: F811
    await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
    await _seed(mongo, "documents", [_target_d2(OLD)])
    events: list = []
    await _run_merge(mongo, _note_archive(NEW), events, align_templates=True, on_clash="newer")

    report = _result(events)
    assert report["held_by_target_count"] == 0
    assert report["unplaceable_count"] == 2  # its head arrives, and fills `note`


@pytest.mark.asyncio
async def test_under_overwrite_only_the_appended_head_row_pins_a_version(mongo):  # noqa: F811
    # D2's archived chain: v1 pinned to A-tpl v1 (age a string, which no
    # target version takes), v2 (its head) pinned to A-tpl v2. Overwrite
    # appends only the head on top of the target's D2, so v1's row never
    # arrives and must not leave D2 out.
    await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
    await _seed(mongo, "documents", [_target_d2(OLD)])
    archive = _archive({
        "templates": [_tpl("A-tpl", 1, [AGE_STR]), _tpl("A-tpl", 2, [AGE_INT])],
        "documents": [
            _doc("D2", 1, {"age": "5"}, version=1, ident="h-D2"),
            _doc("D2", 2, {"age": 6}, version=2, ident="h-D2"),
        ],
    })
    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True, on_clash="overwrite")

    report = _result(events)
    assert report["unplaceable_count"] == 0, report
    versions = {v["archive_version"]: v for v in report["templates"][0]["versions"]}
    assert versions[1]["outcome"] == "held" and versions[1]["held_rows"] == 1
    assert versions[2]["outcome"] == "mapped" and versions[2]["target_version"] == 1
    head = max((d for d in await _rows(mongo, "documents") if d["document_id"] == "D2"),
               key=lambda d: d["version"])
    assert (head["version"], head["data"], head["template_version"]) == (2, {"age": 6}, 1)
