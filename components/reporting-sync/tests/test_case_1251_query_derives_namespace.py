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


# CASE-1251, review of 74610d9c R1: an app key (its own namespace beside a
# read-only wip) gets its own namespace on the search_path. Every namespace's
# schema has tables of the same names (terms, templates, ...), so an
# unqualified name resolves in the key's own namespace, as a bare value does
# everywhere in WIP; the shared vocabularies are read schema-qualified.

APP = "rs_app_key"  # admin on ns-a, read on wip


@pytest.fixture
async def same_named(scoped, pg_pool):  # noqa: F811
    from reporting_sync.schema_manager import SchemaManager

    wip = await SchemaManager(pg_pool).ensure_schema("wip")
    async with pg_pool.acquire() as conn:
        for schema, value in (("ns-a", "own"), (wip, "shared")):
            await conn.execute(f'CREATE TABLE "{schema}"."case1251_vocab" (value TEXT)')
            await conn.execute(f'INSERT INTO "{schema}"."case1251_vocab" VALUES (\'{value}\')')
    yield
    async with pg_pool.acquire() as conn:
        for schema in ("ns-a", wip):
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."case1251_vocab"')


async def test_an_unqualified_name_resolves_in_the_keys_own_namespace(same_named):
    r = await _query(APP, "SELECT value FROM case1251_vocab")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"value": "own"}]


async def test_the_shared_namespace_is_read_schema_qualified(same_named):
    r = await _query(APP, 'SELECT value FROM "wip".case1251_vocab')
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"value": "shared"}]
