"""A single-namespace key may omit the namespace on /query and /export/csv
(CASE-1251, review of aa040249, R2).

The platform derives a single-namespace key's namespace everywhere; these two
routes set search_path only from an explicit ``namespace``, so such a key's
unqualified table names were unknown. Now a key that can read exactly one
namespace, or whose scope implies one, gets that namespace's schema on the
search_path. A key that can read several still names its schemas itself.

Runs against the real test PostgreSQL (the scoped-reads fixture of
test_case_996_scoped_sql).
"""

import pytest

from .test_case_996_scoped_sql import READER, XREADER, P, _client, _query, scoped  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_a_single_namespace_key_reads_unqualified_names_without_naming_it(scoped):  # noqa: F811
    r = await _query(READER, "SELECT document_id, title FROM doc_probe__v1")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"document_id": "d1", "title": "alpha"}]


async def test_the_csv_export_derives_it_too(scoped):  # noqa: F811
    async with _client(READER) as c:
        r = await c.post(f"{P}/export/csv", json={"sql": "SELECT document_id FROM doc_probe__v1"})
    assert r.status_code == 200, r.text
    assert r.text.splitlines() == ["document_id", "d1"]


async def test_a_key_reading_several_namespaces_still_names_its_schemas(scoped):  # noqa: F811
    r = await _query(XREADER, "SELECT document_id FROM doc_probe__v1")
    assert r.status_code == 400, r.text
    assert "doc_probe__v1" in r.json()["detail"]
