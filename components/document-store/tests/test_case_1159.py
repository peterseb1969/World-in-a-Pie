"""Two archives whose versioned bootstrap templates drifted can be merged.

Scaffolded apps once minted their bootstrap provenance template under the
one value BOOTSTRAP_RECORD, and each app shaped it its own way. The shapes
below are the real ones: WIP-KB's adds mandatory title / authored_by and a
defaulted doc_status to the scaffold's record, WIP-Cooking's keeps the
scaffold's plain record with commit_sha optional. Merging one such
namespace's archive into the other matches the two templates by value.

Matched by (value, version), the drift refuses the merge. With
align_templates, an archive version maps onto a target version whose schema
can take its documents — and neither app's record schema can take the
other's (KB's records carry fields Cooking's record lacks; Cooking's would
miss fields KB's requires). The supported way through is a target-side
converge: post the target's template once more as a superset that carries
the archive's fields verbatim and the target's other fields as optional.
That is an ordinary template version. The archive's records then map onto
it whole — every row of a migrated chain included — and keep their own
data; the target's own records stay on their version.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from document_store.services.backup_engine import RestoreEngineError
from tests.test_merge_restore import (
    NAMESPACE,
    _all_collections,
    _archive,
    _rows,
    _run_merge,
    _seed,
    _seed_namespace,
)


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    import os

    from motor.motor_asyncio import AsyncIOMotorClient

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


def _f(name: str, type_: str, mandatory: bool = False, **extra) -> dict:
    return {"name": name, "label": name, "type": type_, "mandatory": mandatory, **extra}


# The scaffold's record, as WIP-Cooking ships it (commit_sha optional).
COOKING = [
    _f("bootstrap_id", "string", True),
    _f("app_version", "string", True),
    _f("bootstrapped_at", "datetime", True),
    _f("commit_sha", "string"),
    _f("templates_created", "array"),
    _f("edge_types_created", "array"),
    _f("terminologies_created", "array"),
]
# WIP-KB's record: the scaffold's, every field required, plus its own.
KB = [
    _f("title", "string", True),
    _f("authored_by", "string", True),
    _f("doc_status", "string", True, default_value="draft"),
    _f("tags", "array"),
    _f("bootstrap_id", "string", True),
    _f("app_version", "string", True),
    _f("commit_sha", "string", True),
    _f("bootstrapped_at", "datetime", True),
    _f("templates_created", "array", True),
    _f("edge_types_created", "array", True),
    _f("terminologies_created", "array", True),
    _f("agent_identities_seeded", "array"),
]
KB_RECORD = {
    "title": "Bootstrap", "authored_by": "kb", "doc_status": "draft",
    "bootstrap_id": "bootstrap-kb", "app_version": "1.4.0", "commit_sha": "abc123",
    "bootstrapped_at": "2026-10-01T00:00:00Z", "templates_created": ["KB_DOC"],
    "edge_types_created": [], "terminologies_created": [],
}


def _tpl(template_id: str, version: int, fields: list[dict], status: str = "active") -> dict:
    return {
        "template_id": template_id, "namespace": NAMESPACE, "value": "BOOTSTRAP_RECORD",
        "label": "Bootstrap record", "version": version, "status": status,
        "identity_fields": ["bootstrap_id"], "versioned": True, "fields": fields,
    }


def _doc(template_version: int, data: dict, *, version: int = 1, status: str = "active") -> dict:
    return {
        "document_id": "KB-REC", "namespace": NAMESPACE, "template_id": "KB-tpl",
        "template_version": template_version, "identity_hash": "h-kb-rec",
        "version": version, "data": data, "status": status,
    }


def _superset(archive_fields: list[dict], target_fields: list[dict]) -> list[dict]:
    """The converge version: the archive's fields verbatim, and the
    target's fields the archive lacks, as optional."""
    names = {f["name"] for f in archive_fields}
    return [*archive_fields, *({**f, "mandatory": False} for f in target_fields if f["name"] not in names)]


def _alignment(events) -> dict:
    complete = next(e for e in events if e.phase == "complete")
    return complete.details["namespaces"][NAMESPACE]["template_alignment"]


async def _cooking_target(mongo, *extra_versions: dict) -> None:
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_tpl("CK-tpl", 1, COOKING), *extra_versions])


@pytest.mark.asyncio
async def test_the_drifted_record_refuses_a_plain_merge(mongo):
    await _cooking_target(mongo)
    archive = _archive({"templates": [_tpl("KB-tpl", 1, KB)], "documents": [_doc(1, KB_RECORD)]})
    with pytest.raises(RestoreEngineError, match=r"not\s+compatible"):
        await _run_merge(mongo, archive)


@pytest.mark.asyncio
async def test_alignment_alone_places_no_drifted_record(mongo):
    """KB's records carry fields Cooking's record does not have: no Cooking
    version can take them, so they are reported, not written."""
    await _cooking_target(mongo)
    archive = _archive({"templates": [_tpl("KB-tpl", 1, KB)], "documents": [_doc(1, KB_RECORD)]})
    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True, dry_run=True)
    assert _alignment(events)["unplaceable_count"] == 1


@pytest.mark.asyncio
async def test_a_target_side_converge_takes_the_records_whole(mongo):
    """The target's template gains one version — the archive's fields
    verbatim, its own others optional. The archive's record lands on it with
    its data; the target's own v1 is untouched."""
    await _cooking_target(mongo, _tpl("CK-tpl", 2, _superset(KB, COOKING)))
    archive = _archive({"templates": [_tpl("KB-tpl", 1, KB)], "documents": [_doc(1, KB_RECORD)]})

    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True, dry_run=True)
    report = _alignment(events)
    assert report["unplaceable_count"] == 0, report
    [template] = report["templates"]
    assert [(v["archive_version"], v["target_version"]) for v in template["versions"]] == [(1, 2)]

    await _run_merge(mongo, archive, align_templates=True)
    [doc] = await _rows(mongo, "documents")
    assert (doc["template_id"], doc["template_version"]) == ("CK-tpl", 2)
    assert doc["data"] == KB_RECORD
    assert sorted((t["template_id"], t["version"]) for t in await _rows(mongo, "templates")) == [
        ("CK-tpl", 1), ("CK-tpl", 2),
    ]


@pytest.mark.asyncio
async def test_the_converge_also_takes_a_migrated_chain(mongo):
    """The shape CASE-1101's recipe produced: the record was migrated from
    an older KB version, so its superseded row pins that version. A
    document is placed whole or not at all — the converge version must take
    the old row too, and does when the old shape is a subset of it."""
    kb_v1 = [f for f in KB if f["name"] != "agent_identities_seeded"]
    await _cooking_target(mongo, _tpl("CK-tpl", 2, _superset(KB, COOKING)))
    archive = _archive({
        "templates": [_tpl("KB-tpl", 1, kb_v1, status="inactive"), _tpl("KB-tpl", 2, KB)],
        "documents": [
            _doc(1, KB_RECORD, version=1, status="inactive"),
            _doc(2, KB_RECORD, version=2),
        ],
    })
    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True, dry_run=True)
    assert _alignment(events)["unplaceable_count"] == 0, _alignment(events)

    await _run_merge(mongo, archive, align_templates=True)
    rows = sorted(await _rows(mongo, "documents"), key=lambda d: d["version"])
    assert [(d["version"], d["template_id"], d["template_version"]) for d in rows] == [
        (1, "CK-tpl", 2), (2, "CK-tpl", 2),
    ]
