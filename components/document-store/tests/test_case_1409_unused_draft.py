"""An archived draft version no arriving document uses does not refuse an
align_templates merge.

Archives carry never-activated drafts, and no document can be written
against a draft. Alignment used to map and judge every archived version, so
a draft whose schema fit a target version refused the whole merge. Only the
versions the arriving documents use are decided now; the draft is history.
"""

from __future__ import annotations

import pytest

from document_store.services.backup_engine import RestoreEngineError
from tests.test_case_1043_align_templates import (  # noqa: F401  (mongo is a fixture)
    AGE_INT,
    NOTE,
    _doc,
    _result,
    _target,
    _tpl,
    mongo,
)
from tests.test_merge_restore import _archive, _rows, _run_merge


@pytest.mark.asyncio
async def test_an_unused_archived_draft_does_not_refuse_the_merge(mongo):  # noqa: F811
    await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
    archive = _archive({
        "templates": [_tpl("A-tpl", 1, [AGE_INT]),
                      _tpl("A-tpl", 2, [AGE_INT, NOTE], status="draft")],
        "documents": [_doc("D1", 1, {"age": 4})],
    })

    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True)

    report = _result(events)
    versions = {v["archive_version"]: v for v in report["templates"][0]["versions"]}
    assert versions[1]["target_version"] == 1 and versions[1]["outcome"] == "mapped"
    assert versions[2]["history"] and versions[2]["target_version"] is None
    assert versions[2]["outcome"] == "history"
    [row] = await _rows(mongo, "documents")
    assert (row["document_id"], row["template_id"], row["template_version"]) == ("D1", "B-tpl", 1)


@pytest.mark.asyncio
async def test_a_used_archived_draft_refuses_with_the_alignment_account(mongo):  # noqa: F811
    await _target(mongo, _tpl("B-tpl", 1, [AGE_INT]))
    archive = _archive({
        "templates": [_tpl("A-tpl", 1, [AGE_INT], status="draft")],
        "documents": [_doc("D1", 1, {"age": 4})],
    })

    with pytest.raises(RestoreEngineError) as refused:
        await _run_merge(mongo, archive, [], align_templates=True)

    details = refused.value.details
    assert details["result_kind"] == "merge_refused"
    [issue] = details["incompatibilities"]
    assert issue["name"] == "PATIENT v1" and "draft" in issue["reason"]
    [version] = details["template_alignment"]["templates"][0]["versions"]
    assert version["outcome"] == "refused" and version["rows"] == 1
    assert await _rows(mongo, "documents") == []
