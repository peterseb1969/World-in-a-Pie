"""Merging a newer backup into an older state of the same namespace works.

Two things refused it. A terminology's term_count (def-store's count of its
active terms) was compared as content, so any term added since the older
state made the terminology "incompatible", and extend_terminologies, the
flag the refusal suggested, could never get past it. And a term the newer
backup had deprecated differed in its lifecycle fields, which also refused.

Now term_count is excluded as derived and recounted after the merge adds
or retires terms. A term's lifecycle never refuses: retirement travels one
way. A term the archive retired is retired on the target with the
archive's reason and replacement, and a term the target retired stays
retired.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP, RestoreEngineError
from document_store.services.merge_definitions import compare_definitions
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
async def mongo():
    """A real client, with this suite's namespace cleared before and after."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


OLD = "2026-10-01T00:00:00+00:00"
NEW = "2026-10-20T00:00:00+00:00"


def _terminology(term_count: int) -> dict:
    return {"terminology_id": "TL1", "namespace": NAMESPACE, "value": "KIND", "term_count": term_count}


def _term(term_id: str, value: str, **extra) -> dict:
    return {
        "term_id": term_id, "namespace": NAMESPACE, "terminology_id": "TL1",
        "value": value, "status": "active", **extra,
    }


def _doc(doc_id: str, identity_hash: str, version: int, when: str, data: dict) -> dict:
    return {
        **_document(version, doc_id=doc_id, data=data),
        "identity_hash": identity_hash,
        "updated_at": when,
    }


async def _terminology_row(mongo):
    db_name, coll_name = COLLECTION_MAP["terminologies"]
    return await mongo[db_name][coll_name].find_one({"terminology_id": "TL1"})


async def _seed_older_state(mongo, *terms):
    await _seed_namespace(mongo)
    await _seed(mongo, "terminologies", [_terminology(len([t for t in terms if t["status"] == "active"]))])
    await _seed(mongo, "terms", list(terms))
    await _seed(mongo, "templates", [_template()])
    await _seed(mongo, "documents", [_doc("D1", "h1", 1, OLD, {"n": 1})])


class TestNewerBackupIntoOlderState:
    @pytest.mark.asyncio
    async def test_added_term_updated_and_new_documents_all_arrive(self, mongo):
        # The reproduction from the case: the newer backup added a term, a
        # newer version of D1, and a document D2.
        await _seed_older_state(mongo, _term("T-A", "alpha"), _term("T-B", "beta"))
        archive = _archive({
            "terminologies": [_terminology(3)],
            "terms": [_term("T-A", "alpha"), _term("T-B", "beta"), _term("T-G", "gamma")],
            "templates": [_template()],
            "documents": [
                _doc("D1", "h1", 1, OLD, {"n": 1}),
                _doc("D1", "h1", 2, NEW, {"n": 2}),
                _doc("D2", "h2", 1, NEW, {"n": 3}),
            ],
        })

        await _run_merge(mongo, archive, extend_terminologies=True, on_clash="newer")

        terms = {t["value"] for t in await _rows(mongo, "terms")}
        assert terms == {"alpha", "beta", "gamma"}
        assert (await _terminology_row(mongo))["term_count"] == 3
        docs = await _rows(mongo, "documents", sort_key=lambda d: (d["document_id"], d["version"]))
        d1_head = max((d for d in docs if d["document_id"] == "D1"), key=lambda d: d["version"])
        assert d1_head["data"] == {"n": 2}
        assert any(d["document_id"] == "D2" for d in docs)

    @pytest.mark.asyncio
    async def test_without_the_flag_only_the_missing_term_is_named(self, mongo):
        await _seed_older_state(mongo, _term("T-A", "alpha"))
        archive = _archive({
            "terminologies": [_terminology(2)],
            "terms": [_term("T-A", "alpha"), _term("T-G", "gamma")],
            "templates": [_template()],
        })

        with pytest.raises(RestoreEngineError) as refused:
            await _run_merge(mongo, archive)

        message = str(refused.value)
        assert "gamma" in message and "extend_terminologies" in message
        assert "term_count" not in message


class TestTermLifecycle:
    @pytest.mark.asyncio
    async def test_a_deprecation_in_the_archive_is_carried_forward(self, mongo):
        await _seed_older_state(mongo, _term("T-A", "alpha"), _term("T-B", "beta"))
        archive = _archive({
            "terminologies": [_terminology(1)],
            "terms": [
                _term("T-A", "alpha"),
                _term("T-B", "beta", status="deprecated",
                      deprecated_reason="use alpha", replaced_by_term_id="T-A"),
            ],
            "templates": [_template()],
        })

        await _run_merge(mongo, archive)

        beta = next(t for t in await _rows(mongo, "terms") if t["value"] == "beta")
        assert beta["status"] == "deprecated"
        assert beta["deprecated_reason"] == "use alpha"
        assert beta["replaced_by_term_id"] == "T-A"
        assert (await _terminology_row(mongo))["term_count"] == 1

    @pytest.mark.asyncio
    async def test_a_term_the_target_retired_stays_retired(self, mongo):
        # One way only: a merge never brings a retired term back.
        await _seed_older_state(
            mongo, _term("T-A", "alpha"),
            _term("T-B", "beta", status="deprecated", deprecated_reason="gone"),
        )
        archive = _archive({
            "terminologies": [_terminology(2)],
            "terms": [_term("T-A", "alpha"), _term("T-B", "beta")],
            "templates": [_template()],
        })

        await _run_merge(mongo, archive)

        beta = next(t for t in await _rows(mongo, "terms") if t["value"] == "beta")
        assert beta["status"] == "deprecated"
        assert beta["deprecated_reason"] == "gone"


class TestTemplateVersions:
    @pytest.mark.asyncio
    async def test_a_newer_template_version_lands_next_to_the_old_one(self, mongo):
        await _seed_namespace(mongo)
        await _seed(mongo, "templates", [_template(1)])

        await _run_merge(mongo, _archive({"templates": [_template(1), _template(2)]}), add_missing=True)

        versions = sorted(t["version"] for t in await _rows(mongo, "templates"))
        assert versions == [1, 2]


def test_compare_definitions_ignores_term_count():
    cosmetic, substantive = compare_definitions(_terminology(2), _terminology(3))
    assert cosmetic == [] and substantive == []
