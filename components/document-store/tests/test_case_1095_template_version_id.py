"""A template version a merge adds keeps the target's template_id (CASE-1095 R4).

Across installs the same template carries different template_ids. When v1
matched the target under the target's id and v2 is new, `add_missing`
inserted v2 under the ARCHIVE's id: the template had two ids, and the
merged documents (remapped to the target's id) pinned a version that did
not exist under it. A template_id is stable across versions, so v2 takes
the id v1 mapped to.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

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
    """A real client, with this suite's namespace cleared before and after."""
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


def _tpl(template_id, version):
    row = _template(version)
    row["template_id"] = template_id
    if version > 1:
        row["fields"] = [*row["fields"], {"name": "weight", "type": "number"}]
    return row


@pytest.mark.asyncio
async def test_a_new_version_takes_the_id_its_earlier_version_mapped_to(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_tpl("TPL-T", 1)])

    doc = _document(1, doc_id="D2", template="TPL-A")
    doc["template_version"] = 2
    doc["identity_hash"] = "h2"
    await _run_merge(mongo, _archive({
        "templates": [_tpl("TPL-A", 1), _tpl("TPL-A", 2)],
        "documents": [doc],
    }), add_missing=True)

    templates = await _rows(mongo, "templates", sort_key=lambda t: t["version"])
    assert [(t["template_id"], t["version"]) for t in templates] == [("TPL-T", 1), ("TPL-T", 2)]
    [merged] = await _rows(mongo, "documents")
    assert (merged["template_id"], merged["template_version"]) == ("TPL-T", 2)


@pytest.mark.asyncio
async def test_an_entirely_new_template_keeps_the_archives_id(mongo):
    await _seed_namespace(mongo)

    await _run_merge(mongo, _archive({"templates": [_tpl("TPL-A", 1)]}), add_missing=True)

    [template] = await _rows(mongo, "templates")
    assert template["template_id"] == "TPL-A"
